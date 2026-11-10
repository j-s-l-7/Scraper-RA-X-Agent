"""Compute every table/statistic from stored results, persist to ``ideation.*`` and write the report (05 §2, §8)."""

from __future__ import annotations

import json
import time
from typing import Any

import pandas as pd
from rich.console import Console

from ideation.bakeoff import audit
from ideation.bakeoff import metrics as M
from ideation.bakeoff import report as RP
from ideation.bakeoff.data import Panel, build_matrix
from ideation.bakeoff.models import model_inputs
from ideation.cv.cpcv import cpcv_splits
from ideation.features.registry import Registry
from ideation.trials.registry import TrialSpec

console = Console(width=200, highlight=False)
BENCH = {
    "MOM_12_1": {"mom_12_1": 1.0},
    "LOWVOL": {"rvol_63": -1.0},
    "VALUE_ONLY": {"ep": 0.2, "sp": 0.2, "ebitda_ev": 0.2, "fcf_yield": 0.2, "bp": 0.2},
    "QUALITY_ONLY": {"roe_norm": 0.2, "roic": 0.2, "roa": 0.2, "opm": 0.2, "opm_vol3": -0.2},
    "GROWTH_ONLY": {"rev_g1": 0.25, "eps_g1": 0.25, "rev_g3": 0.25, "eps_g3": 0.25},
}


def _splits(panel: Panel, cfg: dict[str, Any]) -> list:
    p = cfg["protocol"]["cpcv"]
    anchors = [pd.Timestamp(a) for a in panel.anchors]
    return cpcv_splits(anchors, panel.label_end, int(p["n_groups"]), int(p["k"]), int(p["embargo"]))


def benchmarks(panel: Panel, spec: TrialSpec, reg: Registry) -> tuple[pd.DataFrame, pd.DataFrame]:
    """bench-1Y rows (never selectable, not in N) and the index/universe return levels per counted anchor."""
    cfg = json.loads(json.dumps(spec.config))
    cfg["feature_sets"]["bench"] = {"base": "core", "add": ["mom_12_1", "rvol_63"]}
    cfg["features"] = "bench"
    m = build_matrix(panel, cfg, reg)
    counted = RP.test_anchors(panel, cfg)[0]
    rows, levels = [], []
    meta = m.meta
    for name, w in BENCH.items():
        score = sum(m.X[c] * wt for c, wt in w.items())
        d = meta[["anchor", "security_id", "labelled", "ret_total", "excess_univ_med", "excess_n500_pr"]].copy()
        d["score"], d["label"] = score.to_numpy(), m.y.to_numpy()
        pa = M.per_anchor(d[d["anchor"].isin(counted)], n_boot=1000)
        rows.append(
            {
                "benchmark": name,
                "mean IC": RP.f(pa["ic"].mean()),
                "pos": f"{(pa['ic'] > 0).sum()}/{len(pa)}",
                "Q5 excess": RP.f(pa["top_q5_excess"].mean(), pct=True),
                "Q5 excess net 12bps": RP.f(pa["top_q5_excess_cost_adj"].mean(), pct=True),
            }
        )
    for a in counted:
        g = meta[(meta["anchor"] == a) & meta["labelled"]]
        raw = panel.frame[(pd.to_datetime(panel.frame["anchor"]) == a)]
        n500 = float((raw["ret_total"] - raw["excess_n500_pr"]).median())
        cw = float((raw["ret_total"] - raw["excess_univ_med"]).median())
        levels.append(
            {
                "anchor": a.date(),
                "universe median TR": RP.f(cw, pct=True),
                "Nifty 500 PR": RP.f(n500, pct=True),
                "EW U_score TR": RP.f(g["ret_total"].mean(), pct=True),
            }
        )
    return pd.DataFrame(rows), pd.DataFrame(levels)


def adversarial_for(td: RP.TD, panel: Panel, reg: Registry) -> dict[str, Any]:
    cfg = td.spec.config
    m = build_matrix(panel, cfg, reg, raw_input=model_inputs(cfg))
    x = m.raw if m.raw is not None else m.X
    folds = [f for f in sorted(td.meta["folds"]) if f]
    out = {}
    for name in folds:
        info = td.meta["folds"][name]
        tr = [pd.Timestamp(a) for a in info["train_anchors"]]
        out[name] = audit.fold_adversarial(x, m.meta, tr, pd.Timestamp(name), m.broadcast)
        out[name]["ood_share"] = audit.ood_share(x, m.meta, tr, pd.Timestamp(name))
    counted = {str(c.date()) for c in td.counted}
    worst = max((v["auc_values"] for k, v in out.items() if k in counted), default=float("nan"))
    return {"folds": out, "worst_auc": worst, "verdict": audit.verdict(worst) if worst == worst else None}


def finalize(
    settings: Any,
    family: str,
    snap_id: str,
    run_id: int | None,
    specs: list[TrialSpec],
    panels: dict[str, Panel],
    reg: Registry,
    ids: dict[str, int],
    *,
    persist: bool = True,
    leakage_green: bool | None = None,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    data_root = settings.data_dir
    tds, missing = RP.load_trials(specs, panels, data_root)
    family_n = len(specs)
    console.print(f"stats for {len(tds)} trials with results; without: {missing}")
    for t in tds.values():
        t.stats = RP.trial_stats(t, family_n)
    cfg0 = specs[0].config
    splits_by_fam = {f: _splits(p, cfg0) for f, p in panels.items()}
    for fam_name, splits in splits_by_fam.items():
        RP.cpcv_stats({k: t for k, t in tds.items() if t.spec.config["anchor_family"] == fam_name}, splits, 9)
    fam = RP.apply_family(tds, family_n, splits_by_fam.get("june"))
    champ = fam["champion"]
    # audits on the trials that matter for the gates
    audits: dict[str, Any] = {}
    focus = [x for x in dict.fromkeys([champ, RP.BASELINE, *RP.RF_LINEAGE]) if x in tds]
    for k in focus:
        audits[k] = adversarial_for(tds[k], panels[tds[k].spec.config["anchor_family"]], reg)
    gate_rows = {k: RP.gates(tds[k], fam, leakage_green=leakage_green, adversarial=audits[k]["verdict"]) for k in focus}
    june = next(s for s in specs if s.config["anchor_family"] == "june")
    bench, levels = benchmarks(panels["june"], june, reg)
    result = {
        "tds": tds,
        "missing": missing,
        "fam": fam,
        "audits": audits,
        "gates": gate_rows,
        "bench": bench,
        "levels": levels,
        "run_id": run_id,
        "snap_id": snap_id,
        "family_n": family_n,
    }
    text = render(result, specs, leakage_green)
    out_dir = data_root / "reports" / "ms1"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"MS1_1Y_bakeoff_{snap_id}.md"
    path.write_text(text, encoding="utf-8")
    console.print(f"report written: {path} ({time.perf_counter() - t0:.0f} s)")
    console.print(headline(result))
    if persist:
        from ideation.bakeoff.persist import persist_all

        persist_all(settings, family, run_id, specs, tds, missing, ids, fam, gate_rows, audits)
    return result


def headline(res: dict[str, Any]) -> str:
    fam, _tds, gates = res["fam"], res["tds"], res["gates"]
    champ = fam["champion"]
    lines = [f"champion by 90/10 (N={res['family_n']}): {champ}"]
    for k, g in gates.items():
        lines.append(f"{k}: " + ", ".join(f"{a}={RP.f(b)}" for a, b in g.items() if a not in ("need", "n")))
    if fam.get("pbo"):
        lines.append(
            f"PBO = {RP.f(fam['pbo']['pbo'])} over {fam['pbo']['n_trials']} trials, {fam['pbo']['n_splits']} splits"
        )
    ship = champ if champ and gates.get(champ, {}).get("all_pass") else RP.BASELINE
    lines.append(
        f"ships: {ship}" + ("" if ship == champ else " (composite baseline ships; champion failed blocking gates)")
    )
    return "\n".join(lines)


def render(res: dict[str, Any], specs: list[TrialSpec], leakage_green: bool | None) -> str:
    fam, tds, gates, audits = res["fam"], res["tds"], res["gates"], res["audits"]
    order = [s.trial_id for s in specs if s.trial_id in tds]
    num = {s.trial_id: s.num for s in specs}
    out = [f"# MS1: 1Y bake-off (family 1Y-june, N = {res['family_n']}), snapshot {res['snap_id']}", ""]
    out.append(headline(res).replace("\n", "  \n"))
    out += [
        "",
        "Hit rate = share beating the universe median; beat-N500 = share beating Nifty 500 PR; every count uses the "
        "number of counted test anchors as its denominator (5 for the June family).",
        "",
    ]
    out += ["## Table R-D: pooled summary per trial", ""]
    rd = pd.DataFrame([{"#": num[k], **RP.row_rd(tds[k], fam)} for k in order])
    out += [RP.md(rd), ""]
    if res["missing"]:
        out += ["Trials without results: " + "; ".join(f"{k} ({v})" for k, v in res["missing"].items()), ""]
    out += ["## Selection (90/10 reading b; eligible = June-family fitted trials with 5 counted tests)", ""]
    sel = fam["selection"].reset_index()
    sel.columns = ["trial", "0.9 z(IC) + 0.1 z(stability)"]
    sel["mean IC"] = [RP.f(tds[k].stats["summary"]["mean"]) for k in sel["trial"]]
    sel["stability"] = [RP.f(tds[k].stats["stability"]) for k in sel["trial"]]
    sel["0.9 z(IC) + 0.1 z(stability)"] = sel["0.9 z(IC) + 0.1 z(stability)"].map(RP.f)
    out += [RP.md(sel.head(15)), ""]
    out += [
        "## Gates (02 §5.5 with 05 O3)",
        "",
        f"Leakage tests green: {RP.f(leakage_green)} (supplied by the runner). DSR uses N = {res['family_n']}.",
        "",
    ]
    grows = [
        {
            "trial": k,
            **{a: RP.f(b) for a, b in g.items() if a not in ("need", "n")},
            "adversarial": audits[k]["verdict"],
            "worst AUC": RP.f(audits[k]["worst_auc"]),
        }
        for k, g in gates.items()
    ]
    out += [RP.md(pd.DataFrame(grows)), ""]
    out += ["## RF lineage (RF_GLOBAL -> RF_GLOBAL_EXT -> {9010SEC, EXT_9010SEC} -> 90/10 pick)", ""]
    lin = []
    for k in RP.RF_LINEAGE:
        if k in tds:
            sm = tds[k].stats["summary"]
            lin.append(
                {
                    "step": k,
                    "mean IC": RP.f(sm["mean"]),
                    "CI90": f"{RP.f(sm['ci90'][0])} .. {RP.f(sm['ci90'][1])}",
                    "reference (not target)": RP.f(RP.REFERENCE.get(k)) if k in RP.REFERENCE else "n/a",
                }
            )
    lin.append(
        {
            "step": f"90/10 pick: {fam['champion']}",
            "mean IC": RP.f(tds[fam["champion"]].stats["summary"]["mean"]) if fam["champion"] else "n/a",
            "CI90": "",
            "reference (not target)": RP.f(0.25),
        }
    )
    out += [
        RP.md(pd.DataFrame(lin)),
        "",
        "The dossier values (0.223 / 0.233 / 0.25) are on a different dataset, years and N; "
        "printed for reconciliation only.",
        "",
    ]
    out += ["## Benchmarks (bench-1Y, reported only, not in N)", "", RP.md(res["bench"]), "", RP.md(res["levels"]), ""]
    if fam.get("pbo"):
        p = fam["pbo"]
        out += [
            "## PBO (CPCV, 36 two-group combinations)",
            "",
            f"PBO = {RP.f(p['pbo'])}, trials in the matrix = {p['n_trials']}, "
            f"median logit = {RP.f(p['median_logit'])}. "
            f"IS winners: {p['best_is_counts']}",
            "",
        ]
    out += ["## Adversarial validation and OOD (champion, baseline, RF lineage)", ""]
    arows = []
    for k, a in audits.items():
        for fold, v in a["folds"].items():
            arows.append(
                {
                    "trial": k,
                    "fold": fold,
                    "AUC values": RP.f(v["auc_values"], 2),
                    "verdict": v["verdict"],
                    "AUC +flags (info)": RP.f(v["auc_with_flags"], 2),
                    "AUC +broadcast (info)": RP.f(v.get("auc_with_broadcast"), 2)
                    if "auc_with_broadcast" in v
                    else "n/a",
                    "top shift features": ", ".join(n for n, _ in v["top_with_flags"][:3]),
                    "OOD share": RP.f(v["ood_share"], pct=True),
                }
            )
    out += [RP.md(pd.DataFrame(arows)), ""]
    out += ["## CPCV path distributions (36 splits, 8 paths)", ""]
    crows = []
    for k in order:
        c = tds[k].stats.get("cpcv")
        if c:
            crows.append(
                {
                    "trial": k,
                    "mean IC": RP.f(c["mean_ic"]),
                    "min path": RP.f(c["min_path"]),
                    "max path": RP.f(c["max_path"]),
                    "paths > 0": RP.f(c["share_paths_positive"], pct=True),
                }
            )
    out += [RP.md(pd.DataFrame(crows)), ""]
    out += ["## Per-trial tables R-A (nested and pooled), R-B, R-C", ""]
    for k in order:
        t = tds[k]
        out += [f"### {num[k]}. {k}", ""]
        if t.stats["n_empty_folds"]:
            out += [f"Empty folds (selection found nothing, score NaN): {t.stats['n_empty_folds']}", ""]
        if t.stats["warnings"]:
            out += ["Learner warnings: " + "; ".join(t.stats["warnings"]), ""]
        out += [
            "R-A nested (decision grade; first counted anchor has no history)",
            "",
            RP.md(RP.table_ra(t, "nested")),
            "",
            "R-A pooled-all-OOS (descriptive, uses later years)",
            "",
            RP.md(RP.table_ra(t, "pooled")),
            "",
            "R-B IC by anchor",
            "",
            RP.md(RP.table_rb(t)),
            "",
            "R-C monotonicity",
            "",
            RP.md(RP.table_rc(t)),
            "",
        ]
    return "\n".join(out)
