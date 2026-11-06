"""Forward-return labels with exits (02 §3.2–3.4, 03 §C3/§C5, 05 §1.3). Bulk computation over a ``PitStore``.

Timing (03 §C3):

* entry = close of the first trade on or after ``s1(A)``, the first good session after A. A later first trade is
  flagged ``entry_delayed``.
* ``label_end`` = the last good session on or before ``A + h`` (calendar months).
* exit = the last trade on or before ``label_end``. ``label_available_from = label_end``.

**Returns.** The total return is ``exp(Σ adjusted log returns over the trades in (entry, exit]) − 1``, with
``ln(c_t / c_{t−1}) − ln F_t`` and F = split/bonus/jump/dividend factors, own adjustment on the stitched series.
``ret_price`` leaves out dividends. Only trades in ``[entry, exit]`` are read, so prices on or before A cannot
change a label (T-03). A factor counts only if it was available by ``label_end``.

**Exits** (02 §3.3, classified PIT). Every rule uses data on or before ``label_end``; whether a suspended name
resumed later is never consulted.

* ``normal``: a trade in the final 20 sessions of the window.
* ``distress`` (−100%): no trade in the final 20 sessions, and either the last trade was in series BZ, or the last
  60 sessions before it were all BE with the last close below 50% of entry.
* ``corp_exit``: no trade in the final 20 sessions; last series EQ, ADV63 at the last trade ≥ 25% of its 1-year
  median, and no >50% fall. Return = last traded close, then cash.
* ``stale``: no trade in the final 20 sessions and neither rule fits. Return = last traded close (02's
  "suspension"). Same economics as ``corp_exit``.
* ``ret_total_last_price`` is the 03 §A9 sensitivity: distress exits at the last traded close.
* No trade at all in ``[s1(A), label_end]``: entry is unknown, the return is −100% when distressed, else 0 (cash).
  Flagged ``no_trade_after_anchor``.

**Cross-sectional variants** (02 §3.4, 05 §1.3), per (anchor, horizon):

* ``excess_univ_med``: over every ``traded_ok`` name (full traded universe, uncovered and delisted included).
* ``excess_univ_cw``: against the mcap-weighted TR of ``U_score``.
* ``excess_n500_pr``: against the ``^CRSLDX`` price return (dividend gap disclosed).
* ``xs_rank``: within ``U_score`` (1Y) or ``U_score_T`` (3M/1M); NULL outside it.

Labels for sealed holdout anchors are built and stored like any other. Every read goes through
``labels/loader.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from ideation.pit.calendar import TradingCalendar

if TYPE_CHECKING:
    from ideation.pit.contract import PitStore

HORIZON_MONTHS = {"1Y": 12, "3M": 3, "1M": 1}
FINAL_WINDOW_SESSIONS = 20
BE_RUN_SESSIONS = 60
COLLAPSE = 0.5
CORP_EXIT_LIQ_SHARE = 0.25
DAY0 = np.datetime64("1970-01-01", "D")


def _days(values: Any) -> np.ndarray:
    return (np.asarray(pd.to_datetime(pd.Series(values)).to_numpy(), dtype="datetime64[D]") - DAY0).astype(np.int64)


def _date(day: int) -> date:
    return (DAY0 + np.timedelta64(int(day), "D")).astype(object)


@dataclass
class Panel:
    """Prices of all securities as one array, sorted by (security, date), with per-security offsets."""

    sid: np.ndarray
    day: np.ndarray
    close: np.ndarray
    series: np.ndarray
    turnover: np.ndarray
    key: np.ndarray  # sid_index * 10^6 + day, sorted
    f_tot: np.ndarray  # Σ ln F (all applied factors) at trades <= k, within security
    f_px: np.ndarray  # same without dividends
    sid_index: dict[int, int]
    start: np.ndarray  # first row of each security index
    late: pd.DataFrame = field(default_factory=pd.DataFrame)  # factors available after their eff_date


def build_panel(prices: pd.DataFrame, factors: pd.DataFrame) -> Panel:
    p = prices[["security_id", "date", "close", "series", "turnover_lacs"]].sort_values(["security_id", "date"])
    sid = p["security_id"].to_numpy(np.int64)
    day = _days(p["date"])
    uniq, start = np.unique(sid, return_index=True)
    sidx = np.searchsorted(uniq, sid)
    key = sidx * 1_000_000 + day
    close = p["close"].to_numpy(float)
    f = factors[factors["apply"].astype(bool)].copy()
    f = f[f["security_id"].isin(uniq)]
    fkey = np.searchsorted(uniq, f["security_id"].to_numpy(np.int64)) * 1_000_000 + _days(f["eff_date"])
    pos = np.searchsorted(key, fkey)
    ok = (pos < len(key)) & (key[np.minimum(pos, len(key) - 1)] == fkey)
    lnf = np.log(f["factor"].to_numpy(float))
    is_div = (f["kind"] == "dividend").to_numpy()
    tot, px = np.zeros(len(key)), np.zeros(len(key))
    np.add.at(tot, pos[ok], lnf[ok])
    np.add.at(px, pos[ok & ~is_div], lnf[ok & ~is_div])
    f = f.assign(pos=np.where(ok, pos, -1), lnf=lnf, div=is_div, avail=_days(f["available_from"]))
    late = f[ok & (f["avail"].to_numpy() > _days(f["eff_date"]))][["pos", "lnf", "div", "avail"]]
    return Panel(
        sid=sid, day=day, close=close, series=p["series"].to_numpy(object), turnover=p["turnover_lacs"].to_numpy(float),
        key=key, f_tot=_cumsum_by_group(tot, start), f_px=_cumsum_by_group(px, start),
        sid_index={int(s): i for i, s in enumerate(uniq)}, start=start, late=late.reset_index(drop=True),
    )  # fmt: skip


def _cumsum_by_group(x: np.ndarray, start: np.ndarray) -> np.ndarray:
    c = np.cumsum(x)
    base = np.repeat(c[start] - x[start], np.diff(np.append(start, len(x))))
    return c - base


@dataclass(frozen=True)
class LabelSpec:
    anchor: date
    horizon: str
    entry_session: date
    label_end: date


def label_specs(cal: TradingCalendar, anchors: list[date], horizons: list[str]) -> list[LabelSpec]:
    out = []
    for a in anchors:
        s1 = cal.entry_session(a)
        for h in horizons:
            end = cal.exit_session(a, HORIZON_MONTHS[h])
            if s1 is None or end is None or end < s1:
                continue  # the calendar does not reach A + h yet
            out.append(LabelSpec(a, h, s1, end))
    return out


def compute_labels(
    store: PitStore, universe: pd.DataFrame, specs: list[LabelSpec], *, panel: Panel | None = None
) -> pd.DataFrame:
    """Labels for every ``traded_ok`` universe row at each spec's anchor. ``universe``: one or more anchors of
    universe_member rows (security_id, anchor, traded_ok, in_u_score, in_u_score_t, mcap_pit_cr)."""
    cal = store.calendar
    max_end = max(s.label_end for s in specs)
    # Only data available by the latest label_end is read; each label is then restricted to its own label_end.
    view = store.as_of(max_end, record=False)
    prices = view.prices_raw(columns=["security_id", "date", "close", "series", "turnover_lacs"])
    factors = store.table("factors")
    factors = factors[factors["available_from"] <= max_end]
    pn = panel or build_panel(prices, factors)
    rights = view.rights()
    idx = view.macro(["^CRSLDX"])
    n500 = pd.Series(idx["value"].to_numpy(float), index=_days(idx["date"]))
    good_days = (cal.good - DAY0).astype(np.int64)
    covered = set(store.table("factors").query("source == 'adj_factor'")["security_id"].unique())

    frames = []
    for sp in specs:
        u = universe[(universe["anchor"] == sp.anchor) & universe["traded_ok"]]
        if u.empty:
            continue
        frames.append(_labels_for_spec(sp, u, pn, cal, good_days, rights, n500, covered))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _labels_for_spec(
    sp: LabelSpec,
    u: pd.DataFrame,
    pn: Panel,
    cal: TradingCalendar,
    good_days: np.ndarray,
    rights: pd.DataFrame,
    n500: pd.Series,
    covered: set[int],
) -> pd.DataFrame:
    sids = u["security_id"].to_numpy(np.int64)
    gi = np.array([pn.sid_index.get(int(s), -1) for s in sids])
    s1, end = _days([sp.entry_session])[0], _days([sp.label_end])[0]
    lo = np.searchsorted(pn.key, gi * 1_000_000 + s1, side="left")
    hi = np.searchsorted(pn.key, gi * 1_000_000 + end, side="right") - 1  # last trade <= label_end
    same = lambda i: (i >= 0) & (i < len(pn.key)) & (pn.sid[np.clip(i, 0, len(pn.key) - 1)] == sids)  # noqa: E731
    has_entry = (gi >= 0) & same(lo) & (pn.day[np.clip(lo, 0, len(pn.key) - 1)] <= end)
    has_last = (gi >= 0) & same(hi)
    e = np.where(has_entry, lo, 0)
    x = np.where(has_last, hi, 0)
    # price leg = ln(c_x / c_e) from those two closes only (a cumulative sum would let pre-entry prices change the
    # floating-point rounding, which the bit-exact T-03 catches); factor leg = Σ ln F over trades in (e, x]
    with np.errstate(divide="ignore", invalid="ignore"):
        ln_raw = np.log(pn.close[x] / pn.close[e])
    ln_tot = ln_raw - (pn.f_tot[x] - pn.f_tot[e])
    ln_px = ln_raw - (pn.f_px[x] - pn.f_px[e])
    if len(pn.late):  # factors not yet available at label_end: undo their adjustment
        for r in pn.late.itertuples(index=False):
            if r.avail <= end:
                continue
            hit = has_entry & (r.pos > e) & (r.pos <= x)
            ln_tot = np.where(hit, ln_tot + r.lnf, ln_tot)
            if not r.div:
                ln_px = np.where(hit, ln_px + r.lnf, ln_px)
    # final-window rule: a trade in the last 20 good sessions up to label_end
    end_pos = int(np.searchsorted(good_days, end, side="right")) - 1
    win_start = good_days[max(0, end_pos - FINAL_WINDOW_SESSIONS + 1)]
    last_day = np.where(has_last, pn.day[x], -1)
    normal = has_entry & (last_day >= win_start)
    entry_px = np.where(has_entry, pn.close[e], np.nan)
    exit_px = np.where(has_last & has_entry, pn.close[x], np.nan)
    exit_type = np.where(normal, "normal", "stale").astype(object)
    stale_days = np.zeros(len(sids), dtype=np.int64)
    flags: list[list[str]] = [[] for _ in sids]
    for i in np.flatnonzero(~normal):
        if has_last[i]:
            stale_days[i] = max(0, end_pos - int(np.searchsorted(good_days, last_day[i], side="right")) + 1)
        exit_type[i] = _classify_exit(pn, int(x[i]) if has_last[i] else -1, int(e[i]) if has_entry[i] else -1,
                                      bool(has_entry[i]), good_days)  # fmt: skip
        if not has_entry[i]:
            flags[i].append("no_trade_after_anchor")
    ret_total = np.expm1(ln_tot)
    ret_price = np.expm1(ln_px)
    no_entry = ~has_entry
    ret_total = np.where(no_entry, 0.0, ret_total)
    ret_price = np.where(no_entry, 0.0, ret_price)
    ret_last = ret_total.copy()
    distress = exit_type == "distress"
    ret_total = np.where(distress, -1.0, ret_total)
    ret_price = np.where(distress, -1.0, ret_price)
    ret_last = np.where(distress & no_entry, 0.0, ret_last)
    entry_day = np.where(has_entry, pn.day[e], s1)
    for i in range(len(sids)):
        if has_entry[i] and entry_day[i] > s1:
            flags[i].append("entry_delayed")
        if int(sids[i]) not in covered:
            flags[i].append("no_div_data")
    _flag_events(flags, sids, entry_day, last_day, end, rights, pn)
    out = pd.DataFrame(
        {
            "anchor": sp.anchor, "security_id": sids, "horizon": sp.horizon,
            "entry_date": [_date(d) for d in entry_day], "label_end": sp.label_end,
            "label_available_from": sp.label_end,
            "ret_total": ret_total, "ret_price": ret_price, "ret_total_last_price": ret_last,
            "exit_type": exit_type, "stale_days": stale_days, "entry_price": entry_px, "exit_price": exit_px,
            "last_trade": [_date(d) if d >= 0 else None for d in last_day],
            "flags": [sorted(set(f)) for f in flags],
        }
    )  # fmt: skip
    # cross-sectional variants
    med = float(np.nanmedian(out["ret_total"])) if len(out) else np.nan
    out["excess_univ_med"] = out["ret_total"] - med
    us = u.set_index("security_id")
    in_score = out["security_id"].map(us["in_u_score"]).fillna(False).astype(bool)
    in_t = out["security_id"].map(us["in_u_score_t"]).fillna(False).astype(bool)
    w = out["security_id"].map(us["mcap_pit_cr"]).where(in_score)
    cw = float(np.nansum(w * out["ret_total"]) / np.nansum(w)) if np.nansum(w) > 0 else np.nan
    out["excess_univ_cw"] = out["ret_total"] - cw
    i0, i1 = n500.index.searchsorted(s1, "right") - 1, n500.index.searchsorted(end, "right") - 1
    bench = n500.iloc[i1] / n500.iloc[i0] - 1 if i0 >= 0 and i1 >= 0 else np.nan
    out["excess_n500_pr"] = out["ret_total"] - bench
    pool = in_score if sp.horizon == "1Y" else in_t
    out["xs_rank"] = out["ret_total"].where(pool).rank(pct=True, method="average")
    return out


def _classify_exit(pn: Panel, x: int, e: int, has_entry: bool, good_days: np.ndarray) -> str:
    """02 §3.3 at label_end (PIT): distress / corp_exit / stale for a name with no trade in the final 20 sessions."""
    if x < 0:
        return "stale"
    if pn.series[x] == "BZ":
        return "distress"
    entry_close = pn.close[e] if has_entry else np.nan
    collapsed = has_entry and pn.close[x] < COLLAPSE * entry_close
    first = pn.start[np.searchsorted(pn.start, x, side="right") - 1]
    lp = int(np.searchsorted(good_days, pn.day[x], side="right")) - 1
    be_from = good_days[max(0, lp - BE_RUN_SESSIONS + 1)]
    run = slice(first, x + 1)
    in_run = pn.day[run] >= be_from
    if collapsed and in_run.any() and (pn.series[run][in_run] == "BE").all():
        return "distress"
    if pn.series[x] == "EQ" and not collapsed and _liquid_at_exit(pn, first, x, good_days):
        return "corp_exit"
    return "stale"


def _liquid_at_exit(pn: Panel, first: int, x: int, good_days: np.ndarray) -> bool:
    """ADV63 (zero-filled) at the last trade >= 25% of its median over the preceding year (02 §3.3)."""
    lp = int(np.searchsorted(good_days, pn.day[x], side="right")) - 1
    lo = max(0, lp - 252 - 62)
    days = good_days[lo : lp + 1]
    t = pd.Series(0.0, index=days)
    rows = slice(first, x + 1)
    sel = pn.day[rows] >= days[0]
    t.loc[pn.day[rows][sel]] = pn.turnover[rows][sel]
    adv = t.rolling(63, min_periods=63).mean().dropna()
    if adv.empty:
        return False
    return bool(adv.iloc[-1] >= CORP_EXIT_LIQ_SHARE * float(adv.tail(252).median()))


def _flag_events(
    flags: list[list[str]], sids: np.ndarray, entry_day: np.ndarray, last_day: np.ndarray, end: int,
    rights: pd.DataFrame, pn: Panel,
) -> None:  # fmt: skip
    if len(rights):
        r = rights[_days(rights["available_from"]) <= end]
        by: dict[int, np.ndarray] = {int(k): _days(g["ex_date"]) for k, g in r.groupby("security_id")}
        for i, s in enumerate(sids):
            d = by.get(int(s))
            if d is not None and ((d > entry_day[i]) & (d <= max(last_day[i], entry_day[i]))).any():
                flags[i].append("rights_unadjusted")
