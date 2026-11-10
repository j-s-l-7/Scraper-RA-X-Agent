"""Vendor-surprise challenger features and their validity gate (05 §6.3, O5). The ONLY module in the model-building
packages allowed to name the vendor estimate columns (``BAN_ALLOWLIST`` in ``tests/leakage/test_static_ban.py``).

* Reads ``PitView.earnings_vendor()`` (rows with ``available_from = event_date + 1``; the gate on the view admits this
  module's name). The builder additionally refuses to run unless the caller passes ``allow_vendor_estimates=True``,
  which a trial sets through ``trial.config_json.allow_vendor_estimates``.
* Emits only renamed columns: ``f_vsurp_last``, ``f_vbeat_4q``, ``isna_vsurp``. The stored ``surprise`` column is never
  used; surprise is recomputed as ``100*(reported - estimate)/|estimate|`` (a within-row ratio, immune to the
  share-basis drift of 05 §0.1).
* :func:`validity_gate` is a research diagnostic (like outcomes it looks at the announcement window after the event),
  not a feature: Spearman(surprise, ``ear_3d``) over the gate period with a quarter-clustered t statistic.
  ``ear_3d`` is computed locally (P3's ``features/events.py`` is not merged yet): sum of the stock's split/bonus/jump/
  dividend-adjusted log-returns over sessions [s(e)-1, s(e)+1] minus the equal-weight mean of the same window over the
  other securities of its industry group (today's label: disclosed). Events are capped before the sealed tactical
  window (05 §8).
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from ideation.features.registry import Registry
from ideation.pit.contract import LeakageError

if TYPE_CHECKING:
    from ideation.pit.contract import PitView

SOURCE = "features_vendor.yaml"
EST, REP = "eps_estimate", "eps_reported"
IDENTITY_COLS = ("anchor", "security_id", "company_id")


class VendorNotAllowedError(PermissionError):
    """The caller did not set ``allow_vendor_estimates`` (05 §6.3: challenger trials only)."""


def surprise_pct(est: pd.Series, rep: pd.Series) -> pd.Series:
    """Recomputed surprise in percent; NaN when the estimate is missing or exactly zero."""
    est, rep = est.astype("float64"), rep.astype("float64")
    safe = est.where(est != 0)
    return 100.0 * (rep - est) / safe.abs()


def _events(view: PitView) -> pd.DataFrame:
    df = view.earnings_vendor()
    df = df[df[EST].notna() & df[REP].notna()]
    df = df.assign(
        event_date=pd.to_datetime(df["event_date"]), surp=surprise_pct(df[EST], df[REP]), beat=(df[REP] > df[EST])
    )
    return df.drop_duplicates(["company_id", "event_date"], keep="last").sort_values(["company_id", "event_date"])


def build_vendor(
    view: PitView, members: pd.DataFrame, reg: Registry, params: dict[str, Any], *, allow_vendor_estimates: bool
) -> pd.DataFrame:
    """One row per member security with ``f_vsurp_last``, ``f_vbeat_4q`` and ``isna_vsurp`` (raw; NaN means none)."""
    if allow_vendor_estimates is not True:
        raise VendorNotAllowedError("vendor estimates need trial.config_json.allow_vendor_estimates = true (05 §6.3)")
    a = pd.Timestamp(view.anchor)
    ev = _events(view)
    if len(ev) and ev["event_date"].max() >= a:  # available_from = event_date + 1 <= anchor
        raise LeakageError(f"vendor event on/after the anchor {view.anchor}")
    last_lo = a - pd.Timedelta(days=int(params["last_window_days"]))
    beat_lo = a - pd.Timedelta(days=int(params["beat_window_days"]))
    clip = float(params["clip_pct"])
    last = ev[ev["event_date"] > last_lo].drop_duplicates("company_id", keep="last").set_index("company_id")
    recent = ev[ev["event_date"] > beat_lo].groupby("company_id").tail(int(params["beat_n"]))
    beats = recent.groupby("company_id")["beat"].mean()
    mem = members[members["company_id"].notna()][["security_id", "company_id"]].copy()
    mem["company_id"] = mem["company_id"].astype("int64")
    mem["f_vsurp_last"] = mem["company_id"].map(last["surp"].clip(-clip, clip)).astype("float64")
    mem["f_vbeat_4q"] = mem["company_id"].map(beats.astype("float64")).astype("float64")
    mem["isna_vsurp"] = (~mem["company_id"].isin(last.index)).astype("int8")
    mem.insert(0, "anchor", view.anchor)
    cols = [*IDENTITY_COLS, *reg.names(source=SOURCE), "isna_vsurp"]
    return mem[cols].sort_values("security_id").reset_index(drop=True)


# ── validity gate ────────────────────────────────────────────────────────────────────────────────────────────
def ear_3d_events(view: PitView, ev: pd.DataFrame, lo: date, hi: date) -> pd.DataFrame:
    """Events with ``ear_3d`` (see module docstring). ``view`` is a late-anchor view: this is not a feature."""
    px = view.prices_adj()
    px = px[["security_id", "date", "close_adj"]]
    sess = np.array(sorted(pd.to_datetime(px["date"]).unique()), dtype="datetime64[ns]")
    sec_ids = np.sort(px["security_id"].unique())
    di = np.searchsorted(sess, pd.to_datetime(px["date"]).to_numpy(dtype="datetime64[ns]"))
    si = np.searchsorted(sec_ids, px["security_id"].to_numpy())
    logp = np.full((len(sess), len(sec_ids)), np.nan)
    logp[di, si] = np.log(px["close_adj"].to_numpy())

    smap = view.securities()
    smap = smap[smap["company_id"].notna()].assign(
        vf=pd.to_datetime(smap["valid_from"]),
        vt=pd.to_datetime(smap["valid_to"]),
        company_id=lambda d: d["company_id"].astype("int64"),
    )
    comp = view.company().set_index("company_id")["industry_group"]
    grp_of_sec = smap.drop_duplicates("security_id").set_index("security_id")["company_id"].map(comp)
    grp_codes = pd.Categorical(grp_of_sec.reindex(sec_ids)).codes  # -1 = unknown group

    ev = ev[(ev["event_date"] >= pd.Timestamp(lo)) & (ev["event_date"] <= pd.Timestamp(hi))]
    ie = np.searchsorted(sess, ev["event_date"].to_numpy(dtype="datetime64[ns]"))  # first session >= event_date
    ok = (ie >= 2) & (ie + 1 < len(sess))
    ev, ie = ev[ok], ie[ok]
    ev = ev.assign(i0=ie - 2, i1=ie + 1)
    ev = ev[pd.to_datetime(sess[ev["i1"].to_numpy()]) <= pd.Timestamp(hi)]
    # security of the company valid at the event date
    m = ev.merge(smap[["company_id", "security_id", "vf", "vt"]], on="company_id", how="inner")
    m = m[(m["vf"] <= m["event_date"]) & (m["event_date"] <= m["vt"])].drop_duplicates(["company_id", "event_date"])
    out_rows = []
    for (i0, i1), g in m.groupby(["i0", "i1"]):
        r = logp[i1] - logp[i0]
        valid = ~np.isnan(r) & (grp_codes >= 0)
        sums = np.bincount(grp_codes[valid], weights=r[valid], minlength=grp_codes.max() + 2)
        cnts = np.bincount(grp_codes[valid], minlength=grp_codes.max() + 2)
        pos = np.searchsorted(sec_ids, g["security_id"].to_numpy())
        own = r[pos]
        gc = grp_codes[pos]
        own_in = ~np.isnan(own) & (gc >= 0)
        n_oth = cnts[gc] - own_in
        comp_ret = np.where(n_oth >= 3, (sums[gc] - np.where(own_in, own, 0.0)) / np.maximum(n_oth, 1), np.nan)
        out_rows.append(g.assign(ear_3d=own - comp_ret, n_peers=n_oth))
    res = pd.concat(out_rows, ignore_index=True)
    return res[res["ear_3d"].notna()]


def clustered_spearman(x: np.ndarray, y: np.ndarray, cluster: np.ndarray) -> dict[str, float]:
    """Pooled Spearman rho and its cluster-robust t (OLS slope of standardised ranks, CR1 by ``cluster``)."""
    rx = pd.Series(x).rank().to_numpy()
    ry = pd.Series(y).rank().to_numpy()
    zx = (rx - rx.mean()) / rx.std()
    zy = (ry - ry.mean()) / ry.std()
    n = len(zx)
    rho = float((zx * zy).mean())
    resid = zy - rho * zx
    score = pd.Series(zx * resid).groupby(cluster).sum().to_numpy()
    g = len(score)
    var = (g / (g - 1)) * ((n - 1) / (n - 2)) * float((score**2).sum()) / float((zx**2).sum()) ** 2
    return {"rho": rho, "t": rho / float(np.sqrt(var)), "n": n, "clusters": g}


def validity_gate(view: PitView, params: dict[str, Any], *, min_events: int = 500) -> dict[str, Any]:
    """05 §6.3 gate: pooled Spearman(surp, ear_3d) > 0 with t > 3, clustered by calendar quarter, over
    ``gate_from``..``gate_to``. Also returns the per-quarter Fama-MacBeth view and the per-year table."""
    lo, hi = params["gate_from"], params["gate_to"]
    ev = ear_3d_events(view, _events(view), lo, hi)
    ev = ev[np.isfinite(ev["surp"])]
    q = ev["event_date"].dt.to_period("Q").astype(str).to_numpy()
    main = clustered_spearman(ev["surp"].to_numpy(), ev["ear_3d"].to_numpy(), q)
    per_q = (
        ev.assign(q=q)
        .groupby("q")
        .apply(
            lambda d: d["surp"].corr(d["ear_3d"], method="spearman") if len(d) >= 10 else np.nan, include_groups=False
        )
        .dropna()
    )
    fm_t = float(per_q.mean() / (per_q.std(ddof=1) / np.sqrt(len(per_q)))) if len(per_q) > 2 else float("nan")
    per_year = ev.assign(year=ev["event_date"].dt.year).groupby("year").apply(
        lambda d: pd.Series({"n": len(d), "rho": d["surp"].corr(d["ear_3d"], method="spearman"),
                             "beat_rate": float(d["beat"].mean()), "median_abs_surp": float(d["surp"].abs().median())}),
        include_groups=False,
    )  # fmt: skip
    passed = bool(main["n"] >= min_events and main["rho"] > 0 and main["t"] > 3)
    return {
        "gate_from": str(lo), "gate_to": str(hi), "estimator": "pooled Spearman, t from quarter-clustered (CR1) SE",
        "n_events": main["n"], "n_quarters": main["clusters"], "rho": main["rho"], "t_clustered": main["t"],
        "fama_macbeth_mean_rho": float(per_q.mean()), "fama_macbeth_t": fm_t,
        "share_positive_quarters": float((per_q > 0).mean()), "passed": passed,
        "per_year": per_year.reset_index().to_dict("records"),
    }  # fmt: skip


__all__ = ["VendorNotAllowedError", "build_vendor", "clustered_spearman", "surprise_pct", "validity_gate"]
