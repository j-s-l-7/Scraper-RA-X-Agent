"""R5–R7 (05 §2): nested isotonic calibration, Mondrian conformal band with time decay, conviction."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from ideation.rank import calibrate, conformal
from ideation.rank.conviction import UNSCORABLE, base_conviction, conviction
from ideation.rank.percentile import percentile

ANCHORS = pd.to_datetime([f"{y}-06-30" for y in range(2016, 2026)])


def oos_history(n: int = 400, seed: int = 0, slope: float = 0.2, noise: float = 0.3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    frames = []
    for i, a in enumerate(ANCHORS):
        s = rng.normal(size=n)
        p = percentile(pd.Series(s))
        excess = slope * (p - 0.5) + noise * rng.normal(size=n)
        end = ANCHORS[i + 1] if i + 1 < len(ANCHORS) else a + pd.DateOffset(years=1)
        frames.append(pd.DataFrame({"anchor": a, "p": p, "excess": excess, "label_end": end}))
    return pd.concat(frames, ignore_index=True)


def test_nested_history_rules() -> None:
    oos = oos_history()
    t = ANCHORS[5]  # 2021
    used = calibrate.history_anchors(oos, t, counted=ANCHORS[3:], sealed=[ANCHORS[-1]])
    assert used == list(ANCHORS[3:5])  # counted and < T only
    assert calibrate.fit(oos, ANCHORS[0]) is None  # the first anchor has no history ("n/a")
    late = oos.copy()
    late.loc[late["anchor"] == ANCHORS[4], "label_end"] = t + pd.Timedelta(days=1)
    assert ANCHORS[4] not in calibrate.history_anchors(late, t)  # 03 §C3: label must have ended by T
    pooled = calibrate.fit(oos, None, scheme="pooled", sealed=[ANCHORS[-1]])
    assert pooled.anchors == tuple(ANCHORS[:-1])


def test_isotonic_monotone_and_shrunk() -> None:
    oos = oos_history(n=60, seed=3, slope=0.05, noise=0.4)  # noisy ⇒ raw decile means are not monotone
    cal = calibrate.fit(oos, ANCHORS[6])
    tab = cal.table
    assert not tab["alpha_raw"].is_monotonic_increasing
    assert tab["alpha_iso"].is_monotonic_increasing
    assert tab["alpha_shrunk"].to_numpy() == pytest.approx(0.5 * tab["alpha_raw"].to_numpy())
    assert tab["n"].sum() == 6 * 60
    grid = np.linspace(0, 1, 201)
    assert np.all(np.diff(cal.alpha(grid)) >= 0)
    a = tab["alpha_iso"].to_numpy()
    assert cal.alpha([0.0, 0.05, 0.10, 0.95, 1.0]).tolist() == pytest.approx(
        [a[0], a[0], (a[0] + a[1]) / 2, a[9], a[9]]
    )
    assert set(cal.anchors) == set(ANCHORS[:6])


def test_weighted_quantile_matches_split_conformal() -> None:
    e = np.arange(1.0, 20.0)  # n = 19
    w = np.ones_like(e)
    k = math.ceil((len(e) + 1) * 0.9)  # standard split conformal: the ⌈(n+1)(1−α)⌉-th order statistic
    assert conformal.weighted_upper(e, w, 0.9) == e[k - 1]
    assert conformal.weighted_lower(e, w, 0.9) == e[len(e) - k]
    assert conformal.weighted_upper(e[:3], w[:3], 0.9) == math.inf  # too few points ⇒ infinite band


def test_time_weights_half_life() -> None:
    a = pd.Series(pd.to_datetime(["2020-06-30", "2021-06-30", "2022-06-30", "2022-06-30"]))
    assert conformal.time_weights(a, 2.0).tolist() == pytest.approx([0.5, 2**-0.5, 1.0, 1.0])


def test_conformal_coverage_iid() -> None:
    """Calibrated residual bands from the nested history cover ~80% of iid test names (acceptance: [0.75, 0.85])."""
    oos = oos_history(n=2000, seed=7)
    covs = []
    for t in ANCHORS[4:]:
        cal = calibrate.fit(oos, t)
        hist = oos[oos["anchor"].isin(cal.anchors)]
        bands = conformal.fit_bands(hist.assign(resid=hist["excess"] - cal.alpha(hist["p"])))
        test = oos[oos["anchor"] == t]
        center = cal.alpha(test["p"])
        lo, hi = conformal.band(test["p"], center, bands)
        assert (lo < center).all()
        assert (hi > center).all()
        covs.append(conformal.coverage(test["excess"], lo, hi))
    assert 0.75 <= np.mean(covs) <= 0.85, covs
    assert all(0.72 <= c <= 0.88 for c in covs)


def test_conviction_rules() -> None:
    assert [base_conviction(p) for p in (0.99, 0.95, 0.93, 0.90, 0.5)] == ["High", "High", "Medium", "Medium", "Low"]
    assert conviction(0.97) == "High"
    assert conviction(0.97, c=0.5) == "Medium"
    assert conviction(0.97, risk_flag=True, regime_misaligned=True) == "Low"
    assert conviction(0.97, risk_flag=True, regime_misaligned=True, c=0.1) == "Low"  # floor
    assert conviction(0.92, xagent_consensus=True) == "High"
    assert conviction(0.97, xagent_consensus=True) == "High"  # max High
    assert conviction(0.97, financial=True) == "Medium"
    assert conviction(0.97, financial=True, xagent_consensus=True) == "Medium"  # 03 §A8 cap
    assert conviction(0.99, ood=True) == UNSCORABLE
