"""Read-only lake adapter (01 §1.1, 02 §1.1 point 2). The only code that reads ``equity``/``macro``.

Each read connection does three things:

* connects as ``ideation_rw`` (checked; a superuser or any other role is refused) with
  ``default_transaction_read_only=on``, ``statement_timeout`` and ``application_name='ideation-ro'``;
* uses an empty ``search_path``, so every relation must be schema-qualified and ban-list matching is sound;
* accepts only :class:`~ideation.lake.queries.NamedQuery` objects. In ``backtest`` mode it refuses any query that
  touches the ban list, before connecting.

Bulk reads go through ``COPY (SELECT …) TO STDOUT (FORMAT csv)`` into Arrow. ``snapshot()`` gives one
``REPEATABLE READ READ ONLY`` transaction, a consistent view while the scraper writes.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from enum import StrEnum
from typing import TYPE_CHECKING, Any

import psycopg
import pyarrow as pa
import pyarrow.csv as pacsv

from ideation.db.identity import require_app_role
from ideation.lake.bans import BannedSourceError, assert_allowed
from ideation.lake.queries import NamedQuery

if TYPE_CHECKING:
    import pandas as pd

    from ideation.config import Settings

__all__ = ["BannedSourceError", "LakeMode", "LakeReader", "LakeSession", "check_query"]

_SPOOL_BYTES = 64 * 1024 * 1024  # COPY output spills to a temp file beyond this


class LakeMode(StrEnum):
    LIVE = "live"
    BACKTEST = "backtest"


def check_query(query: NamedQuery, params: Any, mode: LakeMode) -> None:
    """Refuse anything but a NamedQuery; in backtest mode also refuse banned sources (no connection needed)."""
    if not isinstance(query, NamedQuery):
        raise TypeError("the lake adapter accepts only NamedQuery objects from ideation.lake.queries")
    if mode is LakeMode.BACKTEST:
        assert_allowed(query.sql, params, sources=query.sources)


class LakeSession:
    """Queries on one open read-only connection (autocommit, or inside a snapshot transaction)."""

    def __init__(self, conn: psycopg.Connection, mode: LakeMode) -> None:
        self._conn = conn
        self.mode = mode

    def check(self, query: NamedQuery, params: Any = None) -> None:
        check_query(query, params, self.mode)

    def fetch(self, query: NamedQuery, params: Any = None) -> list[tuple[Any, ...]]:
        self.check(query, params)
        return self._conn.execute(query.sql, params).fetchall()

    def fetch_one(self, query: NamedQuery, params: Any = None) -> tuple[Any, ...]:
        rows = self.fetch(query, params)
        if len(rows) != 1:
            raise ValueError(f"{query.name}: expected one row, got {len(rows)}")
        return rows[0]

    def copy_arrow(self, query: NamedQuery, params: Any = None) -> pa.Table:
        """Stream ``COPY (query) TO STDOUT`` as CSV into an Arrow table.

        NULL stays NULL and a quoted empty string stays ``''`` (``listing_history.isin`` is ``''`` before 2011-06).
        """
        self.check(query, params)
        stmt = f"copy ({query.sql}) to stdout (format csv, header true)"
        with tempfile.SpooledTemporaryFile(max_size=_SPOOL_BYTES) as buf:
            with self._conn.cursor() as cur, cur.copy(stmt, params) as copy:
                for chunk in copy:
                    buf.write(chunk)
            buf.seek(0)
            convert = pacsv.ConvertOptions(
                column_types=query.arrow_types,
                strings_can_be_null=True,
                quoted_strings_can_be_null=False,
                true_values=["t", "true"],  # Postgres CSV booleans are t/f
                false_values=["f", "false"],
            )
            return pacsv.read_csv(buf, convert_options=convert)

    def copy_pandas(self, query: NamedQuery, params: Any = None) -> pd.DataFrame:
        return self.copy_arrow(query, params).to_pandas()


class LakeReader:
    """Factory for read-only sessions on the lake."""

    def __init__(
        self,
        dsn: str,
        *,
        mode: LakeMode,
        role: str = "ideation_rw",
        statement_timeout_s: int = 120,
        connect_timeout_s: int = 5,
        application_name: str = "ideation-ro",
    ) -> None:
        self.dsn = dsn
        self.mode = LakeMode(mode)
        self.role = role
        self.statement_timeout_s = statement_timeout_s
        self.connect_timeout_s = connect_timeout_s
        self.application_name = application_name

    @classmethod
    def from_settings(cls, settings: Settings, mode: LakeMode) -> LakeReader:
        return cls(
            settings.lake_dsn,
            mode=mode,
            role=settings.app_role,
            statement_timeout_s=settings.statement_timeout_s,
            connect_timeout_s=settings.connect_timeout_s,
        )

    def _connect(self) -> psycopg.Connection:
        options = (
            f"-c default_transaction_read_only=on -c statement_timeout={self.statement_timeout_s * 1000}"
            " -c search_path= -c idle_in_transaction_session_timeout=600000"
        )
        conn = psycopg.connect(
            self.dsn,
            autocommit=True,
            connect_timeout=self.connect_timeout_s,
            application_name=self.application_name,
            options=options,
        )
        try:
            require_app_role(conn, self.role, read_only=True)
        except BaseException:
            conn.close()
            raise
        return conn

    @contextmanager
    def session(self) -> Iterator[LakeSession]:
        """Autocommit read-only session: each statement sees the latest committed data."""
        with self._connect() as conn:
            yield LakeSession(conn, self.mode)

    @contextmanager
    def snapshot(self) -> Iterator[LakeSession]:
        """One ``REPEATABLE READ READ ONLY`` transaction: every query sees the same lake state."""
        with self._connect() as conn:
            conn.autocommit = False
            conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
            conn.read_only = True
            with conn.transaction():
                yield LakeSession(conn, self.mode)

    def fetch(self, query: NamedQuery, params: Any = None) -> list[tuple[Any, ...]]:
        """One-off query. The ban check runs before any connection is made."""
        check_query(query, params, self.mode)
        with self.session() as s:
            return s.fetch(query, params)

    def copy_arrow(self, query: NamedQuery, params: Any = None) -> pa.Table:
        check_query(query, params, self.mode)
        with self.session() as s:
            return s.copy_arrow(query, params)
