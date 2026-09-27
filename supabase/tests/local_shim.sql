-- local_shim.sql
-- NOT part of the migration set. Applied only against a throwaway local Postgres,
-- before the real migrations, to stand in for the Supabase-managed pieces that do not
-- exist outside a Supabase project: the anon, authenticated and service_role roles,
-- and auth.uid().
--
-- Usage against a scratch database:
--   psql -d macro_shim_test -f supabase/tests/local_shim.sql
--   psql -d macro_shim_test -f supabase/migrations/20260921000100_schemas.sql
--   ... (the rest of the migrations, in filename order)
--   psql -d macro_shim_test -f supabase/tests/smoke.sql
--
-- To simulate a call as a given role, in the same psql session:
--   set role authenticated;
--   select count(*) from macro_mart.snapshot_row;
--   reset role;

do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then
    create role anon nologin noinherit;
  end if;

  if not exists (select 1 from pg_roles where rolname = 'authenticated') then
    create role authenticated nologin noinherit;
  end if;

  if not exists (select 1 from pg_roles where rolname = 'service_role') then
    create role service_role nologin noinherit bypassrls;
  else
    alter role service_role bypassrls;
  end if;
end $$;

create schema if not exists auth;

create or replace function auth.uid()
returns uuid
language sql
stable
as $$
  select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid;
$$;

comment on function auth.uid() is
  'Local stand-in for the Supabase auth.uid() helper. Reads the request.jwt.claim.sub GUC.';
