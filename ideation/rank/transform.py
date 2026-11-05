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
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

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
    sd = x.std(ddof=0)
    return (x - x.mean()) / sd if sd > 0 else x * 0.0


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
    parts = [_transform_one(frame, features, spec, na_layout) for _, frame in df.groupby(spec.anchor_col, sort=True)]
    return pd.concat(parts).loc[df.index]


def flag_columns(columns: Sequence[str]) -> list[str]:
    return [c for c in columns if c.startswith(("isna_", "na_layout_"))]


def value_columns(columns: Sequence[str]) -> list[str]:
    return [c for c in columns if not c.startswith(("isna_", "na_layout_"))]
