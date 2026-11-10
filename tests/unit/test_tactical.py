"""MS2 tactical pipeline: sealed-anchor discipline, checkpoint resume, consensus scalar scores, per-anchor metrics."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ideation.labels import loader
from ideation.tactical import evalx
from ideation.tactical.data import ANCHOR_MAX, Design, load_panel
from ideation.tactical.engine import cpcv_split_list, run_wfo, wfo_folds
from ideation.trials.registry import TrialSpec

pytestmark = pytest.mark.unit


def _anchors(n: int, start: str = "2011-01-31") -> list[pd.Timestamp]:
    return list(pd.date_range(start, periods=n, freq="ME"))


def _synthetic(tmp_path, n_anchors: int = 70, n_names: int = 60):
    rng = np.random.default_rng(0)
    anchors = _anchors(n_anchors)
    rows = []
    for a in anchors:
        for s in range(n_names):
            rows.append((a, s, f"g{s % 5}", f"s{s % 2}", True, rng.normal()))
    tech = pd.DataFrame(rows, columns=["anchor", "security_id", "industry_group", "sector", "in_u_score_t", "f"])
    tech["anchor"] = tech["anchor"].dt.date
    tech.to_parquet(tmp_path / "features_tech.parquet")
    pd.DataFrame({"anchor": tech["anchor"], "security_id": tech["security_id"], "adv63_lakh": 50.0}).to_parquet(
        tmp_path / "universe_member.parquet"
    )
    lab = []
    for h, m in (("3M", 3), ("1M", 1)):
        d = tech[["anchor", "security_id"]].copy()
        d["horizon"] = h
        d["ret_total"] = tech["f"] * 0.05 + rng.normal(scale=0.1, size=len(d))
        d["ret_price"] = d["ret_total"]
        d["label_end"] = (pd.to_datetime(d["anchor"]) + pd.DateOffset(months=m)).dt.date
        d["excess_univ_med"] = d["ret_total"]
        d["excess_n500_pr"] = d["ret_total"]
        d["exit_type"] = "normal"
        lab.append(d)
    pd.concat(lab).to_parquet(tmp_path / "labels.parquet")
    return tech


def test_panel_never_requests_sealed_anchor(tmp_path, monkeypatch):
    # 200 monthly anchors from 2011-01 run into 2027: everything after 2025-06-30 must be cut before load_labels
    _synthetic(tmp_path, n_anchors=200)
    loader.AUDIT.clear()
    panel = load_panel(tmp_path, "synthetic")
    assert max(panel.anchors) <= ANCHOR_MAX
    assert loader.AUDIT, "labels must be read through load_labels"
    for rec in loader.AUDIT:
        assert not loader.sealed_anchors(rec.anchors, rec.horizon), rec
    with pytest.raises(loader.SealedAnchorError):
        loader.load_labels([pd.Timestamp("2025-07-31").date()], "3M", "fit", family="tactical-3M",
                           source=pd.read_parquet(tmp_path / "labels.parquet"), unsealed=set())  # fmt: skip


def _spec(learner: str = "ridge") -> TrialSpec:
    cfg = {"family": "tactical-3M", "horizon": "3M", "learner": learner, "embargo_anchors": 3,
           "counted_oos": {"start": "2014-01-31", "end": "2016-12-30"}, "params": {}}  # fmt: skip
    return TrialSpec("tactical-3M", 99, "T_TEST", "3M", "monthly", cfg)


def test_wfo_resume_and_purge(tmp_path):
    anchors = _anchors(70)
    label_end = pd.Series({a: a + pd.DateOffset(months=3) for a in anchors})
    rng = np.random.default_rng(1)
    meta = pd.DataFrame({"anchor": np.repeat(anchors, 40), "security_id": np.tile(np.arange(40), 70)})
    meta["industry_group"] = "g"
    meta["label_end"] = meta["anchor"] + pd.DateOffset(months=3)
    X = pd.DataFrame({"a": rng.normal(size=len(meta)), "b": rng.normal(size=len(meta))})
    y = pd.Series(0.3 * X["a"] + rng.normal(size=len(meta)))
    d = Design(X, meta, y, [], {"a": 1, "b": 1}, ["a", "b"], meta)
    spec = _spec()
    folds = wfo_folds(spec, anchors, label_end)
    assert len(folds) == 3
    for f in folds:  # C3 purge and the 3-anchor embargo: last train label ends no later than the block start
        assert label_end[f.train_anchors[-1]] <= f.test_anchors[0]
        assert len([a for a in anchors if f.train_anchors[-1] < a < f.test_anchors[0]]) >= 3
    s1, _ = run_wfo(spec, d, anchors, label_end, tmp_path, importance=False, progress=lambda m: None)
    s2, _ = run_wfo(spec, d, anchors, label_end, tmp_path, importance=False, progress=lambda m: None)
    pd.testing.assert_frame_equal(s1, s2)  # second call loads the checkpoints
    assert evalx.per_anchor(s1, d.raw.assign(ret_3m=y, exu_3m=y, ret_1m=y, exu_1m=y))["ic"].mean() > 0.1


def test_cpcv_split_count():
    anchors = _anchors(170)
    label_end = pd.Series({a: a + pd.DateOffset(months=3) for a in anchors})
    splits = cpcv_split_list(anchors, label_end)
    assert len(splits) == 66
    assert all(max(s.train_anchors, default=pd.Timestamp.min) not in s.test_anchors for s in splits)


def test_consensus_scores_definitions():
    rng = np.random.default_rng(2)
    base = pd.DataFrame({"anchor": pd.Timestamp("2020-01-31"), "security_id": np.arange(200)})
    members = {f"m{i}": base.assign(score=rng.normal(size=200)) for i in range(10)}
    cs = evalx.consensus_scores(members)
    p = pd.concat({k: v["score"].rank() for k, v in members.items()}, axis=1)
    p = (p - 0.5) / 200
    srt = np.sort(p.to_numpy(), axis=1)[:, ::-1]
    np.testing.assert_allclose(cs["CONS_MAJ_T"]["score"].to_numpy(), srt[:, 5])
    np.testing.assert_allclose(cs["CONS_UNAN_T"]["score"].to_numpy(), srt[:, -1])
    np.testing.assert_allclose(cs["ENS_RANKAVG_T"]["score"].to_numpy(), p.mean(axis=1).to_numpy())
    assert ((cs["CONS_MAJ_T"]["score"] >= 0.9) == ((p >= 0.9).sum(axis=1) >= 6)).all()
    assert ((cs["CONS_UNAN_T"]["score"] >= 0.9) == ((p >= 0.9).sum(axis=1) == 10)).all()
