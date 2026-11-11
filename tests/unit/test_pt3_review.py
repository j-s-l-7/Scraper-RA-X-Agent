# ruff: noqa: E501
"""PT3 review fixes: registered N, empty --trials intersection, coverage merge dtype."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

from ideation.bakeoff.cli import select_subset_trials
from ideation.bakeoff.pt3 import n_preregistered
from ideation.features.text_cli import merge_coverage


def _specs() -> list[SimpleNamespace]:
    cfg = [{}, {}, {"subset": "text_covered"}, {"subset": "annual_covered"}, {"subset": "annual_covered"}, {"subset": "trend_covered"}]
    return [SimpleNamespace(trial_id=f"T{i}", config=c) for i, c in enumerate(cfg)]


def test_n_preregistered_counts_registered_not_fitted() -> None:
    specs = _specs()
    assert n_preregistered(specs) == 3  # 6 specs - 3 PT3 specs, whatever was fitted


def test_empty_trials_intersection_is_an_error_not_the_whole_family() -> None:
    specs = _specs()
    kinds = {"annual_covered", "trend_covered"}
    assert select_subset_trials(specs, kinds, None) == {"T3", "T4", "T5"}
    assert select_subset_trials(specs, kinds, {"T3", "T0"}) == {"T3"}
    with pytest.raises(ValueError, match="no trial selected"):
        select_subset_trials(specs, kinds, {"T0", "T1"})
    with pytest.raises(ValueError, match="no trial selected"):
        select_subset_trials([SimpleNamespace(trial_id="A", config={})], kinds, None)


def test_merge_coverage_robust_to_date_vs_timestamp() -> None:
    cov_a = pd.DataFrame({"anchor": [date(2023, 6, 30), date(2024, 6, 28)], "n_u": [5, 6]})
    cov_t = pd.DataFrame({"anchor": pd.to_datetime(["2023-06-30", "2024-06-28", "2025-06-30"]), "n_u": [5, 6, 7], "u_trend": [1, 2, 3]})
    m = merge_coverage(cov_a, cov_t)
    assert len(m) == 2 and m["u_trend"].tolist() == [1, 2]
    m2 = merge_coverage(cov_a.assign(anchor=pd.to_datetime(cov_a["anchor"])), cov_t.assign(anchor=[date(2023, 6, 30), date(2024, 6, 28), date(2025, 6, 30)]))
    assert len(m2) == 2
