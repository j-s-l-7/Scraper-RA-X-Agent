"""T-23 (02 §7) with the 03 §C3 amendment, plus calendar snapping and anchor parsing.

Anchors snap back to the last good session, and entries roll forward to the first good session after A. Exits
snap back to the last good session on or before A + h (03 §C3: "exit = last good session on or before A + h"). So
02's "exit on 2019-09-25 rolls to 2019-10-01" becomes 2019-09-19, and a label never ends after A + h.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from ideation.lake.symbols import build_calendar
from ideation.pit.calendar import CalendarError, TradingCalendar, add_months, parse_anchors

MISSING = {date(2019, 9, d) for d in (20, 23, 24, 25, 26, 27, 30)} | {date(2021, 11, 4), date(2025, 2, 1)}


def _calendar(start: date = date(2019, 1, 1), end: date = date(2026, 1, 31)) -> TradingCalendar:
    days = [d.date() for d in pd.bdate_range(start, end)] + [date(2025, 2, 1), date(2021, 11, 4)]
    days = sorted(d for d in set(days) if start <= d <= end)
    bhav = pd.DataFrame({"date": [d for d in days if d not in MISSING], "n_rows": 1500, "n_eq": 1500})
    cal = build_calendar(bhav, pd.Series(days))
    assert set(cal.loc[cal["bhav_missing"], "session"]) == {d for d in MISSING if start <= d <= end}
    return TradingCalendar.from_frame(cal)


CAL = _calendar()


def test_t23_anchor_snaps_back() -> None:
    assert CAL.snap_anchor(date(2019, 9, 30)) == date(2019, 9, 19)
    assert CAL.snap_anchor(date(2025, 2, 1)) == date(2025, 1, 31)
    assert CAL.snap_anchor(date(2021, 11, 4)) == date(2021, 11, 3)
    assert not CAL.is_good(date(2019, 9, 25))


def test_t23_entry_rolls_forward() -> None:
    assert CAL.entry_session(date(2019, 9, 19)) == date(2019, 10, 1)
    assert CAL.entry_session(date(2019, 9, 18)) == date(2019, 9, 19)


def test_t23_exit_snaps_back_03_c3() -> None:
    # A + 1M = 2019-09-25 (bhav missing): 03 §C3 → last good session on/before = 2019-09-19 (02 said 2019-10-01)
    assert CAL.exit_session(date(2019, 8, 25), 1) == date(2019, 9, 19)
    a = date(2018, 9, 28)
    assert CAL.exit_session(a, 12) == date(2019, 9, 19)  # 2019-09-28 is a Saturday after the gap
    for anchor in (date(2019, 6, 28), date(2020, 6, 30), date(2024, 6, 28)):
        for months in (1, 3, 12):
            end = CAL.exit_session(anchor, months)
            assert end is not None
            assert end <= add_months(anchor, months)
            assert CAL.is_good(end)


def test_exit_beyond_calendar_is_none() -> None:
    assert CAL.exit_session(date(2025, 6, 30), 12) is None


def test_add_months_clips_month_end() -> None:
    assert add_months(date(2016, 1, 31), 1) == date(2016, 2, 29)
    assert add_months(date(2015, 3, 31), 3) == date(2015, 6, 30)
    assert add_months(date(2016, 6, 30), 12) == date(2017, 6, 30)


def test_lookback_and_counts() -> None:
    s = CAL.lookback(date(2019, 10, 1), 3)
    assert [d.astype(object) for d in s] == [date(2019, 9, 18), date(2019, 9, 19), date(2019, 10, 1)]
    assert CAL.n_good_between(date(2019, 9, 19), date(2019, 10, 1)) == 2
    with pytest.raises(CalendarError):
        CAL.lookback(date(2019, 1, 3), 10)


def test_parse_anchors() -> None:
    cal = _calendar(date(2010, 1, 4), date(2012, 12, 31))
    june = parse_anchors(cal, "june")
    assert june == [date(2010, 6, 30), date(2011, 6, 30), date(2012, 6, 29)]
    monthly = parse_anchors(cal, "monthly")
    assert monthly[0] == date(2010, 4, 30)
    assert monthly[-1] == date(2012, 12, 31)
    assert len(monthly) == 33
    assert set(june) <= set(monthly)
    assert parse_anchors(cal, "2011-06-26") == [date(2011, 6, 24)]  # Sunday → Friday


def test_calendar_rejects_duplicates() -> None:
    with pytest.raises(CalendarError):
        TradingCalendar([date(2020, 1, 1), date(2020, 1, 1)], [False, False])


def test_calendar_never_reads_the_clock() -> None:
    import inspect

    import ideation.pit.calendar as m

    src = inspect.getsource(m)
    assert "today()" not in src
    assert "now()" not in src
