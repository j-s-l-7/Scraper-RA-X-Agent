# ruff: noqa: E501
"""PT3 (05 §12 item 2): the annual-report and multi-call-trend challenger trials on their covered subsets, paired against
the same learner without the features on the same names (the MS1-text protocol: ``bakeoff/text.py``), plus the family N
and the deflated Sharpe re-evaluated at the new N.

Fitting goes through the ordinary driver (``ideation train --horizon 1y --bakeoff --cpcv --pt3``); this module reads the
stored results, computes the paired statistics and the DSR block, writes the report and persists the PT3 trials' rows
(they never enter ``apply_family``: selection, PBO and the DSR pool of the other trials are untouched).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ideation.bakeoff import report as RP
from ideation.bakeoff import text as TX
from ideation.bakeoff.data import Panel
from ideation.eval import dsr as dsr_mod
from ideation.trials.registry import TrialSpec

PT3_SUBSETS = ("annual_covered", "trend_covered")
CHAMPION = "P1_UNIFIED_XS"
"""The 90/10 pick of MS1-core (DSR 0.813 at N = 50): the reference for 'what the extra trials do to the deflation'."""
FLAGS = {"annual_covered": ("isna_ar", "isna_ar_prev"), "trend_covered": ("isna_trend", None)}


def coverage_table(panel: Panel, subset: str) -> pd.DataFrame:
    """Universe-restricted coverage per June anchor: names in U_score, covered (the trial's subset), covered with the
    previous-year / second reference (annual only)."""
    cur_flag, prev_flag = FLAGS[subset]
    rows = []
    for a, g in panel.frame.groupby("anchor", sort=True):
        cur = g[cur_flag] == 0
        row = {"anchor": str(a), "n U_score": len(g), "covered": int(cur.sum()), "share": float(cur.mean())}
        if prev_flag:
            row["cur+prev"] = int((cur & (g[prev_flag] == 0)).sum())
        rows.append(row)
    return pd.DataFrame(rows)


def dsr_block(specs: list[TrialSpec], panels: dict[str, Panel], root: Path, res: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Champion DSR at the pre-registered N and at the enlarged N; each PT3 trial's DSR at the enlarged N. The Sharpe pool
    is the 40 eligible June trials of MS1-core (same rule as ``report._selectable``); the pool variance is reported with and
    without the three PT3 trials."""
    tds, _ = RP.load_trials(specs, panels, root)
    pool: dict[str, pd.Series] = {}
    for k, td in tds.items():
        if td.tid in RP.NOT_SELECTABLE or td.spec.config["anchor_family"] != "june":
            continue
        ic = TX._ic_series(td.oos, td.counted).loc[lambda s, c=td.counted: s.index.isin(c)]
        if len(ic.dropna()) >= 5:
            pool[k] = ic
    sr = {k: RP.sharpe_of(v) for k, v in pool.items()}
    arr = np.array([v for v in sr.values() if v == v])
    n_pre, n_all = len(specs) - len(res), len(specs)
    out: dict[str, Any] = {"n_pre": n_pre, "n_all": n_all, "pool_size": len(arr), "var_pool": float(np.var(arr, ddof=1))}
    if CHAMPION in pool:
        for label, n in (("pre", n_pre), ("all", n_all)):
            r = dsr_mod.dsr_of_series(pool[CHAMPION], arr, n_trials=n)
            out[f"champion_{label}"] = {"dsr": r.dsr, "sr0": r.sr0, "sr": r.sr, "n_trials": n}
    new_sr = {tid: RP.sharpe_of(r["ic_text"].loc[r["counted"]]) for tid, r in res.items()}
    arr2 = np.array([*arr, *[v for v in new_sr.values() if v == v]])
    out["var_pool_with_pt3"] = float(np.var(arr2, ddof=1))
    out["trials"] = {}
    for tid, r in res.items():
        ic = r["ic_text"].loc[r["counted"]]
        d1 = dsr_mod.dsr_of_series(ic, arr, n_trials=n_all)
        d2 = dsr_mod.dsr_of_series(ic, arr2, n_trials=n_all)
        out["trials"][tid] = {"sr": d1.sr, "dsr_pool40": d1.dsr, "dsr_pool43": d2.dsr, "sr0": d1.sr0, "n_obs": d1.n_obs}
    return out


def render(res: dict[str, dict[str, Any]], covs: dict[str, pd.DataFrame], dsr: dict[str, Any], snap_id: str) -> str:
    n = dsr["n_all"]
    out = [f"# PT3: annual-report text and multi-call-trend 1Y challengers (family 1Y-june, N = {n}), snapshot {snap_id}", ""]
    out += [
        "Covered subset = names where the trial's feature block exists (annual: a visible quality-OK annual report with fy_end "
        "within 600 days; trend: at least 3 visible transcripts in the last 800 days), cut BEFORE the rank-gauss transform; "
        "target and percentile live inside the covered names. The control arm is the same learner on the core columns, same "
        "covered rows, folds and windows (a protocol arm: not registered, not in N). Paired delta = mean over the 5 counted "
        "anchors (2020-2024) of IC(with features) - IC(control), iid bootstrap over anchors (10,000). With 5 anchors "
        "P(delta > 0) is weak evidence. Challengers only: they enter Resurrection only if they win, and none is selectable.",
        "",
    ]
    for sub, cov in covs.items():
        c = cov.copy()
        c["share"] = c["share"].map(lambda x: f"{100 * x:.1f}%")
        out += [f"## Universe-restricted coverage on U_score, June anchors ({sub})", "", RP.md(c), ""]
    rows = []
    for tid, r in res.items():
        for label, key in (("same learner, core columns, same names (control)", "vs_control"),
                           ("full-universe model restricted to the same names", "vs_full_restricted")):  # fmt: skip
            p = r[key]
            rows.append({"trial": tid, "compared with": label, "IC with features": TX.f(p["ic_a"]), "IC other": TX.f(p["ic_b"]),
                         "delta": TX.f(p["delta"]), "CI90": f"{TX.f(p['ci90'][0])} .. {TX.f(p['ci90'][1])}",
                         "CI95": f"{TX.f(p['ci95'][0])} .. {TX.f(p['ci95'][1])}", "P(delta>0)": TX.f(p["p_gt0"], 2),
                         "years better": f"{p['pos']}/{p['n']}"})  # fmt: skip
    out += ["## Do the features help on covered names? (mean IC over the counted anchors 2020-2024)", "", RP.md(pd.DataFrame(rows)), ""]
    out += [f"## Trial count and DSR (N: {dsr['n_pre']} pre-registered + {len(res)} PT3 = {n})", ""]
    if "champion_pre" in dsr:
        c0, c1 = dsr["champion_pre"], dsr["champion_all"]
        out += [f"{CHAMPION} (90/10 pick): DSR(IC) {TX.f(c0['dsr'])} at N = {c0['n_trials']} (SR0 {TX.f(c0['sr0'])}), "
                f"{TX.f(c1['dsr'])} at N = {c1['n_trials']} (SR0 {TX.f(c1['sr0'])}); gate 4 needs >= 0.90. "
                f"Pool: {dsr['pool_size']} eligible trials, variance of SR {TX.f(dsr['var_pool'], 4)} "
                f"({TX.f(dsr['var_pool_with_pt3'], 4)} with the PT3 trials).", ""]  # fmt: skip
    drows = [{"trial": tid, "SR(IC)": TX.f(v["sr"]), "SR0 at N": TX.f(v["sr0"]), "DSR (pool of 40)": TX.f(v["dsr_pool40"]),
              "DSR (pool of 40 + PT3)": TX.f(v["dsr_pool43"]), "n_obs": v["n_obs"]} for tid, v in dsr["trials"].items()]
    out += [RP.md(pd.DataFrame(drows)), ""]
    for tid, r in res.items():
        a = pd.DataFrame({"with features": r["ic_text"], "control": r["ic_ctrl"], "full model restricted": r["ic_full_restricted"]})
        a.index = [str(x.date()) + ("" if x in set(r["counted"]) else " (indicative)") for x in a.index]
        out += [f"### {tid}: IC by anchor", "", RP.md(a.round(3).reset_index().rename(columns={"index": "anchor"})), ""]
        fo = pd.DataFrame(r["folds"]).T.reset_index().rename(columns={"index": "test fold"})
        out += ["Rows per fold", "", RP.md(fo), ""]
        if "cpcv" in r:
            out += [f"CPCV (36 splits, covered anchors, robustness only): mean IC with features {TX.f(r['cpcv']['text'])}, "
                    f"control {TX.f(r['cpcv']['ctrl'])}.", ""]  # fmt: skip
    return "\n".join(out)


def pt3_report(
    settings: Any, family: str, snap_id: str, run_id: int | None, specs: list[TrialSpec],
    panels: dict[str, Panel], ids: dict[str, int], *, persist: bool = True,
) -> dict[str, Any]:  # fmt: skip
    t0 = time.perf_counter()
    pt3_ids = {s.trial_id for s in specs if s.config.get("subset") in PT3_SUBSETS}
    res, tds = TX.analyse(specs, panels, settings.data_dir, only=pt3_ids, family_n=len(specs))
    panel = next(iter(panels.values()))
    covs = {sub: coverage_table(panel, sub) for sub in PT3_SUBSETS}
    dsr = dsr_block(specs, panels, settings.data_dir, res)
    text = render(res, covs, dsr, snap_id)
    d = settings.data_dir / "reports" / "pt3"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"PT3_{snap_id}.md"
    path.write_text(text, encoding="utf-8")

    def _p(x: dict[str, Any]) -> dict[str, Any]:
        return {k: (list(v) if isinstance(v, tuple) else v) for k, v in x.items()}

    (d / f"PT3_{snap_id}.json").write_text(json.dumps({
        "dsr": dsr,
        "trials": {tid: {"vs_control": _p(r["vs_control"]), "vs_full_restricted": _p(r["vs_full_restricted"]),
                         "cpcv": r.get("cpcv"), "ic_text": {str(k.date()): float(v) for k, v in r["ic_text"].items()},
                         "ic_ctrl": {str(k.date()): float(v) for k, v in r["ic_ctrl"].items()},
                         "counted": [str(a.date()) for a in r["counted"]]} for tid, r in res.items()}}, default=float))  # fmt: skip
    if persist:
        from ideation.bakeoff.persist import persist_all

        persist_all(settings, family, run_id, [s for s in specs if s.trial_id in tds], tds, {}, ids,
                    {"champion": None, "pbo": None}, {}, {})  # fmt: skip
    print(f"PT3 report: {path} ({time.perf_counter() - t0:.0f} s)")
    for tid, r in res.items():
        p = r["vs_control"]
        print(f"{tid}: IC with features {p['ic_a']:.4f} vs control {p['ic_b']:.4f}; delta {p['delta']:+.4f} "
              f"CI90 [{p['ci90'][0]:+.4f}, {p['ci90'][1]:+.4f}] P(>0) {p['p_gt0']:.2f}")  # fmt: skip
    for k in ("champion_pre", "champion_all"):
        if k in dsr:
            print(f"{CHAMPION} DSR {k}: {dsr[k]['dsr']:.3f} (N = {dsr[k]['n_trials']})")
    return {"res": res, "dsr": dsr, "path": path}
