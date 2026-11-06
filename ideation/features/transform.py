"""Fundamental-feature transforms handed to the model layer (03 §C6).

The R1 rank-gauss / industry-z transform itself is ``ideation.rank.transform.xs_transform`` (E1). This module supplies
what that transform needs from the feature registry:

* :func:`na_layout_frame`: boolean frame of values that are not applicable for the row's layout (``na_layout_<f>``);
* :func:`raw_winsorized`: the winsorized RAW columns that the faithful ``P1_UNIFIED`` trial (``input: raw_winsorized``)
  z-scores with training-fold statistics. Winsorisation is per anchor cross-section, on the rows the caller passes
  (U_score). Growth columns use P5/P95, the others P1/P99; NaN stays NaN (no fill, no rank).
"""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from ideation.features.registry import Registry
from ideation.rank.transform import GrowthMode, winsorize


def na_layout_frame(df: pd.DataFrame, reg: Registry, cols: Sequence[str] | None = None) -> pd.DataFrame:
    """True where feature ``f`` does not apply to the row's ``layout`` (index aligned with ``df``)."""
    names = list(cols) if cols is not None else [c for c in reg.features if c in df.columns]
    return pd.DataFrame(
        {n: ~df["layout"].isin(reg.features[n].layouts) & df["layout"].notna() for n in names}, index=df.index
    )


def raw_winsorized(
    df: pd.DataFrame, reg: Registry, cols: Sequence[str] | None = None, *, growth_mode: GrowthMode = "winsor",
    anchor_col: str = "anchor",
) -> pd.DataFrame:  # fmt: skip
    """``df[cols]`` winsorized within each anchor cross-section, same index as ``df``."""
    names = list(cols) if cols is not None else [c for c in reg.features if c in df.columns]
    out = df[names].astype(float).copy()
    growth = reg.growth_cols()
    for _, idx in df.groupby(anchor_col, sort=True).groups.items():
        for n in names:
            out.loc[idx, n] = winsorize(out.loc[idx, n], growth=n in growth, mode=growth_mode)
    return out
