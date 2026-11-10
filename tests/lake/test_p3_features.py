"""P3 acceptance on the real snapshot (01 P3, 02 T-01, 05 §10.3). Needs `ideation snapshot --full` and the universe /
features outputs for that snapshot (`ideation universe build`).

* Data hygiene the panel rule relies on: every price row and every applied factor is available on its own date.
* T-01 on technical, flow, event and regime columns: bit-identical when every row with available_from after the anchor
  is poisoned (three modes), with the universe REBUILT on the poisoned store; positive control included.
* ear_3d does not move when the anchor moves forward (nothing after s(e+1) enters it).
* Breadth at the COVID low, RRG table shape, trainable_from mask on a built real matrix.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from ideation.config import REPO_ROOT, load_methodology
from ideation.features import events
from ideation.features.registry import load_registry
from ideation.features.tech_build import build_anchor, expected_columns
from ideation.pit.contract import PitStore
from ideation.regime.context import build_context
from ideation.universe.build import UniverseConfig

REG = load_registry(REPO_ROOT / "config")
UCFG = UniverseConfig.from_methodology(load_methodology().data)


@pytest.fixture(scope="module")
def tstore(settings) -> PitStore:
    try:
        snap = PitStore.latest(settings.data_dir)
    except FileNotFoundError as exc:
        pytest.skip(str(exc))
    out = settings.data_dir / "outputs" / snap.snap_id
    if not (out / "universe_member.parquet").is_file():
        pytest.skip("no stored universe for the latest snapshot")
    return PitStore(snap.snapshot_dir, outputs_dir=out)


def test_prices_and_factors_are_available_on_their_own_date(tstore: PitStore) -> None:
    p = tstore.arrow("prices")
    d = p["date"].to_pandas()
    a = p["available_from"].to_pandas()
    assert int((a != d).sum()) == 0
    f = tstore.table("factors")
    f = f[f["apply"].astype(bool)]
    assert int((f["available_from"] > f["eff_date"]).sum()) == 0


@pytest.mark.parametrize("mode", ["nan", "shuffle", "inflate"])
@pytest.mark.parametrize("anchor", [date(2019, 6, 28), date(2023, 6, 30)])
def test_t01_future_poison_tactical_features(tstore: PitStore, anchor: date, mode: str) -> None:
    clean = build_anchor(tstore, REG, UCFG, anchor, rebuild_universe=True)
    dirty = build_anchor(tstore.poison_after(anchor, mode), REG, UCFG, anchor, rebuild_universe=True)  # type: ignore[arg-type]
    pd.testing.assert_frame_equal(clean.features, dirty.features, check_exact=True)
    pd.testing.assert_frame_equal(clean.rrg, dirty.rrg, check_exact=True)
    assert len(clean.features) > 1000
    assert clean.features["ear_3d"].notna().sum() > 50


def test_t01_positive_control(tstore: PitStore) -> None:
    a = date(2023, 6, 30)
    clean = build_anchor(tstore, REG, UCFG, a).features
    early = build_anchor(tstore.poison_after(date(2022, 6, 30), "inflate"), REG, UCFG, a).features  # type: ignore[arg-type]
    assert not clean.equals(early)


def test_ear_3d_is_stable_when_the_anchor_moves(tstore: PitStore) -> None:
    a0 = date(2023, 6, 30)
    cal = tstore.calendar
    a1 = cal.good[cal.good_index(a0) + 15].astype(object)
    uni = tstore.as_of(a0).universe()  # the same securities at both anchors (a1 is not a stored universe anchor)
    mem = uni[uni["traded_ok"]][["security_id"]].reset_index(drop=True)
    out = []
    for a in (a0, a1):
        view = tstore.as_of(a)
        ctx = build_context(view, mem)
        sel = events.select_events(ctx, view.earnings_dates())
        res = events.event_returns(ctx, sel)
        sel = sel.assign(sid=ctx.members["security_id"].to_numpy()[sel["row"].to_numpy()])
        out.append(sel.assign(ear=res["ear_3d"].reindex(sel["row"]).to_numpy())[["sid", "event_date", "ear"]])
    m = out[0].merge(out[1], on=["sid", "event_date"])
    assert len(m) > 100
    a, b = m["ear_x"].to_numpy(), m["ear_y"].to_numpy()
    ok = ~np.isnan(a) & ~np.isnan(b)
    assert ok.sum() > 100
    assert np.max(np.abs(a[ok] - b[ok])) < 1e-9


def test_regime_at_covid_low_and_rrg_table(tstore: PitStore) -> None:
    r = build_anchor(tstore, REG, UCFG, date(2020, 3, 31))
    assert r.regime["pct_above_200dma"] < 15.0
    assert r.regime["pct_above_50dma"] < 15.0
    assert r.labels["breadth"] == "Narrow" and r.labels["trend"] == "Down"
    t = r.rrg[r.rrg["group_type"] == "industry_group"]
    assert len(t) == 22 and t["quadrant"].notna().all()
    latest = build_anchor(tstore, REG, UCFG, tstore.calendar.good[-1].astype(object))
    idx = latest.rrg[latest.rrg["group_type"] == "index"]["group_code"]
    assert not {"^CNXCMDT", "^CNXMEDIA", "^CNXSERVICE"} & set(idx)  # start 2026-10: too short, excluded


def test_trainable_mask_on_real_matrix(tstore: PitStore) -> None:
    a = date(2019, 2, 28)
    f = build_anchor(tstore, REG, UCFG, a).features
    for name, tf in REG.trainable_from().items():
        if a < tf:
            assert f[name].isna().all(), name
    assert set(expected_columns(REG)) <= set(f.columns)
    assert f["mom_12_1"].notna().mean() > 0.8
