"""PT2 acceptance on the real snapshot + PT1 text output: T-25 ``test_text_future_poison`` (T-01 applied to the text
features) and the vendor validity gate (05 §6.3). Needs ``ideation snapshot --full`` and ``ideation text extract``."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest
import yaml

from ideation.config import REPO_ROOT
from ideation.features import events_vendor as ev
from ideation.features.registry import load_registry
from ideation.features.text import build_text, load_params
from ideation.pit.contract import TEXT_DOCS_FILE, PitStore

pytestmark = pytest.mark.lake
REG = load_registry(REPO_ROOT / "config")


@pytest.fixture(scope="module")
def tstore(settings) -> PitStore:
    if not (settings.data_dir / "text" / TEXT_DOCS_FILE).is_file():
        pytest.skip("no PT1 output; run `ideation text extract`")
    base = PitStore.latest(settings.data_dir)
    out = settings.data_dir / "outputs" / base.snap_id
    if not (out / "universe_member.parquet").is_file():
        pytest.skip("no universe output")
    return PitStore(base.snapshot_dir, outputs_dir=out, text_dir=settings.data_dir / "text")


def _members(view) -> pd.DataFrame:
    u = view.universe()
    return u[u["traded_ok"] & u["company_id"].notna()]


@pytest.mark.parametrize("anchor", [date(2019, 6, 28), date(2022, 1, 31), date(2024, 6, 28)])
def test_text_future_poison(tstore: PitStore, anchor: date) -> None:  # T-25
    par = load_params(REPO_ROOT / "config")
    clean = build_text(tstore.as_of(anchor), _members(tstore.as_of(anchor)), REG, par)
    assert clean["isna_text"].eq(0).sum() > 100  # the test is not vacuous
    assert max(x for x in clean["text_available_from"] if x is not None) <= anchor
    for mode in ("nan", "shuffle", "inflate"):
        pois = tstore.poison_after(anchor, mode)  # type: ignore[arg-type]
        v = pois.as_of(anchor)
        got = build_text(v, _members(v), REG, par)
        pd.testing.assert_frame_equal(got, clean, check_exact=True)
    # positive control: poisoning a year earlier must change the features (the poison really reaches the dataset)
    early = tstore.poison_after(anchor - timedelta(days=400), "inflate")
    v = early.as_of(anchor)
    changed = build_text(v, _members(v), REG, par)
    assert not changed.drop(columns=["anchor"]).equals(clean.drop(columns=["anchor"]))


def test_vendor_validity_gate_passes(settings) -> None:
    store = PitStore.latest(settings.data_dir)
    par = yaml.safe_load((settings.config_dir / "features_vendor.yaml").read_text())["params"]
    view = store.as_of(store.calendar.good[-1].astype(object), record=False)
    res = ev.validity_gate(view, par)
    assert res["n_events"] > 10_000 and res["n_quarters"] >= 40
    assert res["rho"] > 0 and res["t_clustered"] > 3 and res["passed"]
    assert str(res["gate_to"]) == "2025-06-30"  # the sealed tactical window is never read


def test_vendor_features_future_poison(settings) -> None:
    store = PitStore.latest(settings.data_dir, outputs_dir=None)
    par = yaml.safe_load((settings.config_dir / "features_vendor.yaml").read_text())["params"]
    anchor = date(2022, 6, 30)
    v = store.as_of(anchor)
    comp = v.company()
    m = pd.DataFrame({"security_id": comp["company_id"], "company_id": comp["company_id"]})
    clean = ev.build_vendor(v, m, REG, par, allow_vendor_estimates=True)
    assert clean["isna_vsurp"].eq(0).sum() > 100
    for mode in ("nan", "shuffle", "inflate"):
        got = ev.build_vendor(store.poison_after(anchor, mode).as_of(anchor), m, REG, par, allow_vendor_estimates=True)  # type: ignore[arg-type]
        pd.testing.assert_frame_equal(got, clean, check_exact=True)
