"""writer.guard: default-deny on write targets outside ideation.* (01 §1.1, 03 §C1 defence in depth)."""

from __future__ import annotations

import pytest

from ideation.db.migrate import discover
from ideation.db.writer import WriteGuardError, guard, strip_comments, write_targets

ALLOWED = [
    "insert into ideation.run (kind) values ('x')",
    "INSERT INTO IDEATION.RUN (kind) VALUES ('x')",
    'insert into "ideation"."run" (kind) values (%s)',
    "create table if not exists ideation.t (x int references ideation.run)",
    "update ideation.run r set status = 'ok' from equity.company c where c.company_id = 1",
    "insert into ideation.t values (1) on conflict (x) do update set y = excluded.y",
    "insert into ideation.t select company_id from equity.company",
    "delete from ideation.t using equity.company c where true",
    "select * from equity.company for update",
    "select * from equity.company for no key update",
    "create unique index if not exists t_uq on ideation.t (x) where y = 'ok'",
    "create or replace view ideation.v as select * from equity.company",
    "comment on column ideation.run.status is 'running | ok | failed'",
    "comment on table ideation.run is 'x'",
    "explain analyze select 1",
    "copy (select * from equity.company) to stdout",
    "copy ideation.t (a, b) from stdin",
    "create schema if not exists ideation",
    "select pg_advisory_lock(1)",
    "create temp table pg_temp.scratch (x int)",
    "truncate ideation.a, ideation.b restart identity",
    "drop table if exists ideation.a, ideation.b cascade",
    "with x as (select 1) insert into ideation.t select * from x",
    "-- insert into equity.company\nselect 1",
    "/* update equity.company set x = 1 */ select 1",
    "select 'it''s -- not a comment', 1",
]

REJECTED = [
    "insert into equity.company (company_id) values (1)",
    "INSERT INTO EQUITY.COMPANY VALUES (1)",
    'insert into "equity"."company" values (1)',
    'insert into "Equity".company values (1)',
    "insert /* sneaky */ into equity.company values (1)",
    "insert--c\ninto equity.company values (1)",
    "insert into macro.series values ('x')",
    "insert into public.t values (1)",
    "insert into run values (1)",  # unqualified
    "update equity.company set name = 'x'",
    "update equity.company as c set name = 'x'",
    "update only macro.series set unit = 'x'",
    "delete from equity.company",
    "delete from only macro.series_daily",
    "truncate equity.nse_bhav_full",
    "truncate table ideation.a, equity.b",
    "merge into equity.company c using ideation.t t on true when matched then delete",
    "copy equity.company from stdin",
    "copy macro.series (code) from stdin",
    "select * into equity.t from ideation.x",
    "select * into temp scratch from ideation.x",  # unqualified target
    "create table equity.x as select 1",
    "create table if not exists macro.x (y int)",
    "create unlogged table public.x (y int)",
    "create view equity.v as select 1",
    "create index i on equity.company (name)",
    "create unique index if not exists i on macro.series (code)",
    "alter table equity.company add column x int",
    "alter table if exists only macro.series drop column unit",
    "drop table equity.company",
    "drop table if exists ideation.a, macro.b cascade",
    "drop schema ideation cascade",
    "comment on table equity.company is 'x'",
    "comment on column equity.company.name is 'x'",
    "lock table equity.company in access exclusive mode",
    "vacuum full equity.nse_bhav_full",
    "refresh materialized view equity.mv",
    "with t as (delete from equity.company returning *) insert into ideation.y select * from t",
    "with t as (update equity.company set name = 'x' returning *) select * from t",
    "insert into ideation.a values (1); insert into equity.b values (2)",
    "do $$ begin insert into ideation.a values (1); end $$",
    "select 1; do $$ begin perform 1; end $$",
    "create function ideation.f() returns void language sql as $$ delete from equity.company $$",
    "create function ideation.f() returns void language plpgsql security definer as $$ begin end $$",
    "grant select on ideation.run to public",
    "revoke all on equity.company from ideation_rw",
    "create role x",
    "alter role ideation_rw superuser",
    "set role brainx",
    "set session authorization brainx",
    "alter system set fsync = off",
    "alter default privileges in schema ideation grant all on tables to public",
    "alter schema ideation owner to brainx",
    "create schema equity2",
    "copy (select 1) to program 'sh'",
    "create extension dblink",
    "select pg_read_file('/etc/passwd')",
    "prepare p as insert into equity.company values (1); execute p",
    # literals are scanned too (fail-closed): this is how a single-quoted function body is caught
    "comment on column ideation.run.status is 'insert into equity.company'",
    "create function ideation.f() returns void language sql as 'delete from equity.company'",
]


@pytest.mark.parametrize("sql", ALLOWED)
def test_allowed(sql: str) -> None:
    assert guard(sql) == sql


@pytest.mark.parametrize("sql", REJECTED)
def test_rejected(sql: str) -> None:
    with pytest.raises(WriteGuardError):
        guard(sql)


@pytest.mark.parametrize("schema", ["equity", "macro"])
def test_rejects_lake_schemas_for_every_verb(schema: str) -> None:
    for template in (
        "insert into {s}.x values (1)",
        "update {s}.x set a = 1",
        "delete from {s}.x",
        "truncate {s}.x",
        "create table {s}.x (a int)",
        "alter table {s}.x add column b int",
        "drop table {s}.x",
        "copy {s}.x from stdin",
        "merge into {s}.x using ideation.y on true when matched then delete",
    ):
        with pytest.raises(WriteGuardError):
            guard(template.format(s=schema))


def test_write_targets_lists_every_statement() -> None:
    targets = write_targets("insert into ideation.a values (1); update ideation.b set x = 1; delete from ideation.c")
    assert targets == ["ideation.a", "ideation.b", "ideation.c"]


def test_strip_comments_respects_quotes() -> None:
    assert "--" in strip_comments("select '--x'")
    assert "secret" not in strip_comments("select 1 -- secret\n")
    assert "keep" in strip_comments("select $$ -- keep $$")
    assert "x" not in strip_comments("/* outer /* nested x */ still */ select 1").replace("select", "")


@pytest.mark.parametrize("migration", discover(), ids=lambda m: m.path.name)
def test_every_migration_passes_the_guard(migration) -> None:
    guard(migration.sql)
    assert all(t.startswith("ideation.") for t in write_targets(migration.sql))
