"""Run registry write/read and run_key idempotency on the real ideation schema (always rolled back)."""

from __future__ import annotations

import secrets
from datetime import date

import pytest

from ideation.config import Settings
from ideation.db.writer import Writer
from ideation.runs import RunRegistry


@pytest.fixture
def registry(settings: Settings):
    with Writer.from_settings(settings).connect() as conn, conn.transaction(force_rollback=True):
        yield RunRegistry(conn)


def test_write_and_read(registry: RunRegistry) -> None:
    h = secrets.token_hex(8)
    rec = registry.start("selftest", config_hash=h, as_of=date(2026, 11, 5), params={"k": 1})
    assert rec.status == "running"
    assert rec.config_hash == h
    assert rec.params == {"k": 1}
    registry.finish(rec.run_id, "ok", stats={"rows": 42})
    got = registry.get(rec.run_id)
    assert got is not None
    assert got.status == "ok"
    assert got.stats == {"rows": 42}
    assert got.finished_at is not None
    assert registry.find_ok(rec.run_key) == got
    assert registry.recent(5, "selftest")[0].run_id == rec.run_id


def test_idempotent_skip_and_force_supersede(registry: RunRegistry) -> None:
    h = secrets.token_hex(8)
    first = registry.start("selftest", config_hash=h)
    registry.finish(first.run_id, "ok")
    skipped = registry.start("selftest", config_hash=h)
    assert skipped.status == "skipped"
    assert skipped.stats == {"reason": "idempotent", "ok_run_id": first.run_id}
    forced = registry.start("selftest", config_hash=h, force=True)
    assert forced.status == "running"
    assert forced.run_key == first.run_key
    registry.finish(forced.run_id, "ok")
    assert registry.get(first.run_id).status == "superseded"
    assert registry.find_ok(first.run_key).run_id == forced.run_id


def test_track_records_failure(registry: RunRegistry) -> None:
    h = secrets.token_hex(8)

    def failing_step() -> None:
        with registry.track("selftest", config_hash=h) as run:
            run.stats["rows"] = 1
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        failing_step()
    rec = registry.recent(1, "selftest")[0]
    assert rec.status == "failed"
    assert rec.error == "RuntimeError: boom"
    assert rec.stats == {"rows": 1}
    with registry.track("selftest", config_hash=h) as run:
        assert not run.skipped
    with registry.track("selftest", config_hash=h) as run:
        assert run.skipped


def test_finish_twice_is_an_error(registry: RunRegistry) -> None:
    rec = registry.start("selftest", config_hash=secrets.token_hex(8))
    registry.finish(rec.run_id, "failed", error="x")
    with pytest.raises(RuntimeError, match="not running"):
        registry.finish(rec.run_id, "ok")
