"""PT2 unit tests on a synthetic store: T-24 ``test_text_availability`` (feature level), cur/prev windows, no fill,
poison_after on the text dataset (synthetic T-25), vendor-surprise features, the vendor flag and the clustered gate."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from ideation.config import REPO_ROOT
from ideation.features import events_vendor as ev
from ideation.features.registry import load_registry
from ideation.features.text import build_text, load_params
from ideation.pit.contract import TEXT_DOCS_FILE, LeakageError, PitStore, VendorGateError
from ideation.text import extract

REG = load_registry(REPO_ROOT / "config")
PAR = load_params(REPO_ROOT / "config")
DAYS = [d.date() for d in pd.bdate_range("2024-01-01", "2025-06-30")]
TODAY = date(2026, 11, 1)


def _doc(cid: int, typ: str, dd: date, src: str, tone: float, guide: float, *, filed=None, q_end=None, evs=(), qa=1.0):
    q_end = q_end or date(dd.year, dd.month, 1) - pd.Timedelta(days=1)
    return {
        "company_id": cid, "doc_type": typ, "doc_date": dd, "source": src, "qa_ok": qa, "tone_net": tone,
        "guide_net": guide, "unc_r": tone * 2, "gdn_r": 0.5, "capex_r": 1.5, "qa_tone_net": tone - 0.1,
        "qa_minus_prep_tone": -0.1, "read_ok": True,
        "available_from": extract.available_from(dd, src, filed, q_end, evs, today=TODAY),
    }  # fmt: skip


def _w(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), path)


def _store(tmp_path: Path, docs: list[dict], vendor: pd.DataFrame | None = None) -> PitStore:
    cal = pd.DataFrame({"session": DAYS, "in_bhav": True, "in_index": True, "bhav_missing": False, "n_eq": 1})
    _w(tmp_path / "snap" / "derived" / "calendar.parquet", cal.assign(available_from=cal["session"], role="data"))
    if vendor is not None:
        _w(tmp_path / "snap" / "earnings_vendor.parquet", vendor)
    _w(tmp_path / "text" / TEXT_DOCS_FILE, pd.DataFrame(docs))
    return PitStore(tmp_path / "snap", text_dir=tmp_path / "text")


def _members(*cids: int) -> pd.DataFrame:
    return pd.DataFrame({"security_id": [100 + c for c in cids], "company_id": list(cids)})


def test_text_availability() -> None:  # T-24 (feature level); the extract-level cases are pinned in test_text_extract
    docs = [
        _doc(1, "concall_transcript", date(2024, 10, 1), "bse", 0.6, 0.2, filed=date(2024, 10, 14)),  # -> 2024-11-01
        _doc(2, "concall_transcript", date(2024, 7, 1), "tijori", 0.5, 0.1, q_end=date(2024, 6, 30)),  # >= 2024-08-15
        _doc(3, "concall_transcript", date(2024, 10, 1), "bse", 0.4, 0.0, filed=date(2024, 10, 14),
             q_end=date(2024, 9, 30), evs=[date(2024, 11, 5)]),  # event 2024-11-05 -> 2024-11-06
    ]  # fmt: skip
    assert [d["available_from"] for d in docs][0] == date(2024, 11, 1)  # noqa: RUF015
    assert docs[1]["available_from"] >= date(2024, 8, 15)
    assert docs[2]["available_from"] == date(2024, 11, 6)
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        st = _store(Path(td), docs)
        m = _members(1, 2, 3)
        # the call is invisible on its doc_date month and on the last session before available_from
        f = build_text(st.as_of(date(2024, 10, 31)), m, REG, PAR).set_index("company_id")
        assert f.loc[[1, 3], "isna_text"].tolist() == [1, 1] and f.loc[[1, 3], "tone_lvl"].isna().all()  # noqa: PT018
        # company 1 turns visible on 2024-11-01, company 3 only on 2024-11-06
        f = build_text(st.as_of(date(2024, 11, 1)), m, REG, PAR).set_index("company_id")
        assert f.loc[1, "isna_text"] == 0 and f.loc[1, "tone_lvl"] == 0.6 and f.loc[3, "isna_text"] == 1  # noqa: PT018
        f = build_text(st.as_of(date(2024, 11, 6)), m, REG, PAR).set_index("company_id")
        assert f.loc[3, "isna_text"] == 0 and f.loc[3, "tone_lvl"] == 0.4  # noqa: PT018
        # tijori: statutory floor 2024-08-15; not visible on 2024-08-14
        assert build_text(st.as_of(date(2024, 8, 14)), m, REG, PAR).set_index("company_id").loc[2, "isna_text"] == 1
        assert build_text(st.as_of(date(2024, 8, 15)), m, REG, PAR).set_index("company_id").loc[2, "isna_text"] == 0


def test_cur_prev_windows_no_fill_and_levels(tmp_path: Path) -> None:
    T = "concall_transcript"
    docs = [
        _doc(1, T, date(2023, 10, 1), "bse", 0.50, 0.10),  # 396 d before 2024-11-01-ish anchor window edge cases
        _doc(1, T, date(2024, 7, 1), "bse", 0.55, 0.00),  # too close to cur (gap 92 d is ok): prev candidate
        _doc(1, T, date(2024, 10, 1), "bse", 0.70, 0.30),  # cur
        _doc(2, T, date(2024, 10, 1), "bse", 0.60, 0.20),  # cur but no prev: d_* NaN, levels set
        _doc(2, T, date(2024, 9, 1), "bse", 0.10, 0.90),  # gap 30 d < 60: not a prev
        _doc(3, T, date(2024, 1, 1), "bse", 0.60, 0.20),  # older than 200 d at the anchor: not covered
        _doc(1, "concall_ppt", date(2024, 10, 1), "bse", 0.0, 0.5),
        _doc(1, "concall_ppt", date(2024, 4, 1), "bse", 0.0, 0.2),
    ]  # fmt: skip
    st = _store(tmp_path, docs)
    f = build_text(st.as_of(date(2024, 12, 2)), _members(1, 2, 3, 4), REG, PAR).set_index("company_id")
    assert f.loc[1, "d_tone"] == pytest.approx(0.70 - 0.55)  # latest doc in [cur-400, cur-60] is 2024-07-01
    assert f.loc[1, "d_guide"] == pytest.approx(0.30)
    assert f.loc[1, "ppt_d_guide"] == pytest.approx(0.3) and f.loc[1, "ppt_capex_r"] == 1.5  # noqa: PT018
    assert f.loc[2, "isna_text"] == 0 and f.loc[2, "isna_text_prev"] == 1 and np.isnan(f.loc[2, "d_tone"])  # noqa: PT018
    assert f.loc[2, "tone_lvl"] == 0.6
    for c in (3, 4):  # uncovered: every column NaN, no fill
        assert f.loc[c, "isna_text"] == 1
        assert f.loc[c, [*REG.names(source="features_text.yaml")]].isna().all()
    # rank-gauss only within covered names; uncovered stay NaN
    assert f["tone_lvl_rg"].notna().sum() == 2 and f.loc[[3, 4], "tone_lvl_rg"].isna().all()  # noqa: PT018
    assert f.loc[1, "tone_lvl_rg"] > f.loc[2, "tone_lvl_rg"]


def test_poison_after_text_docs(tmp_path: Path) -> None:
    T = "concall_transcript"
    docs = [_doc(c, T, date(2024, m, 1), "bse", 0.3 + 0.05 * c + 0.01 * m, 0.1 * c, filed=None)
            for c in (1, 2, 3) for m in (1, 4, 7, 10)]  # fmt: skip
    st = _store(tmp_path, docs)
    a = date(2024, 11, 29)
    base = build_text(st.as_of(a), _members(1, 2, 3), REG, PAR)
    assert base["isna_text"].eq(0).all() and base["d_tone"].notna().all()  # noqa: PT018
    for mode in ("nan", "shuffle", "inflate"):
        pois = st.poison_after(a, mode)  # type: ignore[arg-type]
        got = build_text(pois.as_of(a), _members(1, 2, 3), REG, PAR)
        pd.testing.assert_frame_equal(got, base)
    # positive control: poisoning from before the visible documents changes the output
    early = st.poison_after(date(2024, 5, 1), "inflate")
    changed = build_text(early.as_of(a), _members(1, 2, 3), REG, PAR)
    assert not changed.drop(columns=["anchor"]).equals(base.drop(columns=["anchor"]))


def test_text_docs_guard_and_unknown_type(tmp_path: Path) -> None:
    st = _store(tmp_path, [_doc(1, "concall_transcript", date(2024, 10, 1), "bse", 0.5, 0.1)])
    v = st.as_of(date(2024, 12, 2))
    assert len(v.text_docs("concall_transcript")) == 1
    with pytest.raises(LeakageError):
        v.text_docs("annual_report")


# ── vendor ───────────────────────────────────────────────────────────────────────────────────────────────────
def _vendor_store(tmp_path: Path) -> PitStore:
    rows = [
        (1, date(2024, 2, 1), 1.0, 1.2), (1, date(2024, 5, 2), 2.0, 1.0), (1, date(2024, 8, 1), 1.0, 1.0),
        (1, date(2024, 11, 5), 0.0, 0.5),  # estimate 0 -> surprise NaN, not a beat? reported > estimate -> beat
        (2, date(2024, 10, 1), -2.0, -1.0), (3, date(2024, 10, 1), 1.0, np.nan), (4, date(2025, 1, 5), 1.0, 2.0),
    ]  # fmt: skip
    vend = pd.DataFrame(rows, columns=["company_id", "event_date", "eps_estimate", "eps_reported"])
    vend["available_from"] = [d + timedelta(days=1) for d in vend["event_date"]]
    vend["role"] = "vendor"
    return _store(tmp_path, [_doc(1, "concall_transcript", date(2024, 1, 1), "bse", 0.5, 0.1)], vend)


def test_vendor_surprise_formula() -> None:
    s = ev.surprise_pct(pd.Series([1.0, -2.0, 0.0, 2.0]), pd.Series([1.2, -1.0, 0.5, 1.0]))
    assert s.iloc[0] == pytest.approx(20.0) and s.iloc[1] == pytest.approx(50.0)  # |est| in the denominator  # noqa: PT018
    assert np.isnan(s.iloc[2]) and s.iloc[3] == pytest.approx(-50.0)  # noqa: PT018


def test_vendor_features_and_flag(tmp_path: Path) -> None:
    st = _vendor_store(tmp_path)
    par = {"last_window_days": 120, "beat_window_days": 400, "beat_n": 4, "clip_pct": 100.0}
    m = _members(1, 2, 3, 4, 5)
    v = st.as_of(date(2024, 12, 2))
    with pytest.raises(ev.VendorNotAllowedError):
        ev.build_vendor(v, m, REG, par, allow_vendor_estimates=False)
    f = ev.build_vendor(v, m, REG, par, allow_vendor_estimates=True).set_index("company_id")
    assert np.isnan(f.loc[1, "f_vsurp_last"]) and f.loc[1, "isna_vsurp"] == 0  # latest event: estimate 0  # noqa: PT018
    assert f.loc[1, "f_vbeat_4q"] == pytest.approx(0.5)  # beats: +, -, (tie = no beat), + -> 2 of 4
    assert f.loc[2, "f_vsurp_last"] == pytest.approx(50.0)
    assert f.loc[3, "isna_vsurp"] == 1 and np.isnan(f.loc[3, "f_vbeat_4q"])  # no reported EPS: not an event  # noqa: PT018
    assert f.loc[4, "isna_vsurp"] == 1  # event after the anchor is invisible
    assert f.loc[5, "isna_vsurp"] == 1
    # window: at 2024-12-02 the 2024-08-01 event is inside 120 d only for company 1's older rows
    st2 = _vendor_store(tmp_path / "b")
    g = ev.build_vendor(st2.as_of(date(2024, 6, 3)), m, REG, par, allow_vendor_estimates=True).set_index("company_id")
    assert g.loc[1, "f_vsurp_last"] == pytest.approx(-50.0)  # 2024-05-02: (1-2)/2


def test_earnings_vendor_gate_blocks_other_callers(tmp_path: Path) -> None:
    v = _vendor_store(tmp_path).as_of(date(2024, 12, 2))
    with pytest.raises(VendorGateError):
        v.earnings_vendor()


def test_clustered_spearman_detects_and_rejects_signal() -> None:
    rng = np.random.default_rng(0)
    n = 4000
    q = rng.integers(0, 40, n)
    x = rng.normal(size=n)
    r = ev.clustered_spearman(x, 0.5 * x + rng.normal(size=n), q)
    assert r["rho"] > 0.3 and r["t"] > 10 and r["clusters"] == 40  # noqa: PT018
    r0 = ev.clustered_spearman(x, rng.normal(size=n), q)
    assert abs(r0["rho"]) < 0.05 and abs(r0["t"]) < 3  # noqa: PT018
