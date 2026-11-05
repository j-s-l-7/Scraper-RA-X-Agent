"""One-time superuser bootstrap of ``ideation_rw`` (03 §C1): runs ``sql/bootstrap/000_role.sql``, then verifies.

This is the only code path that connects as a superuser. It does not go through ``writer.guard``, by design: the
file's grants on ``equity``/``macro`` are exactly what the guard forbids for the app. The file is fixed, reviewed
and idempotent. Its only lake changes are the role, its grants, and schema ``ideation``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import psycopg

from ideation.config import REPO_ROOT
from ideation.db.identity import require_superuser

ROLE_SQL = REPO_ROOT / "sql" / "bootstrap" / "000_role.sql"


@dataclass(frozen=True)
class RoleState:
    exists: bool
    login: bool
    superuser: bool
    bypassrls: bool
    createrole: bool
    createdb: bool
    schema_owner: str | None
    select_grants: int
    write_grants: int
    lake_tables: int
    default_acl_schemas: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return (
            self.exists
            and self.login
            and self.bypassrls
            and not (self.superuser or self.createrole or self.createdb)
            and self.schema_owner == "ideation_rw"
            and self.select_grants == self.lake_tables
            and self.write_grants == 0
            and set(self.default_acl_schemas) == {"equity", "macro"}
        )


_STATE_SQL = """
select
  r.rolname is not null, coalesce(r.rolcanlogin, false), coalesce(r.rolsuper, false),
  coalesce(r.rolbypassrls, false), coalesce(r.rolcreaterole, false), coalesce(r.rolcreatedb, false),
  (select pg_get_userbyid(nspowner) from pg_namespace where nspname = 'ideation'),
  (select count(*) from pg_class c join pg_namespace n on n.oid = c.relnamespace
    where n.nspname in ('equity', 'macro') and c.relkind in ('r', 'p', 'v', 'm')
      and r.oid is not null and has_table_privilege(r.oid, c.oid, 'SELECT')),
  (select count(*) from pg_class c join pg_namespace n on n.oid = c.relnamespace
    where n.nspname in ('equity', 'macro') and c.relkind in ('r', 'p', 'v', 'm') and r.oid is not null
      and (has_table_privilege(r.oid, c.oid, 'INSERT') or has_table_privilege(r.oid, c.oid, 'UPDATE')
        or has_table_privilege(r.oid, c.oid, 'DELETE') or has_table_privilege(r.oid, c.oid, 'TRUNCATE'))),
  (select count(*) from pg_class c join pg_namespace n on n.oid = c.relnamespace
    where n.nspname in ('equity', 'macro') and c.relkind in ('r', 'p', 'v', 'm')),
  coalesce((select array_agg(distinct d.defaclnamespace::regnamespace::text
                            order by d.defaclnamespace::regnamespace::text) from pg_default_acl d
    where r.oid is not null and d.defaclobjtype = 'r' and d.defaclacl::text like '%%ideation_rw=r/%%'), '{}')
from (select 1) one left join pg_roles r on r.rolname = 'ideation_rw'
"""


def role_state(conn: psycopg.Connection) -> RoleState:
    row = conn.execute(_STATE_SQL).fetchone()
    assert row is not None
    return RoleState(*row[:10], default_acl_schemas=tuple(row[10]))


def bootstrap_role(dsn: str, sql_path: Path = ROLE_SQL, *, connect_timeout_s: int = 5) -> tuple[RoleState, RoleState]:
    """Run the role SQL in one transaction as a superuser. Returns (state before, state after)."""
    sql_text = sql_path.read_text(encoding="utf-8")
    with psycopg.connect(
        dsn, autocommit=True, connect_timeout=connect_timeout_s, application_name="ideation-bootstrap"
    ) as conn:
        require_superuser(conn)
        before = role_state(conn)
        with conn.transaction():
            conn.execute(sql_text)
        after = role_state(conn)
    return before, after
