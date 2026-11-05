"""Per-anchor metrics (02 §5.1, 05 §2 tables R-A..R-C). Bucket conventions follow ``rank.buckets`` (D10/Q5 = top).

Definitions (printed in reports, 05 §11 item 4):
* hit rate = share of a bucket's names whose return beats the benchmark (default: the universe median);
* turnover = one-way name turnover of a set, ``1 − |prev ∩ cur| / |cur|``;
* cost-adjusted return = return − 2 · bps · turnover (a replaced name costs one sell and one buy, 03 §C12).
  For a Q5−Q1 spread pass the sum of both legs' turnovers.
"""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd

from ideation.rank.buckets import TABLE_ROWS, decile, membership, quintile
from ideation.rank.percentile import percentile

MIN_PAIRS = 3


def rank_ic(score: np.ndarray | pd.Series, label: np.ndarray | pd.Series) -> float:
    """Spearman rank correlation over pairs where both are present; NaN if < 3 pairs or a side is constant."""
    s = pd.Series(np.asarray(score, dtype=float))
    y = pd.Series(np.asarray(label, dtype=float))
    ok = s.notna() & y.notna()
    if ok.sum() < MIN_PAIRS:
        return float("nan")
    rs, ry = s[ok].rank().to_numpy(), y[ok].rank().to_numpy()
    if rs.std() == 0 or ry.std() == 0:
        return float("nan")
    return float(np.corrcoef(rs, ry)[0, 1])


def ic_by_anchor(df: pd.DataFrame, score: str = "score", label: str = "label", anchor: str = "anchor") -> pd.Series:
    """Rank IC per anchor (sorted by anchor)."""
    return df.groupby(anchor, sort=True).apply(lambda g: rank_ic(g[score], g[label]), include_groups=False)


def _buckets(score: pd.Series, q: int) -> pd.Series:
    p = percentile(score)
    return decile(p) if q == 10 else quintile(p)


def spread(score: pd.Series, ret: pd.Series, q: int = 5, stat: str = "mean") -> float:
    """Top-bucket minus bottom-bucket return (mean or median) within one cross-section."""
    b = _buckets(pd.Series(score, dtype=float), q)
    r = pd.Series(ret, dtype=float, index=b.index)
    agg = r.groupby(b).agg(stat)
    return float(agg.get(q, np.nan) - agg.get(1, np.nan))


def hit_rate(score: pd.Series, ret: pd.Series, bench: float | pd.Series | None = None, q: int = 10) -> float:
    """Share of the top bucket beating ``bench`` (default: the median return of the cross-section)."""
    r = pd.Series(ret, dtype=float)
    b = _buckets(pd.Series(score, dtype=float), q)
    ref = r.median() if bench is None else bench
    beat = (r > ref).where(r.notna())
    return float(beat[b == q].mean())


def top_vs_bench(score: pd.Series, ret: pd.Series, bench: float, q: int = 5) -> float:
    """Mean return of the top bucket minus a benchmark return (universe median, CW universe or N500 PR)."""
    b = _buckets(pd.Series(score, dtype=float), q)
    return float(pd.Series(ret, dtype=float)[b == q].mean() - bench)


def turnover(prev: Iterable[object], cur: Iterable[object]) -> float:
    prev_s, cur_s = set(prev), set(cur)
    if not cur_s:
        return float("nan")
    return 1.0 - len(prev_s & cur_s) / len(cur_s)


def cost_adjusted(ret: float, turnover_: float, bps_per_side: float = 12.0) -> float:
    return ret - 2.0 * bps_per_side / 1e4 * turnover_


def bucket_table(p: pd.Series, ret: pd.Series, bench: float | None = None) -> pd.DataFrame:
    """One cross-section: n, mean, median and hit rate for every R-A row (P99, P95, D10 … D1, P5, P1)."""
    r = pd.Series(ret, dtype=float, index=pd.Series(p).index)
    ref = r.median() if bench is None else bench
    m = membership(p)
    rows = []
    for row in TABLE_ROWS:
        x = r[m[row]].dropna()
        rows.append({"bucket": row, "n": len(x), "mean": x.mean(), "median": x.median(), "hit": (x > ref).mean()})
    return pd.DataFrame(rows).set_index("bucket")


def monotonicity(p: pd.Series, ret: pd.Series) -> float:
    """Spearman of decile index vs decile mean return (table R-C)."""
    d = decile(p)
    means = pd.Series(ret, dtype=float, index=d.index).groupby(d).mean()
    return rank_ic(means.index.to_numpy(dtype=float), means.to_numpy())


def tail_order(bucket_means: pd.Series) -> tuple[bool, bool]:
    """Dossier §4.2 high-conviction test: (P99 > P95 > D10 > D9, P1 < P5 < D1 < D2)."""
    m = bucket_means
    top = bool(m["P99"] > m["P95"] > m["D10"] > m["D9"])
    bottom = bool(m["P1"] < m["P5"] < m["D1"] < m["D2"])
    return top, bottom
