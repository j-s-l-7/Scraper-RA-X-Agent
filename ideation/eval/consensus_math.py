"""Cross-model consensus math (05 §4); MS3's ``eval/consensus.py`` builds the report on top of this.

Inputs are one anchor's cross-section: ``p`` = names × models percentile frame (R3), ``ret`` = realized returns.

* ``votes_i = Σ_m 1[i ∈ D10_m]``; ``C_maj = {votes ≥ 6}``, ``C_unan = {votes = M}``, ``B_maj = {D1 votes ≥ 6}``;
  |C_unan| < 5 marks the anchor "too few" (excluded from means, counted).
* Agreement: pairwise Spearman of scores, Kendall's W across the M rankings (tie-corrected), pairwise Jaccard of the
  D10 sets.
* ``ENS_RANKAVG`` = mean over models of p.
* Comparisons: size-matched top-|C| of a single model, a random-|C| draw from the universe (percentile of the observed
  mean among ``n_draws`` draws); paired bootstrap of deltas lives in ``eval.bootstrap.paired_delta``.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ideation.rank.buckets import decile


def votes(p: pd.DataFrame, bucket: int = 10) -> pd.Series:
    """Number of models placing each name in decile ``bucket`` (10 = top, 1 = bottom)."""
    return p.apply(lambda col: decile(col) == bucket).fillna(False).sum(axis=1).astype(int)


@dataclass(frozen=True)
class ConsensusSets:
    c_maj: pd.Index
    c_unan: pd.Index
    b_maj: pd.Index
    too_few_unan: bool
    n_models: int


def consensus_sets(p: pd.DataFrame, *, majority: int = 6, min_unan: int = 5) -> ConsensusSets:
    top, bottom = votes(p, 10), votes(p, 1)
    m = p.shape[1]
    c_unan = top.index[top == m]
    return ConsensusSets(
        top.index[top >= majority], c_unan, bottom.index[bottom >= majority], len(c_unan) < min_unan, m
    )


def ens_rankavg(p: pd.DataFrame) -> pd.Series:
    return p.mean(axis=1).rename("ENS_RANKAVG")


def kendall_w(scores: pd.DataFrame) -> float:
    """Kendall's coefficient of concordance of the M column rankings over the n rows (tie-corrected)."""
    s = scores.dropna()
    n, m = s.shape
    if n < 2 or m < 2:
        return float("nan")
    ranks = s.rank(axis=0)
    r = ranks.sum(axis=1).to_numpy()
    big_s = ((r - r.mean()) ** 2).sum()
    ties = sum(((cnt**3 - cnt).sum()) for cnt in (ranks[c].value_counts().to_numpy() for c in ranks))
    return float(12 * big_s / (m**2 * (n**3 - n) - m * ties))


def pairwise_spearman(scores: pd.DataFrame) -> pd.DataFrame:
    return scores.rank().corr()


def pairwise_jaccard(p: pd.DataFrame) -> pd.DataFrame:
    sets = {c: set(p.index[(decile(p[c]) == 10).fillna(False)]) for c in p.columns}
    cols = list(p.columns)
    out = pd.DataFrame(np.nan, index=cols, columns=cols)
    for a in cols:
        for b in cols:
            union = sets[a] | sets[b]
            out.loc[a, b] = len(sets[a] & sets[b]) / len(union) if union else np.nan
    return out


def top_k(p: pd.Series, k: int) -> pd.Index:
    """Size-matched comparison set: the k names with the highest p (ties broken by index order)."""
    return p.dropna().sort_values(ascending=False, kind="stable").index[:k]


def random_set_percentile(
    ret: pd.Series, k: int, observed_mean: float, *, n_draws: int = 1_000, seed: int = 0
) -> float:
    """Share of random size-k draws from the cross-section whose mean return is below ``observed_mean``."""
    r = ret.dropna().to_numpy()
    if k <= 0 or k > len(r):
        return float("nan")
    rng = np.random.default_rng(seed)
    means = np.array([r[rng.choice(len(r), size=k, replace=False)].mean() for _ in range(n_draws)])
    return float((means < observed_mean).mean())


def set_excess(ret: pd.Series, members: pd.Index, bench: float | None = None) -> dict[str, float]:
    """Equal-weight mean and median excess of a set vs a benchmark (default: the cross-section median) and its hit
    rate (share beating the benchmark)."""
    ref = float(ret.median()) if bench is None else bench
    x = ret.reindex(members).dropna()
    return {
        "n": len(x),
        "mean": float(x.mean() - ref),
        "median": float(x.median() - ref),
        "hit": float((x > ref).mean()),
    }
