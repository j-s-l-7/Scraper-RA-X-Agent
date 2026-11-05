"""Run registry: ``ideation.run`` with ``run_key`` idempotency (01 §8.3).

``run_key = sha256(kind, as_of, snap_id, config_hash, normalized params)``. A run whose key already has an ``ok`` row
is recorded as ``skipped`` and does nothing, unless forced. When a forced run finishes ``ok``, the previous ``ok`` row
is marked ``superseded`` in the same transaction, so "one ok row per key" holds.
"""

from __future__ import annotations

import hashlib
import json
import socket
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from psycopg.types.json import Jsonb

from ideation.config import REPO_ROOT, canonical_json
from ideation.db.writer import GuardedConnection

_COLUMNS = (
    "run_id, kind, as_of, snap_id, run_key, status, started_at, finished_at, git_sha, config_hash, params, stats,"
    " error, host"
)


def compute_run_key(
    kind: str, *, as_of: date | None, snap_id: str | None, config_hash: str, params: dict[str, Any] | None
) -> str:
    payload = {
        "kind": kind,
        "as_of": as_of.isoformat() if as_of else None,
        "snap_id": snap_id,
        "config_hash": config_hash,
        "params": params or {},
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def git_sha(repo: Any = REPO_ROOT) -> str | None:
    """HEAD sha, suffixed ``-dirty`` when the work tree has uncommitted changes; None outside git."""
    try:
        sha = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True, timeout=10
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=no"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return f"{sha}-dirty" if dirty else sha


@dataclass(frozen=True)
class RunRecord:
    run_id: int
    kind: str
    as_of: date | None
    snap_id: str | None
    run_key: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    git_sha: str | None
    config_hash: str | None
    params: dict[str, Any] | None
    stats: dict[str, Any] | None
    error: str | None
    host: str | None


@dataclass
class ActiveRun:
    """Handle yielded by :meth:`RunRegistry.track`. Put counters in ``stats``; they are saved on exit."""

    record: RunRecord
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def run_id(self) -> int:
        return self.record.run_id

    @property
    def skipped(self) -> bool:
        return self.record.status == "skipped"


class RunRegistry:
    def __init__(self, conn: GuardedConnection) -> None:
        self.conn = conn

    def _one(self, sql: str, params: tuple[Any, ...]) -> RunRecord | None:
        row = self.conn.execute(sql, params).fetchone()
        return RunRecord(*row) if row else None

    def get(self, run_id: int) -> RunRecord | None:
        return self._one(f"select {_COLUMNS} from ideation.run where run_id = %s", (run_id,))

    def find_ok(self, run_key: str) -> RunRecord | None:
        return self._one(f"select {_COLUMNS} from ideation.run where run_key = %s and status = 'ok'", (run_key,))

    def recent(self, limit: int = 20, kind: str | None = None) -> list[RunRecord]:
        rows = self.conn.execute(
            f"select {_COLUMNS} from ideation.run where (%s::text is null or kind = %s) order by run_id desc limit %s",
            (kind, kind, limit),
        ).fetchall()
        return [RunRecord(*r) for r in rows]

    def start(
        self,
        kind: str,
        *,
        config_hash: str,
        as_of: date | None = None,
        snap_id: str | None = None,
        params: dict[str, Any] | None = None,
        force: bool = False,
    ) -> RunRecord:
        """Insert a ``running`` row, or a finished ``skipped`` row if an ok run with the same key exists."""
        key = compute_run_key(kind, as_of=as_of, snap_id=snap_id, config_hash=config_hash, params=params)
        prior = None if force else self.find_ok(key)
        status = "skipped" if prior else "running"
        stats = {"reason": "idempotent", "ok_run_id": prior.run_id} if prior else None
        params_json = Jsonb(json.loads(canonical_json(params or {})))
        record = self._one(
            "insert into ideation.run (kind, as_of, snap_id, run_key, status, finished_at, git_sha, config_hash,"
            " params, stats, host) values (%s, %s, %s, %s, %s, case when %s = 'skipped' then now() end, %s, %s, %s,"
            f" %s, %s) returning {_COLUMNS}",
            (
                kind,
                as_of,
                snap_id,
                key,
                status,
                status,
                git_sha(),
                config_hash,
                params_json,
                Jsonb(stats) if stats else None,
                socket.gethostname(),
            ),
        )
        assert record is not None
        return record

    def finish(
        self, run_id: int, status: str, *, stats: dict[str, Any] | None = None, error: str | None = None
    ) -> None:
        """Close a running run. An ``ok`` finish supersedes any earlier ok row with the same key (forced reruns)."""
        if status not in ("ok", "failed"):
            raise ValueError(f"finish status must be ok|failed, not {status!r}")
        stats_json = Jsonb(json.loads(canonical_json(stats))) if stats is not None else None
        with self.conn.transaction():
            if status == "ok":
                self.conn.execute(
                    "update ideation.run set status = 'superseded' where status = 'ok'"
                    " and run_key = (select run_key from ideation.run where run_id = %s) and run_id <> %s",
                    (run_id, run_id),
                )
            cur = self.conn.execute(
                "update ideation.run set status = %s, finished_at = now(), stats = coalesce(%s, stats), error = %s"
                " where run_id = %s and status = 'running'",
                (status, stats_json, error, run_id),
            )
            if cur.rowcount != 1:
                raise RuntimeError(f"run {run_id} is not running")

    @contextmanager
    def track(self, kind: str, *, config_hash: str, **kwargs: Any) -> Iterator[ActiveRun]:
        """``with registry.track('snapshot', config_hash=h, as_of=d) as run:``. Check ``run.skipped`` first.

        A normal exit finishes ``ok`` with ``run.stats``. An exception finishes ``failed`` and re-raises.
        """
        active = ActiveRun(self.start(kind, config_hash=config_hash, **kwargs))
        if active.skipped:
            yield active
            return
        try:
            yield active
        except BaseException as exc:
            self.finish(active.run_id, "failed", stats=active.stats, error=f"{type(exc).__name__}: {exc}"[:4000])
            raise
        self.finish(active.run_id, "ok", stats=active.stats)
