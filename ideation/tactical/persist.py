# ruff: noqa: E501
"""Persist MS2 results into schema ``ideation`` (model rows, wf_result, oos_prediction, experiment_trial). Writes only
through the guarded writer. Models get status ``candidate``: promotion is manual (03 A12), never done here."""

from __future__ import annotations

import json
import math
from typing import Any

import numpy as np
import pandas as pd

from ideation.config import canonical_json
from ideation.db.writer import GuardedConnection
from ideation.rank.buckets import decile
from ideation.rank.percentile import percentile
from ideation.rank.transform import rank_gauss
from ideation.trials.registry import TrialSpec


def _f(x: Any) -> float | None:
    return None if x is None or (isinstance(x, float) and not math.isfinite(x)) else float(x)


def model_id(conn: GuardedConnection, spec: TrialSpec, horizon: str, selection: dict[str, Any]) -> int:
    params = {"trial": spec.trial_id, "num": spec.num, "config_hash": spec.config_hash, "family_trial": spec.family}
    row = conn.execute("select model_id from ideation.model where params->>'config_hash' = %s and horizon = %s",
                       (spec.config_hash, horizon)).fetchone()  # fmt: skip
    if row:
        conn.execute("update ideation.model set selection = %s::jsonb, status = 'candidate' where model_id = %s",
                     (canonical_json(selection), row[0]))  # fmt: skip
        return int(row[0])
    fam = spec.config.get("learner") or spec.config.get("model") or spec.config.get("consensus")
    row = conn.execute(
        "insert into ideation.model (horizon, family, params, selection, status) values (%s, %s, %s::jsonb, %s::jsonb,"
        " 'candidate') returning model_id",
        (horizon, str(fam), canonical_json(params), canonical_json(selection)),
    ).fetchone()
    assert row is not None
    return int(row[0])


def write_wf_result(conn: GuardedConnection, mid: int, pa: pd.DataFrame, n_universe: pd.Series) -> int:
    conn.execute("delete from ideation.wf_result where model_id = %s", (mid,))
    rows = []
    for a, r in pa.iterrows():
        cov = {"q5_excess": _f(r["q5_excess"]), "hit_d10": _f(r["hit_d10"]), "turnover_q5": _f(r["to_q5"]),
               "turnover_q1": _f(r["to_q1"]), "spread_cost_adj": _f(r["spread_cost_adj"])}  # fmt: skip
        rows.append((mid, pd.Timestamp(a).date(), int(r["n"]), int(n_universe.get(a, r["n"])), _f(r["ic"]), None, None,
                     _f(r["spread"]), _f(r["hit_d10"]), _f(r["q5_ret"]), _f(r["q1_ret"]), _f(r["univ_ret"]), None,
                     json.dumps(cov)))  # fmt: skip
    conn.executemany(
        "insert into ideation.wf_result (model_id, test_anchor, n, n_universe, ic, ic_lo, ic_hi, spread_tb, hit_top,"
        " top_ret, bottom_ret, bench_ret, unscored_ret, coverage) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)",
        rows,
    )
    return len(rows)


def write_oos(conn: GuardedConnection, trial_id: int, sc: pd.DataFrame, raw: pd.DataFrame) -> int:
    d = sc.dropna(subset=["score"]).merge(raw[["anchor", "security_id", "ret_3m", "exu_3m"]],
                                         on=["anchor", "security_id"], how="inner")  # fmt: skip
    d["p"] = percentile(d["score"], by=d["anchor"].to_numpy())
    d["bucket"] = decile(d["p"]).astype(int)
    d["label"] = d.groupby("anchor")["ret_3m"].transform(lambda s: rank_gauss(s))
    if "fold" not in d:
        d["fold"] = "consensus"
    conn.execute("delete from ideation.oos_prediction where trial_id = %s", (trial_id,))
    cols = [
        "trial_id",
        "anchor",
        "security_id",
        "score",
        "p",
        "bucket_dec",
        "label",
        "ret_total",
        "excess_univ_med",
        "fold",
    ]
    gen = ((trial_id, a.date(), int(s), _f(sc_), _f(p), int(b), _f(lb), _f(r), _f(e), str(f)) for a, s, sc_, p, b, lb, r, e, f in
           zip(d["anchor"], d["security_id"], d["score"], d["p"], d["bucket"], d["label"], d["ret_3m"], d["exu_3m"],
               d["fold"].fillna("consensus"), strict=True))  # fmt: skip
    return conn.copy_rows("ideation.oos_prediction", cols, gen)


def update_trial(conn: GuardedConnection, trial_id: int, status: str, pa3: pd.DataFrame | None) -> None:
    if pa3 is None:
        conn.execute("update ideation.experiment_trial set status = %s where trial_id = %s", (status, trial_id))
        return
    ic = {str(pd.Timestamp(a).date()): _f(v) for a, v in pa3["ic"].items()}
    sp = {str(pd.Timestamp(a).date()): _f(v) for a, v in pa3["spread_cost_adj"].items()}
    conn.execute(
        "update ideation.experiment_trial set status = %s, n_oos = %s, ic_series = %s::jsonb, spread_series = %s::jsonb"
        " where trial_id = %s", (status, int(pa3["ic"].notna().sum()), json.dumps(ic), json.dumps(sp), trial_id),
    )  # fmt: skip


_ = np
