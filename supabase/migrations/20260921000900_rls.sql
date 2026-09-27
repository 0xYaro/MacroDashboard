-- 20260921000900_rls.sql
-- RLS and grants per docs/CONTRACT.md:
--   - RLS enabled on every table in all four macro_ schemas.
--   - authenticated: SELECT on macro_mart.* and macro_quality.* only.
--   - anon: nothing, anywhere.
--   - macro_raw and macro_core: not exposed. The worker and the weekly authoring agent
--     reach them with the service-role key, which bypasses RLS, and which is the stated
--     exception in the contract ("the weekly authoring agent writes macro_core through
--     the service-role key, not through the Data API").
--   - service_role: everything. It bypasses RLS via its BYPASSRLS attribute but still
--     needs the underlying grants.
--
-- This project is shared with Accounting. Every statement below names the four macro_
-- schemas explicitly. Accounting's raw, core, mart and quality are never named here and
-- their grants and policies are untouched.
--
-- The two views in 0007 grant themselves; they are re-granted here by the
-- "all tables in schema" grant, which covers views.
--
-- Re-runnable: RLS enable, GRANT and REVOKE are naturally idempotent; policies are
-- dropped and recreated.

-- ============================================================
-- Enable RLS on every table in the four macro_ schemas
-- ============================================================

do $$
declare
  t record;
begin
  for t in
    select schemaname, tablename
    from pg_tables
    where schemaname in ('macro_raw', 'macro_core', 'macro_mart', 'macro_quality')
  loop
    execute format('alter table %I.%I enable row level security', t.schemaname, t.tablename);
  end loop;
end $$;

-- ============================================================
-- anon: nothing
-- ============================================================

revoke all on schema macro_raw, macro_core, macro_mart, macro_quality from public, anon;

do $$
declare
  s text;
begin
  foreach s in array array['macro_raw', 'macro_core', 'macro_mart', 'macro_quality'] loop
    execute format('revoke all on all tables in schema %I from public, anon', s);
    execute format('revoke all on all sequences in schema %I from public, anon', s);
    execute format('revoke all on all functions in schema %I from public, anon', s);
  end loop;
end $$;

alter default privileges in schema macro_raw, macro_core, macro_mart, macro_quality
  revoke all on tables from anon;
alter default privileges in schema macro_raw, macro_core, macro_mart, macro_quality
  revoke all on sequences from anon;
alter default privileges in schema macro_raw, macro_core, macro_mart, macro_quality
  revoke all on functions from anon;

-- ============================================================
-- authenticated: SELECT on macro_mart.* and macro_quality.* only
-- ============================================================
-- No USAGE is granted on macro_raw or macro_core, so no grant on any object in them
-- can be exercised. The two history views in macro_mart read macro_core with
-- security_invoker = false and are the only route across that line.

grant usage on schema macro_mart to authenticated;
grant usage on schema macro_quality to authenticated;

grant select on all tables in schema macro_mart to authenticated;
grant select on all tables in schema macro_quality to authenticated;

alter default privileges in schema macro_mart grant select on tables to authenticated;
alter default privileges in schema macro_quality grant select on tables to authenticated;

revoke all on schema macro_raw, macro_core from authenticated;

do $$
declare
  t record;
begin
  for t in
    select schemaname, tablename
    from pg_tables
    where schemaname in ('macro_mart', 'macro_quality')
  loop
    execute format('drop policy if exists authenticated_select on %I.%I', t.schemaname, t.tablename);
    execute format(
      'create policy authenticated_select on %I.%I for select to authenticated using (true)',
      t.schemaname, t.tablename
    );
  end loop;
end $$;

-- macro_raw and macro_core keep RLS enabled with zero policies. A non-superuser,
-- non-BYPASSRLS role therefore reads nothing from them even if a grant were ever
-- added by mistake.

-- ============================================================
-- service_role: everything
-- ============================================================

grant usage on schema macro_raw, macro_core, macro_mart, macro_quality to service_role;

grant all privileges on all tables in schema macro_raw, macro_core, macro_mart, macro_quality to service_role;
grant usage, select on all sequences in schema macro_raw, macro_core, macro_mart, macro_quality to service_role;
grant execute on all functions in schema macro_core to service_role;

alter default privileges in schema macro_raw, macro_core, macro_mart, macro_quality
  grant all on tables to service_role;
alter default privileges in schema macro_raw, macro_core, macro_mart, macro_quality
  grant usage, select on sequences to service_role;
