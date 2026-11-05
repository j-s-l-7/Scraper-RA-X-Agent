"""Point-in-time availability rules (02 §1.2; label timing 03 §C3).

Vendored, not imported (the repo must stand alone), from Scraper ``screener_scraper/completeness.py`` at commit
a478ebb0f7492db3a9936f619c6376b9c501c572 (2026-11-04): ``RESULT_LAG``, ``YEAR_RESULT_LAG``, ``SHP_LAG``,
``month_end``, ``quarter_end``, ``fy_of`` and ``result_lag``. ``tests/unit/test_lag_pins.py`` pins them.

Conventions (a row may be used at anchor A iff ``available_from <= A``; decisions are taken after A's close):

* Facts and shareholding: ``available_from = deadline + 1 day``, where ``deadline = period_end + statutory lag``
  with COVID overrides (which only move it later). A matched results event moves it later still, never earlier.
* Macro: ``available_from = month_end(period) + release lag``. 02's table already includes the release day: no +1.
* Labels (03 §C3): ``label_available_from = label_end`` (EOD prices are usable after that close). Not facts.

Nothing here reads the clock: the caller passes ``today`` (the data observation date) explicitly.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date, timedelta
from typing import Final

from ideation.pit.overrides import apply_overrides

# ── vendored from Scraper completeness.py (LODR 33 / LODR 31) ────────────────────────────────────────────────
RESULT_LAG: Final = 45  # LODR 33: quarterly results within 45 days
YEAR_RESULT_LAG: Final = 60  # LODR 33: the year's audited results within 60 days
SHP_LAG: Final = 21  # LODR 31: shareholding pattern within 21 days of the quarter end


def month_end(y: int, m: int) -> date:
    return date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)


def quarter_end(d: date) -> date:
    return month_end(d.year, 3 * ((d.month - 1) // 3) + 3)


def fy_of(period_end: date, fy_month: int = 3) -> int:
    """Fiscal-year label of a period end (FY ends in ``fy_month``; label = the calendar year it ends in)."""
    return period_end.year + (period_end.month > fy_month)


def result_lag(qend: date, fy_month: int) -> int:
    return YEAR_RESULT_LAG if qend.month == fy_month else RESULT_LAG


# ── ideation rules (02 §1.2) ─────────────────────────────────────────────────────────────────────────────────
EVENT_MATCH_WINDOW_DAYS: Final = 120
"""Minimum event match window: an event matches iff ``period_end < event_date <= window_end`` (02 §1.2)."""
EVENT_MATCH_SLACK_DAYS: Final = 15
"""The window also reaches ``slack`` days past the (COVID-overridden) statutory deadline, so an extended deadline
beyond day 120 is still covered (FY2020: deadline 2020-07-31 is day 122). Widening only ever moves availability
LATER (a later-matched event can only raise ``max(deadline + 1, event + 1)``), the conservative direction."""
ONE_DAY: Final = timedelta(days=1)


def event_window_end(period_end: date, period_type: str = "A", fy_month: int = 3) -> date:
    """Last event date that may match ``period_end``: ``max(pe + 120d, overridden deadline + 15d)``."""
    deadline = results_deadline(period_end, period_type, fy_month)
    return max(period_end + timedelta(days=EVENT_MATCH_WINDOW_DAYS), deadline + timedelta(days=EVENT_MATCH_SLACK_DAYS))


def match_event(
    period_end: date, event_dates: Iterable[date], *, today: date, period_type: str = "A", fy_month: int = 3
) -> date | None:
    """First results event for ``period_end`` (02 §1.2), or None.

    Callers pass only events with ``eps_reported`` not null. Events after ``today`` are scheduled, not reported
    (the lake has 2027-dated rows), so they are ignored.
    """
    hi = event_window_end(period_end, period_type, fy_month)
    eligible = [d for d in event_dates if period_end < d <= hi and d <= today]
    return min(eligible, default=None)


def results_deadline(period_end: date, period_type: str, fy_month: int) -> date:
    """Statutory results deadline with COVID overrides. Only annual ('A') and quarterly ('Q') facts are dated.

    'TTM' is live-only (02 §1.2: banned in backtests) and 'H' (SME half-years, unplanned) is not supported yet.
    Both raise rather than get a guessed lag.
    """
    if period_type == "A":
        lag = YEAR_RESULT_LAG
    elif period_type == "Q":
        lag = result_lag(period_end, fy_month)
    else:
        raise ValueError(f"no availability rule for period_type={period_type!r} (TTM is live-only; H unsupported)")
    return apply_overrides(period_end, period_end + timedelta(days=lag), kind="results")


def fact_available_from(
    period_end: date, period_type: str, fy_month: int, event_date: date | None = None, *, today: date
) -> date:
    """First date a financial fact may be used: ``max(deadline + 1, event_date + 1)`` (later wins).

    ``event_date`` should come from :func:`match_event`. An event after ``today`` (future-dated) is ignored. An
    event outside the match window means the caller paired the wrong event, so it raises.
    """
    available = results_deadline(period_end, period_type, fy_month) + ONE_DAY
    if event_date is None or event_date > today:
        return available
    if not period_end < event_date <= event_window_end(period_end, period_type, fy_month):
        raise ValueError(f"event {event_date} is outside the match window of period_end {period_end}")
    return max(available, event_date + ONE_DAY)


def shp_available_from(period_end: date) -> date:
    """Shareholding pattern: ``period_end + 21d (+ COVID blanket) + 1``. The results table does not apply."""
    deadline = apply_overrides(period_end, period_end + timedelta(days=SHP_LAG), kind="shareholding")
    return deadline + ONE_DAY


def label_available_from(label_end: date) -> date:
    """03 §C3 (overrides 02 §3.1): a label is usable from its exit session's close, i.e. on ``label_end``."""
    return label_end


# ── macro (02 §1.2) ──────────────────────────────────────────────────────────────────────────────────────────
MACRO_MONTHLY_LAG_DAYS: Final[dict[str, int]] = {"CPI": 15, "WPI": 16, "IIP": 45}
WORLD_BANK_LAG_DAYS: Final = 270
FPI_SECTOR_LAG_DAYS: Final = 7
_WORLD_BANK_CODE = re.compile(r"[A-Z]{2}\.[A-Z0-9]+(?:\.[A-Z0-9]+)*")
_DAILY_SUFFIXES = ("=X", "=F", ".NS")


def macro_kind(code: str) -> str:
    """Release family of a ``macro.series`` code. Unknown codes raise: no silent default lag."""
    for family in MACRO_MONTHLY_LAG_DAYS:
        if code.startswith(f"MOSPI_{family}_"):
            return family
    if _WORLD_BANK_CODE.fullmatch(code):
        return "WORLD_BANK"
    if code.startswith("^") or code.endswith(_DAILY_SUFFIXES):
        return "DAILY"
    raise KeyError(f"no release-lag rule for macro code {code!r}")


def macro_available_from(code: str, period: date) -> date:
    """Release date of a macro observation.

    ``period`` is the lake's period key: the session date (daily), any date in the month (monthly; the lake stores
    the 1st), or any date in the year (World Bank annual).
    """
    kind = macro_kind(code)
    if kind == "DAILY":
        return period
    if kind == "WORLD_BANK":
        return date(period.year, 12, 31) + timedelta(days=WORLD_BANK_LAG_DAYS)
    return month_end(period.year, period.month) + timedelta(days=MACRO_MONTHLY_LAG_DAYS[kind])


def fpi_sector_available_from(report_date: date) -> date:
    """``macro.fpi_sector_fortnightly``: ``report_date + 7d`` (02 §1.2)."""
    return report_date + timedelta(days=FPI_SECTOR_LAG_DAYS)
