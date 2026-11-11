# ruff: noqa: E501
"""PT3 unit tests: annual-report extraction rules, T-24-style availability, the PIT annual features and multi-call
trends on a synthetic store (no-fill, windows, poison_after = synthetic future-poison)."""

from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from ideation.config import REPO_ROOT
from ideation.features.annual_text import build_annual, load_params
from ideation.features.registry import load_registry
from ideation.features.text import build_text_trend, load_trend_params
from ideation.pit.contract import ANNUAL_DOCS_FILE, TEXT_DOCS_FILE, LeakageError, PitStore
from ideation.text import annual as A

REG = load_registry(REPO_ROOT / "config")
PAR = load_params(REPO_ROOT / "config")
TPAR = load_trend_params(REPO_ROOT / "config")
DAYS = [d.date() for d in pd.bdate_range("2019-01-01", "2025-12-31")]


# ── extraction ───────────────────────────────────────────────────────────────────────────────────────────────
MDA = (
    "Management Discussion and Analysis\n"
    + "The Indian economy and the industry saw strong growth and robust demand. Our outlook is positive and we "
    "expect further growth across every segment and sector. Margins improved and profits grew well. "
    "However there is uncertainty and risk from volatile commodity prices; litigation may arise. " * 25
    + "\nReport on Corporate Governance\n"
)


def _auditor(qualified: bool, eom: bool, gc: bool = False) -> str:
    head = "Independent Auditor’s Report\nTo the Members of Example Limited\nReport on the Audit of the Financial Statements\n"
    head += "Qualified Opinion\n" if qualified else "Opinion\n"
    body = "We have audited the accompanying financial statements which give a true and fair view. " * 70
    if qualified:
        body += "\nBasis for Qualified Opinion\nExcept for the effects of the matters described in the basis section.\n"
    if eom:
        body += "\nEmphasis of Matter\nWe draw attention to note 12. Our opinion is not modified.\n"
    if gc:
        body += "\nMaterial Uncertainty Related to Going Concern\nThe company has incurred losses.\n"
    body += "\nKey Audit Matters\nRevenue recognition. Principal audit procedures included testing controls.\n"
    return head + body + "\nBalance Sheet as at 31st March 2023\n"


def _pages(*, qualified=False, eom=False, gc=False, pad=9000, cover="Dear Sir/Madam\nAugust 08, 2023\nAnnual Report 2022-23") -> list[str]:
    notice = (
        "NOTICE is hereby given that the 45th Annual General Meeting of the Company will be held on Friday, "
        "29th September, 2023 at 11:00 AM through video conferencing.\n"
    )
    filler = "lorem ipsum dolor sit amet consectetur " * (pad // 6)
    return [cover, MDA, _auditor(qualified, eom, gc), "Statement of the year ended 31st March, 2023 and year ended 31st "
            "March, 2023 and year ended 31st March, 2023.", filler, notice]


def test_sections_flags_and_dates() -> None:
    row = A.extract_annual(_pages(qualified=True, eom=True, gc=True))
    assert row["doc_ok"] == 1 and row["mda_ok"] == 1 and row["aud_ok"] == 1
    assert (row["aud_qual"], row["aud_eom"], row["aud_gc"], row["aud_kam"]) == (1, 1, 1, 1)
    assert row["aud_flag"] == 3 and row["aud_kam_n"] >= 1
    assert row["mda_n_tok"] >= 300 and row["mda_tone_net"] > 0 and row["mda_unc_r"] > 0
    assert A._load_dates(row["cover_dates"])[0] == date(2023, 8, 8)
    assert A._load_dates(row["agm_dates"])[0] == date(2023, 9, 29)
    assert A._load_dates(row["fy_cands"]) == [date(2023, 3, 31)]
    clean_row = A.extract_annual(_pages())
    assert (clean_row["aud_qual"], clean_row["aud_eom"], clean_row["aud_gc"], clean_row["aud_flag"]) == (0, 0, 0, 0)


def test_missing_section_is_nan_not_filled() -> None:
    row = A.extract_annual(["short document without any headings " * 2000])
    assert row["mda_ok"] == 0 and row["aud_ok"] == 0
    assert math.isnan(row["mda_tone_net"]) and row["aud_flag"] is None and row["aud_qual"] is None


def test_quality_filter() -> None:
    assert A.extract_annual(_pages(pad=60))["doc_ok"] == 0  # too few words (this fixture is about 4k words)
    garbled = ["�ऀ" * 50000 + " word" * 9000]
    q = A.extract_annual(garbled)
    assert q["garble_rate"] > A.MAX_GARBLE and q["doc_ok"] == 0
    spaced = ["a b c d e f g h " * 3000]
    assert A.extract_annual(spaced)["frag_rate"] > A.MAX_FRAG


def test_toc_hit_is_not_a_section() -> None:
    toc = "Contents\nManagement Discussion and Analysis 12\nIndependent Auditor's Report 58\nBalance Sheet 68\n"
    row = A.extract_annual([toc, "plain text " * 10000])
    assert row["mda_ok"] == 0 and row["aud_ok"] == 0


# ── availability (T-24 style) ────────────────────────────────────────────────────────────────────────────────
def test_annual_availability() -> None:
    c = lambda *a: date(*a)  # noqa: E731
    # fixed lag binds: FY end 2023-03-31 + 180 d = 2023-09-27; a cover letter of 2023-08-08 is earlier
    r = A.availability(2023, 3, [c(2023, 8, 8)], [], [])
    assert r["available_from"] == c(2023, 3, 31) + timedelta(days=180) and r["avail_basis"] == "lag"
    assert r["filed_date"] == c(2023, 8, 8)
    # a later AGM date wins (parsed + 1)
    r = A.availability(2023, 3, [c(2023, 8, 8)], [c(2023, 11, 15)], [])
    assert r["available_from"] == c(2023, 11, 16) and r["avail_basis"] == "agm"
    # the cover letter and AGM disagree: the later parsed date is used
    assert A.availability(2023, 3, [c(2023, 10, 5)], [c(2023, 9, 29)], [])["available_from"] == c(2023, 10, 6)
    # dates outside the plausibility windows are ignored (before FY end + 30 d, or a year later)
    r = A.availability(2023, 3, [c(2023, 4, 10)], [c(2025, 1, 1)], [])
    assert r["available_from"] == c(2023, 9, 27) and r["filed_date"] is None and r["agm_date"] is None
    # fiscal-year end: the LATER of the label-based end and a text-parsed 'year ended' date inside the window
    assert A.resolve_fy_end(2023, 3, [c(2022, 3, 31)]) == (c(2023, 3, 31), "label")
    assert A.resolve_fy_end(2023, 3, [c(2023, 4, 30)]) == (c(2023, 4, 30), "text")
    assert A.resolve_fy_end(2023, 3, [c(2023, 12, 31)]) == (c(2023, 12, 31), "text")  # later = conservative
    assert A.resolve_fy_end(2023, 3, [c(2024, 9, 30)]) == (c(2023, 3, 31), "label")  # beyond +366 d: stray date
    # a December-year-end company whose label is the publication year: label end (Dec 2023) is the conservative one
    assert A.availability(2023, 12, [], [], [c(2022, 12, 31)])["fy_end"] == c(2023, 12, 31)
    # never earlier than the lag
    assert A.availability(2020, 3, [c(2020, 7, 1)], [], [], lag=120)["available_from"] == c(2020, 7, 29)


# ── PIT store fixtures ───────────────────────────────────────────────────────────────────────────────────────
def _w(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), path)


def _ar(cid: int, fy: int, tone: float, aud: float, *, rpt=0.5, cl=0.1, unc=3.0, ok=1, lag=180, parsed=None) -> dict:
    fy_end = date(fy, 3, 31)
    av = A.availability(fy, 3, [parsed] if parsed else [], [], [], lag)
    return {"company_id": cid, "fiscal_year": fy, "fy_end": fy_end, "doc_ok": ok, "read_ok": True,
            "mda_tone_net": tone, "mda_unc_r": unc, "aud_flag": aud, "rpt_r": rpt, "cl_r": cl,
            "avail_basis": av["avail_basis"], "available_from": av["available_from"]}  # fmt: skip


def _tdoc(cid: int, dd: date, tone: float, guide: float) -> dict:
    return {"company_id": cid, "doc_type": "concall_transcript", "doc_date": dd, "source": "bse", "tone_net": tone,
            "guide_net": guide, "read_ok": True, "available_from": dd + timedelta(days=40)}  # fmt: skip


def _store(tmp_path: Path, annual: list[dict], texts: list[dict] | None = None) -> PitStore:
    cal = pd.DataFrame({"session": DAYS, "in_bhav": True, "in_index": True, "bhav_missing": False, "n_eq": 1})
    _w(tmp_path / "snap" / "derived" / "calendar.parquet", cal.assign(available_from=cal["session"], role="data"))
    _w(tmp_path / "text" / ANNUAL_DOCS_FILE, pd.DataFrame(annual))
    _w(tmp_path / "text" / TEXT_DOCS_FILE, pd.DataFrame(texts or [_tdoc(99, date(2020, 1, 1), 0.5, 0.0)]))
    return PitStore(tmp_path / "snap", text_dir=tmp_path / "text")


def _members(*cids: int) -> pd.DataFrame:
    return pd.DataFrame({"security_id": [100 + c for c in cids], "company_id": list(cids)})


def test_cur_prev_levels_deltas_no_fill(tmp_path: Path) -> None:
    docs = [
        _ar(1, 2021, 0.40, 0, rpt=0.4), _ar(1, 2022, 0.55, 1, rpt=0.6),  # cur FY2022, prev FY2021
        _ar(2, 2022, 0.30, 2),  # cur only: deltas NaN
        _ar(3, 2020, 0.50, 0),  # too old at the anchor (fy_end 2020-03-31 < A - 600 d)
        _ar(4, 2022, 0.70, 0, ok=0),  # fails the quality filter: not cur
        _ar(5, 2023, 0.90, 0),  # FY2023: available 2023-09-27, invisible at the anchor below
    ]
    st = _store(tmp_path, docs)
    a = date(2023, 6, 30)
    f = build_annual(st.as_of(a), _members(1, 2, 3, 4, 5, 6), REG, PAR).set_index("company_id")
    assert f.loc[1, "ar_tone"] == pytest.approx(0.55) and f.loc[1, "d_ar_tone"] == pytest.approx(0.15)
    assert f.loc[1, "d_ar_aud"] == 1 and f.loc[1, "d_ar_rpt"] == pytest.approx(0.2)
    assert f.loc[2, "isna_ar"] == 0 and f.loc[2, "isna_ar_prev"] == 1 and np.isnan(f.loc[2, "d_ar_tone"])
    for c in (3, 4, 5, 6):  # uncovered: every value NaN, no fill
        assert f.loc[c, "isna_ar"] == 1
        assert f.loc[c, [*REG.names(source="features_annual_text.yaml")]].isna().all()
    assert f["ar_tone_rg"].notna().sum() == 2 and f.loc[[3, 4, 5, 6], "ar_tone_rg"].isna().all()
    # company 5 turns visible exactly on available_from (2023-09-27), not a day earlier
    g = build_annual(st.as_of(date(2023, 9, 26)), _members(5), REG, PAR).set_index("company_id")
    assert g.loc[5, "isna_ar"] == 1
    g = build_annual(st.as_of(date(2023, 9, 27)), _members(5), REG, PAR).set_index("company_id")
    assert g.loc[5, "isna_ar"] == 0 and g.loc[5, "ar_tone"] == pytest.approx(0.90)


def test_section_nan_propagates(tmp_path: Path) -> None:
    d = _ar(1, 2022, np.nan, np.nan)
    st = _store(tmp_path, [d, _ar(1, 2021, 0.4, 0)])
    f = build_annual(st.as_of(date(2023, 6, 30)), _members(1), REG, PAR).set_index("company_id")
    assert f.loc[1, "isna_ar"] == 0 and np.isnan(f.loc[1, "ar_tone"]) and np.isnan(f.loc[1, "d_ar_aud"])
    assert f.loc[1, "ar_rpt"] == 0.5


def test_annual_future_poison(tmp_path: Path) -> None:
    docs = [_ar(c, fy, 0.3 + 0.05 * c + 0.01 * (fy - 2018), c % 3) for c in (1, 2, 3) for fy in range(2018, 2026)]
    st = _store(tmp_path, docs)
    a = date(2023, 6, 30)
    base = build_annual(st.as_of(a), _members(1, 2, 3), REG, PAR)
    assert base["isna_ar"].eq(0).all() and base["d_ar_tone"].notna().all()
    for mode in ("nan", "shuffle", "inflate"):
        got = build_annual(st.poison_after(a, mode).as_of(a), _members(1, 2, 3), REG, PAR)  # type: ignore[arg-type]
        pd.testing.assert_frame_equal(got, base)
    early = st.poison_after(date(2022, 1, 1), "inflate")  # positive control: poison reaches the dataset
    changed = build_annual(early.as_of(a), _members(1, 2, 3), REG, PAR)
    assert not changed.drop(columns=["anchor"]).equals(base.drop(columns=["anchor"]))


def test_annual_docs_guard(tmp_path: Path) -> None:
    st = _store(tmp_path, [_ar(1, 2022, 0.5, 0)])
    v = st.as_of(date(2023, 6, 30))
    assert len(v.annual_docs()) == 1
    assert len(st.as_of(date(2022, 9, 26)).annual_docs()) == 0  # FY2022 report: available 2022-09-27
    with pytest.raises(LeakageError):
        v._guard(pd.DataFrame({"available_from": [date(2024, 1, 1)]}), "x")


# ── multi-call trends ────────────────────────────────────────────────────────────────────────────────────────
def test_trend_features(tmp_path: Path) -> None:
    T = [
        _tdoc(1, date(2022, 7, 1), 0.30, 0.0), _tdoc(1, date(2022, 10, 1), 0.40, 0.1),
        _tdoc(1, date(2023, 1, 1), 0.50, 0.2), _tdoc(1, date(2023, 4, 1), 0.60, 0.3),  # steady improvement
        _tdoc(2, date(2022, 7, 1), 0.60, 0.3), _tdoc(2, date(2022, 10, 1), 0.50, 0.2),
        _tdoc(2, date(2023, 1, 1), 0.40, 0.1), _tdoc(2, date(2023, 4, 1), 0.45, 0.2),  # latest call improved once
        _tdoc(3, date(2023, 1, 1), 0.50, 0.2), _tdoc(3, date(2023, 4, 1), 0.60, 0.3),  # only two calls
        _tdoc(4, date(2020, 1, 1), 0.5, 0.0), _tdoc(4, date(2020, 4, 1), 0.5, 0.0), _tdoc(4, date(2020, 7, 1), 0.5, 0.0),
    ]  # fmt: skip
    st = _store(tmp_path, [_ar(1, 2022, 0.5, 0)], T)
    a = date(2023, 8, 31)
    f = build_text_trend(st.as_of(a), _members(1, 2, 3, 4, 5), REG, TPAR).set_index("company_id")
    assert f.loc[1, "impr_streak"] == 3 and f.loc[1, "tone_slope4"] > 0.3 and f.loc[1, "guide_slope4"] > 0.1
    assert f.loc[2, "impr_streak"] == 1 and f.loc[2, "tone_slope4"] < 0
    for c in (3, 4, 5):  # < 3 calls in the window / stale / no calls: NaN, flagged, no fill
        assert f.loc[c, "isna_trend"] == 1 and f.loc[c, ["tone_slope4", "guide_slope4", "impr_streak"]].isna().all()
    # the 2023-04-01 call (available 2023-05-11) is not visible on 2023-05-10
    g = build_text_trend(st.as_of(date(2023, 5, 10)), _members(1), REG, TPAR).set_index("company_id")
    assert g.loc[1, "trend_n_calls"] == 3 and g.loc[1, "impr_streak"] == 2
    # future poison does not change the output
    base = build_text_trend(st.as_of(a), _members(1, 2, 3, 4, 5), REG, TPAR)
    for mode in ("nan", "shuffle", "inflate"):
        got = build_text_trend(st.poison_after(a, mode).as_of(a), _members(1, 2, 3, 4, 5), REG, TPAR)  # type: ignore[arg-type]
        pd.testing.assert_frame_equal(got, base)


def test_registry_sets() -> None:
    assert len(REG.names(source="features_annual_text.yaml")) == 10
    assert set(REG.names(source="features_text_trend.yaml")) == {"tone_slope4", "guide_slope4", "impr_streak"}
    assert all(not REG.features[n].complete for n in REG.names(sets=["annual", "text_trend"]))


def test_trend_empty_anchor(tmp_path: Path) -> None:
    st = _store(tmp_path, [_ar(1, 2022, 0.5, 0)], [_tdoc(1, date(2023, 1, 1), 0.5, 0.0)])  # one call only
    f = build_text_trend(st.as_of(date(2023, 8, 31)), _members(1, 2), REG, TPAR)
    assert f["isna_trend"].eq(1).all() and f[["tone_slope4", "guide_slope4", "impr_streak"]].isna().all().all()
