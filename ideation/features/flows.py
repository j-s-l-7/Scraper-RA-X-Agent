"""Flow / ownership features (01 §2.6, 03 §B1). Pure functions of one ``PitView`` and an ``AnchorContext``.

* Ownership uses the YEARLY shareholding rows only (frequency Y; quarterly rows are broad only from 2023 and are
  never used in a backtest feature). ``*_chg_1y`` = latest yearly row minus the row exactly 12 months earlier
  (consecutive yearly periods; the latest row that has such a partner); it must be at most ``MAX_STALE_MONTHS``
  before the anchor.
  Availability is the snapshot's ``available_from`` (period end + 21 d + COVID overrides + 1), enforced by the view.
* ``fpi_sector_flow_3m``: NSDL equity net investment (``net_curr``, one fortnight each, never ``net_prev``) summed over
  the fortnights reported in (A - 92 d, A], divided by the assets under custody of the latest report, for the group's
  mapped NSDL sectors (``config/fpi_sector_map.yaml``). Needs >= 5 fortnights and a report within 45 days.
"""

from __future__ import annotations

from datetime import timedelta
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from ideation.config import REPO_ROOT
from ideation.regime.context import AnchorContext

SOURCE = "features_technical.yaml"
OWN_CATS = {"promoters": "prom_chg_1y", "fii": "fii_chg_1y", "dii": "dii_chg_1y", "num_shareholders": "holders_chg_1y"}
MAX_STALE_MONTHS = 15
FPI_WINDOW_DAYS = 92
FPI_MIN_REPORTS = 5
FPI_MAX_STALE_DAYS = 45


def _month_key(s: pd.Series) -> np.ndarray:
    d = np.asarray(s.tolist(), dtype="datetime64[D]")
    return d.astype("datetime64[M]").astype("int64")  # months since 1970-01


def ownership_by_company(view) -> pd.DataFrame:
    """company_id -> the four yearly changes at the view's anchor."""
    sh = view.shareholding()
    y = sh[(sh["frequency"] == "Y") & sh["category"].isin(list(OWN_CATS))]
    if y.empty:
        return pd.DataFrame(columns=list(OWN_CATS.values()))
    y = y.assign(k=_month_key(y["period_end"]))
    y = y.drop_duplicates(["company_id", "category", "k"], keep="last")
    a = view.anchor
    a_key = (a.year - 1970) * 12 + a.month - 1
    out = {}
    for cat, name in OWN_CATS.items():
        c = y[y["category"] == cat][["company_id", "k", "value"]]
        prev = c.assign(k=c["k"] + 12).rename(columns={"value": "prev"})
        m = c.merge(prev, on=["company_id", "k"], how="inner")  # every row that has its 12-months-earlier partner
        m = m[a_key - m["k"] <= MAX_STALE_MONTHS].sort_values("k").groupby("company_id", sort=False).tail(1)
        if cat == "num_shareholders":
            ok = (m["value"] > 0) & (m["prev"] > 0)
            m = m[ok].assign(chg=np.log(m.loc[ok, "value"] / m.loc[ok, "prev"]))
        else:
            m = m.assign(chg=m["value"] - m["prev"])
        out[name] = m.set_index("company_id")["chg"]
    return pd.DataFrame(out)


@lru_cache(maxsize=4)
def _fpi_map(path: str) -> dict[str, str]:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def fpi_flow_by_group(view, map_path: Path | None = None) -> pd.Series:
    """industry_group -> fpi_sector_flow_3m at the view's anchor."""
    mp = _fpi_map(str(map_path or REPO_ROOT / "config" / "fpi_sector_map.yaml"))
    fp = view.fpi_sector()
    fp = fp[(fp["route"] == "Equity") & fp["measure"].isin(["net_curr", "auc_end"])]
    fp = fp.assign(group=fp["sector"].map(mp)).dropna(subset=["group", "inr_cr"])
    if fp.empty:
        return pd.Series(dtype=float)
    a = view.anchor
    lo = a - timedelta(days=FPI_WINDOW_DAYS)
    fp = fp[(fp["report_date"] > lo) & (fp["report_date"] <= a)]
    net = fp[fp["measure"] == "net_curr"]
    flow = net.groupby("group")["inr_cr"].sum()
    n_rep = net.groupby("group")["report_date"].nunique()
    auc = fp[fp["measure"] == "auc_end"]
    last = auc.groupby("group")["report_date"].max()
    auc_last = auc.merge(last.rename("last"), left_on="group", right_index=True)
    auc_last = auc_last[auc_last["report_date"] == auc_last["last"]].groupby("group")["inr_cr"].sum()
    fresh = last >= a - timedelta(days=FPI_MAX_STALE_DAYS)
    val = (flow / auc_last).where((n_rep >= FPI_MIN_REPORTS) & fresh & (auc_last > 0))
    return val.dropna()


def flow_columns(view, ctx: AnchorContext) -> pd.DataFrame:
    """Flow columns for ``ctx.members`` rows."""
    own = ownership_by_company(view)
    cid = pd.Series(ctx.ids["company_id"].to_numpy()[ctx.col])
    res = pd.DataFrame(
        {n: cid.map(own[n]).to_numpy(dtype=float) if n in own else np.nan for n in OWN_CATS.values()}
    )
    grp = pd.Series(ctx.ids["industry_group"].to_numpy()[ctx.col])
    res["fpi_sector_flow_3m"] = grp.map(fpi_flow_by_group(view)).to_numpy(dtype=float)
    return res
