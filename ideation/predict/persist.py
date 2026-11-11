# ruff: noqa: E501
"""Persistence of live predictions into ``ideation.prediction`` (existing table) and read-back for the ideas stage.

One row per (run, horizon, company). Names without a ``company_id`` (unmapped U_score_T names) stay in R1 and the
percentile but are not persisted. Everything that is not a first-class column goes into ``contrib`` (jsonb).
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import pandas as pd

from ideation.predict.assemble import to_jsonable

COLUMNS = ["run_id", "model_id", "horizon", "anchor", "company_id", "symbol", "score", "rank_pct", "alpha", "exp_ret", "lo",
           "hi", "ood_score", "ood", "c_conf", "contrib"]  # fmt: skip
CONTRIB_KEYS = ["security_id", "industry_group", "sector", "layout", "mcap_pit_cr", "adv20_cr", "last_close", "series",
                "sma50", "feat_complete_share", "ideation_score", "decile", "hit_rate", "beta", "vol", "neutral_p_bv",
                "neutral_p_bvls", "flags", "quadrant", "rs_ratio", "rs_mom", "regime_misaligned", "drivers"]  # fmt: skip
CHAL_PREFIX = "chal_"


def dedupe_company(df: pd.DataFrame) -> pd.DataFrame:
    """One security per company (the most liquid), required by the table key (run, horizon, company)."""
    d = df[df["company_id"].notna()].sort_values(["company_id", "adv63_lakh"], ascending=[True, False])
    d = d.drop_duplicates("company_id", keep="first")
    assert d["company_id"].is_unique
    return d


def contrib_json(r: Any, model: str, units: str) -> str:
    c = {k: to_jsonable(getattr(r, k, None)) for k in CONTRIB_KEYS if k not in ("flags", "drivers")}
    c["flags"] = list(r.flags)
    c["drivers"] = r.drivers
    c["model"] = model
    c["units"] = units
    chal = {k[len(CHAL_PREFIX) :]: to_jsonable(getattr(r, k)) for k in r._fields if k.startswith(CHAL_PREFIX)}
    if chal:
        c["challenger"] = {"label": "research challenger, unpromoted", **chal}
    return json.dumps(c, allow_nan=False)


def prediction_rows(
    df: pd.DataFrame, run_id: int, model_id: int | None, horizon: str, anchor: Any, model: str
) -> list[tuple]:
    units = f"excess return vs the universe median over {horizon}; the market leg (pi) is added in the portfolio phase"
    rows = []
    d = dedupe_company(df)
    for r in d.itertuples():
        rows.append((run_id, model_id, horizon, anchor, int(r.company_id), r.symbol, float(r.score), float(r.p), float(r.alpha), None,
                     float(r.lo), float(r.hi), float(r.ood_score), bool(r.ood), float(r.c_conf), contrib_json(r, model, units)))  # fmt: skip
    return rows


def write_predictions(conn: Any, rows: list[tuple]) -> int:
    return conn.copy_rows("ideation.prediction", COLUMNS, rows)


def read_predictions(conn: Any, run_id: int, horizon: str) -> pd.DataFrame:
    cur = conn.execute(
        "select model_id, anchor, company_id, symbol, score, rank_pct, alpha, lo, hi, ood_score, ood, c_conf, contrib"
        " from ideation.prediction where run_id = %s and horizon = %s order by company_id",
        (run_id, horizon),
    )
    cols = [
        "model_id",
        "anchor",
        "company_id",
        "symbol",
        "score",
        "p",
        "alpha",
        "lo",
        "hi",
        "ood_score",
        "ood",
        "c_conf",
        "contrib",
    ]
    df = pd.DataFrame(cur.fetchall(), columns=cols)
    ex = pd.DataFrame(list(df["contrib"])) if len(df) else pd.DataFrame()
    out = pd.concat([df.drop(columns=["contrib"]), ex], axis=1)
    for c in ("score", "p", "alpha", "lo", "hi", "ood_score", "c_conf"):
        out[c] = out[c].astype(float)
    return out


_ = np
