"""Fundamental surprise WITHOUT analysts, as an outcome (05 §5.2). Post-anchor by design: reads facts that become
available after the anchor, from a LATE view, and says so in the row (``extra.restated``).

* Annual panel per (company, FY): basis rule F1 (consolidated when any C row exists for the FY, else standalone;
  decided on the late view, disclosed), value = latest-available (restated) figure, ``first_avail`` = the EARLIEST
  ``available_from`` of the lines of that FY, i.e. when it first became usable. PIT at anchor A:
  FY(Y) = the latest FY with ``first_avail <= A`` (and not older than ~13 months, else the name has no current FY);
  FY(Y+1) counts only if ``first_avail <= A + 12M + 60 d``.
* 1Y ``fundamental``: ``out_fs_sales = g_sales(Y+1) - CAGR3_sales(Y-3 -> Y)``, same for PAT (PAT > 0 at both CAGR ends,
  base PAT(Y) > 0, else NaN); ``out_accel = 1[out_fs_sales > 0]``. A growth or CAGR spanning a basis switch or a period
  gap other than 12 months is NaN. Values are winsorised at +-5 (500 pp).
* 3M ``fundamental_q``: quarterly sales YoY of the next quarter minus the median of the YoY of the latest 4 available
  quarters (at least 2 valid). Stored in ``out_fs_sales``. Report-only; coverage is thin until 2025 (03 §B3).
* :func:`cmh_accel` is the Cochran-Mantel-Haenszel test (stratified by anchor) that ``out_accel`` is higher in a top
  set than in a bottom set (05 §5.2).
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from ideation.pit.contract import PitView

LINES = {"sales": "Sales", "revenue": "Revenue", "np": "Net Profit"}
SECTION = {"A": "profit-loss", "Q": "quarters"}
WINSOR = 5.0
STALE_DAYS = 550
OUT_COLS = ["anchor", "security_id", "horizon", "kind", "n_events", "out_fs_sales", "out_fs_pat", "out_accel", "extra"]


def metric_ids(metric: pd.DataFrame, period_type: str = "A") -> dict[str, int]:
    out = {}
    for alias, name in LINES.items():
        hit = metric[(metric["section"] == SECTION[period_type]) & (metric["name"] == name)]["metric_id"]
        if hit.nunique() != 1:
            raise ValueError(f"metric {SECTION[period_type]}/{name} resolves to {hit.nunique()} ids")
        out[alias] = int(hit.iloc[0])
    return out


def build_panel(view: PitView, period_type: str) -> pd.DataFrame:
    """Long panel: company_id, mi (year*12+month of the period end), pe, basis, rev, np, first_avail. ``view`` is a
    late view; callers slice by availability per anchor."""
    ids = metric_ids(view.metric(), period_type)
    f = view.fundamentals(period_type, sorted(ids.values()))
    alias = {v: k for k, v in ids.items()}
    f = f.assign(pe=pd.to_datetime(f["period_end"]), af=pd.to_datetime(f["available_from"]),
                 alias=f["metric_id"].map(alias))  # fmt: skip
    f = f.sort_values("af", kind="stable")
    keys = ["company_id", "basis", "alias", "pe"]
    first = f.groupby(keys)["af"].min().rename("first_af")
    f = f.drop_duplicates(keys, keep="last").join(first, on=keys)  # latest value, earliest availability
    f = f.assign(is_c=(f["basis"] == "C").astype("int8"))
    f = f[f["is_c"] == f.groupby(["company_id", "pe"])["is_c"].transform("max")]
    wide = f.set_index(["company_id", "pe", "alias"])["value"].unstack("alias").reindex(columns=list(ids))
    avail = f.groupby(["company_id", "pe"]).agg(first_avail=("first_af", "min"), is_c=("is_c", "max"))
    out = wide.join(avail).reset_index()
    out["basis"] = np.where(out["is_c"] == 1, "C", "S")
    out["rev"] = out["sales"].fillna(out["revenue"])
    out["mi"] = out["pe"].dt.year * 12 + out["pe"].dt.month - 1
    out = out.sort_values(["company_id", "mi", "first_avail"], kind="stable")
    out = out.drop_duplicates(["company_id", "mi"], keep="first")  # one period per month: the earliest-available row
    return out[["company_id", "mi", "pe", "basis", "rev", "np", "first_avail"]]


def _pick(panel: pd.DataFrame, cid: pd.Series, mi: pd.Series | np.ndarray, tag: str) -> pd.DataFrame:
    """Panel rows at (company, mi) aligned with ``cid``; columns suffixed ``tag``."""
    idx = pd.MultiIndex.from_arrays([cid.to_numpy(), np.asarray(mi)])
    p = panel.set_index(["company_id", "mi"])
    got = p.reindex(idx)[["basis", "rev", "np", "first_avail"]]
    got.index = cid.index
    return got.add_suffix(tag)


def _growth(num: pd.Series, den: pd.Series, ok: pd.Series) -> pd.Series:
    return (num / den - 1).where(ok & (den > 0))


def _latest_at(panel: pd.DataFrame, anchor: date, stale_days: int) -> pd.DataFrame:
    a = pd.Timestamp(anchor)
    p = panel[(panel["first_avail"] <= a) & (panel["pe"] >= a - pd.Timedelta(days=stale_days))]
    return p.groupby("company_id").tail(1)[["company_id", "mi", "pe"]].reset_index(drop=True)


def build_fundamental(panel: pd.DataFrame, members: pd.DataFrame, anchor: date) -> pd.DataFrame:
    """1Y annual rows (kind 'fundamental') for the members (security_id, company_id)."""
    a = pd.Timestamp(anchor)
    cutoff = a + pd.DateOffset(months=12) + pd.Timedelta(days=60)
    cur = _latest_at(panel, anchor, STALE_DAYS)
    cur = cur[cur["company_id"].isin(members["company_id"].dropna().astype("int64"))].reset_index(drop=True)
    cid = cur["company_id"]
    pts = {k: _pick(panel, cid, cur["mi"] + 12 * k, f"_{k}") for k in (-3, 0, 1)}
    p0, pm3, p1 = pts[0], pts[-3], pts[1]
    ok_next = (p1["first_avail_1"] <= cutoff) & (p1["basis_1"] == p0["basis_0"])
    ok_cagr = pm3["basis_-3"] == p0["basis_0"]
    s_g = _growth(p1["rev_1"], p0["rev_0"], ok_next)
    s_cagr = ((p0["rev_0"] / pm3["rev_-3"]) ** (1 / 3) - 1).where(ok_cagr & (pm3["rev_-3"] > 0) & (p0["rev_0"] > 0))
    fs_sales = (s_g - s_cagr).clip(-WINSOR, WINSOR)
    pat_ok = ok_cagr & (pm3["np_-3"] > 0) & (p0["np_0"] > 0)
    p_g = _growth(p1["np_1"], p0["np_0"], ok_next)
    p_cagr = ((p0["np_0"] / pm3["np_-3"]) ** (1 / 3) - 1).where(pat_ok)
    fs_pat = (p_g - p_cagr).clip(-WINSOR, WINSOR)
    out = pd.DataFrame({"company_id": cid, "out_fs_sales": fs_sales, "out_fs_pat": fs_pat, "fy": cur["pe"].dt.date})
    out = out.dropna(subset=["out_fs_sales", "out_fs_pat"], how="all")
    mem = members[members["company_id"].notna()][["security_id", "company_id"]].copy()
    mem["company_id"] = mem["company_id"].astype("int64")
    out = mem.merge(out, on="company_id", how="inner")
    out["out_accel"] = (out["out_fs_sales"] > 0).astype("Int16").where(out["out_fs_sales"].notna())
    out["extra"] = [{"fy": str(f), "restated": "as restated at the snapshot date (05 §5.2)"} for f in out["fy"]]
    out = out.assign(anchor=anchor, horizon="1Y", kind="fundamental", n_events=1)
    return out[OUT_COLS].sort_values("security_id").reset_index(drop=True)


def build_fundamental_q(panel: pd.DataFrame, members: pd.DataFrame, anchor: date) -> pd.DataFrame:
    """3M quarterly rows (kind 'fundamental_q'): next-quarter YoY sales growth minus the median YoY of the latest 4."""
    a = pd.Timestamp(anchor)
    cutoff = a + pd.DateOffset(months=3)
    cur = _latest_at(panel, anchor, 130)
    cur = cur[cur["company_id"].isin(members["company_id"].dropna().astype("int64"))].reset_index(drop=True)
    cid = cur["company_id"]

    def yoy(shift: int, need_avail_by: pd.Timestamp | None = None) -> pd.Series:
        x = _pick(panel, cid, cur["mi"] + shift, "_x")
        b = _pick(panel, cid, cur["mi"] + shift - 12, "_b")
        ok = x["basis_x"] == b["basis_b"]
        if need_avail_by is not None:
            ok &= x["first_avail_x"] <= need_avail_by
        else:
            ok &= x["first_avail_x"] <= a
        return _growth(x["rev_x"], b["rev_b"], ok)

    nxt = yoy(3, cutoff)
    trail = pd.concat([yoy(-3 * k) for k in range(4)], axis=1)
    med = trail.median(axis=1).where(trail.notna().sum(axis=1) >= 2)
    fs = (nxt - med).clip(-WINSOR, WINSOR)
    out = pd.DataFrame({"company_id": cid, "out_fs_sales": fs}).dropna()
    mem = members[members["company_id"].notna()][["security_id", "company_id"]].copy()
    mem["company_id"] = mem["company_id"].astype("int64")
    out = mem.merge(out, on="company_id", how="inner")
    out["out_fs_pat"] = np.nan
    out["out_accel"] = (out["out_fs_sales"] > 0).astype("Int16")
    out["extra"] = [{"restated": "as restated at the snapshot date (05 §5.2)"}] * len(out)
    out = out.assign(anchor=anchor, horizon="3M", kind="fundamental_q", n_events=1)
    return out[OUT_COLS].sort_values("security_id").reset_index(drop=True)


def cmh_accel(df: pd.DataFrame, top: pd.Series, bottom: pd.Series) -> dict[str, Any]:
    """CMH test, strata = anchor: is ``out_accel`` higher in ``top`` than in ``bottom`` (boolean Series aligned with
    ``df``)? Returns the Mantel-Haenszel common odds ratio, the one-sided z and p."""
    from scipy.stats import norm

    d = df.assign(
        _g=np.where(top.reindex(df.index).fillna(False), 1, np.where(bottom.reindex(df.index).fillna(False), 0, -1))
    )
    d = d[(d["_g"] >= 0) & d["out_accel"].notna()]
    num = den = sa = sv = 0.0
    for _, g in d.groupby("anchor"):
        a = float(((g["_g"] == 1) & (g["out_accel"] == 1)).sum())
        b = float(((g["_g"] == 1) & (g["out_accel"] == 0)).sum())
        c = float(((g["_g"] == 0) & (g["out_accel"] == 1)).sum())
        e = float(((g["_g"] == 0) & (g["out_accel"] == 0)).sum())
        n = a + b + c + e
        if n < 2 or (a + b) == 0 or (c + e) == 0:
            continue
        num += a * e / n
        den += b * c / n
        sa += a - (a + b) * (a + c) / n
        sv += (a + b) * (c + e) * (a + c) * (b + e) / (n**2 * (n - 1))
    z = sa / np.sqrt(sv) if sv > 0 else float("nan")
    return {"mh_odds_ratio": num / den if den > 0 else float("nan"), "z": float(z), "p_one_sided": float(norm.sf(z))}
