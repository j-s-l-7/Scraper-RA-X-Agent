"""Write path to the ``ideation`` schema, with ``guard(sql)`` as defence in depth (01 §1.1, 03 §C1).

The database role is the real enforcement: ``ideation_rw`` has SELECT only on ``equity``/``macro``. This guard
catches mistakes in our own code before they reach the server. It is default-deny on write targets:

* every write target (INSERT/UPDATE/DELETE/MERGE/TRUNCATE/COPY FROM/SELECT INTO/DDL/LOCK …) must be schema-qualified
  as ``ideation.<name>`` (or ``pg_temp.<name>``). Unqualified, ``equity.``, ``macro.`` and ``public.`` targets are
  rejected;
* privilege, role, dynamic-SQL and server-side-file statements are always rejected (GRANT, REVOKE, ALTER/CREATE ROLE,
  SET ROLE, DO, EXECUTE, COPY … PROGRAM, ALTER SYSTEM, ALTER DEFAULT PRIVILEGES, DROP SCHEMA …).

Comments are stripped by a quote-aware scanner. String and dollar-quoted bodies are kept, so a function body that
writes elsewhere is caught too.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg import sql as pgsql

from ideation.db.identity import require_app_role

ALLOWED_SCHEMAS = frozenset({"ideation", "pg_temp"})


class WriteGuardError(PermissionError):
    """A statement would write outside the ``ideation`` schema, or uses a forbidden construct."""


# ── lexing ───────────────────────────────────────────────────────────────────────────────────────────────────
_DOLLAR_TAG = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$")


def strip_comments(sql: str) -> str:
    """Remove ``--`` and ``/* */`` comments, honouring '…', "…" and $tag$…$tag$ quoting."""
    out: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch == "-" and sql.startswith("--", i):
            j = sql.find("\n", i)
            i = n if j < 0 else j
            out.append(" ")
        elif ch == "/" and sql.startswith("/*", i):
            depth, i = 1, i + 2
            while i < n and depth:
                if sql.startswith("/*", i):
                    depth, i = depth + 1, i + 2
                elif sql.startswith("*/", i):
                    depth, i = depth - 1, i + 2
                else:
                    i += 1
            out.append(" ")
        elif ch in ("'", '"'):
            j = i + 1
            while j < n:
                if sql[j] == ch:
                    if j + 1 < n and sql[j + 1] == ch:  # doubled quote escape
                        j += 2
                        continue
                    break
                j += 1
            out.append(sql[i : j + 1])
            i = j + 1
        elif ch == "$" and (m := _DOLLAR_TAG.match(sql, i)):
            tag = m.group(0)
            j = sql.find(tag, m.end())
            j = n if j < 0 else j + len(tag)
            out.append(sql[i:j])
            i = j
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _normalize(sql: str) -> str:
    return re.sub(r"\s+", " ", strip_comments(sql)).strip().lower()


# ── patterns ─────────────────────────────────────────────────────────────────────────────────────────────────
_ID = r'(?:"[^"]+"|[a-z_][a-z0-9_$]*)'
_REL = rf"(?P<rel>{_ID}(?:\s*\.\s*{_ID})?)"  # schema.rel; a 3-part name reads as db.schema → rejected
_IFX = r"(?:if (?:not )?exists )?"

_TARGET_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p)
    for p in (
        rf"\binto (?:temp(?:orary)? |unlogged |table )*{_REL}",  # insert into / merge into / select … into
        rf"(?<!\bdo )(?<!\bfor )(?<!\bkey )\bupdate (?:only )?{_REL}(?: (?:as )?{_ID})? set\b",
        rf"\bdelete from (?:only )?{_REL}",
        rf"\btruncate (?:table )?(?:only )?{_REL}",
        rf"\bcopy {_REL} ?(?:\([^)]*\) ?)?from\b",
        rf"\bcreate (?:or replace )?(?:(?:global|local) )?(?:temp(?:orary)? |unlogged |recursive )?"
        rf"(?:materialized )?(?:table|view|sequence|type|domain|function|procedure|aggregate) {_IFX}{_REL}",
        rf"\bcreate (?:unique )?index (?:concurrently )?(?:{_IFX}{_ID} )?on (?:only )?{_REL}",
        rf"\bcreate (?:or replace )?(?:constraint )?trigger {_ID} .*? on {_REL}",
        rf"\bcreate (?:or replace )?rule {_ID} as on \w+ to {_REL}",
        rf"\balter (?:materialized )?(?:table|view|sequence|type|domain|function|procedure|index) {_IFX}"
        rf"(?:only )?{_REL}",
        rf"\bdrop (?:materialized )?(?:table|view|sequence|type|domain|function|procedure|index|trigger|rule) "
        rf"(?:concurrently )?{_IFX}{_REL}",
        rf"\bcomment on (?:table|view|materialized view|sequence|index|function|type) {_REL}",
        rf"\bcomment on column (?P<rel>{_ID}\s*\.\s*{_ID})\s*\.\s*{_ID}",  # schema.table.column
        rf"\block (?:table )?(?:only )?{_REL}",
        rf"\brefresh materialized view (?:concurrently )?{_REL}",
        rf"(?<!\bexplain )\b(?:vacuum|analyze|cluster|reindex)(?: \([^)]*\))?"
        rf"(?: (?:full|freeze|verbose|analyze|table|index))* {_REL}",
    )
)
# Statements whose argument list may hold several relations.
_MULTI_TARGET = re.compile(
    rf"\b(?:truncate|drop (?:table|view|sequence|materialized view)) (?:table )?{_IFX}(?P<list>[^;]+)"
)

_SCHEMA_DDL = re.compile(rf"\b(?:create|alter) schema {_IFX}(?:(?P<name>{_ID}))")
_FORBIDDEN: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (label, re.compile(p))
    for label, p in (
        ("GRANT/REVOKE", r"\b(?:grant|revoke)\b"),
        ("role management", r"\b(?:create|alter|drop) (?:role|user|group)\b"),
        ("role switching", r"\bset (?:local |session )?(?:role|session authorization)\b|\breset role\b"),
        ("ALTER SYSTEM/DATABASE", r"\balter (?:system|database)\b"),
        ("default privileges", r"\balter default privileges\b"),
        ("ownership changes", r"\breassign owned\b|\bdrop owned\b|\bowner to\b"),
        ("DROP SCHEMA/DATABASE", r"\bdrop (?:schema|database)\b"),
        ("DO block", r"(?:^|;) ?do\b"),
        ("dynamic SQL", r"\bexecute\b"),
        ("server-side program", r"\bprogram\b"),
        ("extensions", r"\b(?:create|alter|drop) extension\b"),
        ("security definer", r"\bsecurity definer\b"),
        ("large objects / server files", r"\b(?:lo_import|lo_export|pg_read_file|pg_read_binary_file)\b"),
        ("publication/subscription", r"\b(?:create|alter) (?:publication|subscription)\b"),
    )
)


def _schema_of(rel: str) -> str | None:
    parts = [p.strip().strip('"') for p in rel.split(".")]
    return parts[-2] if len(parts) >= 2 else None


def write_targets(sql: str) -> list[str]:
    """Every relation the statement(s) would write to, as written (lower-cased)."""
    text = _normalize(sql)
    found = [m.group("rel") for pat in _TARGET_PATTERNS for m in pat.finditer(text)]
    for m in _MULTI_TARGET.finditer(text):
        rels = m.group("list").split(" cascade")[0].split(" restrict")[0]
        found.extend(r.strip() for r in rels.split(",") if r.strip())
    return found


def guard(sql: str) -> str:
    """Return ``sql`` unchanged if every write target is in ``ideation`` (or ``pg_temp``); raise otherwise."""
    text = _normalize(sql)
    for label, pat in _FORBIDDEN:
        if pat.search(text):
            raise WriteGuardError(f"forbidden construct ({label}) in: {text[:160]}")
    for m in _SCHEMA_DDL.finditer(text):
        if m.group("name").strip('"') != "ideation":
            raise WriteGuardError(f"schema DDL outside ideation: {m.group(0)}")
    for rel in write_targets(sql):
        schema = _schema_of(rel)
        if schema not in ALLOWED_SCHEMAS:
            where = "unqualified" if schema is None else f"schema {schema!r}"
            raise WriteGuardError(f"write target {rel!r} is {where}; only ideation.* is writable")
    return sql


# ── connection ───────────────────────────────────────────────────────────────────────────────────────────────
class GuardedConnection:
    """A psycopg connection whose every statement passes :func:`guard`. No raw cursor is exposed."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def _text(self, query: str | pgsql.Composable) -> str:
        return query if isinstance(query, str) else query.as_string(self._conn)

    def execute(self, query: str | pgsql.Composable, params: Any = None) -> psycopg.Cursor[Any]:
        guard(self._text(query))
        return self._conn.execute(query, params)

    def executemany(self, query: str | pgsql.Composable, params_seq: Sequence[Any]) -> None:
        guard(self._text(query))
        with self._conn.cursor() as cur:
            cur.executemany(query, params_seq)

    def copy_rows(self, table: str, columns: Sequence[str], rows: Iterable[Sequence[Any]]) -> int:
        """``COPY <table> (<columns>) FROM STDIN`` (fast bulk load). The statement passes :func:`guard`, so the
        target must be ``ideation.*``; identifiers are quoted by psycopg."""
        schema, _, name = table.partition(".")
        stmt = pgsql.SQL("copy {}.{} ({}) from stdin").format(
            pgsql.Identifier(schema), pgsql.Identifier(name), pgsql.SQL(", ").join(map(pgsql.Identifier, columns))
        )
        guard(self._text(stmt))
        n = 0
        with self._conn.cursor() as cur, cur.copy(stmt) as cp:
            for r in rows:
                cp.write_row(r)
                n += 1
        return n

    def transaction(self, *, force_rollback: bool = False) -> psycopg.Transaction:
        return self._conn.transaction(force_rollback=force_rollback)

    @property
    def closed(self) -> bool:
        return self._conn.closed


class Writer:
    """Factory for guarded write connections (``search_path=ideation``, autocommit + explicit transactions)."""

    def __init__(self, dsn: str, *, role: str = "ideation_rw", connect_timeout_s: int = 5) -> None:
        self.dsn = dsn
        self.role = role
        self.connect_timeout_s = connect_timeout_s

    @classmethod
    def from_settings(cls, settings: Any) -> Writer:
        return cls(settings.dsn, role=settings.app_role, connect_timeout_s=settings.connect_timeout_s)

    @contextmanager
    def connect(self) -> Iterator[GuardedConnection]:
        with psycopg.connect(
            self.dsn,
            autocommit=True,
            connect_timeout=self.connect_timeout_s,
            application_name="ideation-rw",
            options="-c search_path=ideation",
        ) as conn:
            require_app_role(conn, self.role, read_only=False)
            yield GuardedConnection(conn)
