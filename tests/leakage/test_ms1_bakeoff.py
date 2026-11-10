"""MS1 synthetic tests: fold table, sealed holdout (T-26 for the bake-off driver), CPCV row-level check, resume,
determinism (T-19) and consensus scores. The lake-backed T-02 lives in tests/lake/test_ms1_lake.py."""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ideation.bakeoff import run as R
from ideation.bakeoff.data import load_panel
from ideation.bakeoff.metrics import consensus_scores
from ideation.cv.walkforward import annual_folds
from ideation.features.registry import load_registry
from ideation.labels import loader
from ideation.pit.contract import LeakageError
from ideation.trials.registry import load_family

JUNES = [date(y, 6, 30 if y not in (2018, 2024) else 29 if y == 2018 else 28) for y in range(2016, 2026)]
REG = load_registry()
CORE = REG.names(sets=["core"], source="features_fundamental.yaml")
N = 240


def synth_dir(tmp: Path) -> Path:
    rng = np.random.default_rng(0)
    out = tmp / "outputs" / "snap"
    out.mkdir(parents=True)
    uni, fund, tech, lab = [], [], [], []
    for a in JUNES:
        sid = np.arange(N)
        f = rng.normal(size=(N, len(CORE)))
        ret = 0.15 * f[:, 0] + rng.normal(scale=1.0, size=N)
        uni.append(pd.DataFrame({"anchor": a, "security_id": sid, "in_u_score": True, "in_u_score_adv05": sid < 200,
                                 "in_u1000": sid < 220, "adv63_lakh": 100.0, "mcap_pit_cr": 500.0}))  # fmt: skip
        fund.append(pd.DataFrame({"anchor": a, "security_id": sid, "company_id": sid, "industry_group": sid % 12,
                                  "sector": sid % 4, "layout": "core", **dict(zip(CORE, f.T)),
                                  "feature_available_from": a}))  # fmt: skip
        tech.append(pd.DataFrame({"anchor": a, "security_id": sid, "regime_vix": float(a.year), "mom_12_1": rng.normal(size=N),
                                  "rvol_63": rng.normal(size=N), "beta_252": rng.normal(size=N)}))  # fmt: skip
        end = JUNES[JUNES.index(a) + 1] if a != JUNES[-1] else date(2026, 6, 30)
        lab.append(pd.DataFrame({"anchor": a, "security_id": sid, "horizon": "1Y", "label_end": end, "ret_total": ret,
                                 "excess_univ_med": ret - np.median(ret), "excess_n500_pr": ret - 0.1, "exit_type": "normal"}))  # fmt: skip
    pd.concat(uni).to_parquet(out / "universe_member.parquet")
    pd.concat(fund).to_parquet(out / "features_fundamental.parquet")
    pd.concat(tech).to_parquet(out / "features_tech.parquet")
    pd.concat(lab).to_parquet(out / "labels.parquet")
    return out


@pytest.fixture(scope="module")
def panel(tmp_path_factory: pytest.TempPathFactory):  # noqa: ANN201
    out = synth_dir(tmp_path_factory.mktemp("ms1"))
    loader.AUDIT.clear()
    p = load_panel(out, "snap", "june", REG, label_source=out / "labels.parquet")
    return p


def spec(trial: str):  # noqa: ANN201
    return {s.trial_id: s for s in load_family("1Y-june")}[trial]


def test_fold_table_is_exactly_the_c3_table() -> None:
    anchors = [pd.Timestamp(a) for a in JUNES[:9]]
    ends = {a: pd.Timestamp(JUNES[i + 1]) for i, a in enumerate(anchors)}  # exit = last good session <= A + 12M
    folds = annual_folds(anchors, ends, anchors[3:], n_train=3)
    got = {f.test_anchors[0].year: [a.year for a in f.train_anchors] for f in folds}
    assert got == {2019: [2016, 2017, 2018], 2020: [2017, 2018, 2019], 2021: [2018, 2019, 2020],
                   2022: [2019, 2020, 2021], 2023: [2020, 2021, 2022], 2024: [2021, 2022, 2023]}  # fmt: skip


def test_panel_never_requests_or_holds_a_sealed_anchor(panel) -> None:  # noqa: ANN001
    assert all(a.year <= 2024 for a in panel.anchors)
    assert pd.to_datetime(panel.frame["anchor"]).dt.year.max() == 2024
    for rec in loader.AUDIT:
        assert not loader.sealed_anchors(rec.anchors, rec.horizon), rec


def test_wfo_oos_anchors_resume_and_determinism(panel, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    s = spec("RIDGE_GLOBAL")
    ctx = R.make_context(s, panel, REG, tmp_path, {})
    oos, infos = R.run_wfo(ctx, importance=True)
    assert sorted({a.year for a in oos["anchor"]}) == [2019, 2020, 2021, 2022, 2023, 2024]
    assert all(set(i["train_anchors"]) and max(i["train_anchors"]) < f for f, i in infos.items())
    sha1 = hashlib.sha256(oos.sort_values(["anchor", "security_id"])["score"].to_numpy().tobytes()).hexdigest()
    # resume: no refit allowed
    monkeypatch.setattr(R, "_fit_predict", lambda *a, **k: (_ for _ in ()).throw(AssertionError("refit")))
    oos2, _ = R.run_wfo(ctx, importance=True)
    sha2 = hashlib.sha256(oos2.sort_values(["anchor", "security_id"])["score"].to_numpy().tobytes()).hexdigest()
    assert sha1 == sha2
    # planted signal is recovered out of sample
    ic = oos.groupby("anchor").apply(lambda g: g["score"].corr(g["label"], method="spearman"), include_groups=False)
    assert ic.mean() > 0.03


def test_cpcv_runs_36_splits_without_overlap_and_check_has_teeth(panel, tmp_path: Path) -> None:  # noqa: ANN001
    ctx = R.make_context(spec("OLS_GLOBAL"), panel, REG, tmp_path, {})
    cp, splits = R.run_cpcv(ctx)
    assert len(splits) == 36 and cp["split"].nunique() == 36
    assert {a.year for a in cp["anchor"]} == set(range(2016, 2025))
    meta = ctx.matrix.meta
    prev = meta[meta["anchor"] == pd.Timestamp(JUNES[2])]
    R.assert_no_overlap(prev, [pd.Timestamp(JUNES[3])], panel.label_end)  # C3: the previous anchor's label ends at T
    long_label = prev.assign(label_end=pd.Timestamp(JUNES[5]))  # a 3-year label overlaps the test span
    with pytest.raises(LeakageError):
        R.assert_no_overlap(long_label, [pd.Timestamp(JUNES[3])], panel.label_end)


def test_consensus_scores_votes_and_unanimity() -> None:
    rng = np.random.default_rng(1)
    base = rng.normal(size=500)
    members = {}
    for i in range(10):
        sc = base + rng.normal(scale=0.3, size=500)
        members[f"m{i}"] = pd.DataFrame({"anchor": pd.Timestamp("2020-06-30"), "security_id": np.arange(500), "score": sc,
                                         "label": 0.0, "labelled": True, "ret_total": 0.0})  # fmt: skip
    maj = consensus_scores(members, "majority")
    assert maj["score"].between(0, 10).all() and maj["score"].max() >= 6
    unan = consensus_scores(members, "unanimous")
    assert unan["score"].notna().all() or unan["score"].isna().all()
    ens = consensus_scores(members, "rank_average")
    assert ens["score"].between(0, 1).all()
    few = {k: v.assign(score=np.random.default_rng(int(k[1:])).normal(size=500)) for k, v in members.items()}
    assert consensus_scores(few, "unanimous")["score"].isna().all()  # < 5 unanimous names: anchor excluded
