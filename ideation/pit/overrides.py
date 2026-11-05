"""COVID statutory-deadline overrides (02 §1.2). Overrides only ever move a deadline LATER, never earlier.

Rule:

    deadline = max(period_end + lag,
                   period_end + lag + 30d   if period_end in [2019-12-31, 2021-06-30]   (blanket margin),
                   table deadline           if the table for this filing kind has one for this period_end)

P1 verification (2026-11-06) against the SEBI circulars cited on each entry below confirmed every table deadline.
The +30d blanket is deliberately KEPT inside the window even for verified entries: removing it would move some
availability dates earlier than Phase 0 had them (e.g. Q3 FY20 from +76 to +46 days), and the brief allows overrides
to move dates only later. The blanket is a conservative margin for late filers; its cost is a few weeks of staleness
on 2020/21 quarterly facts (bias block item 13).

The blanket alone would make FY2020 (2020-03-31) available on 2020-06-30, but SEBI extended FY2020 results to
2020-07-31, so the table entry wins (T-14: FY2020 annual available >= 2020-08-01). Taking the max keeps both safe.
The results-event match window reaches 15 days past the overridden deadline (``lags.event_window_end``), so the
FY2020 extension (day 122) is inside the window.
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
    """True once checked against the SEBI circular (documentation; the blanket still applies, see module doc)."""
    note: str


# 02 §1.2 "COVID statutory extensions" for LODR Reg 33 (financial results), verified 2026-11-06.
RESULT_DEADLINE_OVERRIDES: Final[dict[date, DeadlineOverride]] = {
    date(2019, 12, 31): DeadlineOverride(
        None,
        True,
        "Q3 FY20 / Dec-2019 FY: normal Reg 33 lag (deadlines 2020-02-14 / 2020-02-29 passed before the first COVID "
        "relaxation, SEBI/HO/CFD/CMD1/CIR/P/2020/38 of 2020-03-19, which covered periods ending 2020-03-31 only)",
    ),
    date(2020, 3, 31): DeadlineOverride(
        date(2020, 7, 31),
        True,
        "Q4/FY20: SEBI/HO/CFD/CMD1/CIR/P/2020/106 of 2020-06-24 extended Reg 33 results for the quarter/year ended "
        "2020-03-31 to 2020-07-31 (after earlier relaxations in SEBI/HO/CFD/CMD1/CIR/P/2020/38 of 2020-03-19)",
    ),
    date(2020, 6, 30): DeadlineOverride(
        date(2020, 9, 15),
        True,
        "Q1 FY21: SEBI/HO/CFD/CMD1/CIR/P/2020/140 of 2020-07-29 extended Reg 33 results for the quarter/half-year "
        "ended 2020-06-30 from 2020-08-14 to 2020-09-15",
    ),
    date(2021, 3, 31): DeadlineOverride(
        date(2021, 6, 30),
        True,
        "Q4/FY21: SEBI/HO/CFD/CMD1/P/CIR/2021/556 of 2021-04-29 extended Reg 33 quarterly (2021-05-15) and annual "
        "(2021-05-30) results for the period ended 2021-03-31 to 2021-06-30",
    ),
    date(2021, 6, 30): DeadlineOverride(
        None,
        False,
        "Q1 FY22: no Reg 33 extension found (filings on the normal 2021-08-14 deadline; the 2021-06-30 circular "
        "SEBI/HO/MIRSD/DOP/P/CIR/2021/587 covers intermediaries, not listed companies); blanket +30d retained",
    ),
}

# LODR Reg 31 (shareholding pattern, 21 days), verified 2026-11-06. The blanket (+30d) already covers this entry.
SHAREHOLDING_DEADLINE_OVERRIDES: Final[dict[date, DeadlineOverride]] = {
    date(2020, 3, 31): DeadlineOverride(
        date(2020, 5, 15),
        True,
        "Quarter ended 2020-03-31: SEBI/HO/CFD/CMD1/CIR/P/2020/38 of 2020-03-19 extended Reg 31 from 2020-04-21 to "
        "2020-05-15",
    ),
}


def in_covid_window(period_end: date) -> bool:
    return COVID_WINDOW_START <= period_end <= COVID_WINDOW_END


def apply_overrides(period_end: date, base_deadline: date, *, kind: FilingKind) -> date:
    """Return the overridden deadline, which is never earlier than ``base_deadline``.

    Each filing kind has its own table; the +30d blanket applies to every period_end inside the COVID window.
    """
    table = RESULT_DEADLINE_OVERRIDES if kind == "results" else SHAREHOLDING_DEADLINE_OVERRIDES
    entry = table.get(period_end)
    candidates = [base_deadline]
    if in_covid_window(period_end):
        candidates.append(base_deadline + timedelta(days=COVID_BLANKET_DAYS))
    if entry is not None and entry.deadline is not None:
        candidates.append(entry.deadline)
    return max(candidates)
