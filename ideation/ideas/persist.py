"""``ideation.idea`` persistence, prior-run lookup for continuity, and the latest predict run."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from ideation.predict.assemble import to_jsonable

COLUMNS = ["run_id", "as_of", "horizon", "company_id", "symbol", "side", "rank", "conviction", "score", "exp_ret_lo",
           "exp_ret_mid", "exp_ret_hi", "thesis", "risks", "invalidation", "sector_ctx", "regime_ctx", "overlay", "flags",
           "continuity", "prev_idea_id"]  # fmt: skip


def latest_predict_run(conn: Any, as_of: date | None = None) -> tuple[int, date, dict[str, Any]] | None:
    q = "select run_id, as_of, stats from ideation.run where kind = 'predict' and status = 'ok'"
    args: tuple = ()
    if as_of is not None:
        q += " and as_of = %s"
        args = (as_of,)
    r = conn.execute(q + " order by run_id desc limit 1", args).fetchone()
    return (int(r[0]), r[1], r[2] or {}) if r else None


def previous_ideas(conn: Any, horizon: str, before_run: int) -> pd.DataFrame | None:
    """Ideas of the latest earlier ok ideas run that has rows for ``horizon``."""
    r = conn.execute(
        "select max(i.run_id) from ideation.idea i join ideation.run r on r.run_id = i.run_id"
        " where r.kind = 'ideas' and r.status = 'ok' and i.horizon = %s and i.run_id < %s", (horizon, before_run)).fetchone()  # fmt: skip
    if not r or r[0] is None:
        return None
    rows = conn.execute("select idea_id, company_id, symbol, side, rank, as_of from ideation.idea where run_id = %s and horizon = %s",
                        (r[0], horizon)).fetchall()  # fmt: skip
    return pd.DataFrame(rows, columns=["idea_id", "company_id", "symbol", "side", "rank", "as_of"])


def idea_rows(df: pd.DataFrame, run_id: int, as_of: date, horizon: str, regime: dict[str, str], overlay: dict[str, Any],
              llm: dict[int, dict[str, Any]] | None = None) -> list[tuple]:  # fmt: skip
    rows = []
    for r in df.itertuples():
        thesis: dict[str, Any] = {
            "model": r.model, "units": r.units, "ideation_score": to_jsonable(r.ideation_score), "p": to_jsonable(r.p),
            "decile": to_jsonable(r.decile), "hit_rate_decile": to_jsonable(r.hit_rate), "drivers": r.drivers,
            "beta": to_jsonable(r.beta), "vol": to_jsonable(r.vol), "neutral_p_bv": to_jsonable(r.neutral_p_bv),
            "neutral_p_bvls": to_jsonable(r.neutral_p_bvls), "c_conf": to_jsonable(r.c_conf),
            "challenger": getattr(r, "challenger", None) if isinstance(getattr(r, "challenger", None), dict) else None,
        }  # fmt: skip
        if llm and int(r.company_id) in llm:
            thesis["llm_summary"] = llm[int(r.company_id)]
        sector = {"industry_group": r.industry_group, "quadrant": r.quadrant, "rs_ratio": to_jsonable(r.rs_ratio),
                  "rs_mom": to_jsonable(r.rs_mom)}  # fmt: skip
        pid = None if pd.isna(r.prev_idea_id) else int(r.prev_idea_id)
        rows.append((run_id, as_of, horizon, int(r.company_id), r.symbol, r.side, int(r.rank), r.conviction, float(r.score),
                     float(r.lo), float(r.alpha), float(r.hi), json.dumps(thesis, allow_nan=False),
                     json.dumps(list(r.risk_list)), json.dumps(list(r.invalidation)), json.dumps(sector, allow_nan=False),
                     json.dumps(regime), json.dumps(overlay.get(int(r.company_id), {"status": "no_overlay_connected"})),
                     list(r.flags), r.continuity, pid))  # fmt: skip
    return rows


def write_ideas(conn: Any, rows: list[tuple]) -> int:
    return conn.copy_rows("ideation.idea", COLUMNS, rows)


_ = np
