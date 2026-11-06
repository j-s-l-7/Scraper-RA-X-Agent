"""Probabilistic and deflated Sharpe ratio (Bailey & López de Prado 2012, 2014; 02 §5.3).

Sharpe ratios are per observation (not annualised). ``kurt`` is the Pearson kurtosis (normal = 3), so pass
``scipy.stats.kurtosis(x, fisher=False)``.

    SR0 = sqrt(V[SR_n]) · ((1 − γ) Φ⁻¹(1 − 1/N) + γ Φ⁻¹(1 − 1/(N e)))          γ = Euler–Mascheroni
    PSR(SR*) = Φ( (SR − SR*) √(T − 1) / √(1 − skew·SR + (kurt − 1)/4 · SR²) )
    DSR = PSR(SR0)

Applied to the cost-adjusted Q5−Q1 series and to the IC series (IC-IR as the Sharpe) with N = trials in the family.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats

EULER_GAMMA = 0.5772156649015329


def expected_max_sr(var_sr: float, n_trials: int) -> float:
    """Expected maximum of N independent Sharpe estimates with variance ``var_sr`` and mean 0 (SR0)."""
    if n_trials <= 1:
        return 0.0
    z1 = stats.norm.ppf(1 - 1 / n_trials)
    z2 = stats.norm.ppf(1 - 1 / (n_trials * math.e))
    return math.sqrt(var_sr) * ((1 - EULER_GAMMA) * z1 + EULER_GAMMA * z2)


def probabilistic_sr(sr: float, sr_benchmark: float, n_obs: int, skew: float, kurt: float) -> float:
    denom = 1 - skew * sr + (kurt - 1) / 4 * sr**2
    if n_obs < 2 or denom <= 0:
        return float("nan")
    return float(stats.norm.cdf((sr - sr_benchmark) * math.sqrt(n_obs - 1) / math.sqrt(denom)))


@dataclass(frozen=True)
class DSRResult:
    dsr: float
    sr: float
    sr0: float
    n_obs: int
    n_trials: int
    var_sr: float
    skew: float
    kurt: float


def deflated_sharpe(
    sr: float,
    n_obs: int,
    skew: float,
    kurt: float,
    *,
    sr_trials: np.ndarray | None = None,
    var_sr: float | None = None,
    n_trials: int | None = None,
) -> DSRResult:
    """DSR of a selected strategy. Give either the trials' Sharpe ratios or (``var_sr``, ``n_trials``)."""
    if sr_trials is not None:
        t = np.asarray(sr_trials, dtype=float)
        t = t[~np.isnan(t)]
        var_sr = float(np.var(t, ddof=1)) if len(t) > 1 else 0.0
        n_trials = len(t) if n_trials is None else n_trials
    if var_sr is None or n_trials is None:
        raise ValueError("deflated_sharpe needs sr_trials or both var_sr and n_trials")
    sr0 = expected_max_sr(var_sr, n_trials)
    return DSRResult(probabilistic_sr(sr, sr0, n_obs, skew, kurt), sr, sr0, n_obs, n_trials, var_sr, skew, kurt)


def sharpe_moments(x: np.ndarray | pd.Series) -> tuple[float, int, float, float]:
    """(per-observation SR = mean/std(ddof=1), n, skew, Pearson kurtosis) of a return or IC series."""
    v = np.asarray(pd.Series(x, dtype=float).dropna())
    n = len(v)
    sd = v.std(ddof=1) if n > 1 else float("nan")
    sr = float(v.mean() / sd) if sd and sd > 0 else float("nan")
    return sr, n, float(stats.skew(v)), float(stats.kurtosis(v, fisher=False))


def dsr_of_series(x: np.ndarray | pd.Series, sr_trials: np.ndarray, n_trials: int | None = None) -> DSRResult:
    """DSR of a series against the family's per-trial Sharpe ratios (``n_trials`` defaults to their count)."""
    sr, n, skew, kurt = sharpe_moments(x)
    return deflated_sharpe(sr, n, skew, kurt, sr_trials=sr_trials, n_trials=n_trials)
