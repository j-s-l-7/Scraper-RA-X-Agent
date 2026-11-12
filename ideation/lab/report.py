# ruff: noqa: E501, N806, E741, F841
"""Assemble the E0 / E1 result tables from stored scores (no refit). Output: markdown + json under
``IDEATION_DATA_DIR/lab/<snap>/`` (never in git)."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ideation.eval import selection
from ideation.eval.bootstrap import mean_ci
from ideation.eval.inference import ic_summary, newey_west_t
from ideation.eval.metrics import rank_ic
from ideation.lab import engine as E
from ideation.lab import stats as S
from ideation.lab.build_fund import lab_dir
from ideation.lab.run import FIXED_BENCH, LabRun
from ideation.labels.loader import load_labels, unsealed_only
from ideation.trials.registry import load_family

CTRL = ["anchor", "security_id", "ret_total", "beta_252", "rvol_63", "idio_vol", "mcap_pit_cr", "adv63_lakh", "labelled"]
SUBERAS = {"2016-17": ("2016-06-01", "2017-12-31"), "2018-19": ("2018-01-01", "2019-12-31"),
           "2020-21": ("2020-01-01", "2021-12-31"), "2022-24": ("2022-01-01", "2024-12-31")}  # fmt: skip
LEGACY_JSON = "legacy_sr.json"


def f3(x: Any, nd: int = 3) -> str:
    return "n/a" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:.{nd}f}"


def md(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    out = ["| " + " | ".join(["", *map(str, cols)]) + " |", "|" + "---|" * (len(cols) + 1)]
    for i, r in df.iterrows():
        out.append("| " + " | ".join([str(i), *[str(v) for v in r.to_numpy()]]) + " |")
    return "\n".join(out)


def legacy_sr(data_dir: Path, snap: str) -> np.ndarray:
    """Per-observation Sharpe ratio (mean/sd of the counted 2020-2024 June IC) of every stored legacy 1Y trial."""
    cache = lab_dir(data_dir, snap) / LEGACY_JSON
    if cache.exists():
        return np.array(json.loads(cache.read_text()), dtype=float)
    out = []
    for p in sorted((data_dir / "bakeoff" / snap / "results").glob("*/oos.parquet")):
        d = pd.read_parquet(p)
        d = d[d["score"].notna() & d["ret_total"].notna()]
        d["anchor"] = pd.to_datetime(d["anchor"])
        d = d[(d["anchor"].dt.year >= 2020) & (d["anchor"].dt.year <= 2024) & (d["anchor"].dt.month == 6)]
        ic = d.groupby("anchor").apply(lambda g: rank_ic(g["score"], g["ret_total"]), include_groups=False)
        out.append(S.sharpe(ic) if len(ic) >= 3 else np.nan)
    arr = np.array(out)
    cache.write_text(json.dumps([None if np.isnan(x) else float(x) for x in arr]))
    return arr


def evaluate(scored: pd.DataFrame, panel: pd.DataFrame, lo: str | None = None, hi: str | None = None,
             months: set[int] | None = None) -> pd.DataFrame:
    d = scored[["anchor", "security_id", "score"]].merge(panel[CTRL].drop(columns=["ret_total"]).assign(_r=panel["ret_total"]), on=["anchor", "security_id"], how="left")
    d = d.rename(columns={"_r": "ret_total"})
    d = d[d["labelled"].fillna(False).astype(bool) & d["score"].notna()]
    if lo:
        d = d[d["anchor"] >= pd.Timestamp(lo)]
    if hi:
        d = d[d["anchor"] <= pd.Timestamp(hi)]
    if months:
        d = d[d["anchor"].dt.month.isin(months)]
    return d.reset_index(drop=True)


def paired(a: pd.Series, b: pd.Series, seed: int = 0) -> dict[str, float]:
    """Paired monthly IC difference with NW t (lag 11) and a stationary-bootstrap CI."""
    c = pd.concat([a, b], axis=1, join="inner").dropna()
    dlt = c.iloc[:, 0] - c.iloc[:, 1]
    ci = mean_ci(dlt, method="stationary", mean_block=12.0, n_boot=5000, seed=seed)
    return {"delta": float(dlt.mean()), "nw_t": float(newey_west_t(dlt, S.LAG_12M)), "ci90": ci.ci90, "n": len(dlt)}


def adversarial_calibrated(lr: LabRun, cols: list[str], fold, n_null: int = 3, rows: int = 4000, seed: int = 0) -> dict[str, float]:
    """AUC train-vs-test on CONSUMED columns, with an anchor-shuffle null (which anchors count as 'test' is permuted)."""
    from ideation.bakeoff.audit import adversarial_auc

    fr, X = lr.panel.frame, lr.master
    rng = np.random.default_rng(seed)
    anchors = list(fold.train_anchors) + list(fold.test_anchors)
    n_test = len(fold.test_anchors)

    def sample(sel: list[pd.Timestamp]) -> pd.DataFrame:
        m = fr["anchor"].isin(sel).to_numpy() & fr["labelled"].to_numpy()
        idx = np.flatnonzero(m)
        idx = rng.choice(idx, size=min(rows, len(idx)), replace=False)
        return X.iloc[idx][cols]

    auc, _ = adversarial_auc(sample(list(fold.train_anchors)), sample(list(fold.test_anchors)))
    nulls = []
    for _ in range(n_null):
        perm = list(rng.permutation(len(anchors)))
        te = [anchors[i] for i in perm[:n_test]]
        tr = [anchors[i] for i in perm[n_test:]]
        nulls.append(adversarial_auc(sample(tr), sample(te))[0])
    return {"auc": float(auc), "null_mean": float(np.mean(nulls)), "excess": float(auc - np.mean(nulls))}


def consumed_value_cols(lr: LabRun, names: list[str]) -> list[str]:
    bc = set(lr.reg.broadcast_cols())
    return [n for n in names if n not in bc and n not in E.RAW_BINARY]


def tactical_headline(lr: LabRun, log: Callable[[str], None]) -> dict[str, Any]:
    """The tactical 3M headline in the lab's standard: stored OOS scores of SCR_T0 / RIDGE_T0 / T_EW_SIGNPRIOR / MOM at
    quarterly-spaced non-overlapping eras (NW lag 2 on all months), neutralised IC, size/ADV terciles, sub-eras."""
    from ideation.tactical.data import load_panel as tload

    d = lr.data_dir
    pan = tload(d / "outputs" / lr.snap, lr.snap)
    uni = pd.read_parquet(d / "outputs" / lr.snap / "universe_member.parquet", columns=["anchor", "security_id", "mcap_pit_cr"])
    uni["anchor"] = pd.to_datetime(uni["anchor"]).dt.normalize()
    fr = pan.frame.merge(uni, on=["anchor", "security_id"], how="left")
    out = {}
    files = {"SCR_T0": "03_SCR_T0", "T_EW_SIGNPRIOR": "01_T_EW_SIGNPRIOR", "RIDGE_T0": None}
    for p in (d / "tactical_ms2" / "scores").glob("*_RIDGE_T0.parquet"):
        files["RIDGE_T0"] = p.stem
    for name, stem in files.items():
        if not stem:
            continue
        sc = pd.read_parquet(d / "tactical_ms2" / "scores" / f"{stem}.parquet")
        sc["anchor"] = pd.to_datetime(sc["anchor"]).dt.normalize()
        x = sc.merge(fr[["anchor", "security_id", "ret_3m", "beta_252", "rvol_63", "idio_vol", "mcap_pit_cr", "adv63_lakh"]], on=["anchor", "security_id"], how="inner")
        x = x[x["score"].notna() & x["ret_3m"].notna()].rename(columns={"ret_3m": "ret_total"})
        ic = S.ic_by_anchor(x)
        q = ic[[a.month in (3, 6, 9, 12) for a in ic.index]]
        s = ic_summary(ic, 3, 1, n_boot=2000)
        nt = S.neutral_and_buckets(x)
        subs = {k: float(ic[(ic.index >= lo) & (ic.index <= hi)].mean()) for k, (lo, hi) in SUBERAS.items()}
        subs["2014-15"] = float(ic[(ic.index >= "2014-01-01") & (ic.index <= "2015-12-31")].mean())
        out[name] = {"mean": float(ic.mean()), "nw_t": s["nw_t"], "n": s["n"], "quarterly_eras_mean": float(q.mean()), "quarterly_eras_n": len(q),
                     "quarterly_hit": float((q > 0).mean()), "quarterly_icir": float(q.mean() / q.std(ddof=1)),
                     "res_BVLS": nt["res_BVLS"], "size_terciles": nt["size_terciles"], "adv_terciles": nt["adv_terciles"], "sub_eras": subs,
                     "q5_q1": S.quintile_table(x)["q5_q1"]}  # fmt: skip
    return out


def reality_checks(lr: LabRun, log: Callable[[str], None]) -> dict[str, Any]:
    """Single factors and the EW value+momentum+quality+low-vol composite at 1M / 3M / 1Y on all monthly anchors."""
    fr, X = lr.panel.frame, lr.master
    anchors = lr.panel.anchors
    lab = {}
    for h, last in (("3M", "2024-06-30"), ("1M", "2024-06-30")):
        req = unsealed_only([a.date() for a in anchors if a <= pd.Timestamp(last)], h)
        l = load_labels(req, h, "evaluate", family="lab-reality", source=lr.data_dir / "outputs" / lr.snap / "labels.parquet")
        l["anchor"] = pd.to_datetime(l["anchor"]).dt.normalize()
        lab[h] = l.set_index(["anchor", "security_id"])["ret_total"]
    base = fr[["anchor", "security_id", "labelled", "ret_total"]].copy()
    base = base.rename(columns={"ret_total": "ret_1Y"})
    idx = pd.MultiIndex.from_frame(base[["anchor", "security_id"]])
    base["ret_3M"] = lab["3M"].reindex(idx).to_numpy()
    base["ret_1M"] = lab["1M"].reindex(idx).to_numpy()
    factors: dict[str, pd.Series] = {
        "VALUE5_EW": E.fixed_score("VALUE_ONLY", X, lr.reg), "P1_UNIFIED_XS": E.fixed_score("P1_UNIFIED_XS", X, lr.reg),
        "MOM_12_1": X["mom_12_1"], "MOM_6M": X["mom_6m"], "DIST_52WH": X["dist_52wh"],
        "QUALITY_EW": E.fixed_score("QUALITY_ONLY", X, lr.reg), "GROSS_PROFIT": X["gp_ta"], "PIOTROSKI": X["piotroski"],
        "NEG_ACCRUALS": -X["accruals"], "NEG_ASSET_GROWTH": -X["asset_gr"], "LOWVOL": -X["rvol_63"], "LOWBETA": -X["beta_252"],
        "SIZE_SMALL": -X["size"], "VMQL_EW": E.fixed_score("VMQL_FIXED", X, lr.reg),
    }  # fmt: skip
    rows = []
    tables = {}
    for name, s in factors.items():
        row = {"factor": name}
        for h in ("1M", "3M", "1Y"):
            col = f"ret_{h[:-1] + h[-1]}" if h != "1Y" else "ret_1Y"
            col = {"1M": "ret_1M", "3M": "ret_3M", "1Y": "ret_1Y"}[h]
            d = pd.DataFrame({"anchor": base["anchor"], "security_id": base["security_id"], "score": s.to_numpy(), "ret_total": base[col].to_numpy()})
            d = d[d["ret_total"].notna() & base["labelled"].to_numpy()] if h == "1Y" else d[d["ret_total"].notna()]
            if h == "1Y":
                d = d[d["anchor"] <= pd.Timestamp("2024-06-30")]
            ic = S.ic_by_anchor(d).dropna()
            lags = {"1M": 0, "3M": 2, "1Y": 11}[h]
            row[f"IC_{h}"] = float(ic.mean())
            row[f"t_{h}"] = float(newey_west_t(ic, lags)) if len(ic) > 5 else np.nan
            qt = S.quintile_table(d)
            row[f"LS_{h}"] = qt["q5_q1"]
            if name == "VMQL_EW":
                tables[h] = qt
        rows.append(row)
    tab = pd.DataFrame(rows).set_index("factor")
    return {"table": tab, "vmql_quintiles": tables}


def e0_fixed(lr: LabRun) -> dict[str, Any]:
    """Fixed-weight models (no training) on the standard windows: counted monthly, June 2020-24, June 2016-24, all months."""
    out = {}
    for n in [*FIXED_BENCH, "VMQL_FIXED", "BLEND_VAL_PRICE"]:
        sc = lr.fixed_scores(n)
        res = {}
        for wname, kw in {"june_2020_24": dict(lo="2020-06-01", months={6}), "june_2016_24": dict(lo="2016-06-01", months={6}),
                          "monthly_2020_24": dict(lo="2020-06-01"), "monthly_all": dict(lo="2016-06-01")}.items():  # fmt: skip
            d = evaluate(sc, lr.panel.frame, hi="2024-06-30", **kw)
            ic = S.ic_by_anchor(d).dropna()
            r = {"mean": float(ic.mean()), "n": len(ic), "hit": float((ic > 0).mean())}
            if len(ic) > 12:
                r["nw_t"] = float(newey_west_t(ic, S.LAG_12M))
                r["n_eff"] = S.effective_n(ic)
            res[wname] = r
        d = evaluate(sc, lr.panel.frame, lo="2016-06-01", hi="2024-06-30")
        ic = S.ic_by_anchor(d).dropna()
        res["sub_eras"] = {k: float(ic[(ic.index >= lo) & (ic.index <= hi)].mean()) for k, (lo, hi) in SUBERAS.items()}
        nt = S.neutral_and_buckets(d)
        res["res_BVLS"], res["size_terciles"], res["adv_terciles"] = nt["res_BVLS"], nt["size_terciles"], nt["adv_terciles"]
        res["quintiles"] = S.quintile_table(d)
        res["turnover_q5"] = S.top_turnover(d)
        out[n] = res
    return out


def write_all(data_dir: Path, snap: str, log: Callable[[str], None] = print) -> Path:
    lr = LabRun(data_dir, snap, log=log)
    lr.run_all()  # cache hits only when the data key matches; otherwise genuine refits
    log(f"genuine fits this call: {lr.fits}")
    ld = lab_dir(data_dir, snap)
    fr = lr.panel.frame
    specs = load_family("1Y-monthly-v2")
    leg = legacy_sr(data_dir, snap)
    res: dict[str, Any] = {"legacy_n_sr": int(np.sum(~np.isnan(leg)))}
    lines = ["# Accuracy lab tables (generated; not in git)", ""]

    log("E0 fixed-weight models")
    fx = e0_fixed(lr)
    res["fixed"] = fx
    rows = {n: {k: f3(v["mean"]) + (f" (t {v['nw_t']:.1f}, neff {v['n_eff']:.0f})" if "nw_t" in v else f" (n {v['n']})") for k, v in r.items() if isinstance(v, dict) and "mean" in v and k != "sub_eras"} for n, r in fx.items()}
    lines += ["## E0 fixed-weight models, mean 1Y rank IC by window", "", md(pd.DataFrame(rows).T), ""]
    sub = pd.DataFrame({n: r["sub_eras"] for n, r in fx.items()}).T.map(f3)
    lines += ["### by sub-era (monthly anchors)", "", md(sub), ""]
    nt = pd.DataFrame({n: {"res_BVLS": f3(r["res_BVLS"]), "size lo/mid/hi": "/".join(f3(x) for x in r["size_terciles"]), "ADV lo/mid/hi": "/".join(f3(x) for x in r["adv_terciles"]),
                           "Q5-Q1 (1Y)": f3(r["quintiles"]["q5_q1"]), "monotone": r["quintiles"]["monotone_strict"], "q": " ".join(f3(x, 2) for x in r["quintiles"]["q_mean_excess"]),
                           "turnover Q5": f3(r["turnover_q5"], 2)} for n, r in fx.items()}).T
    lines += ["### neutralised IC, buckets, quintiles (all monthly anchors 2016-06..2024-06)", "", md(nt), ""]

    log("reality checks")
    rc = reality_checks(lr, log)
    res["reality"] = {"table": rc["table"].round(4).to_dict(), "vmql_quintiles": rc["vmql_quintiles"]}
    lines += ["## E0 reality check: factors at 1M / 3M / 1Y (all monthly anchors 2016-06..2024-06; IC = mean rank IC, t = NW (lag 0/2/11), LS = Q5-Q1 mean raw return over the horizon)", "", md(rc["table"].round(3)), ""]
    for h, qt in rc["vmql_quintiles"].items():
        lines.append(f"VMQL_EW quintile excess return over the universe mean, horizon {h}: " + " | ".join(f"Q{i + 1} {x * 100:.2f}%" for i, x in enumerate(qt["q_mean_excess"])) + f"; monotone={qt['monotone_strict']}, inversions={qt['n_inversions']}, Q5-Q1 {qt['q5_q1'] * 100:.2f}%")
    lines.append("")

    log("tactical headline")
    th = tactical_headline(lr, log)
    res["tactical"] = th
    lines += ["## E0 tactical 3M headline in the lab standard (stored OOS scores, nothing refit)", "", md(pd.DataFrame({n: {"mean IC": f3(v["mean"]), "NW t (lag 2)": f3(v["nw_t"], 1), "quarterly eras mean": f3(v["quarterly_eras_mean"]),
          "n eras": v["quarterly_eras_n"], "hit": f3(v["quarterly_hit"], 2), "ICIR (q)": f3(v["quarterly_icir"], 2), "res_BVLS": f3(v["res_BVLS"]), "size terciles": "/".join(f3(x) for x in v["size_terciles"]),
          "ADV terciles": "/".join(f3(x) for x in v["adv_terciles"]), **{f"era {k}": f3(x) for k, x in v["sub_eras"].items()}, "Q5-Q1 3M": f3(v["q5_q1"])} for n, v in th.items()}).T), ""]

    log("E1 trial tables")
    designs = {}
    for dname in ("monthly", "june"):
        tr = {}
        for s in specs:
            p = lr.path(s.trial_id, dname, lr.keys.get((s.trial_id, dname), ""))
            if "learner" in s.config or "consensus" in s.config:
                if not p.exists():
                    continue
                tr[s.trial_id] = pd.read_parquet(p)
            else:
                fxs = lr.fixed_scores("VMQL_FIXED" if s.config["model"] == "vmql_fixed" else "BLEND_VAL_PRICE")
                tr[s.trial_id] = fxs
        designs[dname] = tr
    for n in ("P1_UNIFIED_XS", "VALUE_ONLY", "P1_COMP_EW", "MOM_12_1", "LOWVOL", "QUALITY_ONLY"):
        for dname in designs:
            designs[dname][f"BENCH:{n}"] = lr.fixed_scores(n)
    ev = {"monthly": dict(lo="2020-06-01", hi="2024-06-30"), "june": dict(lo="2020-06-01", hi="2024-06-30", months={6})}
    reps: dict[str, dict[str, Any]] = {}
    for dname, tr in designs.items():
        for tid, sc in tr.items():
            d = evaluate(sc, fr, **ev[dname])
            if d.empty:
                continue
            r = S.full_report(d, n_boot=5000)
            # leave-COVID-stale-out: drop anchors 2020-06..2021-09
            d2 = d[(d["anchor"] < "2020-06-01") | (d["anchor"] > "2021-09-30")]
            r["ic_ex_covid_stale"] = float(S.ic_by_anchor(d2).mean()) if dname == "monthly" else float(S.ic_by_anchor(d[~d["anchor"].dt.year.isin([2020, 2021])]).mean())
            reps[f"{dname}:{tid}"] = r
    era_ic = {k.split(":", 1)[1]: v["ic_series"][[a.month == 6 for a in v["ic_series"].index]] for k, v in reps.items() if k.startswith("monthly:") and not k.startswith("monthly:BENCH")}
    dsr = S.dsr_families(era_ic, leg[~np.isnan(leg)], 53)
    res["dsr"] = dsr
    base_series = reps["monthly:BENCH:VALUE_ONLY"]["ic_series"]
    comp_series = reps["monthly:BENCH:P1_COMP_EW"]["ic_series"]
    rows = []
    for k, r in reps.items():
        dn, tid = k.split(":", 1)
        if dn != "monthly":
            continue
        e, m = r["eras"], r["monthly"]
        dd = dsr.get(tid, {})
        pv = paired(r["ic_series"], base_series) if tid != "BENCH:VALUE_ONLY" else {}
        rows.append({"trial": tid, "IC (12 months/yr)": f3(m["mean"]), "NW t": f3(m["nw_t"], 1), "n_eff": f3(m["n_eff"], 1), "CI90": f"{m['ci90'][0]:.3f}..{m['ci90'][1]:.3f}",
                     "ICIR monthly": f3(m["ir_monthly"], 2), "June eras IC": " ".join(f3(x, 2) for x in e["ic_by_era"]), "era mean": f3(e.get("mean")), "ICIR era": f3(e.get("ir"), 2),
                     "era hit": f3(e.get("pct_positive"), 2), "phase band mean": "..".join(f3(x) for x in e.get("band_mean", (np.nan, np.nan))), "res_B": f3(r["res_B"]), "res_BVLS": f3(r["res_BVLS"]),
                     "size terc": "/".join(f3(x, 2) for x in r["size_terciles"]), "ADV terc": "/".join(f3(x, 2) for x in r["adv_terciles"]), "Q5-Q1": f3(r["quintiles"]["q5_q1"]),
                     "monotone": r["quintiles"]["monotone_strict"], "inv": r["quintiles"]["n_inversions"], "turn Q5": f3(r["turnover_q5"], 2), "n/anchor": int(r["n_rows"] / max(r["n_anchors"], 1)),
                     "ex COVID-stale": f3(r["ic_ex_covid_stale"]), "DSR lab(N=18)": f3(dd.get("dsr_lab"), 2), "DSR all(N=71)": f3(dd.get("dsr_all"), 2), "DSR effN=10": f3(dd.get("dsr_eff10"), 2),
                     "d vs VALUE_ONLY": f3(pv.get("delta")) + (f" (t {pv['nw_t']:.1f})" if pv else ""), "MDE(5 eras)": f3(e.get("mde_5eras"))})  # fmt: skip
    t_m = pd.DataFrame(rows).set_index("trial")
    lines += ["## E1 trials, MONTHLY design, counted test anchors 2020-06..2024-06 (49 monthly anchors, 5 June eras)", "", md(t_m), ""]
    rows = []
    for k, r in reps.items():
        dn, tid = k.split(":", 1)
        if dn != "june":
            continue
        rows.append({"trial": tid, "June eras IC 2020..2024": " ".join(f3(x, 3) for x in r["eras"]["ic_by_era"]), "mean": f3(r["eras"].get("mean")), "ICIR": f3(r["eras"].get("ir"), 2), "res_BVLS": f3(r["res_BVLS"]), "Q5-Q1": f3(r["quintiles"]["q5_q1"])})
    lines += ["## E1 trials, legacy JUNE design (train T-3..T-1, 5 counted June tests)", "", md(pd.DataFrame(rows).set_index("trial")), ""]
    res["monthly_ic"] = {k.split(":", 1)[1]: {str(a.date()): float(v) for a, v in r["ic_series"].dropna().items()} for k, r in reps.items() if k.startswith("monthly:")}
    res["june_ic"] = {k.split(":", 1)[1]: {str(a.date()): float(v) for a, v in r["ic_series"].dropna().items()} for k, r in reps.items() if k.startswith("june:")}

    # selection: 90/10 (stability term not computed -> z=0 for everyone) over eligible trials, monthly design
    elig = {tid: r["monthly"]["mean"] for k, r in reps.items() for tid in [k.split(":", 1)[1]] if k.startswith("monthly:") and not tid.startswith("BENCH") and tid != "CTRL_SHUFFLED"}
    sel = selection.selection_scores(pd.Series(elig), pd.Series(dtype=float)).sort_values(ascending=False)
    res["selection_9010"] = sel.round(3).to_dict()
    lines += ["## Selection (existing 90/10 rule; stability term not computed -> z = 0, so it ranks by mean monthly IC)", "", md(sel.round(3).to_frame("score")), ""]

    log("adversarial (consumed columns, anchor-shuffle null)")
    adv_rows = {}
    mf = lr.folds["monthly"]
    sets = lr.sets
    consumed = {"core": consumed_value_cols(lr, sets["core"]), "lean": consumed_value_cols(lr, sets["lean"]), "mvrs": consumed_value_cols(lr, sets["mvrs"]),
                "mvrsq": consumed_value_cols(lr, sets["mvrsq"]), "P1_UNIFIED_XS cols": E.UNIFIED3, "VMQL cols": [*E.VALUE5, "mom_12_1", "rvol_63", *E.QUALITY_ONLY]}
    for nm, cols in consumed.items():
        a = [adversarial_calibrated(lr, cols, f) for f in mf[1:]]  # skip nothing important: every block after the first
        adv_rows[nm] = {"auc": " ".join(f3(x["auc"], 2) for x in a), "null": " ".join(f3(x["null_mean"], 2) for x in a), "max excess": f3(max(x["excess"] for x in a), 2),
                        "gate (excess <= 0.05)": all(x["excess"] <= 0.05 for x in a)}  # fmt: skip
    res["adversarial"] = adv_rows
    lines += ["## Adversarial AUC on CONSUMED columns with anchor-shuffle null (monthly-design blocks 2..5)", "", md(pd.DataFrame(adv_rows).T), ""]

    (ld / "LAB_TABLES.md").write_text("\n".join(lines), encoding="utf-8")
    (ld / "lab_results.json").write_text(json.dumps(res, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o), indent=1))
    return ld / "LAB_TABLES.md"
