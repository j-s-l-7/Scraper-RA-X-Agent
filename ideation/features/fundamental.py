"""Point-in-time fundamental features (05 §10.3 P2; 01 §2.3-2.4; 03 §C2 / C7; 02 §1.4). Input: one ``PitView``.

Everything is annual-only (03 §B3). Per anchor A:

* **Facts.** ``view.fundamentals('A', ids)`` already returns only rows with ``available_from <= A``. That date was
  fixed in the snapshot by ``lags.fact_available_from`` = ``max(statutory deadline + 1, matched results event + 1)``
  (later wins). The builder re-derives nothing; it asserts that nothing it used is later than A.
* **Basis rule (F1).** Per (company, FY) use consolidated (C) when any C row for that FY is available at A, else
  standalone (S). Never ``company.has_consolidated`` (today's flag). A growth rate, 3Y mean or vol that spans a C/S
  switch, a layout change or a period gap other than 12 months is NaN. A balance-sheet average ``avg(X)`` falls back
  to ``X_t`` in the same situations (no cross-basis arithmetic).
* **Valuation (03 §C2).** ``mcap_pit_cr`` comes from the universe member frame (hierarchy A → B1 → B2), never from
  a vendor price table and never from price × EPS (03 §B4). ``ebitda_ev = OP / (mcap + Borrowings − Cash +
  NCI)``; non-positive EV or mcap gives NaN.
* **Growth rule (03 §C7 / 02 §4.1).** Growth needs the prior FY, a positive base (EPS: ``max(|EPS_{t-1}|, 0.5)``) and
  is NaN for FYs ending within 12 months after the stitched listing date. A listing date equal to the first bhav
  session means "listed earlier" and carries no rule. Identical extreme growth (> 500 %) in two consecutive FYs sets
  ``growth_flag`` (06 item 3); the value is kept. 02 §4.1's "scale the difference by total assets when the prior is
  ≤ 0" is not used: ``eps_g1`` already has the 0.5 floor on the denominator (01 §2.3).
* **Layouts (F10).** ``fin_layout``: Revenue and Financing Profit both present for the current FY. ``fin_other``: sector
  = Financial Services and not fin_layout (today's sector label: disclosed residual bias). Columns not applicable to
  the layout are NaN here; ``transform.na_layout_frame`` marks them for R1.
* **Coverage.** ``feature_complete`` = share of non-missing *applicable, complete-flagged core* features whose
  history need fits the data horizon at A (``need_fy`` ≤ number of broad FYs available by A, 02 §4.1). A name that
  lacks history that other names have counts as missing. ``c_i`` (02 §1.4) = ``feature_complete × min(1, FYs
  available / 3)``.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from ideation.features.registry import Registry
from ideation.pit.contract import LeakageError

if TYPE_CHECKING:
    from ideation.pit.contract import PitView

SOURCE = "features_fundamental.yaml"
HISTORY_YEARS = 8  # panel window; the longest definition (eps_g5 / rev_g5) needs 6 FYs
IDENTITY_COLS = (
    "anchor", "security_id", "company_id", "sector", "industry_group", "layout", "basis", "fy_end", "fy_age_days",
    "n_fy_hist", "mcap_cr", "mcap_src", "mcap_flag",
)  # fmt: skip
FLAG_COLS = ("neg_ebitda", "growth_flag")
META_COLS = ("feature_complete", "c_i", "n_feat_applicable", "feature_available_from")


def metric_ids(reg: Registry, metric: pd.DataFrame, source: str = SOURCE) -> dict[str, int]:
    """alias → metric_id for the lines this file's features use, validated against the catalog."""
    used = {a for n in reg.names(source=source) for a in reg.features[n].lines}
    return {a: i for a, i in reg.validate_metrics(metric).items() if a in used}


def feasible_fy(anchor: date, first_fy_end: date) -> int:
    """Broad annual FYs (Mar year-ends from ``first_fy_end``) whose results are statutorily due by ``anchor``."""
    n, y = 0, first_fy_end.year
    while date(y, 3, 31) + timedelta(days=61) <= anchor:
        n += 1
        y += 1
    return n


def _panel(view: PitView, ids: dict[str, int], companies: np.ndarray, years: int) -> pd.DataFrame:
    """One row per (company, FY) on the resolved basis; columns = line aliases + basis, pe, avail."""
    id2alias = {v: k for k, v in ids.items()}
    f = view.fundamentals("A", sorted(ids.values()))
    f = f[f["company_id"].isin(companies)]
    pe = pd.to_datetime(f["period_end"])
    f = f.assign(pe=pe, af=pd.to_datetime(f["available_from"]))
    f = f[f["pe"] >= pd.Timestamp(view.anchor) - pd.DateOffset(years=years)]
    keys = ["company_id", "basis", "metric_id", "pe"]
    if f.duplicated(keys).any():  # conflicting restatements: the later-available row wins
        f = f.sort_values("af", kind="stable").drop_duplicates(keys, keep="last")
    f = f.assign(is_c=(f["basis"] == "C").astype("int8"))
    has_c = f.groupby(["company_id", "pe"])["is_c"].transform("max")
    f = f[f["is_c"] == has_c]  # C where any C row exists for the FY, else S
    f = f.assign(alias=f["metric_id"].map(id2alias))
    wide = f.set_index(["company_id", "pe", "alias"])["value"].unstack("alias")
    wide = wide.reindex(columns=list(ids))
    meta = f.groupby(["company_id", "pe"]).agg(is_c=("is_c", "max"), avail=("af", "max"))
    wide["basis"] = np.where(meta["is_c"].reindex(wide.index).to_numpy() == 1, "C", "S")
    wide["avail"] = meta["avail"].reindex(wide.index)
    out = wide.reset_index().sort_values(["company_id", "pe"], kind="stable").reset_index(drop=True)
    anyline = out[["sales", "revenue", "np"]].notna().any(axis=1)
    return out[anyline].reset_index(drop=True)


def _listing_dates(view: PitView) -> pd.Series:
    """Stitched listing date per company (earliest security ``valid_from``); NaT when it is the first bhav session."""
    sec = view.securities()
    sec = sec[sec["company_id"].notna()]
    first = pd.to_datetime(sec.groupby(sec["company_id"].astype(int))["valid_from"].min())
    cal0 = pd.Timestamp(view.calendar.good[0].astype("datetime64[D]"))
    return first.where(first > cal0)


def _std0(*cols: pd.Series) -> pd.Series:
    return pd.Series(np.std(np.column_stack([c.to_numpy(float) for c in cols]), axis=1, ddof=0), index=cols[0].index)


def _fy_features(p: pd.DataFrame, listing: pd.Series, reg: Registry) -> pd.DataFrame:
    """Per-FY derived columns (levels, growth, rolling 3Y/5Y) for every (company, FY) row of the panel."""
    cl = reg.cleaning
    g = p.groupby("company_id", sort=False)
    mi = p["pe"].dt.year * 12 + p["pe"].dt.month
    fin = (p["revenue"].notna() & p["fin_profit"].notna()).astype(int)
    p = p.assign(_mi=mi, _fin=fin)
    g = p.groupby("company_id", sort=False)
    consec = (p["_mi"] - g["_mi"].shift(1)) == int(cl["fy_gap_months"])
    same_basis = p["basis"] == g["basis"].shift(1)
    link_b = (consec & same_basis).fillna(False)  # balance-sheet averages
    link = (link_b & (p["_fin"] == g["_fin"].shift(1))).fillna(False)  # growth / rolling stats
    link1 = link.groupby(p["company_id"], sort=False).shift(1).fillna(False).astype(bool)
    d: dict[str, pd.Series] = {}

    def prev(c: str, k: int = 1) -> pd.Series:
        return g[c].shift(k)

    eq = p["eqcap"] + p["reserves"]
    p = p.assign(_eq=eq)
    g = p.groupby("company_id", sort=False)

    def avg(c: str) -> pd.Series:
        pv = g[c].shift(1)
        return pd.Series(np.where(link_b & pv.notna(), (p[c] + pv) / 2, p[c]), index=p.index)

    avg_eq, avg_ta = avg("_eq"), avg("ta")
    avg_eq = avg_eq.where(avg_eq > 0)
    d["roe"] = p["np_eps"] / avg_eq
    d["roe_norm"] = p["np_exex"] / avg_eq
    d["roa"] = p["np"] / avg_ta.where(avg_ta > 0)
    ic = p["_eq"] + p["borr"] - p["cash"]
    p = p.assign(_ic=ic)
    g = p.groupby("company_id", sort=False)
    avg_ic = avg("_ic")
    lo, hi = cl["tax_clip"]
    nopat = (p["op"] - p["dep"]) * (1 - p["tax_pct"].clip(lo, hi) / 100)
    d["roic"] = nopat / avg_ic.where(avg_ic > 0)
    d["opm"] = p["op"] / p["sales"].where(p["sales"] > 0)

    rev = p["revenue"].where(p["_fin"] == 1, p["sales"])
    p = p.assign(_rev=rev)
    g = p.groupby("company_id", sort=False)

    def pct_growth(c: str) -> pd.Series:
        pv = g[c].shift(1)
        return (p[c] / pv - 1).where(link & (p[c] > 0) & (pv > 0))

    g1 = {"rev_g1": pct_growth("_rev"), "ta_g1": pct_growth("ta"), "dep_g1": pct_growth("deposits")}
    pv_eps = g["eps"].shift(1)
    g1["eps_g1"] = ((p["eps"] - pv_eps) / np.maximum(pv_eps.abs(), float(cl["eps_floor"]))).where(
        link & p["eps"].notna() & pv_eps.notna()
    )
    lst = p["company_id"].map(listing)
    ipo = lst.notna() & (p["pe"] > lst) & (p["pe"] <= lst + pd.DateOffset(months=int(cl["ipo_growth_months"])))
    for k, v in g1.items():
        d[k] = v.where(~ipo)

    p = p.assign(**{k: d[k] for k in ("rev_g1", "eps_g1")}, _roe=d["roe"], _roic=d["roic"], _opm=d["opm"])
    g = p.groupby("company_id", sort=False)

    def lag(c: str, k: int) -> pd.Series:
        return g[c].shift(k)

    def mean_k(c: str, k: int) -> pd.Series:
        s = p[c].copy()
        for i in range(1, k):
            s = s + lag(c, i)
        return s / k

    chain3 = link & link1
    d["eps_g3"], d["rev_g3"] = mean_k("eps_g1", 3), mean_k("rev_g1", 3)
    d["eps_g5"], d["rev_g5"] = mean_k("eps_g1", 5), mean_k("rev_g1", 5)
    d["roe_3y"] = mean_k("_roe", 3).where(chain3)
    d["roic_3y"] = mean_k("_roic", 3).where(chain3)
    d["roe_vol3"] = _std0(p["_roe"], lag("_roe", 1), lag("_roe", 2)).where(chain3)
    d["roic_vol3"] = _std0(p["_roic"], lag("_roic", 1), lag("_roic", 2)).where(chain3)
    d["opm_vol3"] = _std0(p["_opm"], lag("_opm", 1), lag("_opm", 2)).where(chain3)
    d["ccc_chg"] = (p["ccc"] - prev("ccc")).where(link)
    d["cfo_np"] = p["cfo"] / p["np"].where(p["np"] > 0)
    d["payout"] = p["payout_pct"] / 100.0
    d["capex_int"] = -p["capex"] / p["sales"].where(p["sales"] > 0)
    d["fin_margin"] = p["fin_margin_pct"] / 100.0
    d["cost_ratio"] = p["expenses"] / (p["revenue"] + p["other_income"]).where((p["revenue"] + p["other_income"]) > 0)
    d["lev"] = p["ta"] / p["_eq"].where(p["_eq"] > 0)
    big = float(cl["extreme_growth"])
    flag = pd.Series(False, index=p.index)
    for c in ("rev_g1", "eps_g1"):
        x, x1 = p[c], lag(c, 1)
        flag |= (x > big) & (x1 > big) & np.isclose(x, x1, rtol=1e-6)
    d["growth_flag"] = flag
    out = pd.DataFrame(d, index=p.index)
    return out.assign(eq=p["_eq"], _fin=p["_fin"])


def build_fundamental(
    view: PitView, members: pd.DataFrame, reg: Registry, ids: dict[str, int] | None = None
) -> pd.DataFrame:
    """Features for the securities in ``members`` (columns ``security_id``, ``company_id``, ``mcap_pit_cr``,
    ``mcap_src``, ``mcap_flag``) at ``view.anchor``. One row per security, sorted by ``security_id``."""
    anchor, cl = view.anchor, reg.cleaning
    m = members[members["company_id"].notna()].copy()
    m["company_id"] = m["company_id"].astype("int64")
    m = m.sort_values("security_id", kind="stable").reset_index(drop=True)
    ids = ids or metric_ids(reg, view.metric(), SOURCE)
    p = _panel(view, ids, m["company_id"].unique(), HISTORY_YEARS)
    listing = _listing_dates(view)
    d = _fy_features(p, listing, reg)
    full = p.join(d)
    n_fy = p.groupby("company_id")["pe"].size().rename("n_fy_hist")
    last_avail = p.groupby("company_id")["avail"].max().rename("feature_available_from")
    cur = full.groupby("company_id", sort=False).tail(1).set_index("company_id")
    cur = cur.join(n_fy).join(last_avail)
    cur = cur.drop(columns=["avail"])
    com = view.company().set_index("company_id")[["sector", "industry_group"]]
    out = m[["security_id", "company_id", "mcap_pit_cr", "mcap_src", "mcap_flag"]].rename(
        columns={"mcap_pit_cr": "mcap_cr"}
    )
    out = out.join(cur, on="company_id").join(com, on="company_id")
    out["fy_end"] = out["pe"]
    out["fy_age_days"] = (pd.Timestamp(anchor) - out["pe"]).dt.days
    stale = out["fy_age_days"] > int(cl["max_fy_age_days"])
    out["layout"] = np.where(
        out["_fin"] == 1, "fin_layout", np.where(out["sector"] == "Financial Services", "fin_other", "core")
    )
    out.loc[out["pe"].isna() | stale, "layout"] = None
    out = _valuation(out, reg)
    out = _apply_layouts(out, reg, stale | out["pe"].isna())
    out = _completeness(out, reg, anchor)
    out["anchor"] = anchor
    cols = [*IDENTITY_COLS, *reg.names(source=SOURCE), *FLAG_COLS, *META_COLS]
    out = out.reindex(columns=cols)
    mx = out["feature_available_from"].max()
    if pd.notna(mx) and mx > pd.Timestamp(anchor):
        raise LeakageError(f"fundamental features: feature_available_from {mx} > anchor {anchor}")
    for c in ("fy_end", "feature_available_from"):  # python dates, like universe_member / labels (date32 in parquet)
        out[c] = out[c].map(lambda x: None if pd.isna(x) else x.date()).astype(object)
    return out.sort_values("security_id", kind="stable").reset_index(drop=True)


def _valuation(o: pd.DataFrame, reg: Registry) -> pd.DataFrame:
    """Yield-form valuation columns from ``mcap_cr`` and the current-FY lines; leverage; size."""
    mc = o["mcap_cr"].where(o["mcap_cr"] > 0)
    ev = mc + o["borr"] - o["cash"] + o["nci"].fillna(0.0)
    o["sp"] = o["sales"] / mc
    o["ep"] = o["np_eps"] / mc
    o["fcf_yield"] = o["fcf"] / mc
    o["bp"] = o["eq"].where(o["eq"] > 0) / mc
    o["ebitda_ev"] = o["op"] / ev.where(ev > 0)
    o["size"] = np.log(mc)
    nd = o["borr"] - o["cash"]
    ok = o["op"] > 0
    o["nd_ebitda"] = (nd / o["op"]).where(ok)
    o["neg_ebitda"] = ((~ok) & (nd > 0) & o["op"].notna()).astype(float)
    fresh = o["fy_age_days"] <= int(reg.cleaning["max_fy_age_days"])
    p99 = o.loc[ok & (o["layout"] == "core") & fresh, "nd_ebitda"].quantile(0.99)
    o["nd_ebitda"] = o["nd_ebitda"].where(~(o["neg_ebitda"] == 1), p99)
    return o


def _apply_layouts(o: pd.DataFrame, reg: Registry, dead: pd.Series) -> pd.DataFrame:
    for name in reg.names(source=SOURCE):
        f = reg.features[name]
        ok = o["layout"].isin(f.layouts) & ~dead
        o[name] = o[name].where(ok) if name in o else np.nan
    # a FY older than max_fy_age_days (or no FY) gives no features and no flags
    for c in FLAG_COLS:
        o[c] = o[c].where(~dead) if c in o else np.nan
    o["growth_flag"] = o["growth_flag"].fillna(False).astype(bool) if "growth_flag" in o else False
    return o


def _completeness(o: pd.DataFrame, reg: Registry, anchor: date) -> pd.DataFrame:
    horizon = feasible_fy(anchor, reg.cleaning["fy_data_start"])
    num = pd.Series(0.0, index=o.index)
    den = pd.Series(0.0, index=o.index)
    for name in reg.names(source=SOURCE):
        f = reg.features[name]
        if not f.complete or f.need_fy > horizon:
            continue
        app = o["layout"].isin(f.layouts)
        den += app.astype(float)
        num += (app & o[name].notna()).astype(float)
    o["n_feat_applicable"] = den
    o["feature_complete"] = (num / den).where(den > 0)
    o["c_i"] = o["feature_complete"] * np.minimum(1.0, o["n_fy_hist"].fillna(0) / 3.0)
    return o


def feature_complete_series(feats: pd.DataFrame) -> pd.Series:
    """``feature_complete`` indexed by ``security_id`` (the shape ``universe.build.build_universe`` expects)."""
    return feats.set_index("security_id")["feature_complete"]


def summary(feats: pd.DataFrame) -> dict[str, Any]:
    return {"n": len(feats), "feature_complete_mean": float(feats["feature_complete"].mean())}
