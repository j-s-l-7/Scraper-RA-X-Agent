"""Training-target variants (E1 axis "target"). They change ONLY what the model is trained on; every trial is scored
against the same raw ``ret_total`` (plus neutralised variants in the report).

* ``rg``       rank-gauss of ret_total within the anchor's universe (the current target).
* ``xsr``      cross-sectional rank, uniform in (-0.5, 0.5).
* ``ind``      industry-group-neutral: ret minus its industry-group median (groups < 8 names: the universe median),
               then rank-gauss across the universe.
* ``beta``     beta-neutral residual: per-anchor OLS of winsorised (1/99) ret on beta_252 (NaN beta -> median),
               residual then rank-gauss.
* ``shuffled`` the ``rg`` target permuted within each anchor (seeded): the null control, IC must be ~ 0.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ideation.rank.transform import rank_gauss

MIN_GROUP = 8
KINDS = ("rg", "xsr", "ind", "beta", "shuffled")


def _one(g: pd.DataFrame, kind: str, rng: np.random.Generator) -> pd.Series:
    r = g["ret_total"]
    ok = r.notna()
    if kind == "rg":
        return rank_gauss(r)
    if kind == "xsr":
        return (r.rank(method="average") - 0.5) / ok.sum() - 0.5
    if kind == "ind":
        med = r.groupby(g["industry_group"]).transform("median")
        n = r.groupby(g["industry_group"]).transform("count")
        x = r - med.where(n >= MIN_GROUP, r.median())
        return rank_gauss(x)
    if kind == "beta":
        w = r.clip(r.quantile(0.01), r.quantile(0.99))
        b = g["beta_252"].fillna(g["beta_252"].median())
        if b.std() == 0 or not ok.any():
            return rank_gauss(r)
        a = np.column_stack([np.ones(len(g)), b.to_numpy(float)])
        coef, *_ = np.linalg.lstsq(a[ok.to_numpy()], w[ok].to_numpy(float), rcond=None)
        return rank_gauss(pd.Series(np.where(ok, w.to_numpy(float) - a @ coef, np.nan), index=g.index))
    if kind == "shuffled":
        y = rank_gauss(r)
        v = y[ok].to_numpy()
        y = y.copy()
        y[ok] = rng.permutation(v)
        return y
    raise ValueError(kind)


def make_target(frame: pd.DataFrame, kind: str, seed: int = 0) -> pd.Series:
    """Target per row (NaN where unlabelled), computed anchor by anchor (never across anchors)."""
    if kind not in KINDS:
        raise ValueError(f"unknown target {kind!r}")
    rng = np.random.default_rng(seed)
    out = pd.Series(np.nan, index=frame.index)
    for _, idx in frame.groupby("anchor", sort=True).groups.items():
        out.loc[idx] = _one(frame.loc[idx], kind, rng).to_numpy()
    return out
