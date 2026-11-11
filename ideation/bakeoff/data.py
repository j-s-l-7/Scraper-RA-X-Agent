"""The 1Y matrix for one trial (05 §2 R0-R1, 05 §3.1).

One frame per anchor family holds every U_score member (all trial universes are subsets of U_score) with the
fundamental columns, the technical/regime columns and the label columns. A trial then picks its universe rows, its
feature list and its R1 spec; R1 runs per anchor on the **whole** trial universe at that anchor (labelled or not), so
test-anchor inputs never depend on which names later had labels. Rows without a label are dropped only from training
and from the metrics (``labelled`` mask), identically for every learner. The target is the rank-gauss of
``ret_total`` within the trial's own universe at each anchor.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ideation.features.registry import Registry
from ideation.features.transform import na_layout_frame, raw_winsorized
from ideation.labels.loader import load_labels, unsealed_only
from ideation.rank.transform import TransformSpec, rank_gauss, xs_transform

FIRST_ANCHOR_YEAR = 2016
LAST_ANCHOR_YEAR = 2024
FUND_FILE = "features_fundamental.parquet"
TECH_FILE = "features_tech.parquet"
UNIVERSE_FILE = "universe_member.parquet"
LABEL_FILE = "labels.parquet"
FAMILY_MONTH = {"june": 6, "march": 3}
ID_COLS = ["anchor", "security_id", "company_id", "industry_group", "sector", "layout"]
LABEL_COLS = ["label_end", "ret_total", "excess_univ_med", "excess_n500_pr", "exit_type"]


@dataclass
class Panel:
    """All rows of one anchor family (June or March), anchors 2016..2024."""

    family: str
    snap_id: str
    frame: pd.DataFrame
    anchors: list[date]
    label_end: pd.Series
    files: dict[str, str] = field(default_factory=dict)
    """input file -> sha256 (run_manifest)."""

    def universe_rows(self, rules: Mapping[str, Any]) -> pd.DataFrame:
        mask = np.ones(len(self.frame), dtype=bool)
        for col in rules["all"]:
            mask &= self.frame[col].to_numpy(dtype=bool)
        return self.frame[mask].reset_index(drop=True)


def family_anchors(
    universe: pd.DataFrame, family: str, first: int = FIRST_ANCHOR_YEAR, last: int = LAST_ANCHOR_YEAR
) -> list[date]:
    """The last session of the family month, per year (the anchors present in the universe table)."""
    month = FAMILY_MONTH[family]
    anchors = sorted({a for a in universe["anchor"].unique() if a.month == month and first <= a.year <= last})
    return anchors


TEXT_FILE = "features_text.parquet"
TEXT_FLAGS = ("isna_text", "isna_text_prev", "isna_ppt")


def attach_text(panel: Panel, out_dir: Path, reg: Registry) -> Panel:
    """Panel with PT2's raw text columns and the coverage flags (MS1-text, 05 §7.3).

    Raw columns only (never the ``_rg`` copies); a name without a row in the text file counts as uncovered
    (``isna_text`` = 1). The availability of every joined row is checked against the anchor, and only the panel's own
    anchors (June 2016..2024) are joined, so the 2025/2026 text rows never enter.
    """
    txt = pd.read_parquet(out_dir / TEXT_FILE)
    txt["anchor"] = pd.to_datetime(txt["anchor"]).dt.date
    txt = txt[txt["anchor"].isin(set(panel.anchors))]
    avail = pd.to_datetime(txt["text_available_from"])
    if (avail.notna() & (avail > pd.to_datetime(txt["anchor"]))).any():
        raise ValueError("text feature available after its anchor")
    cols = [n for n in reg.names(sets=["text"]) if n in txt.columns]
    txt = txt[["anchor", "security_id", *cols, *TEXT_FLAGS]]
    frame = panel.frame.merge(txt, on=["anchor", "security_id"], how="left", validate="1:1")
    for c in TEXT_FLAGS:
        frame[c] = frame[c].fillna(1).astype(int)
    files = {**panel.files, TEXT_FILE: sha256_file(out_dir / TEXT_FILE)}
    return Panel(panel.family, panel.snap_id, frame, panel.anchors, panel.label_end, files)


def sha256_file(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_panel(
    out_dir: Path, snap_id: str, family: str, reg: Registry, *, label_source: pd.DataFrame | Path | None = None
) -> Panel:
    """Fundamental + technical + regime columns and unsealed labels for the family's anchors."""
    uni = pd.read_parquet(out_dir / UNIVERSE_FILE)
    anchors = family_anchors(uni, family)
    uni = uni[uni["anchor"].isin(anchors) & uni["in_u_score"]][
        ["anchor", "security_id", "in_u_score", "in_u_score_adv05", "in_u1000", "adv63_lakh", "mcap_pit_cr"]
    ]
    fund = pd.read_parquet(out_dir / FUND_FILE)
    fund = fund[fund["anchor"].isin(anchors)]
    if (pd.to_datetime(fund["feature_available_from"]) > pd.to_datetime(fund["anchor"])).any():
        raise ValueError("fundamental feature_available_from after its anchor")
    fund_cols = [n for n in reg.names(source="features_fundamental.yaml") if n in fund.columns]
    fund = fund[["anchor", "security_id", "company_id", "industry_group", "sector", "layout", *fund_cols]]
    tech = pd.read_parquet(out_dir / TECH_FILE)
    tech = tech[tech["anchor"].isin(anchors)]
    tech_cols = [n for n in reg.names(sets=["tech", "regime"]) if n in tech.columns]
    tech = tech[["anchor", "security_id", *tech_cols]]
    frame = uni.merge(fund, on=["anchor", "security_id"], how="left", validate="1:1")
    frame = frame.merge(tech, on=["anchor", "security_id"], how="left", validate="1:1")
    # regime columns are anchor-constant: broadcast the anchor's single value to every row (also rows w/o a tech row)
    for c in reg.broadcast_cols():
        if c in frame.columns:
            frame[c] = frame.groupby("anchor")[c].transform("first")
    src = label_source if label_source is not None else out_dir / LABEL_FILE
    labels = load_labels(unsealed_only(anchors, "1Y"), "1Y", "fit", family="1Y-june", source=src)
    labels = labels[["anchor", "security_id", *LABEL_COLS]]
    frame = frame.merge(labels, on=["anchor", "security_id"], how="left", validate="1:1")
    frame["labelled"] = frame["ret_total"].notna()
    ends = pd.to_datetime(frame["label_end"])
    label_end = ends.groupby(pd.to_datetime(frame["anchor"])).max()
    frame = frame.sort_values(["anchor", "security_id"], kind="stable").reset_index(drop=True)
    files = {
        n: sha256_file(out_dir / n) for n in (UNIVERSE_FILE, FUND_FILE, TECH_FILE, LABEL_FILE) if (out_dir / n).exists()
    }
    return Panel(family, snap_id, frame, anchors, label_end, files)


@dataclass
class TrialMatrix:
    """What a model sees: X (R1), meta, target, plus the unlabelled-row mask."""

    X: pd.DataFrame
    meta: pd.DataFrame
    y: pd.Series
    value_cols: list[str]
    feature_names: list[str]
    broadcast: list[str]
    core_names: list[str]
    raw: pd.DataFrame | None = None


def resolve_features(cfg: Mapping[str, Any], reg: Registry) -> list[str]:
    spec = cfg["feature_sets"][cfg["features"]]
    base = reg.names(sets=[spec["base"]], source="features_fundamental.yaml")
    names = [*base, *[a for a in spec["add"] if a not in base]]
    unknown = [n for n in names if n not in reg.features]
    if unknown:
        raise KeyError(f"feature set {cfg['features']!r} names unknown features: {unknown}")
    return names


def universe_rule(cfg: Mapping[str, Any]) -> Mapping[str, Any]:
    key = cfg["universe"]
    if key == "U_score" and float(cfg["adv_floor_cr"]) == 0.5:
        key = "U_score_adv05"
    return cfg["universe_rules"][key]


def build_matrix(
    panel: Panel, cfg: Mapping[str, Any], reg: Registry, *, raw_input: Sequence[str] | None = None
) -> TrialMatrix:
    """R1 on the trial's universe. ``raw_input`` (P1_UNIFIED) returns the winsorized raw columns instead of R1."""
    names = resolve_features(cfg, reg)
    rows = panel.universe_rows(universe_rule(cfg)).copy()
    if cfg.get("subset") == "text_covered":
        # 05 §7.3: the covered subset is cut BEFORE R1, so nothing is zero-filled into an uncovered name and the
        # per-anchor transform, the target and the percentile all live inside the covered names.
        if "isna_text" not in rows.columns:
            raise KeyError("subset text_covered needs the text columns (data.attach_text)")
        rows = rows[rows["isna_text"] == 0].reset_index(drop=True)
    elif cfg.get("subset"):
        raise ValueError(f"unknown subset {cfg['subset']!r}")
    bcols = [n for n in names if n in reg.broadcast_cols()]
    growth = reg.growth_cols()
    spec = TransformSpec(
        kind=cfg["spec"],
        growth_mode=cfg["growth_mode"],
        growth_cols=growth,
        priors=reg.priors(),
        broadcast_cols=frozenset(bcols),
    )
    na_layout = na_layout_frame(rows, reg, [n for n in names if n in rows.columns])
    rows["layout"] = rows["layout"]
    x = xs_transform(rows, names, spec, na_layout)
    raw = None
    if raw_input is not None:
        raw = raw_winsorized(rows, reg, list(raw_input), growth_mode=cfg["growth_mode"])
    y = pd.Series(np.nan, index=rows.index)
    for _, idx in rows.groupby("anchor", sort=True).groups.items():
        lab = rows.loc[idx, "ret_total"]
        y.loc[idx] = rank_gauss(lab).to_numpy()
    meta = rows[
        [
            "anchor",
            "security_id",
            "industry_group",
            "sector",
            "label_end",
            "ret_total",
            "excess_univ_med",
            "excess_n500_pr",
            "labelled",
        ]
    ].copy()
    meta["anchor"] = pd.to_datetime(meta["anchor"])
    meta["label_end"] = pd.to_datetime(meta["label_end"])
    value_cols = [c for c in x.columns if not c.startswith(("isna_", "na_layout_"))]
    core = [n for n in reg.names(sets=["core"], source="features_fundamental.yaml")]
    return TrialMatrix(x, meta, y, value_cols, names, bcols, core, raw)
