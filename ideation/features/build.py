"""Build fundamental features for a list of anchors from one ``PitStore`` and persist them.

Members per anchor are exactly the securities ``universe.build.build_universe`` produces (same stitched securities,
same ``mcap_pit`` hierarchy), computed here in memory so that the features never depend on the universe parquet.
Rows: every traded, company-linked security. Output:
``IDEATION_DATA_DIR/outputs/<snap_id>/features_fundamental.parquet`` (+ ``features_coverage.parquet``).

The coverage rows describe U_score as the hook will make it: ``in_u_score`` from the in-memory universe, minus names
with ``feature_complete`` below ``universe.u_score.min_feature_complete`` (the last step of the exclusion waterfall).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from ideation.features.fundamental import SOURCE, build_fundamental
from ideation.features.registry import Registry
from ideation.pit.contract import ManifestRecord, PitStore
from ideation.universe.build import UniverseConfig, build_universe

FEATURES_FILE = "features_fundamental.parquet"
COVERAGE_FILE = "features_coverage.parquet"
COVERAGE_FEATURES = ("roe", "ep", "ebitda_ev", "fcf_yield", "roic", "rev_g1", "roe_3y", "eps_g3", "nd_ebitda")


def features_path(data_dir: Path, snap_id: str) -> Path:
    return data_dir / "outputs" / snap_id / FEATURES_FILE


def coverage_path(data_dir: Path, snap_id: str) -> Path:
    return data_dir / "outputs" / snap_id / COVERAGE_FILE


def coverage_row(
    anchor: date, mem: pd.DataFrame, feats: pd.DataFrame, reg: Registry, ucfg: UniverseConfig
) -> dict[str, Any]:
    thr = float(ucfg["u_score"]["min_feature_complete"])
    before = mem[mem["in_u_score"]]
    f = feats.set_index("security_id").reindex(before["security_id"])
    keep = ~(f["feature_complete"] < thr).to_numpy()
    u = f[keep]
    row: dict[str, Any] = {
        "anchor": anchor,
        "u_score_before": len(before),
        "u_score_after": int(keep.sum()),
        "dropped": int((~keep).sum()),
        "n_financial": int(u["layout"].isin(["fin_layout", "fin_other"]).sum()),
        "feature_complete_mean": float(u["feature_complete"].mean()) if u["feature_complete"].notna().any() else None,
        "c_i_mean": float(u["c_i"].mean()) if u["c_i"].notna().any() else None,
    }
    for n in COVERAGE_FEATURES:
        applicable = int(u["layout"].isin(reg.features[n].layouts).sum())
        row[n] = float(u[n].notna().sum() / applicable) if applicable else None
    return row


def build_anchor(
    store: PitStore, reg: Registry, ucfg: UniverseConfig, anchor: date
) -> tuple[pd.DataFrame, list[ManifestRecord], dict[str, Any]]:
    view = store.as_of(anchor)
    mem, _ = build_universe(view, ucfg)
    members = mem[mem["traded_ok"] & mem["company_id"].notna()]
    feats = build_fundamental(view, members, reg)
    return feats, view.records, coverage_row(anchor, mem, feats, reg, ucfg)


def build_all(
    store: PitStore, reg: Registry, ucfg: UniverseConfig, anchors: list[date]
) -> tuple[pd.DataFrame, list[ManifestRecord], pd.DataFrame]:
    frames, records, cov = [], [], []
    for a in anchors:
        f, r, c = build_anchor(store, reg, ucfg, a)
        frames.append(f)
        records.extend(r)
        cov.append(c)
    return pd.concat(frames, ignore_index=True), records, pd.DataFrame(cov)


__all__ = ["SOURCE", "build_all", "build_anchor", "coverage_path", "features_path"]
