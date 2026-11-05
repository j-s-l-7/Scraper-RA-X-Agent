"""Forward walk-forward splits (02 §4.2–4.4, 03 §C3/§C8, 05 §8) and the fold driver.

Purge (03 §C3): a training anchor A is usable for a test block starting at T iff ``label_end(A) ≤ T``, where
``label_end(A)`` is the latest label end of any name at A (conservative). The driver re-checks every row.

* Annual 1Y (June / March): test blocks of one anchor; train = the 3 most recent usable anchors ⇒ {T−3, T−2, T−1}.
* Monthly (1Y monthly variant, tactical 1M/3M): test blocks of 12 anchors; train = the 36 most recent usable
  anchors. ``embargo`` (tactical: 1 for 1M, 3 for 3M) additionally drops that many of the most recent usable anchors,
  i.e. leaves a gap of ``embargo`` anchors between the last training label and the test block (05 §8, 03 §C8).
* ``n_train=None`` is the expanding window; ``train_start`` drops anchors before it (PostCOVID, tier T1).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, Self

import numpy as np
import pandas as pd

from ideation.pit.contract import LeakageError


@dataclass(frozen=True)
class Fold:
    name: str
    train_anchors: tuple[pd.Timestamp, ...]
    test_anchors: tuple[pd.Timestamp, ...]
    purged: tuple[pd.Timestamp, ...]
    """Anchors before the test block that were dropped by the purge or the embargo."""


def anchor_label_end(anchors: pd.Series, label_end: pd.Series) -> pd.Series:
    """Per-anchor label end = the max over the anchor's rows (sorted by anchor)."""
    return pd.Series(pd.to_datetime(label_end).to_numpy(), index=pd.to_datetime(anchors)).groupby(level=0).max()


def anchor_end_map(anchors: Sequence[object], label_end: Mapping[object, object] | pd.Series) -> pd.Series:
    idx = pd.DatetimeIndex(pd.to_datetime(list(anchors))).sort_values().unique()
    ends = pd.Series(label_end)
    ends.index = pd.to_datetime(ends.index)
    ends = pd.to_datetime(ends).groupby(level=0).max()
    missing = idx.difference(ends.index)
    if len(missing):
        raise ValueError(f"label_end missing for anchors {list(missing.date)}")
    return ends.reindex(idx)


def walk_forward_folds(
    anchors: Sequence[object],
    label_end: Mapping[object, object] | pd.Series,
    test_blocks: Sequence[Sequence[object]],
    *,
    n_train: int | None,
    embargo: int = 0,
    train_start: object | None = None,
) -> list[Fold]:
    ends = anchor_end_map(anchors, label_end)
    start = pd.Timestamp(train_start) if train_start is not None else None
    folds = []
    for block in test_blocks:
        test = tuple(sorted(pd.Timestamp(a) for a in block))
        t0 = test[0]
        before = ends[ends.index < t0]
        if start is not None:
            before = before[before.index >= start]
        usable = before[before <= t0].index
        if embargo:
            usable = usable[:-embargo] if len(usable) > embargo else usable[:0]
        train = usable if n_train is None else usable[-n_train:]
        window_start = train[0] if len(train) else t0
        purged = tuple(a for a in before.index if a >= window_start and a not in train)
        name = t0.date().isoformat() if len(test) == 1 else f"{t0.date()}..{test[-1].date()}"
        folds.append(Fold(name, tuple(train), test, purged))
    return folds


def annual_folds(
    anchors: Sequence[object],
    label_end: Mapping[object, object] | pd.Series,
    test_anchors: Sequence[object],
    *,
    n_train: int | None = 3,
    train_start: object | None = None,
) -> list[Fold]:
    """1Y annual walk-forward: one fold per test anchor, train {T−3, T−2, T−1} after the C3 purge."""
    return walk_forward_folds(anchors, label_end, [[a] for a in test_anchors], n_train=n_train, train_start=train_start)


def monthly_folds(
    anchors: Sequence[object],
    label_end: Mapping[object, object] | pd.Series,
    test_start: object,
    test_end: object,
    *,
    block: int = 12,
    n_train: int | None = 36,
    embargo: int = 0,
    train_start: object | None = None,
) -> list[Fold]:
    """Purged rolling walk-forward on monthly anchors: blocks of ``block`` test anchors in [test_start, test_end]."""
    idx = pd.DatetimeIndex(pd.to_datetime(list(anchors))).sort_values().unique()
    tests = idx[(idx >= pd.Timestamp(test_start)) & (idx <= pd.Timestamp(test_end))]
    blocks = [tests[i : i + block] for i in range(0, len(tests), block)]
    return walk_forward_folds(anchors, label_end, blocks, n_train=n_train, embargo=embargo, train_start=train_start)


# ── PIT availability guard (02 T-06) ─────────────────────────────────────────────────────────────────────────
def unavailable_columns(available_from: pd.DataFrame, anchors: pd.Series) -> list[str]:
    """Columns with any value whose ``available_from`` is after its row's anchor."""
    a = pd.to_datetime(pd.Series(anchors, index=available_from.index))
    late = available_from.apply(lambda c: pd.to_datetime(c) > a)
    return [c for c in available_from.columns if late[c].any()]


def check_available(
    available_from: pd.DataFrame, anchors: pd.Series, mode: Literal["raise", "drop"] = "raise"
) -> list[str]:
    """``raise``: :class:`LeakageError` if any feature value is not yet available at its anchor.
    ``drop``: return the offending columns so the caller excludes them."""
    bad = unavailable_columns(available_from, anchors)
    if bad and mode == "raise":
        raise LeakageError(f"features not available at their anchor: {bad}")
    return bad


# ── fold driver ──────────────────────────────────────────────────────────────────────────────────────────────
class Scorer(Protocol):
    def fit(self, X: pd.DataFrame, y: np.ndarray, meta: pd.DataFrame) -> Self: ...

    def predict(self, X: pd.DataFrame, meta: pd.DataFrame) -> np.ndarray: ...


def assert_purged(meta_train: pd.DataFrame, test_start: pd.Timestamp, label_end_col: str = "label_end") -> None:
    """Row-level C3 check: no training label may end after the test block starts."""
    if label_end_col in meta_train:
        late = pd.to_datetime(meta_train[label_end_col]) > test_start
        if late.any():
            raise LeakageError(f"{int(late.sum())} training rows have label_end > {test_start.date()}")


def walk_forward_predict(
    X: pd.DataFrame,
    y: pd.Series,
    meta: pd.DataFrame,
    folds: Sequence[Fold],
    make_model: Callable[[], Scorer],
    *,
    anchor_col: str = "anchor",
) -> pd.DataFrame:
    """Fit a fresh model per fold on the train anchors (rows with a label only), score the test anchors.

    Returns ``meta`` of the test rows plus ``score`` and ``fold``. Every global fit (scaling, selection, σ for
    90/10, the learner) happens inside ``model.fit`` and therefore sees training rows only (02 §4.6, T-18).
    """
    anchors = pd.to_datetime(meta[anchor_col])
    out = []
    for fold in folds:
        tr = anchors.isin(fold.train_anchors).to_numpy() & y.notna().to_numpy()
        te = anchors.isin(fold.test_anchors).to_numpy()
        if not tr.any() or not te.any():
            continue
        assert_purged(meta.loc[tr], fold.test_anchors[0])
        model = make_model().fit(X.loc[tr], y.loc[tr].to_numpy(dtype=float), meta.loc[tr])
        scored = meta.loc[te].copy()
        scored["score"] = np.asarray(model.predict(X.loc[te], meta.loc[te]), dtype=float)
        scored["fold"] = fold.name
        out.append(scored)
    return pd.concat(out) if out else meta.iloc[:0].assign(score=np.nan, fold="")
