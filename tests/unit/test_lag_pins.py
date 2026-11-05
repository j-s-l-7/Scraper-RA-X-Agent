"""Pin the statutory constants and helpers vendored from Scraper.

Source: Scraper ``screener_scraper/completeness.py`` lines 38-39 (constants) and 101-143 (helpers) at commit
a478ebb0f7492db3a9936f619c6376b9c501c572 (2026-11-04). LODR 33: 45 days quarterly / 60 days annual;
LODR 31: 21 days shareholding. If a regulation changes, update both the constant and this pin deliberately.
"""

from __future__ import annotations

from datetime import date

import pytest

from ideation.pit import lags

SOURCE = "Scraper/screener_scraper/completeness.py@a478ebb0"


def test_constants_pinned() -> None:
    assert (lags.RESULT_LAG, lags.YEAR_RESULT_LAG, lags.SHP_LAG) == (45, 60, 21), SOURCE


@pytest.mark.parametrize(
    ("qend", "fy_month", "lag"),
    [(date(2024, 3, 31), 3, 60), (date(2023, 12, 31), 3, 45), (date(2023, 12, 31), 12, 60), (date(2024, 6, 30), 6, 60)],
)
def test_result_lag(qend: date, fy_month: int, lag: int) -> None:
    assert lags.result_lag(qend, fy_month) == lag


@pytest.mark.parametrize(
    ("period_end", "fy_month", "fy"),
    [
        (date(2024, 3, 31), 3, 2024),
        (date(2024, 6, 30), 3, 2025),
        (date(2023, 12, 31), 12, 2023),
        (date(2024, 1, 31), 12, 2024),
    ],
)
def test_fy_of(period_end: date, fy_month: int, fy: int) -> None:
    assert lags.fy_of(period_end, fy_month) == fy


def test_calendar_helpers() -> None:
    assert lags.month_end(2024, 2) == date(2024, 2, 29)
    assert lags.month_end(2023, 12) == date(2023, 12, 31)
    assert lags.quarter_end(date(2024, 5, 7)) == date(2024, 6, 30)
    assert lags.quarter_end(date(2024, 12, 1)) == date(2024, 12, 31)
