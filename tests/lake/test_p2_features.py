"""P2 acceptance on the real snapshot (01 P2, 02 T-01, 05 §10.3). Needs `ideation snapshot --full`.

* Golden values (01 P2 acceptance #2): TCS, HDFCBANK (fin_layout), BIOCON, SIEMENS (September FY) and IDEA
  (loss-maker, negative equity in 2024) at the 2019-06-28 and 2024-06-28 anchors. The expected numbers were derived
  by hand from the raw ``financial_fact`` rows with formulas written independently of ``ideation.features`` (script
  kept outside the repo); ``RAW`` pins the raw rows they came from, so a snapshot change fails loudly instead of
  silently re-baselining. Snapshot ``20261105-e33e7986``. Market cap is the P1 hierarchy value (method A for all ten).
* T-01 on fundamentals: bit-identical features (mcap, feature_complete and c_i included) when every row with
  ``available_from`` after the anchor is poisoned, in all three modes.
* Availability: a late filer (matched event later than the statutory deadline) enters the features only on its
  ``available_from`` session.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from ideation.config import REPO_ROOT, load_methodology
from ideation.features.build import build_anchor
from ideation.features.registry import load_registry
from ideation.pit.contract import PitStore
from ideation.pit.lags import results_deadline
from ideation.universe.build import UniverseConfig, build_universe

REG = load_registry(REPO_ROOT / "config")
UCFG = UniverseConfig.from_methodology(load_methodology().data)
SNAP = "20261105-e33e7986"
RTOL = 1e-9

# (company_id, anchor) -> expected features. mcap in ₹ crore (P1 hierarchy), ratios as fractions.
GOLDEN: dict[tuple[int, str], dict] = {
    (3365, "2019-06-28"): dict(
        layout="core",
        mcap=838569.750036,
        fy_end="2019-03-31",
        roe=0.360557700459,
        roa=0.28820191118,
        ep=0.0375305691609,
        eps_g1=0.243255262378,
        roe_3y=0.332098680122,
        roe_vol3=0.0241777197026,
        eps_g3=0.112601407632,
        rev_g1=0.189750129971,
        opm=0.269733652868,
        sp=0.174658100884,
        fcf_yield=0.0315549183581,
        roic=0.367550395806,
        ebitda_ev=0.0478143824978,
        nd_ebitda=-0.323647040956,
        rev_g3=0.106362746112,
    ),  # TCS
    (3365, "2024-06-28"): dict(
        layout="core",
        mcap=1412556.6011,
        fy_end="2024-03-31",
        roe=0.507514661743,
        roa=0.3197644374,
        ep=0.0324999366143,
        eps_g1=0.101484503863,
        roe_3y=0.471183389849,
        roe_vol3=0.0289787410131,
        eps_g3=0.131990621554,
        rev_g1=0.0684606445546,
        opm=0.266906883969,
        sp=0.170536883132,
        fcf_yield=0.0295124457083,
        roic=0.509450041787,
        ebitda_ev=0.0456608292562,
        nd_ebitda=-0.0818868980963,
        rev_g3=0.137399555202,
    ),  # TCS
    (1298, "2019-06-28"): dict(
        layout="fin_layout",
        mcap=664875.91872,
        fy_end="2019-03-31",
        roe=0.169649639916,
        roa=0.0187362896036,
        ep=0.0335882220595,
        eps_g1=0.149747616377,
        roe_3y=0.179152367331,
        roe_vol3=0.00671979330834,
        eps_g3=0.17443815061,
        rev_g1=0.233010505581,
        bp=0.231130344284,
        ta_g1=0.171883979673,
        dep_g1=0.170132234026,
        cost_ratio=0.280852161021,
        fin_margin=0.16,
        lev=8.41270750229,
    ),  # HDFCBANK
    (1298, "2024-06-28"): dict(
        layout="fin_layout",
        mcap=1281053.57527,
        fy_end="2024-03-31",
        roe=0.171786215163,
        roa=0.019951144906,
        ep=0.0500072762269,
        eps_g1=0.0228044638525,
        roe_3y=0.16988540384,
        roe_vol3=0.00241069357196,
        eps_g3=0.137544790004,
        rev_g1=0.661155814798,
        bp=0.356266130325,
        ta_g1=0.592690101927,
        dep_g1=0.262513259144,
        cost_ratio=0.426956212699,
        fin_margin=-0.16,
        lev=8.83047616544,
    ),  # HDFCBANK
    (487, "2019-06-28"): dict(
        layout="core",
        mcap=30481.1609491,
        fy_end="2019-03-31",
        roe=0.160475219434,
        roa=0.0904336849698,
        ep=0.0296904701731,
        eps_g1=1.43225806452,
        roe_3y=0.124232085184,
        roe_vol3=0.0365120860703,
        eps_g3=0.383737437627,
        rev_g1=0.337375697308,
        opm=0.252811026478,
        sp=0.180898621585,
        fcf_yield=-0.0110560093352,
        roic=0.1155951417,
        ebitda_ev=0.0429515663837,
        nd_ebitda=0.979196556671,
        rev_g3=0.18651136154,
    ),  # BIOCON
    (487, "2024-06-28"): dict(
        layout="core",
        mcap=42025.6161382,
        fy_end="2024-03-31",
        roe=0.0542895086321,
        roa=0.0241499604633,
        ep=0.0243185012836,
        eps_g1=1.21298701299,
        roe_3y=0.0567341275907,
        roe_vol3=0.0186522813568,
        eps_g3=0.267050856381,
        rev_g1=0.320565598711,
        opm=0.217945242613,
        sp=0.351119182916,
        fcf_yield=0.0248895815485,
        roic=0.0416113067903,
        ebitda_ev=0.0522632658141,
        nd_ebitda=4.35883084577,
        rev_g3=0.277216567514,
    ),  # BIOCON
    (3061, "2019-06-28"): dict(
        layout="core",
        mcap=46559.8741059,
        fy_end="2018-09-30",
        roe=0.112702482957,
        roa=0.0655892844144,
        ep=0.019351426895,
        eps_g1=-0.207080200501,
        roe_3y=0.252843996699,
        roe_vol3=0.167907761948,
        eps_g3=0.221313992347,
        rev_g1=0.156348847718,
        opm=0.103712387651,
        sp=0.274807444086,
        fcf_yield=-0.00169673998302,
        roic=0.175762588178,
        ebitda_ev=0.0309555821854,
        nd_ebitda=-2.78221552374,
        rev_g3=0.067775827051,
    ),  # SIEMENS
    (3061, "2024-06-28"): dict(
        layout="core",
        mcap=274372.654,
        fy_end="2023-09-30",
        roe=0.158804713123,
        roa=0.0939767692492,
        ep=0.00714721373071,
        eps_g1=0.270943918763,
        roe_3y=0.136378635335,
        roe_vol3=0.0202286535837,
        eps_g3=0.368312011931,
        rev_g1=0.211674309084,
        opm=0.127186253452,
        sp=0.0712680353342,
        fcf_yield=0.00486200056949,
        roic=0.296300952029,
        ebitda_ev=0.00931789927537,
        nd_ebitda=-3.00603136309,
        rev_g3=0.253800315933,
    ),  # SIEMENS
    (1388, "2019-06-28"): dict(
        layout="core",
        mcap=34913.5109222,
        fy_end="2019-03-31",
        roe=-0.336122075561,
        roa=-0.0890045495684,
        ep=-0.418290788129,
        eps_g1=0.121107266436,
        roe_3y=-0.171005748591,
        roe_vol3=0.130675386942,
        eps_g3=-2.88401553827,
        rev_g1=0.311644683334,
        opm=0.11096732449,
        sp=1.06239673468,
        fcf_yield=-0.0641012588217,
        roic=-0.0772418291933,
        ebitda_ev=0.0257471246777,
        nd_ebitda=30.3568999028,
        rev_g3=0.0320528955052,
    ),  # IDEA
    (1388, "2024-06-28"): dict(
        layout="core",
        mcap=89696.9566613,
        fy_end="2024-03-31",
        roe=None,
        roa=-0.159280032633,
        ep=-0.348261537099,
        eps_g1=-0.0348837209302,
        roe_3y=None,
        roe_vol3=None,
        eps_g3=0.236365670698,
        rev_g1=0.0112620622614,
        opm=0.400262590265,
        sp=0.475512231268,
        fcf_yield=0.215124355588,
        roic=-0.0357699116218,
        ebitda_ev=0.0512718930296,
        nd_ebitda=14.2498242737,
        rev_g3=0.00813678069505,
    ),  # IDEA
}

# Raw rows (₹ crore; EPS in ₹) of the latest available FY: (period_end, available_from, Profit for EPS, top line,
# EPS, Total Assets). Top line = Revenue for HDFCBANK (fin_layout), Sales otherwise.
RAW: dict[tuple[int, str], tuple[str, str, float, float, float, float]] = {
    (3365, "2019-06-28"): ("2019-03-31", "2019-05-31", 31472.0, 146463.0, 83.87, 113901.0),
    (3365, "2024-06-28"): ("2024-03-31", "2024-05-31", 45908.0, 240893.0, 126.88, 145472.0),
    (1298, "2019-06-28"): ("2019-03-31", "2019-05-31", 22332.0, 105161.0, 20.5, 1292806.0),
    (1298, "2024-06-28"): ("2024-03-31", "2024-05-31", 64062.0, 283649.0, 42.16, 4030194.0),
    (487, "2019-06-28"): ("2019-03-31", "2019-05-31", 905.0, 5514.0, 7.54, 12192.0),
    (487, "2024-06-28"): ("2024-03-31", "2024-05-31", 1022.0, 14756.0, 8.52, 55753.0),
    (3061, "2019-06-28"): ("2018-09-30", "2018-11-30", 901.0, 12795.0, 25.31, 14336.0),
    (3061, "2024-06-28"): ("2023-09-30", "2023-11-30", 1961.0, 19554.0, 55.07, 21646.0),
    (1388, "2019-06-28"): ("2019-03-31", "2019-05-31", -14604.0, 37092.0, -5.08, 229652.0),
    (1388, "2024-06-28"): ("2024-03-31", "2024-05-31", -31238.0, 42652.0, -6.23, 184997.0),
}  # fmt: skip

_CACHE: dict[tuple[str, date], pd.DataFrame] = {}


@pytest.fixture(scope="module")
def golden_store(settings) -> PitStore:
    """The snapshot the golden values were derived from, whatever ``PitStore.latest`` is by now."""
    d = settings.data_dir / "snapshots" / SNAP
    if not (d / "manifest.json").is_file():
        pytest.skip(f"pinned golden snapshot {SNAP} is not on disk")
    return PitStore(d)


def features(store: PitStore, a: date, tag: str = "clean") -> pd.DataFrame:
    key = (tag + store.snap_id, a)
    if key not in _CACHE:
        _CACHE[key] = build_anchor(store, REG, UCFG, a)[0]
    return _CACHE[key]


def test_registry_resolves_against_live_catalog(pit_store: PitStore) -> None:
    ids = REG.validate_metrics(pit_store.table("metric"))
    assert len(ids) == len(REG.lines)
    assert len(set(ids.values())) == len(ids)


@pytest.mark.parametrize(("cid", "anchor"), sorted(GOLDEN))
def test_golden_values_from_raw_rows(golden_store: PitStore, cid: int, anchor: str) -> None:
    pit_store = golden_store
    a = date.fromisoformat(anchor)
    # 1. the raw rows the numbers were derived from are still what the snapshot holds
    ff = pd.read_parquet(pit_store.snapshot_dir / "financial_fact.parquet")
    ff = ff[(ff["company_id"] == cid) & (ff["period_type"] == "A") & (ff["available_from"] <= a)]
    pe, af, npeps, top, eps, ta = RAW[(cid, anchor)]
    cur = ff[ff["period_end"] == date.fromisoformat(pe)]
    cur = cur[cur["basis"] == ("C" if (cur["basis"] == "C").any() else "S")]
    val = {int(m): float(v) for m, v in zip(cur["metric_id"], cur["value"], strict=True)}
    assert (val[70], val[22], val[33]) == (npeps, eps, ta)
    assert val[123 if GOLDEN[(cid, anchor)]["layout"] == "fin_layout" else 12] == top
    assert cur["available_from"].max() == date.fromisoformat(af)
    # 2. the builder reproduces the hand-derived values
    row = features(pit_store, a).set_index("company_id").loc[cid]
    exp = dict(GOLDEN[(cid, anchor)])
    assert row["layout"] == exp.pop("layout")
    assert str(row["fy_end"]) == exp.pop("fy_end")
    for k, v in exp.items():
        got = row["mcap_cr"] if k == "mcap" else row[k]
        if v is None:
            assert np.isnan(got), k
        else:
            assert got == pytest.approx(v, rel=RTOL), (cid, anchor, k)
    assert row["feature_available_from"] <= a


@pytest.mark.parametrize("mode", ["inflate", "nan", "shuffle"])
@pytest.mark.parametrize("anchor", [date(2019, 6, 28), date(2023, 6, 30)])
def test_t01_future_poison_fundamentals(pit_store: PitStore, anchor: date, mode: str) -> None:
    clean = features(pit_store, anchor)
    bad_store = pit_store.poison_after(anchor, mode, seed=7)  # type: ignore[arg-type]
    ff = bad_store.table("financial_fact")
    assert (ff["available_from"] > anchor).any()  # the poison exists in the data the builder reads
    bad = build_anchor(bad_store, REG, UCFG, anchor)[0]
    pd.testing.assert_frame_equal(clean, bad, check_exact=True)
    assert clean["mcap_cr"].notna().sum() > 800
    assert clean["feature_complete"].notna().sum() > 800


def test_t01_positive_control_poisoning_the_past_changes_features(pit_store: PitStore) -> None:
    a = date(2019, 6, 28)
    old = pit_store.poison_after(a - timedelta(days=900), "inflate", seed=7)
    changed = build_anchor(old, REG, UCFG, a)[0]
    assert not changed.equals(features(pit_store, a))


def test_late_filer_enters_on_its_available_from_session(pit_store: PitStore) -> None:
    """A fact whose matched results event is later than deadline + 1 is invisible until that date (later wins)."""
    ff = pit_store.table("financial_fact")
    a = ff[(ff["period_type"] == "A") & (ff["metric_id"] == 70) & (ff["basis"] == "S")]
    a = a[(a["period_end"] >= date(2018, 3, 31)) & (a["period_end"] <= date(2019, 3, 31))]
    late = a[
        [
            af > results_deadline(pe, "A", pe.month) + timedelta(days=1)
            for pe, af in zip(a["period_end"], a["available_from"], strict=True)
        ]
    ]
    late = late[(late["period_end"].map(lambda d: d.month) == 3)]
    assert len(late) > 0, "no late filer in the sample window"
    r = late.sort_values(["available_from", "company_id"]).iloc[len(late) // 2]
    cal = pit_store.calendar
    before = cal.last_good_on_or_before(r["available_from"] - timedelta(days=1))
    after = cal.first_good_after(r["available_from"] - timedelta(days=1))
    cid, pe = int(r["company_id"]), r["period_end"]
    assert after >= r["available_from"]
    fb = features(pit_store, before).set_index("company_id")
    fa = features(pit_store, after).set_index("company_id")
    if cid in fb.index:
        assert fb.loc[cid, "fy_end"] < pe
    assert fa.loc[cid, "fy_end"] == pe


def test_u_score_hook_matches_feature_complete(pit_store: PitStore) -> None:
    a = date(2022, 6, 30)
    f = features(pit_store, a)
    fc = f.set_index("security_id")["feature_complete"]
    mem, _ = build_universe(pit_store.as_of(a, record=False), UCFG, feature_complete=fc)
    base, _ = build_universe(pit_store.as_of(a, record=False), UCFG)
    assert mem["in_u_score"].sum() < base["in_u_score"].sum()
    dropped = mem[(mem["exclusion_reason"] == "features_incomplete")]
    assert len(dropped) > 0
    assert (dropped["feature_complete"] < 0.6).all()
    assert not (mem["in_u_score"] & (mem["feature_complete"] < 0.6)).any()


def test_output_dtypes_join_with_universe_and_labels(pit_store: PitStore) -> None:
    """anchor / fy_end / feature_available_from are python dates (date32 in parquet), like universe_member."""
    f = features(pit_store, date(2019, 6, 28))
    assert isinstance(f["anchor"].iloc[0], date)
    assert f["anchor"].iloc[0] == date(2019, 6, 28)
    assert isinstance(f["fy_end"].dropna().iloc[0], date)
    assert isinstance(f["feature_available_from"].dropna().iloc[0], date)
    assert str(f["security_id"].dtype) == "int64"
    assert str(f["company_id"].dtype) == "int64"


def test_coverage_row_matches_the_hook(pit_store: PitStore) -> None:
    from ideation.features.build import coverage_row

    a = date(2022, 6, 30)
    f = features(pit_store, a)
    mem, _ = build_universe(pit_store.as_of(a, record=False), UCFG)
    cov = coverage_row(a, mem, f, REG, UCFG)
    hooked, _ = build_universe(
        pit_store.as_of(a, record=False), UCFG, feature_complete=f.set_index("security_id")["feature_complete"]
    )
    assert cov["u_score_before"] == int(mem["in_u_score"].sum())
    assert cov["u_score_after"] == int(hooked["in_u_score"].sum())
    assert cov["dropped"] == cov["u_score_before"] - cov["u_score_after"]
