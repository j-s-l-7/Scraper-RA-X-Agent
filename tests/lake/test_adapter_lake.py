"""Read adapter on the real lake: session guards, snapshot isolation, COPY → Arrow, and ban-list completeness."""

from __future__ import annotations

import psycopg
import pyarrow as pa
import pytest

from ideation.config import Settings
from ideation.lake import queries as Q
from ideation.lake.adapter import BannedSourceError, LakeMode, LakeReader
from ideation.lake.bans import find_banned
from ideation.lake.queries import NamedQuery


@pytest.fixture
def reader(settings: Settings) -> LakeReader:
    return LakeReader.from_settings(settings, LakeMode.LIVE)


def test_session_guards(reader: LakeReader, settings: Settings) -> None:
    user, superuser, ro_default, ro_now, timeout, app, search_path = reader.fetch(Q.SESSION_SETTINGS)[0]
    assert (user, superuser, ro_default, ro_now) == (settings.app_role, "off", "on", "on")
    assert timeout == "2min"
    assert app == "ideation-ro"
    assert search_path == ""


def test_unqualified_names_fail(reader: LakeReader) -> None:
    with pytest.raises(psycopg.errors.UndefinedTable):
        reader.fetch(NamedQuery("q", "select count(*) from listing_history", ()))


def test_statement_timeout_applies(settings: Settings) -> None:
    fast = LakeReader(settings.lake_dsn, mode=LakeMode.LIVE, statement_timeout_s=1)
    with pytest.raises(psycopg.errors.QueryCanceled):
        fast.fetch(NamedQuery("sleep", "select pg_sleep(3)", ()))


def test_snapshot_is_repeatable_read_read_only(reader: LakeReader) -> None:
    probe = NamedQuery(
        "iso", "select current_setting('transaction_isolation'), current_setting('transaction_read_only')", ()
    )
    with reader.snapshot() as s:
        assert s.fetch_one(probe) == ("repeatable read", "on")
        a = s.fetch_one(Q.BHAV_MAX_DATE)
        b = s.fetch_one(Q.BHAV_MAX_DATE)
        assert a == b


def test_copy_arrow_listing_history(reader: LakeReader) -> None:
    table = reader.copy_arrow(Q.LISTING_HISTORY)
    assert table.num_rows > 10_000
    assert table.schema.field("first_seen").type == pa.date32()
    assert table.schema.field("is_listed_now").type == pa.bool_()
    isin = table.column("isin")
    assert isin.null_count == 0  # '' stays '' (not NULL) for pre-2011 rows
    assert pa.compute.sum(pa.compute.equal(isin, "")).as_py() > 0
    (n,) = reader.fetch(Q.LISTING_HISTORY_COUNT)[0]
    assert table.num_rows == n


def test_backtest_reader_refuses_banned_on_real_lake(settings: Settings) -> None:
    bt = LakeReader.from_settings(settings, LakeMode.BACKTEST)
    with pytest.raises(BannedSourceError):
        bt.fetch(NamedQuery("q", "select count(*) from equity.market_snapshot", ()))
    assert bt.fetch(Q.LISTING_HISTORY_COUNT)[0][0] > 0  # allowed sources still work


VIEW_DEPS = NamedQuery(
    "view_deps",
    """
    select distinct v.relname, t.relname
    from pg_catalog.pg_depend d
    join pg_catalog.pg_rewrite r on r.oid = d.objid
    join pg_catalog.pg_class v on v.oid = r.ev_class
    join pg_catalog.pg_class t on t.oid = d.refobjid
    join pg_catalog.pg_namespace n on n.oid = v.relnamespace
    where n.nspname in ('equity', 'macro') and v.relkind in ('v', 'm') and t.oid <> v.oid
    """,
    ("pg_catalog.pg_depend",),
)
FUNC_BODIES = NamedQuery(
    "func_bodies",
    """
    select p.proname, p.prosrc from pg_catalog.pg_proc p join pg_catalog.pg_namespace n on n.oid = p.pronamespace
    where n.nspname in ('equity', 'macro') and p.prorettype <> 'pg_catalog.trigger'::regtype
    """,
    ("pg_catalog.pg_proc",),
)


def test_views_and_functions_over_banned_tables_are_banned(reader: LakeReader) -> None:
    """A view or function that reads a banned table must itself be on the ban list."""
    unbanned: set[str] = set()
    with reader.session() as s:
        for view, dep in s.fetch(VIEW_DEPS):
            if find_banned(dep) and not find_banned(view):
                unbanned.add(f"view {view} -> {dep}")
        for name, body in s.fetch(FUNC_BODIES):
            if find_banned(body) and not find_banned(name):
                unbanned.add(f"function {name}")
    assert unbanned == set()
