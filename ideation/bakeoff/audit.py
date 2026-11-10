"""Adversarial validation (02 §6.1, blocking) and the isolation-forest OOD share (02 §6.2, informational)."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest, RandomForestClassifier
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import StratifiedKFold, cross_val_predict

from ideation.rank.transform import value_columns


def adversarial_auc(
    x_train: pd.DataFrame, x_test: pd.DataFrame, seed: int = 0
) -> tuple[float, list[tuple[str, float]]]:
    """train=0 / test=1, 5-fold stratified CV with a small RF (200 trees, depth 6); AUC and the top-5 features."""
    x = pd.concat([x_train, x_test], ignore_index=True).fillna(0.0)
    y = np.r_[np.zeros(len(x_train)), np.ones(len(x_test))]
    rf = RandomForestClassifier(n_estimators=200, max_depth=6, random_state=seed, n_jobs=4)
    pred = cross_val_predict(rf, x, y, cv=StratifiedKFold(5, shuffle=True, random_state=seed), method="predict_proba")[
        :, 1
    ]
    auc = float(roc_auc_score(y, pred))
    imp = pd.Series(rf.fit(x, y).feature_importances_, index=x.columns).sort_values(ascending=False)
    return auc, [(str(k), float(v)) for k, v in imp.head(5).items()]


def verdict(auc: float) -> str:
    return "pass" if auc <= 0.60 else ("warning" if auc <= 0.70 else "BLOCK")


def fold_adversarial(
    x: pd.DataFrame, meta: pd.DataFrame, train_anchors: list[pd.Timestamp], test_anchor: pd.Timestamp,
    broadcast: list[str],
) -> dict[str, Any]:  # fmt: skip
    """AUC on the cross-sectional value columns (blocking), with flags (informational) and with the broadcast
    regime columns (informational; anchor-constant columns separate train from test by construction)."""
    tr = meta["anchor"].isin(train_anchors).to_numpy() & meta["labelled"].to_numpy()
    te = (meta["anchor"] == test_anchor).to_numpy()
    cross = [c for c in value_columns(list(x.columns)) if c not in broadcast]
    out: dict[str, Any] = {}
    out["auc_values"], out["top_values"] = adversarial_auc(x.loc[tr, cross], x.loc[te, cross])
    flags = [c for c in x.columns if c.startswith(("isna_", "na_layout_")) and c.removeprefix("isna_") not in broadcast]
    out["auc_with_flags"], out["top_with_flags"] = adversarial_auc(x.loc[tr, cross + flags], x.loc[te, cross + flags])
    if broadcast:
        out["auc_with_broadcast"], _ = adversarial_auc(x.loc[tr, cross + broadcast], x.loc[te, cross + broadcast])
    out["verdict"] = verdict(out["auc_values"])
    return out


def ood_share(
    x: pd.DataFrame, meta: pd.DataFrame, train_anchors: list[pd.Timestamp], test_anchor: pd.Timestamp, seed: int = 0
) -> float:
    """Share of the test cross-section above the P99 of the training isolation-forest scores."""
    cols = value_columns(list(x.columns))
    tr = meta["anchor"].isin(train_anchors).to_numpy() & meta["labelled"].to_numpy()
    te = (meta["anchor"] == test_anchor).to_numpy()
    iso = IsolationForest(n_estimators=300, contamination="auto", random_state=seed, n_jobs=4).fit(
        x.loc[tr, cols].fillna(0.0)
    )
    s_tr = -iso.score_samples(x.loc[tr, cols].fillna(0.0))
    s_te = -iso.score_samples(x.loc[te, cols].fillna(0.0))
    return float((s_te > np.quantile(s_tr, 0.99)).mean())
