"""Idea selection from stored predictions (01 §5 steps 2-6). Ranking is the shipped model's score only.

Regime, RRG, overlays and risk flags change **conviction and narrative**, never the order. OOD names are listed
separately (conviction "unscorable") and are never ideas.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np
import pandas as pd
from dateutil.relativedelta import relativedelta

from ideation.ideas.overlay import C_BOOST, validate
from ideation.rank.conviction import LEVELS, UNSCORABLE, conviction

FIN = ("fin_layout", "fin_other")
RISK_FLAGS = ("high_leverage_p90", "negative_cfo_latest_fy", "promoter_selling_1y", "cooling_off_7d")
STOP_LOSS = {"1Y": 0.25}  # Balanced profile default (03 §A6); the profile table is P7's
HORIZON_LABEL = {"1Y": "1Y", "3M": "tactical 1-3M"}


@dataclass(frozen=True)
class IdeasConfig:
    top_n: int = 25
    avoid_n: int = 15
    max_per_group: int = 5
    min_mcap_cr: float = 500.0
    min_adv_cr: float = 2.0
    min_price: float = 20.0
    min_completeness: float = 0.6
    series: tuple[str, ...] = ("EQ",)

    @classmethod
    def from_methodology(cls, data: dict[str, Any]) -> IdeasConfig:
        d = data.get("ideas", {})
        keys = ("top_n", "avoid_n", "max_per_group", "min_mcap_cr", "min_adv_cr", "min_price", "min_completeness")
        return cls(**{k: d[k] for k in keys if k in d}, series=tuple(d.get("series", ("EQ",))))


def liquid_mask(df: pd.DataFrame, cfg: IdeasConfig) -> pd.Series:
    """Hard filters of 01 §5 step 2 (mcap, ADV20, price, series, completeness). OOD is handled separately."""
    return (
        (df["mcap_pit_cr"] >= cfg.min_mcap_cr)
        & (df["adv20_cr"] >= cfg.min_adv_cr)
        & (df["last_close"] >= cfg.min_price)
        & df["series"].isin(cfg.series)
        & (df["completeness"] >= cfg.min_completeness)
        & df["company_id"].notna()
    ).fillna(False)


def cap_per_group(df: pd.DataFrame, n: int, cap: int) -> pd.DataFrame:
    """Walk ``df`` in order, keep at most ``cap`` per industry_group, stop at ``n`` names."""
    taken: dict[Any, int] = {}
    keep = []
    for i, g in df["industry_group"].items():
        if taken.get(g, 0) < cap:
            keep.append(i)
            taken[g] = taken.get(g, 0) + 1
        if len(keep) >= n:
            break
    return df.loc[keep]


def conviction_for(row: Any, side: str, boost: bool) -> str:
    p = row.p if side == "long" else 1.0 - row.p
    return conviction(
        float(p),
        c=float(row.c_conf) if pd.notna(row.c_conf) else 1.0,
        risk_flag=any(f in RISK_FLAGS for f in row.flags) if side == "long" else False,
        regime_misaligned=bool(row.regime_misaligned) if side == "long" else False,
        financial=row.layout in FIN,
        xagent_consensus=boost and side == "long",
        ood=bool(row.ood),
    )


def invalidation(row: Any, horizon: str, as_of: date) -> list[str]:
    if horizon == "1Y":
        stop = row.last_close * (1 - STOP_LOSS["1Y"])
        return [
            f"price stop: close below {stop:,.2f} ({STOP_LOSS['1Y']:.0%} under {row.last_close:,.2f}, Balanced profile)",
            "re-rate at the next results: revenue YoY < 0 or OPM down 300 bp",
            "exit from the top 30% at the next scoring",
            "promoter selling above 2 pp in a quarter",
        ]
    expiry = as_of + relativedelta(months=3)
    sma = f"close below SMA50 ({row.sma50:,.2f})" if pd.notna(getattr(row, "sma50", np.nan)) else "close below SMA50"
    return [sma, "industry-group RRG quadrant turns Lagging", f"horizon expiry {expiry.isoformat()} (3M)"]


def risks(row: Any, labels: dict[str, str]) -> list[str]:
    out = []
    d = row.drivers
    if d:
        out += [f"weak {x['category']}: {x['feature']} ({x['contribution']:+.3f})" for x in d["bottom"]]
    out += [f"flag: {f}" for f in row.flags]
    if labels.get("vol") == "Stressed" and pd.notna(row.beta) and row.beta > 1.2:
        out.append(f"stressed volatility regime and beta {row.beta:.2f} > 1.2")
    if labels.get("narrow_rally") == "true" and pd.notna(row.mcap_pit_cr) and row.mcap_pit_cr < 5000:
        out.append("narrow rally and small cap")
    if row.layout in FIN:
        out.append("financial layout: conviction capped at Medium (03 A8)")
    return out


def select_ideas(
    pred: pd.DataFrame, horizon: str, cfg: IdeasConfig, as_of: date, labels: dict[str, str],
    effects: pd.DataFrame | None = None,
) -> dict[str, pd.DataFrame]:  # fmt: skip
    """``pred``: one row per name (columns of ``predict.assemble`` incl. company_id, symbol, p, score, ood...).

    Returns ``longs``, ``avoid``, ``ood`` (unscorable, with a reason), ``excluded`` (overlay), all sorted."""
    eff = validate(effects) if effects is not None else pd.DataFrame({"company_id": [], "action": []})
    pool = pred[liquid_mask(pred, cfg)].copy()
    ood = pool[pool["ood"].astype(bool)].copy()
    ood["conviction"] = UNSCORABLE
    pool = pool[~pool["ood"].astype(bool)]
    excl_ids = set(eff.loc[eff["action"] == "exclude", "company_id"])
    boost_ids = set(eff.loc[eff["action"] == "boost", "company_id"])
    excluded = pool[pool["company_id"].isin(excl_ids)].copy()
    cand = pool[~pool["company_id"].isin(excl_ids)].sort_values(["p", "security_id"], ascending=[False, True])
    longs = cap_per_group(cand, cfg.top_n, cfg.max_per_group).copy()
    bottom = pool[pool["decile"] == 1].sort_values(["p", "security_id"], ascending=[True, True])
    avoid = bottom.head(cfg.avoid_n).copy()
    conflict_ids = {c for c in boost_ids if c in set(pool.loc[pool["decile"] == 1, "company_id"])}
    for df, side in ((longs, "long"), (avoid, "avoid")):
        df["side"] = side
        df["rank"] = np.arange(1, len(df) + 1)
        df["boost"] = df["company_id"].isin(boost_ids - conflict_ids)
        df["conviction"] = [conviction_for(r, side, bool(r.boost)) for r in df.itertuples()]
        if side == "long":
            df["c_conf"] = np.where(df["boost"], np.minimum(df["c_conf"] * C_BOOST, 1.0), df["c_conf"])
        df["invalidation"] = [invalidation(r, horizon, as_of) for r in df.itertuples()]
        df["risk_list"] = [risks(r, labels) for r in df.itertuples()]
    longs["conflict"] = longs["company_id"].isin(conflict_ids)
    return {"longs": longs, "avoid": avoid, "ood": ood, "excluded": excluded}


def continuity(current: pd.DataFrame, previous: pd.DataFrame | None) -> tuple[pd.Series, pd.Series, pd.DataFrame]:
    """(continuity label per current row, prev_idea_id per current row, dropped rows of the previous list).

    ``previous``: ``idea_id, company_id, side, rank, symbol`` of the prior ok run for the same horizon (or None)."""
    if previous is None or previous.empty:
        return pd.Series("new", index=current.index), pd.Series(np.nan, index=current.index), pd.DataFrame()
    prev = previous.set_index(["company_id", "side"])
    lab, pid = [], []
    for r in current.itertuples():
        key = (r.company_id, r.side)
        if key in prev.index:
            lab.append("continuing")
            pid.append(prev.loc[key, "idea_id"])
        else:
            lab.append("new")
            pid.append(np.nan)
    keys = set(zip(current["company_id"], current["side"], strict=True))
    dropped = previous[[(c, s) not in keys for c, s in zip(previous["company_id"], previous["side"], strict=True)]]
    return pd.Series(lab, index=current.index), pd.Series(pid, index=current.index), dropped


_ = LEVELS
