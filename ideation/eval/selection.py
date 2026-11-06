"""90/10 selection over trials, reading (b) (03 §C9, 02 §6.5–6.6, 05 §11).

* Clusters: hierarchical clustering of the training features on distance ``1 − |Spearman|``, average linkage, cut at
  0.5 (02 §6.5). Fitted on a fold's training rows only.
* Clustered permutation importance: permute a whole cluster (same row shuffle for all its columns) within each test
  anchor's cross-section, ``n_repeats`` times; importance = baseline mean IC − permuted mean IC.
* Stability of a trial = ``1 − mean over consecutive folds of d``, with ``d = (1 − ρ_s)/2`` the rank distance between
  the two folds' feature-level importance vectors (each feature carries its cluster's importance). This exact formula
  is our documented decision; the sources do not define stability (06 §4.5).
* Selection score = ``0.9·z(mean OOS IC) + 0.1·z(stability)``, z taken across the trials of the family. A trial
  without a stability value gets z = 0 for that term.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable, Sequence

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform

from ideation.eval.metrics import ic_by_anchor, rank_ic


def feature_clusters(X_train: pd.DataFrame, threshold: float = 0.5) -> dict[str, int]:
    cols = list(X_train.columns)
    if len(cols) == 1:
        return {cols[0]: 1}
    rho = X_train.rank().corr().abs().fillna(0.0).to_numpy()
    dist = 1.0 - rho
    np.fill_diagonal(dist, 0.0)
    z = linkage(squareform(np.clip(dist, 0, None), checks=False), method="average")
    labels = fcluster(z, t=threshold, criterion="distance")
    return {c: int(k) for c, k in zip(cols, labels, strict=True)}


def cluster_permutation_importance(
    predict: Callable[[pd.DataFrame], np.ndarray],
    X_test: pd.DataFrame,
    y_test: pd.Series,
    anchors: pd.Series,
    clusters: dict[str, int],
    *,
    n_repeats: int = 20,
    seed: int = 0,
) -> pd.Series:
    """Mean ΔIC per cluster (positive = the model relies on it)."""
    frame = pd.DataFrame({"anchor": np.asarray(anchors), "label": np.asarray(y_test, dtype=float)})
    base = ic_by_anchor(frame.assign(score=predict(X_test))).mean()
    rng = np.random.default_rng(seed)
    pos_by_anchor = [np.flatnonzero(frame["anchor"].to_numpy() == a) for a in pd.unique(frame["anchor"])]
    out = {}
    for k in sorted(set(clusters.values())):
        cols = [c for c, v in clusters.items() if v == k and c in X_test]
        drops = []
        for _ in range(n_repeats):
            xp = X_test.copy()
            block = xp[cols].to_numpy(copy=True)
            for pos in pos_by_anchor:
                block[pos] = block[rng.permutation(pos)]
            xp[cols] = block
            drops.append(base - ic_by_anchor(frame.assign(score=predict(xp))).mean())
        out[k] = float(np.mean(drops))
    return pd.Series(out, name="delta_ic")


def feature_importance(cluster_importance: pd.Series, clusters: dict[str, int]) -> pd.Series:
    return pd.Series({c: float(cluster_importance.get(k, 0.0)) for c, k in clusters.items()})


def stability(fold_importances: Sequence[pd.Series]) -> float:
    """``1 − mean_f (1 − ρ_s(imp_f, imp_{f+1})) / 2``; NaN with fewer than two folds."""
    if len(fold_importances) < 2:
        return float("nan")
    dists = []
    for a, b in itertools.pairwise(fold_importances):
        idx = a.index.union(b.index)
        rho = rank_ic(a.reindex(idx, fill_value=0.0).to_numpy(), b.reindex(idx, fill_value=0.0).to_numpy())
        dists.append((1 - (0.0 if np.isnan(rho) else rho)) / 2)
    return float(1 - np.mean(dists))


def _z(x: pd.Series) -> pd.Series:
    sd = x.std(ddof=1)
    return (x - x.mean()) / sd if sd and sd > 0 else x * 0.0


def selection_scores(mean_ic: pd.Series, stab: pd.Series) -> pd.Series:
    """``0.9·z(mean OOS IC) + 0.1·z(stability)`` across the trials (index = trial id)."""
    s = stab.reindex(mean_ic.index)
    return (0.9 * _z(mean_ic) + 0.1 * _z(s).fillna(0.0)).rename("selection_9010")


def select(mean_ic: pd.Series, stab: pd.Series) -> object:
    """The trial with the highest 90/10 score (ties: the first in index order)."""
    return selection_scores(mean_ic, stab).idxmax()
