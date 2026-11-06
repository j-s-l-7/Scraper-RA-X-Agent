"""Synthetic panels and a minimal R1 → R2 pipeline for the E1 leakage tests (T-04/05/06/18/19). No lake."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence

import numpy as np
import pandas as pd

from ideation.cv.walkforward import Fold, Scorer, walk_forward_predict
from ideation.eval.metrics import ic_by_anchor
from ideation.rank.transform import TransformSpec, rank_gauss, xs_transform


def planted_rho(spearman: float) -> float:
    """Pearson ρ of a bivariate normal whose Spearman is ``spearman``: ρ = 2 sin(π ρ_s / 6)."""
    return 2 * math.sin(math.pi * spearman / 6)


def make_panel(
    n_anchor: int, n_names: int, n_features: int, *, spearman: float = 0.0, seed: int = 0, start_year: int = 2000
) -> tuple[pd.DataFrame, list[str]]:
    """June anchors; features N(0, 1); ``ret = ρ·f0 + √(1 − ρ²)·ε``; label_end = the next anchor (03 §C3)."""
    rng = np.random.default_rng(seed)
    anchors = pd.to_datetime([f"{start_year + a}-06-30" for a in range(n_anchor + 1)])
    feats = [f"f{i}" for i in range(n_features)]
    raw = pd.DataFrame(
        {
            "anchor": np.repeat(anchors[:-1], n_names),
            "label_end": np.repeat(anchors[1:], n_names),
            "security_id": np.tile(np.arange(n_names), n_anchor),
            "industry_group": np.tile(np.arange(n_names) % 6, n_anchor),
            "sector": np.tile(np.arange(n_names) % 3, n_anchor),
        }
    )
    x = rng.normal(size=(len(raw), n_features))
    raw[feats] = x
    rho = planted_rho(spearman)
    raw["ret"] = rho * x[:, 0] + math.sqrt(1 - rho**2) * rng.normal(size=len(raw))
    return raw, feats


def anchors_of(raw: pd.DataFrame) -> list[pd.Timestamp]:
    return sorted(pd.to_datetime(raw["anchor"].unique()))


def block_fold(raw: pd.DataFrame, n_train: int = 3) -> list[Fold]:
    """One walk-forward fold: train on the first ``n_train`` anchors, test on every later anchor (label_end of the
    last training anchor = the first test anchor, so the C3 purge holds)."""
    a = anchors_of(raw)
    return [Fold("block", tuple(a[:n_train]), tuple(a[n_train:]), ())]


def run_pipeline(
    raw: pd.DataFrame,
    features: Sequence[str],
    folds: Sequence[Fold],
    make_model: Callable[[], Scorer],
    spec: TransformSpec | None = None,
    label: str = "ret",
) -> pd.DataFrame:
    """R1 per-anchor transform → target = rank-gauss of ``label`` per anchor → walk-forward fit/score."""
    X = xs_transform(raw, list(features), spec or TransformSpec())
    y = raw.groupby("anchor")[label].transform(rank_gauss)
    meta = raw[["anchor", "security_id", "industry_group", "label_end"]]
    scored = walk_forward_predict(X, y, meta, folds, make_model)
    return scored.join(raw[[label]])


def oos_ic(scored: pd.DataFrame, label: str = "ret") -> pd.Series:
    return ic_by_anchor(scored, "score", label)
