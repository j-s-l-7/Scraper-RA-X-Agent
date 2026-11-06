"""R3 (05 §2): percentile rank within the scored cross-section, Ideation Score and signed score."""

from __future__ import annotations

import numpy as np
import pandas as pd


def percentile(s: pd.Series, by: pd.Series | np.ndarray | None = None) -> pd.Series:
    """``p = (rank − 0.5)/n`` within the cross-section (or within each ``by`` group, e.g. the anchor).

    Ties get the average rank; NaN scores stay NaN and do not count in ``n``.
    """
    s = pd.Series(s, dtype=float)
    if by is None:
        r = s.rank(method="average")
        return (r - 0.5) / s.notna().sum()
    keys = pd.Series(np.asarray(by), index=s.index)
    grouped = s.groupby(keys, sort=False)
    return (grouped.rank(method="average") - 0.5) / grouped.transform("count")


def ideation_score(p: pd.Series) -> pd.Series:
    """``IS = round(100·p)`` in 0–100 (nullable integer)."""
    return np.round(100 * pd.Series(p, dtype=float)).astype("Int64")


def signed_score(p: pd.Series) -> pd.Series:
    """``S = 2p − 1`` in [−1, 1] (dossier §5.3 view scale)."""
    return 2 * pd.Series(p, dtype=float) - 1
