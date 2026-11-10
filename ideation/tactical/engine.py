"""Walk-forward and CPCV runners with per-(trial, fold) parquet checkpoints (MS2). No database access.

Checkpoint directory: ``<root>/<kind>/<num>_<id>_<config_hash12>_<CODE_VERSION>/`` so a changed config or code version
never reuses stale folds. Every fit uses only the fold's training anchors (``walk_forward`` purge + embargo, or the
CPCV purge/embargo) and the learner's fixed hyperparameters.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ideation.cv.cpcv import cpcv_splits
from ideation.cv.walkforward import Fold, assert_purged, monthly_folds
from ideation.eval.selection import cluster_permutation_importance, feature_clusters
from ideation.models.phase1 import composite_ew
from ideation.models.zoo import Learner, NinetyTenSec, make_learner
from ideation.tactical.data import CODE_VERSION, Design, rows_in
from ideation.trials.registry import TrialSpec

log = logging.getLogger("ideation.tactical")
EMBARGO_3M = 3
N_TRAIN = 36
BLOCK = 12
IMP_REPEATS = 3
IMP_MAX_ROWS = 2000
IMP_CLUSTER_ROWS = 6000
IMP_SKIP = ("svr",)
"""SVR predicts through an explicit-loop RBF (bit-reproducible, ~2 min per 14k rows): its stability is not computed
(NaN, z = 0 in the 90/10 score). Disclosed in STATUS."""


def trial_dir(root: Path, kind: str, spec: TrialSpec) -> Path:
    d = root / kind / f"{spec.num:02d}_{spec.trial_id}_{spec.config_hash[:12]}_{CODE_VERSION}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def build_model(spec: TrialSpec, design: Design) -> Learner:
    cfg = spec.config
    if cfg.get("model") == "p1_comp_ew":
        return composite_ew(design.priors)
    key = cfg["learner"]
    extra: dict[str, Any] = {"priors": design.priors} if key == "scr" else {}
    base = make_learner(key, "tactical", broadcast=design.broadcast, **extra)
    return NinetyTenSec(base) if cfg.get("ninety_ten_sec") else base


def wfo_folds(spec: TrialSpec, anchors: list[pd.Timestamp], label_end: pd.Series) -> list[Fold]:
    cfg = spec.config
    oos = cfg["counted_oos"]
    return monthly_folds(
        anchors, label_end, oos["start"], oos["end"], block=BLOCK, n_train=N_TRAIN,
        embargo=int(cfg.get("embargo_anchors", EMBARGO_3M)), train_start=cfg.get("train_start"),
    )  # fmt: skip


def _block_clusters(X: pd.DataFrame, value_cols: list[str], seed: int = 0) -> dict[str, int]:
    """Clusters of the value columns (1 - |Spearman|, average linkage, cut 0.5); each isna_/na_ flag joins its value
    column's cluster so a missing flag is permuted together with its feature."""
    sample = X[value_cols]
    if len(sample) > IMP_CLUSTER_ROWS:
        sample = sample.sample(IMP_CLUSTER_ROWS, random_state=seed)
    cl = feature_clusters(sample.loc[:, sample.nunique() > 1]) if sample.nunique().gt(1).any() else {}
    nxt = max(cl.values(), default=0) + 1
    for c in value_cols:
        if c not in cl:
            cl[c] = nxt
            nxt += 1
    for c in X.columns:
        if c.startswith(("isna_", "na_layout_")):
            base = c[len("isna_") :] if c.startswith("isna_") else c[len("na_layout_") :]
            if base in cl:
                cl[c] = cl[base]
    return {c: k for c, k in cl.items() if c in X.columns}


def _importance(model: Learner, X_tr: pd.DataFrame, X_te: pd.DataFrame, meta_te: pd.DataFrame, y_te: pd.Series,
                design: Design) -> dict[str, float]:  # fmt: skip
    """Clustered permutation importance on a seeded test subsample (uniform across trials: see IMP_* constants)."""
    n = len(X_te)
    pos = np.sort(np.random.default_rng(0).choice(n, size=min(n, IMP_MAX_ROWS), replace=False))
    Xs, ms, ys = X_te.iloc[pos], meta_te.iloc[pos], y_te.iloc[pos]
    clusters = _block_clusters(X_tr, [c for c in design.value_cols if c not in design.broadcast])
    # broadcast columns are anchor-constant: permuting within an anchor is a no-op, so they are left out.
    clusters = {c: k for c, k in clusters.items() if c in Xs.columns}
    imp = cluster_permutation_importance(
        lambda x: model.predict(x, ms), Xs, ys, ms["anchor"], clusters, n_repeats=IMP_REPEATS, seed=0
    )
    out = {c: float(imp.get(k, 0.0)) for c, k in clusters.items() if not c.startswith(("isna_", "na_layout_"))}
    return out


def _fit_predict(spec: TrialSpec, design: Design, train_anchors: Any, test_anchors: Any, *, importance: bool,
                 seed_note: str = "") -> tuple[pd.DataFrame, dict[str, Any]]:  # fmt: skip
    X, meta, y = design.X, design.meta, design.y
    tr = rows_in(meta, train_anchors) & y.notna().to_numpy()
    te = rows_in(meta, test_anchors)
    info: dict[str, Any] = {"n_train": int(tr.sum()), "n_train_anchors": int(meta.loc[tr, "anchor"].nunique()),
                            "n_test": int(te.sum())}  # fmt: skip
    if not tr.any() or not te.any():
        return meta.loc[te, ["anchor", "security_id"]].assign(score=np.nan), {**info, "empty": True}
    assert_purged(meta.loc[tr], pd.Timestamp(min(test_anchors)))
    t0 = time.perf_counter()
    model = build_model(spec, design)
    model.fit(X.loc[tr], y.loc[tr].to_numpy(dtype=float), meta.loc[tr])
    info["fit_s"] = round(time.perf_counter() - t0, 1)
    t1 = time.perf_counter()
    out = meta.loc[te, ["anchor", "security_id"]].copy()
    out["score"] = np.asarray(model.predict(X.loc[te], meta.loc[te]), dtype=float)
    info["predict_s"] = round(time.perf_counter() - t1, 1)
    base = getattr(model, "base", model)
    info["warnings"] = list(getattr(base, "warnings_", []))
    if importance and spec.config.get("model") is None and spec.config.get("learner") not in IMP_SKIP:
        t2 = time.perf_counter()
        try:
            info["importance"] = _importance(model, X.loc[tr], X.loc[te], meta.loc[te], y.loc[te], design)
        except Exception as exc:  # stability is 10% of the selection score: log, do not lose the fold
            info["importance_error"] = repr(exc)
        info["imp_s"] = round(time.perf_counter() - t2, 1)
    return out, info


def run_wfo(spec: TrialSpec, design: Design, anchors: list[pd.Timestamp], label_end: pd.Series, root: Path,
            *, importance: bool = True, progress: Callable[[str], None] = log.info) -> tuple[pd.DataFrame, list[dict]]:  # fmt: skip
    """All folds of one trial (resumable). Returns (scores of the test rows, per-fold info)."""
    folds = wfo_folds(spec, anchors, label_end)
    d = trial_dir(root, "wfo", spec)
    parts, infos = [], []
    for k, fold in enumerate(folds):
        pq, js = d / f"fold_{k:02d}.parquet", d / f"fold_{k:02d}.json"
        if pq.exists() and js.exists():
            parts.append(pd.read_parquet(pq))
            infos.append(json.loads(js.read_text()))
            continue
        out, info = _fit_predict(spec, design, fold.train_anchors, fold.test_anchors, importance=importance)
        info.update(fold=fold.name, train_first=str(fold.train_anchors[0].date()) if fold.train_anchors else None,
                    train_last=str(fold.train_anchors[-1].date()) if fold.train_anchors else None)  # fmt: skip
        out["fold"] = fold.name
        out.to_parquet(pq, index=False)
        js.write_text(json.dumps(info))
        parts.append(out)
        infos.append(info)
        progress(f"{spec.trial_id} fold {k + 1}/{len(folds)} {fold.name} n_train={info['n_train']} "
                 f"fit={info.get('fit_s')}s imp={info.get('imp_s')}s")  # fmt: skip
    scores = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=["anchor", "security_id", "score"])
    scores["anchor"] = pd.to_datetime(scores["anchor"])
    return scores, infos


# ── CPCV ─────────────────────────────────────────────────────────────────────────────────────────────────────
N_GROUPS = 12
K_TEST = 2


def cpcv_split_list(anchors: list[pd.Timestamp], label_end: pd.Series, embargo: int = EMBARGO_3M):
    return cpcv_splits(anchors, label_end, N_GROUPS, K_TEST, embargo)


def run_cpcv(spec: TrialSpec, design: Design, anchors: list[pd.Timestamp], label_end: pd.Series, root: Path,
             *, only_splits: list[int] | None = None, progress: Callable[[str], None] = log.info) -> dict[int, pd.DataFrame]:  # fmt: skip
    """All 66 splits (resumable): scores of each split's test groups. Training = all non-purged, non-embargoed anchors."""
    splits = cpcv_split_list(anchors, label_end)
    d = trial_dir(root, "cpcv", spec)
    out: dict[int, pd.DataFrame] = {}
    for s in splits:
        if only_splits is not None and s.index not in only_splits:
            continue
        pq, js = d / f"split_{s.index:02d}.parquet", d / f"split_{s.index:02d}.json"
        if pq.exists():
            out[s.index] = pd.read_parquet(pq)
            continue
        t0 = time.perf_counter()
        df, info = _fit_predict(spec, design, s.train_anchors, s.test_anchors, importance=False)
        df.to_parquet(pq, index=False)
        js.write_text(json.dumps({**info, "test_groups": list(s.test_groups)}))
        out[s.index] = df
        progress(f"{spec.trial_id} cpcv split {s.index + 1}/{len(splits)} groups={s.test_groups} "
                 f"n_train={info['n_train']} {time.perf_counter() - t0:.0f}s")  # fmt: skip
    return out


@dataclass
class Baseline:
    name: str
    score: pd.Series
    """Indexed like ``Design.meta``."""


def baseline_scores(design: Design) -> dict[str, pd.Series]:
    """No-fit benchmarks (05 §3.6 tactical subset): MOM_12_1 and LOWVOL on the raw columns."""
    r = design.raw
    return {"MOM_12_1": r["mom_12_1"].astype(float), "LOWVOL": -r["rvol_63"].astype(float)}
