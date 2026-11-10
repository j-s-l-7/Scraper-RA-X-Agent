"""Market regime vector and labels at one anchor (01 §3.1, 3.2, 3.5): breadth, divergence, volatility and trend,
macro. Breadth is measured over the breadth members, the securities of the anchor's tactical universe (``members``
column ``in_u_score_t`` when present, else every emitted row). All values are floats; a column that cannot be formed
is NaN. The vector is broadcast to every row of the anchor as ``regime_<name>`` (kind: broadcast).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ideation.regime.context import AnchorContext
from ideation.regime.macro import macro_features

MA_LONG, MA_SHORT = 200, 50
H52 = 252
R6M = 126
NHNL_DAYS = 20
REGIME_NAMES = (
    "pct_above_200dma", "pct_above_50dma", "ad_ratio_20d", "nh_nl_20d", "div_6m", "pct_beating_6m",
    "vix", "vix_pct_3y", "vix_chg_1m", "n500_ma200_gap", "n500_ma200_slope_63d", "n500_dd_252",
    "cpi_yoy", "iip_yoy", "usdinr_3m", "wti_3m", "us10y_3m_bp",
)  # fmt: skip


def _mean_last(a: np.ndarray, k: int, min_obs: int) -> np.ndarray:
    w = a[-k:]
    n = np.sum(~np.isnan(w), axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        m = np.nansum(w, axis=0) / np.maximum(n, 1)
    return np.where(n >= min_obs, m, np.nan)


def breadth_columns(ctx: AnchorContext) -> np.ndarray:
    m = ctx.members
    if "in_u_score_t" in m.columns:
        return ctx.col[m["in_u_score_t"].fillna(False).to_numpy(dtype=bool)]
    return ctx.col


def compute_regime(ctx: AnchorContext, view) -> dict[str, float]:
    """Regime vector at ``ctx.anchor`` (keys ``REGIME_NAMES``)."""
    B = breadth_columns(ctx)
    L, R = ctx.L[:, B], ctx.R[:, B]
    out: dict[str, float] = dict.fromkeys(REGIME_NAMES, float("nan"))
    if L.shape[0] > MA_LONG and L.shape[1]:
        last = L[-1]
        ma200 = _mean_last(L, MA_LONG, 150)
        ma50 = _mean_last(L, MA_SHORT, 40)
        ok = ~np.isnan(ma200) & ~np.isnan(last)
        out["pct_above_200dma"] = float(np.mean(last[ok] > ma200[ok]) * 100) if ok.any() else float("nan")
        ok = ~np.isnan(ma50) & ~np.isnan(last)
        out["pct_above_50dma"] = float(np.mean(last[ok] > ma50[ok]) * 100) if ok.any() else float("nan")
        w = R[-NHNL_DAYS:]
        adv, dec = float(np.sum(w > 0)), float(np.sum(w < 0))
        out["ad_ratio_20d"] = adv / dec if dec > 0 else float("nan")
        if L.shape[0] >= H52 + NHNL_DAYS:
            roll_max = pd.DataFrame(L).rolling(H52, min_periods=200).max().to_numpy()[-NHNL_DAYS:]
            roll_min = pd.DataFrame(L).rolling(H52, min_periods=200).min().to_numpy()[-NHNL_DAYS:]
            cur = L[-NHNL_DAYS:]
            nh = np.nanmean(np.nanmean(np.where(np.isnan(roll_max), np.nan, cur >= roll_max), axis=1))
            nl = np.nanmean(np.nanmean(np.where(np.isnan(roll_min), np.nan, cur <= roll_min), axis=1))
            out["nh_nl_20d"] = float((nh - nl) * 100)
        if L.shape[0] > R6M:
            r6 = L[-1] / L[-1 - R6M] - 1.0
            r6 = r6[np.isfinite(r6)]
            idx = ctx.idx_level
            i6 = idx[-1] / idx[-1 - R6M] - 1.0
            if len(r6) and np.isfinite(i6):
                out["div_6m"] = float((i6 - np.median(r6)) * 100)
                out["pct_beating_6m"] = float(np.mean(r6 > i6) * 100)
    idx = ctx.idx_level
    if len(idx) > MA_LONG + 63 and np.isfinite(idx[-1]):
        sma = pd.Series(idx).rolling(MA_LONG, min_periods=150).mean().to_numpy()
        out["n500_ma200_gap"] = float(idx[-1] / sma[-1] - 1.0)
        out["n500_ma200_slope_63d"] = float(sma[-1] / sma[-64] - 1.0)
        hi = np.nanmax(idx[-H52:])
        out["n500_dd_252"] = float(idx[-1] / hi - 1.0)
    out.update(macro_features(view, ctx.sess_days))
    return {k: float(v) for k, v in out.items()}


def regime_labels(v: dict[str, float]) -> dict[str, str]:
    """01 §3.5 labels from a regime vector (NaN inputs give 'n/a')."""
    gap, slope = v["n500_ma200_gap"], v["n500_ma200_slope_63d"]
    trend = "n/a"
    if np.isfinite(gap) and np.isfinite(slope):
        trend = "Up" if gap > 0 and slope > 0 else "Down" if gap < 0 and slope < 0 else "Transition"
    vp, vx = v["vix_pct_3y"], v["vix"]
    vol = "n/a"
    if np.isfinite(vp):
        vol = "Stressed" if vp > 67 or (np.isfinite(vx) and vx > 25) else "Calm" if vp < 33 else "Normal"
    b = v["pct_above_200dma"]
    breadth = "n/a" if not np.isfinite(b) else "Broad" if b > 60 else "Narrow" if b < 40 else "Neutral"
    lab = {"trend": trend, "vol": vol, "breadth": breadth}
    lab["narrow_rally"] = str(bool(np.isfinite(v["div_6m"]) and v["div_6m"] > 10 and trend == "Up")).lower()
    tags = []
    if np.isfinite(v["usdinr_3m"]) and v["usdinr_3m"] > 0.02:
        tags.append("INR weakening 3M")
    if np.isfinite(v["wti_3m"]) and v["wti_3m"] > 0.20:
        tags.append("crude +20% 3M")
    if np.isfinite(v["cpi_yoy"]) and v["cpi_yoy"] > 6:
        tags.append("CPI above 6%")
    lab["macro"] = "; ".join(tags)
    return lab
