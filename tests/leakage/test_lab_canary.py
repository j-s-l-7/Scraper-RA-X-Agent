# ruff: noqa: E501, B007
"""Leak canary for the accuracy lab (E0 a-ii). The guards must FAIL loudly on deliberately leaky inputs, and the null
controls must give IC ~ 0 on synthetic data with a known truth. Real-data date permutation is a diagnostic only
(overlapping 12M labels and persistent features make it non-null), so the asserted nulls are synthetic."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from sklearn.linear_model import Ridge

from ideation.cv.walkforward import Fold, assert_purged, check_available, monthly_folds
from ideation.features.registry import load_registry
from ideation.lab import design as D
from ideation.lab import engine as E
from ideation.lab.stats import effective_n, ic_by_anchor, leak_alarm, mde, null_covers_zero
from ideation.lab.targets import make_target
from ideation.pit.contract import LeakageError

ANCHORS = pd.date_range("2016-01-31", "2024-12-31", freq="ME")


def synth(n_names: int = 150, signal: float = 0.0, seed: int = 0) -> pd.DataFrame:
    """Monthly panel. ``f_true`` has IC ~ signal with the 12M return; return noise is iid across anchors."""
    rng = np.random.default_rng(seed)
    rows = []
    for a in ANCHORS:
        f = rng.normal(size=n_names)
        ret = signal * f + np.sqrt(1 - signal**2) * rng.normal(size=n_names)
        rows.append(pd.DataFrame({"anchor": a, "security_id": np.arange(n_names), "f_true": f, "ret_total": ret,
                                  "industry_group": np.arange(n_names) % 5, "beta_252": rng.normal(size=n_names),
                                  "label_end": a + pd.DateOffset(years=1), "labelled": True}))  # fmt: skip
    return pd.concat(rows, ignore_index=True)


def walk(df: pd.DataFrame, feats: list[str], target: str = "rg") -> pd.DataFrame:
    y = make_target(df, target, seed=1)
    label_end = df.groupby("anchor")["label_end"].max()
    folds = monthly_folds(sorted(df["anchor"].unique()), label_end, "2020-06-01", "2024-06-30", block=12, n_train=36, embargo=1)
    out = []
    for f in folds:
        tr = df["anchor"].isin(f.train_anchors).to_numpy()
        te = df["anchor"].isin(f.test_anchors).to_numpy()
        m = Ridge(alpha=10).fit(df.loc[tr, feats], y[tr])
        o = df.loc[te, ["anchor", "security_id", "ret_total"]].copy()
        o["score"] = m.predict(df.loc[te, feats])
        out.append(o)
    return pd.concat(out)


def test_honest_signal_is_found_and_does_not_trip_the_alarm() -> None:
    ic = ic_by_anchor(walk(synth(signal=0.06), ["f_true"]))
    assert 0.02 < ic.mean() < 0.12 and not leak_alarm(ic.mean())


def test_shuffled_target_gives_null_ic() -> None:
    df = synth(signal=0.10)
    ic = ic_by_anchor(walk(df, ["f_true"], target="shuffled"))
    assert null_covers_zero(ic) and abs(ic.mean()) < 0.03


def test_date_permutation_control_is_null_on_iid_data() -> None:
    df = synth(signal=0.10)
    rng = np.random.default_rng(5)
    perm = df.copy()
    for sid, g in perm.groupby("security_id"):  # pair each name's features with its own label from another month
        perm.loc[g.index, "ret_total"] = rng.permutation(g["ret_total"].to_numpy())
    ic = ic_by_anchor(walk(perm, ["f_true"]))
    assert null_covers_zero(ic)


def test_noisy_future_return_feature_trips_the_alarm() -> None:
    df = synth(signal=0.0)
    rng = np.random.default_rng(2)
    df["f_leak"] = df["ret_total"] + 3.0 * rng.normal(size=len(df))  # a noisy copy of the future return
    ic = ic_by_anchor(walk(df, ["f_true", "f_leak"]))
    assert leak_alarm(ic.mean()), ic.mean()


def test_label_end_violating_sample_is_rejected_by_every_guard() -> None:
    meta = pd.DataFrame({"label_end": [pd.Timestamp("2020-05-29"), pd.Timestamp("2020-06-30")]})
    with pytest.raises(LeakageError):
        assert_purged(meta, pd.Timestamp("2020-06-01"))
    label_end = pd.Series(pd.DatetimeIndex(ANCHORS) + pd.DateOffset(years=1), index=pd.DatetimeIndex(ANCHORS))
    bad = Fold("bad", (pd.Timestamp("2019-12-31"), pd.Timestamp("2020-03-31")), (pd.Timestamp("2020-06-30"),), ())
    with pytest.raises(LeakageError):
        D.assert_no_overlap_leak([bad], label_end)  # their labels end in 2020-12 / 2021-03, after the test start


def test_generated_monthly_folds_never_contain_overlapping_labels() -> None:
    label_end = pd.Series(pd.DatetimeIndex(ANCHORS) + pd.DateOffset(years=1), index=pd.DatetimeIndex(ANCHORS))
    label_end = label_end.where(label_end <= pd.Timestamp("2025-12-31"))
    folds = D.monthly_design_folds(list(ANCHORS), label_end, pd.Timestamp("2024-06-30"))
    D.assert_no_overlap_leak(folds, label_end)
    for f in folds:
        gap = (f.test_anchors[0] - f.train_anchors[-1]).days
        assert gap >= 365 + 28  # 12M label purge plus the one-anchor embargo
        assert len(f.train_anchors) == D.N_TRAIN


def test_unavailable_feature_is_flagged() -> None:
    avail = pd.DataFrame({"ok": pd.to_datetime(["2020-01-01"] * 3), "late": pd.to_datetime(["2020-01-01", "2020-09-01", "2020-01-01"])})
    anchors = pd.Series(pd.to_datetime(["2020-06-30"] * 3))
    with pytest.raises(LeakageError):
        check_available(avail, anchors)
    assert check_available(avail, anchors, mode="drop") == ["late"]


def test_future_window_target_in_features_is_caught_by_poisoned_label_end() -> None:
    """Training on a sample whose label ends after T0 changes the fit: the engine's row guard must raise."""
    df = synth(signal=0.05)
    df.loc[df["anchor"] == pd.Timestamp("2019-12-31"), "label_end"] = pd.Timestamp("2021-01-31")
    label_end = df.groupby("anchor")["label_end"].max()
    folds = D.monthly_design_folds(sorted(df["anchor"].unique()), label_end, pd.Timestamp("2021-06-30"))
    for f in folds:  # the purge drops the poisoned anchor from the fold that would have used it
        assert pd.Timestamp("2019-12-31") not in f.train_anchors or f.test_anchors[0] >= pd.Timestamp("2021-01-31")


def test_uniqueness_weights_and_scaling() -> None:
    ends = pd.Series(pd.DatetimeIndex(ANCHORS) + pd.DateOffset(years=1), index=pd.DatetimeIndex(ANCHORS))
    train = list(ANCHORS[:36])
    w = D.uniqueness_weights(train, ends)
    assert w.iloc[20] == pytest.approx(1 / 12, rel=0.1) and w.iloc[0] > w.iloc[20] * 0.99
    meta = pd.Series(np.repeat(train, 10))
    rw = D.row_weights(meta, train, ends)
    assert rw.sum() == pytest.approx(len(rw) / D.OVERSAMPLE)
    assert D.scale_params("rf", {"min_samples_leaf": 25}, 12)["min_samples_leaf"] == 300
    assert D.scale_params("ridge", {"alpha": 10.0}, 12) == {"alpha": 10.0}


def test_effective_n_and_mde() -> None:
    rng = np.random.default_rng(0)
    iid = pd.Series(rng.normal(size=120))
    ar = pd.Series(np.zeros(120))
    for i in range(1, 120):
        ar.iloc[i] = 0.9 * ar.iloc[i - 1] + rng.normal()
    assert effective_n(ar) < 0.5 * effective_n(iid)
    assert mde(0.12, 5) > mde(0.12, 20)


def test_binary_breakouts_stay_raw_and_sample_weights_default_is_bit_identical() -> None:
    reg = load_registry()
    from ideation.lab.panel import LabPanel

    rng = np.random.default_rng(0)
    rows = []
    for a in ANCHORS[:3]:
        n = 60
        rows.append(pd.DataFrame({"anchor": a, "security_id": np.arange(n), "layout": "core", "sector": "x", "industry_group": "g",
                                  "mom_12_1": rng.normal(size=n), "brk_1y": rng.integers(0, 2, n).astype(float)}))  # fmt: skip
    fr = pd.concat(rows, ignore_index=True)
    panel = LabPanel(fr, list(ANCHORS[:3]), pd.Series(dtype="datetime64[ns]"), reg, "t")
    x = E.build_master(panel, ["mom_12_1", "brk_1y"])
    assert set(np.unique(x["brk_1y"])) <= {0.0, 1.0}
    assert abs(x["mom_12_1"].mean()) < 0.2
    X = pd.DataFrame(rng.normal(size=(200, 4)))
    y = rng.normal(size=200)
    from ideation.models.zoo import make_learner

    a = make_learner("ridge").fit(X, y, pd.DataFrame(index=X.index)).predict(X, pd.DataFrame(index=X.index))
    b = make_learner("ridge").fit(X, y, pd.DataFrame({"sample_weight": np.ones(200)})).predict(X, pd.DataFrame(index=X.index))
    assert np.allclose(a, b)
