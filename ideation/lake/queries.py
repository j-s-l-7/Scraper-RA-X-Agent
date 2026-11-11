"""Named lake queries: the only SQL that reads ``equity``/``macro`` (01 §1.1).

The adapter accepts only :class:`NamedQuery` objects, so ad-hoc SQL cannot reach the lake. Each query declares the
relations it reads (the ban list checks them as well as the text) and documents cost and index. The read session has
an empty ``search_path``, so every relation must be schema-qualified.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pyarrow as pa


@dataclass(frozen=True)
class NamedQuery:
    name: str
    sql: str
    sources: tuple[str, ...]
    doc: str = ""
    arrow_types: dict[str, pa.DataType] = field(default_factory=dict)
    """Explicit column types for COPY → Arrow (no inference surprises)."""
    snapshot_exempt: tuple[str, ...] = ()
    """Ban-list names this query may touch in ``LakeMode.SNAPSHOT`` only (05 O5: the vendor-estimate pull).
    BACKTEST mode ignores it and still refuses the query."""


# ── session / health ─────────────────────────────────────────────────────────────────────────────────────────
SESSION_SETTINGS = NamedQuery(
    "session_settings",
    "select current_user, current_setting('is_superuser'), current_setting('default_transaction_read_only'),"
    " current_setting('transaction_read_only'), current_setting('statement_timeout'),"
    " current_setting('application_name'), current_setting('search_path')",
    sources=(),
    doc="Identity and session guards of the read connection. Constant time.",
)

ROLE_ATTRIBUTES = NamedQuery(
    "role_attributes",
    "select rolsuper, rolbypassrls, rolcreaterole, rolcreatedb from pg_catalog.pg_roles where rolname = current_user",
    sources=("pg_catalog.pg_roles",),
    doc="Attributes of the connected role (03 §C1 expects bypassrls and nothing else).",
)

WRITE_GRANTS_ON_LAKE = NamedQuery(
    "write_grants_on_lake",
    """
    select n.nspname || '.' || c.relname
    from pg_catalog.pg_class c join pg_catalog.pg_namespace n on n.oid = c.relnamespace
    where n.nspname in ('equity', 'macro') and c.relkind in ('r', 'p', 'v', 'm')
      and (has_table_privilege(c.oid, 'INSERT') or has_table_privilege(c.oid, 'UPDATE')
           or has_table_privilege(c.oid, 'DELETE') or has_table_privilege(c.oid, 'TRUNCATE'))
    union all
    select n.nspname from pg_catalog.pg_namespace n
    where n.nspname in ('equity', 'macro') and has_schema_privilege(n.oid, 'CREATE')
    order by 1
    """,
    sources=("pg_catalog.pg_class", "pg_catalog.pg_namespace"),
    doc="Any write privilege the current role holds on the lake (must be empty). ~65 relations; < 50 ms.",
)

UNREADABLE_LAKE_TABLES = NamedQuery(
    "unreadable_lake_tables",
    """
    select n.nspname || '.' || c.relname
    from pg_catalog.pg_class c join pg_catalog.pg_namespace n on n.oid = c.relnamespace
    where n.nspname in ('equity', 'macro') and c.relkind in ('r', 'p', 'v', 'm')
      and not has_table_privilege(c.oid, 'SELECT')
    order by 1
    """,
    sources=("pg_catalog.pg_class", "pg_catalog.pg_namespace"),
    doc="Lake relations the role cannot SELECT (must be empty after bootstrap-role).",
)

RO_WRITE_PROBE = NamedQuery(
    "ro_write_probe",
    "create temp table ideation_ro_probe (x int)",
    sources=(),
    doc="Must fail with ReadOnlySqlTransaction on the read path. A temp table is session-local, so it is harmless.",
)

LISTING_HISTORY_COUNT = NamedQuery(
    "listing_history_count",
    "select count(*) from equity.listing_history",
    sources=("equity.listing_history",),
    doc="RLS-bypass proof (03 §B5): > 0 rows only if the role has BYPASSRLS. 16k rows; ~60 ms.",
)

SCHEMA_MIGRATIONS = NamedQuery(
    "schema_migrations",
    "select version, name, checksum from ideation.schema_migration order by version",
    sources=("ideation.schema_migration",),
    doc="Applied migrations (ideation schema, readable by its owner on the read path).",
)

# ── freshness ────────────────────────────────────────────────────────────────────────────────────────────────
BHAV_MAX_DATE = NamedQuery(
    "bhav_max_date",
    "select max(date) from equity.nse_bhav_full",
    sources=("equity.nse_bhav_full",),
    doc="Last bhavcopy session. Index nse_bhav_full_date_idx; < 10 ms.",
)

NSEI_SESSIONS_AFTER = NamedQuery(
    "nsei_sessions_after",
    "select max(date), count(*) filter (where date > %(after)s) from macro.series_daily where code = '^NSEI'",
    sources=("macro.series_daily",),
    doc="Last ^NSEI session and how many ^NSEI sessions bhav is missing. PK (code, date); < 10 ms.",
)

SCRAPE_RUNS_RUNNING = NamedQuery(
    "scrape_runs_running",
    """
    select count(*) filter (where started_at >= now() - make_interval(hours => %(active_hours)s)),
           count(*) filter (where started_at <  now() - make_interval(hours => %(active_hours)s)),
           max(started_at)
    from equity.scrape_run where status = 'running'
    """,
    sources=("equity.scrape_run",),
    doc="Scraper runs still marked running: recent (likely active) vs older (stale rows never closed).",
)

# ── bulk reads (COPY → Arrow) ────────────────────────────────────────────────────────────────────────────────
LISTING_HISTORY = NamedQuery(
    "listing_history",
    "select symbol, isin, series, first_seen, last_seen, is_listed_now from equity.listing_history"
    " order by symbol, series, isin",
    sources=("equity.listing_history",),
    doc="Every NSE (symbol, isin, series) ever seen, 2010+. 16k rows; ~100 ms. isin is '' (not NULL) pre-2011-06.",
    arrow_types={
        "symbol": pa.string(),
        "isin": pa.string(),
        "series": pa.string(),
        "first_seen": pa.date32(),
        "last_seen": pa.date32(),
        "is_listed_now": pa.bool_(),
    },
)


# ── Phase 1 snapshot (01 §1.2, 05 §10.3 P1). Run inside one REPEATABLE READ READ ONLY transaction. ────────────────
# Every query is backtest-safe (ban list) except EARNINGS_VENDOR_FULL, which carries a narrow ``snapshot_exempt``
# honoured only in LakeMode.SNAPSHOT. Rows are re-sorted in Arrow after the pull, so ORDER BY is omitted on the
# large tables (the sort would run inside the transaction).
_D, _S, _F, _I, _B = pa.date32(), pa.string(), pa.float64(), pa.int64(), pa.bool_()

LAKE_STATE = NamedQuery(
    "lake_state",
    """
    select (select max(date) from equity.nse_bhav_full) as max_bhav,
           (select max(date) from macro.series_daily where code = '^NSEI') as max_nsei,
           (select max(updated_at) from equity.financial_fact) as max_fact_ts,
           (select count(*) from equity.financial_fact) as n_fact,
           (select max(as_of) from equity.shares_outstanding) as max_shares,
           (select count(*) from equity.shares_outstanding) as n_shares,
           (select max(event_date) from equity.earnings_event) as max_event,
           (select count(*) from equity.earnings_event) as n_event,
           (select max(fetched_at) from equity.adj_factor) as max_factor_ts,
           (select count(*) from equity.adj_factor) as n_factor,
           (select count(*) from equity.listing_history) as n_listing,
           (select max(loaded_at) from equity.nse_symbol_change) as max_symchg_ts,
           (select count(*) from equity.company) as n_company,
           (select count(*) from equity.shareholding) as n_shp
    """,
    sources=(
        "equity.nse_bhav_full",
        "macro.series_daily",
        "equity.financial_fact",
        "equity.shares_outstanding",
        "equity.earnings_event",
        "equity.adj_factor",
        "equity.listing_history",
        "equity.nse_symbol_change",
        "equity.company",
        "equity.shareholding",
    ),
    doc="Lake fingerprint for the snapshot manifest and run_key. Index-only maxima plus counts; ~1-2 s.",
)

COMPANY = NamedQuery(
    "company",
    "select company_id, nse_symbol, bse_code, isin, name, sector, industry_group, industry, sub_industry,"
    " industry_code, has_consolidated from equity.company",
    sources=("equity.company",),
    doc="Today's company master (2.6k rows). Reference data: current labels, never a survivorship filter.",
    arrow_types={
        "company_id": _I,
        "nse_symbol": _S,
        "bse_code": _S,
        "isin": _S,
        "name": _S,
        "sector": _S,
        "industry_group": _S,
        "industry": _S,
        "sub_industry": _S,
        "industry_code": _S,
        "has_consolidated": _B,
    },
)

METRIC = NamedQuery(
    "metric",
    "select metric_id, section, name, unit, canonical_code, source from equity.metric",
    sources=("equity.metric",),
    doc="Fundamental line-item dictionary (~500 rows).",
    arrow_types={"metric_id": _I, "section": _S, "name": _S, "unit": _S, "canonical_code": _S, "source": _S},
)

SYMBOL_CHANGE = NamedQuery(
    "symbol_change",
    "select old_symbol, new_symbol, effective_date, company_name from equity.nse_symbol_change",
    sources=("equity.nse_symbol_change",),
    doc="NSE symbol changes since 1999 (1.1k rows).",
    arrow_types={"old_symbol": _S, "new_symbol": _S, "effective_date": _D, "company_name": _S},
)

NAME_CHANGE = NamedQuery(
    "name_change",
    "select symbol, old_name, new_name, effective_date from equity.nse_name_change",
    sources=("equity.nse_name_change",),
    doc="NSE name changes (2.1k rows).",
    arrow_types={"symbol": _S, "old_name": _S, "new_name": _S, "effective_date": _D},
)

BHAV = NamedQuery(
    "bhav",
    "select symbol, series, date, open, high, low, close, prev_close, ttl_trd_qnty, turnover_lacs, no_of_trades,"
    " deliv_qty, deliv_per from equity.nse_bhav_full where series in ('EQ', 'BE', 'BZ') and date >= %(since)s",
    sources=("equity.nse_bhav_full",),
    doc="NSE bhavcopy, EQ/BE/BZ only (SME series excluded), raw closes (not CA-adjusted). ~7.3M rows; ~25 s COPY.",
    arrow_types={
        "symbol": _S,
        "series": _S,
        "date": _D,
        "open": _F,
        "high": _F,
        "low": _F,
        "close": _F,
        "prev_close": _F,
        "ttl_trd_qnty": _I,
        "turnover_lacs": _F,
        "no_of_trades": _I,
        "deliv_qty": _I,
        "deliv_per": _F,
    },
)

BHAV_DATES = NamedQuery(
    "bhav_dates",
    "select date, count(*) as n_rows, count(*) filter (where series in ('EQ', 'BE', 'BZ')) as n_eq"
    " from equity.nse_bhav_full where date >= %(since)s group by date",
    sources=("equity.nse_bhav_full",),
    doc="Distinct bhav sessions (all series) for the calendar (02 §2.3). Seq scan, ~3 s.",
    arrow_types={"date": _D, "n_rows": _I, "n_eq": _I},
)

ADJ_FACTOR = NamedQuery(
    "adj_factor",
    "select symbol, ex_date, factor, kind, source from equity.adj_factor",
    sources=("equity.adj_factor",),
    doc="Split/bonus/dividend price factors keyed on today's symbol (26k rows). price_after = factor * before.",
    arrow_types={"symbol": _S, "ex_date": _D, "factor": _F, "kind": _S, "source": _S},
)

CORPORATE_ACTION = NamedQuery(
    "corporate_action",
    "select company_id, action_type, ex_date, value, detail, source from equity.corporate_action",
    sources=("equity.corporate_action",),
    doc="Dividend/split/bonus/rights events (28k rows); rights rows flag labels (02 §3.2).",
    arrow_types={"company_id": _I, "action_type": _S, "ex_date": _D, "value": _F, "detail": _S, "source": _S},
)

SHARES_OUTSTANDING = NamedQuery(
    "shares_outstanding",
    "select company_id, as_of, shares, source from equity.shares_outstanding",
    sources=("equity.shares_outstanding",),
    doc="yfinance shares as-of (raw, not restated). 2M rows; ~5 s.",
    arrow_types={"company_id": _I, "as_of": _D, "shares": _I, "source": _S},
)

FINANCIAL_FACT = NamedQuery(
    "financial_fact",
    "select company_id, basis, metric_id, period_type, period_end, value, updated_at from equity.financial_fact"
    " where period_type in ('A', 'Q', 'H')",
    sources=("equity.financial_fact",),
    doc="Annual/quarterly/half-year facts (8.2M rows; the current-snapshot rolling period type is excluded). ~25 s.",
    arrow_types={
        "company_id": _I,
        "basis": _S,
        "metric_id": _I,
        "period_type": _S,
        "period_end": _D,
        "value": _F,
        "updated_at": pa.timestamp("us", tz="UTC"),
    },
)

SHAREHOLDING = NamedQuery(
    "shareholding",
    "select company_id, frequency, category, period_end, value from equity.shareholding",
    sources=("equity.shareholding",),
    doc="Shareholding pattern by category, Y (2017+) and Q (2023+). 267k rows.",
    arrow_types={"company_id": _I, "frequency": _S, "category": _S, "period_end": _D, "value": _F},
)

EARNINGS_DATES = NamedQuery(
    "earnings_dates",
    "select company_id, event_date, (eps_reported is not null) as has_eps_reported, source from equity.earnings_event",
    sources=("equity.earnings_event",),
    doc="Results-event dates (no estimates; 05 §10.3). 29k rows. Future-dated rows are schedules.",
    arrow_types={"company_id": _I, "event_date": _D, "has_eps_reported": _B, "source": _S},
)

EARNINGS_VENDOR_FULL = NamedQuery(
    "earnings_vendor_full",
    "select company_id, event_date, eps_estimate, eps_reported from equity.earnings_event",
    sources=("equity.earnings_event",),
    doc="Vendor estimate vs reported EPS (05 O5): snapshot-mode only. Consumers: outcomes/analyst.py and "
    "features/events_vendor.py through PitView.earnings_vendor(); available_from = event_date + 1.",
    arrow_types={"company_id": _I, "event_date": _D, "eps_estimate": _F, "eps_reported": _F},
    snapshot_exempt=("eps_estimate",),
)

SERIES_DAILY = NamedQuery(
    "series_daily",
    "select code, date, value from macro.series_daily",
    sources=("macro.series_daily",),
    doc="Daily indices / FX / commodities (142k rows).",
    arrow_types={"code": _S, "date": _D, "value": _F},
)

SERIES_MONTHLY = NamedQuery(
    "series_monthly",
    "select code, period, value, base_year from macro.series_monthly",
    sources=("macro.series_monthly",),
    doc="MoSPI monthly series as revised today (19k rows).",
    arrow_types={"code": _S, "period": _D, "value": _F, "base_year": _S},
)

SERIES_ANNUAL = NamedQuery(
    "series_annual",
    "select code, year, value from macro.series_annual",
    sources=("macro.series_annual",),
    doc="World Bank annual series (1k rows).",
    arrow_types={"code": _S, "year": _I, "value": _F},
)

MACRO_SERIES = NamedQuery(
    "macro_series",
    "select code, name, source, unit, freq, base_year from macro.series",
    sources=("macro.series",),
    doc="Macro series dictionary.",
    arrow_types={"code": _S, "name": _S, "source": _S, "unit": _S, "freq": _S, "base_year": _S},
)

FPI_SECTOR = NamedQuery(
    "fpi_sector",
    "select report_date, sector, measure, route, inr_cr, usd_mn from macro.fpi_sector_fortnightly",
    sources=("macro.fpi_sector_fortnightly",),
    doc="FPI sector flows, fortnightly from 2020-05 (100k rows).",
    arrow_types={"report_date": _D, "sector": _S, "measure": _S, "route": _S, "inr_cr": _F, "usd_mn": _F},
)

ISSUE_SIZE = NamedQuery(
    "issue_size",
    "select symbol, date, series, face_value, issue_size from equity.nse_issue_size",
    sources=("equity.nse_issue_size",),
    doc="Face value and issued shares from 2024-02 (8.8k rows); B2 market-cap face value (03 §C2).",
    arrow_types={"symbol": _S, "date": _D, "series": _S, "face_value": _F, "issue_size": _I},
)

YF_ADJ_CLOSE = NamedQuery(
    "yf_adj_close",
    "select symbol, date, adj_close, volume from equity.historical_prices where date >= %(since)s",
    sources=("equity.historical_prices",),
    doc="yfinance total-return adj_close, ONLY for the T-22 label cross-check (never a valuation price). ~7M rows.",
    arrow_types={"symbol": _S, "date": _D, "adj_close": _F, "volume": _I},
)

DOCUMENTS_CATALOG = NamedQuery(
    "documents_catalog",
    "select company_id, doc_type, period_label, doc_date, source, fetch_source, page_count, char_count, pdf_sha256,"
    " text_path from equity.document where status = 'ok' and doc_type in ('concall_transcript', 'concall_ppt')",
    sources=("equity.document",),
    doc="Extracted concall transcripts and presentations (05 §7.1; ~54k rows, one scan, < 1 s). doc_date is month-"
    "granular. Reads no ban-listed source: no estimates, no snapshots, no status/completeness bookkeeping.",
    arrow_types={
        "company_id": _I,
        "doc_type": _S,
        "period_label": _S,
        "doc_date": _D,
        "source": _S,
        "fetch_source": _S,
        "page_count": _I,
        "char_count": _I,
        "pdf_sha256": _S,
        "text_path": _S,
    },
)

# ── expert ledger (04 §6; live-only consumer, ideation/experts) ────────────────────────────────────────────────
EXPERT_BHAV_CLOSES = NamedQuery(
    "expert_bhav_closes",
    "select symbol, date, close from equity.nse_bhav_full where symbol = any(%(symbols)s)"
    " and series in ('EQ', 'BE', 'BZ') and date >= %(since)s",
    sources=("equity.nse_bhav_full",),
    doc="Raw closes for a handful of symbols since a date (expert forward ledger). Index on (symbol, date); < 50 ms.",
)

EXPERT_BENCH = NamedQuery(
    "expert_bench",
    "select date, value from macro.series_daily where code = %(code)s and date >= %(since)s order by date",
    sources=("macro.series_daily",),
    doc="One index series since a date (expert ledger benchmark). PK (code, date); < 10 ms.",
)

EXPERT_ADJ_FACTOR = NamedQuery(
    "expert_adj_factor",
    "select symbol, ex_date, factor from equity.adj_factor where symbol = any(%(symbols)s) and ex_date >= %(since)s",
    sources=("equity.adj_factor",),
    doc="Split/bonus/dividend factors for a handful of symbols since a date. 26k rows total; < 20 ms.",
)
