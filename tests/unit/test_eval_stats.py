"""02 §5 statistics: DSR/PBO worked examples, CSCV, Newey–West, bootstraps, permutation null, per-anchor metrics."""

from __future__ import annotations

import math
from itertools import combinations

import numpy as np
import pandas as pd
import pytest

from ideation.cv.cscv import cscv_splits
from ideation.eval.bootstrap import mean_ci, over_names, paired_delta, stationary_indices
from ideation.eval.dsr import deflated_sharpe, dsr_of_series, expected_max_sr, probabilistic_sr, sharpe_moments
from ideation.eval.inference import (
    ic_summary,
    newey_west_se,
    newey_west_t,
    nw_lags,
    permutation_null,
    shuffle_within,
)
from ideation.eval.metrics import (
    bucket_table,
    cost_adjusted,
    hit_rate,
    ic_by_anchor,
    monotonicity,
    rank_ic,
    spread,
    tail_order,
    top_vs_bench,
    turnover,
)
from ideation.eval.pbo import pbo_cscv, pbo_from_is_oos, sharpe_perf
from ideation.rank.percentile import percentile


# ── DSR: Bailey & López de Prado (2014) §6 numerical example ────────────────────────────────────────────────
def test_dsr_worked_example() -> None:
    """Annualised SR 2.5 after N = 100 trials with V[SR_n] = 0.5 (annualised), T = 1,250 daily returns,
    skew −3, kurtosis 10 ⇒ SR0 ≈ 0.1132 (daily) and DSR ≈ 0.9004 (published)."""
    res = deflated_sharpe(2.5 / math.sqrt(250), 1250, -3.0, 10.0, var_sr=0.5 / 250, n_trials=100)
    assert res.sr0 == pytest.approx(0.1132, abs=1e-3)
    assert res.dsr == pytest.approx(0.9004, abs=1e-3)


def test_psr_and_sr0_properties() -> None:
    assert expected_max_sr(1.0, 1) == 0.0
    assert expected_max_sr(1.0, 1000) > expected_max_sr(1.0, 10) > 0
    assert probabilistic_sr(0.1, 0.0, 1000, 0.0, 3.0) > 0.99
    assert probabilistic_sr(0.0, 0.0, 1000, 0.0, 3.0) == pytest.approx(0.5)
    rng = np.random.default_rng(0)
    x = rng.normal(0.05, 1.0, 5000)
    _sr, n, skew, kurt = sharpe_moments(x)
    assert n == 5000
    assert kurt == pytest.approx(3.0, abs=0.2)
    assert abs(skew) < 0.1
    trials = rng.normal(0, 0.05, 50)
    res = dsr_of_series(x, trials)
    assert res.n_trials == 50
    assert res.sr0 > 0
    assert 0 <= res.dsr <= 1


# ── PBO: hand-worked CSCV example (T = 4 rows, S = 4 blocks, N = 3 trials, metric = mean) ───────────────────
HAND = np.array(
    [
        [9.0, 5.0, 1.0],
        [2.0, 7.0, 3.0],
        [4.0, 0.0, 14.0],
        [3.0, 6.0, 12.0],
    ]
)


def test_pbo_hand_worked_example() -> None:
    """IS block pairs in order (0,1) (0,2) (0,3) (1,2) (1,3) (2,3). Column sums per pair:
    trial 0: 11 13 12 6 5 7 · trial 1: 12 5 11 7 13 6 · trial 2: 4 15 13 17 15 26.
    IS winners: 1, 2, 2, 2, 2, 2; their OOS rank (complement pair): 1, 3, 3, 3, 3, 1 of N = 3
    ⇒ ω̄ = 1/4 or 3/4, λ = −ln 3 or +ln 3, PBO = 2/6."""
    res = pbo_cscv(HAND, n_blocks=4)
    ln3 = math.log(3)
    assert res.n_splits == 6
    assert res.n_trials == 3
    assert res.best_is.tolist() == [1, 2, 2, 2, 2, 2]
    assert res.logits == pytest.approx([-ln3, ln3, ln3, ln3, ln3, -ln3], abs=1e-12)
    assert res.pbo == pytest.approx(1 / 3, abs=1e-3)


def test_pbo_limits() -> None:
    zero_sum = np.array([[3.0, 1.0], [1.0, 2.0], [2.0, 0.0], [0.0, 3.0]])  # equal totals: IS winner loses OOS
    assert pbo_cscv(zero_sum, n_blocks=4).pbo == 1.0
    rng = np.random.default_rng(1)
    base = rng.normal(size=(64, 5))
    dominant = base.copy()
    dominant[:, 0] = base.max(axis=1) + 1.0  # best in every period
    assert pbo_cscv(dominant, n_blocks=8).pbo == 0.0
    noise = rng.normal(size=(240, 20))
    assert 0.25 < pbo_cscv(noise, n_blocks=12, metric=sharpe_perf).pbo < 0.75


def test_pbo_from_is_oos_shapes() -> None:
    with pytest.raises(ValueError, match="same shape"):
        pbo_from_is_oos(np.zeros((3, 2)), np.zeros((2, 3)))


def test_cscv_split_counts() -> None:
    splits = cscv_splits(138, 12)
    assert len(splits) == math.comb(12, 6) == 924
    counts = np.zeros(138)
    for is_rows, oos_rows in splits:
        assert len(np.intersect1d(is_rows, oos_rows)) == 0
        assert len(is_rows) + len(oos_rows) == 138
        counts[is_rows] += 1
    assert (counts == 462).all()
    with pytest.raises(ValueError, match="even"):
        cscv_splits(10, 3)


# ── Newey–West and IC summary ───────────────────────────────────────────────────────────────────────────────
def test_nw_lags_table() -> None:
    assert nw_lags(12, 12) == 0  # 1Y annual
    assert nw_lags(12, 1) == 11  # 1Y monthly
    assert nw_lags(3, 1) == 2  # 3M monthly
    assert nw_lags(1, 1) == 0  # 1M monthly


def test_newey_west() -> None:
    x = np.array([0.1, -0.05, 0.02, 0.07, 0.0, 0.03])
    assert newey_west_se(x, 0) == pytest.approx(x.std(ddof=0) / math.sqrt(len(x)))
    rng = np.random.default_rng(0)
    e = rng.normal(size=600)
    ar = np.empty(600)
    ar[0] = e[0]
    for t in range(1, 600):
        ar[t] = 0.8 * ar[t - 1] + e[t]
    assert newey_west_se(ar, 11) > 1.8 * newey_west_se(ar, 0)
    assert newey_west_t(np.full(5, 0.1) + np.array([0, 1e-3, -1e-3, 2e-3, 0]), 0) > 10


def test_ic_summary_annual_and_monthly() -> None:
    ic = pd.Series([0.05, 0.02, -0.01, 0.04, 0.06])
    s = ic_summary(ic, 12, 12, n_boot=2000)
    assert s["n"] == 5
    assert s["nw_lags"] == 0
    assert s["boot_method"] == "iid"
    assert s["pct_positive"] == 0.8
    assert "sign_test_p" in s
    assert "t_classic_p" in s
    assert s["ci90"][0] < s["mean"] < s["ci90"][1]
    m = ic_summary(pd.Series(np.random.default_rng(0).normal(0.02, 0.05, 120)), 3, 1, n_boot=2000)
    assert m["nw_lags"] == 2
    assert m["boot_method"] == "stationary"
    assert m["mean_block"] == 5.0
    assert "sign_test_p" not in m


# ── bootstraps ──────────────────────────────────────────────────────────────────────────────────────────────
def test_stationary_bootstrap_block_length_and_determinism() -> None:
    idx = stationary_indices(500, 5.0, 200, np.random.default_rng(0))
    continues = (np.diff(idx, axis=1) % 500) == 1
    assert 1 / (1 - continues.mean()) == pytest.approx(5.0, rel=0.1)
    a = mean_ci(np.arange(30.0), method="stationary", mean_block=3, n_boot=500, seed=4)
    b = mean_ci(np.arange(30.0), method="stationary", mean_block=3, n_boot=500, seed=4)
    assert a == b


def test_mean_ci_and_paired_delta() -> None:
    rng = np.random.default_rng(3)
    x = rng.normal(0, 1, 400)
    ci = mean_ci(x, n_boot=4000)
    assert ci.ci95[0] < 0 < ci.ci95[1]
    assert ci.ci95[0] < ci.ci90[0]
    d = paired_delta(x + 0.5, x, n_boot=2000)
    assert d.p_gt0 == 1.0
    assert d.mean_delta == pytest.approx(0.5)
    d0 = paired_delta(rng.normal(size=200), rng.normal(size=200), n_boot=2000)
    assert 0.0 < d0.p_gt0 < 1.0


def test_over_names() -> None:
    rng = np.random.default_rng(0)
    s = rng.normal(size=300)
    y = 0.3 * s + rng.normal(size=300)
    draws = over_names(s, y, rank_ic, n_boot=300, seed=1)
    assert np.quantile(draws, 0.05) > 0.1


# ── permutation null ────────────────────────────────────────────────────────────────────────────────────────
def test_shuffle_within_keeps_groups() -> None:
    g = np.array([0, 0, 1, 1, 1, 2, 0, 2])
    perm = shuffle_within(g, np.random.default_rng(0))
    assert sorted(perm) == list(range(8))
    assert (g[perm] == g).all()
    g2 = np.column_stack([g, [0, 1, 0, 0, 1, 1, 0, 1]])
    perm2 = shuffle_within(g2, np.random.default_rng(1))
    assert (g2[perm2] == g2).all()


def _xs(n_anchor: int, n: int, rho: float, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for a in range(n_anchor):
        s = rng.normal(size=n)
        y = rho * s + math.sqrt(1 - rho**2) * rng.normal(size=n)
        rows.append(pd.DataFrame({"anchor": a, "score": s, "label": y, "industry_group": rng.integers(0, 5, n)}))
    return pd.concat(rows, ignore_index=True)


def test_permutation_null() -> None:
    sig = permutation_null(_xs(4, 300, 0.3, 0), n_perm=300)
    assert sig["p_value"] == pytest.approx(1 / 301)
    assert sig["observed"] == pytest.approx(ic_by_anchor(_xs(4, 300, 0.3, 0)).mean())
    null = permutation_null(_xs(4, 300, 0.0, 1), n_perm=300)
    assert abs(np.mean(null["null"])) < 0.01
    assert null["p_value"] > 0.05


# ── per-anchor metrics ──────────────────────────────────────────────────────────────────────────────────────
def test_rank_ic() -> None:
    assert rank_ic([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert rank_ic([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert math.isnan(rank_ic([1, 2, np.nan], [1, 2, 3]))
    assert math.isnan(rank_ic([1, 1, 1, 1], [1, 2, 3, 4]))
    assert rank_ic([1, 2, 3, 4, np.nan], [1, 3, 2, 4, 9]) == pytest.approx(0.8)


def test_spread_hit_turnover_costs() -> None:
    score = pd.Series(np.arange(100.0))
    ret = pd.Series(np.arange(100.0) / 100)
    assert spread(score, ret, q=5) == pytest.approx(np.mean(np.arange(80, 100)) / 100 - np.mean(np.arange(20)) / 100)
    assert spread(score, ret, q=10, stat="median") == pytest.approx(0.9)
    assert hit_rate(score, ret) == 1.0
    assert hit_rate(-score, ret) == 0.0
    assert top_vs_bench(score, ret, 0.5, q=5) == pytest.approx(0.395)
    assert turnover({1, 2, 3, 4}, {3, 4, 5, 6}) == 0.5
    assert cost_adjusted(0.10, 0.5) == pytest.approx(0.10 - 0.0012)


def test_bucket_table_monotonicity_tail_order() -> None:
    p = percentile(pd.Series(np.arange(1000.0)))
    ret = pd.Series(np.arange(1000.0) / 1000)
    t = bucket_table(p, ret)
    assert t.loc["P99", "n"] == 10
    assert t.loc["D10", "n"] == 100
    assert t.loc["P1", "n"] == 10
    assert t.loc["D10", "hit"] == 1.0
    assert t.loc["D1", "hit"] == 0.0
    assert monotonicity(p, ret) == pytest.approx(1.0)
    assert tail_order(t["mean"]) == (True, True)
    assert tail_order(bucket_table(p, -ret)["mean"]) == (False, False)


def test_combinations_order_matches_cscv() -> None:
    """The hand example relies on itertools.combinations order of the IS blocks."""
    assert [tuple(i) for i, _ in cscv_splits(4, 4)] == [tuple(c) for c in combinations(range(4), 2)]
