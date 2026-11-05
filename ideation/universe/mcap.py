"""PIT market cap (03 §C2 / 02 §1.3): hierarchy A → B1 → B2, ``mcap_src`` and ``mcap_flag``. Inputs come only
from a ``PitView``. The future split/bonus factors and today's face value are ``basis_conversion`` rows, used only to
put an anchor-date quantity on a consistent share basis.

* **A**: raw close at the anchor (last trade ≤ A) × yfinance shares (latest ``as_of`` available at A, within 400
  days). Shares are divided by the split/bonus/residual-jump factors that took effect in ``(as_of, A]``.
* **B1**: raw close × Π(basis-conversion factors effective after A), i.e. the price on today's basis, ×
  standalone NP / EPS of the latest annual FY available at A. Screener EPS is restated to today's share basis
  (02 §0.4), so both sides are on today's basis. Requires |NP| > ₹1 cr and NP/EPS > 0.
* **B2**: raw close × Equity Capital × 1e7 / FV.
  - FV at A comes from ``issue_size`` when A ≥ 2024-02, and requires no split/bonus in (FY end, A].
  - Otherwise FV is today's (``face_value_now``), and requires no split/bonus in (FY end, today].
  - Equity Capital is the latest annual FY available at A (standalone, else consolidated).

``mcap`` is the first available method. If the available methods disagree by more than 25% (max/min > 1.25), it is
their median instead and ``mcap_flag`` is set. Price/EPS is never used for valuation (03 §B4).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from ideation.pit.contract import PitView

NP_ID, EPS_ID, EQCAP_ID = 21, 22, 24
SHARE_KINDS = ("split", "bonus", "jump")


def _latest_fy(f: pd.DataFrame, metric_id: int, basis: str) -> pd.DataFrame:
    x = f[(f["metric_id"] == metric_id) & (f["basis"] == basis)].dropna(subset=["value"])
    x = x.sort_values(["company_id", "period_end", "available_from"])
    return x.groupby("company_id", as_index=False).tail(1)[["company_id", "period_end", "value"]]


def _prod_between(fac: pd.DataFrame, sec: pd.Series, lo: pd.Series, hi: pd.Series) -> np.ndarray:
    """Π factor over rows of each security with ``lo < eff_date <= hi`` (vectorised; 1 where none or bounds NaN)."""
    out = np.ones(len(sec))
    if fac.empty or len(sec) == 0:
        return out
    left = pd.DataFrame(
        {"_i": np.arange(len(sec)), "security_id": sec.to_numpy(), "_lo": pd.to_datetime(pd.Series(lo.to_list())),
         "_hi": pd.to_datetime(pd.Series(hi.to_list()).where(lambda x: x != date.max, date(2262, 1, 1)))}
    )  # fmt: skip
    right = pd.DataFrame(
        {"security_id": fac["security_id"].to_numpy(), "_d": pd.to_datetime(fac["eff_date"]),
         "_lf": np.log(fac["factor"].to_numpy(float))}
    )  # fmt: skip
    j = left.merge(right, on="security_id", how="inner")
    j = j[(j["_d"] > j["_lo"]) & (j["_d"] <= j["_hi"])]
    if len(j):
        s = j.groupby("_i")["_lf"].sum()
        out[s.index.to_numpy()] = np.exp(s.to_numpy())
    return out


def mcap_pit(view: PitView, members: pd.DataFrame, cfg: dict[str, Any]) -> pd.DataFrame:
    """``members``: security_id, company_id, close (raw, last trade <= anchor), last_trade. Returns one row per member:
    security_id, mcap_a_cr, mcap_b1_cr, mcap_b2_cr, mcap_pit_cr, mcap_src, mcap_flag."""
    anchor = view.anchor
    m = members[["security_id", "company_id", "close"]].copy().reset_index(drop=True)
    m["company_id"] = m["company_id"].astype("Int64")
    past = view.factors(SHARE_KINDS)
    future = view.basis_conversion_factors()
    anchor_s = pd.Series([anchor] * len(m))

    # anchor: yfinance shares
    sh = view.shares()
    lo = anchor - timedelta(days=int(cfg["shares_max_age_days"]))
    sh = sh[sh["as_of"] >= lo].sort_values(["company_id", "as_of"]).groupby("company_id", as_index=False).tail(1)
    m = m.merge(sh[["company_id", "as_of", "shares"]].astype({"company_id": "Int64"}), on="company_id", how="left")
    adj = _prod_between(past, m["security_id"], m["as_of"], anchor_s)
    m["mcap_a_cr"] = m["close"] * (m["shares"] / adj) / 1e7

    # B1 / B2 fundamentals (latest annual FY available at anchor)
    f = view.fundamentals("A", [NP_ID, EPS_ID, EQCAP_ID])
    np_s, eps_s = _latest_fy(f, NP_ID, "S"), _latest_fy(f, EPS_ID, "S")
    b1 = np_s.merge(eps_s, on=["company_id", "period_end"], suffixes=("_np", "_eps"))
    b1 = b1[(b1["value_np"].abs() > float(cfg["b1_min_abs_np_cr"])) & (b1["value_np"] / b1["value_eps"] > 0)]
    b1 = b1.assign(shares_tb_cr=b1["value_np"] / b1["value_eps"])[["company_id", "shares_tb_cr"]]
    m = m.merge(b1.astype({"company_id": "Int64"}), on="company_id", how="left")
    to_today = _prod_between(future, m["security_id"], anchor_s, pd.Series([date.max] * len(m)))
    m["mcap_b1_cr"] = m["close"] * to_today * m["shares_tb_cr"]

    eq = pd.concat([_latest_fy(f, EQCAP_ID, "S").assign(pref=0), _latest_fy(f, EQCAP_ID, "C").assign(pref=1)])
    eq = eq.sort_values(["company_id", "period_end", "pref"], ascending=[True, False, True])
    eq = eq.groupby("company_id", as_index=False).head(1)[["company_id", "period_end", "value"]]
    eq = eq.rename(columns={"period_end": "fy_end", "value": "eqcap_cr"}).astype({"company_id": "Int64"})
    m = m.merge(eq, on="company_id", how="left")
    fv_now = view.face_value_now()[["security_id", "face_value"]].rename(columns={"face_value": "fv_now"})
    m = m.merge(fv_now, on="security_id", how="left")
    if date(2024, 2, 1) <= anchor:
        iz = view.issue_size()
        sec_sym = view.securities()[["security_id", "symbol"]].drop_duplicates()
        iz = iz.merge(sec_sym, on="symbol").sort_values("date").groupby("security_id", as_index=False).tail(1)
        m = m.merge(iz[["security_id", "face_value"]].rename(columns={"face_value": "fv_at"}), on="security_id",
                    how="left")  # fmt: skip
    else:
        m["fv_at"] = np.nan
    since_fy_to_a = _prod_between(past, m["security_id"], m["fy_end"], anchor_s)
    since_a = _prod_between(future, m["security_id"], anchor_s, pd.Series([date.max] * len(m)))
    clean_to_a = np.isclose(since_fy_to_a, 1.0)
    clean_to_today = clean_to_a & np.isclose(since_a, 1.0)
    fv = np.where(m["fv_at"].notna() & clean_to_a, m["fv_at"], np.where(clean_to_today, m["fv_now"], np.nan))
    m["mcap_b2_cr"] = m["close"] * m["eqcap_cr"] * 1e7 / fv / 1e7

    vals = m[["mcap_a_cr", "mcap_b1_cr", "mcap_b2_cr"]].where(lambda x: x > 0)
    m[["mcap_a_cr", "mcap_b1_cr", "mcap_b2_cr"]] = vals
    first = vals.bfill(axis=1).iloc[:, 0]
    src = np.select(
        [vals["mcap_a_cr"].notna(), vals["mcap_b1_cr"].notna(), vals["mcap_b2_cr"].notna()], ["A", "B1", "B2"], None
    )
    ratio = vals.max(axis=1) / vals.min(axis=1)
    flag = (vals.notna().sum(axis=1) >= 2) & (ratio > float(cfg["disagree_ratio"]))
    m["mcap_pit_cr"] = np.where(flag, vals.median(axis=1), first)
    m["mcap_src"] = src
    m["mcap_flag"] = flag.to_numpy()
    cols = ["security_id", "mcap_a_cr", "mcap_b1_cr", "mcap_b2_cr", "mcap_pit_cr", "mcap_src", "mcap_flag"]
    return m[cols]
