"""R4 (05 §2): deciles D1..D10 (D10 = top), quintiles Q1..Q5, and the tails P99/P95 (top) and P5/P1 (bottom).

Bucket of a percentile ``p = (rank − 0.5)/n``: ``D = floor(10·p) + 1``. Tails are symmetric:
P99 = {p ≥ 0.99}, P95 = {p ≥ 0.95}, P5 = {p < 0.05}, P1 = {p < 0.01}. Points per decile = 1..10.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

TABLE_ROWS: tuple[str, ...] = ("P99", "P95", *(f"D{d}" for d in range(10, 0, -1)), "P5", "P1")
"""Row order of table R-A: top tails, D10 … D1, bottom tails."""
_EPS = 1e-9  # guards floor() against p·q landing a hair below an exact boundary


def _bucket(p: pd.Series, q: int) -> pd.Series:
    p = pd.Series(p, dtype=float)
    b = np.minimum(np.floor(p.to_numpy() * q + _EPS) + 1, q)
    return pd.Series(b, index=p.index).astype("Int64")


def decile(p: pd.Series) -> pd.Series:
    """1..10, D10 = top; NaN stays missing."""
    return _bucket(p, 10)


def quintile(p: pd.Series) -> pd.Series:
    """1..5, Q5 = top; NaN stays missing."""
    return _bucket(p, 5)


def tail_flags(p: pd.Series) -> pd.DataFrame:
    p = pd.Series(p, dtype=float)
    return pd.DataFrame({"P99": p >= 0.99, "P95": p >= 0.95, "P5": p < 0.05, "P1": p < 0.01}, index=p.index)


def membership(p: pd.Series) -> pd.DataFrame:
    """Boolean frame with one column per R-A row (``TABLE_ROWS``); a name sits in its decile and its tails."""
    d = decile(p)
    cols = {f"D{k}": (d == k).fillna(False).astype(bool) for k in range(1, 11)}
    cols.update(tail_flags(p).to_dict("series"))
    return pd.DataFrame(cols, index=pd.Series(p).index)[list(TABLE_ROWS)]
