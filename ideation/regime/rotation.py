"""RRG tables at an anchor: EW industry-group and sector composites against the EW all-security composite (primary),
and the official sector indices against the Nifty 500 (display). Indices with too little history at the anchor
(for example the three that start in 2026-10) are left out, never padded."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ideation.regime.context import INDEX_CODE, AnchorContext, asof_levels
from ideation.regime.rrg import DEFAULT, QUAD_NAMES, RrgParams, levels_from_returns, rrg_at_anchor

SECTOR_INDICES = (
    "^CNXAUTO", "^NSEBANK", "^CNXENERGY", "^CNXFMCG", "^CNXIT", "^CNXMEDIA", "^CNXMETAL", "^CNXPHARMA",
    "^CNXPSUBANK", "^CNXREALTY", "^CNXINFRA", "^CNXPSE", "^CNXMNC", "^CNXCONSUM", "^CNXSERVICE", "^CNXCMDT",
    "NIFTY_FIN_SERVICE.NS",
)  # fmt: skip
MIN_INDEX_OBS = 400  # sessions with a level inside the lookback window


def _row(kind: str, code: str, ratio: float, mom: float, quad: int, n: float) -> dict:
    return {"group_type": kind, "group_code": code, "rs_ratio": ratio, "rs_mom": mom,
            "quadrant": QUAD_NAMES.get(int(quad)) if quad >= 0 else None, "n_members": n}  # fmt: skip


def rrg_table(ctx: AnchorContext, view, params: RrgParams = DEFAULT) -> pd.DataFrame:
    comps = ctx.comps
    bench = levels_from_returns(comps.market_ret)
    rows: list[dict] = []
    gl = levels_from_returns(comps.group_ret)
    r, m, q = rrg_at_anchor(gl, bench, params)
    rows += [_row("industry_group", g, r[i], m[i], q[i], comps.group_n[-1, i]) for i, g in enumerate(comps.groups)]
    sl = levels_from_returns(comps.sector_ret)
    r, m, q = rrg_at_anchor(sl, bench, params)
    rows += [_row("sector", s, r[i], m[i], q[i], comps.sector_n[-1, i]) for i, s in enumerate(comps.sectors)]
    codes = list(SECTOR_INDICES)
    df = view.macro([*codes, INDEX_CODE])
    b = asof_levels(df[df["code"] == INDEX_CODE], ctx.sess_days, strict=False)
    for c in codes:
        lv = asof_levels(df[df["code"] == c], ctx.sess_days, strict=False)
        if np.isfinite(lv).sum() < MIN_INDEX_OBS or not np.isfinite(lv[-1]):
            continue
        rr, mm, qq = rrg_at_anchor(lv[:, None], b, params)
        rows.append(_row("index", c, rr[0], mm[0], qq[0], float("nan")))
    out = pd.DataFrame(rows)
    out.insert(0, "anchor", ctx.anchor)
    return out
