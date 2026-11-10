"""T-01 on the P3 builders (technical, flow, event, regime columns) and the ear_3d poison test, on the synthetic
snapshot. The lake twin (tests/lake/test_p3_features.py) repeats T-01 on the real snapshot with the universe rebuilt
on the poisoned store."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ideation.config import REPO_ROOT
from ideation.features import events
from ideation.features.registry import load_registry
from ideation.features.tech_build import build_view
from ideation.pit.contract import PitStore
from ideation.regime.context import build_context
from tests.unit.p3_store import A, make_store

REG = load_registry(REPO_ROOT / "config")


@pytest.fixture(scope="module")
def store(tmp_path_factory: pytest.TempPathFactory) -> PitStore:
    return make_store(tmp_path_factory.mktemp("p3leak"))


@pytest.mark.parametrize("mode", ["nan", "shuffle", "inflate"])
@pytest.mark.parametrize("anchor", [date(2018, 12, 31), A])
def test_t01_future_poison_features(store: PitStore, mode: str, anchor: date) -> None:
    clean = build_view(store.as_of(anchor), REG, None)
    dirty = build_view(store.poison_after(anchor, mode).as_of(anchor), REG, None)  # type: ignore[arg-type]
    pd.testing.assert_frame_equal(clean.features, dirty.features, check_exact=True)
    assert clean.regime.keys() == dirty.regime.keys()
    assert all(
        (np.isnan(v) and np.isnan(dirty.regime[k])) or v == dirty.regime[k] for k, v in clean.regime.items()
    )
    pd.testing.assert_frame_equal(clean.rrg, dirty.rrg, check_exact=True)


def test_t01_positive_control_earlier_poison_changes_features(store: PitStore) -> None:
    clean = build_view(store.as_of(A), REG, None).features
    early = build_view(store.poison_after(date(2019, 3, 1), "inflate").as_of(A), REG, None).features
    assert not clean.equals(early)


def _members() -> pd.DataFrame:
    return pd.DataFrame({"security_id": [c * 10 for c in range(1, 41)]})


def test_ear_3d_uses_nothing_after_the_window(store: PitStore) -> None:
    """Poison everything that became available after s(e'+1): ear_3d of that event is unchanged, pead_drift is not."""
    view = store.as_of(A)
    ctx = build_context(view, _members())
    sel_all = events.select_events(ctx, view.earnings_dates())
    e_min = int(sel_all["e_idx"].min())
    sel = sel_all[sel_all["e_idx"] == e_min].reset_index(drop=True)
    assert len(sel) >= 4
    cap = pd.Timestamp(ctx.panel.sessions[e_min + 1]).date()
    base = events.event_returns(ctx, sel)
    for mode in ("nan", "shuffle", "inflate"):
        pv = store.poison_after(cap, mode).as_of(A)  # type: ignore[arg-type]
        pctx = build_context(pv, _members())
        got = events.event_returns(pctx, sel)
        np.testing.assert_allclose(got["ear_3d"].to_numpy(), base["ear_3d"].to_numpy(), rtol=0, atol=1e-12)
        assert not np.allclose(got["pead_drift"].fillna(0).to_numpy(), base["pead_drift"].fillna(0).to_numpy())
    # and moving the anchor forward (more data available) does not move ear_3d either
    later = store.calendar.good[store.calendar.good_index(A) + 40].astype(object)
    lv = store.as_of(later)
    lctx = build_context(lv, _members())
    lsel = events.select_events(lctx, lv.earnings_dates())
    lsel = lsel[lsel["event_date"].isin(sel["event_date"])]
    lret = events.event_returns(lctx, lsel)
    cmp = base.reindex(sel.set_index("row").index).loc[lsel["row"].to_numpy(), "ear_3d"].to_numpy()
    np.testing.assert_allclose(lret["ear_3d"].to_numpy(), cmp, rtol=0, atol=1e-12)


def test_event_rows_that_arrive_late_are_dropped(store: PitStore, tmp_path: Path) -> None:
    view = store.as_of(A)
    ctx = build_context(view, _members())
    ev = view.earnings_dates().copy()
    sel0 = events.select_events(ctx, ev)
    ev["available_from"] = ev["available_from"].map(lambda d: d.replace(day=min(d.day + 5, 28)))
    sel1 = events.select_events(ctx, ev[ev["available_from"] <= A])
    assert len(sel1) < len(sel0) or sel1.empty
