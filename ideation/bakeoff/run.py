"""Walk-forward and CPCV execution for one trial, with per-(trial, fold) parquet checkpoints (05 §8, §10.1 rule 5).

Checkpoint layout: ``<root>/<snap_id>/<config_hash[:12]>/{wfo_<fold>.parquet|json, cpcv_<split>.parquet}``. A rerun
loads whatever exists and fits only the missing folds/splits, so an interrupted run resumes. A fold's JSON holds
the importance vector (clustered permutation importance, 02 §6.5), ConvergenceWarnings, whether the fold was empty
(WFO selection found nothing), the fit time and the row counts.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ideation.bakeoff.data import Panel, TrialMatrix, build_matrix
from ideation.bakeoff.models import build_model, model_inputs
from ideation.cv.cpcv import CPCVSplit, cpcv_splits
from ideation.cv.walkforward import Fold, annual_folds, assert_purged
from ideation.eval.selection import cluster_permutation_importance, feature_clusters, feature_importance
from ideation.features.registry import Registry
from ideation.pit.contract import LeakageError
from ideation.rank.buckets import decile
from ideation.rank.percentile import percentile
from ideation.rank.transform import value_columns
from ideation.trials.registry import TrialSpec

log = logging.getLogger("ideation.bakeoff")
SCORE_META = ["anchor", "security_id", "industry_group", "labelled", "ret_total", "excess_univ_med", "excess_n500_pr"]


def years_to_anchors(panel: Panel, years: list[int]) -> list[pd.Timestamp]:
    return [pd.Timestamp(a) for a in panel.anchors if a.year in set(years)]


def window_args(cfg: Mapping[str, Any]) -> tuple[int | None, pd.Timestamp | None]:
    w = cfg["train_window"]
    n = int(w["n"]) if w["kind"] == "rolling" else None
    start = pd.Timestamp(w["start"]) if w.get("start") else None
    return n, start


def test_anchors(panel: Panel, cfg: Mapping[str, Any]) -> tuple[list[pd.Timestamp], list[pd.Timestamp]]:
    counted = years_to_anchors(panel, list(cfg["counted_tests"]))
    indicative = years_to_anchors(panel, list(cfg.get("indicative_tests", [])))
    return counted, indicative


@dataclass
class TrialContext:
    spec: TrialSpec
    panel: Panel
    matrix: TrialMatrix
    reg: Registry
    ckpt: Path
    raw_cols: tuple[str, ...] | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def cfg(self) -> Mapping[str, Any]:
        return self.spec.config

    def fresh_model(self):
        m = self.matrix
        return build_model(self.cfg, self.reg, broadcast=m.broadcast, feature_names=m.feature_names)

    def design(self) -> pd.DataFrame:
        return self.matrix.raw if self.raw_cols and self.matrix.raw is not None else self.matrix.X


def make_context(
    spec: TrialSpec, panel: Panel, reg: Registry, ckpt_root: Path, cache: dict[tuple, TrialMatrix]
) -> TrialContext:
    cfg = spec.config
    raw = model_inputs(cfg)
    key = (
        cfg["anchor_family"],
        cfg["universe"],
        float(cfg["adv_floor_cr"]),
        cfg["features"],
        cfg["spec"],
        cfg["growth_mode"],
        raw,
    )
    if key not in cache:
        cache[key] = build_matrix(panel, cfg, reg, raw_input=raw)
    ckpt = ckpt_root / panel.snap_id / spec.config_hash[:12]
    ckpt.mkdir(parents=True, exist_ok=True)
    return TrialContext(spec, panel, cache[key], reg, ckpt, raw)


def assert_no_overlap(meta_train: pd.DataFrame, test_anchors_: list[pd.Timestamp], label_end: pd.Series) -> None:
    """CPCV row-level check: no training label span (A, label_end] overlaps a test group's span (T_first, L_max]."""
    a = pd.to_datetime(meta_train["anchor"])
    e = pd.to_datetime(meta_train["label_end"])
    for t in test_anchors_:
        l_max = label_end[t]
        bad = (a < l_max) & (e > t) & ~a.isin(test_anchors_)
        if bad.any():
            raise LeakageError(f"{int(bad.sum())} training rows overlap the label span of test anchor {t.date()}")


def _fit_predict(
    ctx: TrialContext,
    train_anchors: list[pd.Timestamp],
    test_anchors_: list[pd.Timestamp],
    *,
    importance: bool,
    cpcv: bool = False,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    m = ctx.matrix
    anchors = m.meta["anchor"]
    design = ctx.design()
    tr = anchors.isin(train_anchors).to_numpy() & m.meta["labelled"].to_numpy() & m.y.notna().to_numpy()
    te = anchors.isin(test_anchors_).to_numpy()
    if not tr.any() or not te.any():
        raise ValueError(f"empty fold: train {int(tr.sum())} rows, test {int(te.sum())} rows")
    if cpcv:
        assert_no_overlap(m.meta.loc[tr], test_anchors_, ctx.panel.label_end)
    else:
        assert_purged(m.meta.loc[tr], min(test_anchors_))
    t0 = time.perf_counter()
    model = ctx.fresh_model().fit(design.loc[tr], m.y.loc[tr].to_numpy(dtype=float), m.meta.loc[tr])
    fit_s = time.perf_counter() - t0
    scored = m.meta.loc[te, SCORE_META].copy()
    scored["score"] = np.asarray(model.predict(design.loc[te], m.meta.loc[te]), dtype=float)
    info: dict[str, Any] = {
        "fit_seconds": round(fit_s, 2),
        "n_train": int(tr.sum()),
        "n_test": int(te.sum()),
        "warnings": list(getattr(model, "warnings_", [])),
        "empty": bool(getattr(model, "empty_", False) or not np.isfinite(scored["score"]).any()),
    }
    if hasattr(model, "selection_"):
        info["selected"] = list(getattr(model.selection_, "selected", []))
    if importance and not info["empty"]:
        info["importance"] = _importance(ctx, model, design, tr, te)
    return scored, info


def _importance(
    ctx: TrialContext, model: Any, design: pd.DataFrame, tr: np.ndarray, te: np.ndarray
) -> dict[str, float]:
    """Clustered permutation importance of the fold's model on its labelled test rows (02 §6.5)."""
    m = ctx.matrix
    proto = ctx.cfg["protocol"]["wfo"]
    cols = value_columns(list(design.columns))
    xtr = design.loc[tr, cols]
    clusters = dict.fromkeys(cols, 1) if len(cols) < 2 else feature_clusters(xtr.fillna(xtr.median(numeric_only=True)))
    lab = te & m.meta["labelled"].to_numpy()
    meta_te = m.meta.loc[lab]
    x_te = design.loc[lab]
    y_te = m.y.loc[lab]

    def predict(xp: pd.DataFrame) -> np.ndarray:
        return model.predict(xp, meta_te)

    # clusters cover value columns only; flags stay as they are
    imp = cluster_permutation_importance(
        predict,
        x_te,
        y_te,
        meta_te["anchor"],
        clusters,
        n_repeats=int(proto["importance_repeats"]),
        seed=int(proto["importance_seed"]),
    )
    per_feature = feature_importance(imp, clusters)
    return {str(k): float(v) for k, v in per_feature.items()}


def _fold_paths(ctx: TrialContext, name: str) -> tuple[Path, Path]:
    return ctx.ckpt / f"{name}.parquet", ctx.ckpt / f"{name}.json"


def wfo_folds(ctx: TrialContext) -> list[Fold]:
    cfg = ctx.cfg
    counted, indicative = test_anchors(ctx.panel, cfg)
    n, start = window_args(cfg)
    anchors = [pd.Timestamp(a) for a in ctx.panel.anchors]
    return annual_folds(anchors, ctx.panel.label_end, sorted(indicative + counted), n_train=n, train_start=start)


def run_wfo(
    ctx: TrialContext, *, importance: bool = True, log_fn: Callable[[str], None] | None = None
) -> tuple[pd.DataFrame, dict[str, dict[str, Any]]]:
    """OOS scores (all test anchors) and per-fold info. Fits only the folds without a checkpoint."""
    frames, infos = [], {}
    for fold in wfo_folds(ctx):
        pq, js = _fold_paths(ctx, f"wfo_{fold.name}")
        if pq.exists() and js.exists():
            scored, info = pd.read_parquet(pq), json.loads(js.read_text())
            scored["anchor"] = pd.to_datetime(scored["anchor"])
        else:
            scored, info = _fit_predict(ctx, list(fold.train_anchors), list(fold.test_anchors), importance=importance)
            info.update(train_anchors=[a.date().isoformat() for a in fold.train_anchors])
            scored["fold"] = fold.name
            scored.to_parquet(pq, index=False)
            js.write_text(json.dumps(info))
            if log_fn:
                log_fn(f"{ctx.spec.trial_id} wfo {fold.name}: fit {info['fit_seconds']} s, empty={info['empty']}")
        frames.append(scored.assign(fold=fold.name))
        infos[fold.name] = info
    oos = pd.concat(frames, ignore_index=True)
    oos = add_rank_columns(oos, ctx.matrix, "wfo")
    return oos, infos


def add_rank_columns(oos: pd.DataFrame, m: TrialMatrix, kind: str) -> pd.DataFrame:
    """p, decile and the training target for scored rows (p over every scored name of the anchor, labelled or not)."""
    out = oos.copy()
    out["p"] = percentile(out["score"], by=out["anchor"].to_numpy()).to_numpy()
    out["bucket_dec"] = decile(out["p"]).astype("float").to_numpy()
    y = m.y.reindex(m.meta.index)
    key = pd.MultiIndex.from_frame(m.meta[["anchor", "security_id"]])
    ymap = pd.Series(y.to_numpy(), index=key)
    out["label"] = ymap.reindex(pd.MultiIndex.from_frame(out[["anchor", "security_id"]])).to_numpy()
    return out


def cpcv_split_list(ctx: TrialContext) -> list[CPCVSplit]:
    p = ctx.cfg["protocol"]["cpcv"]
    anchors = [pd.Timestamp(a) for a in ctx.panel.anchors]
    return cpcv_splits(anchors, ctx.panel.label_end, int(p["n_groups"]), int(p["k"]), int(p["embargo"]))


def run_cpcv(ctx: TrialContext, *, log_fn: Callable[[str], None] | None = None) -> tuple[pd.DataFrame, list[CPCVSplit]]:
    """OOS scores of every CPCV split: columns ``split`` plus the SCORE_META columns and ``score``."""
    splits = cpcv_split_list(ctx)
    cfg = ctx.cfg
    start = pd.Timestamp(cfg["train_window"]["start"]) if cfg["train_window"].get("start") else None
    frames = []
    for s in splits:
        pq, js = _fold_paths(ctx, f"cpcv_{s.index:02d}")
        if pq.exists():
            scored = pd.read_parquet(pq)
            scored["anchor"] = pd.to_datetime(scored["anchor"])
        else:
            train = [a for a in s.train_anchors if start is None or a >= start]
            scored, info = _fit_predict(ctx, train, list(s.test_anchors), importance=False, cpcv=True)
            scored.to_parquet(pq, index=False)
            js.write_text(json.dumps(info))
            if log_fn:
                log_fn(f"{ctx.spec.trial_id} cpcv {s.index}/{len(splits)}: fit {info['fit_seconds']} s")
        frames.append(scored.assign(split=s.index))
    out = pd.concat(frames, ignore_index=True)
    m = ctx.matrix
    ymap = pd.Series(m.y.to_numpy(), index=pd.MultiIndex.from_frame(m.meta[["anchor", "security_id"]]))
    out["label"] = ymap.reindex(pd.MultiIndex.from_frame(out[["anchor", "security_id"]])).to_numpy()
    return out, splits
