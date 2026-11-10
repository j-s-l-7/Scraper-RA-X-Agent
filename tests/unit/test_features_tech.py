"""P3 builders on the synthetic snapshot: panel rules, hand-computed technical / event / flow values, macro lags,
registry, trainable_from mask, RRG quadrants."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest

from ideation.config import REPO_ROOT
from ideation.features import events, flows
from ideation.features.registry import load_registry
from ideation.features.tech_build import build_view, expected_columns
from ideation.pit.contract import PitStore
from ideation.regime.context import build_context
from ideation.regime.macro import CPI, IIP, STALE_DAYS, latest_monthly, macro_features
from ideation.regime.regime import REGIME_NAMES, regime_labels
from ideation.regime.rrg import QUAD_NAMES, RrgParams, quadrant, rrg_series, weekly_points
from tests.unit.p3_store import A, make_store

REG = load_registry(REPO_ROOT / "config")


@pytest.fixture(scope="module")
def store(tmp_path_factory: pytest.TempPathFactory) -> PitStore:
    return make_store(tmp_path_factory.mktemp("p3"))


@pytest.fixture(scope="module")
def built(store: PitStore):
    return build_view(store.as_of(A), REG, None)


def closes(store: PitStore) -> pd.DataFrame:
    p = pd.read_parquet(store.snapshot_dir / "derived" / "prices.parquet")
    return p.pivot(index="date", columns="security_id", values="close")


def test_columns_match_registry(built) -> None:
    f = built.features
    assert set(expected_columns(REG)) <= set(f.columns)
    assert set(f.columns) - set(expected_columns(REG)) == {
        "anchor", "security_id", "company_id", "industry_group", "sector", "in_u_score", "in_u_score_t"}  # fmt: skip
    assert f["security_id"].is_monotonic_increasing and len(f) == 40


def test_registry_sets_do_not_touch_p2_sets() -> None:
    p3 = set(expected_columns(REG))
    assert p3.isdisjoint(REG.names(sets=["core", "ext", "shown"]))
    bc = REG.broadcast_cols()
    assert bc == {f"regime_{n}" for n in REGIME_NAMES}
    assert all(REG.features[n].set == "regime" for n in bc)
    assert all(REG.features[n].trainable_from is not None for n in p3)


def test_broadcast_columns_are_constant_within_anchor(built) -> None:
    f = built.features
    for c in REG.broadcast_cols():
        assert f[c].nunique(dropna=False) == 1, c


def test_momentum_and_distance_hand_computed(store: PitStore, built) -> None:
    px = closes(store).loc[:A]
    f = built.features.set_index("security_id")
    s = 30
    assert f.loc[s, "mom_3m"] == pytest.approx(px[s].iloc[-1] / px[s].iloc[-64] - 1)
    assert f.loc[s, "mom_12_1"] == pytest.approx(px[s].iloc[-22] / px[s].iloc[-253] - 1)
    assert f.loc[s, "rev_5d"] == pytest.approx(px[s].iloc[-1] / px[s].iloc[-6] - 1)
    assert f.loc[s, "dist_52wh"] == pytest.approx(px[s].iloc[-1] / px[s].iloc[-252:].max() - 1)
    assert f.loc[s, "ma200_gap"] == pytest.approx(px[s].iloc[-1] / px[s].iloc[-200:].mean() - 1)
    r = px[s].pct_change().iloc[-63:]
    assert f.loc[s, "rvol_63"] == pytest.approx(r.std() * np.sqrt(252))
    assert f.loc[s, "max_21"] == pytest.approx(r.iloc[-21:].max())


def test_split_is_adjusted_not_a_return(store: PitStore, built) -> None:
    px = closes(store).loc[:A]
    f = built.features.set_index("security_id")
    # security 10 halves its raw close on 2018-06-15: the 1y window contains the split ... 12-1 return spans it
    raw = px[10].iloc[-22] / px[10].iloc[-253] - 1
    adj_idx = px[10].iloc[-22] / (px[10].iloc[-253] * 0.5) - 1
    assert f.loc[10, "mom_12_1"] == pytest.approx(raw if px.index[-253] >= date(2018, 6, 15) else adj_idx)
    assert f.loc[10, "max_21"] < 0.5


def test_rs_group_uses_ew_composite(store: PitStore, built) -> None:
    px = closes(store).loc[:A]
    ret = px.pct_change()
    f = built.features.set_index("security_id")
    members = [c * 10 for c in range(1, 41) if (c - 1) % 4 == 0 and c * 10 != 10]  # Capital Goods, split name excluded
    g = ret[members].iloc[-63:].mean(axis=1)
    comp = (1 + g).prod() - 1
    stock = 50  # company 5 -> group index (5-1)%4 = 0
    mine = px[stock].iloc[-1] / px[stock].iloc[-64] - 1
    # composite in the panel also contains security 10 (adjusted); allow the small difference of one of 10 members
    assert f.loc[stock, "rs_grp_3m"] == pytest.approx(mine - comp, abs=0.05)


def test_event_features_hand_computed(store: PitStore, built) -> None:
    f = built.features.set_index("security_id")
    px = closes(store)
    ret = px.pct_change()
    c = 3
    e = date(2019, 5, 8) + timedelta(days=c % 5)  # latest reported event of company 3
    sess = list(px.index)
    i = next(k for k, d in enumerate(sess) if d >= e)
    win = sess[i - 1 : i + 2]
    sec = [x * 10 for x in range(1, 41) if (x - 1) % 4 == (c - 1) % 4 and x * 10 != 10]
    comp = np.log1p(ret.loc[win, sec].mean(axis=1)).sum()
    stock = np.log(px.loc[sess[i + 1], c * 10] / px.loc[sess[i - 2], c * 10])
    assert f.loc[c * 10, "ear_3d"] == pytest.approx(stock - comp, abs=0.02)
    assert f.loc[c * 10, "ear_days"] == sess.index(A) - i
    assert f.loc[c * 10, "isna_event"] == 0.0


def test_event_selection_requires_complete_window(store: PitStore) -> None:
    c = 4
    e = date(2019, 5, 8) + timedelta(days=c % 5)
    # anchor = the event's first session: s(e'+1) is after the anchor, so the May event is not usable yet
    first = next(d for d in store.calendar.good.astype(object) if d >= e)
    view = store.as_of(first)
    ctx = build_context(view, pd.DataFrame({"security_id": [c * 10]}))
    sel = events.select_events(ctx, view.earnings_dates())
    assert sel.empty or sel["event_date"].max() < e
    nxt = store.calendar.good[store.calendar.good_index(first) + 1].astype(object)
    ctx2 = build_context(store.as_of(nxt), pd.DataFrame({"security_id": [c * 10]}))
    sel2 = events.select_events(ctx2, store.as_of(nxt).earnings_dates())
    assert list(sel2["event_date"]) == [e]


def test_scheduled_events_without_eps_are_ignored(built) -> None:
    f = built.features
    assert (f["ear_days"].dropna() <= 120).all() and f["ear_3d"].notna().all()


def test_ownership_uses_yearly_pairs_only(store: PitStore) -> None:
    own = flows.ownership_by_company(store.as_of(A))
    c = 3
    assert own.loc[c, "prom_chg_1y"] == pytest.approx((50 + 9 + c % 3) - (50 + 4 + c % 3))
    assert own.loc[c, "fii_chg_1y"] == pytest.approx(0.5)
    assert own.loc[c, "holders_chg_1y"] == pytest.approx(np.log(1.3 / 1.2))
    # before the 2019-03-31 pattern is available (2019-04-22) the previous pair is used
    early = flows.ownership_by_company(store.as_of(date(2019, 4, 15)))
    assert early.loc[c, "prom_chg_1y"] == pytest.approx((50 + 4 + c % 3) - (50 + 1 + c % 3))


def test_fpi_flow_maps_groups(store: PitStore) -> None:
    s = flows.fpi_flow_by_group(store.as_of(A))
    assert set(s.index) == {"Capital Goods", "Chemicals"}
    assert 0.04 <= s["Capital Goods"] <= 0.07


def test_cpi_visible_only_after_release(store: PitStore) -> None:
    before = latest_monthly(store.as_of(date(2019, 4, 12)), CPI)
    after = latest_monthly(store.as_of(date(2019, 4, 15)), CPI)
    assert before.period == date(2019, 2, 1) and after.period == date(2019, 3, 1)
    iip = latest_monthly(store.as_of(date(2019, 5, 16)), IIP)  # IIP Mar: month end + 45 d = 2019-05-15
    assert iip.period == date(2019, 3, 1)
    assert latest_monthly(store.as_of(date(2019, 5, 14)), IIP).period == date(2019, 2, 1)


def test_stale_monthly_print_is_nan(store: PitStore, monkeypatch: pytest.MonkeyPatch) -> None:
    assert latest_monthly(store.as_of(A), CPI).value > 0  # released 2019-06-15, 13 days old
    monkeypatch.setitem(STALE_DAYS, CPI, 10)
    assert np.isnan(latest_monthly(store.as_of(A), CPI).value)


def _with_series(store: PitStore, mutate) -> PitStore:
    t = store.table("series_daily").copy()
    mutate(t)
    return PitStore(store.snapshot_dir, outputs_dir=store.outputs_dir, _tables={"series_daily": t})


def _macro(store: PitStore) -> dict[str, float]:
    view = store.as_of(A)
    return macro_features(view, view.sessions(600).astype("datetime64[D]").astype("int64"))


def test_foreign_series_are_lagged_one_session(store: PitStore) -> None:
    base = _macro(store)
    prev = store.calendar.good[store.calendar.good_index(A) - 1].astype(object)

    def on_anchor(t: pd.DataFrame) -> None:
        m = t["code"].isin(["INR=X", "CL=F", "^TNX"]) & (t["date"] == A)
        t.loc[m, "value"] = t.loc[m, "value"] * 10

    def on_prev(t: pd.DataFrame) -> None:
        m = t["code"].isin(["INR=X", "CL=F", "^TNX"]) & (t["date"] == prev)
        t.loc[m, "value"] = t.loc[m, "value"] * 10

    same = _macro(_with_series(store, on_anchor))
    assert same == base  # a foreign close dated on the anchor day is not visible
    moved = _macro(_with_series(store, on_prev))
    assert moved["usdinr_3m"] != base["usdinr_3m"] and moved["wti_3m"] != base["wti_3m"]

    def vix_anchor(t: pd.DataFrame) -> None:
        m = (t["code"] == "^INDIAVIX") & (t["date"] == A)
        t.loc[m, "value"] = 99.0

    assert _macro(_with_series(store, vix_anchor))["vix"] == 99.0  # India VIX is same-day


def test_trainable_mask_on_built_matrix(store: PitStore) -> None:
    early = build_view(store.as_of(date(2018, 12, 31)), REG, None).features
    for name, tf in REG.trainable_from().items():
        if date(2018, 12, 31) < tf:
            assert early[name].isna().all(), name
    assert early["deliv_chg"].isna().all() and early["fpi_sector_flow_3m"].isna().all()
    assert early["mom_3m"].notna().any()


def test_quarterly_rows_never_feed_ownership(store: PitStore, built) -> None:
    assert (built.features["prom_chg_1y"].abs() < 100).all()


def test_panel_matches_back_adjust(store: PitStore) -> None:
    view = store.as_of(A)
    panel = view.price_panel(1010)
    adj = view.prices_adj(600)
    piv = adj.pivot(index="date", columns="security_id", values="close_adj")
    got = pd.DataFrame(panel.close_adj, index=[d.astype(object) for d in panel.sessions], columns=panel.security_ids)
    common = piv.index.intersection(got.index)
    np.testing.assert_allclose(got.loc[common, piv.columns].to_numpy(), piv.loc[common].to_numpy(), rtol=1e-12)


def test_panel_drops_rows_not_available_on_their_date(tmp_path: Path) -> None:
    s1 = make_store(tmp_path / "a")
    s2 = make_store(tmp_path / "b")
    path = s2.snapshot_dir / "derived" / "prices.parquet"
    df = pq.read_table(path).to_pandas()
    i = df.index[(df["security_id"] == 30) & (df["date"] == date(2019, 6, 27))][0]
    df.loc[i, "available_from"] = date(2019, 6, 28)  # arrived a day late: no known timing
    df.to_parquet(path, index=False)
    p1 = s1.as_of(A).price_panel(1010)
    p2 = PitStore(s2.snapshot_dir, outputs_dir=s2.outputs_dir).as_of(A).price_panel(1010)
    col = list(p1.security_ids).index(30)
    assert not np.isnan(p1.close_raw[-2, col]) and np.isnan(p2.close_raw[-2, col])


def test_corrupt_later_factor_does_not_touch_returns(store: PitStore) -> None:
    view = store.as_of(A)
    p = view.price_panel(1010)
    r_before = build_context(view, pd.DataFrame({"security_id": [30]})).R
    assert np.isfinite(p.log_f).all() and (p.log_f != 0).sum() == 1
    assert np.isfinite(np.nanmax(r_before))


def test_rrg_quadrants_cycle_clockwise() -> None:
    w = np.arange(160)
    rs = 1.0 + 0.1 * np.sin(2 * np.pi * w / 40.0)
    ratio, mom = rrg_series(rs)
    q = quadrant(ratio[:, 0], mom[:, 0])[100:140]
    assert set(q) == {0, 1, 2, 3}
    names = [QUAD_NAMES[int(x)] for x in q]
    order = [n for i, n in enumerate(names) if i == 0 or n != names[i - 1]]
    cycle = ["Improving", "Leading", "Weakening", "Lagging"]
    start = cycle.index(order[0])
    assert order[:4] == [cycle[(start + k) % 4] for k in range(4)]


def test_rrg_dossier_variant_and_weekly_points() -> None:
    pts = weekly_points(23)
    assert pts[-1] == 22 and np.all(np.diff(pts) == 5)
    rs = np.exp(0.01 * np.arange(120))
    ratio, _ = rrg_series(rs, RrgParams(variant="dossier"))
    assert np.isnan(ratio[59]) and ratio[60] == pytest.approx(100 * np.exp(0.6))


def test_regime_labels() -> None:
    v = dict.fromkeys(REGIME_NAMES, float("nan")) | {"n500_ma200_gap": 0.1, "n500_ma200_slope_63d": 0.02,
                                                      "vix_pct_3y": 80.0, "vix": 20.0, "pct_above_200dma": 30.0,
                                                      "div_6m": 12.0, "wti_3m": 0.25}  # fmt: skip
    lab = regime_labels(v)
    assert lab["trend"] == "Up" and lab["vol"] == "Stressed" and lab["breadth"] == "Narrow"
    assert lab["narrow_rally"] == "true" and "crude +20% 3M" in lab["macro"]
