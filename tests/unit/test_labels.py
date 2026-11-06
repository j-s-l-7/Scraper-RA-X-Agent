"""Labels and exits (02 §3.2-3.3, 03 §C3) on a synthetic snapshot, plus T-03 (past-poison: prices on or before A
cannot change a label; the entry is s1(A))."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from ideation.labels.build import compute_labels, label_specs
from ideation.pit.contract import PitStore

DAYS = [d.date() for d in pd.bdate_range("2020-01-01", "2021-03-31")]
A = DAYS[150]


def _write(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), path)


def make_store(tmp: Path, mutate=None) -> PitStore:
    rows = []

    def add(sid: int, closes: list[float], start: int = 0, series: str | list[str] = "EQ", to: float = 50.0) -> None:
        ser = series if isinstance(series, list) else [series] * len(closes)
        for k, c in enumerate(closes):
            rows.append((sid, DAYS[start + k], c, ser[k], to))

    n = len(DAYS)
    add(1, list(np.linspace(100, 130, n)))  # normal, with a dividend inside the window
    add(2, [200.0] * 160 + [100.0] * (n - 160))  # 1:2 split effective at DAYS[160]
    add(3, [50.0] * 160, series=["EQ"] * 130 + ["BZ"] * 30)  # distress: last trades in BZ, then nothing
    add(4, list(np.linspace(80, 88, 175)))  # corporate exit: liquid EQ, stops at DAYS[174]
    add(5, [30.0] * 151)  # last trade on A: no trade after the anchor
    add(6, [40.0] * 140 + [44.0] * (n - 153), start=0)  # gap: no trades DAYS[140..152], resumes at DAYS[153]
    add(7, [100.0] * 150 + list(np.linspace(100, 40, 26)), series="BE")  # BE run + >50% fall, stops at DAYS[175]
    add(8, [50.0] * 176, series="BE")  # BE, no collapse, stops at DAYS[175]: neither distress nor corp exit
    px = pd.DataFrame(rows, columns=["security_id", "date", "close", "series", "turnover_lacs"])
    px.loc[(px["security_id"] == 6) & (px.index >= px[px["security_id"] == 6].index[0] + 140), "date"] = [
        DAYS[153 + k] for k in range(n - 153)
    ]
    px = px.assign(symbol="S" + px["security_id"].astype(str), available_from=px["date"], role="data")
    if mutate is not None:
        px = mutate(px)
    fac = pd.DataFrame(
        {
            "security_id": [1, 2, 1], "ex_date": [DAYS[170], DAYS[160], DAYS[180]],
            "eff_date": [DAYS[170], DAYS[160], DAYS[180]], "factor": [0.98, 0.5, 0.9],
            "kind": ["dividend", "split", "dividend"], "source": "adj_factor", "flag": "", "apply": True,
            "available_from": [DAYS[170], DAYS[160], DAYS[300]],  # the last one is only known long after label_end
            "role": ["data", "basis_conversion", "data"],
        }
    )  # fmt: skip
    cal = pd.DataFrame({"session": DAYS, "in_bhav": True, "in_index": True, "bhav_missing": False, "n_eq": 6})
    _write(tmp / "derived" / "prices.parquet", px)
    _write(tmp / "derived" / "factors.parquet", fac)
    _write(tmp / "derived" / "calendar.parquet", cal.assign(available_from=cal["session"], role="data"))
    _write(
        tmp / "derived" / "rights.parquet",
        pd.DataFrame(
            {"security_id": [4], "ex_date": [DAYS[160]], "detail": ["RIGHTS 1:5"], "available_from": [DAYS[160]],
             "role": ["data"]}
        ),
    )  # fmt: skip
    idx = pd.DataFrame({"code": "^CRSLDX", "date": DAYS, "value": np.linspace(1000, 1100, n)})
    _write(tmp / "series_daily.parquet", idx.assign(available_from=idx["date"], role="data"))
    (tmp / "manifest.json").write_text(json.dumps({"snap_id": "synthetic", "observation_date": str(DAYS[-1])}))
    return PitStore(tmp)


UNIVERSE = pd.DataFrame(
    {"security_id": [1, 2, 3, 4, 5, 6, 7, 8], "anchor": A, "traded_ok": True,
     "in_u_score": [True, True, False, True, False, True, False, False], "in_u_score_t": True,
     "mcap_pit_cr": [100.0, 300.0, None, 50.0, None, 10.0, None, None]}
)  # fmt: skip


@pytest.fixture
def labels(tmp_path: Path) -> pd.DataFrame:
    store = make_store(tmp_path)
    specs = label_specs(store.calendar, [A], ["3M"])
    return compute_labels(store, UNIVERSE, specs).set_index("security_id")


def test_timing_03_c3(labels: pd.DataFrame) -> None:
    assert (labels["label_end"] == labels["label_available_from"]).all()
    end = labels["label_end"].iloc[0]
    assert end <= (pd.Timestamp(A) + pd.DateOffset(months=3)).date()
    assert labels.loc[1, "entry_date"] == DAYS[151]  # s1(A)
    assert labels.loc[6, "entry_date"] == DAYS[153]
    assert "entry_delayed" in labels.loc[6, "flags"]


def test_total_vs_price_return_and_split(labels: pd.DataFrame) -> None:
    r = labels.loc[1]
    assert r["exit_type"] == "normal"
    assert r["ret_total"] > r["ret_price"]
    expected_price = r["exit_price"] / r["entry_price"] - 1
    assert r["ret_price"] == pytest.approx(expected_price)
    assert r["ret_total"] == pytest.approx((1 + expected_price) / 0.98 - 1)  # the late 0.9 factor is not used
    assert labels.loc[2, "ret_total"] == pytest.approx(0.0)  # split-neutral


def test_exits(labels: pd.DataFrame) -> None:
    assert labels.loc[3, "exit_type"] == "distress"
    assert labels.loc[3, "ret_total"] == -1.0
    assert labels.loc[3, "ret_total_last_price"] == pytest.approx(0.0)
    r4 = labels.loc[4]
    assert r4["exit_type"] == "corp_exit"
    assert r4["ret_total"] == pytest.approx(r4["exit_price"] / r4["entry_price"] - 1)
    assert r4["stale_days"] > 20
    assert "rights_unadjusted" in r4["flags"]
    r5 = labels.loc[5]
    assert "no_trade_after_anchor" in r5["flags"]
    assert r5["ret_total"] == 0.0
    assert np.isnan(r5["entry_price"])


def test_be_collapse_distress_and_residual_stale(labels: pd.DataFrame) -> None:
    r7 = labels.loc[7]
    assert r7["exit_type"] == "distress"  # last 60 sessions all BE and last close < 50% of entry
    assert r7["ret_total"] == -1.0
    assert r7["ret_price"] == -1.0
    assert r7["ret_total_last_price"] == pytest.approx(40.0 / r7["entry_price"] - 1)
    assert r7["exit_price"] == pytest.approx(40.0)
    r8 = labels.loc[8]
    assert r8["exit_type"] == "stale"  # no trade in the final 20 sessions, BE without collapse
    assert r8["ret_total"] == pytest.approx(0.0)  # last traded close, then cash
    assert r8["ret_total_last_price"] == pytest.approx(r8["ret_total"])
    assert r8["stale_days"] > 20


def test_cross_sectional_variants(labels: pd.DataFrame) -> None:
    assert labels["excess_univ_med"].median() == pytest.approx(0.0)
    in_u = UNIVERSE.set_index("security_id")["in_u_score_t"]
    assert labels["xs_rank"].notna().sum() == in_u.sum()
    assert labels["xs_rank"].between(0, 1).all()
    assert labels["excess_n500_pr"].notna().all()


def test_t03_past_poison(tmp_path: Path) -> None:
    """Poisoning OHLC on or before A changes no label: entry is s1(A) and returns use (entry, exit] only."""
    clean = make_store(tmp_path / "clean")
    rng = np.random.default_rng(0)

    def poison(px: pd.DataFrame) -> pd.DataFrame:
        m = px["date"] <= A
        return px.assign(close=np.where(m, rng.uniform(1, 1e6, len(px)), px["close"]))

    dirty = make_store(tmp_path / "dirty", mutate=poison)
    specs = label_specs(clean.calendar, [A], ["1M", "3M"])
    a = compute_labels(clean, UNIVERSE, specs)
    b = compute_labels(dirty, UNIVERSE, specs)
    pd.testing.assert_frame_equal(a, b, check_exact=True)
    assert (a["entry_date"] > A).all()


def test_labels_ignore_data_after_label_end(tmp_path: Path) -> None:
    """Future-poison after label_end (the T-01 analogue for labels)."""
    clean = make_store(tmp_path / "c")
    specs = label_specs(clean.calendar, [A], ["3M"])
    end = specs[0].label_end
    poisoned = clean.poison_after(end, "inflate", seed=3)
    pd.testing.assert_frame_equal(
        compute_labels(clean, UNIVERSE, specs), compute_labels(poisoned, UNIVERSE, specs), check_exact=True
    )
    assert date(2020, 1, 1) <= end
