"""T-02 (02 §7): future-poison SCORES at Jun-2021 through trained fold models.

For the walk-forward fold with test anchor 2021-06-30 (train {2018, 2019, 2020}) the whole input chain is rebuilt from a
snapshot whose rows with ``available_from`` after the test anchor are poisoned (three modes): universe membership (with
the feature-complete hook), fundamental features, technical + regime features, R1, then fit and score. The score vector
of each model must be bit-identical to the clean rebuild. The training labels come from the stored label table (every
label used has ``label_end`` <= the test anchor, so it is legitimately known at T). Models: RF_GLOBAL, RF_GLOBAL_EXT
(technical + broadcast regime columns), RIDGE_GLOBAL and the baseline P1_COMP_EW. A positive control poisons the
past and must move the scores.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from ideation.bakeoff import run as R
from ideation.bakeoff.data import FAMILY_MONTH, LABEL_COLS, Panel
from ideation.config import REPO_ROOT, load_methodology
from ideation.features import tech_build
from ideation.features.fundamental import build_fundamental
from ideation.features.registry import load_registry
from ideation.labels.loader import load_labels
from ideation.pit.contract import PitStore
from ideation.trials.registry import load_family
from ideation.universe.build import UniverseConfig, build_universe

REG = load_registry(REPO_ROOT / "config")
UCFG = UniverseConfig.from_methodology(load_methodology().data)
TEST = date(2021, 6, 30)
TRAIN = [date(2018, 6, 29), date(2019, 6, 28), date(2020, 6, 30)]
MODELS = ["RF_GLOBAL", "RF_GLOBAL_EXT", "RIDGE_GLOBAL", "P1_COMP_EW"]


@pytest.fixture(scope="module")
def tstore(settings) -> PitStore:
    try:
        snap = PitStore.latest(settings.data_dir)
    except FileNotFoundError as exc:
        pytest.skip(str(exc))
    out = settings.data_dir / "outputs" / snap.snap_id
    if not (out / "labels.parquet").is_file():
        pytest.skip("no stored labels for the latest snapshot")
    return PitStore(snap.snapshot_dir, outputs_dir=out)


def rebuild_frame(store: PitStore, labels: pd.DataFrame) -> pd.DataFrame:
    """The Panel frame for the four anchors, built from ``store`` only (labels are the stored, already-known ones)."""
    frames = []
    for a in [*TRAIN, TEST]:
        view = store.as_of(a)
        mem, _ = build_universe(view, UCFG)
        feats = build_fundamental(view, mem[mem["traded_ok"] & mem["company_id"].notna()], REG)
        fc = feats.set_index("security_id")["feature_complete"]
        mem, _ = build_universe(view, UCFG, feature_complete=fc)
        mem = mem[mem["in_u_score"]][
            ["security_id", "in_u_score", "in_u_score_adv05", "in_u1000", "adv63_lakh", "mcap_pit_cr"]
        ]
        tech = tech_build.build_anchor(store, REG, UCFG, a, rebuild_universe=True).features
        tcols = [n for n in REG.names(sets=["tech", "regime"]) if n in tech.columns]
        df = mem.merge(feats, on="security_id", how="left", validate="1:1")
        df = df.merge(tech[["security_id", *tcols]], on="security_id", how="left", validate="1:1")
        for c in REG.broadcast_cols():
            if c in df.columns:
                df[c] = df[c].dropna().iloc[0] if df[c].notna().any() else np.nan
        df["anchor"] = a
        frames.append(df)
    frame = pd.concat(frames, ignore_index=True)
    lab = labels[labels["anchor"].isin([*TRAIN, TEST])][["anchor", "security_id", *LABEL_COLS]]
    frame = frame.merge(lab, on=["anchor", "security_id"], how="left", validate="1:1")
    frame["labelled"] = frame["ret_total"].notna()
    return frame.sort_values(["anchor", "security_id"], kind="stable").reset_index(drop=True)


def mini_panel(frame: pd.DataFrame, snap_id: str) -> Panel:
    ends = pd.to_datetime(frame["label_end"]).groupby(pd.to_datetime(frame["anchor"])).max()
    assert FAMILY_MONTH["june"] == 6
    return Panel("june", snap_id, frame, [*TRAIN, TEST], ends)


def scores(frame: pd.DataFrame, snap_id: str, tmp_path, tag: str) -> dict[str, np.ndarray]:
    specs = {s.trial_id: s for s in load_family("1Y-june")}
    panel = mini_panel(frame, snap_id)
    cache: dict = {}
    out = {}
    for m in MODELS:
        ctx = R.make_context(specs[m], panel, REG, tmp_path / tag, cache)
        sc, _ = R._fit_predict(ctx, [pd.Timestamp(a) for a in TRAIN], [pd.Timestamp(TEST)], importance=False)
        out[m] = sc.sort_values("security_id")["score"].to_numpy()
    return out


@pytest.fixture(scope="module")
def clean(tstore: PitStore, tmp_path_factory: pytest.TempPathFactory):
    labels = load_labels(
        TRAIN, "1Y", "fit", family="1Y-june", source=tstore.outputs_dir / "labels.parquet"
    )  # TEST labels are not needed to score and are not read here
    test_labels = load_labels([TEST], "1Y", "qa", family="1Y-june", source=tstore.outputs_dir / "labels.parquet")
    labels = pd.concat([labels, test_labels])
    frame = rebuild_frame(tstore, labels)
    tmp = tmp_path_factory.mktemp("t02")
    return frame, labels, scores(frame, tstore.snap_id, tmp, "clean"), tmp


@pytest.mark.parametrize("mode", ["inflate", "nan", "shuffle"])
def test_t02_future_poison_scores(tstore: PitStore, clean, mode: str) -> None:
    frame, labels, base, tmp = clean
    assert (tstore.table("financial_fact")["available_from"] > TEST).any()
    bad_store = tstore.poison_after(TEST, mode, seed=7)  # type: ignore[arg-type]
    bad_frame = rebuild_frame(bad_store, labels)
    pd.testing.assert_frame_equal(frame, bad_frame, check_exact=True)  # inputs identical, so the scores are too
    bad = scores(bad_frame, tstore.snap_id, tmp, f"bad_{mode}")
    for m in MODELS:
        assert len(base[m]) > 800
        np.testing.assert_array_equal(base[m], bad[m], err_msg=m)  # bit-for-bit


def test_t02_positive_control_poisoning_the_past_moves_scores(tstore: PitStore, clean) -> None:
    _frame, labels, base, tmp = clean
    old = tstore.poison_after(TEST - timedelta(days=900), "inflate", seed=7)
    moved = scores(rebuild_frame(old, labels), tstore.snap_id, tmp, "old")
    assert any(not np.array_equal(base[m], moved[m]) for m in MODELS)
