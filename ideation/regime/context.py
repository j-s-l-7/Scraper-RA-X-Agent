"""Per-anchor computation context shared by the technical, event and regime builders (one panel read per anchor).

Everything here is a pure function of one ``PitView``. Shared definitions (so rs_grp, the event features and RRG agree):

* **Daily return** of a security on session t: ``close[t] / close[t-1] / exp(log_f[t])`` when it traded on both t and
  t-1 (actual raw closes, no forward fill; ``log_f`` is the adjustment factor taking effect on t), clipped to
  [-0.5, +1.0]. Multi-day gaps give NaN, not a smeared return. A return needs no factor except the one effective on t,
  so it is the same at every anchor from t on (verified on the snapshot: prices and applied factors are available on
  their own date). A corrupt factor dated after t can never reach it.
* **Level** ``L``: back-adjusted close forward-filled at most ``FFILL`` sessions (a stale quote older than that is NaN).
* **Composite (EW)** of a group on session t: mean daily return of every company-linked security with a valid return on
  t, the group label, and a liquid trailing average: ADV (mean traded value over the previous ``ADV_WINDOW`` sessions,
  excluding t) of at least ``ADV_FLOOR_CR``. Membership uses data before t only, so it does not depend on the anchor.
  A group with fewer than ``MIN_MEMBERS`` such securities on t falls back to its sector's composite, and a sector with
  fewer (or a group without a sector) to the all-security composite (01 §3.4).
* **Foreign series** (WTI, INR, US10Y, VIX, gold) close after NSE: they are read with a one-session lag (01 §3.3).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from ideation.pit.contract import PitView
    from ideation.pit.panel import PricePanel

LOOKBACK = 1010  # sessions: the 3-4y breakout window (1008) plus the anchor and one return day
FFILL = 5
MIN_MEMBERS = 5
RET_CLIP = (-0.5, 1.0)
ADV_WINDOW = 63
ADV_MIN_PERIODS = 21
ADV_FLOOR_CR = 0.2  # same trading floor as U_score_T (config/default.yaml universe.u_score_t.adv63_floor_cr)
INDEX_CODE = "^CRSLDX"  # Nifty 500
FOREIGN = frozenset({"CL=F", "GC=F", "INR=X", "^TNX", "^VIX"})


def daily_returns(close_raw: np.ndarray, log_f: np.ndarray) -> np.ndarray:
    """Daily simple returns from raw closes and the effective-session log factors (see module docstring)."""
    r = np.full(close_raw.shape, np.nan)
    c0, c1 = close_raw[:-1], close_raw[1:]
    with np.errstate(invalid="ignore", divide="ignore"):
        r[1:] = np.exp(np.log(c1) - np.log(c0) - log_f[1:]) - 1.0
    r[~np.isfinite(r)] = np.nan
    return np.clip(r, *RET_CLIP)


def ffill_levels(x: np.ndarray, limit: int = FFILL) -> np.ndarray:
    valid = ~np.isnan(x)
    t = np.arange(x.shape[0])[:, None]
    last = np.maximum.accumulate(np.where(valid, t, -1), axis=0)
    out = np.take_along_axis(x, np.maximum(last, 0), axis=0)
    out[(last < 0) | (t - last > limit)] = np.nan
    return out


def liquid_mask(value_cr: np.ndarray, *, window: int = ADV_WINDOW, floor: float = ADV_FLOOR_CR) -> np.ndarray:
    """(T, N) True where the mean traded value of the previous ``window`` sessions (excluding t) is >= ``floor``.
    Fewer than ``ADV_MIN_PERIODS`` previous sessions give False. Zero-filled values, as in the universe ADV."""
    t_len = value_cr.shape[0]
    cs = np.vstack([np.zeros((1, value_cr.shape[1])), np.cumsum(value_cr, axis=0)])
    idx = np.arange(t_len)
    lo = np.maximum(idx - window, 0)
    n = idx - lo  # previous sessions available (t excluded)
    s = cs[idx] - cs[lo]
    with np.errstate(invalid="ignore", divide="ignore"):
        adv = s / np.maximum(n, 1)[:, None]
    return (adv >= floor) & (n >= ADV_MIN_PERIODS)[:, None]


def asof_levels(df: pd.DataFrame, sess_days: np.ndarray, *, strict: bool, max_gap_days: int = 7) -> np.ndarray:
    """Series value as of each session (last obs dated <= session, or < session when ``strict``), NaN if older
    than ``max_gap_days`` calendar days. ``df`` has ``date`` and ``value`` (already availability filtered)."""
    if df.empty:
        return np.full(len(sess_days), np.nan)
    d = np.asarray(df["date"].tolist(), dtype="datetime64[D]").astype("int64")
    v = df["value"].to_numpy(dtype=float)
    o = np.argsort(d, kind="stable")
    d, v = d[o], v[o]
    pos = np.searchsorted(d, sess_days, side="left" if strict else "right") - 1
    out = np.where(pos >= 0, v[np.maximum(pos, 0)], np.nan)
    gap = sess_days - np.where(pos >= 0, d[np.maximum(pos, 0)], -(10**9))
    out[(pos < 0) | (gap > max_gap_days + (1 if strict else 0))] = np.nan
    return out


@dataclass(frozen=True)
class Composites:
    groups: list[str]
    sectors: list[str]
    group_ret: np.ndarray  # (T, G) EW composite daily simple return (with sector fallback applied)
    group_n: np.ndarray  # (T, G) number of own members with a valid return
    sector_ret: np.ndarray  # (T, S)
    sector_n: np.ndarray  # (T, S)
    market_ret: np.ndarray  # (T,) all liquid securities
    group_sector: np.ndarray  # (G,) sector index of each group, -1 when none


def _mean_by(codes: np.ndarray, n_codes: int, r: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    valid = ~np.isnan(r)
    ind = np.zeros((r.shape[1], n_codes))
    ok = codes >= 0
    ind[np.flatnonzero(ok), codes[ok]] = 1.0
    s = np.where(valid, r, 0.0) @ ind
    n = valid.astype(float) @ ind
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(n > 0, s / n, np.nan), n


def build_composites(r: np.ndarray, grp: np.ndarray, sec: np.ndarray, groups: list[str], sectors: list[str],
                     group_sector: np.ndarray) -> Composites:  # fmt: skip
    """``r``: (T, N) member returns (NaN for non-members / non-trading). Fallback chain group → sector → market."""
    g_ret, g_n = _mean_by(grp, len(groups), r)
    s_ret, s_n = _mean_by(sec, len(sectors), r)
    valid = ~np.isnan(r)
    nv = valid.sum(axis=1)
    m_ret = np.where(nv > 0, np.nansum(r, axis=1) / np.maximum(nv, 1), np.nan)
    s_fill = np.where(s_n >= MIN_MEMBERS, s_ret, m_ret[:, None])
    if len(groups):
        has_sec = group_sector >= 0
        fb = np.where(has_sec[None, :], s_fill[:, np.maximum(group_sector, 0)], m_ret[:, None])
    else:
        fb = g_ret
    g_use = np.where(g_n >= MIN_MEMBERS, g_ret, fb)
    return Composites(groups, sectors, g_use, g_n, s_fill, s_n, m_ret, group_sector)


@dataclass(frozen=True)
class AnchorContext:
    anchor: date
    panel: PricePanel
    ids: pd.DataFrame  # per panel security: security_id, company_id, industry_group, sector (NaN when unlinked)
    members: pd.DataFrame  # the rows to emit (subset of panel securities), with their panel column
    col: np.ndarray  # panel column of every member row
    L: np.ndarray  # (T, N) forward-filled adjusted level
    R: np.ndarray  # (T, N) daily returns (all securities, unmasked)
    comps: Composites
    gcode: np.ndarray  # (N,) group index or -1
    scode: np.ndarray  # (N,) sector index or -1
    idx_level: np.ndarray  # (T,) Nifty 500 level (India, same-day)
    idx_ret: np.ndarray  # (T,) its daily return
    sess_days: np.ndarray  # (T,) int64 day numbers


def security_labels(view: PitView, sids: np.ndarray) -> pd.DataFrame:
    """security_id -> (company_id, industry_group, sector). Identity/reference data (documented hindsight: the
    company link and today's taxonomy, as in P2)."""
    sm = view.securities()
    sm = sm[sm["company_id"].notna()].sort_values(["security_id", "valid_from"], kind="stable")
    link = sm.drop_duplicates("security_id", keep="last").set_index("security_id")["company_id"]
    co = view.company().set_index("company_id")[["industry_group", "sector"]]
    out = pd.DataFrame({"security_id": sids})
    out["company_id"] = out["security_id"].map(link)
    out["industry_group"] = out["company_id"].map(co["industry_group"])
    out["sector"] = out["company_id"].map(co["sector"])
    return out


def build_context(view: PitView, members: pd.DataFrame) -> AnchorContext:
    """``members``: rows with ``security_id`` to emit (any extra columns are carried through)."""
    panel = view.price_panel(LOOKBACK)
    ids = security_labels(view, panel.security_ids)
    r = daily_returns(panel.close_raw, panel.log_f)
    r_mem = np.where(liquid_mask(panel.value_cr), r, np.nan)
    groups = sorted(ids["industry_group"].dropna().unique().tolist())
    sectors = sorted(ids["sector"].dropna().unique().tolist())
    g_of = {g: i for i, g in enumerate(groups)}
    s_of = {s: i for i, s in enumerate(sectors)}
    gcode = ids["industry_group"].map(g_of).fillna(-1).to_numpy(dtype=int)
    scode = ids["sector"].map(s_of).fillna(-1).to_numpy(dtype=int)
    first = ids.dropna(subset=["industry_group", "sector"]).drop_duplicates("industry_group")
    gs = first.set_index("industry_group")["sector"].map(s_of)
    group_sector = np.array([int(gs.get(g, -1)) for g in groups], dtype=int)
    # market composite: every liquid security; group / sector composites: the company-linked ones
    comps = build_composites(r_mem, gcode, scode, groups, sectors, group_sector)
    pos = pd.Series(np.arange(len(panel.security_ids)), index=panel.security_ids)
    mem = members[members["security_id"].isin(pos.index)].reset_index(drop=True)
    col = pos.loc[mem["security_id"]].to_numpy()
    sess_days = panel.sessions.astype("datetime64[D]").astype("int64")
    idx = view.macro([INDEX_CODE])
    lvl = asof_levels(idx, sess_days, strict=False)
    ir = np.full(len(lvl), np.nan)
    ir[1:] = lvl[1:] / lvl[:-1] - 1.0
    return AnchorContext(view.anchor, panel, ids, mem, col, ffill_levels(panel.close_adj), r, comps, gcode, scode,
                         lvl, ir, sess_days)  # fmt: skip
