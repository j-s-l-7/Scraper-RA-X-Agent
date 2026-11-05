"""Apply ``sql/ideation/NNN_<name>.sql`` in order, once each, recorded in ``ideation.schema_migration``.

* Files are idempotent themselves; the ledger makes reruns a no-op.
* Each file runs in its own transaction, which also inserts its ledger row (all or nothing). A session advisory lock
  serialises concurrent ``init-db`` calls.
* An applied file whose text changed (checksum mismatch) is an error: write a new migration instead.
* Every file passes ``writer.guard`` before it is sent.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from ideation.config import REPO_ROOT
from ideation.db.writer import GuardedConnection, guard

MIGRATIONS_DIR = REPO_ROOT / "sql" / "ideation"
_FILE = re.compile(r"^(?P<version>\d{3})_(?P<name>[a-z0-9_]+)\.sql$")
_LOCK_KEY = 0x1DEA7104  # arbitrary, stable advisory-lock key for init-db


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    path: Path
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MigrateResult:
    applied: tuple[int, ...]
    already: tuple[int, ...]
    pending: tuple[int, ...]  # non-empty only for dry runs


def discover(sql_dir: Path = MIGRATIONS_DIR) -> list[Migration]:
    """Migration files sorted by version. Unknown file names and duplicate versions are errors."""
    out: list[Migration] = []
    for path in sorted(sql_dir.glob("*.sql")):
        m = _FILE.match(path.name)
        if m is None:
            raise MigrationError(f"bad migration file name {path.name!r} (expected NNN_name.sql)")
        out.append(Migration(int(m["version"]), m["name"], path, path.read_text(encoding="utf-8")))
    versions = [m.version for m in out]
    if len(set(versions)) != len(versions):
        raise MigrationError(f"duplicate migration versions in {sql_dir}: {versions}")
    if not out or out[0].version != 1:
        raise MigrationError(f"migrations must start at 001 ({sql_dir})")
    return out


def applied_versions(conn: GuardedConnection) -> dict[int, str]:
    """``{version: checksum}`` from the ledger; empty when the ledger does not exist yet."""
    exists = conn.execute("select to_regclass('ideation.schema_migration') is not null").fetchone()
    if not exists or not exists[0]:
        return {}
    rows = conn.execute("select version, checksum from ideation.schema_migration").fetchall()
    return {int(v): str(c) for v, c in rows}


def check_drift(migrations: list[Migration], applied: dict[int, str]) -> None:
    known = {m.version: m for m in migrations}
    for version, checksum in applied.items():
        m = known.get(version)
        if m is None:
            raise MigrationError(f"ledger has version {version} but no file for it")
        if m.checksum != checksum:
            raise MigrationError(f"{m.path.name} changed after it was applied; add a new migration instead")


def migrate(conn: GuardedConnection, migrations: list[Migration], *, dry_run: bool = False) -> MigrateResult:
    for m in migrations:
        guard(m.sql)
    conn.execute("select pg_advisory_lock(%s)", (_LOCK_KEY,))
    try:
        applied = applied_versions(conn)
        check_drift(migrations, applied)
        done: list[int] = []
        pending = [m for m in migrations if m.version not in applied]
        if dry_run:
            return MigrateResult((), tuple(sorted(applied)), tuple(m.version for m in pending))
        for m in pending:
            with conn.transaction():
                conn.execute(m.sql)
                conn.execute(
                    "insert into ideation.schema_migration (version, name, checksum) values (%s, %s, %s)",
                    (m.version, m.name, m.checksum),
                )
            done.append(m.version)
        return MigrateResult(tuple(done), tuple(sorted(applied)), ())
    finally:
        conn.execute("select pg_advisory_unlock(%s)", (_LOCK_KEY,))
