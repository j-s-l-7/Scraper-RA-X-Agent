"""Outcomes (05 §5): formulas, windows, PIT availability of the announcement and fundamental data, set statistics.
Synthetic stores only (no lake)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from ideation.outcomes import analyst, fundamental
from ideation.pit.contract import PitStore

BDAYS = [d.date() for d in pd.bdate_range("2012-01-02", "2025-12-31")]
LAST = BDAYS[-1]


def _w(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), path)


def make_store(tmp: Path, vendor: pd.DataFrame, facts: pd.DataFrame | None = None) -> PitStore:
    cal = pd.DataFrame({"session": BDAYS, "in_bhav": True, "in_index": True, "bhav_missing": False, "n_eq": 1})
    _w(tmp / "derived" / "calendar.parquet", cal.assign(available_from=cal["session"], role="data"))
    v = vendor.assign(available_from=(pd.to_datetime(vendor["event_date"]) + pd.Timedelta(days=1)).dt.date,
                      role="data")  # fmt: skip
    _w(tmp / "earnings_vendor.parquet", v)
    names = [(12, "profit-loss", "Sales"), (21, "profit-loss", "Net Profit"), (123, "profit-loss", "Revenue"),
             (1, "quarters", "Sales"), (10, "quarters", "Net Profit"), (120, "quarters", "Revenue")]  # fmt: skip
    metric = pd.DataFrame({"metric_id": [n[0] for n in names], "section": [n[1] for n in names],
                           "name": [n[2] for n in names], "unit": "", "canonical_code": "", "source": "",
                           "available_from": date(1900, 1, 1), "role": "reference"})  # fmt: skip
    _w(tmp / "metric.parquet", metric)
    if facts is None:
        facts = pd.DataFrame({"company_id": [], "basis": [], "metric_id": [], "period_type": [], "period_end": [],
                              "value": [], "updated_at": [], "available_from": [], "role": []})  # fmt: skip
    _w(tmp / "financial_fact.parquet", facts)
    return PitStore(tmp)


def vend(rows: list[tuple[int, str, float | None, float | None]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["company_id", "event_date", "eps_estimate", "eps_reported"]).assign(
        event_date=lambda d: pd.to_datetime(d["event_date"]).dt.date
    )


def facts(
    company: int, sales: dict[str, float], pat: dict[str, float], *, basis: str = "S", lag: int = 61,
    af: dict[str, str] | None = None, mid: tuple[int, int] = (12, 21),
) -> pd.DataFrame:  # fmt: skip
    rows = []
    for mid_, vals in ((mid[0], sales), (mid[1], pat)):
        for pe, v in vals.items():
            a = (af or {}).get(pe) or str((pd.Timestamp(pe) + pd.Timedelta(days=lag)).date())
            rows.append({"company_id": company, "basis": basis, "metric_id": mid_, "period_type": "A",
                         "period_end": date.fromisoformat(pe), "value": v, "updated_at": date(2020, 1, 1),
                         "available_from": date.fromisoformat(a), "role": "data"})  # fmt: skip
    return pd.DataFrame(rows)


# ── formulas ─────────────────────────────────────────────────────────────────────────────────────────────────
def test_surprise_and_beat_formulas() -> None:
    est = np.array([10.0, 0.0, -2.0, 0.001, 1.0, 5.0])
    rep = np.array([12.0, 0.5, -1.0, 0.002, 1.0, 5.0 - 20.0])
    s = analyst.surprise(est, rep)
    assert s[0] == pytest.approx(0.2)
    assert s[1] == pytest.approx(2.0)  # |est|=0 -> denom max(0, 0.025, 0.01) = 0.025 -> 20 -> clipped to 2
    assert s[2] == pytest.approx(0.5)
    assert s[3] == pytest.approx(0.001 / 0.01)  # floor 0.01
    assert s[4] == 0.0 and s[5] == -2.0
    assert analyst.beat(est, rep).tolist() == [1.0, 1.0, 1.0, 1.0, 0.5, 0.0]


# ── analyst windows and PIT ──────────────────────────────────────────────────────────────────────────────────
EV = vend([
    (1, "2019-06-30", 1.0, 1.1),  # on the anchor: excluded
    (1, "2019-07-15", 1.0, 1.2),  # beat
    (1, "2019-10-15", 1.0, 0.8),  # miss
    (1, "2020-01-15", 1.0, 1.0),  # tie
    (1, "2020-04-15", 1.0, 1.5),
    (1, "2020-06-30", 1.0, 2.0),  # 5th event, on A+12M: beyond the first 4
    (2, "2019-09-01", 2.0, 1.0),  # only miss
    (2, "2020-08-01", 2.0, 3.0),  # outside 12M
    (3, "2019-08-01", None, 1.0),  # no estimate: dropped
    (4, "2019-08-01", 1.0, None),  # not reported: dropped
])  # fmt: skip
MEM = pd.DataFrame({"security_id": [10, 20, 30, 40, 50], "company_id": [1, 2, 3, 4, 5]})


def test_analyst_one_year_window_first_four_events(tmp_path: Path) -> None:
    store = make_store(tmp_path, EV)
    ev = analyst.load_events(store.as_of(LAST, record=False))
    out = analyst.build_analyst(ev, MEM, date(2019, 6, 30), "1Y", LAST).set_index("security_id")
    assert set(out.index) == {10, 20}
    assert out.loc[10, "n_events"] == 4
    assert out.loc[10, "out_beat"] == pytest.approx((1 + 0 + 0.5 + 1) / 4)
    assert out.loc[10, "out_surp"] == pytest.approx(np.mean([0.2, -0.2, 0.0, 0.5]))
    assert out.loc[20, "n_events"] == 1 and out.loc[20, "out_beat"] == 0.0
    assert (out["kind"] == "analyst").all() and (out["horizon"] == "1Y").all()


def test_analyst_three_month_first_event_only_and_unobserved_window(tmp_path: Path) -> None:
    store = make_store(tmp_path, EV)
    ev = analyst.load_events(store.as_of(LAST, record=False))
    out = analyst.build_analyst(ev, MEM, date(2019, 6, 30), "3M", LAST).set_index("security_id")
    assert out.loc[10, "n_events"] == 1 and out.loc[10, "out_beat"] == 1.0 and 20 in out.index  # 2019-09-01 <= 09-30
    with pytest.raises(analyst.WindowNotObservedError):
        analyst.build_analyst(ev, MEM, date(2025, 12, 1), "3M", LAST)


def test_announcement_availability_is_event_date_plus_one(tmp_path: Path) -> None:
    """A view at day X sees an event only when event_date + 1 <= X; later events are absent, never partial."""
    store = make_store(tmp_path, EV)
    on = store.as_of(date(2019, 7, 16), record=False)  # event 2019-07-15 -> available 07-16
    before = store.as_of(date(2019, 7, 15), record=False)
    assert pd.Timestamp("2019-07-15") in set(analyst.load_events(on)["event_date"])
    assert pd.Timestamp("2019-07-15") not in set(analyst.load_events(before)["event_date"])
    assert analyst.load_events(before)["event_date"].max() < pd.Timestamp("2019-07-15")


def test_analyst_future_poison_beyond_window_is_inert(tmp_path: Path) -> None:
    store = make_store(tmp_path, EV)
    a = date(2019, 6, 28)
    hi = date(2019, 9, 30)
    clean = analyst.build_analyst(analyst.load_events(store.as_of(LAST, record=False)), MEM, a, "3M", LAST)
    for mode in ("nan", "shuffle", "inflate"):
        pois = store.poison_after(date(2019, 10, 1), mode)  # type: ignore[arg-type]
        got = analyst.build_analyst(analyst.load_events(pois.as_of(LAST, record=False)), MEM, a, "3M", LAST)
        pd.testing.assert_frame_equal(got, clean, check_exact=True)
    # positive control: poisoning from the anchor on changes the outcome
    early = store.poison_after(a, "inflate")
    got = analyst.build_analyst(analyst.load_events(early.as_of(LAST, record=False)), MEM, a, "3M", LAST)
    assert not got.equals(clean)
    assert hi > a


# ── fundamental ──────────────────────────────────────────────────────────────────────────────────────────────
SALES = {"2014-03-31": 100.0, "2015-03-31": 110.0, "2016-03-31": 121.0, "2017-03-31": 133.1,
         "2018-03-31": 146.41, "2019-03-31": 190.333}  # fmt: skip
PAT = {"2014-03-31": 10.0, "2015-03-31": 11.0, "2016-03-31": 12.1, "2017-03-31": 13.31, "2018-03-31": 14.641,
       "2019-03-31": 14.641}  # fmt: skip
M1 = pd.DataFrame({"security_id": [10], "company_id": [1]})
A18 = date(2018, 6, 29)


def _panel(tmp: Path, f: pd.DataFrame) -> pd.DataFrame:
    store = make_store(tmp, EV, f)
    return fundamental.build_panel(store.as_of(LAST, record=False), "A")


def test_fundamental_surprise_values(tmp_path: Path) -> None:
    out = fundamental.build_fundamental(_panel(tmp_path, facts(1, SALES, PAT)), M1, A18).iloc[0]
    # FY(Y)=Mar-18; CAGR3 (Mar-15 -> Mar-18) = 10%; FY19 growth = 30% -> +20pp; PAT flat vs 10% CAGR -> -10pp
    assert out["out_fs_sales"] == pytest.approx(0.30 - 0.10, abs=1e-6)
    assert out["out_fs_pat"] == pytest.approx(0.0 - 0.10, abs=1e-6)
    assert out["out_accel"] == 1 and out["kind"] == "fundamental" and out["horizon"] == "1Y"


def test_fundamental_pit_current_year_must_be_available_at_anchor(tmp_path: Path) -> None:
    # Mar-18 results first reported after the anchor -> FY(Y) is Mar-17, the outcome is FY18 growth minus CAGR(14->17)
    late = facts(1, SALES, PAT, af={"2018-03-31": "2018-07-20"})
    out = fundamental.build_fundamental(_panel(tmp_path, late), M1, A18).iloc[0]
    assert out["out_fs_sales"] == pytest.approx(0.10 - 0.10, abs=1e-6)


def test_fundamental_next_year_must_land_before_cutoff(tmp_path: Path) -> None:
    late = facts(1, SALES, PAT, af={"2019-03-31": "2019-09-15"})  # after A+12M+60d = 2019-08-28
    out = fundamental.build_fundamental(_panel(tmp_path / "a", late), M1, A18)
    assert out.empty
    ok = facts(1, SALES, PAT, af={"2019-03-31": "2019-08-20"})
    assert len(fundamental.build_fundamental(_panel(tmp_path / "b", ok), M1, A18)) == 1


def test_fundamental_basis_switch_pat_sign_and_stale_rules(tmp_path: Path) -> None:
    f = pd.concat([facts(1, SALES, PAT), facts(1, {"2019-03-31": 190.333}, {}, basis="C")])
    # a C row for Mar-19 only: the FY resolves to C there, S before -> growth spans a basis switch -> NaN
    assert fundamental.build_fundamental(_panel(tmp_path / "a", f), M1, A18).empty
    neg = dict(PAT, **{"2018-03-31": -5.0})  # PAT <= 0 at the CAGR end: PAT outcome NaN, sales outcome stays
    o = fundamental.build_fundamental(_panel(tmp_path / "b", facts(1, SALES, neg)), M1, A18).iloc[0]
    assert np.isnan(o["out_fs_pat"]) and not np.isnan(o["out_fs_sales"])
    # stale: nothing within ~18 months of the anchor
    assert fundamental.build_fundamental(_panel(tmp_path / "c", facts(1, SALES, PAT)), M1, date(2021, 6, 30)).empty


def test_fundamental_q(tmp_path: Path) -> None:
    pes = pd.date_range("2023-03-31", periods=9, freq="QE")  # Mar-23 .. Mar-25
    base = [100, 100, 100, 100, 110, 110, 110, 110, 154]  # YoY: Mar-24 .. Dec-24 = 10%, Mar-25 = 40%
    rows = [{"company_id": 1, "basis": "S", "metric_id": 1, "period_type": "Q", "period_end": p.date(),
             "value": float(v), "updated_at": date(2025, 1, 1),
             "available_from": (p + pd.Timedelta(days=46)).date(), "role": "data"}
            for p, v in zip(pes, base, strict=True)]  # fmt: skip
    store = make_store(tmp_path, EV, pd.DataFrame(rows))
    panel = fundamental.build_panel(store.as_of(LAST, record=False), "Q")
    # anchor 2025-04-30: latest available = Dec-24 (Mar-25 lands 2025-05-16); next = Mar-25 (40%), trailing 10% -> +30pp
    out = fundamental.build_fundamental_q(panel, M1, date(2025, 4, 30)).iloc[0]
    assert out["out_fs_sales"] == pytest.approx(0.30, abs=1e-9) and out["out_accel"] == 1 and out["horizon"] == "3M"
    assert fundamental.build_fundamental_q(panel, M1, date(2024, 4, 30)).empty  # no trailing YoY exists yet
    # Mar-25 results delayed past A+3M: excluded
    late = pd.DataFrame(rows)
    late.loc[late["period_end"] == pes[-1].date(), "available_from"] = date(2025, 9, 1)
    p2 = fundamental.build_panel(make_store(tmp_path / "b", EV, late).as_of(LAST, record=False), "Q")
    assert fundamental.build_fundamental_q(p2, M1, date(2025, 4, 30)).empty


# ── report statistics ────────────────────────────────────────────────────────────────────────────────────────
def test_announcement_check_detects_planted_agreement_and_null() -> None:
    rng = np.random.default_rng(0)
    n = 4000
    d = pd.Timestamp("2018-01-01") + pd.to_timedelta(rng.integers(0, 365 * 6, n), unit="D")
    surp = rng.normal(size=n)
    ear = 0.02 * np.sign(surp) + rng.normal(0, 0.03, n)
    res = analyst.announcement_check(pd.DataFrame({"event_date": d, "surp": surp, "ear_3d": ear}))
    assert (
        res["t_beat_minus_miss"] > 10
        and res["sign_agreement"] > 0.6
        and res["beat_minus_miss"] == pytest.approx(0.04, abs=0.01)
    )
    null = analyst.announcement_check(pd.DataFrame({"event_date": d, "surp": surp, "ear_3d": rng.normal(0, 0.03, n)}))
    assert abs(null["t_beat_minus_miss"]) < 3 and abs(null["sign_agreement"] - 0.5) < 0.05


def test_compare_sets_and_cmh() -> None:
    rng = np.random.default_rng(1)
    rows = []
    for a in range(8):
        for i in range(200):
            top = i < 40
            beat_p, acc_p = (0.6, 0.65) if top else (0.45, 0.4)
            rows.append({"anchor": a, "top": top, "bot": i >= 160, "out_beat": float(rng.random() < beat_p),
                         "out_accel": int(rng.random() < acc_p)})  # fmt: skip
    df = pd.DataFrame(rows)
    res = analyst.compare_sets(df, {"top": df["top"], "none": pd.Series(False, index=df.index)}, "out_beat",
                               n_boot=500).set_index("set")  # fmt: skip
    assert res.loc["top", "diff"] > 0.05 and res.loc["top", "ci_lo"] > 0 and res.loc["top", "anchors"] == 8
    assert np.isnan(res.loc["none", "diff"])
    c = fundamental.cmh_accel(df, df["top"], df["bot"])
    assert c["mh_odds_ratio"] > 1.5 and c["p_one_sided"] < 0.01
    rev = fundamental.cmh_accel(df, df["bot"], df["top"])
    assert rev["p_one_sided"] > 0.99
