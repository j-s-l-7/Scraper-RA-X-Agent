"""03 §C1 on the real lake: ideation_rw cannot write equity (permission denied), the read path rejects writes
(read-only), RLS is bypassed, and app paths refuse a superuser identity.

Every write attempt runs inside a transaction that is always rolled back and checks its identity first."""

from __future__ import annotations

import psycopg
import pytest

from ideation.config import Settings
from ideation.db.bootstrap import role_state
from ideation.db.identity import IdentityError
from ideation.db.writer import Writer
from ideation.lake import queries as Q
from ideation.lake.adapter import LakeMode, LakeReader


@pytest.fixture
def reader(settings: Settings) -> LakeReader:
    return LakeReader.from_settings(settings, LakeMode.LIVE)


def test_role_state_matches_c1(rw_conn: psycopg.Connection) -> None:
    state = role_state(rw_conn)
    assert state.ok, state


def test_insert_into_equity_company_is_permission_denied(rw_conn: psycopg.Connection) -> None:
    # writable (not read-only) ideation_rw session: only the missing grant can stop this
    assert rw_conn.execute("select current_setting('default_transaction_read_only')").fetchone() == ("off",)
    with rw_conn.transaction(force_rollback=True), pytest.raises(psycopg.errors.InsufficientPrivilege) as exc:
        # NOT NULL columns are omitted on purpose: even a misconfigured superuser run would error, not write
        rw_conn.execute("insert into equity.company (company_id) values (-1)")
    assert "permission denied" in str(exc.value)


@pytest.mark.parametrize(
    "sql",
    [
        "update macro.series set unit = unit where false",
        "delete from equity.listing_history where false",
        "create table equity.ideation_probe (x int)",
    ],
)
def test_other_lake_writes_are_denied(rw_conn: psycopg.Connection, sql: str) -> None:
    with rw_conn.transaction(force_rollback=True), pytest.raises(psycopg.errors.InsufficientPrivilege):
        rw_conn.execute(sql)


def test_read_path_rejects_writes(reader: LakeReader) -> None:
    with reader.session() as s:
        conn = s._conn
        # ideation.run is owned by ideation_rw, so only read-only mode can stop these
        for sql in (
            "insert into ideation.run (kind, run_key, status) values ('probe', 'probe', 'running')",
            "insert into equity.company (company_id) values (-1)",
            "create temp table ro_probe (x int)",
        ):
            with conn.transaction(force_rollback=True), pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
                conn.execute(sql)


def test_rls_bypass_returns_rows(reader: LakeReader) -> None:
    (n,) = reader.fetch(Q.LISTING_HISTORY_COUNT)[0]
    assert n > 0


def test_app_paths_refuse_a_superuser_dsn(settings: Settings) -> None:
    superuser_dsn = settings.bootstrap_dsn  # trust auth: connects as the OS superuser
    with pytest.raises(IdentityError), LakeReader(superuser_dsn, mode=LakeMode.LIVE).session():
        pass
    with pytest.raises(IdentityError), Writer(superuser_dsn).connect():
        pass


def test_no_write_grants_and_full_select(reader: LakeReader) -> None:
    with reader.session() as s:
        assert s.fetch(Q.WRITE_GRANTS_ON_LAKE) == []
        assert s.fetch(Q.UNREADABLE_LAKE_TABLES) == []
