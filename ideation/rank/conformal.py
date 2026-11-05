"""R6 (05 §2, 02 §6.3): Mondrian-by-decile split-conformal band on calibrated residuals, with time decay.

Residuals ``e = excess − α(p)`` come from the same nested history as R5. Each calibration anchor gets weight
``0.5^(age / half_life)`` (age 0 = the most recent calibration anchor; half-life 2 anchors), and the band follows the
weighted (non-exchangeable) conformal quantile of Barber, Candès, Ramdas & Tibshirani (2023): the test point carries
weight 1 placed at ±∞. The 80% band is equal-tailed (10% / 90% quantiles of the signed residual), so it can be
asymmetric, as return residuals are. Realized coverage is reported per test anchor.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd

from ideation.rank.buckets import decile


def time_weights(anchors: pd.Series, half_life: float = 2.0) -> np.ndarray:
    """Per-row weights ``0.5^(age/half_life)`` with age = rank of the row's anchor counted back from the newest."""
    a = pd.to_datetime(pd.Series(anchors))
    order = sorted(a.unique())
    age = {x: len(order) - 1 - i for i, x in enumerate(order)}
    return np.power(0.5, a.map(age).to_numpy(dtype=float) / half_life)


def weighted_upper(resid: np.ndarray, w: np.ndarray, level: float) -> float:
    """Smallest r with Σ_{e_i ≤ r} w̃_i ≥ level, where w̃ = w / (Σw + 1) and the test point's mass sits at +∞."""
    order = np.argsort(resid, kind="stable")
    cum = np.cumsum(w[order]) / (w.sum() + 1.0)
    k = np.searchsorted(cum, level - 1e-12)
    return float(resid[order][k]) if k < len(resid) else float("inf")


def weighted_lower(resid: np.ndarray, w: np.ndarray, level: float) -> float:
    return -weighted_upper(-resid, w, level)


def fit_bands(
    cal: pd.DataFrame, *, alpha: float = 0.2, half_life: float = 2.0, resid_col: str = "resid"
) -> pd.DataFrame:
    """``cal`` rows: ``anchor``, ``p``, ``resid``. Returns per decile 1..10: n, lo, hi (residual offsets)."""
    c = cal.dropna(subset=["p", resid_col])
    w_all = time_weights(c["anchor"], half_life)
    d = decile(c["p"]).to_numpy()
    rows = []
    for k in range(1, 11):
        m = d == k
        e, w = c.loc[m, resid_col].to_numpy(dtype=float), w_all[m]
        if not m.any():
            rows.append({"decile": k, "n": 0, "lo": -np.inf, "hi": np.inf})
            continue
        rows.append(
            {
                "decile": k,
                "n": int(m.sum()),
                "lo": weighted_lower(e, w, 1 - alpha / 2),
                "hi": weighted_upper(e, w, 1 - alpha / 2),
            }
        )
    return pd.DataFrame(rows).set_index("decile")


def band(p: np.ndarray | pd.Series, center: np.ndarray, bands: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Band ``[α(p) + lo_d, α(p) + hi_d]`` for each name (``center`` = α(p))."""
    d = decile(pd.Series(p)).to_numpy()
    lo = bands["lo"].reindex(d).to_numpy(dtype=float)
    hi = bands["hi"].reindex(d).to_numpy(dtype=float)
    return center + lo, center + hi


def coverage(realized: Sequence[float] | np.ndarray, lo: np.ndarray, hi: np.ndarray) -> float:
    r = np.asarray(realized, dtype=float)
    ok = ~np.isnan(r)
    return float(((r >= lo) & (r <= hi))[ok].mean())
