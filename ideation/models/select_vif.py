"""In-fold feature selection for the WFO sign-constrained Ridge (05 §3.4, 06 §3.4 / §10 item 9).

1. Univariate pooled OLS ``y = a + b·x`` per feature with anchor-clustered (CR1) standard errors; keep p < 0.05.
   The p-value uses a t distribution with G − 1 degrees of freedom (G = number of anchors), the usual small-cluster
   correction; with 3 training anchors that is a demanding threshold (t > 4.30).
2. Keep the best |t| member of each metric family (the feature YAML's ``family:`` key).
3. Iteratively drop the feature with the largest VIF until every VIF < 5.

Everything is fitted on the training rows handed in; nothing here sees a test anchor.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy import stats


def vif(X: pd.DataFrame) -> pd.Series:
    """VIF_j = 1 / (1 − R²_j) of column j regressed on the others plus an intercept (inf if collinear)."""
    m = X.to_numpy(dtype=float)
    n, _k = m.shape
    out = {}
    for j, col in enumerate(X.columns):
        target = m[:, j]
        others = np.column_stack([np.ones(n), np.delete(m, j, axis=1)])
        resid = target - others @ np.linalg.lstsq(others, target, rcond=None)[0]
        tss = ((target - target.mean()) ** 2).sum()
        r2 = 1 - (resid @ resid) / tss if tss > 0 else 1.0
        out[col] = np.inf if r2 >= 1 - 1e-12 else 1 / (1 - r2)
    return pd.Series(out, dtype=float)


def iterative_vif(X: pd.DataFrame, threshold: float = 5.0) -> tuple[list[str], list[str]]:
    """(kept, dropped in order): drop the max-VIF column until all VIF < ``threshold``."""
    kept, dropped = list(X.columns), []
    while len(kept) > 1:
        v = vif(X[kept])
        if v.max() < threshold:
            break
        worst = str(v.idxmax())
        kept.remove(worst)
        dropped.append(worst)
    return kept, dropped


def clustered_ols(x: np.ndarray, y: np.ndarray, clusters: np.ndarray) -> tuple[float, float, float, float]:
    """Univariate OLS slope with CR1 cluster-robust SE: (beta, se, t, p) with p from t(G − 1)."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    n = len(x)
    design = np.column_stack([np.ones(n), x])
    bread = np.linalg.pinv(design.T @ design)
    coef = bread @ design.T @ y
    resid = y - design @ coef
    codes = pd.factorize(np.asarray(clusters))[0]
    g = codes.max() + 1
    scores = np.zeros((g, 2))
    np.add.at(scores, codes, design * resid[:, None])
    meat = scores.T @ scores
    adj = g / (g - 1) * (n - 1) / (n - 2) if g > 1 and n > 2 else 1.0
    cov = adj * bread @ meat @ bread
    se = float(np.sqrt(max(cov[1, 1], 0.0)))
    t = float(coef[1] / se) if se > 0 else 0.0
    p = float(2 * stats.t.sf(abs(t), max(g - 1, 1)))
    return float(coef[1]), se, t, p


def univariate_screen(X: pd.DataFrame, y: np.ndarray, clusters: np.ndarray) -> pd.DataFrame:
    rows = {c: clustered_ols(X[c].to_numpy(), y, clusters) for c in X.columns}
    return pd.DataFrame.from_dict(rows, orient="index", columns=["beta", "se", "t", "p"])


def best_per_family(t: pd.Series, family: Mapping[str, str]) -> list[str]:
    """The max-|t| feature of every family (a feature without a family is its own family), in input order."""
    fam = pd.Series({c: family.get(c, c) for c in t.index})
    best = t.abs().groupby(fam).idxmax()
    return [c for c in t.index if c in set(best)]


@dataclass
class Selection:
    selected: list[str]
    screened: pd.DataFrame
    passed_p: list[str] = field(default_factory=list)
    family_best: list[str] = field(default_factory=list)
    vif_dropped: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.selected


def wfo_select(
    X: pd.DataFrame,
    y: np.ndarray,
    clusters: np.ndarray,
    family: Mapping[str, str],
    *,
    p_max: float = 0.05,
    vif_max: float = 5.0,
    candidates: Sequence[str] | None = None,
) -> Selection:
    cols = list(candidates) if candidates is not None else list(X.columns)
    screened = univariate_screen(X[cols], y, clusters)
    passed = [c for c in cols if screened.loc[c, "p"] < p_max]
    fam_best = best_per_family(screened.loc[passed, "t"], family) if passed else []
    kept, dropped = iterative_vif(X[fam_best], vif_max) if len(fam_best) > 1 else (fam_best, [])
    return Selection(kept, screened, passed, fam_best, dropped)
