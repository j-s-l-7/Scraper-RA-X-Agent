# ruff: noqa: E501
"""Cross-model consensus evaluation (05 §4, MS3). Pure functions over stored out-of-sample predictions.

Inputs
* ``members``: model name -> frame ``anchor, security_id, p`` (percentile of the model's score within the scored
  cross-section of the anchor, read from ``ideation.oos_prediction``). Only the (anchor, security) pairs scored by
  every member are used.
* ``rets``: ``anchor, security_id, ret_total, excess_univ_med, excess_n500_pr`` for the same pairs.

Definitions (printed in the report): ``votes`` = number of members with the name in D10; ``C_maj`` = votes >= 6;
``C_unan`` = votes = M (an anchor with fewer than 5 such names is "too few" and excluded from its means, with the count
reported); ``B_maj`` = bottom mirror. The *best single model* is fixed a priori as the member with the highest mean IC
over the evaluated anchors (a hindsight pick that favours the single model, so the comparison is conservative for the
consensus). Hit rate = share beating the universe median; beat-N500 = share beating Nifty 500 PR; beat-EW = share beating
the equal-weight mean return of the scored cross-section.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from ideation.eval import consensus_math as CM
from ideation.eval.bootstrap import mean_ci, paired_delta
from ideation.eval.metrics import rank_ic

MAJORITY = 6
MIN_UNAN = 5
N_DRAWS = 1_000
N_BOOT = 10_000


@dataclass
class ConsensusResult:
    per_anchor: pd.DataFrame
    """One row per (anchor, set): n, mean/median excess vs the universe median, vs N500, vs EW, hit, beat rates."""
    sets: pd.DataFrame
    """Summary per set over anchors: mean excess with CI90/CI95, hit, beat rates, anchors beating the benchmark."""
    paired: pd.DataFrame
    agreement: dict[str, Any]
    votes_ic: pd.Series
    too_few_unan: list[pd.Timestamp]
    best_single: str
    ens_name: str
    models: list[str]
    method: str
    n_anchors: int


def wide_p(members: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """names x models percentile frame indexed by (anchor, security_id), restricted to the pairs every member scored."""
    cols = {}
    for m, f in members.items():
        cols[m] = f.set_index(["anchor", "security_id"])["p"]
    w = pd.DataFrame(cols).dropna()
    return w.sort_index()


def _set_row(name: str, anchor: pd.Timestamp, idx: pd.Index, g: pd.DataFrame, med: float, n500: float, ew: float) -> dict:
    x = g.loc[g.index.intersection(idx)] if len(idx) else g.iloc[0:0]
    r = x["ret_total"]
    return {
        "anchor": anchor,
        "set": name,
        "n": len(x),
        "ex_med": float(r.mean() - med) if len(x) else np.nan,
        "ex_med_median": float(r.median() - med) if len(x) else np.nan,
        "ex_n500": float((x["ret_total"] - x["n500_ret"]).mean()) if len(x) else np.nan,
        "ex_ew": float(r.mean() - ew) if len(x) else np.nan,
        "hit": float((r > med).mean()) if len(x) else np.nan,
        "beat_n500": float((x["ret_total"] > x["n500_ret"]).mean()) if len(x) else np.nan,
        "beat_ew": float((r > ew).mean()) if len(x) else np.nan,
    }


def evaluate(
    members: dict[str, pd.DataFrame],
    rets: pd.DataFrame,
    *,
    mean_block: float,
    n_boot: int = N_BOOT,
    n_draws: int = N_DRAWS,
    min_anchor_names: int = 20,
    seed: int = 0,
) -> ConsensusResult:
    """Consensus vs single models vs size-matched vs rank average vs a random draw (05 §4 evaluation a-f).

    ``mean_block`` = 1 resamples anchors iid (annual); the monthly 3M series uses a stationary block bootstrap.
    """
    models = list(members)
    P = wide_p(members)
    R = rets.set_index(["anchor", "security_id"]).reindex(P.index)
    R = R.assign(n500_ret=R["ret_total"] - R["excess_n500_pr"]).dropna(subset=["ret_total"])
    P = P.loc[R.index]
    anchors = sorted(P.index.get_level_values("anchor").unique())
    # a-priori best single model: highest mean IC over the evaluated anchors
    ics = {m: [] for m in models}
    for a in anchors:
        pa, ra = P.xs(a, level="anchor"), R.xs(a, level="anchor")["ret_total"]
        for m in models:
            ics[m].append(rank_ic(pa[m], ra))
    mean_ic = pd.Series({m: float(np.nanmean(v)) for m, v in ics.items()})
    best = str(mean_ic.idxmax())
    rows, votes_ic, too_few = [], {}, []
    sp_acc, w_acc, jac_acc = [], [], []
    rng = np.random.default_rng(seed)
    for a in anchors:
        pa, ra = P.xs(a, level="anchor"), R.xs(a, level="anchor")
        if len(pa) < min_anchor_names:
            continue
        med = float((ra["ret_total"] - ra["excess_univ_med"]).median())
        n500 = float(ra["n500_ret"].median())
        ew = float(ra["ret_total"].mean())
        cs = CM.consensus_sets(pa, majority=MAJORITY, min_unan=MIN_UNAN)
        ens = CM.ens_rankavg(pa)
        k = len(cs.c_maj)
        votes = CM.votes(pa, 10)
        votes_ic[a] = rank_ic(votes, ra["ret_total"])
        spec: dict[str, pd.Index] = {"C_maj": cs.c_maj, "B_maj": cs.b_maj}
        if cs.too_few_unan:
            too_few.append(a)
        else:
            spec["C_unan"] = cs.c_unan
        if k > 0:
            spec[f"best single top-|C_maj| ({best})"] = CM.top_k(pa[best], k)
            spec["ENS_RANKAVG top-|C_maj|"] = CM.top_k(ens, k)
            spec["mean of single-model top-|C_maj|"] = pd.Index([])  # filled below as the mean of per-model rows
        spec["ENS_RANKAVG D10"] = pd.Index(ens.index[(pd.Series(ens).rank(pct=True) > 0.9).to_numpy()])
        spec["all names (EW universe)"] = pa.index
        for name, idx in spec.items():
            if name.startswith("mean of single"):
                continue
            rows.append(_set_row(name, a, idx, ra, med, n500, ew))
        if k > 0:
            per = [_set_row("x", a, CM.top_k(pa[m], k), ra, med, n500, ew) for m in models]
            avg = {c: float(np.nanmean([p[c] for p in per])) for c in per[0] if c not in ("anchor", "set")}
            rows.append({"anchor": a, "set": "mean of single-model top-|C_maj|", **avg})
            r = ra["ret_total"].to_numpy()
            d = np.array([r[rng.choice(len(r), size=min(k, len(r)), replace=False)].mean() for _ in range(n_draws)])
            obs = float(ra.loc[ra.index.intersection(cs.c_maj), "ret_total"].mean())
            rows.append({"anchor": a, "set": "random |C_maj| draw (expected)", "n": k, "ex_med": float(d.mean() - med),
                         "ex_med_median": np.nan, "ex_n500": float(d.mean() - n500), "ex_ew": float(d.mean() - ew),
                         "hit": np.nan, "beat_n500": np.nan, "beat_ew": np.nan})  # fmt: skip
            rows.append({"anchor": a, "set": "C_maj percentile among random draws", "n": k, "ex_med": float((d < obs).mean()),
                         "ex_med_median": np.nan, "ex_n500": np.nan, "ex_ew": np.nan, "hit": np.nan,
                         "beat_n500": np.nan, "beat_ew": np.nan})  # fmt: skip
        for m in models:
            rows.append(_set_row(f"{m} D10", a, pa.index[(pa[m] > 0.9).to_numpy()], ra, med, n500, ew))
        sp_acc.append(CM.pairwise_spearman(pa).to_numpy()[np.triu_indices(len(models), 1)].mean())
        w_acc.append(CM.kendall_w(pa))
        jac = CM.pairwise_jaccard(pa).to_numpy()
        jac_acc.append(np.nanmean(jac[np.triu_indices(len(models), 1)]))
    pa_df = pd.DataFrame(rows)
    sets = _summarise(pa_df, mean_block, n_boot, seed)
    pr = _paired(pa_df, best, mean_block, n_boot, seed)
    agree = {
        "mean_pairwise_spearman": float(np.mean(sp_acc)),
        "kendall_w_mean": float(np.nanmean(w_acc)),
        "kendall_w_min": float(np.nanmin(w_acc)),
        "kendall_w_max": float(np.nanmax(w_acc)),
        "mean_pairwise_jaccard_d10": float(np.mean(jac_acc)),
        "mean_ic": mean_ic.to_dict(),
    }
    return ConsensusResult(pa_df, sets, pr, agree, pd.Series(votes_ic), too_few, best, "ENS_RANKAVG", models,
                           f"stationary block bootstrap, mean block {mean_block:g} anchors, {n_boot} draws"
                           if mean_block > 1 else f"iid bootstrap over anchors, {n_boot} draws", len(anchors))  # fmt: skip


def _summarise(pa: pd.DataFrame, mean_block: float, n_boot: int, seed: int) -> pd.DataFrame:
    method = "stationary" if mean_block > 1 else "iid"
    out = []
    for name, g in pa.groupby("set", sort=False):
        g = g.set_index("anchor").sort_index()
        row: dict[str, Any] = {"set": name, "anchors": int(g["ex_med"].notna().sum()), "mean n": float(g["n"].mean())}
        for col in ("ex_med", "ex_n500", "ex_ew"):
            s = g[col].dropna()
            if len(s) >= 2 and not name.startswith("C_maj percentile"):
                ci = mean_ci(s.to_numpy(), method=method, mean_block=mean_block, n_boot=n_boot, seed=seed)
                row[col] = ci.estimate
                row[col + "_ci90"] = ci.ci90
                row[col + "_ci95"] = ci.ci95
            else:
                row[col] = float(s.mean()) if len(s) else np.nan
                row[col + "_ci90"] = (np.nan, np.nan)
                row[col + "_ci95"] = (np.nan, np.nan)
        row["median_ex_med"] = float(g["ex_med_median"].mean())
        for col in ("hit", "beat_n500", "beat_ew"):
            row[col] = float(g[col].mean())
        row["anchors_ex_med_gt0"] = int((g["ex_med"] > 0).sum())
        row["anchors_ex_n500_gt0"] = int((g["ex_n500"] > 0).sum())
        out.append(row)
    return pd.DataFrame(out)


def _paired(pa: pd.DataFrame, best: str, mean_block: float, n_boot: int, seed: int) -> pd.DataFrame:
    method = "stationary" if mean_block > 1 else "iid"
    pv = pa.pivot_table(index="anchor", columns="set", values="ex_med")
    comps = [
        ("C_maj", f"best single top-|C_maj| ({best})"),
        ("C_maj", "ENS_RANKAVG top-|C_maj|"),
        ("C_maj", "mean of single-model top-|C_maj|"),
        ("C_maj", "random |C_maj| draw (expected)"),
        ("C_maj", "all names (EW universe)"),
        ("C_maj", "B_maj"),
    ]
    rows = []
    for a, b in comps:
        if a not in pv or b not in pv:
            continue
        d = pv[[a, b]].dropna()
        if len(d) < 3:
            continue
        r = paired_delta(d[a].to_numpy(), d[b].to_numpy(), method=method, mean_block=mean_block, n_boot=n_boot, seed=seed)
        rows.append({"a": a, "b": b, "n": r.n, "delta": r.mean_delta, "ci90": r.ci90, "ci95": r.ci95, "p_gt0": r.p_gt0})
    return pd.DataFrame(rows)
