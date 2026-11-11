"""Per-trial OOS metrics: tables R-A..R-D (05 §2), gate inputs (02 §5.5 + O3), consensus scores (05 §4).

Everything takes the long OOS frame of one trial: ``anchor, security_id, industry_group, labelled, ret_total,
excess_univ_med, excess_n500_pr, score, p, bucket_dec, label`` (+ ``label_end`` for calibration). The report
denominator is always the number of counted test anchors (06 item 16).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import rankdata

from ideation.cv.cpcv import CPCVSplit, cpcv_paths
from ideation.eval.consensus_math import votes as vote_counts
from ideation.eval.metrics import rank_ic, tail_order, turnover
from ideation.rank import calibrate, conformal
from ideation.rank.buckets import TABLE_ROWS, decile, membership, quintile
from ideation.rank.percentile import percentile

BPS = 12.0
N_BOOT = 10_000


def boot_ic_over_names(
    score: np.ndarray, label: np.ndarray, *, n_boot: int = N_BOOT, seed: int = 0, chunk: int = 500, workers: int = 4
) -> tuple[float, float]:
    """90% CI of the rank IC by resampling names (table R-B): vectorised Spearman, chunks ranked on ``workers`` threads
    (numpy's sort releases the GIL, so this stays inside the 4-thread budget)."""
    from concurrent.futures import ThreadPoolExecutor

    s, y = np.asarray(score, float), np.asarray(label, float)
    ok = np.isfinite(s) & np.isfinite(y)
    s, y = s[ok], y[ok]
    n = len(s)
    if n < 10:
        return float("nan"), float("nan")
    starts = list(range(0, n_boot, chunk))
    seeds = np.random.SeedSequence(seed).spawn(len(starts))

    def one(i: int) -> np.ndarray:
        b = min(chunk, n_boot - starts[i])
        idx = np.random.default_rng(seeds[i]).integers(0, n, size=(b, n))
        rs = rankdata(s[idx], axis=1)
        ry = rankdata(y[idx], axis=1)
        rs -= rs.mean(axis=1, keepdims=True)
        ry -= ry.mean(axis=1, keepdims=True)
        den = np.sqrt((rs * rs).sum(axis=1) * (ry * ry).sum(axis=1))
        return np.where(den > 0, (rs * ry).sum(axis=1) / np.where(den > 0, den, 1), np.nan)

    with ThreadPoolExecutor(workers) as ex:
        out = np.concatenate(list(ex.map(one, range(len(starts)))))
    lo, hi = np.nanquantile(out, [0.05, 0.95])
    return float(lo), float(hi)


def anchor_frame(g: pd.DataFrame) -> pd.DataFrame:
    return g[g["labelled"] & g["score"].notna()]


def per_anchor(oos: pd.DataFrame, *, n_boot: int = N_BOOT) -> pd.DataFrame:
    """Table R-B rows (one per OOS anchor, indicative included): IC with name-bootstrap CI, n, coverage, spreads."""
    rows = []
    prev_q5: set[int] | None = None
    prev_q1: set[int] | None = None
    for anchor, g in oos.groupby("anchor", sort=True):
        a = anchor_frame(g)
        p = percentile(a["score"])
        q = quintile(p)
        d = decile(p)
        r = a["ret_total"]
        ic = rank_ic(a["score"], a["label"])
        lo, hi = boot_ic_over_names(a["score"].to_numpy(), a["label"].to_numpy(), n_boot=n_boot)
        q5 = set(a.loc[(q == 5).to_numpy(), "security_id"])
        q1 = set(a.loc[(q == 1).to_numpy(), "security_id"])
        to5 = turnover(prev_q5, q5) if prev_q5 is not None else 1.0
        to1 = turnover(prev_q1, q1) if prev_q1 is not None else 1.0
        spread_q = float(r[q == 5].mean() - r[q == 1].mean()) if len(a) else np.nan
        cost = 2 * BPS / 1e4 * (to5 + to1)
        med = float(g["ret_total"].median())
        ex = a["excess_univ_med"]
        top_ex = float(ex[(q == 5).to_numpy()].mean())
        rows.append(
            {
                "anchor": pd.Timestamp(anchor),
                "n_scored": int(g["score"].notna().sum()),
                "n_labelled": len(a),
                "n_universe": len(g),
                "coverage": len(a) / len(g) if len(g) else np.nan,
                "ic": ic,
                "ic_lo": lo,
                "ic_hi": hi,
                "spread_d10_d1": float(r[d == 10].mean() - r[d == 1].mean()),
                "spread_q5_q1": spread_q,
                "turnover_q5": to5,
                "turnover_q1": to1,
                "spread_q5_q1_cost_adj": spread_q - cost,
                "top_q5_excess": top_ex,
                "top_q5_excess_cost_adj": top_ex - 2 * BPS / 1e4 * to5,
                "top_q5_vs_ew_cost_adj": float(r[(q == 5).to_numpy()].mean() - r.mean()) - 2 * BPS / 1e4 * to5,
                "hit_top": float((r[(d == 10).to_numpy()] > med).mean()),
                "top_ret": float(r[(d == 10).to_numpy()].mean()),
                "bottom_ret": float(r[(d == 1).to_numpy()].mean()),
                "bench_ret": med,
            }
        )
        prev_q5, prev_q1 = q5, q1
    return pd.DataFrame(rows).set_index("anchor")


def with_ew_benchmark(oos: pd.DataFrame) -> pd.DataFrame:
    """Add ``excess_ew`` = ret_total minus the equal-weight mean return of the labelled trial universe at the anchor."""
    out = oos.copy()
    ew = out[out["labelled"]].groupby("anchor")["ret_total"].mean()
    out["excess_ew"] = out["ret_total"] - out["anchor"].map(ew)
    return out


def bucket_by_anchor(oos: pd.DataFrame) -> pd.DataFrame:
    """Long frame of (anchor, bucket) summaries for the R-A rows."""
    rows = []
    d = with_ew_benchmark(oos)
    for anchor, g in d.groupby("anchor", sort=True):
        a = anchor_frame(g).copy()
        a["p"] = percentile(a["score"])
        m = membership(a["p"])
        for b in TABLE_ROWS:
            x = a[m[b].to_numpy()]
            if x.empty:
                continue
            rows.append(
                {
                    "anchor": pd.Timestamp(anchor),
                    "bucket": b,
                    "n": len(x),
                    "mean_excess": x["excess_univ_med"].mean(),
                    "median_excess": x["excess_univ_med"].median(),
                    "mean_ret": x["ret_total"].mean(),
                    "hit": (x["excess_univ_med"] > 0).mean(),
                    "beat_n500": (x["excess_n500_pr"] > 0).mean(),
                    "beat_ew": (x["excess_ew"] > 0).mean(),
                    "score_lo": x["score"].min(),
                    "score_hi": x["score"].max(),
                    "p_lo": x["p"].min(),
                    "p_hi": x["p"].max(),
                }
            )
    return pd.DataFrame(rows)


def monotonicity_rows(oos: pd.DataFrame, counted: Sequence[pd.Timestamp]) -> pd.DataFrame:
    """Table R-C per counted anchor: Spearman of decile vs decile mean return, top/bottom tail order tests."""
    rows = []
    for anchor, g in oos.groupby("anchor", sort=True):
        if pd.Timestamp(anchor) not in set(counted):
            continue
        a = anchor_frame(g).copy()
        a["p"] = percentile(a["score"])
        d = decile(a["p"])
        means = a["ret_total"].groupby(d.to_numpy()).mean()
        m = membership(a["p"])
        bm = pd.Series({b: a.loc[m[b].to_numpy(), "ret_total"].mean() for b in TABLE_ROWS})
        top, bottom = tail_order(bm)
        rows.append(
            {
                "anchor": pd.Timestamp(anchor),
                "spearman_decile": rank_ic(means.index.to_numpy(dtype=float), means.to_numpy()),
                "top_order": top,
                "bottom_order": bottom,
            }
        )
    return pd.DataFrame(rows).set_index("anchor")


def nested_calibration(
    oos: pd.DataFrame, counted: Sequence[pd.Timestamp], scheme: str
) -> dict[pd.Timestamp, dict[str, Any]]:
    """R5/R6 per counted test anchor. nested: history = counted anchors < T with label_end <= T; pooled: all counted."""
    out: dict[pd.Timestamp, dict[str, Any]] = {}
    base = oos[oos["labelled"] & oos["score"].notna()].copy()
    base["p"] = percentile(base["score"], by=base["anchor"].to_numpy()).to_numpy()
    base["excess"] = base["excess_univ_med"]
    cset = [pd.Timestamp(c) for c in counted]
    hist_pool = base[base["anchor"].isin(cset)]
    for t in cset:
        cal = calibrate.fit(hist_pool, t, counted=cset, sealed=(), scheme=scheme)  # type: ignore[arg-type]
        if cal is None:
            out[t] = {"cal": None}
            continue
        h = hist_pool[hist_pool["anchor"].isin(cal.anchors)].copy()
        h["resid"] = h["excess"] - cal.alpha(h["p"])
        bands = conformal.fit_bands(h[["anchor", "p", "resid"]])
        te = base[base["anchor"] == t].copy()
        alpha = cal.alpha(te["p"])
        lo, hi = conformal.band(te["p"], alpha, bands)
        te["alpha"], te["lo"], te["hi"] = alpha, lo, hi
        te["inside"] = ((te["excess"] >= lo) & (te["excess"] <= hi)).astype(float)
        out[t] = {
            "cal": cal,
            "bands": bands,
            "test": te,
            "coverage": float(te["inside"].mean()),
            "history": cal.anchors,
        }
    return out


def calibration_bucket_rows(cal: dict[str, Any], hist_all: pd.DataFrame) -> pd.DataFrame:
    """Per R-A bucket for one calibrated test anchor: n, alpha_raw/shrunk/iso, band, coverage."""
    if cal.get("cal") is None:
        return pd.DataFrame()
    table = cal["cal"].table
    te = cal["test"]
    m = membership(te["p"])
    h = hist_all[hist_all["anchor"].isin(cal["history"])]
    hm = membership(h["p"])
    rows = []
    for b in TABLE_ROWS:
        sel = te[m[b].to_numpy()]
        hsel = h[hm[b].to_numpy()]
        if b.startswith("D"):
            k = int(b[1:])
            raw, iso = float(table.loc[k, "alpha_raw"]), float(table.loc[k, "alpha_iso"])
        else:
            raw = float(hsel["excess"].mean()) if len(hsel) else np.nan
            iso = float(sel["alpha"].mean()) if len(sel) else np.nan
        rows.append(
            {
                "bucket": b,
                "n": len(sel),
                "alpha_raw": raw,
                "alpha_shrunk": 0.5 * raw if raw == raw else np.nan,
                "alpha_iso": iso,
                "conf_lo": float(sel["lo"].mean()) if len(sel) else np.nan,
                "conf_hi": float(sel["hi"].mean()) if len(sel) else np.nan,
                "coverage": float(sel["inside"].mean()) if len(sel) else np.nan,
            }
        )
    return pd.DataFrame(rows)


# ── consensus (05 §4): trials 46-48 from the members' scores ────────────────────────────────────────────────────
def consensus_scores(
    members: dict[str, pd.DataFrame],
    rule: str,
    *,
    keys: Sequence[str] = ("anchor",),
    min_votes: int = 6,
    min_names: int = 5,
) -> pd.DataFrame:
    """Score of CONS_MAJ (votes), CONS_UNAN (1 if every member votes D10, NaN for the group if < min_names such names)
    or ENS_RANKAVG (mean p) for each row of the common scored cross-section, grouped by ``keys``."""
    names = list(members)
    base = members[names[0]][
        [
            *keys,
            "security_id",
            *[
                c
                for c in members[names[0]].columns
                if c
                in ("industry_group", "labelled", "ret_total", "excess_univ_med", "excess_n500_pr", "label", "fold")
            ],
        ]
    ].copy()
    ids = [*keys, "security_id"]
    wide = base[ids].copy()
    for n in names:
        f = members[n][[*ids, "score"]].copy()
        gkey = f[list(keys)].astype(str).agg("|".join, axis=1).to_numpy()
        f["p"] = percentile(f["score"], by=gkey).to_numpy()
        wide = wide.merge(f[[*ids, "p"]].rename(columns={"p": n}), on=ids, how="left")
    pm = wide[names]
    if rule == "rank_average":
        score = pm.mean(axis=1)
    else:
        top = vote_counts(pm, 10)
        if rule == "majority":
            score = top.astype(float)
        else:
            score = (top == len(names)).astype(float)
            few = score.groupby([wide[k] for k in keys]).transform("sum") < min_names
            score = score.where(~few)
    out = base.merge(wide[ids].assign(score=score.to_numpy()), on=ids, how="left")
    return out


# ── CPCV statistics ───────────────────────────────────────────────────────────────────────────────────────────
def cpcv_anchor_ics(frame: pd.DataFrame) -> pd.DataFrame:
    """IC per (split, anchor) from the CPCV scored frame."""
    rows = []
    for (split, anchor), g in frame.groupby(["split", "anchor"], sort=True):
        a = anchor_frame(g)
        rows.append({"split": int(split), "anchor": pd.Timestamp(anchor), "ic": rank_ic(a["score"], a["label"])})
    return pd.DataFrame(rows)


def cpcv_summary(ics: pd.DataFrame, splits: Sequence[CPCVSplit], n_groups: int) -> dict[str, Any]:
    """Per-path IC series (path j takes the j-th split testing each group), anchor means across paths."""
    anchors = sorted(ics["anchor"].unique())
    paths = cpcv_paths(list(splits), n_groups)
    lookup = ics.set_index(["split", "anchor"])["ic"]
    path_series = []
    for path in paths:
        vals = []
        for g, sidx in path:
            for a in splits[sidx].test_anchors:
                if a in splits[sidx].test_anchors and a in _group_anchors(splits, g, anchors, n_groups):
                    vals.append(lookup.get((sidx, a), np.nan))
        path_series.append(np.array(vals, dtype=float))
    means = np.array([np.nanmean(v) for v in path_series])
    anchor_mean = ics.groupby("anchor")["ic"].mean()
    return {
        "n_splits": len(splits),
        "n_paths": len(paths),
        "path_mean_ic": means.tolist(),
        "mean_ic": float(np.nanmean(means)),
        "min_path": float(np.nanmin(means)),
        "max_path": float(np.nanmax(means)),
        "share_paths_positive": float((means > 0).mean()),
        "anchor_mean_ic": {str(k.date()): float(v) for k, v in anchor_mean.items()},
        "split_mean_ic": ics.groupby("split")["ic"].mean().tolist(),
    }


def _group_anchors(
    splits: Sequence[CPCVSplit], g: int, anchors: list[pd.Timestamp], n_groups: int
) -> list[pd.Timestamp]:
    return [anchors[i] for i in np.array_split(np.arange(len(anchors)), n_groups)[g]]


def pbo_inputs(anchor_ic: pd.DataFrame, splits: Sequence[CPCVSplit]) -> tuple[np.ndarray, np.ndarray]:
    """(IS, OOS) performance per CPCV split for every trial (columns of ``anchor_ic``, rows = anchors).

    OOS = mean path-averaged OOS IC over the split's test anchors; IS = the same quantity over the split's
    train anchors (purged/embargoed anchors excluded). The IS number is therefore an out-of-sample IC of that trial on
    the other anchors (from the splits that tested them), never an in-sample fitted IC.
    """
    is_rows, oos_rows = [], []
    for s in splits:
        is_rows.append(anchor_ic.loc[list(s.train_anchors)].mean(axis=0).to_numpy())
        oos_rows.append(anchor_ic.loc[list(s.test_anchors)].mean(axis=0).to_numpy())
    return np.vstack(is_rows), np.vstack(oos_rows)


def iter_pairs(a: Iterable[Any]) -> list[Any]:
    return list(a)
