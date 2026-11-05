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
