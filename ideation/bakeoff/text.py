"""MS1-text (05 §3.5 #44-45, §7.3): the 1Y text trials on the covered subset, paired against the same learner without
text on the same names, plus the secondary comparison "the full-universe model restricted to the same names".

Fitting goes through the ordinary bake-off driver (``ideation train --horizon 1y --bakeoff --text``); this module only
reads the stored results, computes the paired statistics, writes the two trials' rows to ``ideation.*`` (they never enter
``apply_family``: selection, PBO and the DSR variance pool of the 48 other trials are untouched; DSR would use N = 50)
and renders the report. Indicative by the plan: coverage is 112..880 names per June anchor.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ideation.bakeoff import metrics as M
from ideation.bakeoff import report as RP
from ideation.bakeoff.data import Panel
from ideation.bakeoff.driver import control_spec, is_text, load_cpcv, load_oos, results_dir
from ideation.bakeoff.run import test_anchors
from ideation.eval.bootstrap import paired_delta
from ideation.trials.registry import TrialSpec

N_BOOT = 10_000


def _td(spec: TrialSpec, panel: Panel, root: Path) -> RP.TD:
    import json

    d = results_dir(root, panel.snap_id, spec)
    counted, indicative = test_anchors(panel, spec.config)
    return RP.TD(spec, load_oos(d), load_cpcv(d), json.loads((d / "meta.json").read_text()), counted, indicative)


def coverage_table(panel: Panel) -> pd.DataFrame:
    """Universe-restricted coverage per June anchor: names in U_score, with a current call, with current + previous."""
    f = panel.frame
    rows = []
    for a, g in f.groupby("anchor", sort=True):
        cur = g["isna_text"] == 0
        both = cur & (g["isna_text_prev"] == 0)
        rows.append({"anchor": a, "n U_score": len(g), "cur": int(cur.sum()), "cur+prev": int(both.sum()),
                     "share cur": cur.mean(), "share cur+prev": both.mean()})  # fmt: skip
    return pd.DataFrame(rows)


def _ic_series(oos: pd.DataFrame, anchors: list[pd.Timestamp]) -> pd.Series:
    pa = M.per_anchor(oos[oos["anchor"].isin(anchors)], n_boot=200)
    return pa["ic"]


def paired(a: pd.Series, b: pd.Series, anchors: list[pd.Timestamp]) -> dict[str, Any]:
    common = [x for x in anchors if x in a.index and x in b.index]
    d = paired_delta(a.loc[common].to_numpy(), b.loc[common].to_numpy(), n_boot=N_BOOT)
    return {"n": d.n, "delta": d.mean_delta, "ci90": d.ci90, "ci95": d.ci95, "p_gt0": d.p_gt0,
            "ic_a": float(a.loc[common].mean()), "ic_b": float(b.loc[common].mean()),
            "pos": int(((a.loc[common] - b.loc[common]) > 0).sum())}  # fmt: skip


def analyse(
    specs: list[TrialSpec], panels: dict[str, Panel], root: Path
) -> tuple[dict[str, dict[str, Any]], dict[str, RP.TD]]:
    """Per text trial: IC series of the text model, its control and the full-universe model restricted to the covered
    names; paired deltas; CPCV means. ``tds`` holds the text trials (for persistence)."""
    by_id = {s.trial_id: s for s in specs}
    out: dict[str, dict[str, Any]] = {}
    tds: dict[str, RP.TD] = {}
    for spec in specs:
        if not is_text(spec):
            continue
        panel = panels[spec.config["anchor_family"]]
        txt = _td(spec, panel, root)
        ctl = _td(control_spec(spec), panel, root)
        full = _td(by_id[spec.config["paired_with"]], panel, root)
        counted, indicative = txt.counted, txt.indicative
        allx = sorted(counted + indicative)
        keys = txt.oos[["anchor", "security_id"]]
        # invariant that catches a shared-cache or shared-checkpoint bug: identical rows and fold sizes
        n_t = txt.oos.groupby("anchor").size()
        n_c = ctl.oos.groupby("anchor").size()
        if not n_t.equals(n_c) or not (txt.oos[["anchor", "security_id"]].reset_index(drop=True)
                                       .equals(ctl.oos[["anchor", "security_id"]].reset_index(drop=True))):
            raise AssertionError(f"{spec.trial_id}: text and control arms do not score the same names")
        for fold, info in txt.meta["folds"].items():
            if info["n_train"] != ctl.meta["folds"][fold]["n_train"] or info["n_test"] != ctl.meta["folds"][fold]["n_test"]:
                raise AssertionError(f"{spec.trial_id} fold {fold}: train/test rows differ from its control")
        fk = full.oos.merge(keys.drop_duplicates(), on=["anchor", "security_id"], how="inner")
        fk = fk.assign(label=fk["ret_total"])
        ic_t, ic_c = _ic_series(txt.oos, allx), _ic_series(ctl.oos, allx)
        ic_f = _ic_series(fk, allx)
        res: dict[str, Any] = {
            "ic_text": ic_t, "ic_ctrl": ic_c, "ic_full_restricted": ic_f, "counted": counted, "indicative": indicative,
            "vs_control": paired(ic_t, ic_c, counted),
            "vs_full_restricted": paired(ic_t, ic_f, counted),
            "control_vs_full_restricted": paired(ic_c, ic_f, counted),
            "folds": {k: {"n_train": v["n_train"], "n_test": v["n_test"]} for k, v in txt.meta["folds"].items()},
        }  # fmt: skip
        if txt.cpcv is not None and ctl.cpcv is not None:
            ct, cc = M.cpcv_anchor_ics(txt.cpcv), M.cpcv_anchor_ics(ctl.cpcv)
            mt = ct.groupby("anchor")["ic"].mean()
            mc = cc.groupby("anchor")["ic"].mean()
            res["cpcv"] = {"text": float(mt.mean()), "ctrl": float(mc.mean()), "anchors": len(mt)}
        out[spec.trial_id] = res
        txt.stats = RP.trial_stats(txt, 50)
        txt.stats["dsr_ic"] = {"dsr": float("nan")}
        txt.stats["dsr_spread"] = {"dsr": float("nan")}
        txt.stats["selection_9010"] = None
        tds[spec.trial_id] = txt
    return out, tds


def f(x: Any, nd: int = 3) -> str:
    return RP.f(x, nd)


def render(res: dict[str, dict[str, Any]], cov: pd.DataFrame, snap_id: str, family_n: int) -> str:
    out = [f"# MS1-text: 1Y text trials #44-45 on the covered subset (family 1Y-june, N = {family_n}), snapshot {snap_id}", ""]
    out += [
        "Covered subset = names with `isna_text == 0` (a visible concall transcript within 200 days of the anchor), "
        "cut BEFORE the rank-gauss transform; target and percentile are computed inside the covered names. Indicative "
        "only (05 §7.3): few names in early years, 5 counted test anchors (2020-2024; 2019 indicative). The control "
        "arm is the same learner on the core columns, same covered rows, folds and windows (a protocol arm: not "
        "registered, not in N). Paired delta = mean over the counted anchors of IC(text) - IC(control), iid bootstrap "
        "over anchors (10,000); with 5 anchors P(delta > 0) is weak evidence.",
        "",
        "## Universe-restricted text coverage (June anchors)",
        "",
        RP.md(cov.assign(**{"share cur": cov["share cur"].map(lambda x: f"{100 * x:.1f}%"),
                            "share cur+prev": cov["share cur+prev"].map(lambda x: f"{100 * x:.1f}%"),
                            "anchor": cov["anchor"].map(lambda a: str(a))})),  # fmt: skip
        "",
    ]
    rows = []
    for tid, r in res.items():
        for label, key in (("same learner, core columns, same names (control)", "vs_control"),
                           ("full-universe model restricted to the same names", "vs_full_restricted")):  # fmt: skip
            p = r[key]
            rows.append({"trial": tid, "compared with": label, "IC text": f(p["ic_a"]), "IC other": f(p["ic_b"]),
                         "delta": f(p["delta"]), "CI90": f"{f(p['ci90'][0])} .. {f(p['ci90'][1])}",
                         "CI95": f"{f(p['ci95'][0])} .. {f(p['ci95'][1])}", "P(delta>0)": f(p["p_gt0"], 2),
                         "years text better": f"{p['pos']}/{p['n']}"})  # fmt: skip
    out += ["## Does text help on covered names? (mean IC over the counted anchors 2020-2024)", "", RP.md(pd.DataFrame(rows)), ""]
    for tid, r in res.items():
        a = pd.DataFrame({"text": r["ic_text"], "control": r["ic_ctrl"], "full model restricted": r["ic_full_restricted"]})
        a.index = [str(x.date()) + ("" if x in set(r["counted"]) else " (indicative)") for x in a.index]
        out += [f"### {tid}: IC by anchor", "", RP.md(a.round(3).reset_index().rename(columns={"index": "anchor"})), ""]
        fo = pd.DataFrame(r["folds"]).T.reset_index().rename(columns={"index": "test fold"})
        out += ["Rows per fold", "", RP.md(fo), ""]
        if "cpcv" in r:
            out += [f"CPCV (36 splits, covered anchors, robustness only): mean IC text {f(r['cpcv']['text'])}, control "
                    f"{f(r['cpcv']['ctrl'])}.", ""]  # fmt: skip
    return "\n".join(out)


def text_report(
    settings: Any, family: str, snap_id: str, run_id: int | None, specs: list[TrialSpec],
    panels: dict[str, Panel], ids: dict[str, int], *, persist: bool = True,
) -> dict[str, Any]:  # fmt: skip
    t0 = time.perf_counter()
    res, tds = analyse(specs, panels, settings.data_dir)
    cov = coverage_table(next(iter(panels.values())))
    text = render(res, cov, snap_id, len(specs))
    d = settings.data_dir / "reports" / "ms1"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"MS1_text_{snap_id}.md"
    path.write_text(text, encoding="utf-8")
    import json

    def _p(d: dict[str, Any]) -> dict[str, Any]:
        return {k: (list(v) if isinstance(v, tuple) else v) for k, v in d.items()}

    (d / f"MS1_text_{snap_id}.json").write_text(json.dumps({
        tid: {"vs_control": _p(r["vs_control"]), "vs_full_restricted": _p(r["vs_full_restricted"]),
              "cpcv": r.get("cpcv"), "ic_text": {str(k.date()): float(v) for k, v in r["ic_text"].items()},
              "ic_ctrl": {str(k.date()): float(v) for k, v in r["ic_ctrl"].items()},
              "counted": [str(a.date()) for a in r["counted"]]} for tid, r in res.items()}, default=float))  # fmt: skip
    if persist:
        from ideation.bakeoff.persist import persist_all

        persist_all(settings, family, run_id, [s for s in specs if s.trial_id in tds], tds, {}, ids,
                    {"champion": None, "pbo": None}, {}, {})  # fmt: skip
    print(f"text report: {path} ({time.perf_counter() - t0:.0f} s)")
    for tid, r in res.items():
        p = r["vs_control"]
        print(f"{tid}: IC text {p['ic_a']:.4f} vs control {p['ic_b']:.4f}; delta {p['delta']:+.4f} "
              f"CI90 [{p['ci90'][0]:+.4f}, {p['ci90'][1]:+.4f}] P(>0) {p['p_gt0']:.2f}")  # fmt: skip
    return {"res": res, "path": path, "np": np}
