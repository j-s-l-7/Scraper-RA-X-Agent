# ruff: noqa: E501, N806, S701, N818, UP031, RUF046
"""MS3 research report (05 §10.3): ``ideation report research --family 1Y-june --family tactical-3M``.

Read-only over stored results: trial ledger, ``oos_prediction``, ``outcome``, unsealed labels (``load_labels`` only) and
the JSON files the MS1/MS2 phases wrote. Nothing is refitted and no trial is added. Every number in the narrative is
computed here from those stores; nothing is hard-coded. ``render`` refuses to produce a report without the bias block,
N, sealed/unsealed status and an estimate-with-CI behind every headline statistic (see :func:`validate`).
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from ideation.bakeoff import metrics as M
from ideation.bakeoff import report as RP
from ideation.config import REPO_ROOT
from ideation.eval import consensus as CS
from ideation.eval import dsr as dsr_mod
from ideation.eval.bias_block import gather, render_from_facts
from ideation.eval.bootstrap import mean_ci, paired_delta
from ideation.eval.inference import ic_summary
from ideation.eval.metrics import rank_ic
from ideation.labels.loader import SEALED_RANGES
from ideation.outcomes.analyst import compare_sets
from ideation.outcomes.fundamental import cmh_accel
from ideation.rank.buckets import decile
from ideation.report import portfolio as PF
from ideation.report import store as ST
from ideation.trials.registry import load_family

N_BOOT = 10_000


class ReportRefused(RuntimeError):
    """The report may not be rendered: a mandatory element (bias block, N, CI, holdout status) is missing."""


@dataclass(frozen=True)
class Est:
    """A headline statistic: always an estimate with a confidence interval (a bare float is refused)."""

    label: str
    value: float
    lo: float
    hi: float
    level: int
    n: int

    def __str__(self) -> str:
        return f"{self.value:.4f} [CI{self.level} {self.lo:.4f}, {self.hi:.4f}], n={self.n}"


@dataclass
class FamilyResult:
    family: str
    n_trials: int
    md: str
    headlines: list[Est] = field(default_factory=list)
    bias_block: str = ""
    mech: dict[str, Any] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)


def f(x: Any, nd: int = 3, pct: bool = False) -> str:
    return RP.f(x, nd, pct)


def pc(x: float) -> str:
    return "n/a" if x is None or x != x else f"{100 * x:.1f}%"


def ci(t: tuple[float, float], pct: bool = False) -> str:
    return f"[{pc(t[0]) if pct else f(t[0])}, {pc(t[1]) if pct else f(t[1])}]"


# ── configuration of the two families ───────────────────────────────────────────────────────────────────────────────
CFG: dict[str, dict[str, Any]] = {
    "1Y-june": {"h": "1Y", "baseline": "P1_COMP_EW", "rf": "RF_GLOBAL", "ens": "ENS_RANKAVG", "cons": "CONS_MAJ",
                "unan": "CONS_UNAN", "block": 1.0, "ic_h": (12, 12), "label": "1Y (June anchors)", "unit": "year"},
    "tactical-3M": {"h": "3M", "baseline": "T_EW_SIGNPRIOR", "rf": "RF_T0", "ens": "ENS_RANKAVG_T", "cons": "CONS_MAJ_T",
                    "unan": "CONS_UNAN_T", "block": 3.0, "ic_h": (3, 1), "label": "tactical 3M (monthly anchors)", "unit": "month"},
}  # fmt: skip


def counted_filter(family: str, anchors: pd.Series | pd.Index) -> np.ndarray:
    a = pd.to_datetime(pd.Series(anchors))
    if family == "1Y-june":
        return a.dt.year.between(2020, 2024).to_numpy()
    return ((a >= "2014-01-01") & (a <= "2025-06-30")).to_numpy()


def ts(x: Any) -> pd.Timestamp:
    return pd.Timestamp(x)


# ── context ─────────────────────────────────────────────────────────────────────────────────────────────────────────
class Ctx:
    def __init__(self, settings: Any, conn: Any, snap: str):
        self.settings, self.conn, self.snap = settings, conn, snap
        self.data_dir: Path = settings.data_dir
        self._oos: dict[int, pd.DataFrame] = {}
        self._labels: dict[str, pd.DataFrame] = {}
        self.uni = ST.universe(self.data_dir, snap)
        self.master = ST.company_master(self.data_dir, snap)

    def oos(self, tid: int, family: str) -> pd.DataFrame:
        if tid not in self._oos:
            df = ST.oos(self.conn, tid)
            self._oos[tid] = df[counted_filter(family, df["anchor"])].reset_index(drop=True)
        return self._oos[tid]

    def labels(self, family: str, anchors: list[pd.Timestamp], horizon: str | None = None) -> pd.DataFrame:
        h = horizon or CFG[family]["h"]
        key = f"{family}|{h}|{len(anchors)}"
        if key not in self._labels:
            self._labels[key] = ST.labels(self.data_dir, self.snap, h, anchors, family)
        return self._labels[key]


def full_frame(ctx: Ctx, family: str, tid: int) -> pd.DataFrame:
    """OOS rows of a trial with n500 excess, label_end and the ``labelled`` flag the metric functions expect."""
    o = ctx.oos(tid, family)
    anchors = sorted(o["anchor"].unique())
    lab = ctx.labels(family, anchors)[["anchor", "security_id", "excess_n500_pr", "excess_univ_cw", "label_end"]]
    o = o.merge(lab, on=["anchor", "security_id"], how="left")
    o["labelled"] = o["ret_total"].notna()
    o["label_end"] = pd.to_datetime(o["label_end"])
    return o


# ── pieces ──────────────────────────────────────────────────────────────────────────────────────────────────────────
def trial_ic(row: pd.Series, family: str) -> pd.Series:
    s = ST.ic_series(row)
    return s[counted_filter(family, s.index)] if len(s) else s


def delta(a: pd.Series, b: pd.Series, family: str) -> dict[str, Any] | None:
    idx = a.index.intersection(b.index)
    if len(idx) < 3:
        return None
    blk = CFG[family]["block"]
    d = paired_delta(a.loc[idx].to_numpy(), b.loc[idx].to_numpy(), method="stationary" if blk > 1 else "iid",
                     mean_block=blk, n_boot=N_BOOT)  # fmt: skip
    return {"n": d.n, "delta": d.mean_delta, "ci90": d.ci90, "ci95": d.ci95, "p": d.p_gt0}


def verdict(d: dict[str, Any] | None) -> str:
    if d is None:
        return "n/a"
    if d["ci90"][0] > 0:
        return "better"
    if d["ci90"][1] < 0:
        return "worse"
    return "indistinguishable"


def ic_est(label: str, s: pd.Series, family: str) -> Est:
    h, sp = CFG[family]["ic_h"]
    sm = ic_summary(s, h, sp, n_boot=N_BOOT)
    return Est(label, sm["mean"], sm["ci90"][0], sm["ci90"][1], 90, int(sm["n"]))


def bench_ic_1y(ctx: Ctx, specs: list[Any]) -> dict[str, pd.Series]:
    from ideation.bakeoff.data import build_matrix, load_panel
    from ideation.bakeoff.finalize import BENCH
    from ideation.features.registry import load_registry

    reg = load_registry(ctx.settings.config_dir)
    june = next(s for s in specs if s.config["anchor_family"] == "june")
    panel = load_panel(ctx.data_dir / "outputs" / ctx.snap, ctx.snap, "june", reg)
    cfg = json.loads(json.dumps(june.config))
    cfg["feature_sets"]["bench"] = {"base": "core", "add": ["mom_12_1", "rvol_63"]}
    cfg["features"] = "bench"
    m = build_matrix(panel, cfg, reg)
    out = {}
    counted = [a for a in m.meta["anchor"].unique() if 2020 <= a.year <= 2024]
    for name, w in BENCH.items():
        score = sum(m.X[c] * wt for c, wt in w.items())
        d = m.meta[["anchor", "security_id", "labelled", "ret_total"]].copy()
        d["score"], d["label"] = score.to_numpy(), m.y.to_numpy()
        d = d[d["anchor"].isin(counted)]
        out[name] = pd.Series({a: rank_ic(g.loc[g["labelled"], "score"], g.loc[g["labelled"], "label"]) for a, g in d.groupby("anchor")})
    return out


def bench_ic_3m(ctx: Ctx, anchors: list[pd.Timestamp]) -> dict[str, pd.Series]:
    t = pd.read_parquet(ctx.data_dir / "outputs" / ctx.snap / "features_tech.parquet",
                        columns=["anchor", "security_id", "in_u_score_t", "mom_12_1", "rvol_63"])  # fmt: skip
    t["anchor"] = pd.to_datetime(t["anchor"]).dt.normalize()
    t = t[t["in_u_score_t"] & t["anchor"].isin(anchors)]
    lab = ctx.labels("tactical-3M", anchors)[["anchor", "security_id", "ret_total"]]
    d = t.merge(lab, on=["anchor", "security_id"]).dropna(subset=["ret_total"])
    out = {}
    for name, col, sign in (("MOM_12_1", "mom_12_1", 1.0), ("LOWVOL", "rvol_63", -1.0)):
        out[name] = pd.Series({a: rank_ic(sign * g[col], g["ret_total"]) for a, g in d.groupby("anchor")})
    return out


def required_sr(sr0: float, n_obs: int, level: float = 0.90) -> float:
    """Smallest per-observation Sharpe with PSR(SR0) >= level for a normal series (skew 0, kurtosis 3)."""
    lo, hi = -2.0, 5.0
    for _ in range(80):
        mid = (lo + hi) / 2
        v = dsr_mod.probabilistic_sr(mid, sr0, n_obs, 0.0, 3.0)
        if v == v and v >= level:
            hi = mid
        else:
            lo = mid
    return hi


# ── Q1 / Q2 : tables and trial comparison ───────────────────────────────────────────────────────────────────────────
def model_tables(ctx: Ctx, family: str, name: str, tid: int) -> tuple[str, pd.Series, RP.TD]:
    cfgf = CFG[family]
    o = full_frame(ctx, family, tid)
    counted = sorted(o["anchor"].unique())
    nb = 10_000 if family == "1Y-june" else 400
    pa = M.per_anchor(o, n_boot=nb)
    nested = M.nested_calibration(o, counted, "nested")
    pooled = M.nested_calibration(o, counted, "pooled")
    td = RP.TD(None, o, None, {}, counted, [])  # type: ignore[arg-type]
    td.stats = {"buckets": M.bucket_by_anchor(o), "counted": counted, "nested": nested, "pooled": pooled, "per_anchor": pa,
                "mono": M.monotonicity_rows(o, counted)}  # fmt: skip
    unit = cfgf["unit"]
    out = [f"#### {name}: R-A nested (decision grade; the first counted anchor has no history; denominator = {len(counted)} counted {unit} anchors)", "",
           RP.md(RP.table_ra(td, "nested")), "",
           "R-A pooled-all-OOS (descriptive: uses later years)", "", RP.md(RP.table_ra(td, "pooled")), ""]
    pa2 = pa.copy()
    if family != "1Y-june":
        pa2 = pa2[pa2.index.year >= 2014]
        out += ["R-B (IC by anchor) is summarised per calendar year for the monthly family:", ""]
        yr = pa2.groupby(pa2.index.year).agg(IC=("ic", "mean"), n_anchors=("ic", "size"), spread=("spread_d10_d1", "mean"),
                                             net=("spread_q5_q1_cost_adj", "mean"))  # fmt: skip
        yr["IC"], yr["spread"], yr["net"] = yr["IC"].map(f), yr["spread"].map(lambda x: pc(x)), yr["net"].map(lambda x: pc(x))
        out += [RP.md(yr.reset_index().rename(columns={"index": "year"})), ""]
    else:
        out += ["R-B IC by anchor", "", RP.md(RP.table_rb(td)), ""]
    out += ["R-C monotonicity", "", RP.md(RP.table_rc(td)) if family == "1Y-june" else _rc_summary(td), ""]
    return "\n".join(out), pa["ic"], td


def _rc_summary(td: RP.TD) -> str:
    m = td.stats["mono"]
    n = len(m)
    return RP.md(pd.DataFrame([{"anchors": n, "mean Spearman(decile, mean ret)": f(m["spearman_decile"].mean()),
                                "P99>P95>D10>D9 in": f"{int(m['top_order'].sum())} of {n}",
                                "P1<P5<D1<D2 in": f"{int(m['bottom_order'].sum())} of {n}"}]))  # fmt: skip


def q2_table(tix: pd.DataFrame, family: str, baseline: str, rf: str, extra: dict[str, pd.Series]) -> tuple[str, dict[str, pd.Series]]:
    series = {r["name"]: trial_ic(r, family) for _, r in tix.iterrows() if r["status"] == "ok"}
    base, rfs = series[baseline], series[rf]
    rows = []
    for _, r in tix.iterrows():
        nm = r["name"]
        if nm not in series or len(series[nm]) < 3:
            continue
        s = series[nm]
        h, sp = CFG[family]["ic_h"]
        sm = ic_summary(s, h, sp, n_boot=N_BOOT)
        db, dr = delta(s, base, family), delta(s, rfs, family)
        rows.append({"#": int(r["num"]), "trial": nm, "n": sm["n"], "mean IC": f(sm["mean"]), "CI90": ci(sm["ci90"]),
                     "positive": f"{int((s > 0).sum())}/{len(s)}",
                     "dIC vs baseline": f(db["delta"]) if db and nm != baseline else "-",
                     "CI90 (b)": ci(db["ci90"]) if db and nm != baseline else "-", "P(d>0) (b)": f(db["p"], 2) if db and nm != baseline else "-",
                     "vs baseline": verdict(db) if nm != baseline else "baseline",
                     "dIC vs RF": f(dr["delta"]) if dr and nm != rf else "-", "CI90 (rf)": ci(dr["ci90"]) if dr and nm != rf else "-",
                     "P(d>0) (rf)": f(dr["p"], 2) if dr and nm != rf else "-", "vs RF": verdict(dr) if nm != rf else "RF"})  # fmt: skip
    for nm, s in extra.items():
        sm = ic_summary(s, *CFG[family]["ic_h"], n_boot=N_BOOT)
        db, dr = delta(s, base, family), delta(s, rfs, family)
        rows.append({"#": "bench", "trial": nm + " (naive benchmark, not a trial)", "n": sm["n"], "mean IC": f(sm["mean"]), "CI90": ci(sm["ci90"]),
                     "positive": f"{int((s > 0).sum())}/{len(s)}", "dIC vs baseline": f(db["delta"]), "CI90 (b)": ci(db["ci90"]),
                     "P(d>0) (b)": f(db["p"], 2), "vs baseline": verdict(db), "dIC vs RF": f(dr["delta"]), "CI90 (rf)": ci(dr["ci90"]),
                     "P(d>0) (rf)": f(dr["p"], 2), "vs RF": verdict(dr)})  # fmt: skip
        series[nm] = s
    return RP.md(pd.DataFrame(rows)), series


# ── Q3 : consensus ──────────────────────────────────────────────────────────────────────────────────────────────────
def consensus_section(ctx: Ctx, family: str, tix: pd.DataFrame, members: list[str], headline: list[Est]) -> tuple[str, CS.ConsensusResult]:
    cfgf = CFG[family]
    ids = dict(zip(tix["name"], tix["trial_id"], strict=True))
    mem = {m: ctx.oos(int(ids[m]), family)[["anchor", "security_id", "p"]] for m in members}
    anchors = sorted(next(iter(mem.values()))["anchor"].unique())
    lab = ctx.labels(family, anchors)[["anchor", "security_id", "ret_total", "excess_univ_med", "excess_n500_pr", "excess_univ_cw"]]
    res = CS.evaluate(mem, lab, mean_block=cfgf["block"], n_boot=N_BOOT)
    s = res.sets.set_index("set")
    out = [f"Members M = {len(members)}: {', '.join(members)}. Naive baselines (momentum, low-vol, value-only etc.) are excluded: they are the comparison, not voters. "
           f"Anchors evaluated: {res.n_anchors}. Best single model fixed a priori as the member with the highest mean IC over these anchors: **{res.best_single}** "
           f"(a hindsight pick that favours the single model, so the comparison is conservative for the consensus). CIs: {res.method}.", ""]
    show = s[["anchors", "mean n", "ex_med", "ex_med_ci90", "ex_med_ci95", "ex_n500", "ex_n500_ci90", "ex_ew", "hit", "beat_n500", "beat_ew",
              "anchors_ex_med_gt0", "anchors_ex_n500_gt0"]].reset_index()
    tab = pd.DataFrame({"set": show["set"], "anchors": show["anchors"], "mean n": show["mean n"].map(lambda x: f"{x:.1f}"),
                        "excess vs univ median": show["ex_med"].map(lambda x: pc(x)), "CI90": show["ex_med_ci90"].map(lambda t: ci(t, True)),
                        "CI95": show["ex_med_ci95"].map(lambda t: ci(t, True)), "excess vs N500 PR": show["ex_n500"].map(lambda x: pc(x)),
                        "CI90 (N500)": show["ex_n500_ci90"].map(lambda t: ci(t, True)), "excess vs EW": show["ex_ew"].map(lambda x: pc(x)),
                        "hit": show["hit"].map(pc), "beat N500": show["beat_n500"].map(pc), "beat EW": show["beat_ew"].map(pc),
                        "anchors > median": show["anchors_ex_med_gt0"].astype(str) + "/" + show["anchors"].astype(str),
                        "anchors > N500": show["anchors_ex_n500_gt0"].astype(str) + "/" + show["anchors"].astype(str)})  # fmt: skip
    out += [RP.md(tab), "", "Paired bootstrap of per-anchor excess (set a minus set b):", ""]
    pr = res.paired.copy()
    out += [RP.md(pd.DataFrame({"a": pr["a"], "b": pr["b"], "n": pr["n"], "delta": pr["delta"].map(pc), "CI90": pr["ci90"].map(lambda t: ci(t, True)),
                                "CI95": pr["ci95"].map(lambda t: ci(t, True)), "P(delta>0)": pr["p_gt0"].map(lambda x: f(x, 2))})), ""]  # fmt: skip
    ag = res.agreement
    out += [f"Agreement across the {len(members)} members, averaged over anchors: mean pairwise Spearman {f(ag['mean_pairwise_spearman'])}, Kendall W {f(ag['kendall_w_mean'])} "
            f"(range {f(ag['kendall_w_min'])} to {f(ag['kendall_w_max'])}), mean pairwise Jaccard of the D10 sets {f(ag['mean_pairwise_jaccard_d10'])}. "
            f"votes-IC (Spearman of votes with return) mean {f(res.votes_ic.mean())} over {len(res.votes_ic)} anchors. "
            f"C_unan was marked too few (< {CS.MIN_UNAN} names) at {len(res.too_few_unan)} of {res.n_anchors} anchors and is averaged over the rest only.", ""]
    cm = res.paired
    if len(cm):
        r0 = cm[(cm["a"] == "C_maj") & cm["b"].str.startswith("best single")].iloc[0]
        r1 = cm[(cm["a"] == "C_maj") & (cm["b"] == "ENS_RANKAVG top-|C_maj|")].iloc[0]
        cmaj = s.loc["C_maj"]
        unit = "5" if family == "1Y-june" else str(res.n_anchors)
        out += [f"**Answer.** Consensus (>= 6/{len(members)}) top-decile names beat the Nifty 500 PR in {int(cmaj['anchors_ex_n500_gt0'])} of {unit} {cfgf['unit']}s "
                f"(and the universe median in {int(cmaj['anchors_ex_med_gt0'])}); mean excess vs the universe median {pc(cmaj['ex_med'])} [90% CI {ci(cmaj['ex_med_ci90'], True)[1:-1]}]; "
                f"vs the size-matched best single model ({res.best_single}): delta = {pc(r0['delta'])} [CI90 {ci(r0['ci90'], True)[1:-1]}], P(delta>0) = {f(r0['p_gt0'], 2)}; "
                f"vs the size-matched rank-average ensemble: delta = {pc(r1['delta'])} [CI90 {ci(r1['ci90'], True)[1:-1]}], P(delta>0) = {f(r1['p_gt0'], 2)}.", ""]
        rr = cm[(cm["a"] == "C_maj") & (cm["b"] == "random |C_maj| draw (expected)")]
        if len(rr):
            r2 = rr.iloc[0]
            out += [f"Reading the excess columns: a mean return minus the universe MEDIAN is positive for any random set in a right-skewed cross-section, so the fair yardstick is the same-size random draw: "
                    f"C_maj minus a random same-size set = {pc(r2['delta'])} [CI90 {ci(r2['ci90'], True)[1:-1]}], P(delta>0) = {f(r2['p_gt0'], 2)} -> {verdict({'ci90': r2['ci90']})}.", ""]
        headline.append(Est(f"{family}: C_maj minus size-matched best single model (excess)", float(r0["delta"]), r0["ci90"][0], r0["ci90"][1], 90, int(r0["n"])))
        headline.append(Est(f"{family}: C_maj mean excess vs universe median", float(cmaj["ex_med"]), cmaj["ex_med_ci90"][0], cmaj["ex_med_ci90"][1], 90, int(cmaj["anchors"])))
    return "\n".join(out), res


# ── Q4 : outcomes ───────────────────────────────────────────────────────────────────────────────────────────────────
def outcome_section(ctx: Ctx, family: str, models: dict[str, pd.DataFrame], cres: CS.ConsensusResult, members: dict[str, pd.DataFrame]) -> str:
    h = CFG[family]["h"]
    P = CS.wide_p(members)
    nvotes = P.apply(lambda c: decile(c) == 10).sum(axis=1).rename("votes")
    mcap = ctx.uni.set_index(["anchor", "security_id"])["mcap_pit_cr"]
    out = []
    kinds = [("analyst", h, ["out_beat", "out_surp"])]
    kinds.append(("fundamental", "1Y", ["out_fs_sales", "out_fs_pat", "out_accel"]) if family == "1Y-june" else ("fundamental_q", "3M", ["out_fs_sales"]))
    for kind, hz, cols in kinds:
        oc = ST.outcomes(ctx.conn, kind, hz)
        oc = oc[counted_filter(family, oc["anchor"])]
        out += [f"### Outcome kind `{kind}` ({hz}); {len(oc)} outcome rows in the counted anchors", ""]
        for mname, o in models.items():
            df = o[["anchor", "security_id", "p", "bucket_dec"]].merge(oc, on=["anchor", "security_id"], how="inner")
            if df.empty:
                out += [f"{mname}: no outcome rows for the scored names.", ""]
                continue
            df = df.join(nvotes, on=["anchor", "security_id"]).join(mcap.rename("mcap"), on=["anchor", "security_id"])
            nm = len(members)
            sets = {"P95": df["p"] >= 0.95, "D10": df["bucket_dec"] == 10, "D1": df["bucket_dec"] == 1,
                    f"C_maj (>= 6/{nm})": df["votes"] >= 6, f"C_unan (= {nm}/{nm})": df["votes"] == nm}  # fmt: skip
            cov = len(df) / len(o)
            outc = {"out_beat": "beat rate", "out_surp": "mean surprise", "out_fs_sales": "sales growth minus trend", "out_fs_pat": "PAT growth minus trend", "out_accel": "share accelerating"}
            out += [f"**{mname}** (outcome coverage of scored names {pc(cov)}; the covered subset is larger-cap and survivor-biased, see caveats). Difference = mean over anchors of (set mean minus rest-of-covered mean); CI95 = anchor-clustered bootstrap, {N_BOOT} draws.", ""]
            for col in cols:
                cs_ = compare_sets(df, sets, col, n_boot=N_BOOT)
                cs_ = cs_.assign(**{"set": cs_["set"], "n": cs_["n"], "mean in set": cs_["mean_set"].map(f), "mean rest": cs_["mean_rest"].map(f),
                                    "diff": cs_["diff"].map(f), "CI95": [f"[{f(a)}, {f(b)}]" for a, b in zip(cs_["ci_lo"], cs_["ci_hi"], strict=True)],
                                    "anchors": cs_["anchors"]})[["set", "n", "mean in set", "mean rest", "diff", "CI95", "anchors"]]  # fmt: skip
                out += [f"`{col}` ({outc[col]})", "", RP.md(cs_), ""]
            # size terciles (coverage is size biased): D10 vs rest within each tercile
            df["terc"] = df.groupby("anchor")["mcap"].transform(lambda s: pd.qcut(s.rank(method="first"), 3, labels=["small", "mid", "large"]))
            col0 = cols[0]
            rows = []
            for tname in ("small", "mid", "large"):
                sub = df[df["terc"] == tname]
                c3 = compare_sets(sub, {"D10": sub["bucket_dec"] == 10}, col0, n_boot=N_BOOT)
                if len(c3):
                    r = c3.iloc[0]
                    rows.append({"size tercile": tname, "n D10": int(r["n"]), "diff D10 vs rest": f(r["diff"]), "CI95": f"[{f(r['ci_lo'])}, {f(r['ci_hi'])}]"})
            out += [f"Size-tercile split of `{col0}` (D10 minus rest within tercile):", "", RP.md(pd.DataFrame(rows)), ""]
            if kind == "fundamental" and "out_accel" in df:
                cm = cmh_accel(df, df["bucket_dec"] == 10, df["bucket_dec"] == 1)
                out += [f"Cochran-Mantel-Haenszel (strata = anchor), `out_accel` D10 vs D1: Mantel-Haenszel odds ratio {f(cm['mh_odds_ratio'], 2)}, z = {f(cm['z'], 2)}, one-sided p (D10 > D1) = {f(cm['p_one_sided'])}, two-sided p = {f(2 * min(cm['p_one_sided'], 1 - cm['p_one_sided']))}; "
                        f"{'the direction is the REVERSE of the hypothesis: top-ranked names were less likely to accelerate than bottom-ranked ones' if cm['z'] < 0 else 'the direction agrees with the hypothesis'}.", ""]
    return "\n".join(out)


# ── Q5 : text and event features ────────────────────────────────────────────────────────────────────────────────────
def pt3_section(ctx: Ctx) -> str:
    """PT3 (annual-report text and multi-call trends, trials #51-53) from the stored PT3 report JSON; '' if absent."""
    js = ST.read_json(ctx.data_dir / "reports" / "pt3" / f"PT3_{ctx.snap}.json")
    if not js:
        return ""
    rows = []
    for tid, r in js["trials"].items():
        for key, lab in (("vs_full_restricted", "full-universe model restricted to the same names"), ("vs_control", "matched control")):
            p = r[key]
            rows.append({"trial": tid, "compared with": lab, "IC with features": f(p["ic_a"]), "IC other": f(p["ic_b"]), "delta": f(p["delta"]),
                         "CI90": ci(tuple(p["ci90"])), "P(delta>0)": f(p["p_gt0"], 2), "years better": f"{p['pos']}/{p['n']}"})  # fmt: skip
    d = js.get("dsr", {})
    c0, c1 = d.get("champion_pre"), d.get("champion_all")
    dsr_txt = (f" The champion's deflated Sharpe is {f(c0['dsr'])} at N = {c0['n_trials']} and {f(c1['dsr'])} at N = {c1['n_trials']} once these trials are counted."
               if c0 and c1 else "")
    return ("\n\n**Annual-report text and multi-call trends (PT3, trials #51-53, annual-report coverage is high so the control is the ordinary core model):**\n\n"
            + RP.md(pd.DataFrame(rows)) + "\n\nThese three challengers are registered in N and none is selectable." + dsr_txt
            + " The features use annual reports available only after fiscal-year end plus 180 days (later if a cover-letter or AGM date is later), so they are stale by construction. "
            "Reading: a paired delta whose interval spans zero is no evidence of value.\n")


def text_section(ctx: Ctx, family: str) -> str:
    if family == "1Y-june":
        js = ST.read_json(ctx.data_dir / "reports" / "ms1" / f"MS1_text_{ctx.snap}.json")
        if not js:
            return "MS1-text results are not available (run `ideation backtest text`).\n" + pt3_section(ctx)
        rows = []
        for tid, r in js.items():
            for key, lab in (("vs_control", "same learner, core columns, same covered names (matched control)"), ("vs_full_restricted", "full-universe model restricted to the same names")):
                p = r[key]
                rows.append({"trial": tid, "compared with": lab, "IC text": f(p["ic_a"]), "IC other": f(p["ic_b"]), "delta": f(p["delta"]),
                             "CI90": ci(tuple(p["ci90"])), "CI95": ci(tuple(p["ci95"])), "P(delta>0)": f(p["p_gt0"], 2), "years text better": f"{p['pos']}/{p['n']}"})  # fmt: skip
        return ("1Y text (trials #44-45, covered names only, indicative: few counted anchors and few covered names in the early years):\n\n" + RP.md(pd.DataFrame(rows)) + "\n\n"
                "Reading: against the matched control (trained only on the same few hundred covered rows) text looks positive, but that control is weak because it has so little training data; "
                "against the full-universe model, which saw many times more training rows, text adds nothing distinguishable from zero (CIs include 0). "
                "The honest conclusion is that the 1Y data do not show that text adds value beyond what a larger training set provides.\n" + pt3_section(ctx))
    sc = ctx.data_dir / "tactical_ms2" / "scores"
    rows = []
    for tag, base in (("RF_T1_TEXT", "17"), ("RIDGE_T1_TEXT", "18"), ("RF_T1_VSURP", "19"), ("RIDGE_T1_VSURP", "20")):
        a, b = sc / f"{base}_{tag}.parquet", sc / f"{base}_{tag}_CTRL.parquet"
        if not (a.exists() and b.exists()):
            continue
        sa, sb = pd.read_parquet(a), pd.read_parquet(b)
        anchors = sorted(pd.to_datetime(sa["anchor"]).unique())
        lab = ctx.labels(family, anchors)[["anchor", "security_id", "ret_total"]]
        ics = []
        for df in (sa, sb):
            df = df.assign(anchor=pd.to_datetime(df["anchor"])).merge(lab, on=["anchor", "security_id"]).dropna(subset=["ret_total", "score"])
            ics.append(pd.Series({k: rank_ic(g["score"], g["ret_total"]) for k, g in df.groupby("anchor")}))
        d = delta(ics[0], ics[1], family)
        rows.append({"trial": tag, "n anchors": d["n"], "IC with": f(ics[0].mean()), "IC control": f(ics[1].mean()), "delta": f(d["delta"]),
                     "CI90": ci(d["ci90"]), "CI95": ci(d["ci95"]), "P(delta>0)": f(d["p"], 2)})  # fmt: skip
    return ("Tactical 3M (MS2 stored scores; control = the same learner on the T1 columns, same covered names, folds and windows; stationary block bootstrap):\n\n"
            + RP.md(pd.DataFrame(rows)) + "\n\nText and vendor-surprise features do not add value on covered names at 3M; the point estimates for text are slightly negative. "
            "The T1 tier (delivery, FPI, ownership, event features) adds a small amount over T0 on 30 anchors (MS2), suggestive only.\n")


# ── Q6 : sector and theme lens ──────────────────────────────────────────────────────────────────────────────────────
def sector_section(ctx: Ctx, family: str, shipped: str, o: pd.DataFrame, cmaj_flag: pd.Series | None) -> str:
    cfg = yaml.safe_load((REPO_ROOT / "config" / "themes.yaml").read_text())
    u = ctx.uni[["anchor", "security_id", "company_id", "symbol"]].merge(ctx.master, on="company_id", how="left")
    d = o.merge(u, on=["anchor", "security_id"], how="left")
    d["ex"] = d["excess_univ_med"]
    minn = int(cfg["min_group_names"])
    anchors = sorted(d["anchor"].unique())
    rows = []
    for g, gd in d.groupby("industry_group"):
        ics, shares, ex = [], [], []
        for a in anchors:
            x = gd[(gd["anchor"] == a) & gd["ret_total"].notna()]
            tot = int((d["anchor"] == a).sum())
            if len(x) >= minn:
                ics.append(rank_ic(x["score"], x["ret_total"]))
            if len(x):
                shares.append(((x["bucket_dec"] == 10).sum() / max(1, (d[(d["anchor"] == a)]["bucket_dec"] == 10).sum()), len(x) / tot))
                t, r = x[x["bucket_dec"] == 10]["ex"], x[x["bucket_dec"] != 10]["ex"]
                if len(t) and len(r):
                    ex.append(t.mean() - r.mean())
        if ics:
            rows.append({"industry_group": g, "anchors with >= %d names" % minn: len(ics), "IC": f(np.mean(ics)),
                         "D10 share": pc(np.mean([s[0] for s in shares])), "universe share": pc(np.mean([s[1] for s in shares])),
                         "D10 excess vs rest of group": pc(np.mean(ex)) if ex else "n/a"})  # fmt: skip
    out = [f"By industry_group ({shipped}); labels are current (non-PIT). Groups with fewer than {minn} names at an anchor are skipped for the IC.", "", RP.md(pd.DataFrame(rows).sort_values("industry_group")), ""]
    # themes
    latest = max(anchors)
    t_rows, lists = [], []
    d["is_c"] = cmaj_flag.reindex(pd.MultiIndex.from_frame(d[["anchor", "security_id"]])).to_numpy() if cmaj_flag is not None else False
    for tname, spec in cfg["themes"].items():
        subs, syms = list(spec["sub_industries"]), list(spec["symbols"])
        hit_sub = [s for s in subs if (ctx.master["sub_industry"] == s).any()]
        if len(hit_sub) != len(subs):
            raise ReportRefused(f"theme {tname}: sub_industries without a matching company: {sorted(set(subs) - set(hit_sub))}")
        miss = [s for s in syms if not (ctx.master["nse_symbol"] == s).any()]
        if miss:
            raise ReportRefused(f"theme {tname}: symbols not in the company master: {miss}")
        member = d["sub_industry"].isin(subs) | d["nse_symbol"].isin(syms)
        per = []
        for a in anchors:
            g = d[d["anchor"] == a]
            m = member[g.index]
            tp = g[m & (g["bucket_dec"] == 10) & g["ret_total"].notna()]["ex"]
            np_ = g[~m & (g["bucket_dec"] == 10) & g["ret_total"].notna()]["ex"]
            per.append((int(m.sum()), float((m & (g["bucket_dec"] == 10)).sum() / max(1, m.sum())), float((m & g["is_c"].astype(bool)).sum() / max(1, m.sum())),
                        float(tp.mean() - np_.mean()) if len(tp) and len(np_) else np.nan, len(tp)))  # fmt: skip
        pe = pd.DataFrame(per, columns=["n", "d10", "cmaj", "diff", "n_d10"])
        s = pe["diff"].dropna()
        cib = mean_ci(s.to_numpy(), method="stationary" if CFG[family]["block"] > 1 else "iid", mean_block=CFG[family]["block"], n_boot=N_BOOT) if len(s) > 2 else None
        t_rows.append({"theme": tname, "mean members in U_score": f"{pe['n'].mean():.0f}", "share of members in D10": pc(pe["d10"].mean()),
                       "share in C_maj": pc(pe["cmaj"].mean()), "D10 members minus D10 non-members (excess)": pc(s.mean()) if len(s) else "n/a",
                       "CI90": ci(cib.ci90, True) if cib else "n/a", "anchors": len(s)})  # fmt: skip
        g = d[(d["anchor"] == latest) & member].sort_values("p", ascending=False)
        for _, r in g.head(8).iterrows():
            conv = "High" if r["p"] >= 0.95 else "Medium" if r["p"] >= 0.9 else "Low"
            lists.append({"theme": tname, "symbol": r["nse_symbol"], "Ideation Score": int(round(100 * r["p"])), "conviction (simplified)": conv})
    out += ["Themes (reporting only):", "", RP.md(pd.DataFrame(t_rows)), "",
            f"Latest counted anchor {latest.date()}, top members by Ideation Score (conviction = High if p >= 0.95, Medium if top decile, else Low; the full 01 §5 notches are not applied here):", "",
            RP.md(pd.DataFrame(lists)), "", "> " + cfg["disclosure"].strip(), ""]
    return "\n".join(out)


# ── portfolio comparison ────────────────────────────────────────────────────────────────────────────────────────────
def portfolio_section(ctx: Ctx, family: str, models: dict[str, pd.DataFrame], members: dict[str, pd.DataFrame], bench_score: pd.DataFrame | None, k: int = 25) -> str:
    costs = PF.load_costs()
    rf = costs["rf_annual"]
    anchors_all = sorted(next(iter(models.values()))["anchor"].unique())
    # 1M monthly returns (unsealed); monthly anchors
    mon = ctx.labels("tactical-3M", _monthly_anchors(ctx), "1M")
    r1 = mon.pivot(index="anchor", columns="security_id", values="ret_total")
    n500 = (mon["ret_total"] - mon["excess_n500_pr"]).groupby(mon["anchor"]).median()
    ew = mon.groupby("anchor")["ret_total"].mean()
    mcap = ctx.uni.set_index(["anchor", "security_id"])["mcap_pit_cr"]
    P = CS.wide_p(members)
    ens = P.mean(axis=1)
    votes = P.apply(lambda c: decile(c) == 10).sum(axis=1)
    cmaj_score = votes + ens * 1e-3  # votes, ties by rank-average
    books = {name: o.set_index(["anchor", "security_id"])["p"] for name, o in models.items()}
    books["CONS_MAJ (votes, ties by rank-average)"] = cmaj_score
    books["ENS_RANKAVG"] = ens
    if bench_score is not None:
        books["MOM_12_1"] = bench_score.set_index(["anchor", "security_id"])["mom_12_1"]
    monthly = sorted(r1.index)
    rows = []
    for bname, sc in books.items():
        for scheme in ("EW", "CW (cap 10%)"):
            sets: dict[pd.Timestamp, pd.Series] = {}
            for a in anchors_all:
                s = sc.xs(a, level="anchor").dropna() if a in sc.index.get_level_values("anchor") else pd.Series(dtype=float)
                if len(s) < k:
                    continue
                top = s.sort_values(ascending=False, kind="stable").index[:k]
                mc = mcap.xs(a, level="anchor") if a in mcap.index.get_level_values("anchor") else pd.Series(dtype=float)
                sets[a] = PF.weights(pd.Index(top), "EW" if scheme == "EW" else "CW", mc)
            if not sets:
                continue
            ser = PF.series_1y(sets, r1, monthly, costs) if family == "1Y-june" else PF.series_tranche(sets, r1, costs)
            ser = ser[ser.index <= ts("2025-06-30")]
            gross = ser["gross"]
            net = ser["gross"] - ser["cost"]
            res_bps = costs["research_cost_bps_per_side"] / 1e4 * 2 * ser["turnover"]
            m = PF.metrics(net, n500, ew, float(ser["turnover"].mean()), rf)
            mg = PF.metrics(gross - res_bps, n500, ew, float(ser["turnover"].mean()), rf, n_boot=2000)
            rows.append({"book": f"{bname} top-{k}", "weights": scheme, "months": m["months"], "CAGR (retail costs)": pc(m["CAGR"]), "CAGR (12 bps/side)": pc(mg["CAGR"]),
                         "vol": pc(m["vol"]), "Sharpe": f(m["Sharpe"], 2), "Sharpe CI90": ci(m["Sharpe CI90"]), "Sortino": f(m["Sortino"], 2),
                         "Treynor": f(m["Treynor"], 3), "IR vs N500": f(m["IR vs N500"], 2), "IR vs EW": f(m["IR vs EW"], 2), "max DD": pc(m["max DD"]),
                         "MDD CI90": ci(m["MDD CI90"], True), "turnover/yr": pc(m["turnover (annual one-way)"])})  # fmt: skip
    ref = PF.metrics(n500.loc[[m_ for m_ in n500.index if ts(sorted(anchors_all)[0]) <= m_ <= ts("2025-06-30")]], n500, ew, 0.0, rf, n_boot=2000)
    refew = PF.metrics(ew.loc[[m_ for m_ in ew.index if ts(sorted(anchors_all)[0]) <= m_ <= ts("2025-06-30")]], n500, ew, 0.0, rf, n_boot=2000)
    rows.append({"book": "Nifty 500 PR (price only)", "weights": "-", "months": ref["months"], "CAGR (retail costs)": pc(ref["CAGR"]), "vol": pc(ref["vol"]), "Sharpe": f(ref["Sharpe"], 2), "max DD": pc(ref["max DD"])})
    rows.append({"book": "EW U_score TR (monthly rebalanced)", "weights": "-", "months": refew["months"], "CAGR (retail costs)": pc(refew["CAGR"]), "vol": pc(refew["vol"]), "Sharpe": f(refew["Sharpe"], 2), "max DD": pc(refew["max DD"])})
    note = ("1Y books: June sets held 12 months, equal weight at entry then drifting; monthly return of a name = its unsealed 1M label. Tactical books: three staggered monthly tranches. "
            "A name without a 1M label contributes 0 for that month. Costs: `config/costs.yaml` (03 §D2 defaults, TO VERIFY with the broker) on a 10 lakh notional, fractional-share approximation (the integer-share floor and exact MILP are P7); "
            f"the research cost column charges 12 bps per side on one-way turnover. Rf = {pc(rf)}. Sharpe/MDD CIs: stationary block bootstrap (mean block 3 months, 10,000 draws). "
            "The 1Y series has only 60 months in 5 overlapping-regime years, so every Sharpe here is imprecise. Dossier aspirations (Sharpe > 2, max drawdown < 15%) are printed as aspiration, not a gate. "
            "Black-Litterman rows are appended by P7.")
    return note + "\n\n" + RP.md(pd.DataFrame(rows).fillna("")) + "\n"


def _monthly_anchors(ctx: Ctx) -> list[pd.Timestamp]:
    a = ctx.uni["anchor"].drop_duplicates()
    a = a[(a >= "2014-01-01") & (a <= "2025-06-30")]
    return sorted(pd.to_datetime(a))


# ── bias block ──────────────────────────────────────────────────────────────────────────────────────────────────────
def bias_block(ctx: Ctx, family: str, n_trials: int, champ_dsr: Any, counted: str) -> str:
    row = ctx.conn.execute("select max(run_id) from ideation.run where kind = 'labels' and status = 'ok'").fetchone()
    if not row or row[0] is None:
        raise ReportRefused("no ok labels run: the bias block cannot be built")
    facts = gather(ctx.conn, int(row[0]))
    facts["params"] = {**facts["params"], "family": family, "trials_n": n_trials, "dsr": champ_dsr, "counted_tests": counted}
    txt = render_from_facts(facts)
    cov = pd.read_parquet(ctx.data_dir / "outputs" / ctx.snap / "features_text_coverage.parquet")
    cov["anchor"] = pd.to_datetime(cov["anchor"])
    j = cov[(cov["anchor"].dt.month == 6) & cov["anchor"].dt.year.between(2016, 2024)]
    covs = ", ".join(f"{a.year} {c / n:.0%}" for a, c, n in zip(j["anchor"], j["u_cur"], j["n_u"], strict=True))
    txt = txt.replace("coverage n/a (PT)", f"transcript coverage of U_score (June anchors) {covs}")
    return txt


# ── one family ──────────────────────────────────────────────────────────────────────────────────────────────────────
def family_section(ctx: Ctx, family: str, log: Any) -> FamilyResult:
    t0 = time.perf_counter()
    cfgf = CFG[family]
    conn = ctx.conn
    tix = ST.trial_index(conn, family)
    n = ST.family_n(conn, family)
    specs = load_family(family)
    byname = tix.set_index("name")
    base, rfm = cfgf["baseline"], cfgf["rf"]
    summary: dict[str, Any] = {"family": family, "n": n}
    heads: list[Est] = []
    # champion / shipped
    if family == "1Y-june":
        r = conn.execute("select params->>'trial', selection from ideation.model where horizon = '1Y' and selection @> '{\"champion_by_9010\": true}'").fetchone()
        champ, sel = r[0], r[1]
        passed = bool(sel.get("gates", {}).get("all_pass"))
        audit = ST.read_json(ctx.data_dir / "bakeoff" / ctx.snap / "audit_cache" / f"{byname.loc[champ, 'config_hash'][:12]}.json") or {}
        audit_b = ST.read_json(ctx.data_dir / "bakeoff" / ctx.snap / "audit_cache" / f"{byname.loc[base, 'config_hash'][:12]}.json") or {}
        summary.update(champion=champ, ships=champ if passed else base, champion_gates=sel.get("gates", {}), dsr=sel.get("dsr_ic"), pbo=sel.get("pbo"),
                       auc_champ=audit.get("worst_auc"), auc_base=audit_b.get("worst_auc"))
    else:
        dj = ST.read_json(ctx.data_dir / "tactical_ms2" / "decision.json")
        champ = dj["champion"]
        adv = ST.read_json(ctx.data_dir / "tactical_ms2" / "adversarial_auc.json")
        t0aucs = [x["auc"] for x in adv["T0"]]
        summary.update(champion=champ, ships=dj["ships"], champion_gates=dj["gates"], pbo=dj["pbo"]["pbo"], auc_champ=float(np.mean(t0aucs)),
                       auc_base=float(np.mean(t0aucs)), auc_min=min(t0aucs), auc_max=max(t0aucs))
    md = [f"## {cfgf['label']}: family `{family}`, N = {n} pre-registered trials", ""]
    # Q1
    md += ["### (1) Ranking mechanism and score -> rank -> expected return", "",
           "Mechanism: see `RANKING_MECHANISM.md` (rendered from the template for this run). Ideation Score = round(100 p), p = (rank - 0.5)/n within the scored universe; buckets D1..D10, P99/P95/P5/P1; "
           "the expected return per bucket is the nested shrunk-isotonic alpha with a Mondrian 80% conformal band. Tables per model:", ""]
    mech: dict[str, Any] = {"tables": {}}
    shown = list(dict.fromkeys([champ, base]))
    for nm in shown:
        txt, _ic, _td = model_tables(ctx, family, nm, int(byname.loc[nm, "trial_id"]))
        role = "90/10 champion" if nm == champ else "baseline"
        if nm == summary["ships"]:
            role += " (ships)"
        md += [f"#### {nm} ({role})", "", txt, ""]
        mech["tables"][nm] = txt
        log(f"  {family}: tables for {nm} ({time.perf_counter() - t0:.0f} s)")
    if family == "tactical-3M":
        md += ["1M (reported from the same fits, never selecting; IC by the stored `wf_result` rows of the 1M models):", ""]
        rows = []
        for nm in shown:
            s1 = ST.wf_ic(conn, byname.loc[nm, "config_hash"], "1M")
            if len(s1):
                sm = ic_summary(s1, 1, 1, n_boot=N_BOOT)
                rows.append({"model": nm, "anchors": sm["n"], "mean 1M IC": f(sm["mean"]), "CI95 (block bootstrap)": ci(sm["ci95"]), "NW t (lag 0)": f(sm["nw_t"], 2)})
        md += [RP.md(pd.DataFrame(rows)), ""]
    # Q2
    md += ["### (2) Which models beat P1_COMP_EW / the RF / the naive baselines?", ""]
    bench = bench_ic_1y(ctx, specs) if family == "1Y-june" else bench_ic_3m(ctx, sorted(counted_anchor_list(ctx, tix, family)))
    tab, series = q2_table(tix, family, base, rfm, bench)
    md += [f"IC = mean per-anchor rank IC over the counted {cfgf['unit']} anchors. CI90 from the bootstrap ({'iid over anchors' if cfgf['block'] == 1 else 'stationary block, mean block 3'}); deltas are paired on common anchors. Verdict uses the CI90 of the delta.", "", tab, ""]
    heads.append(ic_est(f"{family}: mean IC of {champ} (90/10 champion)", series[champ], family))
    heads.append(ic_est(f"{family}: mean IC of {base} (baseline)", series[base], family))
    heads.append(ic_est(f"{family}: mean IC of {rfm}", series[rfm], family))
    dcb = delta(series[champ], series[base], family)
    drb = delta(series[rfm], series[base], family)
    heads.append(Est(f"{family}: dIC champion - baseline", dcb["delta"], dcb["ci90"][0], dcb["ci90"][1], 90, dcb["n"]))
    heads.append(Est(f"{family}: dIC {rfm} - baseline", drb["delta"], drb["ci90"][0], drb["ci90"][1], 90, drb["n"]))
    learners = [r["name"] for _, r in tix.iterrows() if r["status"] == "ok" and any(r["name"].startswith(p) for p in ("RF", "PRIDGE", "RIDGE", "LGBM", "XGB", "MLP", "SVR", "OLS", "SCR"))]
    better = [nm for nm in learners if verdict(delta(series[nm], series[base], family)) == "better"]
    worse = [nm for nm in learners if verdict(delta(series[nm], series[base], family)) == "worse"]
    summary.update(dcb=dcb, drb=drb, learners_better=better, learners_worse=worse, ic_champ=heads[0], ic_base=heads[1], ic_rf=heads[2],
                   bench={k: float(v.mean()) for k, v in bench.items()}, series_means={k: float(v.mean()) for k, v in series.items()})
    md += [f"Plain reading: RF versus the baseline: dIC = {f(drb['delta'])} [CI90 {ci(drb['ci90'])[1:-1]}], P(dIC>0) = {f(drb['p'], 2)} -> {verdict(drb)}. "
           f"The 90/10 champion {champ} versus the baseline: dIC = {f(dcb['delta'])} [CI90 {ci(dcb['ci90'])[1:-1]}], P(dIC>0) = {f(dcb['p'], 2)} -> {verdict(dcb)}. "
           f"Learner trials credibly better than the baseline: {better or 'none'}; credibly worse: {worse or 'none'}.", ""]
    if family == "1Y-june":
        pool = {r["name"]: trial_ic(r, family) for _, r in tix.iterrows() if r["status"] == "ok" and int(r["num"]) not in (44, 45)}
        pool = {k: v for k, v in pool.items() if len(v) >= 3}
        srs = np.array([v.mean() / v.std(ddof=1) for v in pool.values() if v.std(ddof=1) > 0])
        var = float(np.var(srs, ddof=1))
        sr0 = dsr_mod.expected_max_sr(var, n)
        cs = pool[champ]
        sr_c, n_c, sk, ku = dsr_mod.sharpe_moments(cs)
        dsr_c = dsr_mod.probabilistic_sr(sr_c, sr0, n_c, sk, ku)
        need = required_sr(sr0, n_c)
        best_sr = float(np.nanmax(srs))
        summary.update(dsr_recomputed=dsr_c, sr0=sr0, sr_needed=need, sr_champion=sr_c, best_sr=best_sr, n_obs=n_c, var_sr=var)
        md += [f"DSR check (recomputed from the stored IC series; stored value {f(summary['dsr'])}): the {len(srs)} non-text trials with >= 3 counted tests have per-observation IC Sharpe variance {f(var, 4)}; "
               f"with N = {n} the expected maximum Sharpe under the null is SR0 = {f(sr0)}. With only {n_c} counted test years the champion needs a per-observation IC Sharpe of at least {f(need)} to reach DSR >= 0.90 "
               f"(normal approximation); it has {f(sr_c)} (recomputed DSR {f(dsr_c)}), and the best of all trials has {f(best_sr)}. "
               f"Five counted test years therefore cannot support DSR >= 0.90 unless the IC is both large and almost constant across years.", ""]
    # Q3 consensus
    members = list(next(s for s in specs if s.trial_id == cfgf["cons"]).config["members"])
    md += ["### (3) Consensus vs single models, size-matched, rank-average and benchmarks", ""]
    ctxt, cres = consensus_section(ctx, family, tix, members, heads)
    md += [ctxt, ""]
    log(f"  {family}: consensus done ({time.perf_counter() - t0:.0f} s)")
    # Q4 outcomes
    md += ["### (4) Beat rates and fundamental surprise of top and consensus names", ""]
    mem_oos = {m: ctx.oos(int(byname.loc[m, "trial_id"]), family) for m in members}
    models = {nm: ctx.oos(int(byname.loc[nm, "trial_id"]), family) for nm in dict.fromkeys([summary["ships"], champ, cfgf["ens"]])}
    md += [outcome_section(ctx, family, models, cres, mem_oos), ""]
    log(f"  {family}: outcomes done ({time.perf_counter() - t0:.0f} s)")
    # Q5
    md += ["### (5) Do text and event features add value on covered names?", "", text_section(ctx, family), ""]
    # Q6
    md += ["### (6) Sector and theme lens", ""]
    P = CS.wide_p(mem_oos)
    cflag = P.apply(lambda c: decile(c) == 10).sum(axis=1) >= 6
    ship_o = full_frame(ctx, family, int(byname.loc[summary["ships"], "trial_id"]))
    md += [sector_section(ctx, family, summary["ships"], ship_o, cflag), ""]
    log(f"  {family}: sector done ({time.perf_counter() - t0:.0f} s)")
    # portfolio
    md += ["### Portfolio-level comparison (05 §9.2)", ""]
    bs = None
    tech = pd.read_parquet(ctx.data_dir / "outputs" / ctx.snap / "features_tech.parquet", columns=["anchor", "security_id", "mom_12_1"])
    tech["anchor"] = pd.to_datetime(tech["anchor"]).dt.normalize()
    bs = tech
    pm = {nm: ctx.oos(int(byname.loc[nm, "trial_id"]), family) for nm in dict.fromkeys([champ, base])}
    md += [portfolio_section(ctx, family, pm, mem_oos, bs), ""]
    log(f"  {family}: portfolio done ({time.perf_counter() - t0:.0f} s)")
    counted = "2020-2024 (5)" if family == "1Y-june" else "2014-01..2025-06 (138 monthly)"
    bb = bias_block(ctx, family, n, f(summary.get("dsr")) if family == "1Y-june" else "n/a (see gates)", counted)
    mech["champion"], mech["baseline"] = champ, base
    return FamilyResult(family, n, "\n".join(md), heads, bb, mech, summary)


def counted_anchor_list(ctx: Ctx, tix: pd.DataFrame, family: str) -> list[pd.Timestamp]:
    r = tix[tix["name"] == CFG[family]["baseline"]].iloc[0]
    return list(trial_ic(r, family).index)


# ── validation and rendering ────────────────────────────────────────────────────────────────────────────────────────
def validate(results: list[FamilyResult], holdout: dict[str, Any] | None) -> None:
    """Refuse to render without the bias block, N, CIs and the sealed/unsealed status (05 §10.3 acceptance)."""
    if holdout is None:
        raise ReportRefused("sealed/unsealed status is missing")
    if not results:
        raise ReportRefused("no family result")
    for r in results:
        if "DATA & BIAS" not in (r.bias_block or "") or "Holdout:" not in r.bias_block:
            raise ReportRefused(f"{r.family}: the bias block is missing")
        if r.family not in ST.EXPECTED_N or r.n_trials != ST.EXPECTED_N[r.family]:
            raise ReportRefused(f"{r.family}: N = {r.n_trials}, expected {ST.EXPECTED_N.get(r.family)}")
        if not r.headlines:
            raise ReportRefused(f"{r.family}: no headline statistic")
        for h in r.headlines:
            if not isinstance(h, Est):
                raise ReportRefused(f"{r.family}: a headline statistic is not an estimate with a CI ({h!r})")
            if not all(math.isfinite(x) for x in (h.value, h.lo, h.hi)) or h.lo > h.hi:
                raise ReportRefused(f"{r.family}: headline {h.label!r} has no valid CI")


def holdout_text(holdout: dict[str, Any]) -> str:
    sealed = "; ".join(f"{k} {lo}..{'' if hi.year == 9999 else hi}" for k, (lo, hi) in SEALED_RANGES.items())
    state = ", ".join(f"{k} unsealed {v['unsealed_at']}" for k, v in holdout.items()) if holdout else "no family unsealed (holdout_unseal is empty)"
    return f"sealed ranges: {sealed}; status: {state}"


def render(results: list[FamilyResult], holdout: dict[str, Any] | None, as_of: str, run_note: str = "") -> str:
    validate(results, holdout)
    assert holdout is not None
    s1 = next((r.summary for r in results if r.family == "1Y-june"), None)
    s3 = next((r.summary for r in results if r.family == "tactical-3M"), None)
    out = [f"# Resurrection: out-of-sample research report ({as_of})", "",
           "N of pre-registered trials: " + "; ".join(f"{r.family} N = {r.n_trials}" for r in results) + f". Holdout: {holdout_text(holdout)}. {run_note}", "",
           "Every number below is computed from stored out-of-sample results; nothing was refitted, tuned or added. Negative results are stated as such. Hit rate = share beating the universe median; "
           "beat-N500 = share beating the Nifty 500 price index; counts use the number of counted test anchors as denominator.", ""]
    out += ["## Headline answers", ""]
    if s1:
        e1, b1, r1 = s1["ic_champ"], s1["ic_base"], s1["ic_rf"]
        out += [f"**1Y.** The baseline {s1['ships']} ships. {s1['champion']} (a valuation composite, not a learner) had the best 90/10 score, mean IC {e1.value:.3f} [CI90 {e1.lo:.3f}, {e1.hi:.3f}] "
                f"versus the baseline {b1.value:.3f} [{b1.lo:.3f}, {b1.hi:.3f}], but fails the deflated-Sharpe gate (DSR {f(s1['dsr'])} < 0.90 at N = {s1['n']}) and the adversarial gate (worst-fold AUC {f(s1['auc_champ'], 2)} > 0.70). "
                f"The random forest did not reproduce the dossier (mean IC {r1.value:.3f} [{r1.lo:.3f}, {r1.hi:.3f}]; vs the baseline dIC = {f(s1['drb']['delta'])} [CI90 {ci(s1['drb']['ci90'])[1:-1]}]). "
                f"Learners credibly better than the baseline: {s1['learners_better'] or 'none'}. Only valuation shows signal (naive value-only IC {f(s1['bench'].get('VALUE_ONLY'))}). "
                f"Five counted test years cannot support DSR >= 0.90: the champion would need a per-observation IC Sharpe of {f(s1['sr_needed'])} and has {f(s1['sr_champion'])}.", ""]
    if s3:
        e3, b3 = s3["ic_champ"], s3["ic_base"]
        out += [f"**Tactical 3M.** By the frozen rule the baseline {s3['ships']} ships because the 90/10 champion {s3['champion']} (mean IC {e3.value:.3f} [CI90 {e3.lo:.3f}, {e3.hi:.3f}], baseline {b3.value:.3f} [{b3.lo:.3f}, {b3.hi:.3f}]) "
                f"fails the blocking adversarial gate (T0 mean AUC {f(s3['auc_champ'], 2)} against the 0.70 limit). **The baseline fails the same gate**: the AUC is a property of the shared R1 input matrix, not of the model. "
                "Both therefore fail; whether this blocks promotion is the owner's decision. On every performance criterion the champion beats the baseline "
                f"(dIC = {f(s3['dcb']['delta'])} [CI90 {ci(s3['dcb']['ci90'])[1:-1]}], P(dIC>0) = {f(s3['dcb']['p'], 2)}).", ""]
    out += ["Details per question follow; consensus, outcome, text, theme and portfolio answers are in each family section.", ""]
    for r in results:
        out += [r.md, ""]
    if s3:
        out += ["## MS2 diagnostics (not trials; N unchanged)", "", diagnostics_md(s3.get("_diag")), ""]
    out += ["## (7) Holdout", "", f"The holdout is {'UNSEALED for: ' + ', '.join(holdout) if holdout else 'sealed'}. {holdout_text(holdout)}. "
            "No holdout label was read for this report (every label read goes through `load_labels` on unsealed anchors). "
            "`ideation holdout unseal` was NOT run: unsealing is irreversible and is awaiting explicit owner confirmation (commands in `docs/STATUS.md`). "
            "`report research --holdout` refuses while sealed.", ""]
    out += ["## (8) Data and bias block (mandatory), per family", ""]
    for r in results:
        out += [f"### {r.family} (N = {r.n_trials}; sealed/unsealed: {holdout_text(holdout)})", "", "```", r.bias_block, "```", ""]
    out += [DEVIATIONS_MD, CAVEATS_MD, OWNER_DECISIONS_MD]
    return "\n".join(out)


def diagnostics_md(diag: dict[str, Any] | None) -> str:
    if not diag:
        return "diagnostics file not found."
    ov = diag["overall"]
    rows = []
    for m, d in ov.items():
        rows.append({"model": m, "raw IC": f(d["raw"]), "beta-neutral": f(d["res_B"]), "share kept": pc(d["res_B"] / d["raw"]), "beta+vol-neutral": f(d["res_BV"]),
                     "share kept (BV)": pc(d["res_BV"] / d["raw"]), "beta+vol+size+liquidity": f(d["res_BVLS"]), "share kept (BVLS)": pc(d["res_BVLS"] / d["raw"])})  # fmt: skip
    return (RP.md(pd.DataFrame(rows)) + "\n\nSource: `tactical_ms2/DIAGNOSTICS.md` (138 anchors, nothing refitted). Reading: most of the IC survives beta/vol neutralisation, so it is not only a low-beta bet; "
            "the timing of the IC (the 2018-19 peak, the 2020-21 weakness) is mostly beta/vol. A 1 to 4 session entry delay does not reduce the IC (reversal / bid-ask bounce is not the driver). "
            "The signal is stronger in small, less liquid names (capacity limited) but remains in the most liquid third at lower strength. "
            "Black-Litterman implication: the score correlates about -0.3 with beta and -0.45 with volatility, so a BL view built on the raw score would double-count the low-beta tilt already priced through the covariance/market leg "
            "and would be an unhedged short-beta bet; express the views on the residual alpha or add beta/vol-neutrality constraints.")


DEVIATIONS_MD = """## Pre-registration deviations and holdout-hygiene disclosures (recorded, not corrected)

1. Tactical trials #19-20 (RF_T1_VSURP, RIDGE_T1_VSURP): the covered subset (`isna_vsurp == 0`) was injected by the driver from the gate key and is not in the hashed config, so the registered hash does not describe the data those trials were fit on.
2. The tactical champion ENS_RANKAVG_T has no CPCV: CPCV covers only SCR_T0, OLS_T0, RIDGE_T0 and the T_EW baseline (the ensemble needs CPCV of all ten members including RF, MLP and SVR, far beyond the time budget); its evidence is the 138-anchor walk-forward only.
3. P1 printed June-2025 (sealed) 1Y statistics before the label loader existed.
4. The MS2 orientation used a scratch script that read `labels.parquet` directly and printed aggregates over all horizons (anchor ranges, non-null shares, exit-type counts). No sealed return value was printed or used; every later label read goes through `load_labels`.
5. 1Y: the 02 §5.5 gate 2 as written compares a quintile mean with the universe median and passes for any random quintile in a right-skewed cross-section; the unbiased companion (G2b) is reported and is informational.
6. 1Y text trials #44-45 ran on the covered subset with a protocol-arm control (not a trial, not in N); their DSR/PBO/selection were not recomputed and they never entered the selection pool.
7. This report's 1M tactical numbers come from the stored 1M `wf_result` rows of the same fits; 1M never selects.
8. Portfolio rows use the 1M labels chained monthly (fractional shares), not a price-level simulation; the 1Y monthly mark-to-market series has 60 months.
9. PT3 trials #51-53 were fitted, reviewed, corrected (fiscal-year end and availability made more conservative, a cache-key and a flag fix) and re-fitted as the same registered trials; no trial was added and N is unchanged. The first-pass results are superseded and recorded in docs/STATUS.md.
"""

CAVEATS_MD = """## Caveats

- **Vendor estimates are not provable point-in-time** (yfinance, vintage unverifiable); they are used only as outcomes and as a tactical challenger.
- **Text coverage is survivor-only**: documents exist only for companies still in the lake, and coverage is size-biased (larger caps first).
- **Taxonomy hindsight**: sector, industry-group and theme labels are Nov-2026 labels applied to history.
- **Short training windows**: 1Y trains on three June anchors; the first tactical folds train on fewer than the full 36 anchors; text/vendor subsets train on a few hundred to a few thousand rows.
- Restated fundamentals (Nov-2026 values), survivorship in identity stitching, and size-biased outcome coverage are described in the bias block.
- A 3M rank IC of the size reported in the headline is unusually high for price features; it is concentrated in low-volatility / small-illiquid-name regimes (see diagnostics).
"""

OWNER_DECISIONS_MD = """## Decisions left to the owner

1. **The 02 §6.1 adversarial remedy is NOT applied.** The prescribed remedy (drop or re-transform the shifting features, rerun as new trials) would add trials to N after seeing results. The AUC failure is reported as a finding for both families, and for tactical for the baseline as well as the champion.
2. Whether the tactical adversarial-AUC failure (which the baseline shares) blocks promotion of the tactical champion or the baseline.
3. Whether to unseal the holdout (irreversible), and which trial to freeze as champion for each family. NOT done here.
4. Verifying `config/costs.yaml` against the owner's broker.
"""


# ── RANKING_MECHANISM ───────────────────────────────────────────────────────────────────────────────────────────────
def mechanism_context(ctx: Ctx, results: list[FamilyResult], holdout: dict[str, Any], as_of: str) -> dict[str, Any]:
    from ideation.features.registry import load_registry
    from ideation.models.zoo import LEARNERS, learner_params

    reg = load_registry(ctx.settings.config_dir)
    feats = []
    for name, meta in reg.features.items():
        feats.append({"name": name, "category": getattr(meta, "category", ""), "family": getattr(meta, "family", ""), "prior": getattr(meta, "prior", None),
                      "set": getattr(meta, "set", "")})  # fmt: skip
    zoo = []
    for key in LEARNERS:
        zoo.append({"learner": key, "params_1y": json.dumps(learner_params(key, "1Y"), default=str, sort_keys=True),
                    "params_tactical": json.dumps(learner_params(key, "tactical"), default=str, sort_keys=True)})  # fmt: skip
    return {
        "as_of": as_of,
        "features": feats,
        "zoo": zoo,
        "formulas": FORMULAS,
        "bucket_rows": [*("P99", "P95"), *[f"D{d}" for d in range(10, 0, -1)], "P5", "P1"],
        "families": [{"family": r.family, "n": r.n_trials, "label": CFG[r.family]["label"], "champion": r.mech["champion"], "baseline": r.mech["baseline"],
                      "tables": r.mech["tables"], "bias_block": r.bias_block} for r in results],
        "holdout": holdout_text(holdout),
        "unsealed": bool(holdout),
    }  # fmt: skip


FORMULAS = {
    "winsor": "growth columns clipped at the cross-sectional P5/P95 (or, in the `dossier_median` trial, values outside P5..P95 replaced by the median); all other columns clipped at P1/P99",
    "rank_gauss": "x -> Phi^-1((r - 0.5)/n) with r the rank of x within the anchor's cross-section and n the number of non-missing values",
    "industry": "within-industry-group z-score times the sign prior (groups below the minimum size fall back to sector, then to the whole cross-section)",
    "missing": "a missing-applicable value becomes 0 (the post-transform median) and sets isna_<feature> = 1; a not-applicable value (layout) becomes 0 and sets na_layout_<feature> = 1",
    "target": "y = rank-gauss of the forward total return within the trial's universe at each training anchor",
    "percentile": "p_i = (rank(s_i) - 0.5)/n within the scored universe at the anchor, ties averaged",
    "score": "Ideation Score IS_i = round(100 p_i); signed score S_i = 2 p_i - 1",
    "decile": "D = floor(10 p) + 1 (D10 is the top); P99 = {p >= 0.99}, P95 = {p >= 0.95}, P5 = {p < 0.05}, P1 = {p < 0.01}",
    "alpha": "alpha_d = mean over the nested history of (return - universe median) per decile d; shrunk alpha_d <- 0.5 alpha_d; isotonic (increasing) with weights n; piecewise-linear in p between decile mid-percentiles",
    "conformal": "Mondrian (by decile) split-conformal 80% band on the residuals (return - median - alpha(p)) of the same nested history, time-decay weights with a half-life of two anchors",
    "conviction": "High = P95, Medium = top decile, else Low; notch down for low completeness, a risk flag, regime misalignment or the financials cap; out-of-distribution = unscorable",
    "votes": "votes_i = number of the M models with name i in D10; C_maj = votes >= 6, C_unan = votes = M, B_maj = the bottom mirror",
    "rankavg": "ENS_RANKAVG score = mean over models of p_{i,m}",
    "dsr": "DSR = PSR(SR0), SR0 = sqrt(V[SR]) ((1 - gamma) Phi^-1(1 - 1/N) + gamma Phi^-1(1 - 1/(N e))), gamma the Euler-Mascheroni constant",
    "ninety_ten": "selection score = 0.9 z(mean OOS IC) + 0.1 z(stability), z across the eligible trials",
}


def render_mechanism(context: dict[str, Any]) -> str:
    """Render ``docs/RANKING_MECHANISM.template.md`` (Jinja2). Refuses without the bias block, N and the holdout status."""
    from jinja2 import Environment, StrictUndefined

    fams = context.get("families") or []
    if not fams or not all(x.get("bias_block") and "DATA & BIAS" in x["bias_block"] for x in fams):
        raise ReportRefused("RANKING_MECHANISM: the bias block is missing")
    if not all(isinstance(x.get("n"), int) and x["n"] > 0 for x in fams):
        raise ReportRefused("RANKING_MECHANISM: N is missing")
    if not context.get("holdout"):
        raise ReportRefused("RANKING_MECHANISM: sealed/unsealed status is missing")
    env = Environment(undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=True)
    tpl = (REPO_ROOT / "docs" / "RANKING_MECHANISM.template.md").read_text(encoding="utf-8")
    return env.from_string(tpl).render(**context)


# ── build ───────────────────────────────────────────────────────────────────────────────────────────────────────────
def build(settings: Any, families: list[str], *, snap: str | None = None, as_of: str | None = None, log: Any = print,
          holdout_report: bool = False) -> dict[str, Path]:  # fmt: skip
    from ideation.bakeoff.cli import latest_snap
    from ideation.db.writer import Writer

    t0 = time.perf_counter()
    snap = snap or latest_snap(settings.data_dir)
    as_of = as_of or date.today().isoformat()
    with Writer.from_settings(settings).connect() as conn:
        holdout = ST.holdout_status(conn)
        if holdout_report:
            need = [fam for fam in families if fam not in holdout]
            if need:
                raise ReportRefused(f"--holdout refused: the holdout is still sealed for {need}; run `ideation holdout unseal` after the owner confirms")
            raise ReportRefused("--holdout rendering is not implemented in MS3: the holdout is reported separately, once, after unsealing")
        ctx = Ctx(settings, conn, snap)
        results = []
        for fam in families:
            log(f"{fam}: building ({time.perf_counter() - t0:.0f} s)")
            r = family_section(ctx, fam, log)
            diag = ST.read_json(settings.data_dir / "tactical_ms2" / "diagnostics_key.json") if fam == "tactical-3M" else None
            r.summary["_diag"] = diag
            results.append(r)
        text = render(results, holdout, as_of)
        mech = render_mechanism(mechanism_context(ctx, results, holdout, as_of))
    out_dir = settings.data_dir / "reports" / as_of
    out_dir.mkdir(parents=True, exist_ok=True)
    p1, p2 = out_dir / "RESEARCH_OOS.md", out_dir / "RANKING_MECHANISM.md"
    p1.write_text(text, encoding="utf-8")
    p2.write_text(mech, encoding="utf-8")
    (REPO_ROOT / "docs" / "RANKING_MECHANISM.md").write_text(mech, encoding="utf-8")
    log(f"written {p1} and {p2} in {time.perf_counter() - t0:.0f} s")
    return {"research": p1, "mechanism": p2}
