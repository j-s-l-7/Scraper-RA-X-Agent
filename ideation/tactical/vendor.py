"""Vendor-surprise challenger features at the monthly anchors (05 §6.3). Built only when a trial's own config sets
``allow_vendor_estimates`` (the CLI passes that flag from the trial config) and the validity gate passed. Cached."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path

import pandas as pd
import yaml

from ideation.config import Settings
from ideation.features.events_vendor import build_vendor
from ideation.features.registry import load_registry
from ideation.pit.contract import PitStore
from ideation.tactical.data import vendor_cache_path

log = logging.getLogger("ideation.tactical")
FIRST_ANCHOR = pd.Timestamp("2020-01-01")  # nothing earlier is trained on (T1 train_start 2020-04-30)


def build_or_load_vendor(settings: Settings, snap_id: str, anchors: Sequence[pd.Timestamp]) -> pd.DataFrame:
    path: Path = vendor_cache_path(settings.data_dir, snap_id)
    if path.exists():
        return pd.read_parquet(path)
    reg = load_registry(settings.config_dir)
    params = yaml.safe_load((settings.config_dir / "features_vendor.yaml").read_text())["params"]
    store = PitStore.latest(settings.data_dir, outputs_dir=settings.data_dir / "outputs" / snap_id)
    frames = []
    for a in anchors:
        if a < FIRST_ANCHOR:
            continue
        view = store.as_of(a.date(), record=False)
        uni = view.universe()
        members = uni[uni["traded_ok"] & uni["company_id"].notna()]
        frames.append(build_vendor(view, members, reg, params, allow_vendor_estimates=True))
        log.info("vendor features %s: %d rows", a.date(), len(frames[-1]))
    out = pd.concat(frames, ignore_index=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(path, index=False)
    return out
