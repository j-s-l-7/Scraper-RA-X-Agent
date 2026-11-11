# ruff: noqa: E501
"""P6 on the real snapshot and ledger: the live path is the backtest code (parity with stored OOS scores), live
features ignore poisoned future rows, nothing sealed is calibrated on, persistence round-trips, runs are idempotent."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from ideation.config import load_methodology
from ideation.db.writer import Writer
from ideation.features.registry import load_registry
from ideation.ideas import persist as ipersist
from ideation.ideas import select
from ideation.labels import loader
from ideation.pit.contract import PitStore
from ideation.predict import live, score
from ideation.report import store as rstore
from ideation.runs import RunRegistry
from ideation.trials.registry import load_family
from ideation.universe.build import UniverseConfig

pytestmark = pytest.mark.lake
A = date(2024, 6, 28)


@pytest.fixture(scope="module")
def ctx(settings):
    try:
        snap = PitStore.latest(settings.data_dir)
    except FileNotFoundError as exc:
        pytest.skip(str(exc))
    reg = load_registry(settings.config_dir)
    ucfg = UniverseConfig.from_methodology(load_methodology().data)
    store = PitStore(snap.snapshot_dir, mode="live")
    return {"store": store, "reg": reg, "ucfg": ucfg, "live": live.build_live(store, reg, ucfg, A)}


def test_live_scores_equal_the_stored_backtest_scores(settings, ctx) -> None:
    """02 P5: same code. 1Y (P1_COMP_EW) and 3M (T_EW_SIGNPRIOR) at a historical anchor, built through the live path,
    reproduce the stored OOS scores and percentiles name by name (float32 storage tolerance)."""
    reg, ld = ctx["reg"], ctx["live"]
    s1 = {s.trial_id: s for s in load_family("1Y-june", settings.config_dir)}
    s3 = {s.trial_id: s for s in load_family("tactical-3M", settings.config_dir)}
    with Writer.from_settings(settings).connect() as conn:
        t1 = rstore.trial_index(conn, "1Y-june").set_index("name")["trial_id"]
        t3 = rstore.trial_index(conn, "tactical-3M").set_index("name")["trial_id"]
        o1 = rstore.oos(conn, int(t1["P1_COMP_EW"]))
        o3 = rstore.oos(conn, int(t3["T_EW_SIGNPRIOR"]))
    for sc, oos, tol in (
        (score.score_1y(live.frame_1y(ld, reg), reg, s1["P1_COMP_EW"], A), o1, 1e-6),
        (score.score_3m(live.frame_3m(ld), reg, s3["T_EW_SIGNPRIOR"]), o3, 1e-5),
    ):
        o = oos[oos["anchor"] == pd.Timestamp(A)].set_index("security_id")
        m = sc.frame.set_index("security_id").join(o[["p", "score"]], rsuffix="_oos", how="inner")
        assert len(m) == len(o) > 1000
        assert (m["score"] - m["score_oos"]).abs().max() < tol
        assert (m["p"] - m["p_oos"]).abs().max() < tol


def test_live_features_ignore_poisoned_future_rows(ctx) -> None:
    """T-01 for the live path: poisoning every row available after the anchor leaves the live cross-section untouched."""
    store = ctx["store"]
    poisoned = store.poison_after(A, "inflate")
    other = live.build_live(poisoned, ctx["reg"], ctx["ucfg"], A)
    for f in (live.frame_1y, live.frame_3m):
        a = f(ctx["live"], ctx["reg"]) if f is live.frame_1y else f(ctx["live"])
        b = f(other, ctx["reg"]) if f is live.frame_1y else f(other)
        pd.testing.assert_frame_equal(a, b, check_exact=False, rtol=1e-12, atol=1e-12)


def test_shipped_scoring_reads_no_labels(settings, ctx) -> None:
    loader.AUDIT.clear()
    s1 = {s.trial_id: s for s in load_family("1Y-june", settings.config_dir)}
    score.score_1y(live.frame_1y(ctx["live"], ctx["reg"]), ctx["reg"], s1["P1_COMP_EW"], A)
    assert loader.AUDIT == []


def test_stored_live_run_is_calibrated_on_unsealed_counted_anchors_only(settings) -> None:
    with Writer.from_settings(settings).connect() as conn:
        found = ipersist.latest_predict_run(conn)
        if found is None:
            pytest.skip("no predict run yet")
        _, _, stats = found
    for h, s in stats["horizons"].items():
        anchors = [date.fromisoformat(a) for a in s["calibration"]["anchors"]]
        assert anchors and not any(loader.is_sealed(a, h) for a in anchors)
        assert all(a.year >= 2014 for a in anchors) and s["ood_guard"]["threshold"] > 0
        if h == "1Y":
            assert [a.year for a in anchors] == [2020, 2021, 2022, 2023, 2024]  # 2019 is indicative, 2025 sealed


def test_persisted_ideas_obey_the_hard_rules(settings) -> None:
    with Writer.from_settings(settings).connect() as conn:
        found = ipersist.latest_predict_run(conn)
        n = conn.execute("select count(*) from ideation.v_latest_ideas").fetchone()[0]
        if found is None or n == 0:
            pytest.skip("no ideas run yet")
        bad_fin = conn.execute(
            "select count(*) from ideation.v_latest_ideas i join ideation.prediction p on p.run_id = %s and p.horizon = i.horizon"
            " and p.company_id = i.company_id where p.contrib->>'layout' in ('fin_layout', 'fin_other') and i.conviction = 'High'",
            (found[0],)).fetchone()[0]  # fmt: skip
        ood = conn.execute(
            "select count(*) from ideation.v_latest_ideas i join ideation.prediction p on p.run_id = %s and p.horizon = i.horizon"
            " and p.company_id = i.company_id where p.ood", (found[0],)).fetchone()[0]  # fmt: skip
        both = conn.execute("select count(distinct horizon) from ideation.v_latest_ideas").fetchone()[0]
        chal = conn.execute(
            "select count(*) from ideation.v_latest_ideas where thesis->'challenger'->>'label' is not null"
        ).fetchone()[0]
    assert bad_fin == 0 and ood == 0
    assert both == 2 and chal > 0


def test_idea_round_trip_and_run_key_idempotency(settings) -> None:
    """Persist synthetic ideas inside a rolled-back transaction: arrays/jsonb round-trip, the view reads the newest ok
    ideas run, and an identical run key is skipped."""
    from tests.unit.test_p6_ideas import REGIME, make_pred

    df = make_pred(300)
    sel = select.select_ideas(df, "3M", select.IdeasConfig(), date(2026, 11, 5), REGIME)
    cur = pd.concat([sel["longs"], sel["avoid"]]).assign(continuity="new", prev_idea_id=np.nan)
    with Writer.from_settings(settings).connect() as conn, conn.transaction(force_rollback=True):
        reg = RunRegistry(conn)
        rec = reg.start("ideas", config_hash="t" * 16, as_of=date(2026, 11, 5), params={"predict_run": -1})
        rows = ipersist.idea_rows(cur, rec.run_id, date(2026, 11, 5), "3M", REGIME, {})
        assert ipersist.write_ideas(conn, rows) == len(cur)
        reg.finish(rec.run_id, "ok", stats={})
        got = conn.execute(
            "select count(*), min(rank), max(array_length(flags, 1)) from ideation.v_latest_ideas where run_id = %s",
            (rec.run_id,),
        ).fetchone()
        assert got[0] == len(cur) and got[1] == 1
        again = reg.start("ideas", config_hash="t" * 16, as_of=date(2026, 11, 5), params={"predict_run": -1})
        assert again.status == "skipped"
        forced = reg.start(
            "ideas", config_hash="t" * 16, as_of=date(2026, 11, 5), params={"predict_run": -1}, force=True
        )
        assert forced.status == "running"
