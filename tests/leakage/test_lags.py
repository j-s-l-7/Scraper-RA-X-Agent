"""02 §7 T-10..T-14: availability lags, with the exact dates from the catalogue (+ 03 §C3 label timing)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from ideation.pit.lags import (
    fact_available_from,
    fpi_sector_available_from,
    label_available_from,
    macro_available_from,
    macro_kind,
    match_event,
    results_deadline,
    shp_available_from,
)
from ideation.pit.overrides import RESULT_DEADLINE_OVERRIDES, apply_overrides

TODAY = date(2026, 11, 5)


def usable(available_from: date, anchor: date) -> bool:
    return available_from <= anchor


# ── T-10 ─────────────────────────────────────────────────────────────────────────────────────────────────────
def test_lag_annual_march_fy() -> None:
    af = fact_available_from(date(2024, 3, 31), "A", 3, None, today=TODAY)
    assert af == date(2024, 5, 31)
    assert not usable(af, date(2024, 5, 30))
    assert usable(af, date(2024, 5, 31))


def test_march_anchor_cannot_use_fy_ending_that_day() -> None:
    """Context brief §hard-requirements 1: a 31-Mar anchor cannot use the FY ending that day."""
    assert not usable(fact_available_from(date(2025, 3, 31), "A", 3, today=TODAY), date(2025, 3, 31))


# ── T-11 ─────────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("period_end", "deadline", "available"),
    [
        (date(2023, 12, 31), date(2024, 2, 29), date(2024, 3, 1)),  # leap year: +60 lands on Feb 29
        (date(2022, 12, 31), date(2023, 3, 1), date(2023, 3, 2)),
    ],
)
def test_lag_dec_fy_leap(period_end: date, deadline: date, available: date) -> None:
    assert results_deadline(period_end, "A", 12) == deadline
    assert fact_available_from(period_end, "A", 12, today=TODAY) == available


# ── T-12 ─────────────────────────────────────────────────────────────────────────────────────────────────────
def test_lag_quarter_and_q4() -> None:
    assert fact_available_from(date(2023, 12, 31), "Q", 3, today=TODAY) == date(2024, 2, 15)  # 45-day rule
    assert fact_available_from(date(2024, 3, 31), "Q", 3, today=TODAY) == date(2024, 5, 31)  # FY-closing quarter: 60
    # the same calendar quarter is not FY-closing for a December FY-ender: back to 45 days
    assert fact_available_from(date(2024, 3, 31), "Q", 12, today=TODAY) == date(2024, 5, 16)


# ── T-13 ─────────────────────────────────────────────────────────────────────────────────────────────────────
def test_lag_event_later_wins() -> None:
    pe = date(2023, 3, 31)
    event = pe + timedelta(days=70)
    assert fact_available_from(pe, "A", 3, event, today=TODAY) == pe + timedelta(days=71)


def test_event_earlier_ignored() -> None:
    pe = date(2023, 3, 31)
    early = pe + timedelta(days=30)
    assert fact_available_from(pe, "A", 3, early, today=TODAY) == pe + timedelta(days=61)


def test_future_dated_event_ignored() -> None:
    pe = date(2026, 9, 30)
    scheduled = date(2026, 11, 12)  # after `today`: a scheduled date, not a reported result
    assert fact_available_from(pe, "Q", 3, scheduled, today=TODAY) == pe + timedelta(days=46)
    assert match_event(pe, [scheduled, date(2027, 1, 20)], today=TODAY) is None


def test_match_event_window_and_order() -> None:
    pe = date(2023, 3, 31)
    events = [date(2023, 3, 31), date(2023, 8, 10), date(2023, 5, 20), date(2023, 7, 30), date(2023, 9, 1)]
    assert match_event(pe, events, today=TODAY) == date(2023, 5, 20)  # first in (pe, pe+120]
    assert match_event(pe, [date(2023, 3, 31)], today=TODAY) is None  # on period_end: excluded
    assert match_event(pe, [pe + timedelta(days=121)], today=TODAY) is None


def test_event_outside_window_is_a_caller_bug() -> None:
    with pytest.raises(ValueError, match="match window"):
        fact_available_from(date(2023, 3, 31), "A", 3, date(2023, 3, 1), today=TODAY)


@pytest.mark.parametrize("period_type", ["TTM", "H", "X"])
def test_undated_period_types_raise(period_type: str) -> None:
    with pytest.raises(ValueError, match="period_type"):
        fact_available_from(date(2024, 3, 31), period_type, 3, today=TODAY)


# ── T-14 ─────────────────────────────────────────────────────────────────────────────────────────────────────
def test_lag_shp_weekend() -> None:
    af = shp_available_from(date(2024, 3, 31))
    assert date(2024, 3, 31) + timedelta(days=21) == date(2024, 4, 21)  # deadline is a Sunday
    assert af == date(2024, 4, 22)
    assert af.weekday() == 0  # Monday: the first session on/after the deadline + 1
    assert not usable(af, date(2024, 4, 19))  # Friday anchor before the weekend
    assert usable(af, date(2024, 4, 22))


@pytest.mark.parametrize(
    ("code", "period", "expected"),
    [
        ("MOSPI_CPI_2012_GENERAL_INFL", date(2024, 1, 1), date(2024, 2, 15)),  # lake stores the 1st of the month
        ("MOSPI_CPI_2012_GENERAL_INFL", date(2024, 1, 31), date(2024, 2, 15)),
        ("MOSPI_IIP_2011_GENERAL_GROWTH", date(2024, 1, 1), date(2024, 3, 16)),  # leap February
        ("MOSPI_IIP_2011_GENERAL_GROWTH", date(2023, 1, 1), date(2023, 3, 17)),
        ("MOSPI_WPI_ALL_INFL", date(2024, 1, 1), date(2024, 2, 16)),
        ("FP.CPI.TOTL.ZG", date(2023, 1, 1), date(2024, 9, 26)),  # World Bank: Dec-31 + 270d
        ("^NSEI", date(2024, 1, 2), date(2024, 1, 2)),
        ("INR=X", date(2024, 1, 2), date(2024, 1, 2)),
        ("NIFTY_FIN_SERVICE.NS", date(2024, 1, 2), date(2024, 1, 2)),
    ],
)
def test_macro_lags(code: str, period: date, expected: date) -> None:
    assert macro_available_from(code, period) == expected


def test_macro_unknown_code_raises() -> None:
    with pytest.raises(KeyError):
        macro_kind("SOMETHING_NEW")
    assert macro_kind("FP.CPI.TOTL") == "WORLD_BANK"  # contains 'CPI' but is World Bank, not MoSPI


def test_fpi_sector_lag() -> None:
    assert fpi_sector_available_from(date(2024, 1, 15)) == date(2024, 1, 22)


def test_covid_override() -> None:
    # FY2020 annual: blanket alone would give 2020-06-30; the SEBI table (2020-07-31) wins.
    assert fact_available_from(date(2020, 3, 31), "A", 3, today=TODAY) >= date(2020, 8, 1)
    assert fact_available_from(date(2020, 3, 31), "A", 3, today=TODAY) == date(2020, 8, 1)
    # Q3 FY20 (verified: normal +45) keeps the blanket margin: 45 + 30 (P1 never moves a date earlier).
    assert fact_available_from(date(2019, 12, 31), "Q", 3, today=TODAY) == date(2019, 12, 31) + timedelta(days=76)
    # Q1 FY21: table 2020-09-15 beats blanket (2020-08-14 + 30 = 2020-09-13).
    assert fact_available_from(date(2020, 6, 30), "Q", 3, today=TODAY) == date(2020, 9, 16)
    # Dec FY-ender annual inside the window: blanket only.
    assert fact_available_from(date(2020, 12, 31), "A", 12, today=TODAY) == date(2021, 3, 31) + timedelta(days=1)
    # window edges are inclusive; outside the window nothing changes
    assert fact_available_from(date(2021, 6, 30), "Q", 3, today=TODAY) == date(2021, 6, 30) + timedelta(days=76)
    assert fact_available_from(date(2021, 9, 30), "Q", 3, today=TODAY) == date(2021, 9, 30) + timedelta(days=46)
    assert fact_available_from(date(2019, 9, 30), "Q", 3, today=TODAY) == date(2019, 9, 30) + timedelta(days=46)
    # shareholding: its own table (Reg 31 Mar-2020 -> 2020-05-15) is inside the blanket, so blanket wins
    assert shp_available_from(date(2020, 3, 31)) == date(2020, 3, 31) + timedelta(days=21 + 30 + 1)


def test_covid_table_verified_with_circulars() -> None:
    """P1 item 10: every table entry inside the window cites a SEBI circular or says none was found."""
    from ideation.pit.overrides import SHAREHOLDING_DEADLINE_OVERRIDES

    for table in (RESULT_DEADLINE_OVERRIDES, SHAREHOLDING_DEADLINE_OVERRIDES):
        for pe, entry in table.items():
            assert "SEBI/HO/" in entry.note, pe
            assert entry.verified or entry.deadline is None, pe  # unverified entries never shorten anything
    assert RESULT_DEADLINE_OVERRIDES[date(2020, 3, 31)].deadline == date(2020, 7, 31)
    assert RESULT_DEADLINE_OVERRIDES[date(2020, 6, 30)].deadline == date(2020, 9, 15)
    assert RESULT_DEADLINE_OVERRIDES[date(2021, 3, 31)].deadline == date(2021, 6, 30)
    assert SHAREHOLDING_DEADLINE_OVERRIDES[date(2020, 3, 31)].deadline == date(2020, 5, 15)


def test_event_window_covers_fy2020_extension() -> None:
    """P1 item 10: the FY2020 deadline (2020-07-31, day 122) lies beyond the 120-day window; the window now reaches
    deadline + 15d, so a results event on 2020-07-31 matches and availability moves later (conservative)."""
    from ideation.pit.lags import event_window_end

    pe = date(2020, 3, 31)
    assert event_window_end(pe, "A", 3) == date(2020, 8, 15)
    assert match_event(pe, [date(2020, 7, 31)], today=TODAY) == date(2020, 7, 31)
    assert fact_available_from(pe, "A", 3, date(2020, 8, 10), today=TODAY) == date(2020, 8, 11)
    assert match_event(pe, [date(2020, 8, 16)], today=TODAY) is None
    # outside COVID tables the window stays 120 days
    assert event_window_end(date(2023, 3, 31), "A", 3) == date(2023, 3, 31) + timedelta(days=120)
    assert event_window_end(date(2023, 6, 30), "Q", 3) == date(2023, 6, 30) + timedelta(days=120)
    # widening never makes anything earlier: for every period, window_end >= pe + 120
    for pe2 in [date(2019, 12, 31), date(2020, 6, 30), date(2020, 9, 30), date(2021, 3, 31), date(2021, 6, 30)]:
        for pt in ("A", "Q"):
            assert event_window_end(pe2, pt, 3) >= pe2 + timedelta(days=120)


def test_overrides_never_move_earlier() -> None:
    for pe in [*RESULT_DEADLINE_OVERRIDES, date(2020, 9, 30), date(2018, 3, 31), date(2023, 6, 30)]:
        for lag in (21, 45, 60):
            base = pe + timedelta(days=lag)
            for kind in ("results", "shareholding"):
                assert apply_overrides(pe, base, kind=kind) >= base


def test_covid_event_still_later_wins() -> None:
    pe = date(2020, 6, 30)  # override deadline 2020-09-15; match window ends 2020-10-28
    assert fact_available_from(pe, "Q", 3, date(2020, 10, 1), today=TODAY) == date(2020, 10, 2)
    assert fact_available_from(pe, "Q", 3, date(2020, 8, 20), today=TODAY) == date(2020, 9, 16)  # earlier: ignored


# ── 03 §C3: labels are usable at label_end (not +1); facts keep deadline + 1 ───────────────────────────────
def test_label_available_from_is_label_end() -> None:
    assert label_available_from(date(2025, 6, 30)) == date(2025, 6, 30)
