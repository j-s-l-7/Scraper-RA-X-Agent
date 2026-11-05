"""05 §3.2–3.4 zoo, selection and Phase-I family: fixed parameters, scr signs, MLP early-stopping ban, VIF, SVR
subsample, composites, industry equations, WFO selection and the 90/10 per-security blend."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ideation.models.phase1 import (
    DOSSIER_COLUMNS,
    IndustryEquations,
    Unified,
    WFOSelectedSCR,
    composite_dossier,
    composite_ew,
    final_wfo,
    industry_equations_in_sample,
    unified_xs,
)
from ideation.models.select_vif import best_per_family, clustered_ols, iterative_vif, vif, wfo_select
from ideation.models.zoo import (
    FIXED_PARAMS,
    LEARNERS,
    MLPEnsemble,
    NinetyTenSec,
    PolyRidge,
    SignConstrainedRidge,
    learner_params,
    make_learner,
    stratified_subsample,
)


def panel(n_anchor: int = 4, n: int = 120, seed: int = 0, k: int = 5) -> tuple[pd.DataFrame, np.ndarray, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    anchors = pd.to_datetime([f"{2016 + a}-06-30" for a in range(n_anchor)])
    meta = pd.DataFrame(
        {
            "anchor": np.repeat(anchors, n),
            "security_id": np.tile(np.arange(n), n_anchor),
            "industry_group": np.tile(np.arange(n) % 3, n_anchor),
        }
    )
    X = pd.DataFrame(rng.normal(size=(len(meta), k)), columns=[f"f{i}" for i in range(k)])
    X["isna_f0"] = (rng.random(len(meta)) < 0.1).astype(float)
    y = 0.4 * X["f0"].to_numpy() - 0.3 * X["f1"].to_numpy() + rng.normal(size=len(meta))
    return X, y, meta


def test_fixed_parameters_match_05_s32() -> None:
    assert set(LEARNERS) == {"ols", "ridge", "scr", "pridge", "rf", "lgbm", "xgb", "mlp", "svr"}
    assert learner_params("ridge") == {"alpha": 10.0}
    assert learner_params("ridge", "tactical") == {"alpha": 100.0}
    assert learner_params("scr")["alpha"] == 10.0
    assert learner_params("pridge")["alpha"] == 100.0
    assert learner_params("pridge", "tactical")["alpha"] == 1000.0
    rf, rf_t = learner_params("rf"), learner_params("rf", "tactical")
    assert (rf["n_estimators"], rf["min_samples_leaf"], rf["max_features"], rf["random_state"]) == (500, 25, 0.33, 0)
    assert (rf_t["n_estimators"], rf_t["min_samples_leaf"]) == (400, 100)
    lg = learner_params("lgbm")
    assert (lg["num_leaves"], lg["learning_rate"], lg["n_estimators"], lg["colsample_bytree"]) == (15, 0.03, 400, 0.7)
    assert (lg["subsample"], lg["subsample_freq"], lg["min_child_samples"]) == (0.8, 1, 40)
    assert (lg["deterministic"], lg["force_row_wise"], lg["n_jobs"], lg["random_state"]) == (True, True, 4, 0)
    assert learner_params("lgbm", "tactical")["min_child_samples"] == 200
    xg = learner_params("xgb")
    assert (xg["max_depth"], xg["learning_rate"], xg["n_estimators"], xg["subsample"]) == (4, 0.03, 400, 0.8)
    assert (xg["colsample_bytree"], xg["min_child_weight"], xg["tree_method"]) == (0.7, 20, "hist")
    assert (xg["n_jobs"], xg["random_state"]) == (4, 0)
    assert learner_params("xgb", "tactical")["min_child_weight"] == 100
    mlp = learner_params("mlp")
    assert mlp["hidden_layer_sizes"] == (32, 16)
    assert (mlp["alpha"], mlp["learning_rate_init"], mlp["batch_size"], mlp["max_iter"]) == (1e-3, 1e-3, 256, 50)
    assert mlp["early_stopping"] is False
    assert mlp["seeds"] == (0, 1, 2, 3, 4)
    assert learner_params("mlp", "tactical")["max_iter"] == 30
    sv = learner_params("svr")
    assert (sv["kernel"], sv["C"], sv["epsilon"], sv["gamma"], sv["max_rows"]) == ("rbf", 1.0, 0.1, "scale", 6000)
    assert learner_params("svr", "tactical")["max_rows"] == 8000
    learner_params("rf")["n_estimators"] = 1  # a copy: the table itself is untouched
    assert FIXED_PARAMS["rf"]["1Y"]["n_estimators"] == 500


def test_fixed_parameters_cannot_be_overridden() -> None:
    with pytest.raises(ValueError, match="cannot be overridden"):
        make_learner("rf", n_estimators=10)
    with pytest.raises(ValueError, match="cannot be overridden"):
        make_learner("mlp", early_stopping=True)


def test_mlp_early_stopping_banned() -> None:
    p = learner_params("mlp")
    with pytest.raises(ValueError, match="banned"):
        MLPEnsemble({**p, "early_stopping": True})
    X, y, meta = panel(n_anchor=2, n=150)
    m = make_learner("mlp", "tactical").fit(X, y, meta)
    assert len(m.ests_) == 5
    assert all(e.early_stopping is False for e in m.ests_)
    assert [e.random_state for e in m.ests_] == [0, 1, 2, 3, 4]
    assert all(e.max_iter == 30 for e in m.ests_)
    assert any("Maximum iterations" in w for w in m.warnings_)  # recorded, not suppressed


def test_every_learner_fits_and_predicts() -> None:
    X, y, meta = panel(n_anchor=2, n=150)
    for key in LEARNERS:
        extra = {"priors": {"f0": 1, "f1": -1}} if key == "scr" else {}
        pred = make_learner(key, **extra).fit(X, y, meta).predict(X, meta)
        assert pred.shape == (len(X),)
        assert np.isfinite(pred).all(), key


def test_scr_coefficients_nonnegative_after_flip() -> None:
    X, y, meta = panel()
    priors = {"f0": 1, "f1": -1, "f2": 1, "f3": -1}  # f4 has no prior; isna flags are dropped
    m = SignConstrainedRidge(10.0, priors).fit(X, y, meta)
    assert m.used_ == ["f0", "f1", "f2", "f3"]
    assert (m.coef_ >= 0).all()
    assert m.coef_["f0"] > 0.1
    assert m.coef_["f1"] > 0.1  # true raw coefficient −0.3 with prior − ⇒ flipped coefficient > 0
    wrong = SignConstrainedRidge(10.0, {"f0": -1, "f1": 1}).fit(X, y, meta)
    assert (wrong.coef_ >= 0).all()
    assert wrong.coef_.max() == pytest.approx(0.0, abs=1e-9)  # both priors contradict the data ⇒ 0
    empty = SignConstrainedRidge(10.0, {}).fit(X, y, meta)
    assert np.isnan(empty.predict(X, meta)).all()


def test_pridge_design() -> None:
    X, y, meta = panel(k=4)
    m = PolyRidge(100.0).fit(X, y, meta)
    assert m.values_ == ["f0", "f1", "f2", "f3"]
    assert m.flags_ == ["isna_f0"]
    assert m._design(X).shape[1] == 4 + 10 + 1  # linear + squares/interactions + flag


def test_svr_subsample_stratified_and_seeded() -> None:
    anchors = np.repeat(pd.to_datetime(["2019-06-28", "2020-06-30", "2021-06-30"]), [3000, 2000, 1000])
    rows = stratified_subsample(anchors, 1200, 0)
    assert len(rows) == 1200
    assert len(np.unique(rows)) == 1200
    counts = pd.Series(anchors[rows]).value_counts().sort_index().tolist()
    assert counts == [600, 400, 200]
    assert (rows == stratified_subsample(anchors, 1200, 0)).all()
    assert (stratified_subsample(anchors[:500], 1200, 0) == np.arange(500)).all()
    X, y, meta = panel(n_anchor=3, n=2500)
    svr = make_learner("svr").fit(X, y, meta)
    assert svr.n_rows_ == 6000


def test_vif_drops_planted_collinear_pair() -> None:
    rng = np.random.default_rng(0)
    a = rng.normal(size=2000)
    X = pd.DataFrame(
        {"a": a, "a_twin": a + 0.05 * rng.normal(size=2000), "b": rng.normal(size=2000), "c": rng.normal(size=2000)}
    )
    v = vif(X)
    assert v["a"] > 100
    assert v["b"] < 1.1
    kept, dropped = iterative_vif(X, 5.0)
    assert len(dropped) == 1
    assert dropped[0] in {"a", "a_twin"}
    assert {"b", "c"} <= set(kept)
    assert vif(X[kept]).max() < 5
    X["exact"] = X["b"] + X["c"]
    assert np.isinf(vif(X)["exact"])


def test_clustered_ols_and_family_choice() -> None:
    rng = np.random.default_rng(1)
    x = rng.normal(size=900)
    clusters = np.repeat([0, 1, 2], 300)
    beta, se, _t, p = clustered_ols(x, 0.5 * x + rng.normal(size=900), clusters)
    assert beta == pytest.approx(0.5, abs=0.1)
    assert se > 0
    assert p < 0.05
    *_, p_null = clustered_ols(x, rng.normal(size=900), clusters)
    assert p_null > 0.05
    t_stats = pd.Series({"roe": 2.0, "roe_3y": -3.5, "roe_norm": 1.0, "ep": 2.5})
    assert best_per_family(t_stats, {"roe": "roe", "roe_3y": "roe", "roe_norm": "roe"}) == ["roe_3y", "ep"]


def test_wfo_select_and_scr() -> None:
    X, y, meta = panel(n_anchor=3, n=400, k=5)
    X["f0_alt"] = X["f0"] + 0.3 * np.random.default_rng(2).normal(size=len(X))
    priors = {"f0": 1, "f0_alt": 1, "f1": -1, "f2": 1, "f3": 1}
    sel = wfo_select(X, y, meta["anchor"].to_numpy(), {"f0": "f0", "f0_alt": "f0"}, candidates=list(priors))
    family = sel.screened.loc[["f0", "f0_alt"], "t"].abs()
    assert [c for c in sel.selected if c in ("f0", "f0_alt")] == [family.idxmax()]  # one per family: max |t|
    assert "f1" in sel.selected
    assert sel.passed_p == [c for c in priors if sel.screened.loc[c, "p"] < 0.05]
    m = WFOSelectedSCR(10.0, priors, {"f0": "f0", "f0_alt": "f0"}).fit(X, y, meta)
    assert not m.empty_
    assert (m.scr_.coef_ >= 0).all()
    pure_noise = np.random.default_rng(9).normal(size=len(X))
    e = WFOSelectedSCR(10.0, {"f2": 1, "f3": 1}, {}).fit(X, pure_noise, meta)
    assert e.empty_
    assert np.isnan(e.predict(X, meta)).all()


def test_composites_and_unified() -> None:
    cols = list(DOSSIER_COLUMNS.values())
    d = composite_dossier()
    assert sum(d.weights.values()) == pytest.approx(0.8)
    assert d.weights["nd_ebitda"] == pytest.approx(-0.1)
    assert d.weights["ep"] == pytest.approx(0.12)
    assert d.weights["eps_g3"] == pytest.approx(0.15)
    X = pd.DataFrame(np.eye(len(cols)), columns=cols)
    meta = pd.DataFrame({"anchor": pd.Timestamp("2020-06-30")}, index=X.index)
    assert d.fit(X, np.zeros(len(X)), meta).predict(X, meta).tolist() == pytest.approx(list(d.weights.values()))
    ew = composite_ew({"a": 1, "b": -1, "c": 0})
    assert ew.weights == {"a": 0.5, "b": -0.5}
    assert unified_xs().weights == {"ebitda_ev": 1.0, "sp": 1.0, "fcf_yield": 1.0}
    train = pd.DataFrame({"ebitda_ev": [0.1, 0.3], "sp": [1.0, 3.0], "fcf_yield": [0.0, 0.2]})
    u = Unified().fit(train, np.zeros(2), meta.iloc[:2])
    test = pd.DataFrame({"ebitda_ev": [0.2 + np.sqrt(0.02)], "sp": [2.0], "fcf_yield": [0.1]})
    assert u.predict(test, meta.iloc[:1]).tolist() == pytest.approx([1.0])  # train-fold mean/std, not the test's


def test_industry_equations() -> None:
    X, y, meta = panel(n_anchor=3, n=120, k=5)
    priors = {"f0": 1, "f1": -1, "f2": -1, "f3": 1}
    m = IndustryEquations(priors).fit(X, y, meta)
    assert set(m.equations_) == {0, 1, 2}
    for eq in m.equations_.values():
        assert len(eq) <= 4
        assert sum(abs(w) for w in eq.values()) == pytest.approx(1.0)
        assert eq["f0"] > 0
        assert eq["f1"] < 0  # prior − and negative ρ: allowed, weight ρ/Σ|ρ| < 0
        for f, w in eq.items():
            if priors.get(f) == 1:
                assert w > 0
            if priors.get(f) == -1:
                assert w < 0
    small = meta.assign(industry_group=np.where(meta["security_id"] < 5, 99, meta["industry_group"]))
    m2 = IndustryEquations(priors).fit(X, y, small)
    assert 99 not in m2.equations_
    pred = m2.predict(X, small)
    assert np.isfinite(pred).all()
    table = industry_equations_in_sample(X, y, meta, priors)
    assert set(table.columns) == {"group", "feature", "weight", "n", "in_sample_ic"}
    assert (table["in_sample_ic"] > 0).all()


def test_final_wfo_columns() -> None:
    X = pd.DataFrame(np.random.default_rng(0).normal(size=(300, 4)), columns=["ebitda_ev", "sp", "fcf_yield", "roe"])
    meta = pd.DataFrame({"anchor": pd.Timestamp("2020-06-30")}, index=X.index)
    y = X["sp"].to_numpy() + np.random.default_rng(1).normal(size=300)
    m = final_wfo(10.0, {"ebitda_ev": 1, "sp": 1, "fcf_yield": 1, "roe": 1}).fit(X, y, meta)
    assert m.used_ == ["ebitda_ev", "sp", "fcf_yield"]


def test_ninety_ten_per_security() -> None:
    X, y, meta = panel(n_anchor=3, n=50, k=3)
    stable = meta["security_id"] < 25
    X.loc[stable, ["f0", "f1", "f2"]] = 0.0  # perfectly stable features ⇒ σ = 0, the best stability rank
    base = make_learner("ridge")
    m = NinetyTenSec(base, core_cols=["f0", "f1", "f2"]).fit(X, y, meta)
    assert (m.sigma_[m.sigma_.index < 25] == 0).all()
    test_meta = pd.DataFrame({"anchor": pd.Timestamp("2019-06-28"), "security_id": [0, 30, 999]})
    test_X = pd.DataFrame(np.zeros((3, 4)), columns=["f0", "f1", "f2", "isna_f0"])
    out = m.predict(test_X, test_meta)
    # equal base scores ⇒ rank(pred) = 0.5 for all; stability ranks: id 0 (σ = 0) top, id 30 lower, id 999 unknown
    r_stab = np.array([0.75, 0.25, 0.5])
    assert out.tolist() == pytest.approx((0.9 * 0.5 + 0.1 * r_stab).tolist())
