"""Price / volume features at the anchor close (01 §2.5, 05 §6.2). Pure function of an ``AnchorContext`` (one PitView).

Every trailing window ends at the anchor row (the last row of the panel). Definitions are in
``config/features_technical.yaml``
(trainable_from, priors) and below. Sessions are rows of the panel; a window with too few valid observations is NaN.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ideation.regime.context import AnchorContext
from ideation.regime.rotation import rrg_table  # noqa: F401  (re-export for builders)

SOURCE = "features_technical.yaml"
ANN = np.sqrt(252.0)
VOL_CONF = 1.5  # raw ADV20 / ADV126 ratio that confirms a breakout


def _ret(L: np.ndarray, col: np.ndarray, k: int, skip: int = 0) -> np.ndarray:
    """Return from row -1-k to row -1-skip (adjusted levels)."""
    if L.shape[0] <= k:
        return np.full(len(col), np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        return L[-1 - skip, col] / L[-1 - k, col] - 1.0


def _cum(g: np.ndarray, k: int) -> np.ndarray:
    """Compounded composite return over the last k rows, per column (NaN if any row is NaN)."""
    with np.errstate(invalid="ignore"):
        return np.exp(np.sum(np.log1p(g[-k:]), axis=0)) - 1.0


def _nan_cols(a: np.ndarray, min_obs: int) -> np.ndarray:
    return np.sum(~np.isnan(a), axis=0) >= min_obs


def _mean(a: np.ndarray, min_obs: int) -> np.ndarray:
    n = np.sum(~np.isnan(a), axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(n >= min_obs, np.nansum(a, axis=0) / np.maximum(n, 1), np.nan)


def _median_last(a: np.ndarray, k: int) -> np.ndarray:
    return np.median(a[-k:], axis=0)


def _std(a: np.ndarray, min_obs: int) -> np.ndarray:
    n = np.sum(~np.isnan(a), axis=0)
    mu = _mean(a, min_obs)
    with np.errstate(invalid="ignore", divide="ignore"):
        var = np.nansum((a - mu) ** 2, axis=0) / np.maximum(n - 1, 1)
    return np.where(n >= min_obs, np.sqrt(var), np.nan)


def _beta(R: np.ndarray, x: np.ndarray, min_obs: int = 60) -> tuple[np.ndarray, np.ndarray]:
    """OLS beta and residual std (annualised) of columns of R on x, using rows where both are present."""
    valid = ~np.isnan(R) & ~np.isnan(x)[:, None]
    n = valid.sum(axis=0).astype(float)
    xv = np.where(valid, x[:, None], 0.0)
    yv = np.where(valid, R, 0.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        mx, my = xv.sum(0) / n, yv.sum(0) / n
        dx = np.where(valid, x[:, None] - mx, 0.0)
        dy = np.where(valid, R - my, 0.0)
        sxx, sxy = (dx * dx).sum(0), (dx * dy).sum(0)
        beta = sxy / sxx
        resid = dy - beta * dx
        iv = np.sqrt((resid**2).sum(0) / np.maximum(n - 2, 1)) * ANN
    ok = (n >= min_obs) & (sxx > 0)
    return np.where(ok, beta, np.nan), np.where(ok, iv, np.nan)


def technical_columns(ctx: AnchorContext, rrg: pd.DataFrame | None) -> pd.DataFrame:
    """One row per ``ctx.members`` row, price/volume feature columns only (no identity columns)."""
    L, R, col = ctx.L, ctx.R, ctx.col
    T = L.shape[0]
    Lm = L[:, col]
    Rm = R[:, col]
    V = ctx.panel.value_cr[:, col]
    out: dict[str, np.ndarray] = {}

    out["mom_1m"] = _ret(L, col, 21)
    out["mom_3m"] = _ret(L, col, 63)
    out["mom_6m"] = _ret(L, col, 126)
    out["mom_12_1"] = _ret(L, col, 252, skip=21)
    out["rev_5d"] = _ret(L, col, 5)

    g = ctx.comps.group_ret[:, np.maximum(ctx.gcode[col], 0)]
    g = np.where((ctx.gcode[col] >= 0)[None, :], g, np.nan)
    idx = ctx.idx_level
    for k, tag in ((63, "3m"), (126, "6m")):
        out[f"rs_grp_{tag}"] = out[f"mom_{tag}"] - _cum(g, k) if k < T else np.full(len(col), np.nan)
        with np.errstate(invalid="ignore", divide="ignore"):
            i_ret = idx[-1] / idx[-1 - k] - 1.0 if k < T else np.nan
        out[f"rs_n500_{tag}"] = out[f"mom_{tag}"] - i_ret

    last = Lm[-1]
    w252 = Lm[-252:]
    with np.errstate(invalid="ignore", divide="ignore"):
        ok = _nan_cols(w252, 200)
        out["dist_52wh"] = np.where(ok, last / np.nanmax(np.where(np.isnan(w252), -np.inf, w252), axis=0) - 1.0, np.nan)
        out["dist_52wl"] = np.where(ok, last / np.nanmin(np.where(np.isnan(w252), np.inf, w252), axis=0) - 1.0, np.nan)

    # volume ratio and breakouts
    adv20 = _median_last(V, 20)
    adv126 = _median_last(V, 126) if T >= 126 else np.full(len(col), np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        ratio = adv20 / adv126
    with np.errstate(divide="ignore", invalid="ignore"):
        out["vol_ratio"] = np.where((adv20 > 0) & (adv126 > 0), np.log(ratio), np.nan)
    conf = np.where((adv20 > 0) & (adv126 > 0), ratio > VOL_CONF, False)
    for tag, win, min_obs in (("1y", 252, 200), ("2y", 504, 400), ("3_4y", 1008, 756)):
        prior = Lm[-1 - win : -1] if win < T else Lm[:-1]
        n_ok = np.sum(~np.isnan(prior), axis=0)
        enough = (n_ok >= min_obs) & (min_obs < T)
        hi = np.max(np.where(np.isnan(prior), -np.inf, prior), axis=0)
        lo = np.min(np.where(np.isnan(prior), np.inf, prior), axis=0)
        out[f"brk_{tag}"] = np.where(enough & ~np.isnan(last), ((last >= hi) & conf).astype(float), np.nan)
        out[f"brkdn_{tag}"] = np.where(enough & ~np.isnan(last), ((last <= lo) & conf).astype(float), np.nan)

    ma200 = _mean(Lm[-200:], 150)
    ma50 = _mean(Lm[-50:], 40)
    out["ma200_gap"] = last / ma200 - 1.0
    out["ma50_200"] = ma50 / ma200 - 1.0

    r63 = Rm[-63:]
    out["rvol_63"] = _std(r63, 40) * ANN
    dn = np.where(np.isnan(r63), np.nan, np.minimum(r63, 0.0) ** 2)
    out["dvol_63"] = np.where(_nan_cols(r63, 40), np.sqrt(_mean(dn, 40)) * ANN, np.nan)
    beta, ivol = _beta(Rm[-252:], ctx.idx_ret[-252:])
    out["beta_252"] = np.clip(np.where(np.isnan(beta), 1.0, beta), -1.0, 3.0)
    out["idio_vol"] = ivol
    out["liq_rank"] = pd.Series(adv126).rank(pct=True).to_numpy()

    d = ctx.panel.deliv[:, col]
    both = _nan_cols(d[-20:], 10) & _nan_cols(d[-126:], 60)
    out["deliv_chg"] = np.where(both, _mean(d[-20:], 10) - _mean(d[-126:], 60), np.nan)
    out["deliv_lvl_63"] = _mean(d[-63:], 30)

    r21, r126, r252 = Rm[-21:], Rm[-126:], Rm[-252:]
    out["max_21"] = np.where(_nan_cols(r21, 15), np.nanmax(np.where(np.isnan(r21), -np.inf, r21), axis=0), np.nan)
    n = np.sum(~np.isnan(r126), axis=0)
    mu = _mean(r126, 90)
    dev = r126 - mu
    with np.errstate(invalid="ignore", divide="ignore"):
        m2 = np.nansum(dev**2, axis=0) / np.maximum(n, 1)
        m3 = np.nansum(dev**3, axis=0) / np.maximum(n, 1)
        out["skew_126"] = np.where((n >= 90) & (m2 > 0), m3 / m2**1.5, np.nan)
    n2 = np.sum(~np.isnan(r252), axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        neg = np.sum(r252 < 0, axis=0) / n2
        pos = np.sum(r252 > 0, axis=0) / n2
    r_252 = _ret(L, col, 252)
    out["id_frog"] = np.where(n2 >= 200, np.sign(r_252) * (neg - pos), np.nan)
    r63v = Rm[-63:]
    v63 = V[-63:]
    with np.errstate(invalid="ignore", divide="ignore"):
        am = np.where((v63 > 0) & ~np.isnan(r63v), np.abs(r63v) / v63, np.nan)
    out["amihud_63"] = _mean(am, 40)
    up = np.sum(np.where(r63v > 0, v63, 0.0), axis=0)
    dnv = np.sum(np.where(r63v < 0, v63, 0.0), axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        out["updown_vol_63"] = np.where((up > 0) & (dnv > 0) & _nan_cols(r63v, 40), np.log(up / dnv), np.nan)

    res = pd.DataFrame(out)
    # RRG of the stock's industry group
    keys = ctx.ids["industry_group"].iloc[col].reset_index(drop=True)
    grp = rrg[rrg["group_type"] == "industry_group"].set_index("group_code") if rrg is not None else None
    qcode = {"Lagging": 0, "Improving": 1, "Weakening": 2, "Leading": 3}
    if grp is not None and len(grp):
        res["rrg_ratio_grp"] = keys.map(grp["rs_ratio"]).to_numpy(dtype=float)
        res["rrg_mom_grp"] = keys.map(grp["rs_mom"]).to_numpy(dtype=float)
        res["rrg_quad_grp"] = keys.map(grp["quadrant"].map(qcode)).to_numpy(dtype=float)
    else:
        res["rrg_ratio_grp"] = res["rrg_mom_grp"] = res["rrg_quad_grp"] = np.nan
    return res
