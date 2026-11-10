"""Earnings-event features (05 §6.1): ``ear_3d``, ``ear_days``, ``pead_drift``, ``isna_event``. No estimates are read.

Event selection (``select_events``): per company the latest event with a reported EPS, dated in (A - 120 d, A], whose
announcement window is complete at the anchor. With ``e'`` the first session on or after the event date and ``s(.)``
the panel's session index, the window is the sessions e'-1, e', e'+1; the event is usable iff ``s(e'+1) <= s(A)``
(computed on the exchange calendar, so weekend and holiday event dates are handled). An event row is also required to
have been available the day after its date (a row that arrived later has unknown timing and is dropped).

``event_returns`` computes the features for explicit events from raw closes and the factors effective inside the
window only, plus the group composite on the window sessions. ``ear_3d`` therefore depends on nothing after
``s(e'+1)``; ``pead_drift`` uses the data up to the anchor.
"""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd

from ideation.regime.context import AnchorContext, ffill_levels

SOURCE = "features_events.yaml"
EVENT_WINDOW_DAYS = 120
PEAD_MAX_SESSIONS = 63
EAR_DAYS_CAP = 120


def select_events(ctx: AnchorContext, events: pd.DataFrame) -> pd.DataFrame:
    """Latest complete event per member row. ``events``: ``PitView.earnings_dates()``. Returns a frame with ``row``
    (index into ``ctx.members``), ``event_date`` and ``e_idx`` (panel row of the first session on/after the event)."""
    anchor = ctx.anchor
    mem = ctx.members
    cid = ctx.ids["company_id"].to_numpy()[ctx.col]
    ev = events[events["has_eps_reported"].astype(bool)]
    if ev.empty:
        return pd.DataFrame(columns=["row", "event_date", "e_idx"])
    ed = np.asarray(ev["event_date"].tolist(), dtype="datetime64[D]").astype("int64")
    af = np.asarray(ev["available_from"].tolist(), dtype="datetime64[D]").astype("int64")
    a_day = np.datetime64(anchor, "D").astype("int64")
    lo = np.datetime64(anchor - timedelta(days=EVENT_WINDOW_DAYS), "D").astype("int64")
    ok = (af - ed <= 1) & (ed > lo) & (ed <= a_day)
    ev = ev.assign(_ed=ed)[ok]
    e_idx = np.searchsorted(ctx.sess_days, ev["_ed"].to_numpy(), side="left")
    T = len(ctx.sess_days)
    good = (e_idx >= 2) & (e_idx + 1 <= T - 1)
    ev = ev.assign(e_idx=e_idx)[good]
    ev = ev.sort_values(["company_id", "e_idx", "_ed"], kind="stable").drop_duplicates("company_id", keep="last")
    rows = pd.DataFrame({"row": np.arange(len(mem)), "company_id": cid})
    rows = rows[rows["company_id"].notna()]
    rows["company_id"] = rows["company_id"].astype("int64")
    out = rows.merge(ev[["company_id", "event_date", "e_idx"]], on="company_id", how="inner")
    return out[["row", "event_date", "e_idx"]].reset_index(drop=True)


def event_returns(ctx: AnchorContext, sel: pd.DataFrame) -> pd.DataFrame:
    """Features for the events in ``sel`` (columns ``row``, ``e_idx``), indexed by ``row``."""
    p = ctx.panel
    Lraw = ffill_levels(p.close_raw)
    T = Lraw.shape[0]
    g_all = ctx.comps.group_ret
    out = []
    for row, e in zip(sel["row"].to_numpy(), sel["e_idx"].to_numpy(), strict=True):
        c = ctx.col[row]
        gc = ctx.gcode[c]
        a, b = e - 2, e + 1
        ear = pead = np.nan
        if gc >= 0:
            with np.errstate(invalid="ignore", divide="ignore"):
                stock = np.log(Lraw[b, c] / Lraw[a, c]) - p.log_f[a + 1 : b + 1, c].sum()
                grp = np.log1p(g_all[a + 1 : b + 1, gc]).sum()
            ear = stock - grp
            if T - 1 - e <= PEAD_MAX_SESSIONS and e + 2 < T - 1:
                s0, s1 = e + 2, T - 1
                with np.errstate(invalid="ignore", divide="ignore"):
                    s_ret = Lraw[s1, c] / Lraw[s0, c] / np.exp(p.log_f[s0 + 1 : s1 + 1, c].sum()) - 1.0
                    g_ret = np.exp(np.log1p(g_all[s0 + 1 : s1 + 1, gc]).sum()) - 1.0
                pead = s_ret - g_ret
        out.append((row, float(ear) if np.isfinite(ear) else np.nan, float(min(T - 1 - e, EAR_DAYS_CAP)),
                    float(pead) if np.isfinite(pead) else np.nan))  # fmt: skip
    return pd.DataFrame(out, columns=["row", "ear_3d", "ear_days", "pead_drift"]).set_index("row")


def event_columns(ctx: AnchorContext, events: pd.DataFrame) -> pd.DataFrame:
    n = len(ctx.members)
    sel = select_events(ctx, events)
    res = event_returns(ctx, sel) if len(sel) else pd.DataFrame(columns=["ear_3d", "ear_days", "pead_drift"])
    res = res.reindex(np.arange(n)).astype(float)
    # an event with a window return that cannot be formed (no price) counts as no usable event
    res.loc[res["ear_3d"].isna(), ["ear_days", "pead_drift"]] = np.nan
    res["isna_event"] = res["ear_3d"].isna().astype(float)
    return res.reset_index(drop=True)
