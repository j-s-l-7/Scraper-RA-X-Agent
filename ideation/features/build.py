"""Build fundamental features for a list of anchors from one ``PitStore`` and persist them.

Members per anchor are exactly the securities ``universe.build.build_universe`` produces (same stitched securities,
same ``mcap_pit`` hierarchy), computed here in memory so that the features never depend on the universe parquet.
Rows: every traded, company-linked security. Output:
``IDEATION_DATA_DIR/outputs/<snap_id>/features_fundamental.parquet``.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from ideation.features.fundamental import build_fundamental
from ideation.features.registry import Registry
from ideation.pit.contract import ManifestRecord, PitStore
from ideation.universe.build import UniverseConfig, build_universe

FEATURES_FILE = "features_fundamental.parquet"


def features_path(data_dir: Path, snap_id: str) -> Path:
    return data_dir / "outputs" / snap_id / FEATURES_FILE


def build_anchor(
    store: PitStore, reg: Registry, ucfg: UniverseConfig, anchor: date
) -> tuple[pd.DataFrame, list[ManifestRecord]]:
    view = store.as_of(anchor)
    mem, _ = build_universe(view, ucfg)
    mem = mem[mem["traded_ok"] & mem["company_id"].notna()]
    feats = build_fundamental(view, mem, reg)
    return feats, view.records


def build_all(
    store: PitStore, reg: Registry, ucfg: UniverseConfig, anchors: list[date]
) -> tuple[pd.DataFrame, list[ManifestRecord]]:
    frames, records = [], []
    for a in anchors:
        f, r = build_anchor(store, reg, ucfg, a)
        frames.append(f)
        records.extend(r)
    return pd.concat(frames, ignore_index=True), records
