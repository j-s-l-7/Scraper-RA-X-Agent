"""Quality / investment builder on the synthetic PIT snapshot of test_features_fundamental: values, link guards,
filing-lag availability and the T-01 future poison (all three modes) with a positive control."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from ideation.features.quality import SOURCE, build_quality
from tests.unit.test_features_fundamental import (
    IDS,
    REG,
    A,
    _w,
    facts,
    members,
    store,  # noqa: F401  (fixture)
)

QFEATS = REG.names(source=SOURCE)


@pytest.fixture
def qstore(store):  # noqa: F811
    """The base store plus Material / Manufacturing cost lines for companies 1 (consolidated) and 5."""
    ff = pd.read_parquet(store.snapshot_dir / "financial_fact.parquet")
    rows = []
    for cid, basis in ((1, "C"), (5, "S")):
        rows += facts(cid, [2015, 2016, 2017, 2018, 2019], basis, mat_pct=40.0, mfg_pct=10.0)
    extra = pd.DataFrame(rows)
    extra = extra[extra["metric_id"].isin([IDS["mat_pct"], IDS["mfg_pct"]])]
    _w(store.snapshot_dir / "financial_fact.parquet", pd.concat([ff, extra], ignore_index=True))
    return type(store)(store.snapshot_dir)


def bq(st, anchor: date = A) -> pd.DataFrame:
    return build_quality(st.as_of(anchor), members(), REG, IDS).set_index("security_id")


def test_registry_quality_set_is_isolated() -> None:
    assert set(QFEATS) == {"gp_ta", "accruals", "asset_gr", "piotroski"}
    assert all(REG.features[n].set == "quality" and not REG.features[n].complete for n in QFEATS)
    assert not set(QFEATS) & set(REG.names(sets=["core"]))


def test_values_on_consolidated_company(qstore) -> None:
    q = bq(qstore)
    # company 1 (security 10): FY2019 consolidated, sales 500, np 50, cfo 75, TA 1500 (FY2018 1200)
    avg_ta = (1500 + 1200) / 2
    assert q.loc[10, "gp_ta"] == pytest.approx(500 * (1 - 0.5) / avg_ta)
    assert q.loc[10, "accruals"] == pytest.approx((50 - 75) / avg_ta)
    assert q.loc[10, "asset_gr"] == pytest.approx(1500 / 1200 - 1)
    assert 0 <= q.loc[10, "piotroski"] <= 8


def test_missing_cost_lines_or_history_give_nan_not_fill(qstore) -> None:
    q = bq(qstore)
    assert np.isnan(q.loc[20, "gp_ta"])  # company 2 has no cost lines in the fixture
    assert np.isnan(q.loc[80, "asset_gr"]) and np.isnan(q.loc[80, "piotroski"])  # one FY only: no previous year
    assert np.isnan(q.loc[30, "asset_gr"])  # company 3: FY2018 missing, FY2019 vs FY2017 is not a 12-month link
    assert np.isnan(q.loc[30, "piotroski"])


def test_stale_fy_gives_no_features(qstore) -> None:
    q = bq(qstore, date(2021, 12, 31))
    assert q[QFEATS].isna().all().all()


def test_late_filing_is_not_visible_before_availability(qstore) -> None:
    # company 5: FY2019 filed 2019-07-15; at the June anchor the latest visible FY is 2018
    early = bq(qstore, date(2019, 6, 28)).loc[50]
    late = bq(qstore, date(2019, 7, 15)).loc[50]
    assert early["q_fy_end"] == date(2018, 3, 31) and late["q_fy_end"] == date(2019, 3, 31)


@pytest.mark.parametrize("mode", ["nan", "shuffle", "inflate"])
def test_t01_future_poison(qstore, mode: str) -> None:
    clean = build_quality(qstore.as_of(A), members(), REG, IDS)
    bad_store = qstore.poison_after(A, mode, seed=3)  # type: ignore[arg-type]
    assert (bad_store.table("financial_fact")["available_from"] > A).any()
    bad = build_quality(bad_store.as_of(A), members(), REG, IDS)
    pd.testing.assert_frame_equal(clean, bad, check_exact=True)
    old = qstore.poison_after(date(2018, 1, 1), "inflate", seed=3)
    assert not build_quality(old.as_of(A), members(), REG, IDS).equals(clean)  # positive control
