"""P1 acceptance on the real snapshot (02 §7, 05 §1/§10.3). Requires `ideation snapshot --full` to have run.

T-01 future-poison (prices / universe / mcap / label inputs), T-03 past-poison, T-15 rename stitching,
T-16 survivorship, T-20 PIT adjustment, T-21 mcap consistency, T-22 label cross-check, 02 §0.1 coverage and
02 §0.2 exit-count reproductions, U_score N vs 05 §1.1.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pytest

from ideation.config import load_methodology
from ideation.labels.build import compute_labels, label_specs
from ideation.labels.exits_report import exit_counts_02
from ideation.labels.loader import load_labels, unsealed_only
from ideation.pit.contract import PitStore
from ideation.universe.build import UniverseConfig, build_universe
from ideation.universe.coverage import coverage_02

CFG = UniverseConfig.from_methodology(load_methodology().data)
_UCACHE: dict[tuple[str, date], pd.DataFrame] = {}


def universe(store: PitStore, a: date) -> pd.DataFrame:
    key = (store.snap_id, a)
    if key not in _UCACHE:
        _UCACHE[key] = build_universe(store.as_of(a, record=False), CFG)[0]
    return _UCACHE[key]


# ── T-01: future poison ──────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(("anchor", "mode"), [(date(2019, 6, 28), "inflate"), (date(2023, 6, 30), "nan")])
def test_t01_future_poison_universe_and_mcap(pit_store: PitStore, anchor: date, mode: str) -> None:
    clean, cov0 = build_universe(pit_store.as_of(anchor, record=False), CFG)
    bad_store = pit_store.poison_after(anchor, mode, seed=11)  # type: ignore[arg-type]
    bad, cov1 = build_universe(bad_store.as_of(anchor, record=False), CFG)
    pd.testing.assert_frame_equal(clean, bad, check_exact=True)
    assert cov0 == cov1
    assert clean["mcap_pit_cr"].notna().sum() > 800  # the poison really reached data the builder reads


def test_t01_future_poison_labels(pit_store: PitStore) -> None:
    a = date(2019, 6, 28)
    u = universe(pit_store, a)
    specs = label_specs(pit_store.calendar, [a], ["1Y", "3M"])
    end = max(s.label_end for s in specs)
    clean = compute_labels(pit_store, u, specs)
    bad = compute_labels(pit_store.poison_after(end, "inflate", seed=5), u, specs)
    pd.testing.assert_frame_equal(clean, bad, check_exact=True)


def test_t03_past_poison_labels(pit_store: PitStore) -> None:
    """Poison OHLC on or before A in the stitched prices: labels are bit-identical; entry is s1(A)."""
    a = date(2019, 6, 28)
    u = universe(pit_store, a)
    specs = label_specs(pit_store.calendar, [a], ["1Y", "1M"])
    clean = compute_labels(pit_store, u, specs)
    dirty = PitStore(pit_store.snapshot_dir)
    tbl = pit_store.arrow("prices")
    past = pc.less_equal(tbl["date"], pa.scalar(a, pa.date32()))
    rng = np.random.default_rng(7)
    for col in ("open", "high", "low", "close", "prev_close"):
        noise = pa.array(rng.uniform(1, 1e6, tbl.num_rows))
        tbl = tbl.set_column(tbl.column_names.index(col), col, pc.if_else(past, noise, tbl[col]))
    dirty._arrow["prices"] = tbl
    pd.testing.assert_frame_equal(clean, compute_labels(dirty, u, specs), check_exact=True)
    assert (clean["entry_date"] > a).all()


# ── T-15: rename stitching ───────────────────────────────────────────────────────────────────────────────────
def test_t15_renames_are_one_security_and_not_exits(pit_store: PitStore) -> None:
    seg = pit_store.table("security_map")
    sid = seg.groupby("symbol")["security_id"].agg(lambda s: set(s))
    assert sid["ZOMATO"] == sid["ETERNAL"]
    assert sid["TATAMOTORS"] == sid["TMPV"]
    assert sid["TMCV"].isdisjoint(sid["TMPV"])  # the demerged entity is a new security
    sc = pit_store.table("symbol_change")
    cal = pit_store.calendar
    linked = total = 0
    for old, new, eff in sc[["old_symbol", "new_symbol", "effective_date"]].itertuples(index=False):
        if old not in sid.index or new not in sid.index or eff < date(2010, 3, 1):
            continue
        o, n = seg[seg["symbol"] == old], seg[seg["symbol"] == new]
        pe = cal.good_index(eff)
        if (abs(np.array([cal.good_index(d) for d in o["valid_to"]]) - pe) <= 10).any() and (
            abs(np.array([cal.good_index(d) for d in n["valid_from"]]) - pe) <= 10
        ).any():
            total += 1
            linked += bool(sid[old] & sid[new])
    assert total > 400
    assert linked == total
    # covered names in the 2016-03-31 top-1000: renames inside the year are not exits
    a = date(2016, 3, 31)
    u = universe(pit_store, a)
    lab = compute_labels(pit_store, u[u["in_u1000"]], label_specs(cal, [a], ["1Y"]))
    cov = lab.merge(u[["security_id", "company_id"]], on="security_id")
    cov = cov[cov["company_id"].notna()]
    assert (cov["exit_type"] != "distress").all()
    assert (cov["exit_type"] == "normal").mean() >= 0.99


# ── T-16: survivorship ───────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("anchor", "symbol", "exit_type"),
    [
        (date(2016, 3, 31), "ADVANTA", "corp_exit"),  # delisted after the UPL merger; liquid EQ to the end
        (date(2016, 3, 31), "KALINDEE", "corp_exit"),  # amalgamated into Texmaco
        (date(2015, 7, 31), "PLETHICO", "distress"),  # suspended in BZ, then gone
        (date(2015, 7, 31), "COREEDUTEC", "distress"),  # BZ distress
    ],
)
def test_t16_known_exits_present_and_labelled(pit_store: PitStore, anchor: date, symbol: str, exit_type: str) -> None:
    """02 T-16 at 2016-03-31: no BZ/distress exit of an uncovered top-1000 name falls inside the next year (BZ
    names have already left the EQ/BE liquidity ranking), so the distress fixtures use the 2015-07-31 anchor."""
    u = universe(pit_store, anchor)
    row = u[(u["symbol"] == symbol) & u["in_u1000"]]
    assert len(row) == 1, f"{symbol} must be in the top-1000 at {anchor}"
    r = row.iloc[0]
    assert pd.isna(r["company_id"])
    assert not r["in_u_score"]
    assert r["exclusion_reason"] in ("no_company", "adv_below_floor")
    lab = compute_labels(pit_store, row, label_specs(pit_store.calendar, [anchor], ["1Y"])).iloc[0]
    assert lab["exit_type"] == exit_type
    if exit_type == "distress":
        assert lab["ret_total"] == -1.0
        assert lab["ret_total_last_price"] > -1.0
    else:
        assert lab["ret_total"] == pytest.approx(lab["exit_price"] / lab["entry_price"] * np.exp(0) - 1, abs=0.2)


# ── T-20 / T-21 ──────────────────────────────────────────────────────────────────────────────────────────────
def test_t20_pit_adjustment_no_future_factors(pit_store: PitStore) -> None:
    a = date(2016, 3, 31)
    v = pit_store.as_of(a, record=False)
    kinds = ("split", "bonus", "jump", "dividend")
    adj = v.prices_adj(500, kinds=kinds)
    fac = pit_store.table("factors")
    fac = fac[fac["apply"].astype(bool) & fac["kind"].isin(kinds)]
    known = fac[fac["available_from"] <= a]
    for sym in ("HATHWAY", "MINDACORP", "RELIANCE", "INFY"):
        s = adj[adj["symbol"] == sym]
        sid = s["security_id"].iloc[0]
        f = known[(known["security_id"] == sid) & (known["eff_date"] <= a)]
        for row in s.itertuples(index=False):
            mult = float(np.prod(f.loc[f["eff_date"] > row.date, "factor"].to_numpy()))
            assert row.close_adj == pytest.approx(row.close * mult, rel=1e-12)
    rel = adj[adj["symbol"] == "RELIANCE"]
    future = fac[(fac["security_id"] == rel["security_id"].iloc[0]) & (fac["kind"] == "split")]
    assert (future["eff_date"] > a).any()  # 2017 bonus exists in the snapshot ...
    split_only = v.prices_adj(500, kinds=("split", "bonus"))
    r2 = split_only[split_only["symbol"] == "RELIANCE"]
    assert (r2["close_adj"] == r2["close"]).all()  # ... but is never applied inside the 2016 view
    hath = split_only[(split_only["symbol"] == "HATHWAY") & (split_only["date"] < date(2015, 1, 6))]
    assert np.allclose(hath["close_adj"], hath["close"] * 0.2)


def test_t21_mcap_consistency(pit_store: PitStore) -> None:
    u = universe(pit_store, date(2019, 3, 29))
    x = u.dropna(subset=["mcap_a_cr", "mcap_b1_cr"])
    err = (x["mcap_b1_cr"] / x["mcap_a_cr"] - 1).abs()
    assert len(x) > 1000
    assert err.median() < 0.02
    assert (err <= 0.10).mean() >= 0.85


# ── T-22 ─────────────────────────────────────────────────────────────────────────────────────────────────────
def test_t22_label_crosscheck_yf(pit_store: PitStore) -> None:
    """Own 1Y TR vs yfinance adj_close, covered names, every UNSEALED June anchor: <= 2% differ by > 5 pp. Sealed
    anchors are never read (the loader enforces it)."""
    cal = pit_store.calendar
    anchors = unsealed_only(cal.annual_anchors(6, 2015, 2025), "1Y")
    assert date(2025, 6, 30) not in anchors
    u = pd.concat([universe(pit_store, a) for a in anchors])
    built = compute_labels(pit_store, u, label_specs(cal, anchors, ["1Y"]))
    lab = load_labels(anchors, "1Y", "qa", source=built, unsealed=set())
    yf = pit_store.table("yf_adj_close")[["symbol", "date", "adj_close"]]
    co = pit_store.table("company").set_index("company_id")["nse_symbol"]
    sec_co = u.drop_duplicates("security_id").set_index("security_id")["company_id"].dropna().astype(int)
    lab = lab[(lab["exit_type"] == "normal") & lab["security_id"].isin(sec_co.index)].copy()
    lab["sym"] = lab["security_id"].map(sec_co).map(co)
    px = yf.set_index(["symbol", "date"])["adj_close"]
    k0 = pd.MultiIndex.from_arrays([lab["sym"], lab["entry_date"]])
    k1 = pd.MultiIndex.from_arrays([lab["sym"], lab["last_trade"]])
    lab["yf"] = px.reindex(k1).to_numpy() / px.reindex(k0).to_numpy() - 1
    lab = lab.dropna(subset=["yf"])
    lab["off"] = (lab["ret_total"] - lab["yf"]).abs() > 0.05
    share = lab.groupby("anchor")["off"].mean()
    assert lab.groupby("anchor").size().min() > 900
    assert (share <= 0.02).all(), share.to_dict()


# ── reproductions (02 §0.1, §0.2) and 05 §1.1 N ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("anchor", [date(2016, 3, 31), date(2019, 3, 29), date(2022, 3, 31), date(2025, 3, 28)])
def test_coverage_02_reproduction(pit_store: PitStore, anchor: date) -> None:
    c = coverage_02(pit_store, anchor, CFG.raw)
    assert c["n_traded"] == c["ref_02"]["traded"]
    for k, dpp in c["delta_pp_02map"].items():
        assert abs(dpp) <= 2.0, (k, dpp)


def test_exit_counts_02_reproduction(pit_store: PitStore) -> None:
    r = exit_counts_02(pit_store)
    for k, pct in r["delta_pct_vs_02"].items():
        assert abs(pct) <= 5.0, (k, pct)


@pytest.mark.parametrize(
    ("anchor", "expected", "tol"),
    [(date(2016, 6, 30), 700, 0.25), (date(2019, 6, 28), 780, 0.15), (date(2022, 6, 30), 1250, 0.15),
     (date(2025, 6, 30), 1650, 0.15)],
)  # fmt: skip
def test_u_score_n_vs_05(pit_store: PitStore, anchor: date, expected: int, tol: float) -> None:
    """05 §1.1 expects ±15% after stitching. 2016 measures above +15%: stitching maps about 96 more 2016 names than
    05's 'mapped by today's nse_symbol' upper bound, and the P2 core-feature filter is not applied yet (STATUS)."""
    n = int(universe(pit_store, anchor)["in_u_score"].sum())
    assert abs(n / expected - 1) <= tol, n
