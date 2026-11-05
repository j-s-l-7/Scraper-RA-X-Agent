-- One-time, superuser-only bootstrap of the application role (03 §C1). Run via `ideation bootstrap-role`.
-- Idempotent: re-running re-asserts the same attributes and grants and changes nothing else.
--
-- ideation_rw:
--   * BYPASSRLS: 57 lake tables have row-level security enabled with no policies; without it the role reads 0 rows.
--   * SELECT only on equity/macro (plus default privileges for tables brainx creates later). No write grants.
--   * Owns schema `ideation` (all outputs). CONNECT + CREATE on the database.
-- The lake uses trust auth on localhost, so the role has no password.

do $$
begin
  if not exists (select 1 from pg_catalog.pg_roles where rolname = 'ideation_rw') then
    create role ideation_rw login nosuperuser nocreaterole nocreatedb noreplication bypassrls;
  else
    alter role ideation_rw login nosuperuser nocreaterole nocreatedb noreplication bypassrls;
  end if;
end
$$;

grant connect, create on database equity_lake to ideation_rw;

create schema if not exists ideation authorization ideation_rw;
alter schema ideation owner to ideation_rw;

grant usage on schema equity, macro to ideation_rw;
grant select on all tables in schema equity, macro to ideation_rw;
alter default privileges for role brainx in schema equity grant select on tables to ideation_rw;
alter default privileges for role brainx in schema macro  grant select on tables to ideation_rw;

-- Read-only SQL functions (financials_asof, v_financials_asof). The other equity functions are trigger
-- functions, which cannot be called directly. None is SECURITY DEFINER (probed 2026-11-05).
grant execute on all functions in schema equity to ideation_rw;
