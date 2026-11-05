"""PIT universes per anchor (05 §1 binding; 03 §C4 / 02 §2.1 as the ``in_u1000`` slice). Input: one ``PitView``.

One row per stitched security with at least one EQ/BE trade in the 63-session window.

* ``traded_ok``: traded in ≥ 50 of the last 63 good sessions and in at least one of the last 5.
* ``adv63_lakh``: Σ turnover over the 63 sessions / 63 (zero-filled mean; 02 §2 fixes the scraper's
  traded-days-only average).
* ``rank_adv``: 1 = most liquid among ``traded_ok`` names; the rest follow by ADV.
* ``in_u1000`` / ``in_u500``: ``traded_ok`` and ``rank_adv`` ≤ 1000 / 500 (03 §C4, kept as a slice per 05 O1).
* ``U_score`` (05 §1.1; ``scorable = in_u_score``). Reasons are assigned in this waterfall order:
  ``thin_trading``, ``adv_below_floor`` (ADV63 < ₹0.2 cr), ``too_new`` (< 126 listed sessions), ``no_company``,
  ``no_fundamentals_at_anchor``, ``mcap_unknown``, ``mcap_below_floor_100``, ``features_incomplete``. The last is a
  hook: off until P2 passes core-feature completeness. ``in_u_score_adv05`` is the ₹0.5 cr sensitivity.
* ``U_score_T`` (05 §1.2): ``thin_trading``, ``adv_below_floor``, ``too_new`` (< 252), then ``mcap_below_floor_100``
  where mcap is known, else ``adv_below_floor_unknown_mcap`` (ADV63 < ₹0.5 cr). Unmapped and delisted names count.

Listed sessions count good sessions from the stitched security's first appearance. A name already trading when
bhav starts (2010-01-04) gets a configurable credit, because its real listing date is earlier.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from ideation.universe.mcap import mcap_pit

if TYPE_CHECKING:
    from ideation.pit.contract import PitView

LAKH_PER_CR = 100.0
FY_METRICS = (12, 123, 21)  # annual Sales, Revenue, Net Profit: "an annual FY is available"
U_SCORE_ORDER = (
    "thin_trading", "adv_below_floor", "too_new", "no_company", "no_fundamentals_at_anchor", "mcap_unknown",
    "mcap_below_floor_100", "features_incomplete",
)  # fmt: skip
U_SCORE_T_ORDER = ("thin_trading", "adv_below_floor", "too_new", "mcap_below_floor_100", "adv_below_floor_unknown_mcap")


@dataclass(frozen=True)
class UniverseConfig:
    raw: dict[str, Any]

    def __getitem__(self, k: str) -> Any:
        return self.raw[k]

    @classmethod
    def from_methodology(cls, data: dict[str, Any]) -> UniverseConfig:
        return cls(data["default"]["universe"])


def _first_reason(conds: list[tuple[str, pd.Series]], index: pd.Index) -> pd.Series:
    out = pd.Series([None] * len(index), index=index, dtype=object)
    for reason, fails in conds:
        out = out.where(out.notna() | ~fails.fillna(False).astype(bool), reason)
    return out


def build_universe(
    view: PitView, cfg: UniverseConfig, *, feature_complete: pd.Series | None = None
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Members (all EQ/BE securities traded in the window) and the coverage summary for ``view.anchor``."""
    anchor = view.anchor
    n_win = int(cfg["adv_window_sessions"])
    sess = view.sessions(n_win)
    recent_start = sess[-int(cfg["recent_trade_sessions"])].astype(object)
    px = view.prices_raw(n_win, columns=["security_id", "date", "symbol", "series", "close", "turnover_lacs"])
    px = px[px["series"].isin(cfg["liquidity_series"])]
    px = px.sort_values(["security_id", "date"])
    px = px.assign(_d=pd.to_datetime(px["date"]))
    g = px.groupby("security_id", sort=True)
    last = g.tail(1).set_index("security_id")
    mem = pd.DataFrame(
        {
            "adv63_lakh": g["turnover_lacs"].sum() / n_win,
            "n_days63": g.size(),  # stitched prices have one row per (security, date)
            "last_trade": last["date"],
            "symbol": last["symbol"],
            "series": last["series"],
            "close": last["close"],
        }
    ).reset_index()
    traded_on_a = set(px.loc[px["date"] == anchor, "security_id"])
    mem["traded_ok"] = (mem["n_days63"] >= int(cfg["min_traded_sessions"])) & (mem["last_trade"] >= recent_start)
    mem = mem.sort_values(["traded_ok", "adv63_lakh", "security_id"], ascending=[False, False, True])
    mem["rank_adv"] = np.arange(1, len(mem) + 1)
    mem["in_u1000"] = mem["traded_ok"] & (mem["rank_adv"] <= int(cfg["top_n"]))
    mem["in_u500"] = mem["traded_ok"] & (mem["rank_adv"] <= int(cfg["report_slice"]))

    sec = view.securities()
    first_seen = sec.groupby("security_id")["valid_from"].min()
    company = sec.sort_values("valid_from").groupby("security_id")["company_id"].last()
    isin = sec.sort_values("valid_from").groupby("security_id")["isin"].last()
    lm = sec[["security_id", "link_method"]].drop_duplicates().sort_values(["security_id", "link_method"])
    link = lm.groupby("security_id")["link_method"].agg(",".join)
    mem["company_id"] = mem["security_id"].map(company).astype("Int64")
    mem["isin"] = mem["security_id"].map(isin)
    cal = view.calendar
    cal_start = cal.good[0].astype(object)
    credit = int(cfg["pre2010_listed_credit_sessions"])
    fs = mem["security_id"].map(first_seen)
    fs64 = np.asarray(fs.tolist(), dtype="datetime64[D]")
    upto = int(np.searchsorted(cal.good, np.datetime64(anchor, "D"), side="right"))
    mem["listed_sessions"] = (upto - np.searchsorted(cal.good, fs64, side="left")) + np.where(
        fs64 == np.datetime64(cal_start, "D"), credit, 0
    )

    # an annual FY available at anchor (fundamentals view: available_from <= anchor), ending within fy_max_age_days
    f = view.fundamentals("A", FY_METRICS)
    recent_fy = f[f["period_end"] >= anchor - timedelta(days=int(cfg["fy_max_age_days"]))]
    have_fy = set(recent_fy["company_id"].dropna().astype(int))
    mem["has_fy_at_anchor"] = mem["company_id"].isin(have_fy).fillna(False).astype(bool)

    mc = mcap_pit(view, mem[mem["company_id"].notna()], cfg["mcap"])
    mem = mem.merge(mc, on="security_id", how="left")
    mem["mcap_flag"] = mem["mcap_flag"].fillna(False).astype(bool)

    us, ut = cfg["u_score"], cfg["u_score_t"]
    adv_floor = float(us["adv63_floor_cr"]) * LAKH_PER_CR
    if feature_complete is not None:
        mem["feature_complete"] = mem["security_id"].map(feature_complete).astype(float)
    else:
        mem["feature_complete"] = np.nan
    fc_fail = mem["feature_complete"].notna() & (mem["feature_complete"] < float(us["min_feature_complete"]))
    mcap = mem["mcap_pit_cr"]
    mem["exclusion_reason"] = _first_reason(
        [
            ("thin_trading", ~mem["traded_ok"]),
            ("adv_below_floor", mem["adv63_lakh"] < adv_floor),
            ("too_new", mem["listed_sessions"] < int(cfg["min_listed_sessions"]["1Y"])),
            ("no_company", mem["company_id"].isna()),
            ("no_fundamentals_at_anchor", ~mem["has_fy_at_anchor"]),
            ("mcap_unknown", mcap.isna()),
            ("mcap_below_floor_100", mcap < float(us["mcap_floor_cr"])),
            ("features_incomplete", fc_fail),
        ],
        mem.index,
    )
    mem["in_u_score"] = mem["exclusion_reason"].isna()
    mem["scorable"] = mem["in_u_score"]
    mem["in_u_score_adv05"] = mem["in_u_score"] & (mem["adv63_lakh"] >= float(us["adv63_sensitivity_cr"]) * LAKH_PER_CR)
    adv_t = float(ut["adv63_floor_cr"]) * LAKH_PER_CR
    mem["u_score_t_reason"] = _first_reason(
        [
            ("thin_trading", ~mem["traded_ok"]),
            ("adv_below_floor", mem["adv63_lakh"] < adv_t),
            ("too_new", mem["listed_sessions"] < int(cfg["min_listed_sessions"]["tactical"])),
            ("mcap_below_floor_100", mcap.notna() & (mcap < float(ut["mcap_floor_cr"]))),
            ("adv_below_floor_unknown_mcap",
             mcap.isna() & (mem["adv63_lakh"] < float(ut["adv63_floor_unknown_mcap_cr"]) * LAKH_PER_CR)),
        ],
        mem.index,
    )  # fmt: skip
    mem["in_u_score_t"] = mem["u_score_t_reason"].isna()
    mem["anchor"] = anchor
    mem["available_from"] = anchor
    mem["link_methods"] = mem["security_id"].map(link)
    mem = mem.sort_values("rank_adv").reset_index(drop=True)
    coverage = summarize(mem, anchor, traded_on_a, cfg)
    return mem, coverage


def summarize(mem: pd.DataFrame, anchor: date, traded_on_a: set[int], cfg: UniverseConfig) -> dict[str, Any]:
    u1000, u500 = mem[mem["in_u1000"]], mem[mem["in_u500"]]
    with_fund = mem["company_id"].notna() & mem["has_fy_at_anchor"]
    reasons = mem["exclusion_reason"].value_counts().to_dict()
    cascade, remaining = [], len(mem)
    for r in U_SCORE_ORDER:
        remaining -= int(reasons.get(r, 0))
        cascade.append({"after": r, "n": remaining})
    us = mem[mem["in_u_score"]]
    return {
        "anchor": anchor,
        "n_rows": len(mem),
        "n_traded": len(traded_on_a),
        "n_traded_ok": int(mem["traded_ok"].sum()),
        "n_universe": len(u1000),
        "n_scorable": int(mem["in_u_score"].sum()),
        "n_u_score": int(mem["in_u_score"].sum()),
        "n_u_score_adv05": int(mem["in_u_score_adv05"].sum()),
        "n_u_score_t": int(mem["in_u_score_t"].sum()),
        "n_u1000": len(u1000),
        "reasons_u_score": {k: int(v) for k, v in reasons.items()},
        "reasons_u_score_t": {k: int(v) for k, v in mem["u_score_t_reason"].value_counts().items()},
        "u_score_cascade": cascade,
        "share_with_fund_top500": float(with_fund[mem["in_u500"]].mean()) if len(u500) else None,
        "share_with_fund_top1000": float(with_fund[mem["in_u1000"]].mean()) if len(u1000) else None,
        "share_u_score_in_u1000": float(u1000["in_u_score"].mean()) if len(u1000) else None,
        "adv_marginal_lakh": float(u1000["adv63_lakh"].min()) if len(u1000) else None,
        "mcap_marginal_cr": float(us["mcap_pit_cr"].min()) if len(us) else None,
        "mcap_src_u_score": {k: int(v) for k, v in us["mcap_src"].value_counts().items()},
        "mcap_flag_u_score": int(us["mcap_flag"].sum()),
        "u_score_be_series": int((us["series"] == "BE").sum()),
        "u_score_t_unmapped": int(mem.loc[mem["in_u_score_t"], "company_id"].isna().sum()),
        "u1000_mcap_ge_500_scorable_02": int(
            (u1000["company_id"].notna() & u1000["has_fy_at_anchor"] & (u1000["mcap_pit_cr"] >= cfg["mcap_floor_cr"])
             & (u1000["series"] == "EQ")).sum()
        ),
    }  # fmt: skip
