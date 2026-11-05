"""init-db on the real lake: idempotent (second run applies nothing, catalog unchanged), all tables present, and
the migration files themselves are idempotent from scratch (applied twice into a throw-away schema, rolled back)."""

from __future__ import annotations

import re
import secrets

import psycopg
import pytest

from ideation.config import Settings
from ideation.db.migrate import discover, migrate
from ideation.db.writer import Writer
from tests.unit.test_migrate_files import EXPECTED_TABLES

CATALOG = """
select 'col', c.relname || '.' || a.attname || ':' || format_type(a.atttypid, a.atttypmod) || ':' || a.attnotnull
from pg_attribute a join pg_class c on c.oid = a.attrelid join pg_namespace n on n.oid = c.relnamespace
where n.nspname = %(s)s and a.attnum > 0 and not a.attisdropped
union all
select 'idx', indexname || ':' || indexdef from pg_indexes where schemaname = %(s)s
union all
select 'con', conname || ':' || pg_get_constraintdef(co.oid) from pg_constraint co
join pg_namespace n on n.oid = co.connamespace where n.nspname = %(s)s
union all
select 'rel', c.relname || ':' || c.relkind::text || ':' || pg_get_userbyid(c.relowner)
from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname = %(s)s
order by 1, 2
"""


def fingerprint(conn: psycopg.Connection, schema: str) -> list[tuple[str, str]]:
    return conn.execute(CATALOG, {"s": schema}).fetchall()


def tables_in(conn: psycopg.Connection, schema: str) -> set[str]:
    rows = conn.execute(
        "select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace"
        " where n.nspname = %s and c.relkind in ('r', 'p')",
        (schema,),
    ).fetchall()
    return {r[0] for r in rows}


def test_init_db_twice_is_idempotent(settings: Settings, rw_conn: psycopg.Connection) -> None:
    migrations = discover()
    writer = Writer.from_settings(settings)
    with writer.connect() as conn:
        migrate(conn, migrations)  # brings the schema up to date (no-op when already applied)
    before = fingerprint(rw_conn, "ideation")
    with writer.connect() as conn:
        again = migrate(conn, migrations)
    assert again.applied == ()
    assert again.already == tuple(m.version for m in migrations)
    assert fingerprint(rw_conn, "ideation") == before
    assert tables_in(rw_conn, "ideation") >= EXPECTED_TABLES
    ledger = rw_conn.execute("select version, checksum from ideation.schema_migration order by 1").fetchall()
    assert ledger == [(m.version, m.checksum) for m in migrations]
    owners = {r for (k, r) in before if k == "rel"}
    assert all(r.endswith(":ideation_rw") for r in owners)


def test_migration_files_are_idempotent_from_scratch(rw_conn: psycopg.Connection) -> None:
    scratch = f"ideation_scratch_{secrets.token_hex(4)}"

    def retarget(sql: str) -> str:
        sql = re.sub(r"\bschema if not exists ideation\b", f"schema if not exists {scratch}", sql)
        return re.sub(r"\bideation\.", f"{scratch}.", sql)

    with rw_conn.transaction(force_rollback=True):
        for m in discover():
            rw_conn.execute(retarget(m.sql))
        first = fingerprint(rw_conn, scratch)
        for m in discover():
            rw_conn.execute(retarget(m.sql))  # second application: must be a no-op
        assert fingerprint(rw_conn, scratch) == first
        assert tables_in(rw_conn, scratch) == EXPECTED_TABLES
    assert not rw_conn.execute("select to_regnamespace(%s)", (scratch,)).fetchone()[0]


def test_label_timing_constraint_enforced(rw_conn: psycopg.Connection) -> None:
    """03 §C3: label_available_from may not precede label_end."""
    with rw_conn.transaction(force_rollback=True), pytest.raises(psycopg.errors.CheckViolation):
        rw_conn.execute(
            "insert into ideation.label (anchor, security_id, horizon, entry_date, label_end, label_available_from,"
            " exit_type) values ('2022-06-30', 1, '1Y', '2022-07-01', '2023-06-30', '2023-06-29', 'normal')"
        )
