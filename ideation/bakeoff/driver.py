"""The bake-off loop (05 §3, §8): every runnable trial of the family, WFO then CPCV, results parquet per trial.

Trials 44-45 (``subset: text_covered``) are skipped here and stay ``registered`` (MS1-text runs them later). Consensus
trials 46-48 are built from the members' predictions after the members ran. Window variants cannot be expressed in
CPCV (it trains on every non-purged anchor), so their CPCV equals their base's and is copied, not refitted.
"""

from __future__ import annotations

import json
import logging
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from ideation.bakeoff import run as R
from ideation.bakeoff.data import Panel, TrialMatrix, load_panel
from ideation.bakeoff.metrics import consensus_scores
from ideation.config import canonical_json
from ideation.features.registry import Registry, load_registry
from ideation.trials.registry import TrialSpec

log = logging.getLogger("ideation.bakeoff")
CPCV_COPY_FROM = {"P1_WFO_2Y": "P1_WFO_3Y", "RF_GLOBAL_TRAIN2Y": "RF_GLOBAL", "RF_GLOBAL_EXPANDING": "RF_GLOBAL"}
"""Trials whose only difference from the base is the walk-forward window."""


def is_text(spec: TrialSpec) -> bool:
    return bool(spec.config.get("subset"))


def results_dir(root: Path, snap_id: str, spec: TrialSpec) -> Path:
    d = root / "bakeoff" / snap_id / "results" / spec.config_hash[:12]
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class TrialOutcome:
    spec: TrialSpec
    status: str
    seconds: float
    error: str | None = None


def _save(d: Path, oos: pd.DataFrame, cpcv: pd.DataFrame | None, infos: dict[str, Any], extra: dict[str, Any]) -> None:
    oos.to_parquet(d / "oos.parquet", index=False)
    if cpcv is not None:
        cpcv.to_parquet(d / "cpcv.parquet", index=False)
    (d / "meta.json").write_text(json.dumps({"folds": infos, **extra}, default=str))


def load_oos(d: Path) -> pd.DataFrame:
    f = pd.read_parquet(d / "oos.parquet")
    f["anchor"] = pd.to_datetime(f["anchor"])
    return f


def load_cpcv(d: Path) -> pd.DataFrame | None:
    p = d / "cpcv.parquet"
    if not p.exists():
        return None
    f = pd.read_parquet(p)
    f["anchor"] = pd.to_datetime(f["anchor"])
    return f


def _attach_label_end(oos: pd.DataFrame, panel: Panel) -> pd.DataFrame:
    ends = panel.label_end
    out = oos.copy()
    out["label_end"] = out["anchor"].map(ends)
    return out


def run_bakeoff(
    specs: list[TrialSpec],
    panels: dict[str, Panel],
    reg: Registry,
    data_root: Path,
    *,
    cpcv: bool = True,
    only: set[str] | None = None,
    log_fn: Callable[[str], None] = print,
    on_done: Callable[[TrialOutcome], None] | None = None,
) -> list[TrialOutcome]:
    """Run (resume) every runnable trial. ``on_done`` is called after each trial (the CLI persists status there)."""
    ckpt_root = data_root / "bakeoff"
    cache: dict[tuple, TrialMatrix] = {}
    by_id = {s.trial_id: s for s in specs}
    outcomes = []
    for spec in specs:
        if is_text(spec):
            log_fn(f"{spec.trial_id}: text trial, left 'registered' (MS1-text)")
            continue
        if only and spec.trial_id not in only:
            continue
        panel = panels[spec.config["anchor_family"]]
        t0 = time.perf_counter()
        d = results_dir(data_root, panel.snap_id, spec)
        try:
            if (
                (d / "meta.json").exists()
                and (d / "oos.parquet").exists()
                and (not cpcv or (d / "cpcv.parquet").exists())
            ):
                log_fn(f"{spec.trial_id}: results exist, skipping")
                out = TrialOutcome(spec, "ok", 0.0)
            elif "consensus" in spec.config:
                _run_consensus(spec, by_id, panel, data_root, d, cpcv=cpcv)
                out = TrialOutcome(spec, "ok", time.perf_counter() - t0)
            else:
                ctx = R.make_context(spec, panel, reg, ckpt_root, cache)
                oos, infos = R.run_wfo(ctx, log_fn=log_fn)
                oos = _attach_label_end(oos, panel)
                cp = None
                if cpcv:
                    base = CPCV_COPY_FROM.get(spec.trial_id)
                    if (
                        base is not None
                        and (bd := results_dir(data_root, panel.snap_id, by_id[base])).exists()
                        and (bd / "cpcv.parquet").exists()
                    ):
                        cp = pd.read_parquet(bd / "cpcv.parquet")
                        log_fn(f"{spec.trial_id}: CPCV = {base}'s (window-invariant), copied")
                    else:
                        cp, _ = R.run_cpcv(ctx, log_fn=log_fn)
                _save(d, oos, cp, infos, {"seconds": round(time.perf_counter() - t0, 1), "notes": ctx.notes})
                out = TrialOutcome(spec, "ok", time.perf_counter() - t0)
            log_fn(f"{spec.trial_id}: ok in {out.seconds:.0f} s")
        except Exception as exc:  # a failed trial is a result, not a crash
            tb = traceback.format_exc()
            log_fn(f"{spec.trial_id}: FAILED {type(exc).__name__}: {exc}")
            log.error("trial failed", extra={"trial": spec.trial_id, "tb": tb})
            (d / "error.txt").write_text(tb)
            out = TrialOutcome(spec, "failed", time.perf_counter() - t0, f"{type(exc).__name__}: {exc}")
        outcomes.append(out)
        if on_done:
            on_done(out)
    return outcomes


def _members(spec: TrialSpec, by_id: dict[str, TrialSpec], panel: Panel, data_root: Path) -> dict[str, Path]:
    return {m: results_dir(data_root, panel.snap_id, by_id[m]) for m in spec.config["members"]}


def _run_consensus(
    spec: TrialSpec, by_id: dict[str, TrialSpec], panel: Panel, data_root: Path, d: Path, *, cpcv: bool
) -> None:
    rule = spec.config["consensus"]
    kw = {"min_votes": int(spec.config.get("min_votes", 6)), "min_names": int(spec.config.get("min_names", 5))}
    dirs = _members(spec, by_id, panel, data_root)
    for m, md in dirs.items():
        if not (md / "oos.parquet").exists():
            raise FileNotFoundError(f"consensus member {m} has no results (failed or not run)")
    oos_members = {m: load_oos(md) for m, md in dirs.items()}
    oos = consensus_scores(oos_members, rule, keys=("anchor",), **kw)
    oos = _rank_cols(oos)
    oos = _attach_label_end(oos, panel)
    cp = None
    if cpcv:
        cm = {m: load_cpcv(md) for m, md in dirs.items()}
        if any(v is None for v in cm.values()):
            raise FileNotFoundError("consensus member without CPCV results")
        cp = consensus_scores(cm, rule, keys=("split", "anchor"), **kw)  # type: ignore[arg-type]
        cp["label"] = cp["label"].astype(float)
    _save(d, oos, cp, {}, {"members": list(dirs), "rule": rule, "kw": kw})


def _rank_cols(oos: pd.DataFrame) -> pd.DataFrame:
    from ideation.rank.buckets import decile
    from ideation.rank.percentile import percentile

    out = oos.copy()
    out["p"] = percentile(out["score"], by=out["anchor"].to_numpy()).to_numpy()
    out["bucket_dec"] = decile(out["p"]).astype("float").to_numpy()
    return out


def load_panels(data_dir: Path, snap_id: str, families: set[str], reg: Registry | None = None) -> dict[str, Panel]:
    reg = reg or load_registry()
    out_dir = data_dir / "outputs" / snap_id
    return {f: load_panel(out_dir, snap_id, f, reg) for f in sorted(families)}


def config_blob(spec: TrialSpec) -> str:
    return canonical_json(spec.config)
