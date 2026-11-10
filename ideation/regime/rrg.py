"""Relative Rotation Graph (01 §3.4): JdK-style RS-Ratio / RS-Momentum on weekly points.

Weekly points are anchor-aligned: the anchor session and every 5th session before it. A calendar-week resample would
label a partial week with a Friday after the anchor; this never looks past the anchor and gives the same cadence.

    RS       = P_group / P_benchmark
    x_w      = RS_w / SMA_10w(RS) - 1
    RS-Ratio = 100 + 10 * z(x),               z over the trailing 52 weekly values (>= 26 needed)
    RS-Mom   = 100 + 10 * z(Ratio_w / Ratio_{w-4} - 1)

Quadrants: Leading (ratio >= 100, mom >= 100), Weakening (>= 100, < 100), Lagging (< 100, < 100), Improving
(< 100, >= 100). ``variant="dossier"`` is the dossier's 60-week-ROC reading: Ratio = 100 * RS_w / RS_{w-60},
Mom = 100 * Ratio_w / Ratio_{w-4}.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

WEEK = 5
SMA_W = 10
Z_W = 52
Z_MIN = 26
MOM_LAG = 4
QUAD_NAMES = {0: "Lagging", 1: "Improving", 2: "Weakening", 3: "Leading"}
QUAD_CODE = {v: k for k, v in QUAD_NAMES.items()}


@dataclass(frozen=True)
class RrgParams:
    variant: str = "jdk"
    week: int = WEEK


DEFAULT = RrgParams()


def weekly_points(n_sessions: int, week: int = WEEK) -> np.ndarray:
    """Row indices (ascending) of the anchor-aligned weekly points; the last is the anchor row."""
    return np.arange(n_sessions - 1, -1, -week)[::-1]


def levels_from_returns(ret: np.ndarray) -> np.ndarray:
    """Composite level (start 1) from daily returns; a NaN return is flat but marks the level NaN until the first
    valid return (so warm-up rows are excluded, not treated as a flat history)."""
    r = np.asarray(ret, dtype=float)
    started = np.maximum.accumulate(~np.isnan(r), axis=0)
    lvl = np.exp(np.cumsum(np.log1p(np.where(np.isnan(r), 0.0, r)), axis=0))
    return np.where(started, lvl, np.nan)


def _roll(a: np.ndarray, w: int, fn: str, min_periods: int) -> np.ndarray:
    df = pd.DataFrame(a).rolling(w, min_periods=min_periods)
    return getattr(df, fn)().to_numpy()


def _z(a: np.ndarray) -> np.ndarray:
    mu = _roll(a, Z_W, "mean", Z_MIN)
    sd = _roll(a, Z_W, "std", Z_MIN)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(sd > 1e-12, (a - mu) / sd, np.nan)


def rrg_series(rs: np.ndarray, params: RrgParams = DEFAULT) -> tuple[np.ndarray, np.ndarray]:
    """(ratio, mom) for weekly RS columns ``rs`` (W, K), oldest first."""
    rs = np.asarray(rs, dtype=float)
    if rs.ndim == 1:
        rs = rs[:, None]
    if params.variant == "dossier":
        ratio = np.full(rs.shape, np.nan)
        ratio[60:] = 100.0 * rs[60:] / rs[:-60]
        mom = np.full(rs.shape, np.nan)
        mom[MOM_LAG:] = 100.0 * ratio[MOM_LAG:] / ratio[:-MOM_LAG]
        return ratio, mom
    sma = _roll(rs, SMA_W, "mean", SMA_W)
    with np.errstate(invalid="ignore", divide="ignore"):
        x = rs / sma - 1.0
    ratio = 100.0 + 10.0 * _z(x)
    chg = np.full(rs.shape, np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        chg[MOM_LAG:] = ratio[MOM_LAG:] / ratio[:-MOM_LAG] - 1.0
    return ratio, 100.0 + 10.0 * _z(chg)


def quadrant(ratio: np.ndarray, mom: np.ndarray) -> np.ndarray:
    """Quadrant code per element (see ``QUAD_NAMES``), -1 where either input is NaN."""
    q = np.where(ratio >= 100, np.where(mom >= 100, 3, 2), np.where(mom >= 100, 1, 0))
    return np.where(np.isnan(ratio) | np.isnan(mom), -1, q)


def rrg_at_anchor(group_levels: np.ndarray, bench_level: np.ndarray, params: RrgParams = DEFAULT
                  ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:  # fmt: skip
    """Ratio, momentum and quadrant code of every column of ``group_levels`` (T, K) at the last row (the anchor),
    against ``bench_level`` (T,). Levels are daily; weekly points are taken here."""
    pts = weekly_points(group_levels.shape[0], params.week)
    with np.errstate(invalid="ignore", divide="ignore"):
        rs = group_levels[pts] / bench_level[pts][:, None]
    ratio, mom = rrg_series(rs, params)
    r, m = ratio[-1], mom[-1]
    return r, m, quadrant(r, m)
