"""Fundamental feature builder on a synthetic PIT snapshot (no lake): basis rule, FY gaps, C7 growth rule, later-wins
availability, layouts, completeness / c_i, T-01 future poison, registry validation and the transform helpers."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from ideation.config import REPO_ROOT
from ideation.features.fundamental import FLAG_COLS, SOURCE, build_fundamental, feasible_fy
from ideation.features.registry import Registry, RegistryError, load_registry
from ideation.features.transform import na_layout_frame, raw_winsorized
from ideation.pit.contract import LeakageError, PitStore

REG = load_registry(REPO_ROOT / "config")
DAYS = [d.date() for d in pd.bdate_range("2012-01-02", "2021-12-31")]
A = date(2019, 6, 28)
IDS = {alias: i + 1 for i, alias in enumerate(REG.lines)}


def _w(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), path)


def lines(y: int, *, scale: float = 1.0, **over: float) -> dict[str, float]:
    """A plausible set of annual lines for FY ``y`` (Mar year-end)."""
    k = (y - 2014) * scale
    v = dict(sales=100 * k, op=20 * k, dep=5.0, tax_pct=25.0, np=10 * k, eps=1.0 * k, np_eps=10 * k, np_exex=9 * k,
             eqcap=10.0, reserves=100 * k, borr=20.0, ta=300 * k, cash=10.0, cfo=15 * k, fcf=12 * k, nci=0.0,
             payout_pct=20.0, capex=-5.0, ccc=40 + y - 2014)  # fmt: skip
    v.update(over)
    return v


def facts(cid: int, years: list[int], basis: str = "S", *, avail: dict[int, date] | None = None, **kw) -> list[dict]:
    rows = []
    for y in years:
        pe = date(y, 3, 31)
        af = (avail or {}).get(y, pe + timedelta(days=61))
        for alias, val in lines(y, **kw).items():
            rows.append({"company_id": cid, "basis": basis, "metric_id": IDS[alias], "period_type": "A",
                         "period_end": pe, "value": float(val), "available_from": af, "role": "data"})  # fmt: skip
    return rows


@pytest.fixture
def store(tmp_path: Path) -> PitStore:
    cal = pd.DataFrame({"session": DAYS, "in_bhav": True, "in_index": True, "bhav_missing": False, "n_eq": 1})
    _w(tmp_path / "derived" / "calendar.parquet", cal.assign(available_from=cal["session"], role="data"))
    m = pd.DataFrame({"metric_id": list(IDS.values()), "section": [REG.lines[a][0] for a in IDS],
                      "name": [REG.lines[a][1] for a in IDS], "unit": "cr", "canonical_code": None,
                      "source": "screener", "available_from": date(1900, 1, 1), "role": "reference"})  # fmt: skip
    _w(tmp_path / "metric.parquet", m)
    sectors = {1: "Industrials", 2: "Industrials", 3: "Industrials", 4: "Industrials", 5: "Industrials",
               6: "Financial Services", 7: "Industrials", 8: "Industrials"}  # fmt: skip
    co = pd.DataFrame({"company_id": list(sectors), "sector": list(sectors.values()), "industry_group": "G",
                       "available_from": date(1900, 1, 1), "role": "reference"})  # fmt: skip
    _w(tmp_path / "company.parquet", co)
    first = {c: DAYS[0] for c in sectors} | {4: date(2018, 4, 2)}
    sm = pd.DataFrame({"security_id": [c * 10 for c in sectors], "symbol": [f"S{c}" for c in sectors], "isin": "",
                       "valid_from": [first[c] for c in sectors], "valid_to": date(2021, 12, 31),
                       "company_id": [float(c) for c in sectors], "link_method": "seed", "company_link": "isin",
                       "series_last": "EQ", "available_from": [first[c] for c in sectors],
                       "role": "identity"})  # fmt: skip
    _w(tmp_path / "derived" / "security_map.parquet", sm)
    rows: list[dict] = []
    # 1: consolidated beats standalone for the same FY (S values differ wildly)
    rows += facts(1, [2015, 2016, 2017, 2018, 2019], "C") + facts(1, [2015, 2016, 2017, 2018, 2019], "S", scale=7.0)
    # 2: standalone until FY2016, consolidated afterwards: the FY2017 growth spans a switch
    rows += facts(2, [2015, 2016], "S") + facts(2, [2017, 2018, 2019], "C")
    # 3: FY2018 is missing (gap)
    rows += facts(3, [2015, 2016, 2017, 2019], "S")
    # 4: listed 2018-04-02: FY2019 ends within 12 months after listing
    rows += facts(4, [2018, 2019], "S")
    # 5: FY2019 filed late (available 2019-07-15)
    rows += facts(5, [2015, 2016, 2017, 2018, 2019], "S", avail={2019: date(2019, 7, 15)})
    # 6: financing layout (Revenue + Financing Profit present)
    fin = dict(revenue=500.0, fin_profit=120.0, fin_margin_pct=24.0, expenses=300.0, other_income=10.0, deposits=900.0)
    rows += facts(6, [2015, 2016, 2017, 2018, 2019], "S", **fin)
    # 7: negative equity in FY2019
    rows += facts(7, [2018, 2019], "S", eqcap=10.0, reserves=-500.0)
    # 8: one FY only (c_i = 1/3 of completeness), FY2019
    rows += facts(8, [2019], "S")
    _w(tmp_path / "financial_fact.parquet", pd.DataFrame(rows))
    (tmp_path / "manifest.json").write_text(json.dumps({"snap_id": "t", "observation_date": "2021-12-31"}))
    return PitStore(tmp_path)


def members(cids: range = range(1, 9), mcap: float = 1000.0) -> pd.DataFrame:
    return pd.DataFrame({"security_id": [c * 10 for c in cids], "company_id": list(cids), "mcap_pit_cr": mcap,
                         "mcap_src": "A", "mcap_flag": False})  # fmt: skip


def build(store: PitStore, anchor: date = A, **kw) -> pd.DataFrame:
    return build_fundamental(store.as_of(anchor), members(**kw), REG).set_index("company_id")


def test_basis_rule_prefers_consolidated_per_fy(store: PitStore) -> None:
    f = build(store)
    assert f.loc[1, "basis"] == "C"
    # FY2019 Sales = 500, FY2018 = 400 on the C rows (the S rows are 7x)
    assert f.loc[1, "rev_g1"] == pytest.approx(500 / 400 - 1)
    assert f.loc[1, "sp"] == pytest.approx(500 / 1000)
    assert f.loc[1, "ep"] == pytest.approx(50 / 1000)
    avg_eq = ((10 + 500) + (10 + 400)) / 2
    assert f.loc[1, "roe"] == pytest.approx(50 / avg_eq)


def test_growth_and_rolling_stats_are_nan_across_basis_switch(store: PitStore) -> None:
    f = build(store)
    assert f.loc[2, "basis"] == "C"
    assert f.loc[2, "rev_g1"] == pytest.approx(500 / 400 - 1)  # FY2018 and FY2019 are both C
    assert np.isnan(f.loc[2, "eps_g3"])  # needs the FY2017 growth, which spans S -> C
    assert np.isfinite(f.loc[2, "roe_3y"])  # FY2017-19 are all consolidated: the switch lies outside the window
    g = [(y - 2014) / (y - 2015) - 1 for y in (2019, 2018, 2017)]
    assert f.loc[1, "eps_g3"] == pytest.approx(np.mean(g))


def test_fy_gap_gives_nan_growth_but_levels_survive(store: PitStore) -> None:
    f = build(store)
    assert np.isnan(f.loc[3, "rev_g1"])  # FY2018 missing: FY2019 vs FY2017 is not a YoY change
    assert np.isnan(f.loc[3, "eps_g1"])
    assert f.loc[3, "opm"] == pytest.approx(0.2)


def test_ipo_year_growth_is_nan_then_valid(store: PitStore) -> None:
    f = build(store)
    assert np.isnan(f.loc[4, "rev_g1"])  # FY2019 ends 2019-03-31, within 12 months after the 2018-04-02 listing
    rows = facts(4, [2020], "S")
    # extend the store with FY2020 for company 4 and look from 2020-06-30
    ff = pd.read_parquet(store.snapshot_dir / "financial_fact.parquet")
    _w(store.snapshot_dir / "financial_fact.parquet", pd.concat([ff, pd.DataFrame(rows)], ignore_index=True))
    s2 = PitStore(store.snapshot_dir)
    f2 = build(s2, date(2020, 6, 30))
    assert f2.loc[4, "rev_g1"] == pytest.approx(600 / 500 - 1)  # FY2020 ends after listing + 12 months (2019-04-02)


def test_late_filer_enters_on_its_available_from(store: PitStore) -> None:
    before = build(store, date(2019, 7, 12))
    after = build(store, date(2019, 7, 15))
    assert before.loc[5, "fy_end"] == date(2018, 3, 31)
    assert after.loc[5, "fy_end"] == date(2019, 3, 31)
    assert before.loc[5, "feature_available_from"] <= date(2019, 7, 12)
    assert after.loc[5, "feature_available_from"] == date(2019, 7, 15)
    assert before.loc[1, "fy_end"] == date(2019, 3, 31)


def test_layouts_and_not_applicable_columns(store: PitStore) -> None:
    f = build(store)
    assert f.loc[6, "layout"] == "fin_layout"
    assert f.loc[1, "layout"] == "core"
    assert np.isnan(f.loc[6, "ebitda_ev"])
    assert np.isnan(f.loc[6, "roic"])
    assert np.isnan(f.loc[6, "nd_ebitda"])
    assert f.loc[6, "fin_margin"] == pytest.approx(0.24)
    assert f.loc[6, "cost_ratio"] == pytest.approx(300 / 510)
    assert f.loc[6, "bp"] == pytest.approx(510 / 1000)
    assert f.loc[6, "rev_g1"] == pytest.approx(0.0)  # Revenue 500 both years
    assert f.loc[6, "dep_g1"] == pytest.approx(0.0)
    assert np.isnan(f.loc[1, "bp"])  # financing-only column on a core name
    na = na_layout_frame(f.reset_index(), REG, ["ebitda_ev", "bp", "roe"])
    assert na.set_index(f.index)["ebitda_ev"].loc[6]
    assert not na.set_index(f.index)["bp"].loc[6]
    assert not na["roe"].any()


def test_negative_equity_blanks_roe_and_roic(store: PitStore) -> None:
    f = build(store)
    assert np.isnan(f.loc[7, "roe"])
    assert np.isnan(f.loc[7, "roic"])
    assert f.loc[7, "roa"] == pytest.approx(50 / ((300 * 5 + 300 * 4) / 2))
    assert f.loc[7, "ep"] == pytest.approx(50 / 1000)


def test_completeness_and_c_i(store: PitStore) -> None:
    f = build(store)
    horizon = feasible_fy(A, date(2015, 3, 31))
    assert horizon == 5
    assert f.loc[1, "feature_complete"] == pytest.approx(1.0)
    assert f.loc[1, "c_i"] == pytest.approx(1.0)
    assert f.loc[8, "feature_complete"] < f.loc[1, "feature_complete"]  # one FY: no growth, no 3Y statistics
    assert f.loc[8, "c_i"] == pytest.approx(f.loc[8, "feature_complete"] / 3)
    assert f.loc[6, "feature_complete"] == pytest.approx(1.0)  # layout-applicable features only
    assert feasible_fy(date(2016, 6, 30), date(2015, 3, 31)) == 2
    assert feasible_fy(date(2015, 5, 1), date(2015, 3, 31)) == 0


def test_feature_available_from_never_exceeds_anchor(store: PitStore) -> None:
    for a in (date(2019, 6, 28), date(2019, 7, 15), date(2018, 6, 29)):
        f = build(store, a)
        assert f["feature_available_from"].max() <= a


def test_stale_fy_gives_no_features(store: PitStore) -> None:
    f = build(store, date(2021, 12, 31))
    assert f["layout"].isna().all()  # every latest FY is older than 730 days
    assert f[REG.names(source=SOURCE)].isna().all().all()


@pytest.mark.parametrize("mode", ["nan", "shuffle", "inflate"])
def test_t01_future_poison_synthetic(store: PitStore, mode: str) -> None:
    clean = build_fundamental(store.as_of(A), members(), REG)
    bad_store = store.poison_after(A, mode, seed=3)  # type: ignore[arg-type]
    assert (bad_store.table("financial_fact")["available_from"] > A).any()
    bad = build_fundamental(bad_store.as_of(A), members(), REG)
    pd.testing.assert_frame_equal(clean, bad, check_exact=True)
    old = store.poison_after(date(2018, 1, 1), "inflate", seed=3)
    assert not build_fundamental(old.as_of(A), members(), REG).equals(clean)  # positive control


def test_output_is_sorted_and_flag_columns_present(store: PitStore) -> None:
    f = build_fundamental(store.as_of(A), members(range(8, 0, -1)), REG)
    assert f["security_id"].is_monotonic_increasing
    assert set(FLAG_COLS) <= set(f.columns)
    assert set(REG.names(source=SOURCE)) <= set(f.columns)


def test_growth_flag_marks_repeated_extreme_growth(store: PitStore) -> None:
    rows = []
    for y, s in zip([2016, 2017, 2018, 2019], [1.0, 7.0, 49.0, 343.0], strict=True):
        rows += facts(9, [y], "S", sales=s, eps=s)
    ff = pd.read_parquet(store.snapshot_dir / "financial_fact.parquet")
    _w(store.snapshot_dir / "financial_fact.parquet", pd.concat([ff, pd.DataFrame(rows)], ignore_index=True))
    co = pd.read_parquet(store.snapshot_dir / "company.parquet")
    _w(store.snapshot_dir / "company.parquet", pd.concat([co, co.iloc[[0]].assign(company_id=9)], ignore_index=True))
    sm = pd.read_parquet(store.snapshot_dir / "derived" / "security_map.parquet")
    _w(store.snapshot_dir / "derived" / "security_map.parquet",
       pd.concat([sm, sm.iloc[[0]].assign(security_id=90, company_id=9.0)], ignore_index=True))  # fmt: skip
    f = build_fundamental(PitStore(store.snapshot_dir).as_of(A), members(range(9, 10)), REG).set_index("company_id")
    assert bool(f.loc[9, "growth_flag"])
    assert f.loc[9, "rev_g1"] == pytest.approx(6.0)  # kept, only flagged


# ── registry ────────────────────────────────────────────────────────────────────────────────────────────────
def test_registry_loads_and_exposes_model_keys() -> None:
    names = set(REG.names(sets=["core"]))
    assert {"ebitda_ev", "sp", "fcf_yield", "ep", "roic", "roe", "roa", "eps_g3", "rev_g3", "nd_ebitda"} <= names
    pri = REG.priors()
    assert pri["fcf_yield"] == 1  # 06 item 4: the dossier's 'unknown' gap is closed
    assert pri["nd_ebitda"] == -1
    assert REG.families()["roe_3y"] == REG.families()["roe"]
    assert REG.growth_cols() >= {"rev_g1", "eps_g1", "eps_g3", "rev_g3"}
    assert REG.broadcast_cols() == frozenset()
    assert "size" not in REG.names(sets=["core"])
    assert "eps_g5" not in REG.names(sets=["core", "ext"])


def test_registry_validates_lines_against_catalog() -> None:
    cat = pd.DataFrame({"metric_id": [1], "section": ["profit-loss"], "name": ["Sales"]})
    with pytest.raises(RegistryError, match="resolves to 0"):
        REG.validate_metrics(cat)
    dup = pd.DataFrame({"metric_id": [1, 2] * 1, "section": ["profit-loss"] * 2, "name": ["Sales"] * 2})
    reg = Registry({}, {"sales": ("profit-loss", "Sales")}, {})
    with pytest.raises(RegistryError, match="resolves to 2"):
        reg.validate_metrics(dup)


def _write_yaml(tmp_path: Path, text: str) -> Path:
    (tmp_path / "features_x.yaml").write_text(text)
    return tmp_path


BAD = """
lines: {a: "profit-loss/Sales"}
features:
  f1: {family: x, prior: %s, kind: cross_sectional, category: q, layouts: [core], set: core, growth: false,
       need_fy: 1, lines: [%s]}
"""


@pytest.mark.parametrize(("prior", "line", "msg"), [("2", "a", "prior"), ("1", "zzz", "undeclared")])
def test_registry_rejects_bad_entries(tmp_path: Path, prior: str, line: str, msg: str) -> None:
    with pytest.raises(RegistryError, match=msg):
        load_registry(_write_yaml(tmp_path, BAD % (prior, line)))


def test_registry_rejects_duplicates_across_files(tmp_path: Path) -> None:
    ok = BAD % ("1", "a")
    (tmp_path / "features_a.yaml").write_text(ok)
    (tmp_path / "features_b.yaml").write_text(ok.replace("lines: {a:", "lines: {b:").replace("[a]", "[b]"))
    with pytest.raises(RegistryError, match="already defined"):
        load_registry(tmp_path)


# ── transforms ──────────────────────────────────────────────────────────────────────────────────────────────
def test_raw_winsorized_is_per_anchor_and_keeps_nan() -> None:
    rng = np.random.default_rng(0)
    df = pd.DataFrame({"anchor": np.repeat([1, 2], 200), "sp": rng.normal(size=400), "eps_g1": rng.normal(size=400)})
    df.loc[0, "sp"] = 1e6
    df.loc[1, "eps_g1"] = np.nan
    df.loc[205, "sp"] = -1e6
    w = raw_winsorized(df, REG, ["sp", "eps_g1"])
    assert w["sp"].max() < 10
    assert w["sp"].min() > -10
    assert np.isnan(w.loc[1, "eps_g1"])
    a1 = df[df["anchor"] == 1]["eps_g1"].dropna()
    assert w.loc[a1.index, "eps_g1"].max() == pytest.approx(np.quantile(a1, 0.95))  # growth: P5/P95
    a2 = df[df["anchor"] == 2]["sp"]
    assert w.loc[a2.index, "sp"].min() == pytest.approx(np.quantile(a2, 0.01))
    med = raw_winsorized(df, REG, ["eps_g1"], growth_mode="dossier_median")
    lo, hi = np.quantile(a1, [0.05, 0.95])
    kept = med.loc[a1.index, "eps_g1"]
    assert kept.between(lo, hi).all()  # outliers replaced by the median, never clipped to the bound
    assert (kept == np.median(a1)).sum() > (a1.between(lo, hi) & (a1 == np.median(a1))).sum()


def test_leakage_error_is_raised_for_future_inputs(store: PitStore) -> None:
    """The final assertion in build_fundamental fires if an input row were ever later than the anchor."""
    f = build_fundamental(store.as_of(A), members(), REG)
    assert f["feature_available_from"].max() <= A
    assert issubclass(LeakageError, AssertionError)


def test_other_feature_files_do_not_leak_into_the_fundamental_builder(tmp_path: Path, store: PitStore) -> None:
    """P3 / PT2 add their own features_*.yaml: their columns must not appear in, or dilute, the fundamental output."""
    import shutil

    shutil.copy(REPO_ROOT / "config" / "features_fundamental.yaml", tmp_path / "features_fundamental.yaml")
    (tmp_path / "features_tech.yaml").write_text(
        "features:\n  mom_12_1: {family: mom, prior: 1, kind: cross_sectional, category: momentum, set: core}\n"
        "  vix_in: {family: vix, prior: null, kind: broadcast, category: regime, set: core}\n"
    )
    both = load_registry(tmp_path)
    assert "mom_12_1" in both.features
    assert both.broadcast_cols() == frozenset({"vix_in"})
    a = build_fundamental(store.as_of(A), members(), REG)
    b = build_fundamental(store.as_of(A), members(), both)
    pd.testing.assert_frame_equal(a, b, check_exact=True)
    assert "mom_12_1" not in b.columns


def test_conflicting_cleaning_keys_across_files_raise(tmp_path: Path) -> None:
    (tmp_path / "features_a.yaml").write_text("cleaning: {max_fy_age_days: 730}\nfeatures: {}\n")
    (tmp_path / "features_b.yaml").write_text("cleaning: {max_fy_age_days: 365}\nfeatures: {}\n")
    with pytest.raises(RegistryError, match="cleaning key"):
        load_registry(tmp_path)


def test_output_anchor_is_a_python_date(store: PitStore) -> None:
    f = build_fundamental(store.as_of(A), members(), REG)
    assert isinstance(f["anchor"].iloc[0], date)
    assert not isinstance(f["anchor"].iloc[0], pd.Timestamp)
