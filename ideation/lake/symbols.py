"""Calendar and security stitching (02 §2.2, §2.3), built from a snapshot. No lake access.

**Segments.** A segment is one (symbol, isin) pair from ``listing_history``, series EQ/BE/BZ, with
``first_seen..last_seen`` spanning those series. Bhav has no ISIN column, so a bhav row (symbol, date) takes the ISIN
of the segment covering that date.

**Securities** are unions of segments joined by any of these rules (02 §2.2):

* **isin**: the same non-empty ISIN under another symbol. Renames keep the ISIN (ZOMATO → ETERNAL).
* **symbol_change**: an ``nse_symbol_change`` old → new row whose ``effective_date`` is within ±10 sessions of the
  old symbol's last_seen; the new symbol's segment must start within ±10 sessions of the change.
* **contiguity**: the same symbol, with the next segment starting within 10 sessions of the previous one's last_seen.
  This covers face-value splits, which change the ISIN, and the blank ISINs before 2011-06. A gap above 10 sessions
  means the symbol was reused, so the link is rejected.

Inside a security, overlapping segments of different symbols are trimmed: the later segment wins from its first
session, and the overlap is counted.

**company_id** comes from today's ``company`` table. It is matched first by ISIN (any segment ISIN equals
``company.isin``), then by symbol (``company.nse_symbol`` equals the symbol of the security's latest segment, and
that segment is the latest use of the symbol). Anything else has no company (uncovered or delisted).

The map uses hindsight on identity only: a later rename or ISIN change ties earlier rows to today's company. It
never uses a later price or fact, and it is built once per snapshot. It is exempt from the future-poison fixture
(role ``identity``), and the bias block discloses this.

``security_id`` is a stable 31-bit hash of the security's earliest segment (symbol, isin, first_seen). Ids
therefore survive snapshot refreshes. Collisions raise.
"""

from __future__ import annotations

import zlib
from collections import defaultdict, deque
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any

import numpy as np
import pandas as pd

from ideation.pit.calendar import TradingCalendar

STITCH_SERIES = ("EQ", "BE", "BZ")
MAX_GAP_SESSIONS = 10


# ── calendar ────────────────────────────────────────────────────────────────────────────────────────────────
def build_calendar(bhav_dates: pd.DataFrame, nsei_dates: pd.Series) -> pd.DataFrame:
    """``session, in_bhav, in_index, bhav_missing, n_eq`` from the first bhav session on (02 §2.3).

    ``in_bhav`` means EQ/BE/BZ rows exist that day. Index-only sessions are ``bhav_missing``.
    """
    b = bhav_dates.loc[bhav_dates["n_eq"] > 0, ["date", "n_eq"]].rename(columns={"date": "session"})
    first = b["session"].min()
    idx = pd.DataFrame({"session": sorted({d for d in nsei_dates if d >= first})})
    cal = b.merge(idx.assign(in_index=True), on="session", how="outer")
    cal["in_bhav"] = cal["n_eq"].notna()
    cal["in_index"] = cal["in_index"].fillna(False).astype(bool)
    cal["n_eq"] = cal["n_eq"].fillna(0).astype("int64")
    cal["bhav_missing"] = ~cal["in_bhav"]
    return cal.sort_values("session").reset_index(drop=True)[["session", "in_bhav", "in_index", "bhav_missing", "n_eq"]]


# ── stitching ───────────────────────────────────────────────────────────────────────────────────────────────
@dataclass
class StitchResult:
    segments: pd.DataFrame
    """security_id, symbol, isin, valid_from, valid_to, company_id, link_method, company_link, series_last."""
    stats: dict[str, Any] = field(default_factory=dict)


class _UF:
    def __init__(self, n: int) -> None:
        self.p = list(range(n))

    def find(self, a: int) -> int:
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


def stable_id(symbol: str, isin: str, first_seen: Any) -> int:
    return zlib.crc32(f"{symbol}|{isin}|{first_seen}".encode()) & 0x7FFFFFFF


def build_segments(listing: pd.DataFrame) -> pd.DataFrame:
    lh = listing[listing["series"].isin(STITCH_SERIES)].copy()
    lh["isin"] = lh["isin"].fillna("")
    lh = lh.sort_values(["symbol", "isin", "last_seen", "series"])
    seg = (
        lh.groupby(["symbol", "isin"], as_index=False, sort=True)
        .agg(first_seen=("first_seen", "min"), last_seen=("last_seen", "max"), series_last=("series", "last"))
        .sort_values(["symbol", "first_seen", "isin"])
        .reset_index(drop=True)
    )
    return seg


def stitch(
    listing: pd.DataFrame, symbol_change: pd.DataFrame, company: pd.DataFrame, cal: TradingCalendar
) -> StitchResult:
    seg = build_segments(listing)
    n = len(seg)
    uf = _UF(n)
    edges: list[tuple[int, int, str]] = []
    gpos = lambda d: cal.good_index(d)  # noqa: E731 - session position for gap arithmetic
    seg["p_first"] = [gpos(d) for d in seg["first_seen"]]
    seg["p_last"] = [gpos(d) for d in seg["last_seen"]]
    stats: dict[str, Any] = defaultdict(int)

    # (c) same-symbol contiguity (also pre-2011 blank ISIN → first ISIN); reject reuse gaps
    for _sym, g in seg.groupby("symbol", sort=False):
        idx = g.index.tolist()
        for a, b in pairwise(idx):
            gap = seg.at[b, "p_first"] - seg.at[a, "p_last"]
            if gap <= MAX_GAP_SESSIONS:
                edges.append((a, b, "contiguity"))
            else:
                stats["contiguity_rejected_reuse"] += 1
    # (a) same non-empty ISIN under different symbols
    for _isin, g in seg[seg["isin"] != ""].groupby("isin", sort=False):
        idx = g.sort_values("first_seen").index.tolist()
        for a, b in pairwise(idx):
            edges.append((a, b, "isin"))
    # (b) nse_symbol_change: old's last segment ±10 sessions of the change; new's segment starting near it
    by_symbol = {s: g.sort_values("first_seen") for s, g in seg.groupby("symbol", sort=False)}
    for old, new, eff in symbol_change[["old_symbol", "new_symbol", "effective_date"]].itertuples(index=False):
        if old not in by_symbol or new not in by_symbol:
            continue
        if eff < cal.good[0].astype(object):
            continue
        pe = gpos(eff)
        olds = by_symbol[old]
        cand_old = olds[(olds["p_last"] - pe).abs() <= MAX_GAP_SESSIONS]
        news = by_symbol[new]
        cand_new = news[(news["p_first"] - pe).abs() <= MAX_GAP_SESSIONS]
        if cand_old.empty or cand_new.empty:
            stats["symbol_change_unmatched"] += 1
            continue
        edges.append((int(cand_old.index[-1]), int(cand_new.index[0]), "symbol_change"))
        stats["symbol_change_linked"] += 1
    for a, b, _m in edges:
        uf.union(a, b)
    seg["root"] = [uf.find(i) for i in range(n)]

    # company mapping (today's company table; ISIN first, then current symbol)
    comp_by_isin = dict(zip(company["isin"], company["company_id"], strict=False))
    comp_by_sym = dict(zip(company["nse_symbol"], company["company_id"], strict=False))
    last_use = seg.groupby("symbol")["last_seen"].max()
    seg["cid_isin"] = seg["isin"].map(comp_by_isin)
    sec_company: dict[int, tuple[int | None, str]] = {}
    for root, g in seg.groupby("root", sort=False):
        isin_hits = g.dropna(subset=["cid_isin"]).sort_values("last_seen")
        if len(isin_hits):
            cids = isin_hits["cid_isin"].astype(int).unique()
            if len(cids) > 1:
                stats["security_multiple_isin_companies"] += 1
            sec_company[root] = (int(isin_hits["cid_isin"].iloc[-1]), "isin")
            continue
        latest = g.sort_values("last_seen").iloc[-1]
        cid = comp_by_sym.get(latest["symbol"])
        if cid is not None and latest["last_seen"] == last_use[latest["symbol"]]:
            sec_company[root] = (int(cid), "symbol")
        else:
            sec_company[root] = (None, "none")
    # if two securities claim one company by symbol while another has it by ISIN, keep only the ISIN claim
    claims: dict[int, list[tuple[int, str]]] = defaultdict(list)
    for root, (cid, how) in sec_company.items():
        if cid is not None:
            claims[cid].append((root, how))
    for cl in claims.values():
        if len(cl) > 1 and any(h == "isin" for _, h in cl):
            for root, how in cl:
                if how == "symbol":
                    sec_company[root] = (None, "none")
                    stats["symbol_claim_dropped"] += 1
        if len(cl) > 1:
            stats["companies_with_multiple_securities"] += 1

    # link_method per segment: BFS from the root segment over labelled edges
    adj: dict[int, list[tuple[int, str]]] = defaultdict(list)
    for a, b, m in edges:
        adj[a].append((b, m))
        adj[b].append((a, m))
    method = [""] * n
    for root, g in seg.groupby("root", sort=False):
        cid, _how = sec_company[root]
        start = int(g.dropna(subset=["cid_isin"]).index[0]) if g["cid_isin"].notna().any() else int(g.index[-1])
        method[start] = "seed" if cid is not None else "own"
        q = deque([start])
        while q:
            u = q.popleft()
            for v, m in adj[u]:
                if not method[v]:
                    method[v] = m
                    q.append(v)
    seg["link_method"] = method

    # security ids, trimming of overlapping segments inside a security
    rows = []
    ids: dict[int, int] = {}
    for root, g in seg.groupby("root", sort=False):
        g = g.sort_values(["first_seen", "symbol"])
        first = g.iloc[0]
        sid = stable_id(first["symbol"], first["isin"], first["first_seen"])
        if sid in ids.values():
            raise RuntimeError(f"security_id collision for {first['symbol']}")
        ids[root] = sid
        cid, how = sec_company[root]
        recs = g.to_dict("records")
        for i, r in enumerate(recs):
            valid_to = r["last_seen"]
            if i + 1 < len(recs) and recs[i + 1]["first_seen"] <= valid_to:
                nxt = recs[i + 1]["first_seen"]
                prev = cal.last_good_on_or_before(nxt - pd.Timedelta(days=1))
                if prev < r["first_seen"]:
                    stats["segments_fully_overlapped"] += 1
                    continue
                valid_to = prev
                stats["segments_trimmed"] += 1
            rows.append(
                {
                    "security_id": sid, "symbol": r["symbol"], "isin": r["isin"], "valid_from": r["first_seen"],
                    "valid_to": valid_to, "company_id": cid, "link_method": r["link_method"], "company_link": how,
                    "series_last": r["series_last"],
                }
            )  # fmt: skip
    out = pd.DataFrame(rows).sort_values(["security_id", "valid_from", "symbol"]).reset_index(drop=True)
    out["company_id"] = out["company_id"].astype("Int64")
    secs = out.groupby("security_id")
    stats.update(
        {
            "segments": n,
            "securities": int(secs.ngroups),
            "securities_with_company": int(out.dropna(subset=["company_id"])["security_id"].nunique()),
            "companies_mapped": int(out["company_id"].nunique()),
            "multi_segment_securities": int((secs.size() > 1).sum()),
            "link_method_counts": out["link_method"].value_counts().to_dict(),
            "company_link_counts": out.drop_duplicates("security_id")["company_link"].value_counts().to_dict(),
            "edges": pd.Series([m for *_, m in edges]).value_counts().to_dict(),
        }
    )
    return StitchResult(out, dict(stats))


def assign_security(bhav: pd.DataFrame, segments: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    """Attach ``security_id`` (and the segment ISIN) to bhav rows by (symbol, date in [valid_from, valid_to])."""
    seg = segments[["symbol", "valid_from", "valid_to", "security_id", "isin"]].copy()
    b = bhav.copy()
    b["_d"] = pd.to_datetime(b["date"])
    seg["_f"] = pd.to_datetime(seg["valid_from"])
    seg["_t"] = pd.to_datetime(seg["valid_to"])
    b = b.sort_values("_d")
    seg = seg.sort_values("_f")
    m = pd.merge_asof(b, seg, left_on="_d", right_on="_f", by="symbol", direction="backward")
    ok = m["security_id"].notna() & (m["_d"] <= m["_t"])
    stats = {"bhav_rows": len(b), "unassigned_rows": int((~ok).sum())}
    m = m[ok].drop(columns=["_d", "_f", "_t", "valid_from", "valid_to"])
    m["security_id"] = m["security_id"].astype("int64")
    dup = m.duplicated(["security_id", "date"], keep=False)
    stats["duplicate_security_date_rows"] = int(dup.sum())
    if dup.any():  # two symbols of one security on the same day: keep the higher-turnover row (rename overlap)
        m = m.sort_values(["security_id", "date", "turnover_lacs"]).drop_duplicates(
            ["security_id", "date"], keep="last"
        )
    return m.sort_values(["date", "security_id"]).reset_index(drop=True), stats


def segments_np_dates(df: pd.DataFrame, cols: tuple[str, ...]) -> dict[str, np.ndarray]:
    return {c: np.asarray(df[c].tolist(), dtype="datetime64[D]") for c in cols}
