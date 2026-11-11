# ruff: noqa: E501
"""PT3 acceptance on the real snapshot + extraction outputs: the future-poison test (T-01 applied to the annual-report
features and to the multi-call trends) and the availability invariants of the real annual-report table.
Needs ``ideation snapshot --full``, ``ideation text extract`` and ``ideation text annual``."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from ideation.config import REPO_ROOT
from ideation.features.annual_text import build_annual, load_params
from ideation.features.registry import load_registry
from ideation.features.text import build_text_trend, load_trend_params
from ideation.pit.contract import ANNUAL_DOCS_FILE, TEXT_DOCS_FILE, PitStore

pytestmark = pytest.mark.lake
REG = load_registry(REPO_ROOT / "config")


@pytest.fixture(scope="module")
def astore(settings) -> PitStore:
    for f in (ANNUAL_DOCS_FILE, TEXT_DOCS_FILE):
        if not (settings.data_dir / "text" / f).is_file():
            pytest.skip(f"no {f}; run `ideation text annual` / `ideation text extract`")
    base = PitStore.latest(settings.data_dir)
    out = settings.data_dir / "outputs" / base.snap_id
    if not (out / "universe_member.parquet").is_file():
        pytest.skip("no universe output")
    return PitStore(base.snapshot_dir, outputs_dir=out, text_dir=settings.data_dir / "text")


def _members(view) -> pd.DataFrame:
    u = view.universe()
    return u[u["traded_ok"] & u["company_id"].notna()]


@pytest.mark.parametrize("anchor", [date(2019, 6, 28), date(2022, 1, 31), date(2024, 6, 28)])
def test_annual_future_poison(astore: PitStore, anchor: date) -> None:  # T-25 for PT3
    par = load_params(REPO_ROOT / "config")
    clean = build_annual(astore.as_of(anchor), _members(astore.as_of(anchor)), REG, par)
    assert clean["isna_ar"].eq(0).sum() > 100  # not vacuous
    assert max(x for x in clean["ar_available_from"] if pd.notna(x)) <= anchor
    for mode in ("nan", "shuffle", "inflate"):
        v = astore.poison_after(anchor, mode).as_of(anchor)  # type: ignore[arg-type]
        pd.testing.assert_frame_equal(build_annual(v, _members(v), REG, par), clean, check_exact=True)
    early = astore.poison_after(anchor - timedelta(days=500), "inflate")  # positive control
    v = early.as_of(anchor)
    changed = build_annual(v, _members(v), REG, par)
    assert not changed.drop(columns=["anchor"]).equals(clean.drop(columns=["anchor"]))


@pytest.mark.parametrize("anchor", [date(2021, 6, 30), date(2024, 6, 28)])
def test_trend_future_poison(astore: PitStore, anchor: date) -> None:
    par = load_trend_params(REPO_ROOT / "config")
    clean = build_text_trend(astore.as_of(anchor), _members(astore.as_of(anchor)), REG, par)
    assert clean["isna_trend"].eq(0).sum() > 100
    for mode in ("nan", "shuffle", "inflate"):
        v = astore.poison_after(anchor, mode).as_of(anchor)  # type: ignore[arg-type]
        pd.testing.assert_frame_equal(build_text_trend(v, _members(v), REG, par), clean, check_exact=True)
    early = astore.poison_after(anchor - timedelta(days=400), "inflate")
    v = early.as_of(anchor)
    assert not build_text_trend(v, _members(v), REG, par).drop(columns=["anchor"]).equals(clean.drop(columns=["anchor"]))


def test_real_annual_availability_invariants(settings) -> None:
    p = settings.data_dir / "text" / ANNUAL_DOCS_FILE
    if not p.is_file():
        pytest.skip("no annual output")
    df = pd.read_parquet(p)
    ok = df[df["read_ok"] == True]  # noqa: E712
    fy, av = pd.to_datetime(ok["fy_end"]), pd.to_datetime(ok["available_from"])
    lag = (av - fy).dt.days
    assert (lag >= int(df["lag_days"].iloc[0])).all()  # never earlier than the fixed lag
    parsed = pd.to_datetime(ok["parsed_date"])
    m = parsed.notna()
    assert ((av[m] - parsed[m]).dt.days >= 1).all()  # never on or before a parsed filing / AGM date
    assert ok["available_from"].notna().all()
    lagged = ok["avail_basis"] == "lag"
    assert ((parsed[lagged] + timedelta(days=1)).isna() | ((parsed[lagged] + timedelta(days=1)) <= av[lagged])).all()
