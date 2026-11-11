"""02 T-17 (runtime half): the adapter in backtest mode refuses every banned source, before connecting."""

from __future__ import annotations

import pytest

from ideation.lake import queries as Q
from ideation.lake.adapter import BannedSourceError, LakeMode, LakeReader, check_query
from ideation.lake.bans import BANS, find_banned
from ideation.lake.queries import NamedQuery

# A DSN that cannot connect: proves the refusal happens before any connection attempt.
DEAD = LakeReader("postgresql://ideation_rw@127.0.0.1:1/none", mode=LakeMode.BACKTEST, connect_timeout_s=1)

BANNED_SQL = {
    "market_snapshot": "select * from equity.market_snapshot",
    "yf_snapshot": "select * from equity.yf_snapshot",
    "v_latest_snapshot": "select * from equity.v_latest_snapshot",
    "analyst_estimate": "select * from equity.analyst_estimate",
    "valuation_history": "select * from equity.valuation_history",
    "dq_company_score": "select * from equity.dq_company_score",
    "dq_daily": "select * from equity.dq_daily",
    "dq_issue": "select * from equity.dq_issue",
    "v_quality_scorecard": "select * from equity.v_quality_scorecard",
    "v_quality_summary": "select * from equity.v_quality_summary",
    "completeness": "select * from equity.completeness",
    "completeness_daily": "select * from equity.completeness_daily",
    "v_document_completeness": "select * from equity.v_document_completeness",
    "data_gap": "select * from equity.data_gap",
    "v_coverage": "select * from equity.v_coverage",
    "v_source_coverage": "select * from equity.v_source_coverage",
    "equity.universe": 'select * from "equity"."universe" where universe_code = %s',
    "universe_candidate": "select * from equity.universe_candidate",
    "is_active": "select company_id from equity.company where is_active",
    "price_history": "select * from equity.price_history",
    "news_article": "select * from equity.news_article",
    "fpi_flow_daily": "select * from macro.fpi_flow_daily",
    "eps_estimate": "select eps_estimate from equity.earnings_event",
    "surprise_pct": "select surprise_pct from equity.earnings_event",
    "financials_asof": "select * from equity.financials_asof(now())",
    "v_financials_asof": "select * from equity.v_financials_asof(now())",
    "financial_fact_history": "select * from equity.financial_fact_history",
    "historical_prices_history": "select * from equity.historical_prices_history",
    "growth_summary": "select * from equity.growth_summary",
    "expert_call": "select * from ideation.expert_call",
    "expert_call_symbol": "select * from ideation.expert_call_symbol",
    "expert_extraction_cache": "select * from ideation.expert_extraction_cache",
    "expert_signal_ledger": "select * from ideation.expert_signal_ledger",
    "v_expert_active_calls": "select * from ideation.v_expert_active_calls",
    "TTM": "select * from equity.financial_fact where period_type = 'TTM'",
    "historical_prices.close": "select historical_prices.close from equity.historical_prices",
}


def test_every_ban_has_a_case() -> None:
    assert {b.name for b in BANS} == set(BANNED_SQL)


@pytest.mark.parametrize("name", sorted(BANNED_SQL))
def test_backtest_mode_raises_on_each_banned_source(name: str) -> None:
    query = NamedQuery(f"probe_{name}", BANNED_SQL[name], sources=())
    assert name in find_banned(BANNED_SQL[name])
    with pytest.raises(BannedSourceError):
        DEAD.fetch(query)
    with pytest.raises(BannedSourceError):
        DEAD.copy_arrow(query)


@pytest.mark.parametrize("name", sorted(BANNED_SQL))
def test_live_mode_does_not_ban(name: str) -> None:
    check_query(NamedQuery("q", BANNED_SQL[name], ()), None, LakeMode.LIVE)


def test_declared_sources_are_checked() -> None:
    q = NamedQuery("q", "select * from equity.v_something_new", sources=("equity.market_snapshot",))
    with pytest.raises(BannedSourceError):
        check_query(q, None, LakeMode.BACKTEST)


@pytest.mark.parametrize("params", [("TTM",), {"pt": "ttm"}, (["A", "TTM"],)])
def test_ttm_via_parameters_is_banned(params: object) -> None:
    q = NamedQuery("q", "select * from equity.financial_fact where period_type = any(%s)", ())
    with pytest.raises(BannedSourceError):
        check_query(q, params, LakeMode.BACKTEST)


@pytest.mark.parametrize(
    "sql",
    [
        "select * from equity.universe_pit",  # PIT universe: allowed
        "select * from equity.historical_prices",
        "select * from equity.financial_fact where period_type in ('A', 'Q')",
        "select symbol, isin from equity.listing_history",
        "select * from equity.earnings_event_dates",  # identifier boundaries: not 'eps_estimate'
    ],
)
def test_allowed_sources_pass(sql: str) -> None:
    check_query(NamedQuery("q", sql, ()), None, LakeMode.BACKTEST)


def test_every_named_query_is_backtest_safe_or_live_tooling() -> None:
    """Named queries in lake/queries.py must not touch banned sources; any future live-only one must be listed.
    A query with ``snapshot_exempt`` is snapshot-only (05 O5) and must still be refused in backtest mode."""
    live_only: set[str] = set()
    snapshot_only = {"earnings_vendor_full"}
    for value in vars(Q).values():
        if not isinstance(value, NamedQuery) or value.name in live_only:
            continue
        if value.name in snapshot_only:
            with pytest.raises(BannedSourceError):
                check_query(value, None, LakeMode.BACKTEST)
            check_query(value, None, LakeMode.SNAPSHOT)
        else:
            assert not value.snapshot_exempt, value.name
            check_query(value, None, LakeMode.BACKTEST)
            check_query(value, None, LakeMode.SNAPSHOT)


def test_snapshot_exemption_is_narrow() -> None:
    """The vendor exemption lifts only the bans the query names; any other banned source still raises."""
    q = NamedQuery("q", "select eps_estimate from equity.market_snapshot", (), snapshot_exempt=("eps_estimate",))
    with pytest.raises(BannedSourceError, match="market_snapshot"):
        check_query(q, None, LakeMode.SNAPSHOT)
    with pytest.raises(BannedSourceError):
        check_query(Q.EARNINGS_VENDOR_FULL, None, LakeMode.BACKTEST)
    check_query(Q.EARNINGS_VENDOR_FULL, None, LakeMode.SNAPSHOT)
    plain = NamedQuery("p", "select eps_estimate from equity.earnings_event", ())
    with pytest.raises(BannedSourceError):
        check_query(plain, None, LakeMode.SNAPSHOT)  # no exemption declared: still banned
    exempt = [v.name for v in vars(Q).values() if isinstance(v, NamedQuery) and v.snapshot_exempt]
    assert exempt == ["earnings_vendor_full"]


def test_only_named_queries_are_accepted() -> None:
    with pytest.raises(TypeError):
        check_query("select 1", None, LakeMode.LIVE)  # type: ignore[arg-type]
