"""E1 DDL (migrations 010–012) and the trial registry, inside ``ddl_sandbox`` (rolled back; never the real ledger)."""

from __future__ import annotations

import psycopg
import pytest

from ideation.db.writer import GuardedConnection
from ideation.trials.registry import (
    UnregisteredTrialError,
    assert_registered,
    family_n,
    load_family,
    register,
    registered,
)


def test_tables_and_constraints(ddl_sandbox: GuardedConnection) -> None:
    conn = ddl_sandbox
    for t in ("oos_prediction", "rank_calibration", "outcome"):
        assert conn.execute("select to_regclass(%s)", (f"ideation.{t}",)).fetchone()[0] is not None
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        conn.execute(
            "insert into ideation.rank_calibration (model_key, horizon, test_anchor, scheme, bucket)"
            " values ('abc', '1Y', '2022-06-30', 'peeking', 'D10')"
        )
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        conn.execute(
            "insert into ideation.outcome (anchor, security_id, horizon, kind) values ('2022-06-30', 1, '1Y', 'x')"
        )
    with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.transaction():
        conn.execute("insert into ideation.oos_prediction (trial_id, anchor, security_id) values (-1, '2022-06-30', 1)")


def test_register_then_train_guard(ddl_sandbox: GuardedConnection) -> None:
    conn = ddl_sandbox
    # The live table holds real registered trials by now; clear them inside this rolled-back transaction
    # so the test sees an empty registry (the sandbox ROLLBACK restores them).
    conn.execute(
        "delete from ideation.oos_prediction where trial_id in"
        " (select trial_id from ideation.experiment_trial where family in ('1Y-june', 'tactical-3M'))"
    )
    conn.execute("delete from ideation.experiment_trial where family in ('1Y-june', 'tactical-3M')")
    one_y, tactical = load_family("1Y-june"), load_family("tactical-3M")
    first = register(conn, one_y)
    assert len(first.inserted) == 50
    assert first.n_family == 50
    again = register(conn, one_y)
    assert again.inserted == ()
    assert len(again.existing) == 50
    assert register(conn, tactical).n_family == 25
    assert family_n(conn, "1Y-june") == 50
    rows = registered(conn, "1Y-june")
    assert {status for _, _, status, _ in rows} == {"registered"}
    assert [name for _, name, _, _ in rows][:3] == ["P1_UNIFIED", "P1_UNIFIED_XS", "P1_COMP_DOSSIER"]
    rf = next(s for s in one_y if s.trial_id == "RF_GLOBAL")
    trial_id = assert_registered(conn, "1Y-june", rf.config_hash)
    with pytest.raises(UnregisteredTrialError, match="not registered"):
        assert_registered(conn, "1Y-june", "0" * 64)
    with pytest.raises(UnregisteredTrialError):
        assert_registered(conn, "tactical-3M", rf.config_hash)  # registered in another family only
    conn.execute(
        "insert into ideation.oos_prediction (trial_id, anchor, security_id, score, p, bucket_dec)"
        " values (%s, '2022-06-30', 1, 0.3, 0.95, 10)",
        (trial_id,),
    )
    stored = conn.execute(
        "select config_json->'params'->>'n_estimators', horizon, anchor_family from ideation.experiment_trial"
        " where trial_id = %s",
        (trial_id,),
    ).fetchone()
    assert stored == ("500", "1Y", "june")
