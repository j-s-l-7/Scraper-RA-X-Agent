"""Assemble the tactical feature matrix (technical + flow + event + regime columns) for anchors.

Rows per anchor: every ``traded_ok`` security of the anchor's PIT universe (mapped or not), with the flags
``in_u_score`` / ``in_u_score_t`` carried so the consumer picks its universe. Membership comes from the stored
universe table (``PitView.universe()``, written by ``ideation universe build``) and falls back to an in-memory
``build_universe`` when the anchor is not stored or ``rebuild_universe`` is set (T-01 uses that to prove membership is
point-in-time too). The ``trainable_from`` mask of the registry is applied last, so no downstream step can forget it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from ideation.features import events, flows, technical
from ideation.features.registry import Registry
from ideation.pit.contract import ManifestRecord, PitStore, PitView
from ideation.regime.context import build_context
from ideation.regime.regime import REGIME_NAMES, compute_regime, regime_labels
from ideation.regime.rotation import rrg_table
from ideation.universe.build import UniverseConfig, build_universe

ID_COLS = ["anchor", "security_id", "company_id", "industry_group", "sector", "in_u_score", "in_u_score_t"]
FEATURE_FILES = (technical.SOURCE, events.SOURCE)


@dataclass
class AnchorResult:
    features: pd.DataFrame
    regime: dict[str, float]
    labels: dict[str, str]
    rrg: pd.DataFrame
    records: list[ManifestRecord]


def anchor_members(view: PitView, ucfg: UniverseConfig | None, *, rebuild: bool = False) -> pd.DataFrame:
    mem = view.universe() if not rebuild and view.has_universe() else pd.DataFrame()
    if mem.empty:
        if ucfg is None:
            raise ValueError(f"no stored universe for {view.anchor} and no UniverseConfig to build one")
        mem, _ = build_universe(view, ucfg)
    mem = mem[mem["traded_ok"].astype(bool)]
    return mem[["security_id", "in_u_score", "in_u_score_t"]].reset_index(drop=True)


def apply_trainable_mask(df: pd.DataFrame, reg: Registry, anchor: date) -> pd.DataFrame:
    """NaN every column whose ``trainable_from`` lies after ``anchor`` (01 P3 #4)."""
    for name, tf in reg.trainable_from().items():
        if name in df.columns and anchor < tf:
            df[name] = np.nan
    return df


def build_anchor(
    store: PitStore, reg: Registry, ucfg: UniverseConfig | None, anchor: date, *, rebuild_universe: bool = False,
    with_rrg_table: bool = True,
) -> AnchorResult:  # fmt: skip
    view = store.as_of(anchor)
    return build_view(view, reg, ucfg, rebuild_universe=rebuild_universe, with_rrg_table=with_rrg_table)


def build_view(
    view: PitView, reg: Registry, ucfg: UniverseConfig | None, *, rebuild_universe: bool = False,
    with_rrg_table: bool = True,
) -> AnchorResult:  # fmt: skip
    members = anchor_members(view, ucfg, rebuild=rebuild_universe)
    ctx = build_context(view, members)
    rrg = rrg_table(ctx, view)
    vec = compute_regime(ctx, view)
    tech = technical.technical_columns(ctx, rrg)
    flw = flows.flow_columns(view, ctx)
    evt = events.event_columns(ctx, view.earnings_dates())
    n = len(ctx.members)
    ids = ctx.ids.iloc[ctx.col].reset_index(drop=True)
    base = pd.DataFrame({
        "anchor": [view.anchor] * n, "security_id": ctx.members["security_id"].to_numpy(),
        "company_id": ids["company_id"].to_numpy(), "industry_group": ids["industry_group"].to_numpy(),
        "sector": ids["sector"].to_numpy(), "in_u_score": ctx.members["in_u_score"].astype(bool).to_numpy(),
        "in_u_score_t": ctx.members["in_u_score_t"].astype(bool).to_numpy(),
    })  # fmt: skip
    reg_cols = pd.DataFrame({f"regime_{k}": np.full(n, vec[k]) for k in REGIME_NAMES})
    df = pd.concat([base, tech, flw, evt, reg_cols], axis=1)
    df = apply_trainable_mask(df, reg, view.anchor)
    df = df.sort_values("security_id", kind="stable").reset_index(drop=True)
    return AnchorResult(df, vec, regime_labels(vec), rrg if with_rrg_table else rrg.iloc[0:0], view.records)


def expected_columns(reg: Registry) -> list[str]:
    return [n for n, f in reg.features.items() if f.source in FEATURE_FILES]
