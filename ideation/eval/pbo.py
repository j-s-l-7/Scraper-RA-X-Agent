"""Probability of backtest overfitting (Bailey, Borwein, López de Prado & Zhu 2017; 02 §5.3, 05 §8).

For each split c: n* = the trial with the best in-sample performance; ω̄_c = (OOS rank of n*) / (N + 1), ranks
1 = worst … N = best, ties averaged; λ_c = ln(ω̄_c / (1 − ω̄_c)). PBO = share of splits with λ_c ≤ 0, i.e. the IS
winner lands at or below the OOS median (the median itself counts as overfit, the conservative reading).

Two entry points:
* :func:`pbo_from_is_oos` takes per-split IS and OOS performance (e.g. from CPCV splits);
* :func:`pbo_cscv` takes a T × N performance matrix and builds the CSCV splits itself (tactical: 24 × ~138 OOS ICs,
  S = 12).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from scipy.stats import rankdata

from ideation.cv.cscv import cscv_splits


@dataclass(frozen=True)
class PBOResult:
    pbo: float
    logits: np.ndarray
    best_is: np.ndarray
    """Index of the IS-optimal trial per split."""
    oos_rel_rank: np.ndarray
    """ω̄ per split."""
    n_splits: int
    n_trials: int


def pbo_from_is_oos(is_perf: np.ndarray, oos_perf: np.ndarray) -> PBOResult:
    """``is_perf`` and ``oos_perf`` are (n_splits × N); higher is better."""
    is_perf = np.asarray(is_perf, dtype=float)
    oos_perf = np.asarray(oos_perf, dtype=float)
    if is_perf.shape != oos_perf.shape or is_perf.ndim != 2:
        raise ValueError("is_perf and oos_perf must be 2-D arrays of the same shape (splits × trials)")
    n_splits, n_trials = is_perf.shape
    best = np.nanargmax(is_perf, axis=1)
    ranks = np.vstack([rankdata(row, method="average") for row in oos_perf])
    omega = ranks[np.arange(n_splits), best] / (n_trials + 1)
    logits = np.log(omega / (1 - omega))
    return PBOResult(float((logits <= 0).mean()), logits, best, omega, n_splits, n_trials)


def mean_perf(block: np.ndarray) -> np.ndarray:
    return np.nanmean(block, axis=0)


def sharpe_perf(block: np.ndarray) -> np.ndarray:
    return np.nanmean(block, axis=0) / np.nanstd(block, axis=0, ddof=1)


def pbo_cscv(perf: np.ndarray, n_blocks: int = 16, metric: Callable[[np.ndarray], np.ndarray] = mean_perf) -> PBOResult:
    """PBO from a T × N matrix of per-period performance (rows = time, columns = trials)."""
    m = np.asarray(perf, dtype=float)
    splits = cscv_splits(m.shape[0], n_blocks)
    is_perf = np.vstack([metric(m[i]) for i, _ in splits])
    oos_perf = np.vstack([metric(m[o]) for _, o in splits])
    return pbo_from_is_oos(is_perf, oos_perf)
