"""Security factors: causal sanity check, re-dating, residual detector, jump detector (02 §3.2), back-adjustment."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ideation.lake.prices import back_adjust, build_factors

DAYS = [d.date() for d in pd.bdate_range("2020-01-01", "2020-06-30")]


def prices(sid: int, closes: list[float], turnover: list[float] | None = None) -> pd.DataFrame:
    n = len(closes)
    return pd.DataFrame(
        {"security_id": sid, "date": DAYS[:n], "close": closes, "turnover_lacs": turnover or [10.0] * n}
    )


def segments(*pairs: tuple[int, str, int | None]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"security_id": s, "symbol": sym, "company_id": c, "valid_from": DAYS[0], "valid_to": DAYS[-1]}
            for s, sym, c in pairs
        ]
    )


def flat_then(ratio: float, at: int, n: int = 60, level: float = 100.0) -> list[float]:
    return [level] * at + [level * ratio] * (n - at)


COMPANY = pd.DataFrame({"company_id": [1, 2], "nse_symbol": ["AAA", "BBB"]})
NO_CA = pd.DataFrame(columns=["company_id", "action_type", "ex_date", "detail"])


def run(px: pd.DataFrame, af: pd.DataFrame, seg: pd.DataFrame) -> pd.DataFrame:
    return build_factors(px, af, NO_CA, seg, COMPANY).factors


def test_split_kept_at_ex_date_and_redated_forward_causally() -> None:
    px = prices(1, flat_then(0.5, 30))
    af = pd.DataFrame({"symbol": ["AAA"], "ex_date": [DAYS[26]], "factor": [0.5], "kind": ["split"]})  # 4 early
    f = run(px, af, segments((1, "AAA", 1)))
    row = f[f["kind"] == "split"].iloc[0]
    assert row["apply"]
    assert row["flag"] == "redated"
    assert row["eff_date"] == DAYS[30]
    assert row["available_from"] >= row["eff_date"]  # decided only after the jump was observable
    assert row["role"] == "basis_conversion"


def test_big_factor_without_any_jump_is_dropped() -> None:
    px = prices(1, [100.0] * 60)
    af = pd.DataFrame({"symbol": ["AAA"], "ex_date": [DAYS[20]], "factor": [0.2], "kind": ["split"]})
    f = run(px, af, segments((1, "AAA", 1)))
    assert not f["apply"].any()
    assert f["flag"].tolist() == ["dropped"]


def test_small_bonus_applied_unchecked() -> None:
    px = prices(1, [100.0] * 60)  # a 1:10 bonus is not separable from noise: applied at the ex-date
    af = pd.DataFrame({"symbol": ["AAA"], "ex_date": [DAYS[20]], "factor": [10 / 11], "kind": ["bonus"]})
    f = run(px, af, segments((1, "AAA", 1)))
    assert f["apply"].all()
    assert f["eff_date"].tolist() == [DAYS[20]]


def test_residual_detector_adds_missing_bonus() -> None:
    px = prices(1, flat_then(0.1, 30, level=9000.0))  # split 1:2 + bonus 4:1 = 0.1, lake has only the split
    af = pd.DataFrame({"symbol": ["AAA"], "ex_date": [DAYS[30]], "factor": [0.5], "kind": ["split"]})
    f = run(px, af, segments((1, "AAA", 1)))
    applied = f[f["apply"].astype(bool)]
    assert sorted(applied["factor"].round(6)) == [0.2, 0.5]
    assert set(applied["flag"]) == {"", "jump_residual"}
    assert (applied["role"] == "basis_conversion").all()


def test_jump_detector_uncovered_only_and_not_on_ticks() -> None:
    to = [10.0] * 30 + [80.0] + [20.0] * 29
    uncovered = prices(5, flat_then(0.5, 30), to)
    penny = prices(6, [0.10] * 30 + [0.05] * 30, to)
    covered_crash = prices(7, flat_then(0.62, 30), to)  # IGL-like crash on a covered name
    px = pd.concat([uncovered, penny, covered_crash], ignore_index=True)
    af = pd.DataFrame({"symbol": ["BBB"], "ex_date": [DAYS[5]], "factor": [0.99], "kind": ["dividend"]})
    f = run(px, af, segments((5, "UNC", None), (6, "PENNY", None), (7, "BBB", 2)))
    jumps = f[f["kind"] == "jump"]
    assert jumps["security_id"].tolist() == [5]
    assert jumps["factor"].tolist() == [0.5]
    assert jumps["available_from"].tolist() == [DAYS[30]]


def test_back_adjust_uses_only_factors_up_to_anchor() -> None:
    px = prices(1, flat_then(0.5, 30))
    fac = pd.DataFrame(
        {"security_id": [1, 1], "eff_date": [DAYS[30], DAYS[50]], "factor": [0.5, 0.25], "kind": ["split", "split"],
         "apply": [True, True]}
    )  # fmt: skip
    adj = back_adjust(px, fac, DAYS[40], kinds=("split",))
    assert np.allclose(adj[:30], 50.0)  # pre-split prices on the anchor's basis
    assert np.allclose(adj[30:], 50.0)
    assert adj.iloc[45] == px["close"].iloc[45]  # the later factor (eff after the anchor) is not applied
