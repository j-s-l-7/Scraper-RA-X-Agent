"""Lake fixtures. ``ddl_sandbox`` (05 §10.1 point 2): pending migration files applied inside one transaction that is
always rolled back, so a branch can test its DDL without touching the shared ``ideation`` ledger."""

from __future__ import annotations

from collections.abc import Iterator

import psycopg
import pytest

from ideation.db.migrate import Migration, applied_versions, discover
from ideation.db.writer import GuardedConnection, guard


def pending_migrations(conn: psycopg.Connection) -> list[Migration]:
    applied = applied_versions(GuardedConnection(conn))
    return [m for m in discover() if m.version not in applied]


@pytest.fixture
def ddl_sandbox(rw_conn: psycopg.Connection) -> Iterator[psycopg.Connection]:
    """``BEGIN; apply pending sql files; yield; ROLLBACK``. Postgres DDL is transactional."""
    with rw_conn.transaction(force_rollback=True):
        for m in pending_migrations(rw_conn):
            rw_conn.execute(guard(m.sql))
        yield rw_conn
