"""Time-series inference on IC series (02 §5.2, 05 §8) and the within-group permutation null.

* Newey–West (Bartlett) t of the mean with lag ``L = max(0, ceil(h / spacing) − 1)``:
  annual 1Y → 0, monthly 1Y → 11, monthly 3M → 2, monthly 1M → 0.
* CIs on the mean: iid bootstrap over anchors for annual series (plus the exact sign test and a t with n − 1 df),
  stationary block bootstrap with mean block ``max(L + 1, round(n^(1/3)))`` for monthly series.
* Permutation null: labels permuted within (anchor, industry) groups; the same shuffling serves T-04.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from ideation.eval.bootstrap import mean_ci


def nw_lags(horizon_months: int, spacing_months: int) -> int:
    return max(0, math.ceil(horizon_months / spacing_months) - 1)


def newey_west_se(x: np.ndarray | pd.Series, lags: int) -> float:
    """HAC standard error of the sample mean (Bartlett kernel, 1/n autocovariances)."""
    v = np.asarray(pd.Series(x, dtype=float).dropna())
    n = len(v)
    e = v - v.mean()
    s = e @ e / n
    for lag in range(1, min(lags, n - 1) + 1):
        s += 2 * (1 - lag / (lags + 1)) * (e[lag:] @ e[:-lag]) / n
    return math.sqrt(max(s, 0.0) / n)


def newey_west_t(x: np.ndarray | pd.Series, lags: int) -> float:
    v = pd.Series(x, dtype=float).dropna()
    se = newey_west_se(v, lags)
    return float(v.mean() / se) if se > 0 else float("nan")


def ic_summary(
    ic: pd.Series, horizon_months: int, spacing_months: int, *, n_boot: int = 10_000, seed: int = 0
) -> dict[str, Any]:
    v = pd.Series(ic, dtype=float).dropna()
    n = len(v)
    lags = nw_lags(horizon_months, spacing_months)
    sd = float(v.std(ddof=1)) if n > 1 else float("nan")
    out: dict[str, Any] = {
        "n": n,
        "mean": float(v.mean()),
        "median": float(v.median()),
        "std": sd,
        "ir": float(v.mean() / sd) if sd and sd > 0 else float("nan"),
        "pct_positive": float((v > 0).mean()),
        "nw_lags": lags,
        "nw_t": newey_west_t(v, lags),
    }
    annual = spacing_months >= 12
    block = 1.0 if annual else float(max(lags + 1, round(n ** (1 / 3))))
    ci = mean_ci(v, method="iid" if annual else "stationary", mean_block=block, n_boot=n_boot, seed=seed)
    out.update(ci90=ci.ci90, ci95=ci.ci95, boot_method=ci.method, mean_block=block)
    if n < 10:
        k = int((v > 0).sum())
        out["sign_test_p"] = float(stats.binomtest(k, n, 0.5).pvalue) if n else float("nan")
        out["t_classic"] = float(v.mean() / (sd / math.sqrt(n))) if sd and sd > 0 else float("nan")
        out["t_classic_p"] = float(2 * stats.t.sf(abs(out["t_classic"]), n - 1)) if n > 1 else float("nan")
    return out


def shuffle_within(groups: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """A permutation ``perm`` of row positions that only moves rows within their group: ``y[perm]`` is shuffled."""
    g = np.asarray(groups)
    codes = pd.factorize(g)[0] if g.ndim == 1 else np.unique(g, axis=0, return_inverse=True)[1].ravel()
    base = np.lexsort((np.arange(len(codes)), codes))  # rows sorted by group, stable
    shuffled = np.lexsort((rng.random(len(codes)), codes))  # same group blocks, random order inside
    perm = np.empty(len(codes), dtype=np.int64)
    perm[base] = shuffled
    return perm


def _centered_ranks(x: pd.Series, anchor_codes: np.ndarray) -> np.ndarray:
    r = x.groupby(anchor_codes).rank().to_numpy()
    means = pd.Series(r).groupby(anchor_codes).transform("mean").to_numpy()
    return r - means


def permutation_null(
    df: pd.DataFrame,
    *,
    score: str = "score",
    label: str = "label",
    anchor: str = "anchor",
    group: str | None = "industry_group",
    n_perm: int = 1_000,
    seed: int = 0,
) -> dict[str, Any]:
    """Null distribution of the mean per-anchor rank IC with labels permuted within (anchor, group).

    Returns the observed mean IC, per-anchor observed ICs, the null draws and a one-sided p-value
    ``(1 + #{null ≥ observed}) / (1 + n_perm)``.
    """
    d = df[[c for c in (score, label, anchor, group) if c]].dropna(subset=[score, label]).reset_index(drop=True)
    a_codes = pd.factorize(d[anchor])[0]
    rs = _centered_ranks(d[score], a_codes)
    ry = _centered_ranks(d[label], a_codes)
    n_a = a_codes.max() + 1
    norm = np.sqrt(np.bincount(a_codes, rs * rs, n_a) * np.bincount(a_codes, ry * ry, n_a))
    obs_by_anchor = np.bincount(a_codes, rs * ry, n_a) / norm
    keys = np.column_stack([a_codes, pd.factorize(d[group])[0]]) if group else a_codes
    rng = np.random.default_rng(seed)
    null = np.empty(n_perm)
    for i in range(n_perm):
        perm = shuffle_within(keys, rng)
        null[i] = np.mean(np.bincount(a_codes, rs * ry[perm], n_a) / norm)
    observed = float(obs_by_anchor.mean())
    return {
        "observed": observed,
        "observed_by_anchor": obs_by_anchor,
        "null": null,
        "p_value": float((1 + (null >= observed).sum()) / (1 + n_perm)),
    }
