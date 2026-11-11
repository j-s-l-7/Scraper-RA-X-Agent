# ruff: noqa: E501, RUF005
"""Portfolio-level comparison (05 §9.2): EW and CW top-25 books, monthly mark-to-market from the 1M labels.

* 1Y June sets are held 12 months, equal weight at entry and drifting (``v_i *= 1 + r_i``); the monthly return of name i
  in month k is the unsealed 1M label of the monthly anchor k months after the June anchor.
* Tactical sets use three staggered monthly tranches (Jegadeesh-Titman): the return in month t is the mean over the
  tranches formed at t, t-1, t-2 of their (equal- or capped-cap-weight) set return from the 1M label at anchor t.
* Costs: the retail model of ``config/costs.yaml`` on a 10 lakh notional (fractional shares; the integer-share floor is
  P7), charged on one-way turnover at each rebalance plus the DP charge per scrip sold. A name without a 1M label
  contributes a zero return for that month (disclosed).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import yaml

from ideation.config import REPO_ROOT
from ideation.eval.bootstrap import stationary_indices


def load_costs() -> dict[str, Any]:
    return yaml.safe_load((REPO_ROOT / "config" / "costs.yaml").read_text())


def buy_rate(c: dict[str, Any]) -> float:
    sebi = c["sebi_per_crore_inr"] / 1e7
    return c["stt_buy"] + c["nse_transaction"] + sebi + c["stamp_buy"] + c["gst"] * (c["nse_transaction"] + sebi)


def sell_rate(c: dict[str, Any]) -> float:
    sebi = c["sebi_per_crore_inr"] / 1e7
    return c["stt_sell"] + c["nse_transaction"] + sebi + c["gst"] * (c["nse_transaction"] + sebi)


def rebalance_cost(old: pd.Series | None, new: pd.Series, c: dict[str, Any]) -> tuple[float, float]:
    """(cost as a fraction of the notional, one-way turnover) of moving weights ``old`` -> ``new``."""
    old = old if old is not None else pd.Series(dtype=float)
    idx = old.index.union(new.index)
    o, n = old.reindex(idx).fillna(0.0), new.reindex(idx).fillna(0.0)
    d = n - o
    bought, sold = float(d.clip(lower=0).sum()), float((-d).clip(lower=0).sum())
    n_sold = int(((-d) > 1e-12).sum()) if len(old) else 0
    cost = bought * buy_rate(c) + sold * sell_rate(c) + n_sold * c["dp_charge_per_scrip_sell_inr"] / c["notional_inr"]
    return cost, max(bought, sold)


def capped_cw(mcap: pd.Series, cap: float = 0.10) -> pd.Series:
    """Cap-weights with a per-name cap (iterative redistribution)."""
    w = mcap.clip(lower=0) / mcap.clip(lower=0).sum()
    for _ in range(50):
        over = w > cap
        if not over.any():
            break
        excess = float((w[over] - cap).sum())
        w[over] = cap
        under = ~over
        w[under] += excess * w[under] / w[under].sum()
    return w


def weights(ids: pd.Index, scheme: str, mcap: pd.Series) -> pd.Series:
    if scheme == "EW":
        return pd.Series(1.0 / len(ids), index=ids)
    m = mcap.reindex(ids).fillna(mcap.median())
    return capped_cw(m)


def series_1y(sets: dict[pd.Timestamp, pd.Series], ret1m: pd.DataFrame, monthly: list[pd.Timestamp], c: dict[str, Any]) -> pd.DataFrame:
    """Monthly net/gross returns of a June-rebalanced book. ``sets``: June anchor -> weights; ``ret1m``: anchor x security."""
    rows, prev = [], None
    for a in sorted(sets):
        w0 = sets[a]
        cost, turn = rebalance_cost(prev, w0, c)
        months = [m for m in monthly if m >= a][:12]
        v = w0.copy()
        for k, m in enumerate(months):
            r = ret1m.loc[m].reindex(v.index).fillna(0.0) if m in ret1m.index else pd.Series(0.0, index=v.index)
            pv0 = float(v.sum())
            v = v * (1 + r)
            gross = float(v.sum()) / pv0 - 1
            rows.append({"month": m, "gross": gross, "cost": cost if k == 0 else 0.0, "turnover": turn if k == 0 else 0.0})
        prev = (v / v.sum())
    return pd.DataFrame(rows).set_index("month")


def series_tranche(sets: dict[pd.Timestamp, pd.Series], ret1m: pd.DataFrame, c: dict[str, Any], tranches: int = 3) -> pd.DataFrame:
    """Monthly returns of the staggered-tranche book formed from monthly ``sets`` (anchor -> weights)."""
    anchors = sorted(sets)
    rows = []
    for i, t in enumerate(anchors):
        if i < tranches - 1 or t not in ret1m.index:
            continue
        rt = ret1m.loc[t]
        parts = [float((sets[anchors[i - j]] * rt.reindex(sets[anchors[i - j]].index).fillna(0.0)).sum()) for j in range(tranches)]
        gross = float(np.mean(parts))
        cost, turn = rebalance_cost(sets[anchors[i - tranches]] if i >= tranches else None, sets[t], c)
        rows.append({"month": t, "gross": gross, "cost": cost / tranches, "turnover": turn / tranches})
    return pd.DataFrame(rows).set_index("month")


def _mdd(r: np.ndarray) -> np.ndarray:
    v = np.cumprod(1 + r, axis=-1)
    peak = np.maximum.accumulate(np.concatenate([np.ones(v.shape[:-1] + (1,)), v], axis=-1), axis=-1)[..., 1:]
    return (v / peak - 1).min(axis=-1)


def metrics(net: pd.Series, bench_n500: pd.Series, bench_ew: pd.Series, turnover_monthly: float, rf: float, *, seed: int = 0,
            n_boot: int = 10_000) -> dict[str, Any]:  # fmt: skip
    r = net.dropna()
    n = len(r)
    ann = 12
    cagr = float((1 + r).prod() ** (ann / n) - 1)
    vol = float(r.std(ddof=1) * np.sqrt(ann))
    ex = r - rf / ann
    sharpe = float(ex.mean() / r.std(ddof=1) * np.sqrt(ann))
    dd = np.sqrt((np.minimum(ex, 0) ** 2).mean())
    sortino = float(ex.mean() / dd * np.sqrt(ann)) if dd > 0 else float("nan")
    b = bench_n500.reindex(r.index)
    beta = float(np.cov(r, b)[0, 1] / b.var(ddof=1)) if b.notna().all() and b.var() > 0 else float("nan")
    treynor = float(ex.mean() * ann / beta) if beta == beta and beta > 0 else float("nan")

    def ir(x: pd.Series) -> float:
        a = (r - x.reindex(r.index)).dropna()
        return float(a.mean() * ann / (a.std(ddof=1) * np.sqrt(ann))) if len(a) > 2 and a.std() > 0 else float("nan")

    rng = np.random.default_rng(seed)
    idx = stationary_indices(n, 3.0, n_boot, rng)
    draws = r.to_numpy()[idx]
    sh = (draws.mean(axis=1) - rf / ann) / draws.std(axis=1, ddof=1) * np.sqrt(ann)
    md = _mdd(draws)
    return {
        "months": n, "CAGR": cagr, "vol": vol, "Sharpe": sharpe, "Sortino": sortino, "Treynor": treynor, "beta": beta,
        "IR vs N500": ir(bench_n500), "IR vs EW": ir(bench_ew), "max DD": float(_mdd(r.to_numpy())),
        "turnover (annual one-way)": turnover_monthly * ann,
        "Sharpe CI90": (float(np.quantile(sh, 0.05)), float(np.quantile(sh, 0.95))),
        "MDD CI90": (float(np.quantile(md, 0.05)), float(np.quantile(md, 0.95))),
    }  # fmt: skip
