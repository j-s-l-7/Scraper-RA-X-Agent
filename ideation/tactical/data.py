"""Data assembly for the tactical bake-off (MS2, 05 §3.7, §7.3, §8). No database access.

* Features: ``features_tech.parquet`` (P3) joined with ``universe_member.parquet`` (ADV63) and, for the subset tiers,
  ``features_text.parquet`` (PT2) and the vendor-surprise features (PT2, built per anchor from the trial config).
* Labels: **only** through :func:`ideation.labels.loader.load_labels` with sealed anchors removed
  (``unsealed_only``); the last anchor used anywhere is ``ANCHOR_MAX`` = 2025-06-30 (the tactical holdout is sealed from
  2025-07-31).
* R1 (``rank.transform.xs_transform``) runs per anchor on the rows of a *row subset*; the subset filter
  (``isna_text == 0`` / ``isna_vsurp == 0`` / ADV floor) is applied BEFORE R1, so nothing is zero-filled into an
  uncovered name (05 §7.3).
* Feature columns are picked by registry ``set``/``source`` (never ``reg.names()`` globally).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ideation.features.registry import Registry, load_registry
from ideation.labels.loader import load_labels, unsealed_only
from ideation.rank.transform import TransformSpec, rank_gauss, xs_transform

ANCHOR_MIN = pd.Timestamp("2011-01-01")
ANCHOR_MAX = pd.Timestamp("2025-06-30")
LAKH_PER_CR = 100.0
CODE_VERSION = "ms2-1"

FEATURE_SETS: dict[str, tuple[str, ...]] = {
    "T0": ("tech",),
    "T0_regime": ("tech", "regime"),
    "T1": ("tech", "t1"),
    "T1_text": ("tech", "t1", "text"),
    "T1_vsurp": ("tech", "t1", "vendor"),
}
RAW_FLAG_COLS = ("isna_event",)  # already a 0/1 flag: appended raw, not rank-gaussed


def feature_columns(reg: Registry, fset: str) -> tuple[list[str], list[str], list[str]]:
    """(R1 value columns, raw flag columns, broadcast columns) of a tactical feature set, picked by registry set."""
    sets = FEATURE_SETS[fset]
    names: list[str] = []
    for s in sets:
        names.extend(reg.names(sets=[s]))
    bcast = [n for n in names if n in reg.broadcast_cols()]
    flags = [n for n in names if n in RAW_FLAG_COLS]
    values = [n for n in names if n not in flags]
    return values, flags, bcast


@dataclass
class Panel:
    """All unsealed monthly rows of U_score_T with labels attached."""

    frame: pd.DataFrame
    anchors: list[pd.Timestamp]
    label_end: pd.Series
    """Per-anchor max label end of the 3M labels (the C3 purge key)."""
    reg: Registry
    snap_id: str
    labels_path: Path
    audit: list[tuple[str, int]] = field(default_factory=list)


def _norm_anchor(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s).dt.normalize()


def load_panel(outputs_dir: Path, snap_id: str, config_dir: Path | None = None) -> Panel:
    """Features + universe flags + 3M and 1M labels for every monthly anchor in [2011-01, 2025-06]."""
    reg = load_registry(config_dir)
    tech = pd.read_parquet(outputs_dir / "features_tech.parquet")
    tech["anchor"] = _norm_anchor(tech["anchor"])
    tech = tech[(tech["anchor"] >= ANCHOR_MIN) & (tech["anchor"] <= ANCHOR_MAX) & tech["in_u_score_t"]].copy()
    uni = pd.read_parquet(outputs_dir / "universe_member.parquet", columns=["anchor", "security_id", "adv63_lakh"])
    uni["anchor"] = _norm_anchor(uni["anchor"])
    tech = tech.merge(uni, on=["anchor", "security_id"], how="left", validate="1:1")

    anchors = sorted(tech["anchor"].unique())
    req = unsealed_only([a.date() for a in anchors], "3M")
    assert len(req) == len(anchors), "a sealed anchor slipped into the tactical panel"
    labels_path = outputs_dir / "labels.parquet"
    lab3 = load_labels(req, "3M", "fit", family="tactical-3M", source=labels_path)
    lab1 = load_labels(unsealed_only(req, "1M"), "1M", "evaluate", family="tactical-3M", source=labels_path)
    for lab in (lab3, lab1):
        lab["anchor"] = _norm_anchor(lab["anchor"])
    l3 = lab3[["anchor", "security_id", "ret_total", "ret_price", "label_end", "excess_univ_med", "excess_n500_pr",
               "exit_type"]].rename(columns={"ret_total": "ret_3m", "ret_price": "retp_3m",
                                              "excess_univ_med": "exu_3m", "excess_n500_pr": "exn_3m"})  # fmt: skip
    l1 = lab1[["anchor", "security_id", "ret_total", "excess_univ_med"]].rename(
        columns={"ret_total": "ret_1m", "excess_univ_med": "exu_1m"}
    )
    df = tech.merge(l3, on=["anchor", "security_id"], how="left").merge(l1, on=["anchor", "security_id"], how="left")
    df["label_end"] = pd.to_datetime(df["label_end"])
    df = df[df["ret_3m"].notna()].reset_index(drop=True)  # a missing label drops the row for every learner
    label_end = df.groupby("anchor")["label_end"].max()
    return Panel(df, [pd.Timestamp(a) for a in label_end.index], label_end, reg, snap_id, labels_path)


def attach_text(panel: Panel, outputs_dir: Path) -> pd.DataFrame:
    """Panel rows joined with PT2's text features (monthly anchors; ``isna_text`` = 1 where there is no current call)."""
    cols = ["anchor", "security_id", *panel.reg.names(sets=["text"]), "isna_text", "isna_text_prev", "isna_ppt"]
    txt = pd.read_parquet(outputs_dir / "features_text.parquet", columns=cols)
    txt["anchor"] = _norm_anchor(txt["anchor"])
    out = panel.frame.merge(txt, on=["anchor", "security_id"], how="left")
    out["isna_text"] = out["isna_text"].fillna(1).astype(int)
    return out


def vendor_cache_path(data_dir: Path, snap_id: str) -> Path:
    return data_dir / "tactical_ms2" / f"features_vendor_{snap_id}_{CODE_VERSION}.parquet"


def attach_vendor(panel_frame: pd.DataFrame, vendor: pd.DataFrame) -> pd.DataFrame:
    v = vendor.copy()
    v["anchor"] = _norm_anchor(v["anchor"])
    keep = ["anchor", "security_id", "f_vsurp_last", "f_vbeat_4q", "isna_vsurp"]
    out = panel_frame.merge(v[keep], on=["anchor", "security_id"], how="left")
    out["isna_vsurp"] = out["isna_vsurp"].fillna(1).astype(int)
    return out


# ── targets, matrices ────────────────────────────────────────────────────────────────────────────────────────
def target_rank_gauss(frame: pd.DataFrame, ret_col: str = "ret_3m") -> pd.Series:
    """Rank-gauss of the forward return within each anchor's cross-section of ``frame`` (the trial's universe)."""
    return frame.groupby("anchor", sort=False)[ret_col].transform(lambda s: rank_gauss(s))


@dataclass
class Design:
    """The model matrix of one (feature set, row subset): R1 output plus meta, aligned row by row."""

    X: pd.DataFrame
    meta: pd.DataFrame
    y: pd.Series
    broadcast: list[str]
    priors: dict[str, int]
    value_cols: list[str]
    raw: pd.DataFrame
    """The untransformed rows (scores/returns/flags) for evaluation."""


def build_design(
    frame: pd.DataFrame, reg: Registry, fset: str, *, adv_floor_cr: float = 0.2, subset: str | None = None
) -> Design:
    """Apply the universe floor and the subset filter, THEN R1 per anchor, then the rank-gauss target."""
    d = frame
    if adv_floor_cr > 0.2 + 1e-12:
        d = d[d["adv63_lakh"] >= adv_floor_cr * LAKH_PER_CR]
    if subset == "text_covered":
        d = d[d["isna_text"] == 0]
    elif subset == "vsurp_covered":
        d = d[d["isna_vsurp"] == 0]
    elif subset is not None:
        raise ValueError(f"unknown subset {subset!r}")
    d = d.reset_index(drop=True)
    values, flags, bcast = feature_columns(reg, fset)
    pool = [c for c in values if c in d.columns]
    if len(pool) != len(values):
        raise KeyError(f"columns missing from the panel for {fset}: {sorted(set(values) - set(pool))}")
    spec = TransformSpec(kind="global", priors=reg.priors(), broadcast_cols=frozenset(bcast))
    X = xs_transform(d, pool, spec)
    for f in flags:
        X[f] = d[f].fillna(1.0).astype(float)  # NaN = no visible event data: the flag means "no event"
    meta = d[["anchor", "security_id", "industry_group", "label_end"]].copy()
    y = target_rank_gauss(d)
    return Design(X, meta, y, bcast, {c: p for c, p in reg.priors().items() if c in pool}, pool, d)


def anchors_between(anchors: list[pd.Timestamp], lo: Any, hi: Any) -> list[pd.Timestamp]:
    lo_t, hi_t = pd.Timestamp(lo), pd.Timestamp(hi)
    return [a for a in anchors if lo_t <= a <= hi_t]


def rows_in(meta: pd.DataFrame, anchors: Any) -> np.ndarray:
    return pd.to_datetime(meta["anchor"]).isin(list(anchors)).to_numpy()


def as_date(x: Any) -> date:
    return pd.Timestamp(x).date()
