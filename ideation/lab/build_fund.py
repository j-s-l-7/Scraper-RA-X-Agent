# ruff: noqa: E501
"""Monthly-anchor fundamental features, built in-process into ``IDEATION_DATA_DIR/lab/<snap>/`` (never into the
shared ``outputs/<snap>/``, whose sha256 other phases pin). Same builder as ``ideation features build``."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd


def lab_dir(data_dir: Path, snap_id: str) -> Path:
    d = data_dir / "lab" / snap_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def build_monthly_fundamental(*, force: bool = False) -> Path:
    from ideation.config import get_settings, load_methodology
    from ideation.features.build import build_all
    from ideation.features.registry import load_registry
    from ideation.pit.calendar import parse_anchors
    from ideation.pit.contract import PitStore
    from ideation.universe.build import UniverseConfig

    settings = get_settings()
    meth = load_methodology()
    reg = load_registry(settings.config_dir)
    store = PitStore.latest(settings.data_dir)
    out = lab_dir(settings.data_dir, store.snap_id) / "fund_monthly.parquet"
    if out.exists() and not force:
        return out
    ucfg = UniverseConfig.from_methodology(meth.data)
    anchors = parse_anchors(store.calendar, "monthly")
    t0 = time.perf_counter()
    feats, _, cov = build_all(store, reg, ucfg, anchors)
    feats.to_parquet(out, index=False)
    cov.to_parquet(out.with_name("fund_monthly_coverage.parquet"), index=False)
    print(f"built {len(anchors)} anchors, {len(feats)} rows in {time.perf_counter() - t0:.0f}s -> {out}", file=sys.stderr)
    return out


def load_monthly_fundamental(data_dir: Path, snap_id: str) -> pd.DataFrame:
    return pd.read_parquet(lab_dir(data_dir, snap_id) / "fund_monthly.parquet")


def build_monthly_quality(*, force: bool = False) -> Path:
    """Quality / investment features at every monthly anchor (same members as the fundamental build)."""
    from ideation.config import get_settings, load_methodology
    from ideation.features.quality import BASE_LINES, build_quality
    from ideation.features.registry import load_registry
    from ideation.pit.calendar import parse_anchors
    from ideation.pit.contract import PitStore
    from ideation.universe.build import UniverseConfig, build_universe

    settings = get_settings()
    meth = load_methodology()
    reg = load_registry(settings.config_dir)
    store = PitStore.latest(settings.data_dir)
    out = lab_dir(settings.data_dir, store.snap_id) / "quality_monthly.parquet"
    if out.exists() and not force:
        return out
    ucfg = UniverseConfig.from_methodology(meth.data)
    ids_all = reg.validate_metrics(store.as_of(parse_anchors(store.calendar, "monthly")[-1]).metric())
    ids = {a: ids_all[a] for a in BASE_LINES}
    frames = []
    t0 = time.perf_counter()
    for a in parse_anchors(store.calendar, "monthly"):
        view = store.as_of(a)
        mem, _ = build_universe(view, ucfg)
        members = mem[mem["traded_ok"] & mem["company_id"].notna()]
        q = build_quality(view, members, reg, ids)
        q.insert(0, "anchor", a)
        frames.append(q)
    pd.concat(frames, ignore_index=True).to_parquet(out, index=False)
    print(f"quality: {len(frames)} anchors in {time.perf_counter() - t0:.0f}s -> {out}", file=sys.stderr)
    return out
