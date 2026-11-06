"""R1 (05 §2, 02 §4.6, 03 §C6): per-cross-section transform. Every statistic is fitted on one anchor's rows only.

Steps per anchor and feature:

a. winsorize: growth columns at P5/P95 (``winsor``) or values outside [P5, P95] replaced by the cross-section median
   (``dossier_median``); every other column at P1/P99;
b. ``global`` = rank → Φ⁻¹((r − 0.5)/n); ``industry`` = z-score within ``industry_group`` times the sign prior (a
   group with fewer than ``min_group`` non-missing names falls back to ``sector``, then to the whole cross-section);
   ``hybrid`` = both, global columns as ``<f>`` and industry columns as ``ind_<f>`` (05 §3.3, our definition);
c. missing-applicable values become 0 (the post-transform median) with ``isna_<f>`` = 1; values marked not applicable
   (layout) become 0 with ``na_layout_<f>`` = 1.

Industry columns are already sign-flipped. ``scr`` and the Phase-I composites flip by prior themselves, so they are
run on the ``global`` spec only (as in the trial lists).

**Broadcast columns** (anchor-constant by construction: regime, macro, breadth) are declared explicitly in
``TransformSpec.broadcast_cols``, normally from ``kind: broadcast`` in the feature YAML (:func:`columns_of_kind`); a
column is never classified by looking at its values. They skip steps a–b (a per-cross-section rank or z of a constant
is all zeros) and leave R1 raw, NaN kept, with the usual ``isna_``/``na_layout_`` flags. A train-only standardiser
(:func:`fit_broadcast_scaler`, mean and SD over the fold's training anchors, one value per anchor) is fitted inside
the model (``models.zoo.BroadcastScaler``) and applied unchanged to test anchors; missing values then become 0 (the
training mean). A declared broadcast column that varies within an anchor is an error. An undeclared (cross-sectional)
column that happens to be constant at an anchor is still zeroed, and :class:`ConstantCrossSectionWarning` counts it.
"""

from __future__ import annotations

import warnings
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
import pandas as pd
from scipy.stats import norm

Kind = Literal["global", "industry", "hybrid"]
GrowthMode = Literal["winsor", "dossier_median"]


@dataclass(frozen=True)
class TransformSpec:
    kind: Kind = "global"
    growth_mode: GrowthMode = "winsor"
    growth_cols: frozenset[str] = frozenset()
    priors: Mapping[str, int] = field(default_factory=dict)
    """+1 / −1 per feature for the industry sign flip; a feature without a prior is not flipped."""
    min_group: int = 8
    anchor_col: str = "anchor"
    group_col: str = "industry_group"
    fallback_col: str = "sector"
    broadcast_cols: frozenset[str] = frozenset()
    """Anchor-constant columns that bypass the per-cross-section transform (see the module docstring)."""


class ConstantCrossSectionWarning(UserWarning):
    """A cross-sectional (not broadcast) feature was constant at an anchor and was zeroed by the R1 transform."""


def columns_of_kind(features: Mapping[str, Mapping[str, Any]], kind: str = "broadcast") -> frozenset[str]:
    """Feature names whose YAML entry declares ``kind: <kind>`` (default kind: ``cross_sectional``)."""
    return frozenset(f for f, meta in features.items() if (meta or {}).get("kind", "cross_sectional") == kind)


def winsorize(x: pd.Series, *, growth: bool = False, mode: GrowthMode = "winsor") -> pd.Series:
    x = pd.Series(x, dtype=float)
    v = x.dropna()
    if v.empty:
        return x
    lo_q, hi_q = (0.05, 0.95) if growth else (0.01, 0.99)
    lo, hi = np.quantile(v, [lo_q, hi_q])
    if growth and mode == "dossier_median":
        return x.where(x.isna() | ((x >= lo) & (x <= hi)), float(np.median(v)))
    return x.clip(lo, hi)


def rank_gauss(x: pd.Series) -> pd.Series:
    """Φ⁻¹((rank − 0.5)/n) over the non-missing values; ties averaged; NaN stays NaN."""
    x = pd.Series(x, dtype=float)
    n = x.notna().sum()
    return pd.Series(norm.ppf((x.rank(method="average") - 0.5) / n), index=x.index) if n else x


def _zscore(x: pd.Series) -> pd.Series:
    if x.nunique(dropna=True) <= 1:  # constant: the float SD can be a rounding residue (~1e-17), not 0
        return x * 0.0
    return (x - x.mean()) / x.std(ddof=0)


def industry_z(x: pd.Series, group: pd.Series, fallback: pd.Series, min_group: int = 8) -> pd.Series:
    """z within ``group`` when it has ≥ ``min_group`` non-missing names, else within ``fallback``, else overall."""
    x = pd.Series(x, dtype=float)
    ok = x.notna()
    n_group = ok.groupby(group, dropna=False).transform("sum")
    n_fallback = ok.groupby(fallback, dropna=False).transform("sum")
    z_group = x.groupby(group, dropna=False).transform(_zscore)
    z_fallback = x.groupby(fallback, dropna=False).transform(_zscore)
    z_all = _zscore(x)
    out = np.where(n_group >= min_group, z_group, np.where(n_fallback >= min_group, z_fallback, z_all))
    return pd.Series(out, index=x.index).where(ok)


def _transform_one(
    frame: pd.DataFrame, features: Sequence[str], spec: TransformSpec, na_layout: pd.DataFrame | None
) -> pd.DataFrame:
    """One anchor's cross-section. Tests monkeypatch this to prove it never sees two anchors (T-18)."""
    cols: dict[str, pd.Series] = {}
    flags: dict[str, pd.Series] = {}
    for f in features:
        x = frame[f].astype(float)
        layout = (
            na_layout[f].reindex(frame.index).fillna(False).astype(bool)
            if na_layout is not None and f in na_layout
            else None
        )
        if layout is not None:
            x = x.mask(layout)
        if f in spec.broadcast_cols:
            if x.nunique(dropna=True) > 1:
                raise ValueError(f"broadcast column {f!r} varies within anchor {frame[spec.anchor_col].iloc[0]}")
            cols[f] = x  # raw; standardised later on training anchors only (BroadcastScaler)
            flags[f"isna_{f}"] = (x.isna() if layout is None else x.isna() & ~layout).astype(float)
            if layout is not None:
                flags[f"na_layout_{f}"] = layout.astype(float)
            continue
        x = winsorize(x, growth=f in spec.growth_cols, mode=spec.growth_mode)
        missing = x.isna() if layout is None else x.isna() & ~layout
        if spec.kind in ("global", "hybrid"):
            cols[f] = rank_gauss(x).fillna(0.0)
        if spec.kind in ("industry", "hybrid"):
            z = industry_z(x, frame[spec.group_col], frame[spec.fallback_col], spec.min_group)
            cols[f if spec.kind == "industry" else f"ind_{f}"] = (z * spec.priors.get(f, 1)).fillna(0.0)
        flags[f"isna_{f}"] = missing.astype(float)
        if layout is not None:
            flags[f"na_layout_{f}"] = layout.astype(float)
    return pd.DataFrame({**cols, **flags}, index=frame.index)


def xs_transform(
    df: pd.DataFrame, features: Sequence[str], spec: TransformSpec, na_layout: pd.DataFrame | None = None
) -> pd.DataFrame:
    """Model matrix X for every row of ``df`` (same index), transformed anchor by anchor.

    ``na_layout`` (optional, boolean, indexed like ``df``) marks values that are not applicable for the row's layout.
    """
    parts, constant = [], []
    for anchor, frame in df.groupby(spec.anchor_col, sort=True):
        parts.append(_transform_one(frame, features, spec, na_layout))
        for f in features:
            v = frame[f].dropna()
            if f not in spec.broadcast_cols and len(v) >= 2 and v.nunique() == 1:
                constant.append((anchor, f))
    if constant:
        warnings.warn(
            f"{len(constant)} (anchor, feature) cross-sections were constant and zeroed by R1, e.g. {constant[:3]};"
            " declare anchor-constant features as broadcast",
            ConstantCrossSectionWarning,
            stacklevel=2,
        )
    return pd.concat(parts).loc[df.index]


def fit_broadcast_scaler(
    X: pd.DataFrame, anchors: pd.Series | np.ndarray, cols: Sequence[str]
) -> tuple[pd.Series, pd.Series]:
    """(mean, SD) per broadcast column over the training anchors, one value per anchor (each anchor counts once).

    SD uses ddof=1; with fewer than two non-missing anchors, or SD = 0, the SD is NaN and the scaled column is 0.
    """
    per_anchor = X[list(cols)].groupby(np.asarray(anchors)).mean()  # constant within an anchor ⇒ the anchor's value
    sd = per_anchor.std(ddof=1)
    return per_anchor.mean(), sd.where(sd > 0)


def apply_broadcast_scaler(X: pd.DataFrame, mean: pd.Series, sd: pd.Series) -> pd.DataFrame:
    """Standardise the broadcast columns with training statistics; missing (and unscalable) values become 0."""
    out = X.copy()
    cols = list(mean.index)
    out[cols] = ((X[cols] - mean) / sd).fillna(0.0)
    return out


def flag_columns(columns: Sequence[str]) -> list[str]:
    return [c for c in columns if c.startswith(("isna_", "na_layout_"))]


def value_columns(columns: Sequence[str]) -> list[str]:
    return [c for c in columns if not c.startswith(("isna_", "na_layout_"))]
