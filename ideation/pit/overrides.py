"""COVID statutory-deadline overrides (02 §1.2). Overrides only ever move a deadline LATER, never earlier.

Rule (until each table entry is verified against the SEBI circulars, P1 sign-off item):

    deadline = max(period_end + lag,
                   period_end + lag + 30d   if period_end in [2019-12-31, 2021-06-30] and the entry is not verified,
                   table deadline           if the table has one for this period_end (results only))

The blanket alone would make FY2020 (2020-03-31) available on 2020-06-30, but SEBI extended FY2020 results to
2020-07-31, so the table entry wins (T-14: FY2020 annual available >= 2020-08-01). Taking the max keeps both safe.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Final, Literal

COVID_WINDOW_START: Final = date(2019, 12, 31)
COVID_WINDOW_END: Final = date(2021, 6, 30)
COVID_BLANKET_DAYS: Final = 30

FilingKind = Literal["results", "shareholding"]


@dataclass(frozen=True)
class DeadlineOverride:
    deadline: date | None
    """Extended deadline used for this period_end, or None when the circular kept the normal lag."""
    verified: bool
    """True once checked against the SEBI circular; a verified entry replaces the +30d blanket."""
    note: str


# 02 §1.2 "COVID statutory extensions". None of these is verified yet, so the blanket also applies to each.
RESULT_DEADLINE_OVERRIDES: Final[dict[date, DeadlineOverride]] = {
    date(2019, 12, 31): DeadlineOverride(None, False, "Q3 FY20: normal +45 per 02; blanket applies until verified"),
    date(2020, 3, 31): DeadlineOverride(date(2020, 7, 31), False, "Q4/FY20 results"),
    date(2020, 6, 30): DeadlineOverride(date(2020, 9, 15), False, "Q1 FY21 results"),
    date(2021, 3, 31): DeadlineOverride(date(2021, 6, 30), False, "Q4/FY21 results"),
    date(2021, 6, 30): DeadlineOverride(None, False, "Q1 FY22: to verify; blanket +30d"),
}


def in_covid_window(period_end: date) -> bool:
    return COVID_WINDOW_START <= period_end <= COVID_WINDOW_END


def apply_overrides(period_end: date, base_deadline: date, *, kind: FilingKind) -> date:
    """Return the overridden deadline, which is never earlier than ``base_deadline``.

    The results table applies only to results filings; shareholding gets the blanket only.
    """
    entry = RESULT_DEADLINE_OVERRIDES.get(period_end) if kind == "results" else None
    candidates = [base_deadline]
    if in_covid_window(period_end) and not (entry is not None and entry.verified):
        candidates.append(base_deadline + timedelta(days=COVID_BLANKET_DAYS))
    if entry is not None and entry.deadline is not None:
        candidates.append(entry.deadline)
    return max(candidates)
