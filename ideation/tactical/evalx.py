# ruff: noqa: E501
"""Evaluation of tactical OOS scores: per-anchor metrics, IC inference, DSR, PBO, 90/10, gates (02 §5.5, 03 §C9).

No database access. All bootstraps use ``N_BOOT`` = 10,000 resamples.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from ideation.eval.bootstrap import paired_delta
from ideation.eval.dsr import dsr_of_series, sharpe_moments
from ideation.eval.inference import ic_summary
from ideation.eval.metrics import cost_adjusted, rank_ic
from ideation.eval.pbo import pbo_cscv
from ideation.eval.selection import selection_scores, stability
from ideation.rank.buckets import decile, quintile
from ideation.rank.percentile import percentile

N_BOOT = 10_000
BPS = 12.0
HORIZONS = {"3M": ("ret_3m", "exu_3m", 3), "1M": ("ret_1m", "exu_1m", 1)}


def per_anchor(scores: pd.DataFrame, raw: pd.DataFrame, horizon: str = "3M") -> pd.DataFrame:
    """One row per anchor: n, rank IC, Q5-Q1 mean spread, Q5 excess vs the universe median, top-decile hit rate,
    one-way turnovers of Q5 and Q1 versus the previous anchor, and the cost-adjusted spread (02 5.5 literal:
    2 x 12 bps x the sum of both legs' measured turnovers). ``raw`` carries the returns (``Design.raw``)."""
    ret, exu, _ = HORIZONS[horizon]
    cols = ["anchor", "security_id", ret, exu]
    d = scores[["anchor", "security_id", "score"]].merge(raw[cols], on=["anchor", "security_id"], how="left")
    d = d[d["score"].notna() & d[ret].notna()]
    rows, prev_q5, prev_q1 = [], None, None
    for a, g in d.groupby("anchor", sort=True):
        p = percentile(g["score"])
        q, dec = quintile(p), decile(p)
        q5, q1 = set(g.loc[q == 5, "security_id"]), set(g.loc[q == 1, "security_id"])
        t5 = 1 - len(q5 & prev_q5) / len(q5) if prev_q5 and q5 else np.nan
        t1 = 1 - len(q1 & prev_q1) / len(q1) if prev_q1 and q1 else np.nan
        sp = g.loc[q == 5, ret].mean() - g.loc[q == 1, ret].mean()
        med = g[ret].median()
        rows.append({
            "anchor": a, "n": len(g), "ic": rank_ic(g["score"], g[ret]), "spread": sp,
            "q5_excess": g.loc[q == 5, exu].mean(), "q5_ret": g.loc[q == 5, ret].mean(),
            "q1_ret": g.loc[q == 1, ret].mean(), "univ_ret": g[ret].mean(),
            "hit_d10": float((g.loc[dec == 10, ret] > med).mean()), "to_q5": t5, "to_q1": t1,
        })  # fmt: skip
        prev_q5, prev_q1 = q5, q1
    out = pd.DataFrame(rows).set_index("anchor")
    tt = (out["to_q5"] + out["to_q1"]).fillna(out[["to_q5", "to_q1"]].sum(axis=1, min_count=1)).fillna(2.0)
    out["spread_cost_adj"] = [cost_adjusted(s, t, BPS) for s, t in zip(out["spread"], tt, strict=True)]
    # conservative variant: every 3M tranche pays a full round trip on both legs (4 x 12 bps)
    out["spread_cost_full"] = out["spread"] - 4 * BPS / 1e4
    return out


def summarize(pa: pd.DataFrame, horizon: str = "3M") -> dict[str, Any]:
    """IC inference + spread facts for one trial and horizon."""
    h = HORIZONS[horizon][2]
    s = ic_summary(pa["ic"], h, 1, n_boot=N_BOOT)
    out: dict[str, Any] = {
        "n": s["n"], "ic_mean": s["mean"], "ic_ir": s["ir"], "nw_lags": s["nw_lags"], "nw_t": s["nw_t"],
        "ci90": s["ci90"], "ci95": s["ci95"], "pct_pos": s["pct_positive"],
        "spread_mean": float(pa["spread"].mean()), "spread_cost_adj": float(pa["spread_cost_adj"].mean()),
        "spread_cost_full": float(pa["spread_cost_full"].mean()),
        "q5_excess": float(pa["q5_excess"].mean()), "hit_d10": float(pa["hit_d10"].mean()),
        "turnover_q5": float(pa["to_q5"].mean()), "turnover_q1": float(pa["to_q1"].mean()),
    }  # fmt: skip
    return out


def paired_ic(a: pd.Series, b: pd.Series, horizon: str = "3M") -> dict[str, Any]:
    """Paired stationary-bootstrap delta of two IC series on their common anchors (a - b)."""
    j = pd.concat([a, b], axis=1, join="inner").dropna()
    n = len(j)
    lags = {"3M": 2, "1M": 0}[horizon]
    block = float(max(lags + 1, round(n ** (1 / 3))))
    pdlt = paired_delta(j.iloc[:, 0], j.iloc[:, 1], method="stationary", mean_block=block, n_boot=N_BOOT)
    return {"n": n, "mean_a": float(j.iloc[:, 0].mean()), "mean_b": float(j.iloc[:, 1].mean()),
            "delta": pdlt.mean_delta, "ci90": pdlt.ci90, "ci95": pdlt.ci95, "p_gt0": pdlt.p_gt0}  # fmt: skip


def dsr_pool(ic_series: dict[str, pd.Series], n_trials: int) -> dict[str, dict[str, float]]:
    """DSR of each series against the pool's per-observation Sharpe ratios (variance across the pool), N = family N."""
    sr = np.array([sharpe_moments(s)[0] for s in ic_series.values()])
    out = {}
    for k, s in ic_series.items():
        r = dsr_of_series(s, sr, n_trials=n_trials)
        out[k] = {"dsr": r.dsr, "sr": r.sr, "sr0": r.sr0, "n_obs": r.n_obs, "var_sr": r.var_sr}
    return out


def pbo_matrix(series: dict[str, pd.Series], s_blocks: int = 12) -> dict[str, Any]:
    """CSCV PBO on the T x N matrix of the trials sharing the common anchor set (inner join; reported)."""
    m = pd.concat(series, axis=1, join="inner").dropna()
    r = pbo_cscv(m.to_numpy(), n_blocks=s_blocks)
    return {"pbo": r.pbo, "n_splits": r.n_splits, "n_trials": r.n_trials, "T": len(m), "trials": list(m.columns)}


def stability_of(infos: list[dict[str, Any]]) -> float:
    imps = [pd.Series(i["importance"], dtype=float) for i in infos if i.get("importance")]
    return stability(imps) if len(imps) >= 2 else float("nan")


def select_9010(mean_ic: pd.Series, stab: pd.Series) -> pd.Series:
    return selection_scores(mean_ic, stab).sort_values(ascending=False)


def gates(summ: dict[str, Any], dsr: float, pbo: float, auc: float | None) -> dict[str, tuple[bool, str]]:
    """02 §5.5 tactical gates (all blocking here) plus PBO and adversarial AUC (03 §C9)."""
    g = {
        "mean IC >= 0.02": (summ["ic_mean"] >= 0.02, f"{summ['ic_mean']:.4f}"),
        "NW t >= 2.0": (summ["nw_t"] >= 2.0, f"{summ['nw_t']:.2f}"),
        "95% block-boot CI excludes 0": (summ["ci95"][0] > 0, f"[{summ['ci95'][0]:.4f}, {summ['ci95'][1]:.4f}]"),
        "DSR >= 0.95": (dsr >= 0.95, f"{dsr:.3f}"),
        "cost-adj Q5-Q1 > 0": (summ["spread_cost_adj"] > 0, f"{summ['spread_cost_adj']:.4f}"),
        "positive months >= 55%": (summ["pct_pos"] >= 0.55, f"{summ['pct_pos']:.1%}"),
        "PBO <= 0.5": (pbo <= 0.5, f"{pbo:.2f}"),
    }
    if auc is not None:
        g["adversarial AUC <= 0.70"] = (auc <= 0.70, f"{auc:.3f}")
    return {k: (bool(v[0]), v[1]) for k, v in g.items()}


def consensus_scores(p: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Scalar consensus scores from member scores (frames with anchor, security_id, score): per anchor percentile p
    of every member; CONS_MAJ_T = 6th largest p, CONS_UNAN_T = min p, ENS_RANKAVG_T = mean p. Only names scored by all."""
    wide = {}
    for k, df in p.items():
        s = df.set_index(["anchor", "security_id"])["score"]
        wide[k] = percentile(s, by=s.index.get_level_values("anchor"))
    w = pd.concat(wide, axis=1, join="inner").dropna()
    arr = np.sort(w.to_numpy(), axis=1)[:, ::-1]
    maj = pd.Series(arr[:, 5], index=w.index)
    unan = pd.Series(arr[:, -1], index=w.index)
    ens = w.mean(axis=1)
    out = {}
    for name, s in (("CONS_MAJ_T", maj), ("CONS_UNAN_T", unan), ("ENS_RANKAVG_T", ens)):
        f = s.rename("score").reset_index()
        out[name] = f
    return out


def consensus_sets_eval(p: dict[str, pd.DataFrame], raw: pd.DataFrame, horizon: str = "3M") -> pd.DataFrame:
    """Set-level result per anchor: C_maj (>= 6 of 10 members in D10) and C_unan (all 10) equal-weight excess return
    vs the universe median, set sizes, and a size-matched top-|C| of the rank-average ensemble."""
    ret, _, _ = HORIZONS[horizon]
    wide = {}
    for k, df in p.items():
        s = df.set_index(["anchor", "security_id"])["score"]
        pp = percentile(s, by=s.index.get_level_values("anchor"))
        wide[k] = pp >= 0.9
    votes = pd.concat(wide, axis=1, join="inner").sum(axis=1).rename("votes")
    r = raw.set_index(["anchor", "security_id"])[ret]
    j = pd.concat([votes, r], axis=1, join="inner").dropna()
    m = len(p)
    ens = pd.concat({k: percentile(df.set_index(["anchor", "security_id"])["score"],
                                   by=df["anchor"].to_numpy()) for k, df in p.items()}, axis=1, join="inner").mean(axis=1)  # fmt: skip
    rows = []
    for a, g in j.groupby(level="anchor"):
        med = g[ret].median()
        c_maj, c_un = g[g["votes"] >= 6], g[g["votes"] == m]
        e = ens.loc[a].reindex(g.index.get_level_values("security_id")).to_numpy()
        row = {"anchor": a, "n_maj": len(c_maj), "n_unan": len(c_un),
               "maj_excess": c_maj[ret].mean() - med if len(c_maj) else np.nan,
               "unan_excess": c_un[ret].mean() - med if len(c_un) >= 5 else np.nan}  # fmt: skip
        k = len(c_maj)
        if k:
            top = g.iloc[np.argsort(-e, kind="stable")[:k]]
            row["ens_topk_excess"] = top[ret].mean() - med
        rows.append(row)
    return pd.DataFrame(rows).set_index("anchor")
