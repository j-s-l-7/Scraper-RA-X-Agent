"""Evaluation design of the accuracy lab (E0): monthly anchors with 1Y labels, purge + embargo, sample-uniqueness
weights, non-overlapping eras.

* **Monthly design.** Test blocks of 12 monthly anchors; the model of a block is fitted once, on the 36 most recent
  training anchors whose label ends on or before the block's first anchor ``T0`` (purge, 03 C3) after dropping the
  ``EMBARGO`` most recent of them. Rolling-36 is the pre-registered window (same calendar span as the 3-annual-anchor
  design); at least ``MIN_TRAIN`` anchors must remain.
* **June design (legacy).** One test anchor per year, train {T-3, T-2, T-1} June anchors (``annual_folds``).
* **Uniqueness weights (AFML 4.4).** Label i spans months [anchor_i, label_end_i]; c_t = number of training labels
  alive in month t; w_i = mean over its span of 1/c_t. Weights are rescaled so that they sum to
  ``n_rows / OVERSAMPLE`` (the monthly design is a 12x oversampled version of the annual one, so ridge alpha keeps its
  meaning); row-count hyperparameters of trees are scaled by the same factor (``scale_params``).
* **Eras.** Non-overlapping 12-month-spaced test anchors; June is primary, the other 11 phase offsets are a band.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd

from ideation.cv.walkforward import Fold, annual_folds, monthly_folds
from ideation.pit.contract import LeakageError

OVERSAMPLE = 12
EMBARGO = 1
N_TRAIN = 36
MIN_TRAIN = 24
BLOCK = 12
COUNTED_START = pd.Timestamp("2020-06-01")
PRIMARY_ERA_MONTH = 6


def monthly_design_folds(
    anchors: Sequence[pd.Timestamp], label_end: pd.Series, test_end: pd.Timestamp, *, n_train: int | None = N_TRAIN
) -> list[Fold]:
    folds = monthly_folds(anchors, label_end, COUNTED_START, test_end, block=BLOCK, n_train=n_train, embargo=EMBARGO)
    for f in folds:
        if len(f.train_anchors) < MIN_TRAIN:
            raise ValueError(f"fold {f.name}: only {len(f.train_anchors)} training anchors (< {MIN_TRAIN})")
    return folds


def june_design_folds(anchors: Sequence[pd.Timestamp], label_end: pd.Series) -> list[Fold]:
    june = [a for a in anchors if a.month == 6]
    tests = [a for a in june if a >= COUNTED_START]
    return annual_folds(june, label_end.loc[june], tests, n_train=3)


def assert_no_overlap_leak(folds: Sequence[Fold], label_end: pd.Series) -> None:
    """Every training anchor's label ended on or before the first test anchor of its block (and before it, by the
    embargo, in the monthly design)."""
    for f in folds:
        t0 = f.test_anchors[0]
        late = [a for a in f.train_anchors if label_end.loc[a] > t0]
        if late:
            raise LeakageError(f"fold {f.name}: training anchors {late[:3]} have label_end > {t0.date()}")
        if f.train_anchors and f.train_anchors[-1] >= t0:
            raise LeakageError(f"fold {f.name}: a training anchor is not before the test block")


def uniqueness_weights(train_anchors: Sequence[pd.Timestamp], label_end: pd.Series) -> pd.Series:
    """Average-uniqueness weight per training anchor (month grid), see the module docstring."""
    anchors = pd.DatetimeIndex(sorted(train_anchors))
    if len(anchors) == 0:
        return pd.Series(dtype=float)
    ends = pd.DatetimeIndex(pd.to_datetime(label_end.loc[anchors].to_numpy()))
    lo = anchors.min().to_period("M")
    hi = ends.max().to_period("M")
    grid = pd.period_range(lo, hi, freq="M")
    pos = {p: i for i, p in enumerate(grid)}
    cover = np.zeros(len(grid))
    spans = []
    for a, e in zip(anchors, ends, strict=True):
        i, j = pos[a.to_period("M")], pos[e.to_period("M")]
        cover[i : j + 1] += 1
        spans.append((i, j))
    inv = 1.0 / np.maximum(cover, 1.0)
    return pd.Series([inv[i : j + 1].mean() for i, j in spans], index=anchors)


def row_weights(meta_anchor: pd.Series, train_anchors: Sequence[pd.Timestamp], label_end: pd.Series) -> np.ndarray:
    """Per-row sample weights for rows whose anchors are in ``train_anchors``; sum = n_rows / OVERSAMPLE."""
    w_anchor = uniqueness_weights(train_anchors, label_end)
    w = pd.to_datetime(meta_anchor).map(w_anchor).to_numpy(dtype=float)
    return w * (len(w) / OVERSAMPLE) / w.sum()


def scale_params(key: str, params: Mapping[str, Any], factor: int) -> dict[str, Any]:
    """Row-count hyperparameters scale with the oversampling factor (documented, pre-registered rule)."""
    p = dict(params)
    if factor != 1:
        if key == "rf":
            p["min_samples_leaf"] = int(p["min_samples_leaf"]) * factor
        elif key == "lgbm":
            p["min_child_samples"] = int(p["min_child_samples"]) * factor
    return p


def era_anchors(anchors: Sequence[pd.Timestamp], month: int = PRIMARY_ERA_MONTH) -> list[pd.Timestamp]:
    """Anchors in calendar month ``month``: one per year, i.e. 12-month-spaced and non-overlapping."""
    return [a for a in anchors if a.month == month]
