"""`ideation doctor` passes on the real lake: no FAIL. Freshness and scraper runs may WARN."""

from __future__ import annotations

from datetime import date

from ideation.config import Settings
from ideation.doctor import Status, run_checks


def test_doctor_has_no_failures(settings: Settings) -> None:
    checks = run_checks(settings, today=date.today())
    names = {c.name for c in checks}
    assert {
        "lake_identity",
        "read_only_enforced",
        "rls_bypass",
        "schema_version",
        "lag_pins",
        "lake_freshness",
    } <= names
    failures = [c for c in checks if c.status is Status.FAIL]
    # A branch worktree carries migrations that only main applies (05 §10.1); on main after init-db none are pending.
    failures = [c for c in failures if not (c.name == "schema_version" and c.detail.startswith("pending migrations"))]
    assert failures == []
