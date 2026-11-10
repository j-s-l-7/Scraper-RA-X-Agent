"""MS2 report: metrics for every trial and horizon, paired arms, 90/10 selection, DSR, PBO, gates, champion decision.
Pure pandas on the checkpointed scores (no database access); persistence lives in ``persist.py``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import StratifiedKFold, cross_val_predict

from ideation.eval.metrics import bucket_table
from ideation.rank.percentile import percentile
from ideation.tactical import evalx
from ideation.tactical.data import ANCHOR_MAX, Design
from ideation.tactical.engine import baseline_scores, wfo_folds
from ideation.trials.registry import TrialSpec

LONG_START = pd.Timestamp("2014-01-31")
POOL_EXCLUDE = {"RF_T1", "LGBM_T1", "RIDGE_T1", "RF_T1_TEXT", "RIDGE_T1_TEXT", "RF_T1_VSURP", "RIDGE_T1_VSURP"}
CONSENSUS = ("CONS_MAJ_T", "CONS_UNAN_T", "ENS_RANKAVG_T")
FITTED_SINGLE = ("OLS_T0", "SCR_T0", "RF_T0", "PRIDGE_T0", "RIDGE_T0", "LGBM_T0", "XGB_T0", "MLP_T0", "SVR_T0",
                 "RF_T0_REGIME", "LGBM_T0_REGIME", "RIDGE_T0_REGIME", "RF_T0_ADV05", "RF_T0_9010SEC")  # fmt: skip


def load_scores(root: Path, specs: list[TrialSpec]) -> tuple[dict[str, pd.DataFrame], dict[str, list[dict]]]:
    scores, infos = {}, {}
    for s in specs:
        f = root / "scores" / f"{s.num:02d}_{s.trial_id}.parquet"
        if f.exists():
            df = pd.read_parquet(f)
            df["anchor"] = pd.to_datetime(df["anchor"])
            scores[s.trial_id] = df
            infos[s.trial_id] = json.loads((root / "scores" / f"{s.num:02d}_{s.trial_id}.json").read_text())
        c = root / "scores" / f"{s.num:02d}_{s.trial_id}_CTRL.parquet"
        if c.exists():
            df = pd.read_parquet(c)
            df["anchor"] = pd.to_datetime(df["anchor"])
            scores[s.trial_id + "_CTRL"] = df
    return scores, infos


def baseline_frames(raw: pd.DataFrame) -> dict[str, pd.DataFrame]:
    out = {}
    r = raw[(raw["anchor"] >= LONG_START) & (raw["anchor"] <= ANCHOR_MAX)]
    for name, col in (("MOM_12_1", r["mom_12_1"]), ("LOWVOL", -r["rvol_63"])):
        out[name] = pd.DataFrame({"anchor": r["anchor"], "security_id": r["security_id"], "score": col.astype(float)})
    return out


def all_scores(root: Path, specs: list[TrialSpec], raw: pd.DataFrame):  # type: ignore[no-untyped-def]
    scores, infos = load_scores(root, specs)
    fam_members = next(s.config["members"] for s in specs if "members" in s.config)
    if all(m in scores for m in fam_members):
        scores.update(evalx.consensus_scores({m: scores[m] for m in fam_members}))
    scores.update(baseline_frames(raw))
    return scores, infos


def trial_table(scores: dict[str, pd.DataFrame], raw: pd.DataFrame) -> tuple[dict[str, dict[str, pd.DataFrame]], pd.DataFrame]:
    """per-anchor frames {trial: {'3M': df, '1M': df}} and the summary table (one row per trial x horizon)."""
    pa: dict[str, dict[str, pd.DataFrame]] = {}
    rows = []
    for k, sc in scores.items():
        pa[k] = {}
        for h in ("3M", "1M"):
            df = evalx.per_anchor(sc, raw, h)
            pa[k][h] = df
            s = evalx.summarize(df, h)
            rows.append({"trial": k, "horizon": h, "first": df.index.min().date(), "last": df.index.max().date(), **s})
    return pa, pd.DataFrame(rows)


def adversarial_auc(design: Design, folds: list, seed: int = 0, cap: int = 6000) -> list[dict[str, Any]]:
    """02 6.1: train-vs-test RF classifier AUC (200 trees, depth 6, 5-fold) on the R1 model inputs of each WFO fold.
    Broadcast (anchor-constant) columns are excluded: they separate periods by construction."""
    cols = [c for c in design.X.columns if c not in design.broadcast and c.replace("isna_", "") not in design.broadcast]
    out = []
    anchors = pd.to_datetime(design.meta["anchor"])
    for f in folds:
        tr = np.flatnonzero(anchors.isin(f.train_anchors).to_numpy())
        te = np.flatnonzero(anchors.isin(f.test_anchors).to_numpy())
        rng = np.random.default_rng(seed)
        tr, te = rng.choice(tr, min(cap, len(tr)), replace=False), rng.choice(te, min(cap, len(te)), replace=False)
        X = design.X.iloc[np.concatenate([tr, te])][cols].to_numpy()
        y = np.r_[np.zeros(len(tr)), np.ones(len(te))]
        clf = RandomForestClassifier(200, max_depth=6, n_jobs=4, random_state=seed)
        p = cross_val_predict(clf, X, y, cv=StratifiedKFold(5, shuffle=True, random_state=seed), method="predict_proba")[:, 1]
        clf.fit(X, y)
        top = sorted(zip(cols, clf.feature_importances_, strict=True), key=lambda t: -t[1])[:3]
        out.append({"fold": f.name, "auc": float(roc_auc_score(y, p)), "top": [(c, round(float(v), 3)) for c, v in top]})
    return out


def ra_table(sc: pd.DataFrame, raw: pd.DataFrame, horizon: str = "3M") -> pd.DataFrame:
    """Pooled-all-OOS table R-A (descriptive): mean n, mean/median excess vs the universe median, hit rate per bucket."""
    ret, _, _ = evalx.HORIZONS[horizon]
    d = sc.merge(raw[["anchor", "security_id", ret]], on=["anchor", "security_id"]).dropna(subset=["score", ret])
    parts = []
    for _, g in d.groupby("anchor"):
        p = percentile(g["score"])
        t = bucket_table(p, g[ret] - g[ret].median(), bench=0.0)
        parts.append(t)
    cat = pd.concat(parts)
    return cat.groupby(level=0).agg(n=("n", "mean"), mean_excess=("mean", "mean"), median_excess=("median", "mean"),
                                    hit=("hit", "mean")).reindex(parts[0].index)  # fmt: skip


def decide(summ: pd.DataFrame, pa: dict, scores: dict, stab: dict[str, float], n_family: int,
           auc_mean: float | None) -> dict[str, Any]:  # fmt: skip
    """Selection (90/10), DSR, PBO, gates, non-inferiority and the champion-or-baseline decision."""
    s3 = summ[summ["horizon"] == "3M"].set_index("trial")
    pool = [t for t in s3.index if t not in POOL_EXCLUDE and not t.endswith("_CTRL") and t not in {"MOM_12_1", "LOWVOL"}]
    pool = [t for t in pool if s3.loc[t, "first"] <= LONG_START.date()]
    ic = {t: pa[t]["3M"]["ic"].dropna() for t in pool}
    sel = evalx.select_9010(pd.Series({t: s3.loc[t, "ic_mean"] for t in pool}),
                            pd.Series({t: stab.get(t, np.nan) for t in pool}))  # fmt: skip
    dsr_ic = evalx.dsr_pool(ic, n_family)
    dsr_sp = evalx.dsr_pool({t: pa[t]["3M"]["spread_cost_adj"].dropna() for t in pool}, n_family)
    pbo = evalx.pbo_matrix(ic)
    champ = sel.index[0]
    base_ew, base_mom = "T_EW_SIGNPRIOR", "MOM_12_1"
    ni = {b: evalx.paired_ic(pa[champ]["3M"]["ic"], pa[b]["3M"]["ic"], "3M") for b in (base_ew, base_mom) if b in pa}
    g = evalx.gates(s3.loc[champ].to_dict(), dsr_ic[champ]["dsr"], pbo["pbo"], auc_mean)
    ni_ok = {b: bool(v["delta"] >= -0.01 and v["p_gt0"] >= 0.6) for b, v in ni.items()}
    all_pass = all(v[0] for v in g.values()) and all(ni_ok.values())
    best_base = max((b for b in (base_ew, base_mom) if b in s3.index), key=lambda b: s3.loc[b, "ic_mean"])
    return {"pool": pool, "selection": sel, "dsr_ic": dsr_ic, "dsr_spread": dsr_sp, "pbo": pbo, "champion": champ,
            "gates": g, "non_inferiority": ni, "ni_ok": ni_ok, "ships": champ if all_pass else best_base,
            "champion_passes": all_pass, "best_baseline": best_base}  # fmt: skip
