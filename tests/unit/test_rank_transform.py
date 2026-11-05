"""R1/R3/R4 (05 §2): per-cross-section transform, percentile and buckets."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from scipy.stats import norm

from ideation.rank.buckets import TABLE_ROWS, decile, membership, quintile, tail_flags
from ideation.rank.percentile import ideation_score, percentile, signed_score
from ideation.rank.transform import (
    TransformSpec,
    flag_columns,
    industry_z,
    rank_gauss,
    value_columns,
    winsorize,
    xs_transform,
)


def test_percentile_definition_ties_and_nan() -> None:
    p = percentile(pd.Series([10.0, 20.0, 30.0, 40.0]))
    assert p.tolist() == [0.125, 0.375, 0.625, 0.875]
    p = percentile(pd.Series([1.0, 1.0, 3.0, np.nan]))
    assert p.iloc[:3].tolist() == pytest.approx([(1.5 - 0.5) / 3, (1.5 - 0.5) / 3, (3 - 0.5) / 3])
    assert np.isnan(p.iloc[3])


def test_percentile_by_group() -> None:
    s = pd.Series([1.0, 2.0, 5.0, 6.0, 7.0])
    p = percentile(s, by=np.array(["a", "a", "b", "b", "b"]))
    assert p.tolist() == pytest.approx([0.25, 0.75, 1 / 6, 0.5, 5 / 6])


def test_scores() -> None:
    p = pd.Series([0.004, 0.5, 0.995])
    assert ideation_score(p).tolist() == [0, 50, 100]
    assert signed_score(p).tolist() == pytest.approx([-0.992, 0.0, 0.99])


def test_buckets_sizes_and_tails() -> None:
    p = percentile(pd.Series(np.arange(200, dtype=float)))
    d, q, t = decile(p), quintile(p), tail_flags(p)
    assert d.value_counts().sort_index().tolist() == [20] * 10
    assert q.value_counts().sort_index().tolist() == [40] * 5
    assert d.iloc[-1] == 10
    assert d.iloc[0] == 1
    assert t.sum().to_dict() == {"P99": 2, "P95": 10, "P5": 10, "P1": 2}
    m = membership(p)
    assert list(m.columns) == list(TABLE_ROWS)
    assert m["P99"].sum() == 2
    assert m["D10"].sum() == 20
    assert m.loc[m["P95"]].index.min() == 190  # tails are the extreme names


def test_decile_boundaries_exact() -> None:
    p = pd.Series([0.0999999999, 0.1, 0.3, 0.95, 0.9999, np.nan])
    assert decile(p).tolist()[:5] == [1, 2, 4, 10, 10]
    assert decile(p).isna().iloc[5]


def test_winsorize_and_dossier_median() -> None:
    x = pd.Series(np.r_[np.arange(98, dtype=float), 1e6, -1e6])
    w = winsorize(x)
    assert w.max() < 1e6
    assert w.min() > -1e6
    g = pd.Series(np.r_[np.arange(1, 101, dtype=float)])
    med = winsorize(g, growth=True, mode="dossier_median")
    lo, hi = np.quantile(g, [0.05, 0.95])
    outside = (g < lo) | (g > hi)
    assert outside.sum() == 10
    assert (med[outside] == np.median(g)).all()
    assert (med[~outside] == g[~outside]).all()
    assert winsorize(g, growth=True).max() == pytest.approx(hi)


def test_rank_gauss() -> None:
    r = rank_gauss(pd.Series([3.0, 1.0, 2.0, np.nan]))
    assert r.iloc[:3].tolist() == pytest.approx([norm.ppf(5 / 6), norm.ppf(1 / 6), 0.0])
    assert np.isnan(r.iloc[3])


def test_industry_z_fallback_chain() -> None:
    x = pd.Series(np.arange(20, dtype=float))
    group = pd.Series(["big"] * 10 + ["tiny"] * 3 + ["lone"] * 7)
    sector = pd.Series(["S1"] * 10 + ["S2"] * 10)
    z = industry_z(x, group, sector, min_group=8)
    big = x[:10]
    assert z[:10].tolist() == pytest.approx(((big - big.mean()) / big.std(ddof=0)).tolist())
    s2 = x[10:]  # tiny (3) and lone (7) fall back to sector S2 (10 names)
    assert z[10:].tolist() == pytest.approx(((s2 - s2.mean()) / s2.std(ddof=0)).tolist())
    z_all = industry_z(x, group, pd.Series(["S"] * 3 + ["T"] * 17).where(group != "big", "U"), min_group=30)
    assert z_all.tolist() == pytest.approx(((x - x.mean()) / x.std(ddof=0)).tolist())


def _panel(seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for a in pd.to_datetime(["2020-06-30", "2021-06-30"]):
        for i in range(40):
            rows.append({"anchor": a, "security_id": i, "industry_group": f"g{i % 3}", "sector": f"s{i % 2}"})
    df = pd.DataFrame(rows)
    df["roe"] = rng.normal(size=len(df))
    df["ep"] = rng.normal(size=len(df))
    df.loc[[3, 50], "roe"] = np.nan
    return df


def test_xs_transform_global_flags_and_fill() -> None:
    df = _panel()
    X = xs_transform(df, ["roe", "ep"], TransformSpec())
    assert list(X.columns) == ["roe", "ep", "isna_roe", "isna_ep"]
    assert X.loc[[3, 50], "roe"].tolist() == [0.0, 0.0]
    assert X.loc[[3, 50], "isna_roe"].tolist() == [1.0, 1.0]
    assert X["isna_ep"].sum() == 0
    assert value_columns(X.columns) == ["roe", "ep"]
    assert flag_columns(X.columns) == ["isna_roe", "isna_ep"]
    first = df["anchor"] == df["anchor"].min()
    expected = rank_gauss(df.loc[first, "ep"])
    assert X.loc[first, "ep"].tolist() == pytest.approx(expected.tolist())


def test_xs_transform_is_per_cross_section() -> None:
    """Adding or changing another anchor's rows never changes this anchor's matrix (02 §4.6)."""
    df = _panel()
    X = xs_transform(df, ["roe", "ep"], TransformSpec(kind="hybrid", priors={"ep": -1}))
    df2 = df.copy()
    later = df2["anchor"] == df2["anchor"].max()
    df2.loc[later, ["roe", "ep"]] = 1e9
    X2 = xs_transform(df2, ["roe", "ep"], TransformSpec(kind="hybrid", priors={"ep": -1}))
    pd.testing.assert_frame_equal(X.loc[~later], X2.loc[~later])
    assert {"roe", "ep", "ind_roe", "ind_ep"} <= set(X.columns)


def test_industry_sign_flip_and_layout_flags() -> None:
    df = _panel()
    layout = pd.DataFrame({"ep": df["security_id"] == 7}, index=df.index)
    X = xs_transform(df, ["roe", "ep"], TransformSpec(kind="industry", priors={"ep": -1}), na_layout=layout)
    plain = xs_transform(df, ["roe", "ep"], TransformSpec(kind="industry"), na_layout=layout)
    assert X["ep"].tolist() == pytest.approx((-plain["ep"]).tolist())
    lay = df["security_id"] == 7
    assert (X.loc[lay, "ep"] == 0).all()
    assert (X.loc[lay, "na_layout_ep"] == 1).all()
    assert (X.loc[lay, "isna_ep"] == 0).all()
    assert "na_layout_roe" not in X.columns
