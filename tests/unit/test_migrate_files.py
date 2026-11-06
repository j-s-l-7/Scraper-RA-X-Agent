"""Migration discovery, naming and drift detection (no database)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ideation.db.migrate import MigrationError, check_drift, discover

EXPECTED_TABLES = {
    # 01 §1.3
    "schema_migration", "run", "snapshot", "feature_set", "model", "wf_result", "prediction", "regime_snapshot",
    "sector_rotation", "idea", "portfolio", "portfolio_weight", "backtest", "backtest_period", "chat_session",
    "chat_message",
    # 02 (03 §E P0)
    "run_manifest", "experiment_trial", "calendar", "security_map", "universe_member", "universe_coverage", "label",
    # 05 §8 (P1, 003)
    "holdout_unseal",
    # E1 (05 §2, §5): migrations 010–012
    "oos_prediction", "rank_calibration", "outcome",
}  # fmt: skip


def declared_tables(sql: str) -> set[str]:
    return set(re.findall(r"create table if not exists ideation\.(\w+)", sql))


def test_discover_orders_and_covers_all_tables() -> None:
    """Versions are sorted and unique from 001; gaps are allowed (phases own number ranges, 05 §10.1)."""
    ms = discover()
    versions = [m.version for m in ms]
    assert versions == sorted(set(versions))
    assert versions[0] == 1
    assert set().union(*(declared_tables(m.sql) for m in ms)) == EXPECTED_TABLES


def test_every_create_is_idempotent() -> None:
    for m in discover():
        for stmt in re.findall(r"\bcreate\s+(?:unique\s+)?(?:table|index|schema|view|or replace view)\b[^;(]*", m.sql):
            assert ("if not exists" in stmt) or ("or replace" in stmt), f"{m.path.name}: {stmt.strip()}"
        assert not re.search(r"create\s+(?:unique\s+)?index\s+on\b", m.sql), "every index must be named"


def test_label_timing_constraint_present() -> None:
    sql = "\n".join(m.sql for m in discover())
    assert "label_available_from >= label_end" in sql  # 03 §C3: never earlier than label_end


def _write(d: Path, name: str, body: str = "select 1;") -> None:
    (d / name).write_text(body)


def test_bad_names_and_duplicates(tmp_path: Path) -> None:
    _write(tmp_path, "001_init.sql")
    _write(tmp_path, "1_bad.sql")
    with pytest.raises(MigrationError, match="bad migration file name"):
        discover(tmp_path)
    (tmp_path / "1_bad.sql").unlink()
    _write(tmp_path, "001_dup.sql")
    with pytest.raises(MigrationError, match="duplicate"):
        discover(tmp_path)


def test_must_start_at_001(tmp_path: Path) -> None:
    _write(tmp_path, "002_x.sql")
    with pytest.raises(MigrationError, match="start at 001"):
        discover(tmp_path)


def test_drift_detection(tmp_path: Path) -> None:
    _write(tmp_path, "001_init.sql", "select 1;")
    ms = discover(tmp_path)
    check_drift(ms, {1: ms[0].checksum})
    with pytest.raises(MigrationError, match="changed after it was applied"):
        check_drift(ms, {1: "0" * 64})
    with pytest.raises(MigrationError, match="no file"):
        check_drift(ms, {1: ms[0].checksum, 2: "x"})
