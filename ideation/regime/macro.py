"""Macro context with release lags (01 §3.3, 02 §1.2). Pure functions of one ``PitView``.

* Monthly CPI / IIP: the snapshot's ``available_from`` is the release date from ``pit/lags.py`` (CPI month_end + 15 d,
  IIP month_end + 45 d). The value used is the latest released period. A value released more than ``STALE_DAYS`` ago is
  NaN (the lake series stop in 2025-12 / 2026-03, so late anchors must not carry a year-old print).
* Foreign daily series close after NSE (INR=X, CL=F, ^TNX): read as of the previous calendar day (strictly earlier date)
  at both ends of the 3-month change, i.e. the one-session lag of 01 §3.3. India VIX and the Nifty 500 are same-day.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from ideation.regime.context import asof_levels

CPI = "MOSPI_CPI_2012_GENERAL_GENERALOVERALL_INFL"
IIP = "MOSPI_IIP_201112_GENERAL_GENERAL__GROWTH"
INR, WTI, US10Y, VIX = "INR=X", "CL=F", "^TNX", "^INDIAVIX"
STALE_DAYS = {CPI: 100, IIP: 130}
CHG_SESSIONS = 63
VIX_PCT_SESSIONS = 756
VIX_PCT_MIN = 500
VIX_CHG_SESSIONS = 21


@dataclass(frozen=True)
class MonthlyPrint:
    period: date | None
    value: float
    available_from: date | None


def latest_monthly(view, code: str) -> MonthlyPrint:
    """Latest released print of a monthly series at the view's anchor."""
    df = view.macro_monthly([code])
    df = df[df["value"].notna() & (df["available_from"] <= view.anchor)]
    if df.empty:
        return MonthlyPrint(None, float("nan"), None)
    row = df.sort_values(["period", "available_from"], kind="stable").iloc[-1]
    stale = (view.anchor - row["available_from"]).days > STALE_DAYS.get(code, 130)
    return MonthlyPrint(row["period"], float("nan") if stale else float(row["value"]), row["available_from"])


def _daily(view, codes: list[str]) -> dict[str, pd.DataFrame]:
    df = view.macro(codes)
    return {c: df[df["code"] == c] for c in codes}


def _chg(lvl: np.ndarray, k: int) -> float:
    if len(lvl) <= k:
        return float("nan")
    a, b = lvl[-1], lvl[-1 - k]
    return float(a / b - 1.0) if np.isfinite(a) and np.isfinite(b) and b != 0 else float("nan")


def macro_features(view, sess_days: np.ndarray) -> dict[str, float]:
    """Macro and volatility inputs of the regime vector at the view's anchor (``sess_days`` ends at the anchor)."""
    d = _daily(view, [INR, WTI, US10Y, VIX])
    inr = asof_levels(d[INR], sess_days, strict=True)
    wti = asof_levels(d[WTI], sess_days, strict=True)
    tnx = asof_levels(d[US10Y], sess_days, strict=True)
    vix = asof_levels(d[VIX], sess_days, strict=False)
    out = {
        "cpi_yoy": latest_monthly(view, CPI).value,
        "iip_yoy": latest_monthly(view, IIP).value,
        "usdinr_3m": _chg(inr, CHG_SESSIONS),
        "wti_3m": _chg(wti, CHG_SESSIONS),
        "us10y_3m_bp": float((tnx[-1] - tnx[-1 - CHG_SESSIONS]) * 100.0) if len(tnx) > CHG_SESSIONS else float("nan"),
        "vix": float(vix[-1]),
        "vix_chg_1m": _chg(vix, VIX_CHG_SESSIONS),
    }
    hist = vix[-VIX_PCT_SESSIONS:]
    ok = hist[np.isfinite(hist)]
    have = len(ok) >= VIX_PCT_MIN and np.isfinite(vix[-1])
    out["vix_pct_3y"] = float((ok <= vix[-1]).mean() * 100.0) if have else float("nan")
    return out
