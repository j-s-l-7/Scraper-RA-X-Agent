"""The learner zoo (05 §3.2): every learner with its a-priori fixed hyperparameters, no per-window tuning.

Every learner follows one protocol, ``fit(X, y, meta) -> self`` and ``predict(X, meta) -> ndarray``, where ``X`` is
the R1 matrix (value columns plus ``isna_``/``na_layout_`` flags) and ``meta`` carries ``anchor``, ``security_id``
and ``industry_group`` of the same rows. ``LEARNERS[key](params)`` builds one; :func:`learner_params` gives the fixed
parameters for a horizon (they are what the trial registry hashes).

Determinism (05 §3.1, T-19): fixed seeds; forests fit with ``fit_jobs`` threads and always predict with
``n_jobs=1``; LightGBM ``deterministic``/``force_row_wise``/4 threads/seed 0; XGBoost 4 threads/seed 0.
ConvergenceWarnings are recorded in ``learner.warnings_`` (for ``trial.config_json.warnings``), not suppressed.
"""

from __future__ import annotations

import copy
import warnings
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Literal, Self

import numpy as np
import pandas as pd
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LinearRegression, Ridge
from sklearn.preprocessing import PolynomialFeatures

from ideation.rank.percentile import percentile
from ideation.rank.transform import apply_broadcast_scaler, fit_broadcast_scaler, flag_columns, value_columns

Horizon = Literal["1Y", "tactical"]

# Spec names → library names: LightGBM feature_fraction = colsample_bytree, bagging_fraction = subsample,
# bagging_freq = subsample_freq, min_data_in_leaf = min_child_samples; XGBoost nthread = n_jobs, seed = random_state.
_LGBM_BASE = {
    "num_leaves": 15,
    "learning_rate": 0.03,
    "n_estimators": 400,
    "colsample_bytree": 0.7,
    "subsample": 0.8,
    "subsample_freq": 1,
    "min_child_samples": 40,
    "deterministic": True,
    "force_row_wise": True,
    "n_jobs": 4,
    "random_state": 0,
    "verbose": -1,
}
_XGB_BASE = {
    "max_depth": 4,
    "learning_rate": 0.03,
    "n_estimators": 400,
    "subsample": 0.8,
    "colsample_bytree": 0.7,
    "min_child_weight": 20,
    "tree_method": "hist",
    "n_jobs": 4,
    "random_state": 0,
}
_MLP_BASE = {
    "hidden_layer_sizes": (32, 16),
    "alpha": 1e-3,
    "learning_rate_init": 1e-3,
    "batch_size": 256,
    "max_iter": 50,
    "early_stopping": False,
    "seeds": (0, 1, 2, 3, 4),
}
_SVR_BASE = {"kernel": "rbf", "C": 1.0, "epsilon": 0.1, "gamma": "scale", "max_rows": 6000, "seed": 0}

FIXED_PARAMS: dict[str, dict[Horizon, dict[str, Any]]] = {
    "ols": {"1Y": {}, "tactical": {}},
    "ridge": {"1Y": {"alpha": 10.0}, "tactical": {"alpha": 100.0}},
    "scr": {"1Y": {"alpha": 10.0}, "tactical": {"alpha": 100.0}},
    "pridge": {"1Y": {"alpha": 100.0, "degree": 2}, "tactical": {"alpha": 1000.0, "degree": 2}},
    "rf": {
        "1Y": {"n_estimators": 500, "min_samples_leaf": 25, "max_features": 0.33, "random_state": 0},
        "tactical": {"n_estimators": 400, "min_samples_leaf": 100, "max_features": 0.33, "random_state": 0},
    },
    "lgbm": {"1Y": _LGBM_BASE, "tactical": {**_LGBM_BASE, "min_child_samples": 200}},
    "xgb": {"1Y": _XGB_BASE, "tactical": {**_XGB_BASE, "min_child_weight": 100}},
    "mlp": {"1Y": _MLP_BASE, "tactical": {**_MLP_BASE, "max_iter": 30}},
    "svr": {"1Y": _SVR_BASE, "tactical": {**_SVR_BASE, "max_rows": 8000}},
}


def learner_params(key: str, horizon: Horizon = "1Y") -> dict[str, Any]:
    """A deep copy of the fixed parameters of ``key`` for ``horizon``."""
    return copy.deepcopy(FIXED_PARAMS[key][horizon])


class Learner:
    """Base: remembers the fit columns, records ConvergenceWarnings, refuses missing values.

    R1 fills every cross-sectional column, so a NaN in X can only be an unscaled broadcast column: wrap the learner in
    :class:`BroadcastScaler` (``make_learner(..., broadcast=cols)``). Learners that handle NaN set ``accepts_nan``.
    """

    columns_: list[str]
    warnings_: list[str]
    accepts_nan = False

    def _fit(self, X: pd.DataFrame, y: np.ndarray, meta: pd.DataFrame) -> None:
        raise NotImplementedError

    def _predict(self, X: pd.DataFrame, meta: pd.DataFrame) -> np.ndarray:
        raise NotImplementedError

    def fit(self, X: pd.DataFrame, y: np.ndarray, meta: pd.DataFrame) -> Self:
        if not self.accepts_nan and X.isna().to_numpy().any():
            bad = X.columns[X.isna().any()].tolist()
            raise ValueError(f"missing values in {bad}: broadcast columns must go through BroadcastScaler")
        self.columns_ = list(X.columns)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", ConvergenceWarning)
            self._fit(X, np.asarray(y, dtype=float), meta)
        self.warnings_ = sorted({str(w.message) for w in caught if issubclass(w.category, ConvergenceWarning)})
        return self

    def predict(self, X: pd.DataFrame, meta: pd.DataFrame) -> np.ndarray:
        return np.asarray(self._predict(X[self.columns_], meta), dtype=float)


class Sklearn(Learner):
    """Any scikit-learn-style regressor fitted on the whole matrix (OLS, Ridge, LightGBM, XGBoost)."""

    def __init__(self, factory: Callable[[], Any]) -> None:
        self.factory = factory

    def _fit(self, X: pd.DataFrame, y: np.ndarray, meta: pd.DataFrame) -> None:
        self.est_ = self.factory().fit(X.to_numpy(dtype=float), y)

    def _predict(self, X: pd.DataFrame, meta: pd.DataFrame) -> np.ndarray:
        return self.est_.predict(X.to_numpy(dtype=float))


class Forest(Learner):
    """Random forest: fit with ``fit_jobs`` threads, predict with ``n_jobs=1`` (bit-reproducible)."""

    def __init__(self, params: Mapping[str, Any], fit_jobs: int = 4) -> None:
        self.params = dict(params)
        self.fit_jobs = fit_jobs

    def _fit(self, X: pd.DataFrame, y: np.ndarray, meta: pd.DataFrame) -> None:
        from sklearn.ensemble import RandomForestRegressor

        self.est_ = RandomForestRegressor(**self.params, n_jobs=self.fit_jobs).fit(X.to_numpy(dtype=float), y)

    def _predict(self, X: pd.DataFrame, meta: pd.DataFrame) -> np.ndarray:
        self.est_.set_params(n_jobs=1)
        return self.est_.predict(X.to_numpy(dtype=float))


class SignConstrainedRidge(Learner):
    """``scr`` (dossier §3.1): features × prior sign, no-prior features and flags dropped, Ridge(positive=True).

    After the flip every coefficient is ≥ 0, i.e. each raw coefficient has its prior's sign (or is 0).
    """

    def __init__(self, alpha: float, priors: Mapping[str, int], cols: Sequence[str] | None = None) -> None:
        self.alpha = alpha
        self.priors = dict(priors)
        self.cols = list(cols) if cols is not None else None

    def _use(self, columns: Sequence[str]) -> list[str]:
        pool = value_columns(columns) if self.cols is None else [c for c in self.cols if c in columns]
        return [c for c in pool if self.priors.get(c) in (1, -1)]

    def _fit(self, X: pd.DataFrame, y: np.ndarray, meta: pd.DataFrame) -> None:
        self.used_ = self._use(X.columns)
        self.signs_ = np.array([self.priors[c] for c in self.used_], dtype=float)
        if not self.used_:
            self.est_ = None
            return
        self.est_ = Ridge(alpha=self.alpha, positive=True).fit(X[self.used_].to_numpy(dtype=float) * self.signs_, y)

    @property
    def coef_(self) -> pd.Series:
        """Coefficients on the sign-flipped features (all ≥ 0)."""
        return pd.Series(self.est_.coef_ if self.est_ is not None else [], index=self.used_, dtype=float)

    def _predict(self, X: pd.DataFrame, meta: pd.DataFrame) -> np.ndarray:
        if self.est_ is None:
            return np.full(len(X), np.nan)
        return self.est_.predict(X[self.used_].to_numpy(dtype=float) * self.signs_)


class PolyRidge(Learner):
    """``pridge``: degree-2 polynomial (squares + interactions) of the value columns, flags appended linearly."""

    def __init__(self, alpha: float, degree: int = 2) -> None:
        self.alpha = alpha
        self.degree = degree

    def _design(self, X: pd.DataFrame) -> np.ndarray:
        poly = self.poly_.transform(X[self.values_].to_numpy(dtype=float))
        return np.hstack([poly, X[self.flags_].to_numpy(dtype=float)])

    def _fit(self, X: pd.DataFrame, y: np.ndarray, meta: pd.DataFrame) -> None:
        self.values_, self.flags_ = value_columns(X.columns), flag_columns(X.columns)
        self.poly_ = PolynomialFeatures(self.degree, include_bias=False).fit(X[self.values_].to_numpy(dtype=float))
        self.est_ = Ridge(alpha=self.alpha).fit(self._design(X), y)

    def _predict(self, X: pd.DataFrame, meta: pd.DataFrame) -> np.ndarray:
        return self.est_.predict(self._design(X))


class MLPEnsemble(Learner):
    """``mlp``: mean of one MLPRegressor per seed. ``early_stopping`` is banned: sklearn carves its validation set
    at random from the training rows, which mixes anchors across time (05 §3.2)."""

    def __init__(self, params: Mapping[str, Any]) -> None:
        p = dict(params)
        if p.get("early_stopping", False):
            raise ValueError("MLP early_stopping is banned: its random validation split leaks across time")
        self.seeds = tuple(p.pop("seeds"))
        p["early_stopping"] = False
        self.params = p

    def _fit(self, X: pd.DataFrame, y: np.ndarray, meta: pd.DataFrame) -> None:
        from sklearn.neural_network import MLPRegressor

        xm = X.to_numpy(dtype=float)
        self.ests_ = [MLPRegressor(**self.params, random_state=s).fit(xm, y) for s in self.seeds]

    def _predict(self, X: pd.DataFrame, meta: pd.DataFrame) -> np.ndarray:
        xm = X.to_numpy(dtype=float)
        return np.mean([e.predict(xm) for e in self.ests_], axis=0)


def stratified_subsample(anchors: np.ndarray, max_rows: int, seed: int) -> np.ndarray:
    """Row positions: all rows if ≤ ``max_rows``, else a seeded sample with each anchor's share kept (largest
    remainder rounding), sorted."""
    n = len(anchors)
    if n <= max_rows:
        return np.arange(n)
    codes = np.unique(anchors, return_inverse=True)[1]
    counts = np.bincount(codes)
    quota = counts * max_rows / n
    take = np.floor(quota).astype(int)
    rest = max_rows - take.sum()
    take[np.argsort(-(quota - take), kind="stable")[:rest]] += 1
    rng = np.random.default_rng(seed)
    picked = [rng.choice(np.flatnonzero(codes == g), size=k, replace=False) for g, k in enumerate(take) if k]
    return np.sort(np.concatenate(picked))


def rbf_decision(X: np.ndarray, sv: np.ndarray, coef: np.ndarray, gamma: float, intercept: float) -> np.ndarray:
    """``Σ_i coef_i · exp(−γ‖x − sv_i‖²) + b`` with a fixed operation order (elementwise ops and explicit
    accumulation only, no BLAS or reductions), so it is bit-reproducible. libsvm's own predict is not: it differs by
    a few ulp between identical calls (alignment-dependent summation), which breaks T-19's score sha256."""
    out = np.empty(len(X))
    chunk = 256
    for start in range(0, len(X), chunk):
        xb = X[start : start + chunk]
        d2 = np.zeros((len(xb), len(sv)))
        for f in range(sv.shape[1]):
            diff = xb[:, f : f + 1] - sv[:, f][None, :]
            d2 += diff * diff
        k = np.exp(-gamma * d2)
        acc = np.zeros(len(xb))
        for i in range(len(sv)):
            acc += coef[i] * k[:, i]
        out[start : start + chunk] = acc + intercept
    return out


class SubsampledSVR(Learner):
    """``svr`` on a seeded subsample stratified by anchor (≤ ``max_rows``; all rows if fewer). The fitted libsvm
    model is deterministic; prediction uses :func:`rbf_decision` (same function, bit-reproducible)."""

    def __init__(self, params: Mapping[str, Any]) -> None:
        p = dict(params)
        self.max_rows = int(p.pop("max_rows"))
        self.seed = int(p.pop("seed"))
        self.params = p

    def _fit(self, X: pd.DataFrame, y: np.ndarray, meta: pd.DataFrame) -> None:
        from sklearn.svm import SVR

        rows = stratified_subsample(pd.to_datetime(meta["anchor"]).to_numpy(), self.max_rows, self.seed)
        self.n_rows_ = len(rows)
        self.est_ = SVR(**self.params).fit(X.to_numpy(dtype=float)[rows], y[rows])
        if self.est_.kernel != "rbf":
            raise ValueError("SubsampledSVR predicts with the RBF decision function only")

    def _predict(self, X: pd.DataFrame, meta: pd.DataFrame) -> np.ndarray:
        e = self.est_
        sv = np.ascontiguousarray(e.support_vectors_, dtype=float)
        return rbf_decision(X.to_numpy(dtype=float), sv, e.dual_coef_.ravel(), float(e._gamma), float(e.intercept_[0]))


def _lgbm(params: dict[str, Any]) -> Learner:
    from lightgbm import LGBMRegressor

    return Sklearn(lambda: LGBMRegressor(**params))


def _xgb(params: dict[str, Any]) -> Learner:
    from xgboost import XGBRegressor

    return Sklearn(lambda: XGBRegressor(**params))


LEARNERS: dict[str, Callable[[dict[str, Any]], Learner]] = {
    "ols": lambda p: Sklearn(LinearRegression),
    "ridge": lambda p: Sklearn(lambda: Ridge(alpha=p["alpha"])),
    "scr": lambda p: SignConstrainedRidge(p["alpha"], p.get("priors", {}), p.get("cols")),
    "pridge": lambda p: PolyRidge(p["alpha"], p.get("degree", 2)),
    "rf": lambda p: Forest({k: v for k, v in p.items() if k != "fit_jobs"}, p.get("fit_jobs", 4)),
    "lgbm": _lgbm,
    "xgb": _xgb,
    "mlp": lambda p: MLPEnsemble(p),
    "svr": lambda p: SubsampledSVR(p),
}


def make_learner(key: str, horizon: Horizon = "1Y", *, broadcast: Sequence[str] = (), **extra: Any) -> Learner:
    """``LEARNERS[key]`` with the fixed parameters of ``horizon`` plus ``extra`` (e.g. ``priors`` for scr,
    ``fit_jobs`` for rf). Extras never override a fixed hyperparameter. ``broadcast`` columns get a
    :class:`BroadcastScaler` around the learner."""
    params = learner_params(key, horizon)
    clash = set(extra) & set(params)
    if clash:
        raise ValueError(f"fixed hyperparameters cannot be overridden: {sorted(clash)}")
    learner = LEARNERS[key]({**params, **extra})
    return BroadcastScaler(learner, broadcast) if broadcast else learner


class BroadcastScaler(Learner):
    """Train-only standardisation of broadcast (anchor-constant) columns, then the wrapped learner.

    Mean and SD come from the fold's training anchors, one value per anchor (``rank.transform.fit_broadcast_scaler``),
    and are applied unchanged to the test anchors; missing values become 0. Trees and linear learners then use the
    column like any other (pridge also forms its squares and interactions with features); ``scr`` drops it, because
    it has no sign prior.
    """

    accepts_nan = True

    def __init__(self, base: Learner, cols: Sequence[str]) -> None:
        self.base = base
        self.cols = list(cols)

    def _fit(self, X: pd.DataFrame, y: np.ndarray, meta: pd.DataFrame) -> None:
        self.mean_, self.sd_ = fit_broadcast_scaler(X, pd.to_datetime(meta["anchor"]).to_numpy(), self.cols)
        self.base.fit(apply_broadcast_scaler(X, self.mean_, self.sd_), y, meta)

    def _predict(self, X: pd.DataFrame, meta: pd.DataFrame) -> np.ndarray:
        return self.base.predict(apply_broadcast_scaler(X, self.mean_, self.sd_), meta)


# ── 90/10 reading (a): per-security blend (05 §11) ───────────────────────────────────────────────────────────
class NinetyTenSec(Learner):
    """``final = 0.9·rank(pred) + 0.1·rank(−σ)`` within each scored anchor.

    σ_i = mean over the core features of security i's std (ddof=1) of its cross-sectional rank-gauss value across
    the training anchors of the fold (needs ≥ 2 anchors; otherwise its stability rank is 0.5). Only training rows
    are used, so σ never sees the test anchor.
    """

    def __init__(self, base: Learner, core_cols: Sequence[str] | None = None) -> None:
        self.base = base
        self.core_cols = list(core_cols) if core_cols is not None else None

    def _fit(self, X: pd.DataFrame, y: np.ndarray, meta: pd.DataFrame) -> None:
        cols = self.core_cols or [c for c in value_columns(X.columns) if not c.startswith("ind_")]
        self.base.fit(X, y, meta)
        frame = X[cols].assign(_sid=meta["security_id"].to_numpy())
        g = frame.groupby("_sid")
        sd = g[cols].std(ddof=1).mean(axis=1)
        self.sigma_ = sd[g.size() >= 2]

    def _predict(self, X: pd.DataFrame, meta: pd.DataFrame) -> np.ndarray:
        pred = pd.Series(self.base.predict(X, meta), index=X.index)
        anchors = pd.to_datetime(meta["anchor"]).to_numpy()
        sigma = pd.Series(meta["security_id"].map(self.sigma_).to_numpy(dtype=float), index=X.index)
        r_pred = percentile(pred, by=anchors)
        r_stab = percentile(-sigma, by=anchors).fillna(0.5)
        return (0.9 * r_pred + 0.1 * r_stab).to_numpy()
