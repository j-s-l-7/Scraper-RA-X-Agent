"""Stitched prices, security-level price factors, jump detector and rights flags (02 §3.2, 03 §C5). No lake access.

Factor convention (lake ``adj_factor``): ``price_after = factor × price_before``. A factor takes effect at the first
trade on or after its ex-date (``eff_date``). The adjusted log return of the trade interval ending at ``eff_date``
is ``ln(c_i / c_{i-1}) − ln(F_i)``, where ``F_i`` is the product of the factors effective there.

**Sanity check (causal).** yfinance factors are keyed on today's symbol and are often mis-dated (the ex-date
frequently precedes the NSE jump by 3-8 sessions). Each split or bonus factor is tested against the observed bhav
ratio ``c_i / c_{i-1}``. It is kept at the first interval, nearest first over trade offsets −3..+10, where it explains
the move better than no factor (``|ln obs − ln F| < |ln obs|``; away from offset 0 also ``< ½|ln F|``, and only
factors with F ≤ 0.67 search beyond ±2 trades). If no interval fits, it is dropped. Where a split or bonus is
placed, a residual ratio that still looks like a corporate action adds a ``jump_residual`` factor, because the lake
factor can be incomplete (BAJFINANCE 2025-06-16: split 0.5 in the lake, bhav ratio 0.10, so a missing 4:1 bonus).
A later factor is matched against the residual ratio (observed ratio divided by the factors already placed at that
trade), so a split and a bonus stored on different ex-dates for one jump (BAJFINANCE 2025-06,
TECHM 2015-03, ONGC 2011-02) are both kept. When several split or bonus rows share a date
(GLOBE 2021-07-29: split 0.2 and bonus 0.667), the combined product and each single factor are all candidates. The
row's ``available_from`` is the last trade date the decision examined, so the check never looks past its own
availability. Dividend factors below 0.95 get the same test; smaller ones are always kept.

**Jump detector.** It runs only for uncovered securities (no ``adj_factor`` rows; 02 §3.2), and never on a
previous close below ₹5 (tick noise). It
fires on an overnight ratio between consecutive trades (at most 5 sessions apart) that is ≤ 0.70 or ≥ 1.8 and lies
within 10% (log) of a corporate-action ratio ``k``. The move must also come with turnover above 2× the median of the
previous 20 trades, or be ≤ 0.55 or ≥ 4 (the ±20% circuit bands make such moves implausible as market moves). The
factor is ``k``, not the observed ratio, so the day's residual return is kept. These are flagged
``jump`` / ``jump_covered``. ``available_from`` is the jump date (causal).

Rights issues have no factor (02 §0.3), so prices stay unadjusted. Their ex-dates are kept as label flags.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

JUMP_K_DOWN = (1 / 2, 1 / 3, 1 / 4, 1 / 5, 1 / 6, 1 / 10, 1 / 20, 1 / 25, 1 / 50, 1 / 100, 2 / 3, 2 / 5, 3 / 5)
JUMP_K_UP = (2.0, 5.0, 10.0)
JUMP_TOL_LOG = 0.10
JUMP_MAX_GAP_SESSIONS = 5
JUMP_NEAR_FACTOR_SESSIONS = 3
DIV_CHECK_BELOW = 0.95
SPLIT_SEARCH_OFFSETS = (0, 1, -1, 2, -2, 3, -3, 4, 5, 6, 7, 8, 9, 10)
"""Trade offsets searched for a split/bonus jump, nearest first. yfinance ex-dates often precede the NSE bhav jump
by 3-8 sessions (P1 probe: KARDA +4, INOXWIND +4, VSTIND +5, POWERMECH +6, SARVESHWAR +8)."""
DIV_SEARCH_OFFSETS = (0, 1, -1, 2, -2)
FAR_SEARCH_MIN_LOG = 0.4
"""Only factors with |ln F| >= 0.4 (F <= 0.67: 1:2 bonus, splits) search the far offsets; a small factor would match
ordinary noise there. Small factors search ±2 trades."""
JUMP_MIN_PREV_CLOSE = 5.0
"""Penny stocks move in ₹0.05 ticks (0.10 → 0.05 is a 'ratio 0.5'); the detector ignores closes below ₹5."""


@dataclass
class FactorResult:
    factors: pd.DataFrame
    """security_id, ex_date, eff_date, factor, kind, source, flag, role, available_from."""
    rights: pd.DataFrame
    """security_id, ex_date, detail, available_from."""
    stats: dict[str, Any] = field(default_factory=dict)


def _company_security(segments: pd.DataFrame) -> dict[int, int]:
    """company_id → security_id (if a company maps to several securities, the one trading most recently)."""
    s = segments.dropna(subset=["company_id"]).sort_values("valid_to")
    return {int(c): int(sid) for c, sid in zip(s["company_id"], s["security_id"], strict=True)}


def _symbol_security(segments: pd.DataFrame) -> dict[str, int]:
    """symbol → security of the latest segment that used it (adj_factor is keyed on today's symbol)."""
    s = segments.sort_values("valid_to")
    return dict(zip(s["symbol"], s["security_id"], strict=True))


def _score(obs: float, f: float) -> float:
    return abs(math.log(obs) - math.log(f))


def build_factors(
    prices: pd.DataFrame,
    adj_factor: pd.DataFrame,
    corporate_action: pd.DataFrame,
    segments: pd.DataFrame,
    company: pd.DataFrame,
) -> FactorResult:
    stats: dict[str, Any] = {}
    sym2sec = _symbol_security(segments)
    cid2sec = _company_security(segments)
    comp_sym = dict(zip(company["nse_symbol"], company["company_id"], strict=False))
    af = adj_factor.copy()
    af["security_id"] = [
        cid2sec.get(comp_sym.get(s, -1), sym2sec.get(s)) for s in af["symbol"]
    ]  # company route first, symbol fallback
    stats["factor_rows"] = len(af)
    stats["factor_rows_unmapped"] = int(af["security_id"].isna().sum())
    af = af.dropna(subset=["security_id"])
    af["security_id"] = af["security_id"].astype("int64")

    px = prices[["security_id", "date", "close", "turnover_lacs"]].sort_values(["security_id", "date"])
    groups = {int(k): g for k, g in px.groupby("security_id", sort=False)}
    out: list[dict[str, Any]] = []
    counts: dict[str, int] = {}

    def bump(k: str) -> None:
        counts[k] = counts.get(k, 0) + 1

    for sid, g_af in af.groupby("security_id", sort=False):
        g = groups.get(int(sid))
        if g is None:
            bump("factor_security_without_prices")
            continue
        t = np.asarray(g["date"].tolist(), dtype="datetime64[D]")
        c = g["close"].to_numpy(dtype=float)
        placed = np.zeros(len(t))  # sum of ln F already placed at each trade (split+bonus on nearby dates)
        placed_sb_avail: dict[int, np.datetime64] = {}  # trade index -> latest availability of a split/bonus there
        for ex, rows in g_af.groupby("ex_date", sort=True):
            ex64 = np.datetime64(ex, "D")
            i = int(np.searchsorted(t, ex64, side="left"))
            for kind_group, krows in (
                ("splitbonus", rows[rows["kind"].isin(["split", "bonus"])]),
                ("dividend", rows[rows["kind"] == "dividend"]),
            ):
                if krows.empty:
                    continue
                facs = krows["factor"].astype(float).tolist()
                kinds = krows["kind"].tolist()
                combined = float(np.prod(facs))
                if kind_group == "dividend" and combined >= DIV_CHECK_BELOW:
                    if i >= len(t) or i < 1:
                        bump("dividend_outside_trading")
                        continue
                    for f, k in zip(facs, kinds, strict=True):
                        out.append(_row(sid, ex, t[i], f, k, "adj_factor", "", max(ex64, t[i])))
                        placed[i] += math.log(f)
                    continue
                cands = [(combined, "+".join(kinds), facs)]
                if len(facs) > 1:
                    cands += [(f, k, [f]) for f, k in zip(facs, kinds, strict=True)]
                chosen = None
                examined = ex64
                big = abs(math.log(combined)) >= FAR_SEARCH_MIN_LOG
                offsets = SPLIT_SEARCH_OFFSETS if (kind_group == "splitbonus" and big) else DIV_SEARCH_OFFSETS
                for off in offsets:
                    j = i + off
                    if j < 1 or j >= len(t):
                        continue
                    examined = max(examined, t[j], t[min(i, len(t) - 1)])
                    obs = c[j] / c[j - 1] / math.exp(placed[j])
                    if not (obs > 0):
                        continue
                    best = min(cands, key=lambda x: _score(obs, x[0]))
                    sc = _score(obs, best[0])
                    if sc < abs(math.log(obs)) and (off == 0 or sc < 0.5 * abs(math.log(best[0]))):
                        chosen = (j, best)
                        break
                if chosen is None:
                    if i >= len(t) or i < 1:  # on/before the first trade (pre-2010) or after the last: no effect
                        bump(f"{kind_group}_outside_trading")
                        eff = t[min(i, len(t) - 1)]
                        for f, k in zip(facs, kinds, strict=True):
                            out.append(_row(sid, ex, eff, f, k, "adj_factor", "outside_trading", max(ex64, eff),
                                            apply=False))  # fmt: skip
                        continue
                    bump(f"{kind_group}_dropped_no_jump")
                    for f, k in zip(facs, kinds, strict=True):
                        out.append(_row(sid, ex, t[i], f, k, "adj_factor", "dropped", examined, apply=False))
                    continue
                j, (_f, _k, used) = chosen
                flag = "" if j == i else "redated"
                if j != i:
                    bump(f"{kind_group}_redated")
                if len(used) < len(facs):
                    bump(f"{kind_group}_partial_combo")
                used_set = list(used)
                for f, k in zip(facs, kinds, strict=True):
                    if f in used_set:
                        used_set.remove(f)
                        out.append(_row(sid, ex, t[j], f, k, "adj_factor", flag, max(examined, t[j])))
                        placed[j] += math.log(f)
                        if kind_group == "splitbonus":
                            placed_sb_avail[j] = max(placed_sb_avail.get(j, t[j]), examined, t[j])
                    else:
                        out.append(_row(sid, ex, t[j], f, k, "adj_factor", "dropped_combo", examined, apply=False))
        # residual detector where a split/bonus was placed: the lake factor can be incomplete (BAJFINANCE
        # 2025-06-16 has the 1:2 split but not the 4:1 bonus; bhav ratio 0.10 = 0.5 × 0.2)
        for j, avail in sorted(placed_sb_avail.items()):
            r = c[j] / c[j - 1] / math.exp(placed[j])
            k = _jump_k(r) if c[j - 1] >= JUMP_MIN_PREV_CLOSE else None
            if k is not None:
                out.append(_row(sid, t[j].astype(object), t[j], k, "jump", "detector", "jump_residual", avail))
                placed[j] += math.log(k)
                bump("jump_residual")
    stats.update({f"check_{k}": v for k, v in sorted(counts.items())})
    fac = pd.DataFrame(out)
    applied = fac[fac["apply"]]
    stats["factors_applied"] = {k: int(v) for k, v in applied["kind"].value_counts().items()}

    # jump detector
    kept_sb = applied[applied["kind"].isin(["split", "bonus"])]
    near: dict[int, np.ndarray] = {
        int(k): np.asarray(g["eff_date"].tolist(), dtype="datetime64[D]") for k, g in kept_sb.groupby("security_id")
    }
    covered = set(int(x) for x in af["security_id"].unique())
    jumps = detect_jumps(px[~px["security_id"].isin(covered)], near, covered)  # 02 §3.2: uncovered names only
    stats["jumps"] = {k: int(v) for k, v in jumps["flag"].value_counts().items()} if len(jumps) else {}
    fac = pd.concat([fac, jumps], ignore_index=True)
    # split/bonus, and residual jumps standing in for a bonus missing from the lake, convert share bases (03 §C2)
    fac["role"] = np.where(
        fac["kind"].isin(["split", "bonus"]) | (fac["flag"] == "jump_residual"), "basis_conversion", "data"
    )
    fac = fac.sort_values(["security_id", "eff_date", "kind", "factor"]).reset_index(drop=True)

    # rights (no factor): flag ex-dates
    ra = corporate_action[corporate_action["action_type"] == "rights"].copy()
    ra["security_id"] = ra["company_id"].map(cid2sec)
    stats["rights_rows"] = len(ra)
    stats["rights_unmapped"] = int(ra["security_id"].isna().sum())
    ra = ra.dropna(subset=["security_id"])
    rights = pd.DataFrame(
        {
            "security_id": ra["security_id"].astype("int64"),
            "ex_date": ra["ex_date"],
            "detail": ra["detail"],
            "available_from": ra["ex_date"],
        }
    ).sort_values(["security_id", "ex_date"])
    return FactorResult(fac, rights.reset_index(drop=True), stats)


def face_value_now(issue_size: pd.DataFrame, segments: pd.DataFrame) -> pd.DataFrame:
    """Latest NSE face value per security (B2 market cap, 03 §C2). Role ``basis_conversion``: B2 may use today's
    FV only when no split/bonus took effect after the FY end, so FV-today equals FV-then (a unit, not information)."""
    sym2sec = _symbol_security(segments)
    iz = issue_size.sort_values(["symbol", "date"]).groupby("symbol", as_index=False).tail(1)
    iz = iz.assign(security_id=iz["symbol"].map(sym2sec)).dropna(subset=["security_id", "face_value"])
    iz = iz.sort_values("date").groupby("security_id", as_index=False).tail(1)
    return (
        pd.DataFrame(
            {
                "security_id": iz["security_id"].astype("int64"),
                "face_value": iz["face_value"].astype(float),
                "as_of": iz["date"],
                "available_from": iz["date"],
                "role": "basis_conversion",
            }
        )
        .sort_values("security_id")
        .reset_index(drop=True)
    )


def _row(
    sid: Any, ex: Any, eff: np.datetime64, f: float, kind: str, source: str, flag: str, avail: np.datetime64,
    *, apply: bool = True,
) -> dict[str, Any]:  # fmt: skip
    return {
        "security_id": int(sid),
        "ex_date": pd.Timestamp(ex).date(),
        "eff_date": eff.astype(object),
        "factor": float(f),
        "kind": kind,
        "source": source,
        "flag": flag,
        "apply": apply,
        "available_from": np.datetime64(avail, "D").astype(object),
    }


def _jump_k(ratio: float) -> float | None:
    """The corporate-action ratio ``k`` within 10% (log) of an overnight ratio <= 0.70 or >= 1.8, else None."""
    if not (ratio <= 0.70 or ratio >= 1.8) or not ratio > 0:
        return None
    ks = JUMP_K_DOWN if ratio < 1 else JUMP_K_UP
    k = min(ks, key=lambda x: abs(math.log(ratio) - math.log(x)))
    return k if abs(math.log(ratio) - math.log(k)) <= JUMP_TOL_LOG else None


def detect_jumps(px: pd.DataFrame, near: dict[int, np.ndarray], covered: set[int]) -> pd.DataFrame:
    """Split/bonus-like overnight jumps not explained by a kept factor (02 §3.2 detector, documented thresholds)."""
    p = px.copy()
    p["prev_close"] = p.groupby("security_id")["close"].shift(1)
    p["prev_date"] = p.groupby("security_id")["date"].shift(1)
    p["med_to"] = p.groupby("security_id")["turnover_lacs"].transform(
        lambda s: s.shift(1).rolling(20, min_periods=5).median()
    )
    p = p.dropna(subset=["prev_close"])
    p["ratio"] = p["close"] / p["prev_close"]
    cand = p[((p["ratio"] <= 0.70) | (p["ratio"] >= 1.8)) & (p["prev_close"] >= JUMP_MIN_PREV_CLOSE)].copy()
    if cand.empty:
        return _empty_jumps()
    d = np.asarray(cand["date"].tolist(), dtype="datetime64[D]")
    pd_ = np.asarray(cand["prev_date"].tolist(), dtype="datetime64[D]")
    cand = cand[np.busday_count(pd_, d) <= JUMP_MAX_GAP_SESSIONS + 2]  # weekdays; holidays make it generous
    rows = []
    for r in cand.itertuples(index=False):
        k = _jump_k(r.ratio)
        if k is None:
            continue
        strong = r.ratio <= 0.55 or r.ratio >= 4
        vol_ok = bool(r.med_to and r.med_to > 0 and r.turnover_lacs > 2 * r.med_to)
        if not (strong or vol_ok):
            continue
        sid = int(r.security_id)
        dd = np.datetime64(r.date, "D")
        nf = near.get(sid)
        if nf is not None and len(nf) and np.min(np.abs((nf - dd).astype(int))) <= JUMP_NEAR_FACTOR_SESSIONS + 2:
            continue
        rows.append(
            {
                "security_id": sid, "ex_date": r.date, "eff_date": r.date, "factor": float(k), "kind": "jump",
                "source": "detector", "flag": "jump_covered" if sid in covered else "jump", "apply": True,
                "available_from": r.date,
            }
        )  # fmt: skip
    return pd.DataFrame(rows) if rows else _empty_jumps()


def _empty_jumps() -> pd.DataFrame:
    cols = ["security_id", "ex_date", "eff_date", "factor", "kind", "source", "flag", "apply", "available_from"]
    return pd.DataFrame(columns=cols)


# ── adjusted series ─────────────────────────────────────────────────────────────────────────────────────────
def log_factor_by_trade(prices: pd.DataFrame, factors: pd.DataFrame, *, kinds: tuple[str, ...]) -> pd.Series:
    """Sum of ``ln F`` per (security_id, date) of applied factors of ``kinds`` (aligned to ``prices`` rows)."""
    f = factors[factors["apply"].astype(bool) & factors["kind"].isin(kinds)]
    lf = f.assign(lf=np.log(f["factor"].astype(float))).groupby(["security_id", "eff_date"])["lf"].sum()
    key = pd.MultiIndex.from_arrays([prices["security_id"], prices["date"]])
    return pd.Series(lf.reindex(key).fillna(0.0).to_numpy(), index=prices.index)


def back_adjust(prices: pd.DataFrame, factors: pd.DataFrame, anchor: Any, *, kinds: tuple[str, ...]) -> pd.Series:
    """Prices on the anchor's basis: ``raw_t × Π f`` over factors with ``t < eff_date ≤ anchor`` (02 §1.1, T-20).

    Callers pass only factors already available at the anchor (PitView filters ``available_from ≤ anchor``).
    """
    f = factors[
        factors["apply"].astype(bool) & factors["kind"].isin(kinds) & (factors["eff_date"] <= anchor)
    ].sort_values(["security_id", "eff_date"])
    out = prices["close"].astype(float).copy()
    if f.empty:
        return out
    for sid, g in f.groupby("security_id", sort=False):
        mask = (prices["security_id"] == sid).to_numpy()
        if not mask.any():
            continue
        dates = np.asarray(prices.loc[mask, "date"].tolist(), dtype="datetime64[D]")
        eff = np.asarray(g["eff_date"].tolist(), dtype="datetime64[D]")
        fac = g["factor"].to_numpy(dtype=float)
        # cumulative product of factors strictly after each date: suffix products over eff
        suffix = np.ones(len(fac) + 1)
        for k in range(len(fac) - 1, -1, -1):
            suffix[k] = suffix[k + 1] * fac[k]
        pos = np.searchsorted(eff, dates, side="right")  # factors with eff > date start at pos
        out.loc[mask] = out.loc[mask].to_numpy() * suffix[pos]
    return out
