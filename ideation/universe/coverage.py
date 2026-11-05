"""02 §0.1 coverage reproduction under 02's own definition, reported beside the stitched-universe numbers.

02 §0.1 method: rank EQ/BE symbols that traded on the anchor session by their trailing-90-day average turnover
(averaged over the days they traded). Map each symbol to a company by ISIN (``listing_history.isin =
company.isin``), then by today's ``nse_symbol``, then through the ``nse_symbol_change`` chain. A name "has
fundamentals" if it has an annual Sales, Revenue or Net Profit fact with ``period_end`` in ``(A − 610d, A − 60d]``.

Method differences from P1's universe, documented:

* 02 ranks by a 90-day traded-days average; ``in_u1000`` uses the zero-filled ADV63 plus trading-days rules.
* 02 maps symbols; P1 stitches securities, which also links ISIN changes and pre-2011 blank-ISIN rows. Both are
  computed here: ``*_02map`` uses 02's mapping, ``*_stitched`` uses ``security_map.company_id``.
* Facts come through the PitView (``available_from <= A``). 02 filtered only on ``period_end``, so the two can
  differ for a period ending exactly 60 days before A.

The rename chain uses identity hindsight (renames after A), exactly like 02's probe. This is a coverage report,
not a feature.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pandas as pd

if TYPE_CHECKING:
    from ideation.pit.contract import PitStore

REFERENCE_02 = {  # 02 §0.1 table: anchor -> (traded, top500 with fund, top1000 with fund, all traded with fund)
    "2016-03-31": {"traded": 1526, "top500": 0.836, "top1000": 0.804, "all": 0.735, "rename_only": 26},
    "2019-03-29": {"traded": 1635, "top500": 0.898, "top1000": 0.886, "all": 0.818, "rename_only": 15},
    "2022-03-31": {"traded": 1879, "top500": 0.948, "top1000": 0.933, "all": 0.877, "rename_only": 13},
    "2025-03-28": {"traded": 2306, "top500": 0.972, "top1000": 0.949, "all": 0.882, "rename_only": 5},
}


def _chain_map(symbol_change: pd.DataFrame, comp_by_sym: dict[str, int]) -> dict[str, int]:
    """old symbol → company via the forward rename chain (any hop may land on a current nse_symbol)."""
    nxt: dict[str, list[tuple[Any, str]]] = {}
    for o, n, d in symbol_change[["old_symbol", "new_symbol", "effective_date"]].itertuples(index=False):
        nxt.setdefault(o, []).append((d, n))
    out: dict[str, int] = {}
    for start in nxt:
        seen, cur = {start}, start
        for _ in range(20):
            hops = sorted(nxt.get(cur, []))
            if not hops:
                break
            cur = hops[-1][1]
            if cur in seen:
                break
            seen.add(cur)
            if cur in comp_by_sym:
                out[start] = comp_by_sym[cur]
                break
    return out


def coverage_02(store: PitStore, anchor: Any, cfg: dict[str, Any]) -> dict[str, Any]:
    c02 = cfg["coverage_02"]
    view = store.as_of(anchor, record=False)
    n_sess = store.calendar.n_good_between(anchor - timedelta(days=int(c02["turnover_window_days"]) - 1), anchor)
    px = view.prices_raw(n_sess, columns=["security_id", "date", "symbol", "series", "turnover_lacs"])
    px = px[px["series"].isin(["EQ", "BE"])]
    on_a = px[px["date"] == anchor]
    traded = set(on_a["symbol"])
    avg = px[px["symbol"].isin(traded)].groupby("symbol")["turnover_lacs"].mean().sort_values(ascending=False)
    ranks = pd.DataFrame({"symbol": avg.index, "adv90": avg.to_numpy()})
    ranks["rank"] = range(1, len(ranks) + 1)
    sym2sec = on_a.drop_duplicates("symbol").set_index("symbol")["security_id"]
    ranks["security_id"] = ranks["symbol"].map(sym2sec)

    co = store.table("company")
    comp_by_isin = dict(zip(co["isin"], co["company_id"], strict=False))
    comp_by_sym = dict(zip(co["nse_symbol"], co["company_id"], strict=False))
    secs = store.table("security_map")
    live = secs[(secs["valid_from"] <= anchor) & (secs["valid_to"] >= anchor)]
    isin_at = live.drop_duplicates("symbol").set_index("symbol")["isin"]
    chain = _chain_map(store.table("symbol_change"), comp_by_sym)
    by_isin = ranks["symbol"].map(isin_at).map(comp_by_isin)
    by_sym = ranks["symbol"].map(comp_by_sym)
    by_chain = ranks["symbol"].map(chain)
    ranks["cid_02"] = by_isin.fillna(by_sym).fillna(by_chain)
    ranks["rename_only"] = by_isin.isna() & by_sym.isna() & by_chain.notna()
    stitched = secs.drop_duplicates("security_id").set_index("security_id")["company_id"]
    ranks["cid_stitched"] = ranks["security_id"].map(stitched)

    f = view.fundamentals("A", c02["fund_metric_ids"])
    lo = anchor - timedelta(days=int(c02["fund_period_end_max_days"]))
    hi = anchor - timedelta(days=int(c02["fund_period_end_min_days"]))
    has = set(f.loc[(f["period_end"] > lo) & (f["period_end"] <= hi), "company_id"].dropna().astype(int))
    for k in ("02map", "stitched"):
        cid = ranks["cid_02" if k == "02map" else "cid_stitched"]
        ranks[f"mapped_{k}"] = cid.notna()
        ranks[f"fund_{k}"] = cid.isin(has)

    def share(col: str, top: int | None) -> float:
        r = ranks if top is None else ranks[ranks["rank"] <= top]
        return round(float(r[col].mean()), 4)

    out = {
        "anchor": str(anchor),
        "n_traded": len(ranks),
        "adv90_rank500_lakh": float(ranks.loc[ranks["rank"] == 500, "adv90"].iloc[0]) if len(ranks) >= 500 else None,
        "adv90_rank1000_lakh": float(ranks.loc[ranks["rank"] == 1000, "adv90"].iloc[0]) if len(ranks) >= 1000 else None,
        "rename_only_top1000": int(ranks.loc[ranks["rank"] <= 1000, "rename_only"].sum()),
    }
    for k in ("02map", "stitched"):
        out[f"top500_mapped_{k}"] = int(ranks.loc[ranks["rank"] <= 500, f"mapped_{k}"].sum())
        out[f"top500_fund_{k}"] = share(f"fund_{k}", 500)
        out[f"top1000_fund_{k}"] = share(f"fund_{k}", 1000)
        out[f"all_fund_{k}"] = share(f"fund_{k}", None)
    ref = REFERENCE_02.get(str(anchor))
    if ref:
        out["ref_02"] = ref
        out["delta_pp_02map"] = {
            k: round(100 * (out[f"{k}_fund_02map"] - ref[k]), 2) for k in ("top500", "top1000", "all")
        }
        out["delta_pp_stitched"] = {
            k: round(100 * (out[f"{k}_fund_stitched"] - ref[k]), 2) for k in ("top500", "top1000", "all")
        }
    return out
