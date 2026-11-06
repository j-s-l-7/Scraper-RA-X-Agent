"""PitStore / PitView contract (02 §1.1, S5): guard, read-only frames, manifest records, poison_after semantics,
basis-conversion exemption, vendor gate (05 O5), quarterly ban before 2023-06-30."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from ideation.pit.contract import LeakageError, PitStore, VendorGateError, frame_hash

DAYS = [d.date() for d in pd.bdate_range("2023-01-02", "2023-12-29")]
A = date(2023, 6, 30)


def _w(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), path)


@pytest.fixture
def store(tmp_path: Path) -> PitStore:
    cal = pd.DataFrame({"session": DAYS, "in_bhav": True, "in_index": True, "bhav_missing": False, "n_eq": 1})
    _w(tmp_path / "derived" / "calendar.parquet", cal.assign(available_from=cal["session"], role="data"))
    px = pd.DataFrame({"security_id": 1, "date": DAYS, "close": np.arange(len(DAYS), dtype=float) + 100,
                       "series": "EQ", "symbol": "X", "turnover_lacs": 5.0})  # fmt: skip
    _w(tmp_path / "derived" / "prices.parquet", px.assign(available_from=px["date"], role="data"))
    fac = pd.DataFrame(
        {"security_id": [1, 1], "ex_date": [date(2023, 3, 1), date(2023, 9, 1)],
         "eff_date": [date(2023, 3, 1), date(2023, 9, 1)], "factor": [0.5, 0.5], "kind": "split", "source": "x",
         "flag": "", "apply": True, "available_from": [date(2023, 3, 1), date(2023, 9, 1)], "role": "basis_conversion"}
    )  # fmt: skip
    _w(tmp_path / "derived" / "factors.parquet", fac)
    ff = pd.DataFrame(
        {"company_id": [1, 1, 1], "basis": "S", "metric_id": [21, 21, 21], "period_type": ["A", "A", "Q"],
         "period_end": [date(2022, 3, 31), date(2023, 3, 31), date(2023, 3, 31)], "value": [10.0, 20.0, 5.0],
         "available_from": [date(2022, 5, 31), date(2023, 7, 15), date(2023, 5, 31)], "role": "data"}
    )  # fmt: skip
    _w(tmp_path / "financial_fact.parquet", ff)
    ev = pd.DataFrame(
        {"company_id": [1], "event_date": [date(2023, 5, 1)], "eps_estimate": [1.0], "eps_reported": [1.2]}
    )
    _w(
        tmp_path / "earnings_vendor.parquet",
        ev.assign(available_from=ev["event_date"] + timedelta(days=1), role="vendor"),
    )
    (tmp_path / "manifest.json").write_text(json.dumps({"snap_id": "t", "observation_date": "2023-12-29"}))
    return PitStore(tmp_path)


def test_accessors_respect_anchor_and_record_hashes(store: PitStore) -> None:
    v = store.as_of(A)
    p = v.prices_raw(5)
    assert p["date"].max() == A
    assert len(p) == 5
    f = v.fundamentals("A")
    assert f["period_end"].tolist() == [date(2022, 3, 31)]  # FY2023 not available until 2023-07-15
    assert [r.item for r in v.records] == ["prices_raw[5]", "fundamentals[A|None]"]
    assert all(r.max_available_from <= A for r in v.records)
    assert v.records[0].sha256 == frame_hash(p)


def test_frames_are_read_only(store: PitStore) -> None:
    v = store.as_of(A)
    p = v.prices_raw(5)
    with pytest.raises(ValueError, match="read-only"):
        p["close"].to_numpy()[0] = -1
    p.loc[0, "close"] = -1.0  # copy-on-write: the store is untouched
    assert store.as_of(A).prices_raw(5)["close"].min() > 0


def test_guard_raises_on_future_rows(store: PitStore) -> None:
    v = store.as_of(A)
    leaky = store.table("prices")
    with pytest.raises(LeakageError, match="available_from"):
        v._guard(leaky, "planted")


def test_anchor_must_be_a_session(store: PitStore) -> None:
    with pytest.raises(LeakageError, match="good NSE session"):
        store.as_of(date(2023, 7, 1))  # Saturday


def test_quarterly_ban_before_2023_06_30(store: PitStore) -> None:
    with pytest.raises(LeakageError, match="quarterly"):
        store.as_of(date(2023, 6, 29)).fundamentals("Q")
    assert len(store.as_of(A).fundamentals("Q")) == 1
    with pytest.raises(LeakageError):
        store.as_of(A).fundamentals("H")


def test_basis_conversion_exemption(store: PitStore) -> None:
    v = store.as_of(A)
    assert v.factors(["split"])["eff_date"].tolist() == [date(2023, 3, 1)]
    b = v.basis_conversion_factors()
    assert b["eff_date"].tolist() == [date(2023, 9, 1)]
    assert (b["role"] == "basis_conversion").all()
    adj = v.prices_adj(len(DAYS[: DAYS.index(A) + 1]), kinds=("split",))
    pre = adj[adj["date"] < date(2023, 3, 1)]
    assert np.allclose(pre["close_adj"], pre["close"] * 0.5)  # only the March split (T-20 in miniature)


def test_vendor_gate(store: PitStore) -> None:
    v = store.as_of(A)
    with pytest.raises(VendorGateError):
        v.earnings_vendor()
    ns: dict[str, object] = {"__name__": "ideation.outcomes.analyst", "v": v}
    exec("out = v.earnings_vendor()", ns)  # noqa: S102 - simulates a call from the allow-listed module
    assert len(ns["out"]) == 1  # type: ignore[arg-type]


def test_poison_after_exempts_roles_and_appends_fakes(store: PitStore) -> None:
    bad = store.poison_after(A, "inflate", seed=1)
    clean_v, bad_v = store.as_of(A, record=False), bad.as_of(A, record=False)
    pd.testing.assert_frame_equal(clean_v.prices_raw(20), bad_v.prices_raw(20), check_exact=True)
    pd.testing.assert_frame_equal(clean_v.fundamentals("A"), bad_v.fundamentals("A"), check_exact=True)
    pd.testing.assert_frame_equal(clean_v.basis_conversion_factors(), bad_v.basis_conversion_factors())
    full = bad.table("prices")
    after = full[full["available_from"] > A]
    assert (after["close"] != store.table("prices").set_index("date").reindex(after["date"])["close"].to_numpy()).any()
    assert (full["date"] <= A).sum() > (store.table("prices")["date"] <= A).sum()  # fakes dated before A
