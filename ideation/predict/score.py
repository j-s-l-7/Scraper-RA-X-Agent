"""Score a live cross-section with the shipped composites through the backtest code (R1 -> model -> R3), plus
exact linear contributions, beta/vol-neutral ranks and the OOD guard (01 §4.4).

* 1Y: ``bakeoff.data.build_matrix`` (R1 on the whole universe at the anchor) + ``bakeoff.models.build_model``.
* 3M: ``tactical.data.build_design`` + ``tactical.engine.build_model``.

The shipped models (P1_COMP_EW, T_EW_SIGNPRIOR) and the 1Y challenger (P1_UNIFIED_XS) are fixed-weight composites:
``fit`` is a no-op, so no label is read to score them. The training matrices used for the OOD guard are rebuilt from
the stored feature parquet only (features, never labels).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import norm
from sklearn.ensemble import IsolationForest

from ideation.bakeoff.data import FUND_FILE, TECH_FILE, UNIVERSE_FILE, Panel, TrialMatrix, build_matrix
from ideation.bakeoff.models import build_model as build_1y_model
from ideation.features.registry import Registry
from ideation.models.phase1 import FixedWeights
from ideation.rank.percentile import ideation_score, percentile
from ideation.rank.transform import value_columns
from ideation.tactical.data import Design, build_design
from ideation.tactical.diagnostics import residualize
from ideation.tactical.engine import build_model as build_3m_model
from ideation.trials.registry import TrialSpec

BV = ["beta_252", "rvol_63", "idio_vol"]
BVLS = [*BV, "ln_mcap", "ln_adv"]
OOD_QUANTILE = 0.99
OOD_TREES = 300
N_TRAIN_1Y = 3
N_TRAIN_3M = 36
ANCHOR_MAX_3M = pd.Timestamp("2025-06-30")
ANCHOR_MAX_1Y = pd.Timestamp("2024-12-31")
"""Last anchors whose labels are unsealed (training/reference windows never reach a sealed anchor)."""


@dataclass
class Scored:
    """One model's live scores. ``frame`` is indexed 0..n-1 with security_id, score, p, is_ (Ideation Score)."""

    model: str
    horizon: str
    frame: pd.DataFrame
    X: pd.DataFrame
    weights: dict[str, float] | None = None
    info: dict[str, Any] = field(default_factory=dict)


def _score_frame(security_id: pd.Series, score: np.ndarray) -> pd.DataFrame:
    s = pd.Series(score, dtype=float)
    p = percentile(s)
    return pd.DataFrame({"security_id": security_id.to_numpy(), "score": s.to_numpy(), "p": p.to_numpy(),
                         "ideation_score": ideation_score(p).to_numpy()})  # fmt: skip


def score_1y(frame: pd.DataFrame, reg: Registry, spec: TrialSpec, anchor: date) -> Scored:
    """Live 1Y scores for one fixed-weight trial. ``frame`` = ``live.frame_1y`` (label columns NaN)."""
    panel = Panel("june", "live", frame, [anchor], pd.Series(dtype="datetime64[ns]"))
    tm: TrialMatrix = build_matrix(panel, spec.config, reg)
    model = build_1y_model(spec.config, reg, broadcast=tm.broadcast, feature_names=tm.feature_names)
    model.fit(tm.X, tm.y.to_numpy(dtype=float), tm.meta)
    score = model.predict(tm.X, tm.meta)
    out = _score_frame(tm.meta["security_id"], score)
    w = dict(model.weights) if isinstance(model, FixedWeights) else None
    return Scored(spec.trial_id, "1Y", out, tm.X, w, {"n": len(out), "feature_names": tm.feature_names})


def score_3m(frame: pd.DataFrame, reg: Registry, spec: TrialSpec) -> Scored:
    """Live 3M scores for the fixed-weight baseline. ``frame`` = ``live.frame_3m``."""
    design = build_design(frame, reg, "T0", adv_floor_cr=float(spec.config["adv_floor_cr"]))
    model = build_3m_model(spec, design)
    model.fit(design.X, design.y.to_numpy(dtype=float), design.meta)
    score = model.predict(design.X, design.meta)
    out = _score_frame(design.meta["security_id"], score)
    w = dict(model.weights) if isinstance(model, FixedWeights) else None
    return Scored(spec.trial_id, "3M", out, design.X, w, {"n": len(out), "design": design})


# ── explanations: exact for linear composites ───────────────────────────────────────────────────────────────────
def contributions(sc: Scored) -> pd.DataFrame | None:
    """``w_f · x_f`` on the R1 columns, one column per feature (sums to the score exactly). None for non-linear models."""
    if sc.weights is None:
        return None
    return pd.DataFrame({f: sc.X[f].to_numpy(dtype=float) * w for f, w in sc.weights.items()})


def top_drivers(contrib: pd.DataFrame, reg: Registry, k_pos: int = 3, k_neg: int = 2) -> list[dict[str, Any]]:
    """Per row: top positive and negative contributions grouped by registry ``category``. Pure formatting."""
    cat = {c: reg.features[c].category if c in reg.features else "other" for c in contrib.columns}
    fam = pd.DataFrame(contrib.T.groupby(cat).sum().T)
    rows = []
    for i in range(len(contrib)):
        r = contrib.iloc[i]
        pos = r[r > 0].sort_values(ascending=False).head(k_pos)
        neg = r[r < 0].sort_values().head(k_neg)
        rows.append({
            "top": [{"feature": f, "category": cat[f], "contribution": round(float(v), 4)} for f, v in pos.items()],
            "bottom": [{"feature": f, "category": cat[f], "contribution": round(float(v), 4)} for f, v in neg.items()],
            "by_category": {c: round(float(v), 4) for c, v in fam.iloc[i].items()},
        })  # fmt: skip
    return rows


# ── beta / vol neutral rank ─────────────────────────────────────────────────────────────────────────────────────
def neutral_ranks(p: pd.Series, controls: pd.DataFrame) -> pd.DataFrame:
    """Percentile of the score after removing its cross-sectional dependence on risk controls (MS2 diagnostics).

    ``z = Φ⁻¹(p)`` is regressed on [beta_252, rvol_63, idio_vol] (BV) and additionally [ln mcap, ln ADV63] (BVLS) with
    an intercept; the percentile of each residual is the neutral rank. Missing controls take the cross-sectional
    median (``diagnostics._xs_residual``). The calibrated alpha is NEVER fitted on these ranks (they carry no OOS
    calibration); they exist so that downstream BL can build views on the neutral alpha."""
    df = controls.copy()
    df["anchor"] = 0
    df["ln_mcap"] = np.log(df["mcap_pit_cr"].where(df["mcap_pit_cr"] > 0))
    df["ln_adv"] = np.log(df["adv63_lakh"].where(df["adv63_lakh"] > 0))
    df["z"] = norm.ppf(p.clip(1e-6, 1 - 1e-6).to_numpy())
    out = pd.DataFrame(index=df.index)
    out["neutral_p_bv"] = percentile(residualize(df, "z", BV, wins=False))
    out["neutral_p_bvls"] = percentile(residualize(df, "z", BVLS, wins=False))
    return out


# ── OOD guard (01 §4.4) ─────────────────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class OodGuard:
    threshold: float
    n_train: int
    train_anchors: tuple[str, ...]
    columns: tuple[str, ...]
    iso: Any = field(repr=False, compare=False, default=None)

    def score(self, X: pd.DataFrame) -> np.ndarray:
        return -self.iso.score_samples(X[list(self.columns)].fillna(0.0))

    def flags(self, X: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        s = self.score(X)
        return s, s > self.threshold

    def describe(self) -> dict[str, Any]:
        return {"threshold": self.threshold, "n_train": self.n_train, "train_anchors": list(self.train_anchors),
                "n_columns": len(self.columns), "trees": OOD_TREES, "quantile": OOD_QUANTILE}  # fmt: skip


def fit_ood(X_train: pd.DataFrame, anchors: Sequence[Any], *, seed: int = 0, n_jobs: int = 2) -> OodGuard:
    """IsolationForest (300 trees, seed fixed) on the training matrix' value columns; threshold = P99 of the
    training anomaly scores (higher = more anomalous)."""
    cols = value_columns(list(X_train.columns))
    Xt = X_train[cols].fillna(0.0)
    iso = IsolationForest(n_estimators=OOD_TREES, contamination="auto", random_state=seed, n_jobs=n_jobs).fit(Xt)
    thr = float(np.quantile(-iso.score_samples(Xt), OOD_QUANTILE))
    return OodGuard(thr, len(Xt), tuple(str(pd.Timestamp(a).date()) for a in anchors), tuple(cols), iso)


def _norm_anchor(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s).dt.normalize()


def training_frame_1y(out_dir: Path, reg: Registry, anchors: Sequence[date]) -> pd.DataFrame:
    """Stored U_score x fundamental x technical rows for ``anchors`` (features only, NaN label columns)."""
    from ideation.predict.live import LABEL_NAN

    uni = pd.read_parquet(out_dir / UNIVERSE_FILE)
    uni = uni[uni["anchor"].isin(anchors) & uni["in_u_score"]][
        ["anchor", "security_id", "in_u_score", "in_u_score_adv05", "in_u1000", "adv63_lakh", "mcap_pit_cr"]]
    fund = pd.read_parquet(out_dir / FUND_FILE)
    fund = fund[fund["anchor"].isin(anchors)]
    fcols = [n for n in reg.names(source="features_fundamental.yaml") if n in fund.columns]
    fund = fund[["anchor", "security_id", "company_id", "industry_group", "sector", "layout", *fcols]]
    tech = pd.read_parquet(out_dir / TECH_FILE)
    tech = tech[tech["anchor"].isin(anchors)]
    tcols = [n for n in reg.names(sets=["tech", "regime"]) if n in tech.columns]
    frame = uni.merge(fund, on=["anchor", "security_id"], how="left", validate="1:1")
    frame = frame.merge(tech[["anchor", "security_id", *tcols]], on=["anchor", "security_id"], how="left", validate="1:1")
    for c in reg.broadcast_cols():
        if c in frame.columns:
            frame[c] = frame.groupby("anchor")[c].transform("first")
    for c in LABEL_NAN:
        frame[c] = np.nan if c != "label_end" else pd.NaT
    frame["labelled"] = False
    return frame.sort_values(["anchor", "security_id"], kind="stable").reset_index(drop=True)


def training_frame_3m(out_dir: Path, anchors: Sequence[pd.Timestamp]) -> pd.DataFrame:
    tech = pd.read_parquet(out_dir / "features_tech.parquet")
    tech["anchor"] = _norm_anchor(tech["anchor"])
    tech = tech[tech["anchor"].isin(list(anchors)) & tech["in_u_score_t"]].copy()
    uni = pd.read_parquet(out_dir / UNIVERSE_FILE, columns=["anchor", "security_id", "adv63_lakh", "mcap_pit_cr"])
    uni["anchor"] = _norm_anchor(uni["anchor"])
    df = tech.merge(uni, on=["anchor", "security_id"], how="left", validate="1:1")
    for c in ("ret_3m", "ret_1m", "retp_3m", "exu_3m", "exn_3m", "exu_1m"):
        df[c] = np.nan
    df["label_end"] = pd.NaT
    return df.reset_index(drop=True)


def last_anchors_3m(out_dir: Path, n: int = N_TRAIN_3M) -> list[pd.Timestamp]:
    t = pd.read_parquet(out_dir / "features_tech.parquet", columns=["anchor"])
    a = sorted(_norm_anchor(t["anchor"]).unique())
    return [x for x in a if x <= ANCHOR_MAX_3M][-n:]


def june_anchors_1y(out_dir: Path, n: int = N_TRAIN_1Y) -> list[date]:
    u = pd.read_parquet(out_dir / UNIVERSE_FILE, columns=["anchor"])
    a = sorted({x for x in u["anchor"].unique() if x.month == 6 and pd.Timestamp(x) <= ANCHOR_MAX_1Y})
    return a[-n:]


def ood_guard_1y(out_dir: Path, reg: Registry, spec: TrialSpec) -> OodGuard:
    anchors = june_anchors_1y(out_dir)
    fr = training_frame_1y(out_dir, reg, anchors)
    panel = Panel("june", "train", fr, anchors, pd.Series(dtype="datetime64[ns]"))
    tm = build_matrix(panel, spec.config, reg)
    return fit_ood(tm.X, anchors)


def ood_guard_3m(out_dir: Path, reg: Registry, spec: TrialSpec) -> OodGuard:
    anchors = last_anchors_3m(out_dir)
    design: Design = build_design(training_frame_3m(out_dir, anchors), reg, "T0", adv_floor_cr=float(spec.config["adv_floor_cr"]))
    return fit_ood(design.X, anchors)


_ = Mapping
