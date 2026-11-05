"""Bootstraps (02 §5.2/§5.4, 05 §4): iid over anchors, Politis–Romano stationary block, paired deltas, over names.

Every function takes a ``seed`` and draws from ``numpy.random.default_rng(seed)``, so results are reproducible.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

Method = Literal["iid", "stationary"]


def stationary_indices(n: int, mean_block: float, n_boot: int, rng: np.random.Generator) -> np.ndarray:
    """``(n_boot, n)`` resample indices of the stationary bootstrap (Politis & Romano 1994), wrapping circularly.

    Each position starts a new block with probability ``1/mean_block``; otherwise it continues the previous block.
    ``mean_block = 1`` is the iid bootstrap.
    """
    if n < 1:
        raise ValueError("empty series")
    p_new = 1.0 / max(mean_block, 1.0)
    starts = rng.integers(0, n, size=(n_boot, n))
    new = rng.random((n_boot, n)) < p_new
    idx = np.empty((n_boot, n), dtype=np.int64)
    idx[:, 0] = starts[:, 0]
    for t in range(1, n):
        idx[:, t] = np.where(new[:, t], starts[:, t], (idx[:, t - 1] + 1) % n)
    return idx


def resample_indices(n: int, method: Method, mean_block: float, n_boot: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    if method == "iid":
        return rng.integers(0, n, size=(n_boot, n))
    return stationary_indices(n, mean_block, n_boot, rng)


def percentile_ci(draws: np.ndarray, level: float) -> tuple[float, float]:
    a = (1 - level) / 2
    lo, hi = np.nanquantile(draws, [a, 1 - a])
    return float(lo), float(hi)


@dataclass(frozen=True)
class BootCI:
    estimate: float
    ci90: tuple[float, float]
    ci95: tuple[float, float]
    n: int
    method: str
    mean_block: float

    def as_dict(self) -> dict[str, object]:
        return self.__dict__.copy()


def mean_ci(
    x: np.ndarray | pd.Series,
    *,
    method: Method = "iid",
    mean_block: float = 1.0,
    n_boot: int = 10_000,
    seed: int = 0,
) -> BootCI:
    """Bootstrap CI (90% and 95%, percentile) of the mean of a series (NaNs dropped, order kept)."""
    v = np.asarray(pd.Series(x, dtype=float).dropna())
    idx = resample_indices(len(v), method, mean_block, n_boot, seed)
    draws = v[idx].mean(axis=1)
    return BootCI(float(v.mean()), percentile_ci(draws, 0.90), percentile_ci(draws, 0.95), len(v), method, mean_block)


@dataclass(frozen=True)
class PairedDelta:
    mean_delta: float
    ci90: tuple[float, float]
    ci95: tuple[float, float]
    p_gt0: float
    """Share of bootstrap draws with mean(a − b) > 0."""
    n: int


def paired_delta(
    a: np.ndarray | pd.Series,
    b: np.ndarray | pd.Series,
    *,
    method: Method = "iid",
    mean_block: float = 1.0,
    n_boot: int = 10_000,
    seed: int = 0,
) -> PairedDelta:
    """Paired bootstrap of ``a − b`` (same anchors resampled for both), e.g. ΔIC vs the composite baseline."""
    d = pd.Series(np.asarray(a, dtype=float) - np.asarray(b, dtype=float)).dropna().to_numpy()
    idx = resample_indices(len(d), method, mean_block, n_boot, seed)
    draws = d[idx].mean(axis=1)
    return PairedDelta(
        float(d.mean()), percentile_ci(draws, 0.90), percentile_ci(draws, 0.95), float((draws > 0).mean()), len(d)
    )


def over_names(
    score: np.ndarray | pd.Series,
    label: np.ndarray | pd.Series,
    stat: Callable[[np.ndarray, np.ndarray], float],
    *,
    n_boot: int = 1_000,
    seed: int = 0,
) -> np.ndarray:
    """Draws of ``stat(score, label)`` resampling names with replacement within one cross-section (table R-B CI)."""
    s = np.asarray(score, dtype=float)
    y = np.asarray(label, dtype=float)
    ok = ~(np.isnan(s) | np.isnan(y))
    s, y = s[ok], y[ok]
    idx = np.random.default_rng(seed).integers(0, len(s), size=(n_boot, len(s)))
    return np.array([stat(s[i], y[i]) for i in idx])
