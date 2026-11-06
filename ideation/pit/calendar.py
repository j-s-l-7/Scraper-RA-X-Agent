"""NSE trading calendar (02 §2.3) with 03 §C3 snapping. Pure: built from the snapshot's ``calendar`` dataset.

``calendar`` = bhav sessions ∪ ``^NSEI`` sessions. Sessions present only in the index are ``bhav_missing``
(2019-09-20..30 at the bhav-format seam, 2021-11-04 Muhurat, 2025-02-01 budget Saturday). A *good* session has bhav.

Snapping rules:

* **Anchors** never fall on a bhav-missing session: ``snap_anchor(d)`` = the last good session on or before ``d``
  (2019-09-30 → 2019-09-19, T-23).
* **Entry** = ``s1(A)``, the first good session after A. An entry that would land on a missing session rolls
  forward (anchor 2019-09-19 → entry 2019-10-01).
* **Exit** = the last good session on or before ``A + h`` (03 §C3, which overrides 02 §2.3's roll-forward for exits,
  so a label never ends after ``A + h``. 02 T-23's "exit 2019-09-25 rolls to 2019-10-01" is amended to 2019-09-19).
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

__all__ = ["CalendarError", "TradingCalendar", "add_months"]


class CalendarError(ValueError):
    pass


def add_months(d: date, months: int) -> date:
    """Calendar-month addition with end-of-month clipping (2016-01-31 + 1M = 2016-02-29)."""
    return (pd.Timestamp(d) + pd.DateOffset(months=months)).date()


def _d64(d: date | np.datetime64 | pd.Timestamp) -> np.datetime64:
    return np.datetime64(pd.Timestamp(d).date(), "D")


class TradingCalendar:
    """Sessions (sorted, unique) with a ``bhav_missing`` flag. All lookups are O(log n) on numpy arrays."""

    def __init__(self, sessions: pd.Series | list[date], bhav_missing: pd.Series | list[bool]) -> None:
        s = np.asarray(pd.to_datetime(pd.Series(list(sessions))).dt.date.tolist(), dtype="datetime64[D]")
        m = np.asarray(list(bhav_missing), dtype=bool)
        if len(s) != len(m):
            raise CalendarError("sessions and bhav_missing differ in length")
        order = np.argsort(s, kind="stable")
        s, m = s[order], m[order]
        if len(s) and (np.diff(s).astype(int) <= 0).any():
            raise CalendarError("duplicate sessions")
        self.sessions = s
        self.missing = m
        self.good = s[~m]
        self.good.setflags(write=False)
        self.sessions.setflags(write=False)

    @classmethod
    def from_frame(cls, df: pd.DataFrame) -> TradingCalendar:
        return cls(df["session"].tolist(), df["bhav_missing"].tolist())

    # ── lookups ─────────────────────────────────────────────────────────────────────────────────────────────
    def is_good(self, d: date) -> bool:
        x = _d64(d)
        i = np.searchsorted(self.good, x)
        return bool(i < len(self.good) and self.good[i] == x)

    def last_good_on_or_before(self, d: date) -> date:
        i = int(np.searchsorted(self.good, _d64(d), side="right")) - 1
        if i < 0:
            raise CalendarError(f"no good session on or before {d}")
        return self.good[i].astype(object)

    def first_good_after(self, d: date) -> date | None:
        i = int(np.searchsorted(self.good, _d64(d), side="right"))
        return self.good[i].astype(object) if i < len(self.good) else None

    def snap_anchor(self, d: date) -> date:
        return self.last_good_on_or_before(d)

    def entry_session(self, anchor: date) -> date | None:
        """``s1(A)``: first good session strictly after the anchor."""
        return self.first_good_after(anchor)

    def exit_session(self, anchor: date, months: int) -> date | None:
        """Last good session on or before ``A + months`` (03 §C3); None if the calendar does not reach it yet."""
        target = add_months(anchor, months)
        if len(self.sessions) == 0 or _d64(target) > self.sessions[-1]:
            return None
        return self.last_good_on_or_before(target)

    def lookback(self, anchor: date, n: int) -> np.ndarray:
        """The ``n`` good sessions ending at (and including) the snapped anchor."""
        i = int(np.searchsorted(self.good, _d64(anchor), side="right"))
        if i < n:
            raise CalendarError(f"fewer than {n} good sessions up to {anchor}")
        return self.good[i - n : i]

    def good_index(self, d: date) -> int:
        """Position of the last good session on or before ``d`` (0-based)."""
        return int(np.searchsorted(self.good, _d64(d), side="right")) - 1

    def n_good_between(self, start: date, end: date) -> int:
        """Good sessions in ``[start, end]``."""
        lo = int(np.searchsorted(self.good, _d64(start), side="left"))
        hi = int(np.searchsorted(self.good, _d64(end), side="right"))
        return max(0, hi - lo)

    def month_end_anchors(self, start: date, end: date) -> list[date]:
        """Last good session of every calendar month whose month-end lies in ``[start, end]``."""
        out: list[date] = []
        for me in pd.date_range(pd.Timestamp(start), pd.Timestamp(end), freq="ME"):
            out.append(self.snap_anchor(me.date()))
        return out

    def annual_anchors(self, month: int, first_year: int, last_year: int) -> list[date]:
        """June (``month=6``) or March anchors: last good session on or before the month end."""
        return [
            self.snap_anchor((pd.Timestamp(year=y, month=month, day=1) + pd.offsets.MonthEnd(0)).date())
            for y in range(first_year, last_year + 1)
        ]


FIRST_MONTHLY_ANCHOR = date(2010, 4, 1)
"""The first month-end with 63 bhav sessions behind it (bhav starts 2010-01-04)."""


def parse_anchors(cal: TradingCalendar, spec: str) -> list[date]:
    """``june``, ``march``, ``monthly`` (every month-end with a full 63-session window, up to the last complete
    month) and/or explicit ``YYYY-MM-DD`` dates, comma-separated. Every anchor is snapped to a good session."""
    last = cal.good[-1].astype(object)
    out: set[date] = set()
    month_ends = [
        cal.snap_anchor(me.date())
        for me in pd.date_range(pd.Timestamp(FIRST_MONTHLY_ANCHOR), pd.Timestamp(last), freq="ME")
        if me.date() <= last
    ]
    for tok in (t.strip() for t in spec.split(",") if t.strip()):
        if tok == "monthly":
            out.update(month_ends)
        elif tok in ("june", "march"):
            m = 6 if tok == "june" else 3
            out.update(
                cal.snap_anchor((pd.Timestamp(year=y, month=m, day=1) + pd.offsets.MonthEnd(0)).date())
                for y in range(FIRST_MONTHLY_ANCHOR.year, last.year + 1)
                if FIRST_MONTHLY_ANCHOR
                <= (pd.Timestamp(year=y, month=m, day=1) + pd.offsets.MonthEnd(0)).date()
                <= last
            )
        else:
            out.add(cal.snap_anchor(date.fromisoformat(tok)))
    return sorted(out)
