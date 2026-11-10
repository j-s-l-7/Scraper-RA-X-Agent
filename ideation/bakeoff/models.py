"""Trial config -> model object with the uniform ``fit(X, y, meta)`` / ``predict(X, meta)`` protocol (05 §3.2-3.4)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ideation.features.registry import Registry
from ideation.models import phase1
from ideation.models.zoo import Learner, NinetyTenSec, make_learner

RAW_INPUT_MODELS = {"p1_unified"}


def model_inputs(cfg: Mapping[str, Any]) -> tuple[str, ...] | None:
    """Raw winsorized columns a model needs instead of the R1 matrix (P1_UNIFIED only)."""
    return phase1.UNIFIED_COLS if cfg.get("model") == "p1_unified" else None


def build_model(cfg: Mapping[str, Any], reg: Registry, *, broadcast: list[str], feature_names: list[str]) -> Learner:
    priors = {n: p for n, p in reg.priors().items() if n in feature_names}
    kind = cfg.get("model")
    p = dict(cfg.get("params") or {})
    if kind == "p1_unified":
        return phase1.Unified()
    if kind == "p1_unified_xs":
        return phase1.unified_xs()
    if kind == "p1_comp_dossier":
        return phase1.composite_dossier()
    if kind == "p1_comp_ew":
        return phase1.composite_ew(priors)
    if kind == "p1_industry_wf":
        return phase1.IndustryEquations(priors, k=int(p["k"]), min_group=int(p["min_group"]))
    if kind == "p1_wfo_scr":
        fam = {n: f for n, f in reg.families().items() if n in feature_names}
        return phase1.WFOSelectedSCR(
            float(p["alpha"]), priors, fam, p_max=float(p["p_max"]), vif_max=float(p["vif_max"])
        )
    if kind == "p1_final_wfo":
        return phase1.final_wfo(float(p["alpha"]), priors, tuple(p["cols"]))
    key = cfg["learner"]
    extra: dict[str, Any] = {"priors": priors} if key == "scr" else {}
    base = make_learner(key, "1Y", broadcast=broadcast, **extra)
    if cfg.get("ninety_ten_sec"):
        core = [n for n in reg.names(sets=["core"], source="features_fundamental.yaml") if n in feature_names]
        return NinetyTenSec(base, core_cols=core)
    return base
