"""Live feature build as of the latest data (P6, 01 §8.1 ``predict``). Same builders as the backtest, in memory.

Order matters and mirrors ``ideation universe build`` + ``features build`` + ``features-tech build``:

1. ``build_universe`` (no hook) -> members;
2. ``build_fundamental`` on the traded, company-linked members -> ``feature_complete``;
3. ``build_universe`` again with the ``feature_complete`` hook, so U_score is exactly the training definition;
4. ``tech_build.build_view`` (technical + flow + event + regime columns, RRG), which builds its own U_score_T.

The store is opened in ``live`` mode (no stored universe attached, so membership is always built in memory). The
anchor is the last good session of the snapshot; no wall clock is read.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from ideation.features.fundamental import build_fundamental
from ideation.features.registry import Registry
from ideation.features.tech_build import AnchorResult, build_view
from ideation.pit.contract import ManifestRecord, PitStore, PitView
from ideation.universe.build import UniverseConfig, build_universe

LAKH_PER_CR = 100.0
COOLING_SESSIONS = 7
ADV20 = 20


@dataclass
class LiveData:
    anchor: date
    snap_id: str
    members: pd.DataFrame
    """Universe members at the anchor (hook applied)."""
    fund: pd.DataFrame
    tech: AnchorResult
    market: pd.DataFrame
    """Per security: last raw close, ADV20 (₹ cr), series, max |1-session adjusted return| over the last 7 sessions."""
    records: list[ManifestRecord] = field(default_factory=list)


def live_anchor(store: PitStore) -> date:
    """Last good session in the snapshot."""
    return store.calendar.good[-1].astype(object)


def market_columns(view: PitView) -> pd.DataFrame:
    """Idea-stage market columns from the raw bhav rows: last close, ADV20 in ₹ crore, series, cooling-off return."""
    raw = view.prices_raw(ADV20, columns=["security_id", "date", "series", "close", "turnover_lacs"])
    raw = raw.sort_values(["security_id", "date"])
    last = raw.groupby("security_id").tail(1).set_index("security_id")
    sessions = view.sessions(ADV20)
    adv = raw.groupby("security_id")["turnover_lacs"].sum() / len(sessions) / LAKH_PER_CR
    out = pd.DataFrame({"last_close": last["close"], "series": last["series"], "adv20_cr": adv})
    adj = view.prices_adj(50)[["security_id", "date", "close_adj"]].sort_values(["security_id", "date"])
    out["sma50"] = adj.groupby("security_id")["close_adj"].apply(
        lambda s: float(s.tail(50).mean()) if len(s) >= 40 else np.nan
    )
    adj["r"] = adj.groupby("security_id")["close_adj"].pct_change()
    recent = adj[adj["date"].isin(set(view.sessions(COOLING_SESSIONS).astype("datetime64[D]").astype(object)))]
    out["max_abs_ret_7d"] = recent.groupby("security_id")["r"].apply(
        lambda s: float(np.nanmax(np.abs(s))) if s.notna().any() else np.nan
    )
    return out.reset_index()


def build_live(store: PitStore, reg: Registry, ucfg: UniverseConfig, anchor: date | None = None) -> LiveData:
    anchor = anchor or live_anchor(store)
    view = store.as_of(anchor)
    mem0, _ = build_universe(view, ucfg)
    linked = mem0[mem0["traded_ok"].astype(bool) & mem0["company_id"].notna()]
    fund = build_fundamental(view, linked, reg)
    fc = fund.set_index("security_id")["feature_complete"]
    members, _ = build_universe(view, ucfg, feature_complete=fc)
    tech = build_view(view, reg, ucfg, rebuild_universe=True)
    market = market_columns(view)
    return LiveData(anchor, store.snap_id, members, fund, tech, market, view.records)


# ── frames for the two horizons (same column layout as bakeoff.data.load_panel / tactical.data.load_panel) ──────
ID_COLS = ["anchor", "security_id", "company_id", "industry_group", "sector", "layout"]
LABEL_NAN = ("label_end", "ret_total", "excess_univ_med", "excess_n500_pr", "exit_type")


def frame_1y(live: LiveData, reg: Registry) -> pd.DataFrame:
    """U_score members x fundamental + technical + regime columns; label columns are NaN (no label exists)."""
    mem = live.members[live.members["in_u_score"].astype(bool)]
    uni = mem[["security_id", "in_u_score", "in_u_score_adv05", "in_u1000", "adv63_lakh", "mcap_pit_cr"]].copy()
    uni.insert(0, "anchor", live.anchor)
    fund_cols = [n for n in reg.names(source="features_fundamental.yaml") if n in live.fund.columns]
    fund = live.fund[["security_id", "company_id", "industry_group", "sector", "layout", *fund_cols]]
    tcols = [n for n in reg.names(sets=["tech", "regime"]) if n in live.tech.features.columns]
    tech = live.tech.features[["security_id", *tcols]]
    frame = uni.merge(fund, on="security_id", how="left", validate="1:1").merge(
        tech, on="security_id", how="left", validate="1:1"
    )
    for c in reg.broadcast_cols():
        if c in frame.columns:
            frame[c] = frame[c].dropna().iloc[0] if frame[c].notna().any() else np.nan
    for c in LABEL_NAN:
        frame[c] = np.nan if c != "label_end" else pd.NaT
    frame["labelled"] = False
    return frame.sort_values(["anchor", "security_id"], kind="stable").reset_index(drop=True)


def frame_3m(live: LiveData) -> pd.DataFrame:
    """U_score_T rows of the tech matrix with ADV63; the 3M/1M label columns are NaN."""
    tech = live.tech.features
    tech = tech[tech["in_u_score_t"].astype(bool)].copy()
    tech["anchor"] = pd.Timestamp(live.anchor).normalize()
    uni = live.members[["security_id", "adv63_lakh", "mcap_pit_cr", "symbol"]]
    df = tech.merge(uni, on="security_id", how="left", validate="1:1")
    for c in ("ret_3m", "ret_1m", "retp_3m", "exu_3m", "exn_3m", "exu_1m"):
        df[c] = np.nan
    df["label_end"] = pd.NaT
    return df.reset_index(drop=True)


def security_info(live: LiveData) -> pd.DataFrame:
    """symbol / company / group / layout / price / ADV20 per security, for the ideas stage."""
    m = live.members[
        ["security_id", "symbol", "company_id", "mcap_pit_cr", "adv63_lakh", "feature_complete", "mcap_flag"]
    ].copy()
    f = live.fund[["security_id", "industry_group", "sector", "layout", "c_i", "nd_ebitda", "cfo_np"]]
    out = m.merge(f, on="security_id", how="left").merge(live.market, on="security_id", how="left")
    return out


__all__ = ["LiveData", "build_live", "frame_1y", "frame_3m", "live_anchor", "security_info"]
_ = Any
