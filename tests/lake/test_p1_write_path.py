"""P1 write path against 003 in the rolled-back sandbox, with real rows from the latest snapshot (one anchor):
universe_member / universe_coverage / run_manifest / label / calendar / security_map accept what the CLI writes."""

from __future__ import annotations

from datetime import date

import psycopg
import pytest

from ideation.config import load_methodology
from ideation.db.writer import GuardedConnection
from ideation.labels.build import compute_labels, label_specs
from ideation.labels.cli import write_db as write_labels
from ideation.universe.build import UniverseConfig
from ideation.universe.cli import build_all
from ideation.universe.cli import write_db as write_universe


def test_universe_and_labels_rows_fit_003(ddl_sandbox: psycopg.Connection, pit_store) -> None:
    conn = GuardedConnection(ddl_sandbox)
    run_id = conn.execute(
        "insert into ideation.run (kind, run_key, status) values ('test', 'p1-write-path', 'running') returning run_id"
    ).fetchone()[0]
    a = pit_store.calendar.snap_anchor(date(2019, 6, 28))
    cfg = UniverseConfig.from_methodology(load_methodology().data)
    members, covs, records = build_all(pit_store, cfg, [a])
    write_universe(conn, run_id, members, covs, records)
    n = conn.execute("select count(*), count(*) filter (where in_u_score) from ideation.universe_member").fetchone()
    assert n == (len(members), int(members["in_u_score"].sum()))
    cov = conn.execute("select n_u_score, extra->>'n_u_score' from ideation.universe_coverage").fetchone()
    assert cov[0] == int(cov[1]) == int(members["in_u_score"].sum())
    assert conn.execute("select count(*) from ideation.run_manifest where run_id = %s", (run_id,)).fetchone()[0] > 0
    labels = compute_labels(pit_store, members, label_specs(pit_store.calendar, [a], ["1Y", "1M"]))
    write_labels(conn, run_id, labels)
    got = conn.execute("select count(*), count(*) filter (where label_available_from = label_end) from ideation.label")
    assert got.fetchone() == (len(labels), len(labels))
    with pytest.raises(psycopg.errors.CheckViolation), ddl_sandbox.transaction():
        conn.execute("update ideation.label set label_available_from = label_end - 1")
