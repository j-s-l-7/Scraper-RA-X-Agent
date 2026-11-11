# ruff: noqa: E501
"""Forward-return ledger (04 §6). Pure computation + report; no database, no clock.

Entry = raw close of the first good session strictly after the post's IST date. A horizon return is filled only once the
last good session on or before ``entry_anchor + h`` months exists in the calendar and in the prices (03 §C3, same exit
rule as labels). Prices are raw bhav closes adjusted with ``adj_factor`` rows whose ex-date lies in (entry, exit]
(``price_after = factor x before``). Benchmark = index series close ratio over the same sessions.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pandas as pd

from ideation.pit.calendar import TradingCalendar

IST = ZoneInfo("Asia/Kolkata")
Z90 = 1.6448536269514722


def ist_date(ts: datetime) -> date:
    return (ts if ts.tzinfo else ts.replace(tzinfo=UTC)).astimezone(IST).date()


def entry_session(cal: TradingCalendar, posted_at: datetime) -> date | None:
    """First good session strictly after the post's IST calendar date (a post at 22:00 UTC is already next-day IST)."""
    return cal.first_good_after(ist_date(posted_at))


def _close(px: pd.Series, d: date) -> float | None:
    v = px.get(d)
    return None if v is None or pd.isna(v) else float(v)


def _factor(factors: pd.DataFrame, lo: date, hi: date) -> float:
    if factors.empty:
        return 1.0
    m = (factors["ex_date"] > lo) & (factors["ex_date"] <= hi)
    return float(factors.loc[m, "factor"].prod()) if m.any() else 1.0


def forward_returns(
    posted_at: datetime, px: pd.Series, factors: pd.DataFrame, bench: pd.Series, cal: TradingCalendar,
    horizons: tuple[int, ...],
) -> dict[str, float | date | None]:  # fmt: skip
    """``px``/``bench``: close indexed by session date. Returns entry_date, entry_px and ret_/bench_ per horizon."""
    out: dict[str, float | date | None] = {"entry_date": None, "entry_px": None}
    for h in horizons:
        out[f"ret_{h}m"] = out[f"bench_{h}m"] = None
    e = entry_session(cal, posted_at)
    if e is None:
        return out
    p0 = _close(px, e)
    if p0 is None:
        return out
    out["entry_date"], out["entry_px"] = e, p0
    b0 = _close(bench, e)
    for h in horizons:
        x = cal.exit_session(e, h)
        if x is None or x <= e:
            continue
        p1 = _close(px, x)
        if p1 is None:
            continue
        out[f"ret_{h}m"] = p1 / (p0 * _factor(factors, e, x)) - 1.0
        b1 = _close(bench, x)
        if b0 and b1:
            out[f"bench_{h}m"] = b1 / b0 - 1.0
    return out


def wilson(k: int, n: int, z: float = Z90) -> tuple[float, float]:
    if n == 0:
        return (math.nan, math.nan)
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, centre - half), min(1.0, centre + half))


@dataclass(frozen=True)
class Scorecard:
    author: str
    direction: str
    horizon: int
    n_calls: int
    n_matured: int
    mean_ret: float | None
    mean_excess: float | None
    hits: int
    lo: float | None
    hi: float | None


def scorecards(ledger: pd.DataFrame, horizons: tuple[int, ...]) -> list[Scorecard]:
    """``ledger``: author, direction, ret_<h>m, bench_<h>m. A hit = beats the benchmark for bullish, trails it for
    bearish/avoid ('watch' has no hit definition and is excluded)."""
    out: list[Scorecard] = []
    for (author, direction), g in ledger.groupby(["author", "direction"]):
        if direction == "watch":
            continue
        for h in horizons:
            m = g.dropna(subset=[f"ret_{h}m", f"bench_{h}m"])
            ex = m[f"ret_{h}m"] - m[f"bench_{h}m"]
            sign = 1 if direction == "bullish" else -1
            hits = int((sign * ex > 0).sum())
            lo, hi = wilson(hits, len(m)) if len(m) else (None, None)
            out.append(Scorecard(str(author), str(direction), h, len(g), len(m),
                                 float(m[f"ret_{h}m"].mean()) if len(m) else None,
                                 float(ex.mean()) if len(m) else None, hits, lo, hi))  # fmt: skip
    return out


def render_report(ledger: pd.DataFrame, horizons: tuple[int, ...], min_matured: int, ledger_start: date,
                  label: str, n_before_start: int) -> str:  # fmt: skip
    lines = [f"# Expert forward record since {ledger_start.strftime('%Y-%m')}", "",
         f"_{label}. Calls posted before {ledger_start} are stored but not scored ({n_before_start}). "
         "Nothing here feeds a score, rank or backtest._", ""]  # fmt: skip
    if ledger.empty:
        return "\n".join([*lines, "No scored calls yet."])
    for author, g in ledger.groupby("author"):
        lines += [f"## {author}: {len(g)} scored company-level calls", ""]
        cards = scorecards(g, horizons)
        total_matured = {h: sum(c.n_matured for c in cards if c.horizon == h) for h in horizons}
        if all(total_matured[h] < min_matured for h in horizons):
            lines += [f"Insufficient track record: fewer than {min_matured} matured calls at every horizon "
                  f"(matured: {', '.join(f'{h}M={total_matured[h]}' for h in horizons)}).", ""]  # fmt: skip
        lines += ["| direction | horizon | calls | matured | mean ret | mean excess | hits | hit rate (Wilson 90%) |",
              "|---|---|---|---|---|---|---|---|"]  # fmt: skip
        for c in cards:
            ok = c.n_matured >= min_matured
            rate = f"{c.hits / c.n_matured:.0%} [{c.lo:.0%}, {c.hi:.0%}]" if c.n_matured and ok else "n/a (insufficient)"
            ret = "n/a" if c.mean_ret is None else f"{c.mean_ret:+.1%}"
            exc = "n/a" if c.mean_excess is None else f"{c.mean_excess:+.1%}"
            lines.append(f"| {c.direction} | {c.horizon}M | {c.n_calls} | {c.n_matured} | {ret} | {exc} | {c.hits} | {rate} |")
        lines.append("")
    return "\n".join(lines)
