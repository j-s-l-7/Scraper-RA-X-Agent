"""90/10 selection reading (b) (02 §6.5–6.6) and the consensus math (05 §4)."""

from __future__ import annotations

import itertools

import numpy as np
import pandas as pd
import pytest

from ideation.eval import consensus_math as cm
from ideation.eval.selection import (
    cluster_permutation_importance,
    feature_clusters,
    feature_importance,
    select,
    selection_scores,
    stability,
)
from ideation.rank.percentile import percentile


def test_feature_clusters() -> None:
    rng = np.random.default_rng(0)
    a = rng.normal(size=500)
    X = pd.DataFrame({"a": a, "a2": a + 0.2 * rng.normal(size=500), "b": rng.normal(size=500)})
    cl = feature_clusters(X)
    assert cl["a"] == cl["a2"]
    assert cl["b"] != cl["a"]


def test_cluster_permutation_importance() -> None:
    rng = np.random.default_rng(1)
    n = 600
    X = pd.DataFrame({"a": rng.normal(size=n), "a2": rng.normal(size=n), "b": rng.normal(size=n)})
    y = pd.Series(X["a"] + 0.5 * rng.normal(size=n))
    anchors = pd.Series(np.repeat([1, 2, 3], n // 3))
    clusters = {"a": 1, "a2": 2, "b": 3}
    imp = cluster_permutation_importance(lambda x: x["a"].to_numpy(), X, y, anchors, clusters, n_repeats=5)
    assert imp[1] > 0.5
    assert imp[2] == 0.0
    assert imp[3] == 0.0
    feat = feature_importance(imp, clusters)
    assert feat["a"] == imp[1]


def test_stability() -> None:
    v = pd.Series({"a": 0.3, "b": 0.1, "c": 0.0})
    assert stability([v, v, v]) == pytest.approx(1.0)
    assert stability([v, -v]) == pytest.approx(0.0)
    assert np.isnan(stability([v]))


def test_selection_scores_formula() -> None:
    ic = pd.Series({"T1": 0.03, "T2": 0.05, "T3": 0.04})
    st = pd.Series({"T1": 0.9, "T2": 0.5, "T3": np.nan})
    z_ic = (ic - ic.mean()) / ic.std(ddof=1)
    st_known = st.dropna()
    z_st = ((st_known - st_known.mean()) / st_known.std(ddof=1)).reindex(ic.index).fillna(0.0)
    assert selection_scores(ic, st).to_numpy() == pytest.approx((0.9 * z_ic + 0.1 * z_st).to_numpy())
    assert select(ic, st) == "T2"
    assert select(pd.Series({"A": 0.050, "B": 0.049}), pd.Series({"A": 0.0, "B": 1.0})) == "A"


def _p_frame(n: int = 200, m: int = 10, noise: float = 0.5, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    base = rng.normal(size=n)
    return pd.DataFrame({f"m{j}": percentile(pd.Series(base + noise * rng.normal(size=n))) for j in range(m)})


def test_votes_and_sets() -> None:
    p = _p_frame()
    v = cm.votes(p)
    assert v.max() <= 10
    assert v.sum() == 10 * 20  # each model has 20 names in D10
    sets = cm.consensus_sets(p)
    assert set(sets.c_unan) <= set(sets.c_maj)
    assert (v[sets.c_maj] >= 6).all()
    assert (v[sets.c_unan] == 10).all()
    assert not set(sets.c_maj) & set(sets.b_maj)
    same = pd.DataFrame({f"m{j}": percentile(pd.Series(np.arange(100.0))) for j in range(10)})
    s2 = cm.consensus_sets(same)
    assert len(s2.c_unan) == 10
    assert not s2.too_few_unan
    assert cm.consensus_sets(_p_frame(noise=5.0, seed=2)).too_few_unan


def test_kendall_w() -> None:
    x = pd.Series(np.arange(30.0))
    assert cm.kendall_w(pd.DataFrame({"a": x, "b": x, "c": x})) == pytest.approx(1.0)
    assert cm.kendall_w(pd.DataFrame({"a": x, "b": -x})) == pytest.approx(0.0)
    s = _p_frame(n=150, m=5, noise=1.0, seed=4)
    w = cm.kendall_w(s)
    rho = cm.pairwise_spearman(s).to_numpy()
    mean_rho = rho[np.triu_indices(5, 1)].mean()
    assert mean_rho == pytest.approx((5 * w - 1) / 4)  # identity for untied rankings


def test_jaccard_ensemble_and_comparisons() -> None:
    p = _p_frame(seed=5)
    j = cm.pairwise_jaccard(p)
    assert np.diag(j.to_numpy()) == pytest.approx(np.ones(10))
    assert ((j.to_numpy() >= 0) & (j.to_numpy() <= 1)).all()
    ens = cm.ens_rankavg(p)
    assert ens.to_numpy() == pytest.approx(p.mean(axis=1).to_numpy())
    assert list(cm.top_k(pd.Series([0.1, 0.9, 0.5, 0.9]), 2)) == [1, 3]
    ret = pd.Series(np.arange(100.0))
    assert cm.random_set_percentile(ret, 10, ret.nlargest(10).mean(), n_draws=200) == 1.0
    ex = cm.set_excess(ret, pd.Index([97, 98, 99]))
    assert ex == {"n": 3, "mean": 98 - 49.5, "median": 98 - 49.5, "hit": 1.0}
    for a, b in itertools.combinations(range(3), 2):
        assert j.iloc[a, b] == j.iloc[b, a]
