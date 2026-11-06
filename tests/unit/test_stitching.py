"""Security stitching edge cases (02 §2.2) on synthetic listing history."""

from __future__ import annotations

from datetime import date

import pandas as pd

from ideation.lake.symbols import assign_security, build_calendar, stitch
from ideation.pit.calendar import TradingCalendar

DAYS = [d.date() for d in pd.bdate_range("2010-01-04", "2016-12-30")]
CAL = TradingCalendar.from_frame(
    build_calendar(pd.DataFrame({"date": DAYS, "n_rows": 1, "n_eq": 1}), pd.Series(DAYS, dtype=object))
)


def nth(d: date, k: int) -> date:
    """k good sessions after d (negative: before)."""
    i = CAL.good_index(d) + k
    return CAL.good[i].astype(object)


def lh(rows: list[tuple[str, str, str, date, date]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["symbol", "isin", "series", "first_seen", "last_seen"])


COMPANY = pd.DataFrame(
    {"company_id": [1, 2, 3], "nse_symbol": ["NEWNAME", "SPLITCO", "LIVE2"], "isin": ["INE001", "INE002B", "INE003"]}
)
NO_CHANGES = pd.DataFrame(columns=["old_symbol", "new_symbol", "effective_date"])


def by_symbol(res, sym: str) -> set[int]:
    return set(res.segments.loc[res.segments["symbol"] == sym, "security_id"])


def test_rename_by_symbol_change_within_10_sessions() -> None:
    d = date(2014, 5, 15)
    listing = lh(
        [("OLDNAME", "INE009", "EQ", date(2010, 1, 4), d), ("NEWNAME", "INE001", "EQ", nth(d, 1), date(2016, 12, 30))]
    )
    sc = pd.DataFrame({"old_symbol": ["OLDNAME"], "new_symbol": ["NEWNAME"], "effective_date": [nth(d, 3)]})
    res = stitch(listing, sc, COMPANY, CAL)
    assert by_symbol(res, "OLDNAME") == by_symbol(res, "NEWNAME")
    assert set(res.segments["company_id"].dropna()) == {1}
    assert set(res.segments["link_method"]) == {"seed", "symbol_change"}


def test_symbol_change_too_far_is_not_linked() -> None:
    d = date(2014, 5, 15)
    listing = lh(
        [("OLDNAME", "INE009", "EQ", date(2010, 1, 4), d), ("NEWNAME", "INE001", "EQ", nth(d, 1), date(2016, 12, 30))]
    )
    sc = pd.DataFrame({"old_symbol": ["OLDNAME"], "new_symbol": ["NEWNAME"], "effective_date": [nth(d, 15)]})
    res = stitch(listing, sc, COMPANY, CAL)
    assert by_symbol(res, "OLDNAME") != by_symbol(res, "NEWNAME")
    assert res.stats["symbol_change_unmatched"] == 1


def test_same_isin_across_symbols_links() -> None:
    d = date(2013, 3, 1)
    listing = lh(
        [("ZOMA", "INE003", "EQ", date(2010, 1, 4), d), ("LIVE2", "INE003", "EQ", nth(d, 1), date(2016, 12, 30))]
    )
    res = stitch(listing, NO_CHANGES, COMPANY, CAL)
    assert by_symbol(res, "ZOMA") == by_symbol(res, "LIVE2")
    assert res.segments.set_index("symbol").loc["ZOMA", "link_method"] == "isin"


def test_face_value_split_isin_change_is_contiguity_but_reuse_is_rejected() -> None:
    split = date(2015, 1, 6)
    listing = lh(
        [
            ("SPLITCO", "", "EQ", date(2010, 1, 4), date(2011, 6, 21)),  # pre-2011-06 blank ISIN
            ("SPLITCO", "INE002A", "EQ", date(2011, 6, 22), split),
            ("SPLITCO", "INE002B", "EQ", nth(split, 1), date(2016, 12, 30)),  # new ISIN after FV split
            ("REUSED", "INE777", "BE", date(2010, 1, 4), date(2011, 3, 1)),
            ("REUSED", "INE888", "EQ", date(2012, 3, 1), date(2016, 12, 30)),  # same symbol, a year later
        ]
    )
    res = stitch(listing, NO_CHANGES, COMPANY, CAL)
    assert len(by_symbol(res, "SPLITCO")) == 1
    assert res.segments.loc[res.segments["symbol"] == "SPLITCO", "company_id"].tolist() == [2, 2, 2]
    assert len(by_symbol(res, "REUSED")) == 2  # symbol reuse after a > 10-session gap: two securities
    assert res.stats["contiguity_rejected_reuse"] == 1
    reused = res.segments[res.segments["symbol"] == "REUSED"]
    assert reused["company_id"].isna().all()
    assert set(reused["link_method"]) == {"own"}


def test_company_by_symbol_only_for_latest_use() -> None:
    listing = lh([("LIVE2", "INE555", "EQ", date(2012, 1, 2), date(2016, 12, 30))])  # ISIN unknown to company
    res = stitch(listing, NO_CHANGES, COMPANY.assign(isin=["X", "Y", "Z"]), CAL)
    assert res.segments["company_id"].tolist() == [3]
    assert res.segments["company_link"].tolist() == ["symbol"]


def test_overlapping_segments_are_trimmed_and_ids_stable() -> None:
    d = date(2013, 3, 1)
    listing = lh(
        [("AAA", "INE003", "EQ", date(2010, 1, 4), nth(d, 2)), ("LIVE2", "INE003", "EQ", d, date(2016, 12, 30))]
    )
    a = stitch(listing, NO_CHANGES, COMPANY, CAL)
    b = stitch(listing.iloc[::-1].reset_index(drop=True), NO_CHANGES, COMPANY, CAL)
    assert (
        a.segments.sort_values("symbol")
        .reset_index(drop=True)
        .equals(b.segments.sort_values("symbol").reset_index(drop=True))
    )
    aaa = a.segments.set_index("symbol").loc["AAA"]
    assert aaa["valid_to"] < d
    assert a.stats["segments_trimmed"] == 1
    bhav = pd.DataFrame(
        {"symbol": ["AAA", "LIVE2", "AAA"], "series": "EQ", "date": [nth(d, -1), d, nth(d, 1)],
         "close": [10.0, 10.5, 9.9], "turnover_lacs": [1.0, 5.0, 0.5]}
    )  # fmt: skip
    px, st = assign_security(bhav, a.segments)
    assert len(px) == 2  # AAA after its trimmed valid_to is dropped (unassigned)
    assert st["unassigned_rows"] == 1
    assert px["security_id"].nunique() == 1
