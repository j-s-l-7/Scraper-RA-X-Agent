"""Backtest ban list (02 §1.2 live-only rows, 02 T-17). One source for the adapter (runtime) and the static test.

Every entry is a current snapshot, a live-only dataset, or a survivorship filter: reading it in a backtest leaks
the future. Identifiers match on SQL identifier boundaries (``_`` counts as part of a name), so ``completeness``
does not match ``completeness_daily``. That is why each variant is listed explicitly.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final


class BannedSourceError(RuntimeError):
    """A backtest-mode read touched a source on the ban list."""


@dataclass(frozen=True)
class Ban:
    name: str
    pattern: re.Pattern[str]
    reason: str


_B = r"(?<![\w$])"  # identifier start boundary
_E = r"(?![\w$])"  # identifier end boundary


def _ident(name: str, reason: str) -> Ban:
    return Ban(name, re.compile(rf'{_B}"?{re.escape(name)}"?{_E}', re.IGNORECASE), reason)


BANS: Final[tuple[Ban, ...]] = (
    _ident("market_snapshot", "current snapshot"),
    _ident("yf_snapshot", "current snapshot"),
    _ident("v_latest_snapshot", "view over current snapshots"),
    _ident("analyst_estimate", "live-only: no history"),
    _ident("valuation_history", "built on today's restated EPS and adjusted prices"),
    _ident("dq_company_score", "current data-quality snapshot"),
    _ident("dq_daily", "current data-quality bookkeeping"),
    _ident("dq_issue", "current data-quality bookkeeping"),
    _ident("v_quality_scorecard", "view over current data-quality tables"),
    _ident("v_quality_summary", "view over current data-quality tables"),
    _ident("completeness", "current completeness snapshot"),
    _ident("completeness_daily", "current completeness bookkeeping"),
    _ident("v_document_completeness", "view over current completeness"),
    _ident("data_gap", "current completeness bookkeeping"),
    _ident("v_coverage", "view over current snapshots and live-only tables"),
    _ident("v_source_coverage", "view over live-only tables"),
    Ban(
        "equity.universe",
        re.compile(rf'{_B}"?equity"?\s*\.\s*"?universe"?{_E}', re.IGNORECASE),
        "selected by today's market cap (survivorship); use the ideation PIT universe",
    ),
    _ident("universe_candidate", "current universe candidates (survivorship)"),
    _ident("is_active", "today's listing status (survivorship)"),
    _ident("price_history", "screener prices exist only from 2025-11"),
    _ident("news_article", "live-only"),
    _ident("fpi_flow_daily", "live-only: 25 rows"),
    _ident("eps_estimate", "analyst expectation: live-only"),
    _ident("surprise_pct", "derived from analyst estimates: live-only"),
    _ident("financials_asof", "tracking began 2026-11-01; cannot reconstruct history"),
    _ident("v_financials_asof", "tracking began 2026-11-01; cannot reconstruct history"),
    _ident("financial_fact_history", "restatement log from 2026-11-01 only"),
    _ident("historical_prices_history", "price-revision log from 2026-11-01 only"),
    _ident("growth_summary", "screener's current compounded-growth summary"),
    Ban("TTM", re.compile(r"'ttm'", re.IGNORECASE), "TTM facts are a current snapshot (02 §1.2)"),
    Ban(
        "historical_prices.close",
        re.compile(r"historical_prices\s*\.\s*close\b", re.IGNORECASE),
        "not a raw close (03 §B2); never a valuation price",
    ),
)


def find_banned(text: str) -> list[str]:
    """Names of every ban whose pattern occurs in ``text``."""
    return [b.name for b in BANS if b.pattern.search(text)]


def find_banned_params(params: object) -> list[str]:
    """Bound parameters can smuggle ``'TTM'`` past the SQL text; check string values too."""
    values: Iterable[object]
    if params is None:
        return []
    if isinstance(params, dict):
        values = params.values()
    elif isinstance(params, list | tuple | set | frozenset):
        values = params
    else:
        values = [params]
    hits: list[str] = []
    for v in values:
        if isinstance(v, str) and v.strip().upper() == "TTM":
            hits.append("TTM")
        elif isinstance(v, list | tuple | set | frozenset):
            hits.extend(find_banned_params(v))
    return hits


def assert_allowed(sql: str, params: object = None, *, sources: Iterable[str] = ()) -> None:
    """Raise :class:`BannedSourceError` if the SQL text, declared sources or parameters hit the ban list."""
    hits = find_banned(sql) + find_banned_params(params)
    for s in sources:
        hits.extend(find_banned(s))
    if hits:
        raise BannedSourceError(f"banned in backtest mode: {sorted(set(hits))}")
