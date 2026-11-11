"""Outcomes on the real snapshot: future-poison (T-01 applied to the outcome inputs) and availability pins."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from ideation.outcomes import analyst, fundamental
from ideation.pit.contract import PitStore

pytestmark = pytest.mark.lake
A = date(2021, 6, 30)


@pytest.fixture(scope="module")
def store(settings) -> PitStore:
    return PitStore.latest(settings.data_dir)


def _members(late) -> pd.DataFrame:
    c = late.company()["company_id"]
    return pd.DataFrame({"security_id": c, "company_id": c})


def test_vendor_rows_are_available_event_date_plus_one(store: PitStore) -> None:
    ev = analyst.load_events(store.as_of(store.calendar.good[-1].astype(object), record=False))
    assert ev.attrs["n_dropped_availability_mismatch"] == 0 and len(ev) > 10_000


@pytest.mark.parametrize("mode", ["nan", "shuffle", "inflate"])
def test_analyst_outcome_future_poison(store: PitStore, mode: str) -> None:
    last = store.calendar.good[-1].astype(object)
    mem = _members(store.as_of(last, record=False))
    clean = {h: analyst.build_analyst(analyst.load_events(store.as_of(last, record=False)), mem, A, h, last)
             for h in ("1Y", "3M")}  # fmt: skip
    assert len(clean["1Y"]) > 100 and len(clean["3M"]) > 100
    for h, months in (("1Y", 12), ("3M", 3)):
        d = (pd.Timestamp(A) + pd.DateOffset(months=months) + pd.Timedelta(days=1)).date()
        p = store.poison_after(d, mode)  # type: ignore[arg-type]
        got = analyst.build_analyst(analyst.load_events(p.as_of(last, record=False)), mem, A, h, last)
        pd.testing.assert_frame_equal(got, clean[h], check_exact=True)
    early = store.poison_after(A, "inflate")
    got = analyst.build_analyst(analyst.load_events(early.as_of(last, record=False)), mem, A, "1Y", last)
    assert not got.drop(columns="anchor").equals(clean["1Y"].drop(columns="anchor"))


@pytest.mark.parametrize("mode", ["nan", "shuffle", "inflate"])
def test_fundamental_outcome_future_poison(store: PitStore, mode: str) -> None:
    last = store.calendar.good[-1].astype(object)
    late = store.as_of(last, record=False)
    mem = _members(late)
    clean = fundamental.build_fundamental(fundamental.build_panel(late, "A"), mem, A)
    assert len(clean) > 500
    d = (pd.Timestamp(A) + pd.DateOffset(months=12) + pd.Timedelta(days=60)).date()
    pl = store.poison_after(d, mode).as_of(last, record=False)  # type: ignore[arg-type]
    got = fundamental.build_fundamental(fundamental.build_panel(pl, "A"), mem, A)
    pd.testing.assert_frame_equal(got, clean, check_exact=True)
    pe = store.poison_after(A, "inflate").as_of(last, record=False)
    got = fundamental.build_fundamental(fundamental.build_panel(pe, "A"), mem, A)
    assert not got.drop(columns="extra").equals(clean.drop(columns="extra"))
