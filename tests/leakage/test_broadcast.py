"""Broadcast (anchor-constant) columns: they bypass the per-cross-section transform and are standardised on the fold's
training anchors only (rank.transform, models.zoo.BroadcastScaler). Regime inputs of #33/#50 and tactical #11–13."""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
import pytest

from ideation.models import zoo
from ideation.models.zoo import BroadcastScaler, SignConstrainedRidge, make_learner
from ideation.rank.transform import (
    ConstantCrossSectionWarning,
    TransformSpec,
    columns_of_kind,
    fit_broadcast_scaler,
    xs_transform,
)
from tests.leakage.synthetic import anchors_of, block_fold, make_panel, oos_ic, run_pipeline

BROADCAST = TransformSpec(broadcast_cols=frozenset({"regime"}))


def regime_panel() -> tuple[pd.DataFrame, list[str]]:
    """ret = 0.35·sign(regime_A)·f0 + noise: f0 pays only in the regime's direction; signs alternate by anchor."""
    raw, feats = make_panel(24, 300, 4, spearman=0.0, seed=21)
    rng = np.random.default_rng(5)
    regime = {a: (1 if i % 2 == 0 else -1) * (0.2 + abs(rng.normal())) for i, a in enumerate(anchors_of(raw))}
    raw["regime"] = raw["anchor"].map(regime)
    raw["ret"] = 0.35 * np.sign(raw["regime"]) * raw["f0"] + np.sqrt(1 - 0.35**2) * rng.normal(size=len(raw))
    return raw, [*feats, "regime"]


def test_regime_interaction_needs_the_broadcast_column() -> None:
    raw, cols = regime_panel()
    folds = block_fold(raw, n_train=12)
    new = oos_ic(run_pipeline(raw, cols, folds, lambda: make_learner("rf", broadcast=["regime"]), BROADCAST))
    assert new.mean() > 0.2
    assert new.min() > 0.1  # right sign in both regimes
    with pytest.warns(ConstantCrossSectionWarning):
        old = oos_ic(run_pipeline(raw, cols, folds, lambda: make_learner("rf"), TransformSpec()))
    assert abs(old.mean()) < 0.05  # the old zeroing path loses the regime and with it the signal


def test_t18_broadcast_scaler_sees_only_training_anchors(monkeypatch: pytest.MonkeyPatch) -> None:
    raw, cols = regime_panel()
    raw = raw[~((raw["anchor"] == anchors_of(raw)[0]) & (raw["security_id"] >= 100))]  # anchors of unequal size
    folds = block_fold(raw, n_train=12)
    test = raw["anchor"].isin(folds[0].test_anchors)
    raw.loc[test, "regime"] = raw.loc[test, "regime"] * 1e6  # poison: test anchors must not move the statistics
    seen: list[set[pd.Timestamp]] = []
    fitted: list[BroadcastScaler] = []

    def recording(X: pd.DataFrame, anchors: np.ndarray, c: list[str]) -> tuple[pd.Series, pd.Series]:
        seen.append(set(pd.to_datetime(anchors)))
        return fit_broadcast_scaler(X, anchors, c)

    monkeypatch.setattr(zoo, "fit_broadcast_scaler", recording)

    def make() -> BroadcastScaler:
        fitted.append(make_learner("ridge", broadcast=["regime"]))
        return fitted[-1]

    run_pipeline(raw, cols, folds, make, BROADCAST)
    assert seen == [set(folds[0].train_anchors)]
    per_anchor = raw[raw["anchor"].isin(folds[0].train_anchors)].groupby("anchor")["regime"].first()
    assert fitted[0].mean_["regime"] == pytest.approx(per_anchor.mean())  # one value per anchor, not per row
    assert fitted[0].sd_["regime"] == pytest.approx(per_anchor.std(ddof=1))


def test_undeclared_anchor_constant_column_is_still_zeroed() -> None:
    raw, cols = regime_panel()
    with pytest.warns(ConstantCrossSectionWarning, match="^24 "):
        X = xs_transform(raw, cols, TransformSpec(kind="hybrid"))
    assert (X["regime"] == 0).all()
    assert (X["ind_regime"] == 0).all()
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # declared ⇒ no warning, value passes through, no ind_ copy
        Xb = xs_transform(raw, cols, TransformSpec(kind="hybrid", broadcast_cols=frozenset({"regime"})))
    assert Xb["regime"].tolist() == raw["regime"].tolist()
    assert "ind_regime" not in Xb.columns


def test_declared_broadcast_must_be_constant_within_anchor() -> None:
    raw, cols = regime_panel()
    with pytest.raises(ValueError, match="varies within anchor"):
        xs_transform(raw, cols, TransformSpec(broadcast_cols=frozenset({"f1"})))


def test_missing_broadcast_values_and_scaling() -> None:
    raw, cols = regime_panel()
    first = raw["anchor"] == anchors_of(raw)[0]
    raw.loc[first, "regime"] = np.nan  # e.g. the regime series starts later
    X = xs_transform(raw, cols, BROADCAST)
    assert X.loc[first, "isna_regime"].eq(1).all()
    assert X.loc[~first, "isna_regime"].eq(0).all()
    meta = raw[["anchor", "security_id", "industry_group"]]
    y = raw["ret"].to_numpy()
    m = make_learner("ridge", broadcast=["regime"]).fit(X, y, meta)
    scaled = (X["regime"] - m.mean_["regime"]) / m.sd_["regime"]
    per_anchor = scaled[~first].groupby(raw.loc[~first, "anchor"]).first()
    assert per_anchor.mean() == pytest.approx(0.0, abs=1e-12)  # train mean over anchors
    assert per_anchor.std(ddof=1) == pytest.approx(1.0)
    assert np.isfinite(m.predict(X, meta)).all()  # missing ⇒ 0 after scaling


def test_unscaled_broadcast_is_refused_and_scr_drops_it() -> None:
    raw, cols = regime_panel()
    raw.loc[raw["anchor"] == anchors_of(raw)[0], "regime"] = np.nan
    X = xs_transform(raw, cols, BROADCAST)
    meta = raw[["anchor", "security_id", "industry_group"]]
    y = raw["ret"].to_numpy()
    with pytest.raises(ValueError, match="BroadcastScaler"):
        make_learner("rf").fit(X, y, meta)
    scr = make_learner("scr", priors={"f0": 1, "f1": 1}, broadcast=["regime"]).fit(X, y, meta)
    assert isinstance(scr.base, SignConstrainedRidge)
    assert scr.base.used_ == ["f0", "f1"]  # no sign prior for the broadcast column ⇒ scr does not use it


def test_columns_of_kind() -> None:
    feats = {"roe": {"prior": 1}, "breadth_200": {"kind": "broadcast"}, "vix_in": {"kind": "broadcast"}, "ep": None}
    assert columns_of_kind(feats) == {"breadth_200", "vix_in"}
    assert columns_of_kind(feats, "cross_sectional") == {"roe", "ep"}
