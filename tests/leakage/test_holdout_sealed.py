"""T-26 skeleton (05 §8): sealed holdout anchors never reach a consumer before the family is unsealed.

The fit and calibrate entry points do not exist yet. E1/MS1 add them to ``FIT_ENTRY_POINTS``, and
``test_entry_points_never_see_sealed_anchors`` then monkeypatches each one to record the anchors it receives.
The P1 consumers (QA cross-check and bias block) are covered now through the loader audit.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from ideation.labels import loader
from ideation.labels.loader import SealedAnchorError, is_sealed, load_labels, unsealed_only

# "module:function" of every fit / calibrate entry point (E1, MS1, MS2)
FIT_ENTRY_POINTS: list[str] = [
    "ideation.bakeoff.data:load_panel",
    "ideation.bakeoff.run:run_wfo",
    "ideation.bakeoff.run:run_cpcv",
]

LABELS = pd.DataFrame(
    {
        "anchor": [date(2024, 6, 28), date(2025, 3, 28), date(2025, 6, 30), date(2025, 6, 30), date(2025, 7, 31)],
        "horizon": ["1Y", "1Y", "1Y", "3M", "3M"],
        "security_id": [1, 1, 1, 1, 1],
        "ret_total": [0.1, 0.2, 0.3, 0.4, 0.5],
    }
)


@pytest.mark.parametrize(
    ("anchor", "horizon", "sealed"),
    [
        (date(2025, 6, 30), "1Y", True),  # Jun-2025 (05 O2)
        (date(2025, 3, 28), "1Y", True),  # Mar-2025 (05 §8)
        (date(2025, 4, 30), "1Y", True),  # monthly 1Y overlapping the holdout year (stricter than 05)
        (date(2024, 6, 28), "1Y", False),  # last counted 1Y test
        (date(2025, 2, 28), "1Y", False),
        (date(2025, 6, 30), "3M", False),  # last counted tactical OOS anchor
        (date(2025, 7, 31), "3M", True),
        (date(2026, 7, 31), "3M", True),
        (date(2026, 9, 30), "1M", True),
        (date(2026, 8, 31), "3M", False),  # no 3M label exists that late anyway
    ],
)
def test_sealed_sets(anchor: date, horizon: str, sealed: bool) -> None:
    assert is_sealed(anchor, horizon) is sealed


def test_sealed_anchor_raises_until_unsealed() -> None:
    with pytest.raises(SealedAnchorError, match="1Y-june"):
        load_labels([date(2025, 6, 30)], "1Y", "fit", source=LABELS, unsealed=set())
    with pytest.raises(SealedAnchorError):
        load_labels([date(2024, 6, 28), date(2025, 3, 28)], "1Y", "calibrate", source=LABELS, unsealed={"tactical-3M"})
    out = load_labels([date(2025, 6, 30)], "1Y", "holdout_report", source=LABELS, unsealed={"1Y-june"})
    assert out["ret_total"].tolist() == [0.3]
    ok = load_labels([date(2024, 6, 28)], "1Y", "fit", source=LABELS, unsealed=set())
    assert ok["ret_total"].tolist() == [0.1]


def test_unseal_read_failure_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loader, "unsealed_families_from_db", lambda: set())
    with pytest.raises(SealedAnchorError):
        load_labels([date(2025, 7, 31)], "3M", "fit", source=LABELS)


def test_bad_purpose_and_family_rejected() -> None:
    with pytest.raises(ValueError, match="purpose"):
        load_labels([date(2024, 6, 28)], "1Y", "peek", source=LABELS, unsealed=set())
    with pytest.raises(ValueError, match="does not cover"):
        load_labels([date(2024, 6, 28)], "1Y", "fit", family="tactical-3M", source=LABELS, unsealed=set())


def test_unsealed_only_helper() -> None:
    anchors = [date(2023, 6, 30), date(2024, 6, 28), date(2025, 6, 30)]
    assert unsealed_only(anchors, "1Y") == [date(2023, 6, 30), date(2024, 6, 28)]


def test_audit_records_every_call() -> None:
    before = len(loader.AUDIT)
    load_labels([date(2024, 6, 28)], "1Y", "qa", source=LABELS, unsealed=set())
    assert len(loader.AUDIT) == before + 1
    assert loader.AUDIT[-1].purpose == "qa"
    assert loader.AUDIT[-1].anchors == (date(2024, 6, 28),)


@pytest.mark.parametrize("entry", FIT_ENTRY_POINTS or [pytest.param("none", marks=pytest.mark.skip("P4+ adds"))])
def test_entry_points_never_see_sealed_anchors(entry: str, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """T-26: run the MS1 driver on a synthetic source that CONTAINS 2025 anchors (labels, universe, features) and record
    every anchor that reaches a fit, a score call or the label loader; none may be sealed, and the sealed rows never
    enter the panel."""
    import importlib

    from ideation.bakeoff import data as bdata
    from ideation.bakeoff import run as brun
    from tests.leakage.test_ms1_bakeoff import REG, spec, synth_dir

    seen: list[pd.Timestamp] = []
    real_fit = brun._fit_predict

    def recording(ctx, train, test, **kw):
        seen.extend([*train, *test])
        return real_fit(ctx, train, test, **kw)

    monkeypatch.setattr(brun, "_fit_predict", recording)
    out = synth_dir(tmp_path)
    assert (pd.read_parquet(out / "labels.parquet")["anchor"].astype(str) >= "2025-03-01").any()  # sealed rows exist
    loader.AUDIT.clear()
    mod, fn = entry.split(":")
    assert callable(getattr(importlib.import_module(mod), fn))
    panel = bdata.load_panel(out, "snap", "june", REG, label_source=out / "labels.parquet")
    assert not any(is_sealed(a, "1Y") for a in panel.anchors)
    assert not any(is_sealed(a, "1Y") for a in pd.to_datetime(panel.frame["anchor"]).dt.date)
    ctx = brun.make_context(spec("RIDGE_GLOBAL"), panel, REG, tmp_path / "ck", {})
    if fn == "run_wfo":
        brun.run_wfo(ctx, importance=False)
    elif fn == "run_cpcv":
        brun.run_cpcv(ctx)
    assert all(not is_sealed(a.date(), "1Y") for a in seen)
    assert all(not loader.sealed_anchors(rec.anchors, rec.horizon) for rec in loader.AUDIT)
    assert loader.AUDIT and all(rec.purpose in ("fit", "calibrate", "select", "evaluate") for rec in loader.AUDIT)
    if fn != "load_panel":
        assert seen, "the entry point never fitted anything"
