# ruff: noqa: E501
"""Reporting standard of the accuracy lab (E0 a-iii).

Input everywhere: a *scored frame* with columns ``anchor``, ``security_id``, ``score`` and the evaluation label
``ret_total`` (always the raw 1Y total return, whatever the training target was), plus the controls
(``beta_252, rvol_63, idio_vol, mcap_pit_cr, adv63_lakh``). Reported per trial:

* per-anchor rank IC (monthly series) with Newey-West t (lag 11 at 12M horizon / monthly spacing), the
  autocorrelation-based effective N and a stationary-bootstrap CI;
* era series (12-month spacing, June primary, the 11 other phase offsets as a band): mean, ICIR, hit rate,
  iid-bootstrap CI;
* quintile table (mean return by score quintile, averaged over anchors), Spearman monotonicity, Q5-Q1 spread;
* neutralised IC (return residualised per anchor on beta / beta+vol / +size+ADV) and IC within size and ADV terciles;
* Q5 one-way turnover between consecutive anchors; N; minimum detectable IC;
* DSR on the era series at two N families (this lab's trials; and 53 legacy + this lab's), plus an effective-N DSR.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from ideation.eval import dsr as dsr_mod
from ideation.eval.inference import ic_summary, nw_lags
from ideation.eval.metrics import rank_ic, turnover
from ideation.tactical.diagnostics import (
    CONTROLS,
    add_controls,
    ic_series,
    residualize,
    within_bucket_ic,
)

LAG_12M = nw_lags(12, 1)  # 11


def ic_by_anchor(df: pd.DataFrame, score: str = "score", label: str = "ret_total") -> pd.Series:
    return ic_series(df, score, label)


LEAK_ALARM_IC = 0.25
"""Published out-of-sample cross-sectional ICs are ~0.03-0.10 (REFERENCES.md); a mean IC at or above this is a leakage alarm."""


def leak_alarm(mean_ic: float, threshold: float = LEAK_ALARM_IC) -> bool:
    return bool(np.isfinite(mean_ic) and mean_ic >= threshold)


def null_covers_zero(ic: pd.Series, z: float = 2.58) -> bool:
    """True if the 99% normal CI of the mean per-anchor IC (iid across anchors) covers 0: the shuffled-target check."""
    v = pd.Series(ic, dtype=float).dropna()
    se = v.std(ddof=1) / math.sqrt(len(v))
    return bool(abs(v.mean()) <= z * se)


def effective_n(x: pd.Series, lags: int = LAG_12M) -> float:
    """n / (1 + 2 sum_k (1 - k/(L+1)) rho_k), floored at 1 and capped at n (Bartlett weights)."""
    v = pd.Series(x, dtype=float).dropna().to_numpy()
    n = len(v)
    if n < 4:
        return float(n)
    e = v - v.mean()
    den = e @ e
    if den <= 0:
        return float(n)
    s = 0.0
    for k in range(1, min(lags, n - 2) + 1):
        s += 2 * (1 - k / (lags + 1)) * (e[k:] @ e[:-k]) / den
    return float(min(n, max(1.0, n / max(1.0 + s, 1e-9))))


def mde(sd: float, n: int, alpha_z: float = 1.96, power_z: float = 0.84) -> float:
    """Minimum detectable true mean IC (two-sided 5%, 80% power) with n independent eras of sd ``sd``."""
    return float((alpha_z + power_z) * sd / math.sqrt(n)) if n > 0 else float("nan")


def era_summary(ic: pd.Series, month: int = 6, n_boot: int = 10_000) -> dict[str, Any]:
    v = ic.dropna()
    sub = v[[a.month == month for a in v.index]]
    out: dict[str, Any] = {"n_eras": len(sub), "years": [a.year for a in sub.index], "ic_by_era": [float(x) for x in sub]}
    if len(sub) >= 2:
        s = ic_summary(sub, 12, 12, n_boot=n_boot)
        out.update({k: s[k] for k in ("mean", "std", "ir", "pct_positive", "ci90")})
        out["mde_5eras"] = mde(s["std"], 5)
        out["mde_this"] = mde(s["std"], len(sub))
    # phase band over the 12 possible offsets
    band = []
    for m in range(1, 13):
        s_ = v[[a.month == m for a in v.index]]
        if len(s_) >= 3:
            band.append((float(s_.mean()), float((s_ > 0).mean()), float(s_.mean() / s_.std(ddof=1)) if s_.std(ddof=1) > 0 else np.nan))
    if band:
        arr = np.array(band)
        out["band_mean"] = (float(arr[:, 0].min()), float(arr[:, 0].max()))
        out["band_hit"] = (float(arr[:, 1].min()), float(arr[:, 1].max()))
        out["band_icir"] = (float(np.nanmin(arr[:, 2])), float(np.nanmax(arr[:, 2])))
    return out


def monthly_summary(ic: pd.Series, n_boot: int = 10_000) -> dict[str, Any]:
    v = ic.dropna()
    s = ic_summary(v, 12, 1, n_boot=n_boot)
    s["n_eff"] = effective_n(v)
    s["ir_monthly"] = s.pop("ir")
    return s


def quintile_table(df: pd.DataFrame, ret: str = "ret_total") -> dict[str, Any]:
    """Mean raw return by within-anchor score quintile (Q1 lowest), averaged over anchors."""
    rows = []
    for _, g in df.groupby("anchor", sort=True):
        g = g.dropna(subset=["score", ret])
        if len(g) < 50:
            continue
        r = g["score"].rank(method="first", pct=True)
        q = np.minimum(np.ceil(r * 5), 5).astype(int)
        m = g[ret].groupby(q).mean()
        rows.append(m.reindex(range(1, 6)).to_numpy() - g[ret].mean())
    arr = np.array(rows)
    mean_q = arr.mean(axis=0)
    spread = arr[:, 4] - arr[:, 0]
    return {
        "q_mean_excess": [float(x) for x in mean_q],
        "monotone_strict": bool(np.all(np.diff(mean_q) > 0)),
        "n_inversions": int((np.diff(mean_q) < 0).sum()),
        "spearman": float(stats.spearmanr(range(1, 6), mean_q)[0]),
        "q5_q1": float(spread.mean()),
        "q5_q1_pos_share": float((spread > 0).mean()),
    }


def neutral_and_buckets(df: pd.DataFrame, ret: str = "ret_total") -> dict[str, Any]:
    d = add_controls(df)
    out: dict[str, Any] = {}
    for k in ("B", "BV", "BVLS"):
        d[f"_r{k}"] = residualize(d, ret, CONTROLS[k])
        s = ic_series(d, "score", f"_r{k}").dropna()
        out[f"res_{k}"] = float(s.mean())
        out[f"res_{k}_series"] = s
    for tag, by in (("size", "ln_mcap"), ("adv", "ln_adv")):
        w = within_bucket_ic(d, "score", ret, by)
        out[f"{tag}_terciles"] = [float(w[f"b{k}"].mean()) for k in range(3)]
    return out


def top_turnover(df: pd.DataFrame, q: float = 0.2) -> float:
    prev = None
    tos = []
    for _, g in df.groupby("anchor", sort=True):
        g = g.dropna(subset=["score"])
        top = set(g.loc[g["score"].rank(pct=True) > 1 - q, "security_id"])
        if prev is not None:
            tos.append(turnover(prev, top))
        prev = top
    return float(np.nanmean(tos)) if tos else float("nan")


def full_report(df: pd.DataFrame, *, n_boot: int = 10_000) -> dict[str, Any]:
    """Everything of the reporting standard for one scored frame."""
    ic = ic_by_anchor(df)
    out: dict[str, Any] = {"n_anchors": int(ic.notna().sum()), "n_rows": len(df), "ic_series": ic}
    out["monthly"] = monthly_summary(ic, n_boot)
    out["eras"] = era_summary(ic, 6, n_boot)
    out["quintiles"] = quintile_table(df)
    out.update(neutral_and_buckets(df))
    out["turnover_q5"] = top_turnover(df)
    return out


# ── DSR at two N families ────────────────────────────────────────────────────────────────────────────────────
def sharpe(x: pd.Series) -> float:
    v = np.asarray(pd.Series(x).dropna(), dtype=float)
    return float(v.mean() / v.std(ddof=1)) if len(v) > 1 and v.std(ddof=1) > 0 else float("nan")


def dsr_families(era_ic: dict[str, pd.Series], legacy_sr: np.ndarray, n_legacy: int = 53) -> dict[str, dict[str, float]]:
    """DSR of each trial's era IC series: (a) pool = this lab's trials, N = their count; (b) pool = legacy + lab,
    N = ``n_legacy`` + lab count; (c) effective N = 10 independent bets (audit: 50 trials ~ 10 bets) on pool (b)."""
    lab_sr = {k: sharpe(v) for k, v in era_ic.items() if v.dropna().size >= 3}
    arr_lab = np.array([v for v in lab_sr.values() if v == v])
    arr_all = np.concatenate([legacy_sr[~np.isnan(legacy_sr)], arr_lab])
    n_lab = len(era_ic)
    out = {}
    for k, v in era_ic.items():
        v = v.dropna()
        if len(v) < 3 or len(arr_lab) < 2:
            out[k] = {"dsr_lab": float("nan"), "dsr_all": float("nan"), "dsr_eff10": float("nan")}
            continue
        out[k] = {
            "dsr_lab": dsr_mod.dsr_of_series(v, arr_lab, n_trials=n_lab).dsr,
            "dsr_all": dsr_mod.dsr_of_series(v, arr_all, n_trials=n_legacy + n_lab).dsr,
            "dsr_eff10": dsr_mod.dsr_of_series(v, arr_all, n_trials=10).dsr,
        }
    return out


def rank_gauss_series(x: pd.Series) -> pd.Series:
    r = x.rank(method="average")
    return pd.Series(stats.norm.ppf((r - 0.5) / r.notna().sum()), index=x.index)


__all__ = ["dsr_families", "effective_n", "full_report", "mde", "rank_ic"]
