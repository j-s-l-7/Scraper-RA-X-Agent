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
    assert [c for c in checks if c.status is Status.FAIL] == []
