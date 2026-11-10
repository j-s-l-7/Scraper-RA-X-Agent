# ruff: noqa: E501
"""``ideation tactical …``: the MS2 tactical bake-off (kept out of ``ideation/cli.py`` except one ``add_typer`` line).

* ``tactical wfo``    : monthly walk-forward of the fitted trials (resumable, checkpointed per (trial, fold)).
* ``tactical cpcv``   : CPCV for the top trials + baselines (resumable, checkpointed per (trial, split)).
* ``tactical report`` : metrics, paired arms, DSR/PBO/90-10, gates, persistence (``--persist``).
"""

from __future__ import annotations

import json
import logging
import sys
import time
from typing import Annotated

import numpy as np
import pandas as pd
import typer
import yaml
from rich.console import Console

from ideation.config import get_settings
from ideation.logs import setup_logging

app = typer.Typer(no_args_is_help=True, help="MS2 tactical 1-3M bake-off (family tactical-3M).")
console = Console(width=None if sys.stdout.isatty() else 220)
log = logging.getLogger("ideation.tactical")
SNAP = "20261105-e33e7986"


def _ctx():  # type: ignore[no-untyped-def]
    from ideation.tactical.data import load_panel

    settings = get_settings()
    setup_logging(settings.logs_dir, settings.log_level, console=False)
    logging.getLogger("ideation.tactical").setLevel(logging.INFO)
    out = settings.data_dir / "outputs" / SNAP
    panel = load_panel(out, SNAP, settings.config_dir)
    return settings, out, panel


def _vendor_gate_ok(out) -> bool:  # type: ignore[no-untyped-def]
    f = out / "vendor_gate_p3.json"
    return f.exists() and bool(json.loads(f.read_text()).get("passed"))


def _frames(settings, out, panel, need_text: bool, need_vendor: bool):  # type: ignore[no-untyped-def]
    from ideation.tactical import data

    frames = {"base": panel.frame}
    if need_text:
        frames["text"] = data.attach_text(panel, out)
    if need_vendor:
        from ideation.tactical.vendor import build_or_load_vendor

        v = build_or_load_vendor(settings, panel.snap_id, sorted(panel.frame["anchor"].unique()))
        frames["vsurp"] = data.attach_vendor(panel.frame, v)
    return frames


def design_for(spec, frames, panel):  # type: ignore[no-untyped-def]
    from ideation.tactical.data import build_design

    cfg = spec.config
    fset = cfg.get("features", "T0")
    subset = cfg.get("subset")
    if cfg.get("gate") == "vsurp_validity":
        subset = "vsurp_covered"
    base = {"text_covered": frames.get("text"), "vsurp_covered": frames.get("vsurp")}.get(subset, frames["base"])
    return build_design(base, panel.reg, fset, adv_floor_cr=float(cfg.get("adv_floor_cr", 0.2)), subset=subset)


def control_spec(spec):  # type: ignore[no-untyped-def]
    """Paired control arm of #17-20: same learner/config, features T1 only (a protocol arm, not a trial)."""
    from dataclasses import replace

    cfg = {**spec.config, "features": "T1"}
    return replace(spec, trial_id=spec.trial_id + "_CTRL", config=cfg)


@app.command("wfo")
def wfo_cmd(
    only: Annotated[str, typer.Option(help="comma-separated trial ids (default: all fitted trials)")] = "",
    no_importance: Annotated[
        bool, typer.Option(help="skip clustered permutation importance (90/10 stability)")
    ] = False,
) -> None:
    """Run the walk-forward for every fitted trial (resumable). 3M primary; 1M is scored later from the same scores."""
    from ideation.tactical.engine import run_wfo
    from ideation.trials.registry import load_family

    settings, out, panel = _ctx()
    specs = [s for s in load_family("tactical-3M") if "consensus" not in s.config]
    if only:
        keep = set(only.split(","))
        specs = [s for s in specs if s.trial_id in keep]
    gate_ok = _vendor_gate_ok(out)
    need_vendor = gate_ok and any(s.config.get("allow_vendor_estimates") for s in specs)
    need_text = any("text" in s.config.get("features", "").lower() for s in specs)
    frames = _frames(settings, out, panel, need_text, need_vendor)
    root = settings.data_dir / "tactical_ms2"
    (root / "scores").mkdir(parents=True, exist_ok=True)
    designs: dict = {}
    for spec in specs:
        t0 = time.perf_counter()
        cfg = spec.config
        if cfg.get("gate") == "vsurp_validity" and not gate_ok:
            console.print(f"[yellow]{spec.trial_id}: vendor gate not passed -> failed, not run[/yellow]")
            continue
        runs = [(spec, "trial")]
        if (cfg.get("paired_with") and cfg.get("subset")) or cfg.get("gate") == "vsurp_validity":
            runs.append((control_spec(spec), "control"))
        for sp, kind in runs:
            key = (sp.config.get("features", "T0"), sp.config.get("subset"), sp.config.get("adv_floor_cr", 0.2),
                   sp.config.get("gate", ""))  # fmt: skip
            if key not in designs:
                designs[key] = design_for(sp, frames, panel)
            des = designs[key]
            scores, infos = run_wfo(sp, des, panel.anchors, panel.label_end, root, importance=not no_importance and kind == "trial",
                                    progress=lambda m: console.print(m))  # fmt: skip
            scores.to_parquet(root / "scores" / f"{sp.num:02d}_{sp.trial_id}.parquet", index=False)
            (root / "scores" / f"{sp.num:02d}_{sp.trial_id}.json").write_text(json.dumps(infos))
        console.print(f"[green]{spec.trial_id} done[/green] {time.perf_counter() - t0:.0f}s")


@app.command("vendor-features")
def vendor_features_cmd() -> None:
    """Build the monthly vendor-surprise features (only if the P3-ear_3d vendor gate passed)."""
    settings, out, panel = _ctx()
    if not _vendor_gate_ok(out):
        console.print("[red]vendor gate (P3 ear_3d) has not passed: nothing built[/red]")
        raise typer.Exit(1)
    from ideation.tactical.vendor import build_or_load_vendor

    v = build_or_load_vendor(settings, panel.snap_id, sorted(panel.frame["anchor"].unique()))
    console.print(f"vendor features: {len(v)} rows")


@app.command("params")
def params_cmd() -> None:
    """Print the protocol constants (importance subsample, windows)."""
    from ideation.tactical import engine

    console.print(yaml.safe_dump({k: getattr(engine, k) for k in
                                  ("EMBARGO_3M", "N_TRAIN", "BLOCK", "IMP_REPEATS", "IMP_MAX_ROWS", "N_GROUPS", "K_TEST")}))  # fmt: skip


def _md(df: pd.DataFrame, floatfmt: str = ".4f") -> str:
    cols = list(df.columns)
    lines = ["| " + " | ".join([df.index.name or ""] + [str(c) for c in cols]) + " |",
             "|" + "---|" * (len(cols) + 1)]  # fmt: skip
    for i, r in df.iterrows():
        cells = [f"{v:{floatfmt}}" if isinstance(v, float) else str(v) for v in r.tolist()]
        lines.append("| " + " | ".join([str(i), *cells]) + " |")
    return "\n".join(lines)


@app.command("report")
def report_cmd(persist: Annotated[bool, typer.Option(help="write model/wf_result/oos_prediction/experiment_trial")] = False,
               audit: Annotated[bool, typer.Option(help="run the adversarial-validation AUC (T0, T1)")] = True) -> None:  # fmt: skip
    """Metrics, paired arms, 90/10 selection, DSR, PBO, gates and the champion-or-baseline decision."""
    from ideation.tactical import evalx
    from ideation.tactical import report as rp
    from ideation.tactical.data import build_design
    from ideation.tactical.engine import wfo_folds
    from ideation.trials.registry import load_family

    settings, _out, panel = _ctx()
    root = settings.data_dir / "tactical_ms2"
    specs = load_family("tactical-3M")
    raw = panel.frame
    scores, infos = rp.all_scores(root, specs, raw)
    pa, summ = rp.trial_table(scores, raw)
    stab = {t: evalx.stability_of(i) for t, i in infos.items()}
    aucs: dict = {}
    auc_file = root / "adversarial_auc.json"
    if audit and auc_file.exists():
        aucs = json.loads(auc_file.read_text())
    elif audit:
        for fset in ("T0", "T1"):
            d = build_design(panel.frame, panel.reg, fset)
            sp = next(s for s in specs if s.config.get("features", "T0") == fset and "learner" in s.config)
            fl = wfo_folds(sp, panel.anchors, panel.label_end)
            aucs[fset] = rp.adversarial_auc(d, fl)
            aucs[fset + " (binary breakout/flag columns dropped; diagnostic)"] = rp.adversarial_auc(
                d, fl, drop_binary=True
            )
        auc_file.write_text(json.dumps(aucs))
    auc_mean = float(np.mean([a["auc"] for a in aucs["T0"]])) if aucs else None
    dec = rp.decide(summ, pa, scores, stab, len(specs), auc_mean)
    md = ["# MS2 tactical report (generated, not committed)", ""]
    fmt = summ.copy()
    fmt["ci95"] = fmt["ci95"].map(lambda c: f"[{c[0]:.4f}, {c[1]:.4f}]")
    fmt["ci90"] = fmt["ci90"].map(lambda c: f"[{c[0]:.4f}, {c[1]:.4f}]")
    for h in ("3M", "1M"):
        t = fmt[fmt["horizon"] == h].set_index("trial").drop(columns=["horizon"])
        md += [f"## Trial table, horizon {h} (IC vs total return, per-anchor rank IC)", _md(t), ""]
    sel = dec["selection"].to_frame("score_9010")
    sel["mean_ic"] = [summ[(summ.trial == t) & (summ.horizon == "3M")]["ic_mean"].iloc[0] for t in sel.index]
    sel["stability"] = [stab.get(t, float("nan")) for t in sel.index]
    sel["DSR_ic"] = [dec["dsr_ic"][t]["dsr"] for t in sel.index]
    sel["DSR_spread"] = [dec["dsr_spread"][t]["dsr"] for t in sel.index]
    md += ["## 90/10 selection over the long-window pool (3M)", _md(sel), "", f"PBO (CSCV S=12): {dec['pbo']['pbo']:.3f} "
           f"on T={dec['pbo']['T']} anchors x {dec['pbo']['n_trials']} trials", ""]  # fmt: skip
    md += ["## Champion gates", f"champion by 90/10: **{dec['champion']}**"]
    md += [f"- {k}: {'PASS' if v[0] else 'FAIL'} ({v[1]})" for k, v in dec["gates"].items()]
    for b, v in dec["non_inferiority"].items():
        md.append(f"- vs {b}: delta IC {v['delta']:.4f} CI90 [{v['ci90'][0]:.4f}, {v['ci90'][1]:.4f}] P(d>0)={v['p_gt0']:.2f} -> "
                  f"{'ok' if dec['ni_ok'][b] else 'FAIL'}")  # fmt: skip
    md += [f"**Ships: {dec['ships']}** (champion passes all gates: {dec['champion_passes']}; best baseline "
           f"{dec['best_baseline']})", ""]  # fmt: skip
    pairs = [("RF_T0_REGIME", "RF_T0"), ("LGBM_T0_REGIME", "LGBM_T0"), ("RIDGE_T0_REGIME", "RIDGE_T0"),
             ("RF_T0_9010SEC", "RF_T0"), ("RF_T0_ADV05", "RF_T0"), ("RF_T1", "RF_T0"), ("LGBM_T1", "LGBM_T0"),
             ("RIDGE_T1", "RIDGE_T0"), ("RF_T1_TEXT", "RF_T1_TEXT_CTRL"), ("RIDGE_T1_TEXT", "RIDGE_T1_TEXT_CTRL"),
             ("RF_T1_VSURP", "RF_T1_VSURP_CTRL"), ("RIDGE_T1_VSURP", "RIDGE_T1_VSURP_CTRL")]  # fmt: skip
    rows = []
    for a, b in pairs:
        if a in pa and b in pa:
            r = evalx.paired_ic(pa[a]["3M"]["ic"], pa[b]["3M"]["ic"])
            rows.append({"arm": f"{a} - {b}", **{k: (f"[{v[0]:.4f}, {v[1]:.4f}]" if isinstance(v, tuple) else v)
                                                 for k, v in r.items()}})  # fmt: skip
    md += ["## Paired arms (per-anchor delta IC, common anchors, stationary bootstrap)",
           _md(pd.DataFrame(rows).set_index("arm")) if rows else "(none)", ""]  # fmt: skip
    cs = evalx.consensus_sets_eval({m: scores[m] for m in next(s.config["members"] for s in specs if "members" in s.config)
                                 if m in scores}, raw) if "CONS_MAJ_T" in scores else None  # fmt: skip
    if cs is not None:
        md += ["## Consensus sets (3M, equal-weight excess vs universe median, mean over anchors)",
               _md(cs.mean(numeric_only=True).to_frame("mean")), ""]  # fmt: skip
    champ = dec["champion"]
    yr = pd.DataFrame(
        {
            t: pa[t]["3M"]["ic"].groupby(pa[t]["3M"].index.year).mean()
            for t in (champ, "T_EW_SIGNPRIOR", "RIDGE_T0", "RF_T0", "MOM_12_1", "LOWVOL")
        }
    )
    md += ["## Mean 3M IC by calendar year", _md(yr), ""]
    md += [f"## R-A pooled (descriptive) for {champ}", _md(rp.ra_table(scores[champ], raw)), ""]
    if aucs:
        for k, v in aucs.items():
            md += [f"## Adversarial AUC {k} (R1 inputs, broadcast excluded): mean {np.mean([a['auc'] for a in v]):.3f}",
                   "\n".join(f"- {a['fold']}: {a['auc']:.3f} top {a['top']}" for a in v), ""]  # fmt: skip
    fits = {t: {"fit_s": sum(i.get("fit_s", 0) or 0 for i in v), "imp_s": sum(i.get("imp_s", 0) or 0 for i in v),
                "n_train_first": v[0]["n_train"] if v else None, "warnings": sorted({w for i in v for w in i.get("warnings", [])})}
            for t, v in infos.items()}  # fmt: skip
    md += ["## Fit time and warnings", _md(pd.DataFrame(fits).T), ""]
    (root / "MS2_REPORT.md").write_text("\n".join(md))
    (root / "decision.json").write_text(json.dumps({
        "champion": champ, "ships": dec["ships"], "champion_passes": dec["champion_passes"],
        "top3_fitted": [t for t in dec["selection"].index if t in rp.FITTED_SINGLE][:3],
        "pbo": dec["pbo"], "gates": {k: [v[0], v[1]] for k, v in dec["gates"].items()}}, default=str))  # fmt: skip
    console.print("\n".join(md))
    if persist:
        _persist(settings, specs, scores, pa, summ, raw, dec, stab)


def _persist(settings, specs, scores, pa, summ, raw, dec, stab) -> None:  # type: ignore[no-untyped-def]
    from ideation.db.writer import Writer
    from ideation.tactical import persist as ps

    nuni = raw.groupby("anchor").size()
    with Writer.from_settings(settings).connect() as conn:
        for s in specs:
            if s.trial_id not in pa:
                ps.update_trial(conn, int(conn.execute("select trial_id from ideation.experiment_trial where family=%s and "
                                                       "config_hash=%s", (s.family, s.config_hash)).fetchone()[0]), "failed", None)  # fmt: skip
                continue
            tid = int(conn.execute("select trial_id from ideation.experiment_trial where family=%s and config_hash=%s",
                                   (s.family, s.config_hash)).fetchone()[0])  # fmt: skip
            sel = {
                "score_9010": ps._f(dec["selection"].get(s.trial_id, float("nan"))),
                "stability": ps._f(stab.get(s.trial_id)),
            }
            for h in ("3M", "1M"):
                mid = ps.model_id(conn, s, h, sel)
                ps.write_wf_result(conn, mid, pa[s.trial_id][h], nuni)
            ps.update_trial(conn, tid, "ok", pa[s.trial_id]["3M"])
            n = ps.write_oos(conn, tid, scores[s.trial_id], raw)
            console.print(f"persisted {s.trial_id}: trial_id {tid}, {n} oos rows")


@app.command("cpcv")
def cpcv_cmd(
    trials: Annotated[
        str, typer.Option(help="comma-separated ids; default: decision.json top3_fitted + T_EW_SIGNPRIOR")
    ] = "",
) -> None:
    """CPCV (12 groups, k=2: 66 splits, 11 paths, embargo 3) for the top-3 fitted trials and the no-fit baseline.
    Resumable per (trial, split)."""
    from ideation.tactical.engine import run_cpcv
    from ideation.trials.registry import load_family

    settings, out, panel = _ctx()
    root = settings.data_dir / "tactical_ms2"
    ids = (
        trials.split(",")
        if trials
        else [*json.loads((root / "decision.json").read_text())["top3_fitted"], "T_EW_SIGNPRIOR"]
    )
    specs = {s.trial_id: s for s in load_family("tactical-3M")}
    frames = _frames(settings, out, panel, any("TEXT" in i for i in ids), any("VSURP" in i for i in ids))
    designs: dict = {}
    for tid in ids:
        spec = specs[tid]
        key = (
            spec.config.get("features", "T0"),
            spec.config.get("subset"),
            spec.config.get("adv_floor_cr", 0.2),
            spec.config.get("gate", ""),
        )
        if key not in designs:
            designs[key] = design_for(spec, frames, panel)
        t0 = time.perf_counter()
        run_cpcv(spec, designs[key], panel.anchors, panel.label_end, root, progress=lambda m: console.print(m))
        console.print(f"[green]{tid} CPCV done[/green] {time.perf_counter() - t0:.0f}s")


@app.command("cpcv-report")
def cpcv_report_cmd(trials: Annotated[str, typer.Option(help="ids")] = "") -> None:
    """Path-level IC distribution of the CPCV runs next to the no-fit baselines (MOM_12_1, T_EW_SIGNPRIOR)."""
    from ideation.cv.cpcv import anchor_groups, cpcv_paths
    from ideation.tactical import evalx
    from ideation.tactical.engine import cpcv_split_list, trial_dir
    from ideation.trials.registry import load_family

    settings, _out, panel = _ctx()
    root = settings.data_dir / "tactical_ms2"
    ids = (
        trials.split(",")
        if trials
        else [*json.loads((root / "decision.json").read_text())["top3_fitted"], "T_EW_SIGNPRIOR"]
    )
    specs = {s.trial_id: s for s in load_family("tactical-3M")}
    splits = cpcv_split_list(panel.anchors, panel.label_end)
    paths = cpcv_paths(splits, 12)
    raw = panel.frame
    rows, md = [], ["# CPCV (66 splits, 11 paths)"]
    for tid in [*ids, "MOM_12_1"]:
        if tid == "MOM_12_1":
            sc = raw[["anchor", "security_id"]].assign(score=raw["mom_12_1"])
            pa = evalx.per_anchor(sc, raw)
            s = evalx.summarize(pa)
            rows.append({"trial": tid, "paths": 1, "mean_ic": s["ic_mean"], "min_path": s["ic_mean"], "max_path": s["ic_mean"],
                         "nw_t": s["nw_t"], "spread_cost_adj": s["spread_cost_adj"], "pct_pos": s["pct_pos"]})  # fmt: skip
            continue
        d = trial_dir(root, "cpcv", specs[tid])
        per_split = {
            s.index: pd.read_parquet(d / f"split_{s.index:02d}.parquet")
            for s in splits
            if (d / f"split_{s.index:02d}.parquet").exists()
        }
        if len(per_split) < len(splits):
            md.append(f"{tid}: only {len(per_split)}/{len(splits)} splits done")
            continue
        groups = {g: set(a) for g, a in enumerate(anchor_groups(panel.anchors, 12))}
        pstats = []
        for path in paths:
            parts = []
            for g, si in path:
                df = per_split[si]
                df = df[pd.to_datetime(df["anchor"]).isin(groups[g])]
                parts.append(df)
            sc = pd.concat(parts)
            sc["anchor"] = pd.to_datetime(sc["anchor"])
            pa = evalx.per_anchor(sc, raw)
            pstats.append(evalx.summarize(pa))
        ics = [p["ic_mean"] for p in pstats]
        rows.append({"trial": tid, "paths": len(paths), "mean_ic": float(np.mean(ics)), "min_path": float(np.min(ics)), "max_path": float(np.max(ics)),
                     "nw_t": float(np.mean([p["nw_t"] for p in pstats])), "spread_cost_adj": float(np.mean([p["spread_cost_adj"] for p in pstats])),
                     "pct_pos": float(np.mean([p["pct_pos"] for p in pstats]))})  # fmt: skip
    tab = pd.DataFrame(rows).set_index("trial")
    md.append(_md(tab))
    (root / "CPCV_REPORT.md").write_text("\n".join(md))
    console.print("\n".join(md))


@app.command("diagnostics")
def diagnostics_cmd(
    skip_lag: Annotated[bool, typer.Option(help="skip the entry-lag label recomputation")] = False,
) -> None:
    """MS2 diagnostics (not trials): beta/vol neutral IC, entry-lag robustness, liquidity/size buckets, feature IC."""
    from ideation.tactical import diagnostics

    settings = get_settings()
    setup_logging(settings.logs_dir, settings.log_level, console=False)
    text, key = diagnostics.run(settings, SNAP, skip_lag=skip_lag)
    root = settings.data_dir / "tactical_ms2"
    (root / "DIAGNOSTICS.md").write_text(text)
    (root / "diagnostics_key.json").write_text(json.dumps(key, default=float, indent=1))
    console.print(f"wrote {root / 'DIAGNOSTICS.md'}")
