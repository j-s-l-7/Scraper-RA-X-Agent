"""MS2 diagnostics on synthetic data: a planted beta-only signal dies under neutralisation, a planted alpha survives."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ideation.tactical import diagnostics as dg

pytestmark = pytest.mark.unit


def _frame(kind: str, n_anchors: int = 36, n: int = 400, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for a in pd.date_range("2014-01-31", periods=n_anchors, freq="ME"):
        beta = rng.lognormal(0, 0.4, n)
        alpha = rng.normal(0, 0.05, n)
        mkt = -0.08 + rng.normal(0, 0.02)  # a persistent drawdown regime: beta dominates the cross-section
        noise = rng.normal(0, 0.05, n)
        ret = beta * mkt + noise + (alpha if kind == "alpha" else 0.0)
        score = alpha if kind == "alpha" else -beta
        rows.append(pd.DataFrame({
            "anchor": a, "security_id": np.arange(n), "score": score, "ret_3m": ret, "beta_252": beta,
            "rvol_63": rng.uniform(0.2, 0.6, n), "idio_vol": rng.uniform(0.2, 0.6, n),
            "mcap_pit_cr": rng.lognormal(7, 1, n), "adv63_lakh": rng.lognormal(6, 1, n),
        }))  # fmt: skip
    return pd.concat(rows, ignore_index=True)


def test_beta_only_signal_is_killed_by_neutralisation():
    t = dg.neutral_ic_table(_frame("beta"))
    raw, res = dg.summ(t["raw"])["mean"], dg.summ(t["res_B"])["mean"]
    assert raw > 0.4
    assert abs(res) < 0.05
    assert abs(dg.summ(t["res_BVLS"])["mean"]) < 0.05
    assert abs(dg.summ(t["sres_BVLS"])["mean"]) < 0.05  # the score residualised on beta is pure noise
    assert dg.summ(t["wb_avg"])["mean"] < 0.8 * raw  # a tercile only partly removes a continuous beta exposure


def test_real_signal_survives_neutralisation():
    t = dg.neutral_ic_table(_frame("alpha"))
    raw = dg.summ(t["raw"])["mean"]
    assert raw > 0.3
    for k in ("res_B", "res_BV", "res_BVLS", "sres_BVLS", "wb_avg", "wv_avg"):
        assert dg.summ(t[k])["mean"] > 0.8 * raw, k


def test_kth_session_and_buckets():
    good = np.array(pd.date_range("2020-01-01", periods=30, freq="B").values, dtype="datetime64[D]")
    assert dg.kth_session_after(good, pd.Timestamp("2020-01-03"), 1) == pd.Timestamp("2020-01-06").date()
    assert dg.kth_session_after(good, pd.Timestamp("2020-01-03"), 5) == pd.Timestamp("2020-01-10").date()
    d = dg.add_controls(_frame("alpha", n_anchors=6))
    b = dg.bucket_table(d, "ln_adv")
    assert list(b.index) == ["T1", "T2", "T3", "T3-T1"]
    assert b["n"].min() == 6
