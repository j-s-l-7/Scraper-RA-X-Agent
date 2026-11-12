# ruff: noqa: E501
"""Accuracy lab on real data (needs the monthly rebuild under IDEATION_DATA_DIR/lab/<snap>/): the harness reproduces
the legacy fixed-weight numbers, never touches sealed anchors, and the monthly rebuild equals the legacy June features."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ideation.config import get_settings
from ideation.labels import loader

pytestmark = pytest.mark.lake


def _snap() -> tuple[Path, str]:
    st = get_settings()
    snaps = sorted(p.name for p in (st.data_dir / "outputs").iterdir() if (p / "features_fundamental.parquet").exists())
    snap = snaps[-1]
    if not (st.data_dir / "lab" / snap / "quality_monthly.parquet").exists():
        pytest.skip("run `ideation lab build` first")
    return st.data_dir, snap


@pytest.fixture(scope="module")
def lr():
    from ideation.lab.run import LabRun

    d, snap = _snap()
    loader.AUDIT.clear()
    return LabRun(d, snap, log=lambda m: None)


def test_labels_only_unsealed_anchors_were_requested(lr) -> None:
    for rec in loader.AUDIT:
        assert not loader.sealed_anchors(rec.anchors, rec.horizon)
    assert max(lr.panel.anchors) <= pd.Timestamp("2024-06-30")


def test_june_anchors_reproduce_legacy_universe_and_fixed_ics(lr) -> None:
    from ideation.lab.stats import ic_by_anchor

    fr = lr.panel.frame
    assert (fr[fr["anchor"] == "2016-06-30"].shape[0]) == 827
    out = {}
    for n in ("P1_UNIFIED_XS", "VALUE_ONLY", "P1_COMP_EW"):
        s = lr.fixed_scores(n).merge(fr[["anchor", "security_id", "ret_total", "labelled"]], on=["anchor", "security_id"])
        ic = ic_by_anchor(s[s["labelled"]])
        june = ic[[a.month == 6 for a in ic.index]]
        out[n] = (june[june.index.year >= 2020].mean(), june.mean())
    assert out["P1_UNIFIED_XS"][0] == pytest.approx(0.121, abs=0.002)
    assert out["VALUE_ONLY"][0] == pytest.approx(0.129, abs=0.002)
    assert out["P1_COMP_EW"][0] == pytest.approx(0.029, abs=0.002)
    assert out["P1_UNIFIED_XS"][1] == pytest.approx(0.069, abs=0.003)  # audit: nine anchors


def test_monthly_fundamental_rebuild_equals_legacy_june_features(lr) -> None:
    d, snap = lr.data_dir, lr.snap
    new = pd.read_parquet(d / "lab" / snap / "fund_monthly.parquet")
    old = pd.read_parquet(d / "outputs" / snap / "features_fundamental.parquet")
    m = new.merge(old, on=["anchor", "security_id"], suffixes=("", "_o"))
    assert len(m) == len(old)
    for c in ("ep", "roe", "sp", "feature_complete"):
        assert np.allclose(m[c].fillna(-9), m[f"{c}_o"].fillna(-9))


def test_quality_features_are_available_before_anchor(lr) -> None:
    q = pd.read_parquet(lr.data_dir / "lab" / lr.snap / "quality_monthly.parquet")
    fy = pd.to_datetime(q["q_fy_end"])
    ok = fy.notna()
    assert ok.mean() > 0.5
    assert (fy[ok] <= pd.to_datetime(q["anchor"])[ok]).all()
