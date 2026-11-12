# ruff: noqa: E501
"""The lab panel: one frame of U_score members at MONTHLY anchors with fundamental (monthly rebuild), quality,
technical/regime columns and 1Y labels read only through ``load_labels(unsealed_only(...))``.

U_score here = ``universe_member.in_u_score`` AND the feature_complete hook (>= 0.6) computed from the monthly
fundamental rebuild, applied at every anchor (at June anchors it reproduces the legacy U_score; checked by a test
on real data in the run log). Counted 1Y anchors end at ``LAST_ANCHOR_1Y`` (2024-06-30): later anchors pass
``unsealed_only`` but their 12M labels run into the sealed 2025 return window, so they are excluded.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from ideation.features.registry import Registry
from ideation.lab.build_fund import lab_dir
from ideation.labels.loader import load_labels, unsealed_only

FIRST_TRAIN_ANCHOR = pd.Timestamp("2016-06-01")
LAST_ANCHOR_1Y = pd.Timestamp("2024-06-30")
HOOK = 0.6
FAMILY = "1Y-monthly-v2"
LABEL_COLS = ["label_end", "ret_total", "excess_univ_med", "excess_n500_pr", "exit_type"]
CONTROL_COLS = ["beta_252", "rvol_63", "idio_vol"]
UNI_COLS = ["anchor", "security_id", "in_u_score", "in_u1000", "adv63_lakh", "mcap_pit_cr", "listed_sessions"]


@dataclass
class LabPanel:
    frame: pd.DataFrame
    anchors: list[pd.Timestamp]
    label_end: pd.Series
    reg: Registry
    snap_id: str


def _norm(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s).dt.normalize()


def load_lab_panel(
    data_dir: Path,
    snap_id: str,
    reg: Registry,
    *,
    first: pd.Timestamp = FIRST_TRAIN_ANCHOR,
    last: pd.Timestamp = LAST_ANCHOR_1Y,
    horizon: str = "1Y",
    with_quality: bool = True,
) -> LabPanel:
    out = data_dir / "outputs" / snap_id
    ld = lab_dir(data_dir, snap_id)
    fund = pd.read_parquet(ld / "fund_monthly.parquet")
    fund["anchor"] = _norm(fund["anchor"])
    fund = fund[(fund["anchor"] >= first) & (fund["anchor"] <= last)]
    if (pd.to_datetime(fund["feature_available_from"]) > fund["anchor"]).any():
        raise ValueError("fundamental feature_available_from after its anchor")
    fund_cols = [n for n in reg.names(source="features_fundamental.yaml") if n in fund.columns]
    fund = fund[["anchor", "security_id", "company_id", "industry_group", "sector", "layout", "feature_complete",
                 "fy_end", *fund_cols]]  # fmt: skip
    uni = pd.read_parquet(out / "universe_member.parquet", columns=UNI_COLS)
    uni["anchor"] = _norm(uni["anchor"])
    uni = uni[(uni["anchor"] >= first) & (uni["anchor"] <= last)]
    tech = pd.read_parquet(out / "features_tech.parquet")
    tech["anchor"] = _norm(tech["anchor"])
    tech = tech[(tech["anchor"] >= first) & (tech["anchor"] <= last)]
    tcols = [n for n in reg.names(sets=["tech", "regime"]) if n in tech.columns]
    tcols = list(dict.fromkeys([*tcols, *CONTROL_COLS]))
    tech = tech[["anchor", "security_id", *tcols]]
    frame = fund.merge(uni, on=["anchor", "security_id"], how="inner", validate="1:1")
    frame = frame[frame["in_u_score"].astype(bool) & (frame["feature_complete"] >= HOOK)]
    frame = frame.merge(tech, on=["anchor", "security_id"], how="left", validate="1:1")
    if with_quality:
        q = pd.read_parquet(ld / "quality_monthly.parquet")
        q["anchor"] = _norm(q["anchor"])
        qcols = reg.names(source="features_quality.yaml")
        frame = frame.merge(q[["anchor", "security_id", *qcols]], on=["anchor", "security_id"], how="left", validate="1:1")
    for c in reg.broadcast_cols():
        if c in frame.columns:
            frame[c] = frame.groupby("anchor")[c].transform("first")
    anchors = sorted(frame["anchor"].unique())
    req = unsealed_only([a.date() for a in anchors], horizon)
    src = out / "labels.parquet"
    lab = load_labels(req, horizon, "fit", family=FAMILY, source=src)
    lab["anchor"] = _norm(lab["anchor"])
    lab = lab[["anchor", "security_id", *LABEL_COLS]]
    frame = frame.merge(lab, on=["anchor", "security_id"], how="left", validate="1:1")
    frame["label_end"] = pd.to_datetime(frame["label_end"])
    frame["ln_mcap"] = np.log(frame["mcap_pit_cr"].where(frame["mcap_pit_cr"] > 0))
    frame["ln_adv"] = np.log(frame["adv63_lakh"].where(frame["adv63_lakh"] > 0))
    frame = frame.sort_values(["anchor", "security_id"], kind="stable").reset_index(drop=True)
    # R1 runs on the whole universe at each anchor (labelled or not), so test inputs never depend on which names later
    # had a label; unlabelled rows are dropped only from training and metrics via ``labelled``.
    frame["labelled"] = frame["ret_total"].notna()
    label_end = frame.groupby("anchor")["label_end"].max()
    return LabPanel(frame, [pd.Timestamp(a) for a in label_end.index], label_end, reg, snap_id)
