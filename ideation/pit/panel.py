"""Dense price panel for one anchor (P3): sessions x securities arrays built from the PIT price rows.

``PitView.prices_raw`` + ``prices_adj`` cost about 15 s per anchor (object-dtype dates, a Python loop per security).
The tactical matrix needs ~200 anchors in minutes, so this module builds the same quantities with integer dates and
vectorised numpy, under the same availability rules:

* a price row is used iff ``available_from <= anchor`` AND its trade date lies in the last ``lookback`` good sessions
  up to the anchor (a row that claims to be dated after the anchor is dropped, never used) AND it was available on
  its own trade date (``available_from <= date``: EOD rows are usable from their date's close, so a row that arrives
  later than that has no known timing and is not used; the snapshot has none, ``tests/lake`` asserts it);
* the adjustment uses only the factors ``PitView.factors()`` returns (``available_from <= anchor``) with
  ``eff_date <= anchor`` and ``available_from <= eff_date`` (a factor is known by its effective date, same reason):
  ``close_adj[t] = close[t] * prod f`` over factors with ``t < eff_date`` (02 §1.1, T-20), summed in log space.
  ``tests/unit/test_price_panel.py`` pins it against ``back_adjust``;
* ``log_f[t]`` is the log of the factors taking effect on session ``t``. Daily returns are
  ``close[t] / close[t-1] / exp(log_f[t])``: they need no other factor, so a return does not change when later
  factors become known (the cumulative ``close_adj`` offset cancels in a ratio, but a corrupt later factor would
  still contaminate it, so the event and composite code never goes through ``close_adj`` for returns).

The panel is a frozen value object. Its manifest record (item ``price_panel[<n>]``) hashes the raw close, the
traded value and the session/security axes, so a changed input changes the hash.
"""

from __future__ import annotations

import hashlib
import weakref
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc

if TYPE_CHECKING:
    from ideation.pit.contract import PitStore, PitView

_COLS = ("security_id", "date", "close", "turnover_lacs", "deliv_per", "ttl_trd_qnty")
_CACHE: weakref.WeakKeyDictionary[PitStore, dict[str, np.ndarray]] = weakref.WeakKeyDictionary()


@dataclass(frozen=True)
class PricePanel:
    anchor: date
    sessions: np.ndarray  # datetime64[D], ascending, last == anchor, shape (T,)
    security_ids: np.ndarray  # int64 ascending, shape (N,)
    close_raw: np.ndarray  # (T, N) raw bhav close, NaN where the security did not trade
    close_adj: np.ndarray  # (T, N) back-adjusted to the anchor's basis (factors known at the anchor only)
    value_cr: np.ndarray  # (T, N) traded value in INR crore, 0 where the security did not trade (after its first row)
    deliv: np.ndarray  # (T, N) delivery %, NaN when absent
    qty: np.ndarray  # (T, N) traded quantity (shares), 0 where not traded
    log_f: np.ndarray  # (T, N) log of the adjustment factors with effective session t (0 where none)
    sha256: str

    @property
    def traded(self) -> np.ndarray:
        return ~np.isnan(self.close_raw)


def _columns(store: PitStore) -> dict[str, np.ndarray]:
    """The price table as numpy columns in availability order (cached per store instance)."""
    got = _CACHE.get(store)
    if got is None:
        tbl: pa.Table = store.arrow("prices")
        got = {
            "sid": pc.cast(tbl["security_id"], pa.int64()).to_numpy(zero_copy_only=False),
            "day": pc.cast(tbl["date"], pa.int32()).to_numpy(zero_copy_only=False).astype("int64"),
            "close": pc.cast(tbl["close"], pa.float64()).to_numpy(zero_copy_only=False),
            "to": pc.cast(tbl["turnover_lacs"], pa.float64()).to_numpy(zero_copy_only=False),
            "deliv": pc.cast(tbl["deliv_per"], pa.float64()).to_numpy(zero_copy_only=False),
            "qty": pc.cast(tbl["ttl_trd_qnty"], pa.float64()).to_numpy(zero_copy_only=False),
        }
        _CACHE[store] = got
    return got


def build_panel(view: PitView, lookback: int) -> PricePanel:
    """Dense panel of the last ``lookback`` good sessions (fewer at the start of history) up to ``view.anchor``."""
    store = view._store
    cal = store.calendar
    n = min(lookback, cal.good_index(view.anchor) + 1)
    sessions = cal.lookback(view.anchor, n)
    sess_days = sessions.astype("datetime64[D]").astype("int64")
    cols = _columns(store)
    pos = int(np.searchsorted(store.af_array("prices"), np.datetime64(view.anchor, "D"), side="right"))
    day = cols["day"][:pos]
    avail = store.af_array("prices")[:pos].astype("datetime64[D]").astype("int64")
    ti = np.searchsorted(sess_days, day)
    ti_c = np.minimum(ti, len(sess_days) - 1)
    keep = (day >= sess_days[0]) & (day <= sess_days[-1]) & (sess_days[ti_c] == day) & (avail <= day)
    rows = np.flatnonzero(keep)
    sid = cols["sid"][rows]
    sids, ci = np.unique(sid, return_inverse=True)
    t_idx = ti_c[rows]
    shape = (len(sess_days), len(sids))
    close_raw = np.full(shape, np.nan)
    close_raw[t_idx, ci] = cols["close"][rows]
    value = np.zeros(shape)
    value[t_idx, ci] = np.nan_to_num(cols["to"][rows]) / 100.0  # lakh -> crore
    deliv = np.full(shape, np.nan)
    deliv[t_idx, ci] = cols["deliv"][rows]
    qty = np.zeros(shape)
    qty[t_idx, ci] = np.nan_to_num(cols["qty"][rows])

    # ── back-adjustment with factors known at the anchor (view.factors() is guarded on available_from) ───────
    fac = view.factors()
    fac = fac[(fac["eff_date"] <= view.anchor) & (fac["available_from"] <= fac["eff_date"])]
    log_off = np.zeros(shape)
    lf = np.zeros(shape)
    if len(fac) and len(sids):
        f_sid = fac["security_id"].to_numpy(dtype="int64")
        f_col = np.searchsorted(sids, f_sid)
        f_col_c = np.minimum(f_col, len(sids) - 1)
        known = sids[f_col_c] == f_sid
        eff = np.asarray(fac["eff_date"].tolist(), dtype="datetime64[D]").astype("int64")
        f_row = np.searchsorted(sess_days, eff, side="left")  # first session with date >= eff
        use = known & (f_row > 0) & (f_row < len(sess_days))
        np.add.at(lf, (f_row[use], f_col_c[use]), np.log(fac["factor"].to_numpy(dtype=float)[use]))
        log_off = np.cumsum(lf[::-1], axis=0)[::-1] - lf  # sum over factors with eff index strictly after the row
    close_adj = close_raw * np.exp(log_off)

    h = hashlib.sha256()
    for a in (sess_days, sids, np.ascontiguousarray(close_raw), np.ascontiguousarray(value)):
        h.update(a.tobytes())
    return PricePanel(view.anchor, sessions, sids, close_raw, close_adj, value, deliv, qty, lf, h.hexdigest())
