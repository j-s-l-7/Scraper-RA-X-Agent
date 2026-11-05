"""T-07 (amended by 03 §C3), T-08 (C3 analog), T-09 (05 O4) and the fold driver's row-level purge check."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from ideation.cv.cpcv import cpcv_paths, cpcv_splits, n_paths
from ideation.cv.walkforward import (
    anchor_label_end,
    annual_folds,
    assert_purged,
    check_available,
    monthly_folds,
    walk_forward_predict,
)
from ideation.pit.contract import LeakageError

SESSIONS = pd.bdate_range("2010-01-01", "2027-12-31")


def last_session_on_or_before(d: pd.Timestamp) -> pd.Timestamp:
    return SESSIONS[d >= SESSIONS][-1]


def month_end_session(year: int, month: int) -> pd.Timestamp:
    return last_session_on_or_before(pd.Timestamp(year, month, 1) + pd.offsets.MonthEnd(0))


def label_end(anchor: pd.Timestamp, months: int) -> pd.Timestamp:
    """03 §C3: exit = last good session on or before A + h (calendar months)."""
    return last_session_on_or_before(anchor + pd.DateOffset(months=months))


JUNE = [month_end_session(y, 6) for y in range(2016, 2026)]
JUNE_END = {a: label_end(a, 12) for a in JUNE}


def test_t07_purge_annual() -> None:
    """Test 2022 ⇒ train {2019, 2020, 2021}; 2021's label ends at 2022's close and is allowed."""
    t = month_end_session(2022, 6)
    (fold,) = annual_folds(JUNE, JUNE_END, [t])
    assert [a.year for a in fold.train_anchors] == [2019, 2020, 2021]
    assert all(JUNE_END[a] <= t for a in fold.train_anchors)
    assert fold.test_anchors == (t,)


def test_t07_late_label_is_purged() -> None:
    """Any anchor with a label_end > T is absent (a late exit at 2021 pushes its anchor-level label_end past T)."""
    t = month_end_session(2022, 6)
    ends = dict(JUNE_END)
    ends[month_end_session(2021, 6)] = t + pd.Timedelta(days=1)
    (fold,) = annual_folds(JUNE, ends, [t])
    assert [a.year for a in fold.train_anchors] == [2018, 2019, 2020]
    assert [a.year for a in fold.purged] == [2021]


def test_annual_counted_years_and_expanding() -> None:
    folds = annual_folds(JUNE, JUNE_END, [a for a in JUNE if 2019 <= a.year <= 2024])
    assert next([a.year for a in f.train_anchors] for f in folds) == [2016, 2017, 2018]
    assert all(len(f.train_anchors) == 3 for f in folds)
    exp = annual_folds(JUNE, JUNE_END, [month_end_session(2024, 6)], n_train=None, train_start="2020-06-01")
    assert [a.year for a in exp[0].train_anchors] == [2020, 2021, 2022, 2023]


MONTHLY = [month_end_session(y, m) for y in range(2011, 2026) for m in range(1, 13)]


def test_t08_purge_monthly_overlap() -> None:
    """1Y labels on monthly anchors, test block starts 2022-01-31: train anchors with label_end > 2022-01-31 absent.
    Under C3 the 2021-01-29 label ends 2022-01-28 (last session ≤ 2022-01-29) and is kept; 2021-02-26 is purged."""
    ends = {a: label_end(a, 12) for a in MONTHLY}
    folds = monthly_folds(MONTHLY, ends, "2022-01-01", "2022-12-31")
    (fold,) = folds
    t0 = pd.Timestamp("2022-01-31")
    assert fold.test_anchors[0] == t0
    assert len(fold.test_anchors) == 12
    assert all(ends[a] <= t0 for a in fold.train_anchors)
    assert fold.train_anchors[-1] == pd.Timestamp("2021-01-29")
    assert ends[pd.Timestamp("2021-01-29")] == pd.Timestamp("2022-01-28")
    assert pd.Timestamp("2021-02-26") not in fold.train_anchors
    assert len(fold.train_anchors) == 36
    assert fold.train_anchors[0] == pd.Timestamp("2018-02-28")
    assert len(fold.purged) == 11  # 2021-02 … 2021-12


def test_tactical_monthly_purge_and_embargo() -> None:
    """3M labels: purge drops the 2 anchors whose label crosses the test start; embargo 3 leaves a 3-anchor gap."""
    ends = {a: label_end(a, 3) for a in MONTHLY}
    (fold,) = monthly_folds(MONTHLY, ends, "2020-01-01", "2020-12-31", embargo=0)
    assert fold.train_anchors[-1] == pd.Timestamp("2019-10-31")  # label ends 2020-01-31 ≤ T
    (emb,) = monthly_folds(MONTHLY, ends, "2020-01-01", "2020-12-31", embargo=3)
    assert emb.train_anchors[-1] == pd.Timestamp("2019-07-31")
    assert len(emb.train_anchors) == 36
    folds = monthly_folds(MONTHLY, ends, "2014-01-01", "2025-06-30", embargo=3)
    assert len(folds) == 12
    assert len(folds[-1].test_anchors) == 6
    assert all(ends[a] <= f.test_anchors[0] for f in folds for a in f.train_anchors)


ANNUAL_CPCV = [month_end_session(y, 6) for y in range(2016, 2025)]


def test_t09_cpcv_annual_o4() -> None:
    """05 O4: anchors 2016–2024, 9 groups, k = 2 ⇒ 36 splits, 8 paths; embargo 1 anchor after each test group."""
    ends = {a: label_end(a, 12) for a in ANNUAL_CPCV}
    splits = cpcv_splits(ANNUAL_CPCV, ends, n_groups=9, k=2, embargo=1)
    assert len(splits) == 36 == math.comb(9, 2)
    assert n_paths(9, 2) == 8
    idx = pd.DatetimeIndex(ANNUAL_CPCV)
    for s in splits:
        test = set(s.test_anchors)
        assert not test & set(s.train_anchors)
        for t in s.test_anchors:  # embargo: the next anchor after a test group is never trained on
            nxt = idx[idx > t]
            if len(nxt) and nxt[0] not in test:
                assert nxt[0] not in s.train_anchors
        for a in s.train_anchors:  # purge: no training label span (A, L_A] overlaps a test span (T, L_T]
            for t in s.test_anchors:
                assert not (a < ends[t] and ends[a] > t)
    paths = cpcv_paths(splits, 9)
    assert len(paths) == 8
    for path in paths:
        assert [g for g, _ in path] == list(range(9))
        assert all(g in splits[si].test_groups for g, si in path)
    assert sorted(si for path in paths for _, si in path) == sorted(list(range(36)) * 2)  # each split feeds 2 paths
    for g in range(9):  # each group is tested C(8, 1) = 8 times
        assert sum(g in s.test_groups for s in splits) == 8
    for path in paths:  # each path is a full backtest: every anchor exactly once
        anchors = [ANNUAL_CPCV[g] for g, _ in path]
        assert sorted(anchors) == ANNUAL_CPCV


def test_cpcv_tactical_counts() -> None:
    ends = {a: label_end(a, 3) for a in MONTHLY if a <= pd.Timestamp("2025-06-30")}
    anchors = sorted(ends)
    splits = cpcv_splits(anchors, ends, n_groups=12, k=2, embargo=3)
    assert len(splits) == 66
    assert len(cpcv_paths(splits, 12)) == 11 == n_paths(12, 2)
    idx = pd.DatetimeIndex(anchors)
    for s in splits[:10]:
        test = set(s.test_anchors)
        for t in s.test_anchors:
            after = [a for a in idx[idx > t] if a not in test][:2]
            assert not set(after) & set(s.train_anchors)  # within the purge zone


def test_anchor_label_end_is_max() -> None:
    a = pd.to_datetime(["2020-06-30", "2020-06-30", "2021-06-30"])
    le = pd.to_datetime(["2021-06-30", "2021-03-01", "2022-06-30"])
    m = anchor_label_end(pd.Series(a), pd.Series(le))
    assert m.tolist() == [pd.Timestamp("2021-06-30"), pd.Timestamp("2022-06-30")]


class _Mean:
    def fit(self, X: pd.DataFrame, y: np.ndarray, meta: pd.DataFrame) -> _Mean:
        self.coef = np.linalg.lstsq(X.to_numpy(), y, rcond=None)[0]
        return self

    def predict(self, X: pd.DataFrame, meta: pd.DataFrame) -> np.ndarray:
        return X.to_numpy() @ self.coef


def test_driver_rejects_unpurged_rows_and_drops_missing_labels() -> None:
    anchors = [pd.Timestamp(f"{y}-06-30") for y in range(2016, 2023)]
    meta = pd.DataFrame({"anchor": np.repeat(anchors, 5), "security_id": np.tile(range(5), 7)})
    meta["label_end"] = meta["anchor"] + pd.DateOffset(years=1)
    X = pd.DataFrame({"x": np.arange(len(meta), dtype=float)})
    y = pd.Series(np.arange(len(meta), dtype=float))
    y.iloc[0] = np.nan
    folds = annual_folds(anchors, dict(zip(meta["anchor"], meta["label_end"], strict=False)), anchors[3:])
    out = walk_forward_predict(X, y, meta, folds, _Mean)
    assert len(out) == 4 * 5
    assert set(out["fold"]) == {a.date().isoformat() for a in anchors[3:]}
    bad = meta.copy()
    bad.loc[bad["anchor"] == anchors[2], "label_end"] = anchors[3] + pd.Timedelta(days=5)
    with pytest.raises(LeakageError, match="label_end"):
        walk_forward_predict(X, y, bad, folds, _Mean)
    with pytest.raises(LeakageError):
        assert_purged(bad[bad["anchor"] == anchors[2]], anchors[3])


def test_t06_availability_guard() -> None:
    """02 T-06: a feature whose available_from is after the anchor raises LeakageError (or is dropped)."""
    anchors = pd.Series(pd.to_datetime(["2020-06-30", "2021-06-30"]))
    avail = pd.DataFrame(
        {"ok": pd.to_datetime(["2020-05-31", "2021-05-31"]), "x_leak": pd.to_datetime(["2021-06-30", "2022-06-30"])}
    )
    with pytest.raises(LeakageError, match="x_leak"):
        check_available(avail, anchors)
    assert check_available(avail, anchors, mode="drop") == ["x_leak"]
