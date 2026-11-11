# ruff: noqa: E501
"""Research challengers, shown beside the shipped model and never used for ranking, selection, conviction or bands.

* 1Y ``P1_UNIFIED_XS``: fixed-weight value composite, scored like the shipped composite (no fit).
* 3M ``ENS_RANKAVG_T``: mean per-name percentile of the 10 consensus members (9 learners + the T_EW_SIGNPRIOR
  composite). The 9 learners are **refitted** on the last 36 monthly anchors whose labels are unsealed (to 2025-06-30);
  labels are read only through ``load_labels(unsealed_only(...))``. The fit is therefore ~16 months stale until the
  holdout is unsealed. Fitted-member live scores are cached under ``IDEATION_DATA_DIR/predict`` by config hash and
  training window. Threads are capped by the caller (``OMP_NUM_THREADS``); forests predict with ``n_jobs=1``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ideation.features.registry import Registry
from ideation.labels.loader import load_labels, unsealed_only
from ideation.predict.score import Scored, last_anchors_3m, neutral_ranks, training_frame_3m
from ideation.rank.percentile import ideation_score, percentile
from ideation.tactical.data import build_design, rows_in
from ideation.tactical.engine import build_model
from ideation.trials.registry import TrialSpec, load_family

log = logging.getLogger("ideation.predict")
CHALLENGER_LABEL = "research challenger, unpromoted"
CODE_VERSION = "p6-1"


def chal_columns(prefix: str, score: np.ndarray, security_id: pd.Series, base: pd.DataFrame) -> pd.DataFrame:
    """score -> percentile -> Ideation Score -> neutral rank, under ``prefix`` (e.g. ``chal_``)."""
    s = pd.Series(score, dtype=float)
    p = percentile(s)
    out = pd.DataFrame({"security_id": security_id.to_numpy(), f"{prefix}score": s.to_numpy(), f"{prefix}p": p.to_numpy(),
                        f"{prefix}is": ideation_score(p).to_numpy()})  # fmt: skip
    b = base.drop_duplicates("security_id").set_index("security_id").reindex(out["security_id"])
    nr = neutral_ranks(
        out[f"{prefix}p"], b[["beta_252", "rvol_63", "idio_vol", "mcap_pit_cr", "adv63_lakh"]].reset_index(drop=True)
    )
    out[f"{prefix}neutral_p_bvls"] = nr["neutral_p_bvls"].to_numpy()
    return out


def challenger_1y(sc: Scored, base: pd.DataFrame) -> pd.DataFrame:
    return chal_columns("chal_", sc.frame["score"].to_numpy(), sc.frame["security_id"], base)


def _cache_key(specs: list[TrialSpec], anchors: list[pd.Timestamp], live_anchor: Any, n_live: int) -> str:
    payload = {"specs": [s.config_hash for s in specs], "train": [str(a.date()) for a in anchors],
               "live": str(live_anchor), "n_live": n_live, "code": CODE_VERSION}  # fmt: skip
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def fit_ens(out_dir: Path, data_dir: Path, reg: Registry, live3m: pd.DataFrame, live_anchor: Any,
            config_dir: Path | None = None) -> tuple[pd.DataFrame, dict[str, Any]]:  # fmt: skip
    """Member live scores (columns = member ids) and info. ``live3m`` = ``live.frame_3m``."""
    specs = {s.trial_id: s for s in load_family("tactical-3M", config_dir)}
    members = [specs[m] for m in specs["ENS_RANKAVG_T"].config["members"]]
    anchors = last_anchors_3m(out_dir)
    assert all(a <= pd.Timestamp("2025-06-30") for a in anchors), "a sealed anchor reached the challenger fit"
    cache = data_dir / "predict" / f"ens_{_cache_key(members, anchors, live_anchor, len(live3m))}.parquet"
    if cache.exists():
        return pd.read_parquet(cache), {
            "cached": True,
            "train_first": str(anchors[0].date()),
            "train_last": str(anchors[-1].date()),
        }
    tr = training_frame_3m(out_dir, anchors)
    lab = load_labels(unsealed_only([a.date() for a in anchors], "3M"), "3M", "fit", family="tactical-3M",
                      source=out_dir / "labels.parquet")  # fmt: skip
    lab["anchor"] = pd.to_datetime(lab["anchor"]).dt.normalize()
    lab = lab[["anchor", "security_id", "ret_total", "label_end"]].rename(
        columns={"ret_total": "r3", "label_end": "le"}
    )
    tr = tr.merge(lab, on=["anchor", "security_id"], how="left")
    tr["ret_3m"], tr["label_end"] = tr["r3"], pd.to_datetime(tr["le"])
    tr = (
        tr[tr["ret_3m"].notna()].drop(columns=["r3", "le"]).reset_index(drop=True)
    )  # as MS2's panel: unlabelled rows dropped
    comb = pd.concat([tr, live3m], ignore_index=True)
    design = build_design(comb, reg, "T0", adv_floor_cr=0.2)
    is_tr = rows_in(design.meta, anchors) & design.y.notna().to_numpy()
    is_live = (design.meta["anchor"] == pd.Timestamp(live_anchor).normalize()).to_numpy()
    out = pd.DataFrame({"security_id": design.meta.loc[is_live, "security_id"].to_numpy()})
    info: dict[str, Any] = {"n_train": int(is_tr.sum()), "train_first": str(anchors[0].date()), "train_last": str(anchors[-1].date()),
                            "fit_s": {}}  # fmt: skip
    for spec in members:
        t0 = time.perf_counter()
        model = build_model(spec, design)
        model.fit(design.X.loc[is_tr], design.y.loc[is_tr].to_numpy(dtype=float), design.meta.loc[is_tr])
        out[spec.trial_id] = np.asarray(model.predict(design.X.loc[is_live], design.meta.loc[is_live]), dtype=float)
        info["fit_s"][spec.trial_id] = round(time.perf_counter() - t0, 1)
        log.info("ens member %s fitted in %.1fs", spec.trial_id, time.perf_counter() - t0)
    cache.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(cache, index=False)
    return out, info


def challenger_3m(members: pd.DataFrame, base: pd.DataFrame) -> pd.DataFrame:
    """ENS_RANKAVG_T = mean of the members' percentiles (as ``evalx.consensus_scores``)."""
    cols = [c for c in members.columns if c != "security_id"]
    ps = pd.concat([percentile(members[c]) for c in cols], axis=1)
    return chal_columns("chal_", ps.mean(axis=1).to_numpy(), members["security_id"], base)
