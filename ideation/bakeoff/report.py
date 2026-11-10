"""Statistics over the stored bake-off results: tables R-A..R-D, DSR/PBO/90-10, gates, RF lineage (05 §2, §8; 02 §5.5)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from ideation.bakeoff import metrics as M
from ideation.bakeoff.data import Panel
from ideation.bakeoff.driver import is_text, load_cpcv, load_oos, results_dir
from ideation.bakeoff.run import test_anchors
from ideation.cv.cpcv import CPCVSplit
from ideation.eval import dsr as dsr_mod
from ideation.eval import selection
from ideation.eval.bootstrap import paired_delta
from ideation.eval.inference import ic_summary
from ideation.eval.pbo import pbo_from_is_oos
from ideation.trials.registry import TrialSpec

BASELINE = "P1_COMP_EW"
N_BOOT = 10_000
RF_LINEAGE = ["RF_GLOBAL", "RF_GLOBAL_EXT", "RF_GLOBAL_9010SEC", "RF_GLOBAL_EXT_9010SEC"]
REFERENCE = {"RF_GLOBAL": 0.223, "RF_GLOBAL_EXT": 0.233, "RF_GLOBAL_9010SEC": 0.25}
"""Dossier reference values (06; different dataset, years and N). Printed as 'reference, not target'."""
NOT_SELECTABLE = {"P1_POSTCOVID", "RF_GLOBAL_MARCH", "P1_COMP_EW_MARCH", "CONS_MAJ", "CONS_UNAN", "ENS_RANKAVG"}


@dataclass
class TD:
    spec: TrialSpec
    oos: pd.DataFrame
    cpcv: pd.DataFrame | None
    meta: dict[str, Any]
    counted: list[pd.Timestamp]
    indicative: list[pd.Timestamp]
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def tid(self) -> str:
        return self.spec.trial_id


def load_trials(
    specs: list[TrialSpec], panels: dict[str, Panel], data_root: Path
) -> tuple[dict[str, TD], dict[str, str]]:
    """Results of every trial that has them; second value = id -> reason for trials without results."""
    out, missing = {}, {}
    for spec in specs:
        if is_text(spec):
            missing[spec.trial_id] = "registered (text trial, MS1-text)"
            continue
        panel = panels[spec.config["anchor_family"]]
        d = results_dir(data_root, panel.snap_id, spec)
        if not (d / "oos.parquet").exists():
            err = (d / "error.txt").read_text().strip().splitlines()[-1] if (d / "error.txt").exists() else "no results"
            missing[spec.trial_id] = f"failed: {err}" if (d / "error.txt").exists() else "no results"
            continue
        counted, indicative = test_anchors(panel, spec.config)
        out[spec.trial_id] = TD(
            spec, load_oos(d), load_cpcv(d), json.loads((d / "meta.json").read_text()), counted, indicative
        )
    return out, missing


# ── per-trial statistics ──────────────────────────────────────────────────────────────────────────────────────
def trial_stats(td: TD, family_n: int) -> dict[str, Any]:
    pa = M.per_anchor(td.oos, n_boot=N_BOOT)
    c = [a for a in td.counted if a in pa.index]
    ic = pa.loc[c, "ic"]
    summ = ic_summary(ic, 12, 12, n_boot=N_BOOT) if len(ic) else {}
    nested = M.nested_calibration(td.oos, td.counted, "nested")
    pooled = M.nested_calibration(td.oos, td.counted, "pooled")
    st: dict[str, Any] = {
        "per_anchor": pa, "counted": c, "ic_counted": ic, "summary": summ,
        "buckets": M.bucket_by_anchor(td.oos), "mono": M.monotonicity_rows(td.oos, td.counted),
        "nested": nested, "pooled": pooled, "family_n": family_n,
    }  # fmt: skip
    folds = [td.meta["folds"][k] for k in sorted(td.meta.get("folds", {}))]
    imps = [pd.Series(f["importance"]) for f in folds if f.get("importance")]
    st["stability"] = selection.stability(imps) if len(imps) >= 2 else float("nan")
    st["n_empty_folds"] = sum(1 for f in folds if f.get("empty"))
    st["warnings"] = sorted({w for f in folds for w in f.get("warnings", [])})
    st["fit_seconds"] = float(sum(f.get("fit_seconds", 0.0) for f in folds))
    return st


def sharpe_of(x: pd.Series) -> float:
    v = np.asarray(x.dropna(), dtype=float)
    return float(v.mean() / v.std(ddof=1)) if len(v) > 1 and v.std(ddof=1) > 0 else float("nan")


def apply_family(tds: dict[str, TD], family_n: int, n_boot: int = N_BOOT) -> dict[str, Any]:
    """DSR per trial (IC series and cost-adjusted Q5-Q1), 90/10 scores, paired ΔIC vs the baseline, CPCV and PBO."""
    sr_ic = {k: sharpe_of(t.stats["ic_counted"]) for k, t in tds.items() if len(t.stats["ic_counted"]) >= 3}
    sr_sp = {
        k: sharpe_of(t.stats["per_anchor"].loc[t.stats["counted"], "spread_q5_q1_cost_adj"])
        for k, t in tds.items() if len(t.stats["counted"]) >= 3
    }  # fmt: skip
    arr_ic = np.array([v for v in sr_ic.values() if v == v])
    arr_sp = np.array([v for v in sr_sp.values() if v == v])
    for k, t in tds.items():
        ics = t.stats["ic_counted"]
        t.stats["dsr_ic"] = _dsr(ics, arr_ic, family_n)
        sp = t.stats["per_anchor"].loc[t.stats["counted"], "spread_q5_q1_cost_adj"]
        t.stats["dsr_spread"] = _dsr(sp, arr_sp, family_n)
    base = tds.get(BASELINE)
    for k, t in tds.items():
        if base is None or k == BASELINE or t.spec.config["anchor_family"] != "june":
            t.stats["vs_baseline"] = None
            continue
        common = [a for a in t.stats["counted"] if a in base.stats["ic_counted"].index]
        a, b = t.stats["ic_counted"].loc[common], base.stats["ic_counted"].loc[common]
        pdlt = paired_delta(a.to_numpy(), b.to_numpy(), n_boot=n_boot)
        t.stats["vs_baseline"] = {"mean_delta": pdlt.mean_delta, "ci90": pdlt.ci90, "p_gt0": pdlt.p_gt0, "n": pdlt.n}
    # selection (90/10, reading b)
    elig = [k for k, t in tds.items() if _selectable(t)]
    mean_ic = pd.Series({k: tds[k].stats["summary"]["mean"] for k in elig})
    stab = pd.Series({k: tds[k].stats["stability"] for k in elig})
    scores = selection.selection_scores(mean_ic, stab) if len(elig) > 1 else pd.Series(dtype=float)
    for k in elig:
        tds[k].stats["selection_9010"] = float(scores[k])
    champion = scores.idxmax() if len(scores) else None
    pbo = pbo_for_family(tds)
    return {"eligible": elig, "champion": champion, "selection": scores.sort_values(ascending=False), "pbo": pbo,
            "family_n": family_n, "sr_ic": sr_ic}  # fmt: skip


def _selectable(t: TD) -> bool:
    if t.tid in NOT_SELECTABLE or len(t.stats["ic_counted"].dropna()) < 5:
        return False
    return t.spec.config["anchor_family"] == "june"


def _dsr(series: pd.Series, sr_trials: np.ndarray, family_n: int) -> dict[str, float]:
    v = series.dropna()
    if len(v) < 3 or len(sr_trials) < 2:
        return {"dsr": float("nan")}
    r = dsr_mod.dsr_of_series(v, sr_trials, n_trials=family_n)
    return {"dsr": r.dsr, "sr": r.sr, "sr0": r.sr0, "n_obs": r.n_obs, "n_trials": r.n_trials, "var_sr": r.var_sr}


def cpcv_stats(tds: dict[str, TD], splits: list[CPCVSplit], n_groups: int) -> None:
    for t in tds.values():
        if t.cpcv is None:
            t.stats["cpcv"] = None
            continue
        ics = M.cpcv_anchor_ics(t.cpcv)
        t.stats["cpcv_ics"] = ics
        t.stats["cpcv"] = M.cpcv_summary(ics, splits, n_groups)


def pbo_for_family(tds: dict[str, TD], splits: list[CPCVSplit] | None = None) -> dict[str, Any] | None:
    """PBO over the June-family trials with CPCV (window variants copied from their base are left out as duplicates)."""
    from ideation.bakeoff.driver import CPCV_COPY_FROM

    cols = {
        k: t.stats["cpcv_ics"].groupby("anchor")["ic"].mean()
        for k, t in tds.items()
        if t.stats.get("cpcv_ics") is not None and t.spec.config["anchor_family"] == "june" and k not in CPCV_COPY_FROM
    }  # fmt: skip
    if len(cols) < 3 or splits is None:
        return None
    mat = pd.DataFrame(cols).sort_index()
    is_p, oos_p = M.pbo_inputs(mat, splits)
    res = pbo_from_is_oos(is_p, oos_p)
    best = pd.Series(mat.columns[res.best_is]).value_counts()
    return {"pbo": res.pbo, "n_splits": res.n_splits, "n_trials": res.n_trials, "best_is_counts": best.to_dict(),
            "median_logit": float(np.median(res.logits)), "columns": list(mat.columns), "matrix": mat}  # fmt: skip


# ── gates (02 §5.5 with 05 O3) ───────────────────────────────────────────────────────────────────────────────
def gates(t: TD, fam: dict[str, Any], *, leakage_green: bool | None, adversarial: str | None) -> dict[str, Any]:
    s = t.stats
    summ = s["summary"]
    ic = s["ic_counted"]
    pa = s["per_anchor"].loc[s["counted"]]
    n = len(ic)
    need = 4 if n == 5 else int(np.ceil(0.8 * n))
    g1 = bool(summ["mean"] >= 0.03 and summ["ci90"][0] > 0 and (ic > 0).sum() >= need)
    top = pa["top_q5_excess_cost_adj"]
    g2 = bool((top > 0).sum() >= need and top.mean() > 0)
    vb = s.get("vs_baseline")
    g3 = None if t.tid == BASELINE else (bool(vb["mean_delta"] >= -0.01 and vb["p_gt0"] >= 0.6) if vb else None)
    dsr_ok = s["dsr_ic"].get("dsr", float("nan")) >= 0.90
    pbo = fam["pbo"]["pbo"] if fam.get("pbo") else float("nan")
    g4 = bool(dsr_ok and pbo <= 0.5)
    g5 = None if leakage_green is None or adversarial is None else bool(leakage_green and adversarial != "BLOCK")
    parts = {
        "G1_ic": g1,
        "G2_cost_adj_top_quintile": g2,
        "G3_vs_baseline": g3,
        "G4_dsr_pbo": g4,
        "G5_leakage_adversarial": g5,
    }
    blocking = [v for v in parts.values() if v is not None]
    return {**parts, "all_pass": bool(blocking and all(blocking) and None not in parts.values()), "need": need, "n": n,
            "dsr": s["dsr_ic"].get("dsr"), "pbo": pbo}  # fmt: skip


# ── formatting ───────────────────────────────────────────────────────────────────────────────────────────────
def f(x: Any, nd: int = 3, pct: bool = False) -> str:
    if x is None or (isinstance(x, float) and x != x):
        return "n/a"
    if isinstance(x, (bool, np.bool_)):
        return "yes" if x else "no"
    if pct:
        return f"{100 * float(x):.1f}%"
    return f"{float(x):.{nd}f}" if isinstance(x, (float, np.floating)) else str(x)


def md(df: pd.DataFrame) -> str:
    cols = [str(c) for c in df.columns]
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for row in df.itertuples(index=False):
        lines.append("| " + " | ".join(str(v) for v in row) + " |")
    return "\n".join(lines)


def table_ra(t: TD, scheme: str) -> pd.DataFrame:
    s = t.stats
    b = s["buckets"]
    counted = set(s["counted"])
    bc = b[b["anchor"].isin(counted)]
    latest = max(counted) if counted else None
    cal = s[scheme]
    cal_rows = {a: M.calibration_bucket_rows(c, _hist(t)) for a, c in cal.items() if c.get("cal") is not None}
    rows = []
    for name in ["P99", "P95", *[f"D{k}" for k in range(10, 0, -1)], "P5", "P1"]:
        x = bc[bc["bucket"] == name]
        lt = b[(b["anchor"] == latest) & (b["bucket"] == name)]
        cr = pd.concat([d[d["bucket"] == name] for d in cal_rows.values()]) if cal_rows else pd.DataFrame()
        last_cal = cal_rows.get(latest)
        lc = last_cal[last_cal["bucket"] == name] if last_cal is not None and len(last_cal) else pd.DataFrame()
        rows.append(
            {
                "bucket": name,
                "score range (latest)": f"{f(lt['score_lo'].iloc[0])} .. {f(lt['score_hi'].iloc[0])}" if len(lt) else "n/a",
                "p range": f"{f(lt['p_lo'].iloc[0], 2)} .. {f(lt['p_hi'].iloc[0], 2)}" if len(lt) else "n/a",
                "IS range": f"{round(100 * lt['p_lo'].iloc[0])} .. {round(100 * lt['p_hi'].iloc[0])}" if len(lt) else "n/a",
                "mean n": f(x["n"].mean(), 0),
                "mean excess": f(x["mean_excess"].mean(), pct=True), "median excess": f(x["median_excess"].mean(), pct=True),
                "hit": f(x["hit"].mean(), pct=True), "beat N500": f(x["beat_n500"].mean(), pct=True),
                "beat EW": f(x["beat_ew"].mean(), pct=True),
                "alpha_iso (latest)": f(lc["alpha_iso"].iloc[0], pct=True) if len(lc) else "n/a",
                "80% band (latest)": f"{f(lc['conf_lo'].iloc[0], pct=True)} .. {f(lc['conf_hi'].iloc[0], pct=True)}" if len(lc) else "n/a",
                "coverage": f(cr["coverage"].mean(), pct=True) if len(cr) else "n/a",
                "anchors": len(x),
            }
        )  # fmt: skip
    return pd.DataFrame(rows)


def _hist(t: TD) -> pd.DataFrame:
    base = t.oos[t.oos["labelled"] & t.oos["score"].notna()].copy()
    base["p"] = M.percentile(base["score"], by=base["anchor"].to_numpy()).to_numpy()
    base["excess"] = base["excess_univ_med"]
    return base


def table_rb(t: TD) -> pd.DataFrame:
    pa = t.stats["per_anchor"]
    rows = []
    for a, r in pa.iterrows():
        rows.append(
            {
                "anchor": a.date(), "set": "counted" if a in set(t.counted) else "indicative", "IC": f(r["ic"]),
                "CI90 (names)": f"{f(r['ic_lo'])} .. {f(r['ic_hi'])}", "n scored": r["n_scored"], "coverage": f(r["coverage"], pct=True),
                "D10-D1": f(r["spread_d10_d1"], pct=True), "Q5-Q1": f(r["spread_q5_q1"], pct=True),
                "Q5-Q1 net 12bps": f(r["spread_q5_q1_cost_adj"], pct=True), "Q5 excess net": f(r["top_q5_excess_cost_adj"], pct=True),
            }
        )  # fmt: skip
    return pd.DataFrame(rows)


def table_rc(t: TD) -> pd.DataFrame:
    m = t.stats["mono"].copy()
    m.index = [a.date() for a in m.index]
    rows = [{"anchor": a, "Spearman(decile, mean ret)": f(r["spearman_decile"]), "P99>P95>D10>D9": f(bool(r["top_order"])),
             "P1<P5<D1<D2": f(bool(r["bottom_order"]))} for a, r in m.iterrows()]  # fmt: skip
    n = len(m)
    rows.append({"anchor": f"count of {n}", "Spearman(decile, mean ret)": f(m["spearman_decile"].mean()),
                 "P99>P95>D10>D9": int(m["top_order"].sum()), "P1<P5<D1<D2": int(m["bottom_order"].sum())})  # fmt: skip
    return pd.DataFrame(rows)


def row_rd(t: TD, fam: dict[str, Any]) -> dict[str, Any]:
    s = t.stats
    sm = s["summary"]
    cp = s.get("cpcv")
    return {
        "trial": t.tid, "mean IC": f(sm.get("mean")), "CI90": f"{f(sm['ci90'][0])} .. {f(sm['ci90'][1])}" if sm else "n/a",
        "CI95": f"{f(sm['ci95'][0])} .. {f(sm['ci95'][1])}" if sm else "n/a", "pos": f"{(s['ic_counted'] > 0).sum()}/{len(s['ic_counted'])}",
        "IC-IR": f(sm.get("ir")), "DSR(IC)": f(s["dsr_ic"].get("dsr")), "DSR(spread)": f(s["dsr_spread"].get("dsr")),
        "PBO": f(fam["pbo"]["pbo"]) if fam.get("pbo") else "n/a", "90/10": f(s.get("selection_9010")),
        "stab": f(s["stability"]), "CPCV mean IC": f(cp["mean_ic"]) if cp else "n/a",
        "CPCV paths>0": f(cp["share_paths_positive"], pct=True) if cp else "n/a", "dIC vs base": f(s["vs_baseline"]["mean_delta"]) if s.get("vs_baseline") else "n/a",
        "P(dIC>0)": f(s["vs_baseline"]["p_gt0"]) if s.get("vs_baseline") else "n/a",
    }  # fmt: skip


def p_values(t: TD) -> dict[str, float]:
    sm = t.stats["summary"]
    return {k: sm[k] for k in ("sign_test_p", "t_classic", "t_classic_p", "nw_t") if k in sm}


def ttest_one_sided(ic: pd.Series) -> float:
    v = ic.dropna()
    return float(stats.ttest_1samp(v, 0.0, alternative="greater").pvalue) if len(v) > 2 else float("nan")
