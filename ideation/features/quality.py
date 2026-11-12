# ruff: noqa: E501
"""Point-in-time quality / investment features for the accuracy lab (config/features_quality.yaml).

Same contract as :mod:`ideation.features.fundamental`: ``view.fundamentals`` already returns only facts with
``available_from <= anchor`` (the statutory-deadline + results-event lag fixed in the snapshot), the C-over-S basis
rule and the consecutive-FY / same-basis link guard are reused (``fundamental._panel``), and a Delta that spans a
basis switch or a period gap is NaN. Features are computed for the latest visible FY only and are NaN for a stale FY
(> ``max_fy_age_days``). Output rows are keyed by ``security_id``; the caller joins them to the fundamental frame.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ideation.features.fundamental import HISTORY_YEARS, _panel
from ideation.features.registry import Registry
from ideation.pit.contract import LeakageError, PitView

SOURCE = "features_quality.yaml"
BASE_LINES = ("sales", "revenue", "np", "ta", "cfo", "borr", "eqcap", "op", "mat_pct", "mfg_pct")
PIOTROSKI_N = 8


def _fy_quality(p: pd.DataFrame, gap_months: int) -> pd.DataFrame:
    """Per (company, FY) quality columns on the panel ``p`` (sorted by company, pe)."""
    g = p.groupby("company_id", sort=False)
    mi = p["pe"].dt.year * 12 + p["pe"].dt.month
    link = ((mi - mi.groupby(p["company_id"], sort=False).shift(1)) == gap_months) & (
        p["basis"] == g["basis"].shift(1)
    )
    link = link.fillna(False).astype(bool)

    def prev(c: pd.Series) -> pd.Series:
        return c.groupby(p["company_id"], sort=False).shift(1).where(link)

    ta, ta0 = p["ta"], prev(p["ta"])
    avg_ta = pd.Series(np.where(link & ta0.notna(), (ta + ta0) / 2, ta), index=p.index).where(lambda s: s > 0)
    cost = (p["mat_pct"] + p["mfg_pct"]) / 100.0
    gp = p["sales"] * (1.0 - cost)
    out = pd.DataFrame(index=p.index)
    out["gp_ta"] = (gp / avg_ta).where(p["sales"] > 0)
    out["accruals"] = (p["np"] - p["cfo"]) / avg_ta
    out["asset_gr"] = (ta / ta0 - 1).where((ta > 0) & (ta0 > 0))

    roa = p["np"] / ta.where(ta > 0)
    roa0 = prev(roa)
    lev = p["borr"] / ta.where(ta > 0)
    lev0 = prev(lev)
    opm = p["op"] / p["sales"].where(p["sales"] > 0)
    opm0 = prev(opm)
    turn = p["sales"] / ta.where(ta > 0)
    turn0 = prev(turn)
    comps = pd.DataFrame(
        {
            "f1": (roa > 0).where(roa.notna()),
            "f2": (p["cfo"] > 0).where(p["cfo"].notna()),
            "f3": (roa > roa0).where(roa.notna() & roa0.notna()),
            "f4": (p["cfo"] > p["np"]).where(p["cfo"].notna() & p["np"].notna()),
            "f5": (lev < lev0).where(lev.notna() & lev0.notna()),
            "f6": (p["eqcap"] <= prev(p["eqcap"]) * (1 + 1e-9)).where(p["eqcap"].notna() & prev(p["eqcap"]).notna()),
            "f7": (opm > opm0).where(opm.notna() & opm0.notna()),
            "f8": (turn > turn0).where(turn.notna() & turn0.notna()),
        }
    ).astype(float)
    out["piotroski"] = comps.sum(axis=1).where(comps.notna().sum(axis=1) == PIOTROSKI_N)
    return out


def build_quality(view: PitView, members: pd.DataFrame, reg: Registry, ids: dict[str, int]) -> pd.DataFrame:
    """Quality features for the ``members`` (``security_id``, ``company_id``) at ``view.anchor``.

    ``ids`` = alias → metric_id for at least :data:`BASE_LINES`. Columns: ``security_id``, ``q_fy_end`` and the four
    features (NaN where the FY is older than ``max_fy_age_days``).
    """
    cl = reg.cleaning
    m = members[members["company_id"].notna()].copy()
    m["company_id"] = m["company_id"].astype("int64")
    sub = {a: ids[a] for a in BASE_LINES}
    p = _panel(view, sub, m["company_id"].unique(), HISTORY_YEARS)
    d = _fy_quality(p, int(cl["fy_gap_months"]))
    full = p[["company_id", "pe", "avail"]].join(d)
    cur = full.groupby("company_id", sort=False).tail(1).set_index("company_id")
    out = m[["security_id", "company_id"]].join(cur, on="company_id")
    age = (pd.Timestamp(view.anchor) - out["pe"]).dt.days
    stale = age > int(cl["max_fy_age_days"])
    feats = [n for n in reg.names(source=SOURCE)]
    for f in feats:
        out[f] = out[f].where(~stale & out["pe"].notna())
    if out["avail"].max() is not pd.NaT and pd.notna(out["avail"].max()) and out["avail"].max() > pd.Timestamp(view.anchor):
        raise LeakageError(f"quality features: availability {out['avail'].max()} > anchor {view.anchor}")
    out["q_fy_end"] = out["pe"].map(lambda x: None if pd.isna(x) else x.date()).astype(object)
    return out[["security_id", "q_fy_end", *feats]].sort_values("security_id", kind="stable").reset_index(drop=True)
