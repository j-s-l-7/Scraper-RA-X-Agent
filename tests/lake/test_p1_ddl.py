"""003 DDL in the rolled-back sandbox: new columns exist and the universe/label constraints behave (05 §1, §8)."""

from __future__ import annotations

import psycopg
import pytest

UM_COLS = (
    "anchor, security_id, symbol, series, adv63_lakh, n_days63, rank_adv, listed_sessions, scorable,"
    " exclusion_reason, traded_ok, in_u1000, in_u500, in_u_score, in_u_score_adv05, in_u_score_t, u_score_t_reason"
)


def _um(conn: psycopg.Connection, **kw: object) -> None:
    row = {
        "anchor": "2016-06-30", "security_id": 1, "symbol": "X", "series": "EQ", "adv63_lakh": 30.0, "n_days63": 60,
        "rank_adv": 1, "listed_sessions": 300, "scorable": True, "exclusion_reason": None, "traded_ok": True,
        "in_u1000": True, "in_u500": True, "in_u_score": True, "in_u_score_adv05": False, "in_u_score_t": True,
        "u_score_t_reason": None,
    }  # fmt: skip
    row.update(kw)
    cols = UM_COLS.split(", ")
    conn.execute(
        f"insert into ideation.universe_member ({UM_COLS}) values ({', '.join(['%s'] * len(cols))})",
        [row[c.strip()] for c in cols],
    )


def test_universe_member_valid_rows(ddl_sandbox: psycopg.Connection) -> None:
    _um(ddl_sandbox)
    _um(ddl_sandbox, security_id=2, scorable=False, in_u_score=False, exclusion_reason="adv_below_floor",
        in_u_score_t=False, u_score_t_reason="adv_below_floor")  # fmt: skip
    _um(ddl_sandbox, security_id=3, scorable=False, in_u_score=False, exclusion_reason="mcap_below_floor_100")
    n = ddl_sandbox.execute("select count(*) from ideation.universe_member where anchor = '2016-06-30'").fetchone()
    assert n == (3,)


@pytest.mark.parametrize(
    "kw",
    [
        {"scorable": False},  # scorable must equal in_u_score / reason consistency
        {"in_u_score": False, "scorable": False, "exclusion_reason": None},
        {"exclusion_reason": "made_up", "scorable": False, "in_u_score": False},
        {"in_u_score_t": False},  # U_score_T needs a reason when out
        {"in_u_score": False, "scorable": False, "exclusion_reason": "too_new", "in_u_score_adv05": True},
        {"in_u1000": False},  # in_u500 implies in_u1000
        {"traded_ok": False},  # every universe requires the trading rules
    ],
)
def test_universe_member_rejects_inconsistent_rows(ddl_sandbox: psycopg.Connection, kw: dict[str, object]) -> None:
    with pytest.raises(psycopg.errors.CheckViolation), ddl_sandbox.transaction():
        _um(ddl_sandbox, **kw)


def test_label_new_columns_and_holdout_table(ddl_sandbox: psycopg.Connection) -> None:
    ddl_sandbox.execute(
        "insert into ideation.label (anchor, security_id, horizon, entry_date, label_end, label_available_from,"
        " exit_type, entry_price, exit_price, last_trade, ret_total, ret_total_last_price)"
        " values ('2016-06-30', 1, '1Y', '2016-07-01', '2017-06-30', '2017-06-30', 'distress', 10, 4, '2017-02-01',"
        " -1.0, -0.6)"
    )
    ddl_sandbox.execute("insert into ideation.holdout_unseal (family, champion_config_hash) values ('1Y-june', 'abc')")
    with pytest.raises(psycopg.errors.UniqueViolation), ddl_sandbox.transaction():
        ddl_sandbox.execute(
            "insert into ideation.holdout_unseal (family, champion_config_hash) values ('1Y-june', 'def')"
        )
    with pytest.raises(psycopg.errors.CheckViolation), ddl_sandbox.transaction():
        ddl_sandbox.execute(
            "insert into ideation.security_map (security_id, symbol, isin, valid_from, valid_to, link_method,"
            " company_link) values (1, 'X', '', '2010-01-04', '2011-01-04', 'own', 'guess')"
        )
