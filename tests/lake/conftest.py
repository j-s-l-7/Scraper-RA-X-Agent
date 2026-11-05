"""Lake fixtures. ``ddl_sandbox`` (05 §10.1): BEGIN; apply the migration files the real ledger lacks; yield; ROLLBACK.
Branches test their DDL with it and never run ``ideation init-db`` (only main applies migrations)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from ideation.config import Settings
from ideation.db.migrate import discover, migrate
from ideation.db.writer import GuardedConnection, Writer


@pytest.fixture
def ddl_sandbox(settings: Settings) -> Iterator[GuardedConnection]:
    with Writer.from_settings(settings).connect() as conn, conn.transaction(force_rollback=True):
        migrate(conn, discover())
        yield conn
