"""Join scores, calibration, conformal band, OOD, risk flags and context into one per-name frame (P6 ``predict``)."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from ideation.features.registry import Registry
from ideation.predict.cal import LiveCalibration
from ideation.predict.score import OodGuard, Scored, contributions, neutral_ranks, top_drivers

FIN_LAYOUTS = ("fin_layout", "fin_other")
HIGH_LEVERAGE_Q = 0.90
COOLING_RET = 0.12
PROMOTER_SELL_PP = -5.0


def risk_flags(df: pd.DataFrame) -> list[list[str]]:
    """01 §5 step 3, restricted to what the live columns support (see STATUS for the ones deferred)."""
    lev_cut = df["nd_ebitda"].quantile(HIGH_LEVERAGE_Q) if df["nd_ebitda"].notna().any() else np.inf
    out: list[list[str]] = []
    for r in df.itertuples():
        f: list[str] = []
        if pd.notna(r.nd_ebitda) and r.nd_ebitda > lev_cut:
            f.append("high_leverage_p90")
        if pd.notna(r.cfo_np) and r.cfo_np < 0:
            f.append("negative_cfo_latest_fy")
        if pd.notna(r.prom_chg_1y) and r.prom_chg_1y <= PROMOTER_SELL_PP:
            f.append("promoter_selling_1y")
        if pd.notna(r.max_abs_ret_7d) and r.max_abs_ret_7d > COOLING_RET:
            f.append("cooling_off_7d")
        if r.layout in FIN_LAYOUTS:
            f.append("financial_layout")
        if bool(r.mcap_flag) is True:
            f.append("mcap_methods_disagree")
        out.append(f)
    return out


def regime_misaligned(horizon: str, quadrant: pd.Series, beta: pd.Series, labels: dict[str, str]) -> pd.Series:
    """Tactical: group is Lagging in the RRG. 1Y: Stressed volatility regime with beta > 1.2 (01 §5 conviction)."""
    if horizon == "3M":
        return quadrant.eq("Lagging")
    return (beta > 1.2) & (labels.get("vol") == "Stressed")


def assemble(
    sc: Scored,
    base: pd.DataFrame,
    info: pd.DataFrame,
    cal: LiveCalibration,
    ood: OodGuard,
    reg: Registry,
    labels: dict[str, str],
    rrg: pd.DataFrame,
    chal: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """``base`` = the horizon's input frame (beta/vol/adv columns), ``info`` = ``live.security_info``."""
    f = sc.frame.copy()
    cols_base = ["security_id", "beta_252", "rvol_63", "idio_vol", "mcap_pit_cr", "adv63_lakh", "prom_chg_1y"]
    b = base[[c for c in cols_base if c in base.columns]]
    f = f.merge(b, on="security_id", how="left", validate="1:1")
    inf = info.drop(columns=[c for c in ("mcap_pit_cr", "adv63_lakh") if c in info.columns])
    f = f.merge(inf, on="security_id", how="left", validate="1:1")
    if "prom_chg_1y" not in f.columns:
        f["prom_chg_1y"] = np.nan
    for c in ("nd_ebitda", "cfo_np"):
        if c not in f.columns:
            f[c] = np.nan
    f = f.join(cal.apply(f["p"]))
    s, flag = ood.flags(sc.X.reset_index(drop=True))
    f["ood_score"], f["ood"] = s, flag
    nr = neutral_ranks(f["p"], f[["beta_252", "rvol_63", "idio_vol", "mcap_pit_cr", "adv63_lakh"]])
    f = f.join(nr)
    f = f.rename(columns={"beta_252": "beta", "rvol_63": "vol"})
    flag_cols = [c for c in sc.X.columns if c.startswith("isna_")]
    xc = 1.0 - sc.X[flag_cols].mean(axis=1).to_numpy() if flag_cols else np.ones(len(f))
    if sc.horizon == "1Y":
        f["c_conf"] = f["c_i"].fillna(pd.Series(xc, index=f.index))
        f["completeness"] = f["feature_complete"].fillna(pd.Series(xc, index=f.index))
    else:
        f["c_conf"] = xc
        f["completeness"] = xc
    f["flags"] = risk_flags(f)
    q = rrg[rrg["group_type"] == "industry_group"].set_index("group_code")[["quadrant", "rs_ratio", "rs_mom"]]
    f = f.join(q, on="industry_group")
    f["regime_misaligned"] = regime_misaligned(sc.horizon, f["quadrant"], f["beta"], labels).to_numpy()
    c = contributions(sc)
    f["drivers"] = top_drivers(c, reg) if c is not None else [None] * len(f)
    if c is not None:
        f["contrib_sum_err"] = (c.sum(axis=1).to_numpy() - sc.frame["score"].to_numpy())
    if chal is not None:
        f = f.merge(chal, on="security_id", how="left")
    return f


def to_jsonable(v: Any) -> Any:
    if isinstance(v, (np.floating, float)):
        return None if pd.isna(v) else round(float(v), 6)
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.bool_,)):
        return bool(v)
    if v is pd.NA:
        return None
    return v
