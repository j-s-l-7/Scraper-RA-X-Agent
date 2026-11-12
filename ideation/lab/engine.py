# ruff: noqa: E501, N806
"""Lab engine: one master R1 matrix per panel, trial scoring in the monthly and legacy June designs, fixed-weight
composites, rank-average ensembles. No database access; labels come with the panel (``load_labels`` only)."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd

from ideation.cv.walkforward import Fold
from ideation.features.registry import Registry
from ideation.features.transform import na_layout_frame
from ideation.lab import design as D
from ideation.lab.panel import LabPanel
from ideation.lab.targets import make_target
from ideation.models import phase1
from ideation.models.zoo import LEARNERS, BroadcastScaler, learner_params
from ideation.rank.transform import TransformSpec, xs_transform

RAW_BINARY = ("brk_1y", "brk_2y", "brk_3_4y", "brkdn_1y", "brkdn_2y", "brkdn_3_4y")
"""Binary breakout columns stay RAW 0/1 (audit C6): a rank-gauss of a binary column is a function of the anchor's base rate."""
SPARSE_LAYOUT = ("fin_margin", "cost_ratio", "dep_g1", "ta_g1", "lev", "bp")
MOM = ["mom_1m", "mom_3m", "mom_6m", "mom_12_1", "rev_5d", "rs_grp_3m", "rs_grp_6m", "rs_n500_3m", "rs_n500_6m",
       "dist_52wh", "dist_52wl", "brk_1y", "brk_2y", "brk_3_4y", "brkdn_1y", "brkdn_2y", "brkdn_3_4y", "ma200_gap",
       "ma50_200", "id_frog"]  # fmt: skip
VOLSIZE = ["size", "rvol_63", "beta_252", "idio_vol"]
REGIME = ["regime_pct_above_200dma", "regime_pct_above_50dma", "regime_ad_ratio_20d", "regime_nh_nl_20d",
          "regime_div_6m", "regime_pct_beating_6m", "regime_vix", "regime_vix_pct_3y", "regime_vix_chg_1m",
          "regime_n500_ma200_gap", "regime_n500_ma200_slope_63d", "regime_n500_dd_252", "regime_cpi_yoy",
          "regime_iip_yoy", "regime_usdinr_3m", "regime_wti_3m", "regime_us10y_3m_bp"]  # fmt: skip
QUALITY = ["gp_ta", "accruals", "asset_gr", "piotroski"]
VALUE5 = ["ep", "sp", "ebitda_ev", "fcf_yield", "bp"]
UNIFIED3 = ["ebitda_ev", "sp", "fcf_yield"]
QUALITY_ONLY = {"roe_norm": 1, "roic": 1, "roa": 1, "opm": 1, "opm_vol3": -1}


def feature_sets(reg: Registry) -> dict[str, list[str]]:
    core = reg.names(sets=["core"], source="features_fundamental.yaml")
    lean = [n for n in core if n not in SPARSE_LAYOUT]
    mvrs = [*core, *VOLSIZE, *MOM, *REGIME]
    return {"core": core, "lean": lean, "mvrs": mvrs, "mvrsq": [*mvrs, *QUALITY]}


def union_names(sets: Mapping[str, Sequence[str]]) -> list[str]:
    return list(dict.fromkeys(n for v in sets.values() for n in v))


def build_master(panel: LabPanel, names: Sequence[str]) -> pd.DataFrame:
    """R1 (rank-gauss per anchor, winsorised, NaN -> 0 plus isna/na_layout flags) of the union of columns on the whole
    universe at every anchor. Binary breakout columns are appended raw (NaN -> 0), regime columns stay broadcast."""
    reg, rows = panel.reg, panel.frame
    raw = [n for n in names if n in RAW_BINARY]
    ranked = [n for n in names if n not in RAW_BINARY]
    missing = [n for n in names if n not in rows.columns]
    if missing:
        raise KeyError(f"columns missing from the lab panel: {missing}")
    spec = TransformSpec(
        kind="global", growth_mode="winsor", growth_cols=reg.growth_cols(), priors=reg.priors(),
        broadcast_cols=frozenset(n for n in ranked if n in reg.broadcast_cols()),
    )  # fmt: skip
    na_layout = na_layout_frame(rows, reg, ranked)
    x = xs_transform(rows, ranked, spec, na_layout)
    for n in raw:
        x[n] = rows[n].fillna(0.0).astype(float)
    return x


def columns_for(master: pd.DataFrame, names: Sequence[str]) -> list[str]:
    out = []
    for n in names:
        out.append(n)
        for pre in ("isna_", "na_layout_"):
            if f"{pre}{n}" in master.columns:
                out.append(f"{pre}{n}")
    return out


# ── learner trials ───────────────────────────────────────────────────────────────────────────────────────────
def make_model(cfg: Mapping[str, Any], reg: Registry, feats: Sequence[str], factor: int):
    key = cfg["learner"]
    p = D.scale_params(key, learner_params(key, "1Y"), factor)
    if key == "scr":
        p["priors"] = {n: v for n, v in reg.priors().items() if n in feats}
    base = LEARNERS[key](p)
    bcast = [n for n in feats if n in reg.broadcast_cols()]
    return BroadcastScaler(base, bcast) if bcast else base


def run_learner(
    panel: LabPanel, master: pd.DataFrame, cfg: Mapping[str, Any], folds: Sequence[Fold], *, design: str,
    log: Callable[[str], None] = lambda m: None,
) -> pd.DataFrame:
    reg, fr = panel.reg, panel.frame
    sets = feature_sets(reg)
    feats = sets[cfg["features"]]
    cols = columns_for(master, feats)
    X = master[cols]
    y = make_target(fr, cfg["target"], seed=int(cfg.get("seed", 0)))
    anchors = fr["anchor"]
    factor = D.OVERSAMPLE if design == "monthly" else 1
    out = []
    for f in folds:
        tr = anchors.isin(f.train_anchors).to_numpy() & fr["labelled"].to_numpy() & y.notna().to_numpy()
        te = anchors.isin(f.test_anchors).to_numpy()
        meta = fr.loc[tr, ["anchor", "security_id", "industry_group", "label_end"]].copy()
        D_assert = pd.to_datetime(meta["label_end"]) > f.test_anchors[0]
        if D_assert.any():
            from ideation.pit.contract import LeakageError

            raise LeakageError(f"{int(D_assert.sum())} training rows have label_end > {f.test_anchors[0].date()}")
        if design == "monthly":
            meta["sample_weight"] = D.row_weights(meta["anchor"], list(f.train_anchors), panel.label_end)
        t0 = time.perf_counter()
        model = make_model(cfg, reg, feats, factor).fit(X.loc[tr], y.loc[tr].to_numpy(float), meta)
        sc = fr.loc[te, ["anchor", "security_id"]].copy()
        sc["score"] = model.predict(X.loc[te], fr.loc[te, ["anchor", "security_id", "industry_group"]])
        sc["fold"] = f.name
        out.append(sc)
        log(f"  fold {f.name}: n_train={int(tr.sum())} n_test={int(te.sum())} {time.perf_counter() - t0:.0f}s")
    return pd.concat(out, ignore_index=True)


# ── fixed-weight scores ──────────────────────────────────────────────────────────────────────────────────────
def _sum_cols(master: pd.DataFrame, cols: Mapping[str, float]) -> pd.Series:
    return sum(master[c] * w for c, w in cols.items())


def fixed_score(name: str, master: pd.DataFrame, reg: Registry) -> pd.Series:
    """Fixed-weight scores on the R1 matrix. Signs are ex-ante priors (never learned)."""
    if name == "P1_UNIFIED_XS":
        return _sum_cols(master, dict.fromkeys(UNIFIED3, 1.0))
    if name == "VALUE_ONLY":
        return _sum_cols(master, dict.fromkeys(VALUE5, 1.0))
    if name == "MOM_12_1":
        return master["mom_12_1"]
    if name == "LOWVOL":
        return -master["rvol_63"]
    if name == "QUALITY_ONLY":
        return _sum_cols(master, QUALITY_ONLY)
    if name == "P1_COMP_EW":
        core = reg.names(sets=["core"], source="features_fundamental.yaml")
        pri = {c: s for c, s in reg.priors().items() if c in core}
        return pd.Series(phase1.composite_ew(pri)._predict(master[list(pri)], None), index=master.index)  # type: ignore[arg-type]
    if name == "VMQL_FIXED":
        v = _sum_cols(master, dict.fromkeys(VALUE5, 1.0 / len(VALUE5)))
        q = _sum_cols(master, {c: s / len(QUALITY_ONLY) for c, s in QUALITY_ONLY.items()})
        return v + master["mom_12_1"] - master["rvol_63"] + q
    raise KeyError(name)


def pct_rank(s: pd.Series, anchors: pd.Series) -> pd.Series:
    return s.groupby(anchors.to_numpy()).rank(pct=True)


def blend_val_price(frame: pd.DataFrame, master: pd.DataFrame, reg: Registry, tactical_scores: pd.DataFrame) -> pd.Series:
    """BLEND_VAL_PRICE: 0.5 rank(P1_UNIFIED_XS) + 0.5 rank(tactical SCR_T0 3M walk-forward OOS score), per anchor.
    Diagnostic origin (audit F8), same-period caveat: the tactical family was chosen on 2014-2025 3M returns."""
    v = pct_rank(fixed_score("P1_UNIFIED_XS", master, reg), frame["anchor"])
    t = frame[["anchor", "security_id"]].merge(tactical_scores, on=["anchor", "security_id"], how="left")["score"]
    t.index = frame.index
    pt = pct_rank(t, frame["anchor"])
    return 0.5 * v + 0.5 * pt.fillna(0.5)


def rank_average(frames: Sequence[pd.DataFrame]) -> pd.DataFrame:
    """Rank-average member scores per anchor (the ENS trial). Members must share rows."""
    base = frames[0][["anchor", "security_id", "fold"]].copy()
    acc = np.zeros(len(base))
    for fr in frames:
        m = base.merge(fr[["anchor", "security_id", "score"]], on=["anchor", "security_id"], how="left")
        acc += m.groupby("anchor")["score"].rank(pct=True).to_numpy()
    base["score"] = acc / len(frames)
    return base


def cfg_hash12(cfg: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(cfg, sort_keys=True, default=str).encode()).hexdigest()[:12]
