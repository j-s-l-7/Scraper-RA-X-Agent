"""02 §0.2 reproduction: delistings and suspensions 2015–2025 under 02's symbol-level definitions.

The unit is a symbol in EQ/BE/BZ whose last ``last_seen`` across those series falls in 2015–2025 and which is not
listed now (no ``listing_history`` row with ``is_listed_now``). For these symbols the report counts:

* ``renamed``: the symbol appears as an ``old_symbol`` in ``nse_symbol_change``;
* ``isin_continues``: its last non-empty ISIN shows up under another symbol seen after its last day;
* ``moved_series``: it has a row in another series (block-deal ``BL`` excluded) seen after its last EQ/BE/BZ
  trade;
* ``true_exit``: none of the three;
* the last series before exit (BZ / BE / EQ);
* ``last_price_present``: it has a bhav close on its last day;
* the median ratio of last close to the close about one year earlier, for true exits.

This is a report on identity history, which by construction reads hindsight. Labels never use it: they classify
exits PIT at ``label_end`` (``labels/build.py``).
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from ideation.pit.contract import PitStore

REFERENCE_02 = {
    "stopped": 1053, "renamed": 355, "isin_continues": 384, "true_exit": 664, "last_bz": 205, "last_be": 308,
    "last_price_present": 1050, "true_exit_median_ratio_1y": 0.83,
    "by_year": {2015: 102, 2016: 66, 2017: 65, 2018: 66, 2019: 78, 2020: 71, 2021: 106, 2022: 95, 2023: 150,
                2024: 120, 2025: 134},
}  # fmt: skip
STITCH_SERIES = ("EQ", "BE", "BZ")


def exit_counts_02(store: PitStore, first_year: int = 2015, last_year: int = 2025) -> dict[str, Any]:
    lh = store.table("listing_history")
    main = lh[lh["series"].isin(STITCH_SERIES)]
    agg = (
        main.sort_values("last_seen")
        .groupby("symbol")
        .agg(last_seen=("last_seen", "max"), last_series=("series", "last"))
    )
    listed_now = set(lh.loc[lh["is_listed_now"].astype(bool), "symbol"])
    yr = pd.Series([d.year for d in agg["last_seen"]], index=agg.index)
    stopped = agg[(yr >= first_year) & (yr <= last_year) & ~agg.index.isin(listed_now)].copy()

    renamed = set(store.table("symbol_change")["old_symbol"])
    # the symbol's last ISIN reappears under ANOTHER symbol seen after this symbol's last day (continuation, not
    # an ISIN inherited from an earlier symbol)
    last_isin = main.sort_values("last_seen").groupby("symbol")["isin"].last().fillna("")
    by_isin = main[main["isin"].fillna("") != ""].groupby(["isin", "symbol"])["last_seen"].max().reset_index()
    shared = set()
    for sym, ls in zip(stopped.index, stopped["last_seen"], strict=True):
        isin = last_isin.get(sym, "")
        if isin:
            others = by_isin[(by_isin["isin"] == isin) & (by_isin["symbol"] != sym)]
            if (others["last_seen"] > ls).any():
                shared.add(sym)
    other = lh[~lh["series"].isin((*STITCH_SERIES, "BL"))].groupby("symbol")["last_seen"].max()
    stopped["renamed"] = stopped.index.isin(renamed)
    stopped["isin_continues"] = stopped.index.isin(shared)
    stopped["moved_series"] = [
        bool(s in other.index and other[s] > ls) for s, ls in zip(stopped.index, stopped["last_seen"], strict=True)
    ]
    stopped["true_exit"] = ~(stopped["renamed"] | stopped["isin_continues"] | stopped["moved_series"])

    px = store.table("prices")[["symbol", "date", "close"]]
    px = px[px["symbol"].isin(stopped.index)].sort_values(["symbol", "date"])
    last_close = px.groupby("symbol").tail(1).set_index("symbol")
    stopped["last_price_present"] = [
        bool(s in last_close.index and last_close.at[s, "date"] == ls)
        for s, ls in zip(stopped.index, stopped["last_seen"], strict=True)
    ]
    ratios = []
    for s in stopped.index[stopped["true_exit"]]:
        g = px[px["symbol"] == s]
        if g.empty:
            continue
        end = g["date"].iloc[-1]
        before = g[g["date"] <= end - timedelta(days=365)]
        if len(before):
            ratios.append(g["close"].iloc[-1] / before["close"].iloc[-1])
    out = {
        "window": [first_year, last_year],
        "stopped": len(stopped),
        "by_year": {
            int(k): int(v)
            for k, v in pd.Series([d.year for d in stopped["last_seen"]]).value_counts().sort_index().items()
        },
        "renamed": int(stopped["renamed"].sum()),
        "isin_continues": int(stopped["isin_continues"].sum()),
        "moved_series": int(stopped["moved_series"].sum()),
        "true_exit": int(stopped["true_exit"].sum()),
        "last_bz": int((stopped["last_series"] == "BZ").sum()),
        "last_be": int((stopped["last_series"] == "BE").sum()),
        "last_eq": int((stopped["last_series"] == "EQ").sum()),
        "last_price_present": int(stopped["last_price_present"].sum()),
        "true_exit_median_ratio_1y": round(float(np.median(ratios)), 3) if ratios else None,
    }
    out["delta_pct_vs_02"] = {
        k: round(100 * (out[k] - REFERENCE_02[k]) / REFERENCE_02[k], 1)
        for k in ("stopped", "renamed", "isin_continues", "true_exit", "last_bz", "last_be", "last_price_present")
    }
    return out


def stitched_exit_summary(labels: pd.DataFrame, horizon: str = "1Y") -> dict[str, Any]:
    """Exit types of the stitched-security labels per anchor (unsealed anchors are passed in by the caller)."""
    x = labels[labels["horizon"] == horizon]
    tab = x.groupby(["anchor", "exit_type"]).size().unstack(fill_value=0)
    return {str(a): {k: int(v) for k, v in row.items()} for a, row in tab.iterrows()}
