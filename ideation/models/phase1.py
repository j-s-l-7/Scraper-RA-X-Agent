"""Phase-I linear family (05 §3.4, 06 §3 / §10 items 6–9). Same ``fit(X, y, meta)`` / ``predict(X, meta)`` protocol
as the zoo, so the same walk-forward driver, splits and names apply.

Column names default to the 05 §3.4 yield-form features; a trial can map them to the feature YAML's names.

* ``Unified`` (P1_UNIFIED, faithful): ``z(ebitda_ev) + z(sp) + z(fcf_yield)`` with z from the **training-fold**
  mean/std. It must be given the winsorized raw yield columns: on the R1 rank-gauss matrix the train-fold z is
  ≈ the identity and it collapses to ``P1_UNIFIED_XS`` (= :func:`unified_xs`, fixed equal weights on R1 columns).
* ``FixedWeights`` composites: :func:`composite_dossier` (0.3·Value + 0.3·Quality + 0.3·Growth − 0.1·Risk in yield
  form) and :func:`composite_ew` (equal-weight mean of sign-flipped columns, the gate-3 baseline).
* ``IndustryEquations`` (P1_INDUSTRY_WF): per industry_group, the ≤ 4 features with the largest |Spearman| to y that
  obey the sign rules, weights ρ_k / Σ|ρ_j|, fitted on the training rows and applied at T. The in-sample table
  (:func:`industry_equations_in_sample`) is descriptive only.
* ``WFOSelectedSCR`` (P1_WFO_3Y / P1_WFO_2Y): ``select_vif.wfo_select`` inside the fold, then ``scr``. An empty
  selection scores NaN and sets ``empty_`` (the fold is logged as "empty").
* Final_WFO / PostCOVID: :func:`final_wfo` = ``scr`` on {ebitda_ev, sp, fcf_yield}; the trial sets the window.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np
import pandas as pd

from ideation.eval.metrics import rank_ic
from ideation.models.select_vif import Selection, wfo_select
from ideation.models.zoo import Learner, SignConstrainedRidge
from ideation.rank.transform import value_columns

UNIFIED_COLS = ("ebitda_ev", "sp", "fcf_yield")
FINAL_WFO_COLS = ("ebitda_ev", "sp", "fcf_yield")
DOSSIER_COLUMNS = {
    "ep": "ep",
    "ebitda_ev": "ebitda_ev",
    "sp": "sp",
    "roic": "roic",
    "roe": "roe",
    "roa": "roa",
    "eps_g3": "eps_g3",
    "rev_g3": "rev_g3",
    "nd_ebitda": "nd_ebitda",
}


class FixedWeights(Learner):
    """``score = Σ w_f · x_f``; fitting is a no-op (no parameter is learned)."""

    def __init__(self, weights: Mapping[str, float]) -> None:
        self.weights = dict(weights)

    def _fit(self, X: pd.DataFrame, y: np.ndarray, meta: pd.DataFrame) -> None:
        missing = set(self.weights) - set(X.columns)
        if missing:
            raise KeyError(f"composite columns missing from X: {sorted(missing)}")

    def _predict(self, X: pd.DataFrame, meta: pd.DataFrame) -> np.ndarray:
        cols = list(self.weights)
        return X[cols].to_numpy(dtype=float) @ np.array([self.weights[c] for c in cols])


def composite_dossier(cols: Mapping[str, str] = DOSSIER_COLUMNS) -> FixedWeights:
    """Dossier sub-weights, exact: 0.3·(−Value) + 0.3·Quality + 0.3·Growth + 0.1·(−Risk), with Value in yield form
    (+0.4 ep + 0.3 ebitda_ev + 0.3 sp), Quality = 0.4 roic + 0.3 roe + 0.3 roa, Growth = 0.5 eps_g3 + 0.5 rev_g3,
    Risk = nd_ebitda."""
    w = {
        "ep": 0.3 * 0.4,
        "ebitda_ev": 0.3 * 0.3,
        "sp": 0.3 * 0.3,
        "roic": 0.3 * 0.4,
        "roe": 0.3 * 0.3,
        "roa": 0.3 * 0.3,
        "eps_g3": 0.3 * 0.5,
        "rev_g3": 0.3 * 0.5,
        "nd_ebitda": -0.1,
    }
    return FixedWeights({cols[k]: v for k, v in w.items()})


def composite_ew(priors: Mapping[str, int]) -> FixedWeights:
    """Equal-weight mean of sign-flipped columns over every feature with a ±1 prior (02 §5.5 gate-3 baseline)."""
    signed = {c: float(s) for c, s in priors.items() if s in (1, -1)}
    return FixedWeights({c: s / len(signed) for c, s in signed.items()})


def unified_xs(cols: Sequence[str] = UNIFIED_COLS) -> FixedWeights:
    """P1_UNIFIED_XS: per-cross-section z (the R1 columns) summed with weight 1."""
    return FixedWeights(dict.fromkeys(cols, 1.0))


class Unified(Learner):
    """P1_UNIFIED (faithful): z from the training-fold mean/std of the raw yield columns (Models_Results)."""

    accepts_nan = True  # raw winsorized columns keep their NaN; a missing z counts as 0

    def __init__(self, cols: Sequence[str] = UNIFIED_COLS) -> None:
        self.cols = list(cols)

    def _fit(self, X: pd.DataFrame, y: np.ndarray, meta: pd.DataFrame) -> None:
        self.mean_ = X[self.cols].mean()
        self.std_ = X[self.cols].std(ddof=1).replace(0.0, np.nan)

    def _predict(self, X: pd.DataFrame, meta: pd.DataFrame) -> np.ndarray:
        z = (X[self.cols] - self.mean_) / self.std_
        return z.fillna(0.0).sum(axis=1).to_numpy()


def _sign_ok(rho: float, prior: int | None) -> bool:
    if prior == 1:
        return rho > 0
    if prior == -1:
        return rho < 0
    return rho != 0 and not np.isnan(rho)


def fit_equation(X: pd.DataFrame, y: np.ndarray, priors: Mapping[str, int], k: int = 4) -> dict[str, float]:
    """Top-k |Spearman| features obeying the sign rules; weights ρ / Σ|ρ| (dossier §3.3)."""
    rho = {c: rank_ic(X[c].to_numpy(), y) for c in X.columns}
    ok = {c: r for c, r in rho.items() if _sign_ok(r, priors.get(c))}
    top = sorted(ok, key=lambda c: -abs(ok[c]))[:k]
    total = sum(abs(ok[c]) for c in top)
    return {c: ok[c] / total for c in top} if total > 0 else {}


class IndustryEquations(Learner):
    """P1_INDUSTRY_WF. Groups with < ``min_group`` names per training anchor (on average) and groups unseen at fit
    use the equation fitted on all training rows."""

    def __init__(
        self, priors: Mapping[str, int], *, k: int = 4, min_group: int = 8, group_col: str = "industry_group"
    ) -> None:
        self.priors = dict(priors)
        self.k = k
        self.min_group = min_group
        self.group_col = group_col

    def _fit(self, X: pd.DataFrame, y: np.ndarray, meta: pd.DataFrame) -> None:
        cols = value_columns(X.columns)
        self.global_ = fit_equation(X[cols], y, self.priors, self.k)
        n_anchors = max(meta["anchor"].nunique(), 1)
        self.equations_: dict[object, dict[str, float]] = {}
        groups = meta[self.group_col].to_numpy()
        for g in pd.unique(groups):
            rows = groups == g
            if rows.sum() / n_anchors >= self.min_group:
                self.equations_[g] = fit_equation(X.loc[rows, cols], y[rows], self.priors, self.k)

    def _predict(self, X: pd.DataFrame, meta: pd.DataFrame) -> np.ndarray:
        out = np.full(len(X), np.nan)
        groups = meta[self.group_col].to_numpy()
        for g in pd.unique(groups):
            rows = groups == g
            eq = self.equations_.get(g) or self.global_
            if eq:
                out[rows] = X.loc[rows, list(eq)].to_numpy(dtype=float) @ np.array(list(eq.values()))
        return out


def industry_equations_in_sample(
    X: pd.DataFrame, y: np.ndarray, meta: pd.DataFrame, priors: Mapping[str, int], *, min_group: int = 8
) -> pd.DataFrame:
    """Descriptive, in-sample (dossier §3.3): fit and score the same rows; never a counted trial."""
    model = IndustryEquations(priors, min_group=min_group).fit(X, y, meta)
    score = model.predict(X, meta)
    rows = []
    groups = meta["industry_group"].to_numpy()
    for g, eq in model.equations_.items():
        m = groups == g
        ic = rank_ic(score[m], y[m])
        rows.extend(
            {"group": g, "feature": f, "weight": w, "n": int(m.sum()), "in_sample_ic": ic} for f, w in eq.items()
        )
    return pd.DataFrame(rows, columns=["group", "feature", "weight", "n", "in_sample_ic"])


class WFOSelectedSCR(Learner):
    """P1_WFO_*: in-fold univariate p < 0.05 (anchor-clustered) → best per family → VIF < 5 → ``scr``."""

    def __init__(
        self,
        alpha: float,
        priors: Mapping[str, int],
        family: Mapping[str, str],
        *,
        p_max: float = 0.05,
        vif_max: float = 5.0,
    ) -> None:
        self.alpha = alpha
        self.priors = dict(priors)
        self.family = dict(family)
        self.p_max = p_max
        self.vif_max = vif_max

    def _fit(self, X: pd.DataFrame, y: np.ndarray, meta: pd.DataFrame) -> None:
        candidates = [c for c in value_columns(X.columns) if self.priors.get(c) in (1, -1)]
        self.selection_: Selection = wfo_select(
            X, y, meta["anchor"].to_numpy(), self.family, p_max=self.p_max, vif_max=self.vif_max, candidates=candidates
        )
        self.empty_ = self.selection_.empty
        self.scr_ = SignConstrainedRidge(self.alpha, self.priors, self.selection_.selected)
        if not self.empty_:
            self.scr_.fit(X[self.selection_.selected], y, meta)

    def _predict(self, X: pd.DataFrame, meta: pd.DataFrame) -> np.ndarray:
        if self.empty_:
            return np.full(len(X), np.nan)
        return self.scr_.predict(X, meta)


def final_wfo(alpha: float, priors: Mapping[str, int], cols: Sequence[str] = FINAL_WFO_COLS) -> SignConstrainedRidge:
    """Final_WFO and PostCOVID: ``scr`` on {ebitda_ev, sp, fcf_yield} (P/FCF and FCF yield are one column)."""
    return SignConstrainedRidge(alpha, priors, cols)
