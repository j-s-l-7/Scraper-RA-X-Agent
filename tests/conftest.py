"""Shared fixtures. Markers come from the test directory (unit/, leakage/, lake/), so ``-m`` filtering never
depends on a module remembering its own ``pytestmark``. Lake tests are skipped when the lake is unreachable as
ideation_rw."""

from __future__ import annotations

import os
from collections.abc import Iterator
from functools import cache
from pathlib import Path

import psycopg
import pytest

from ideation.config import Settings, load_settings

TESTS = Path(__file__).parent
_DIR_MARKERS = {"unit": pytest.mark.unit, "leakage": pytest.mark.leakage, "lake": pytest.mark.lake}

# Git hooks export repo-local variables (``git rev-parse --local-env-vars``). In a linked worktree GIT_DIR is
# absolute, so a test running ``git -C <tmp> init/config/commit`` would act on the REAL repository: on 2026-11-06 a
# pre-push run from wt-e1 committed onto the branch, replaced its index and set core.bare=true + user.* in the
# shared config. Tests never need them: git finds the repo from the working directory.
for _name in [n for n in os.environ if n.startswith("GIT_")]:
    del os.environ[_name]


@cache
def lake_probe() -> tuple[bool, str]:
    settings = load_settings()
    try:
        with psycopg.connect(settings.lake_dsn, connect_timeout=3) as conn:
            conn.execute("select 1")
    except psycopg.Error as exc:
        return False, f"lake unreachable as {settings.app_role}: {type(exc).__name__}"
    return True, ""


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        rel = Path(str(item.fspath)).relative_to(TESTS)
        marker = _DIR_MARKERS.get(rel.parts[0]) if len(rel.parts) > 1 else None
        if marker is not None:
            item.add_marker(marker)
    lake_items = [i for i in items if i.get_closest_marker("lake")]
    if lake_items:
        ok, reason = lake_probe()
        if not ok:
            for item in lake_items:
                item.add_marker(pytest.mark.skip(reason=reason))


@pytest.fixture(scope="session")
def settings() -> Settings:
    return load_settings()


@pytest.fixture
def rw_conn(settings: Settings) -> Iterator[psycopg.Connection]:
    """Raw (unguarded) writable ideation_rw connection, for tests that must reach the server's own checks."""
    with psycopg.connect(settings.dsn, autocommit=True, connect_timeout=5, options="-c search_path=ideation") as conn:
        user, superuser = conn.execute("select current_user, current_setting('is_superuser')").fetchone()
        assert (user, superuser) == (settings.app_role, "off"), "tests must never run lake probes as a superuser"
        yield conn
