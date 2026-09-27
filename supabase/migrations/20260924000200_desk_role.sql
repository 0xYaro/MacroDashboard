-- 20260924000200_desk_role.sql
-- The macro_desk login's role, and the one read it may make of Accounting.
--
-- Per docs/DECISIONS.md 2026-09-24 "OpenClaw writes the three feeds through the desk
-- command": the new OpenClaw agent never holds a database credential. It calls typed
-- tools, the tools call `macro-agent desk ...` in this repository, and that command
-- connects as `macro_desk`, a role that can write the three feed tables and read what
-- the feeds need, and nothing else. The superuser login is never used for it.
--
-- 1. The role. NOLOGIN here: a migration file is committed, so it never carries a
--    password. `supabase/desk_role.py` sets LOGIN and a generated password once, on
--    the operator's machine, and writes MACRO_DESK_DATABASE_URL into .env.
--    Roles are cluster-wide, not per database, so the name carries the macro_ prefix
--    like every other object this repository owns (supabase/README.md names roles as
--    the next place a shared-project collision could happen).
--
-- 2. Accounting's open positions. Accounting owns `mart.book_position`,
--    `mart.venue_status` and `core.instrument`, with RLS on every table and a select
--    policy for `authenticated` only. A plain GRANT to macro_desk would therefore read
--    zero rows; reading them that way needs a GRANT, a USAGE on two of Accounting's
--    schemas and a new policy on each of three of Accounting's tables.
--
--    This file does none of that. It defines two SECURITY DEFINER functions in
--    macro_core, owned by the migrating role (which owns Accounting's tables on the
--    live project and so is not subject to their RLS), that return exactly the
--    columns the holdings watch needs: open positions only, never cash, and never the
--    `accounts` column, which carries broker account numbers. macro_desk gets EXECUTE
--    on the two functions and no privilege of any kind on Accounting's schemas.
--
--    The body is dynamic SQL on purpose. A view or a SQL-language function would
--    record a dependency on Accounting's columns, and Accounting's next migration that
--    drops or retypes one of them would fail on an object it has never heard of. A
--    PL/pgSQL `return query execute` is resolved when it runs, so a change on
--    Accounting's side costs this system one refused read, reported by the desk as a
--    sentence, and never blocks Accounting.
--
--    In a database without Accounting (every local test cluster) the functions exist
--    and raise undefined_table with a sentence when called.
--
-- Re-runnable: the role is created only when absent, functions are CREATE OR REPLACE,
-- grants are idempotent.

do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'macro_desk') then
    create role macro_desk nologin noinherit;
  end if;
end $$;

comment on role macro_desk is
  'The desk command''s login (macro-agent desk). Writes the catalyst, tail-risk and holdings feeds; reads the mart views the feeds need and Accounting''s open positions through macro_core.accounting_open_positions(). LOGIN and its password are set by supabase/desk_role.py, never by a migration.';

-- A runaway statement from a tool call should not hold a pooler slot.
alter role macro_desk set statement_timeout = '30s';

-- ============================================================
-- Accounting's open positions, projected
-- ============================================================

create or replace function macro_core.accounting_open_positions()
returns table (
  instrument_id      uuid,
  symbol             text,
  kind               text,
  name               text,
  display_name       text,
  asset_class        text,
  quantity           numeric,
  direction          text,
  mark               numeric,
  mark_as_of         timestamptz,
  currency           text,
  exposure_usd       numeric,
  venues             text[],
  underlying_symbol  text,
  expiry             date,
  strike             numeric,
  put_call           text,
  updated_at         timestamptz
)
language plpgsql
stable
security definer
set search_path = pg_catalog, pg_temp
as $$
begin
  if to_regclass('mart.book_position') is null or to_regclass('core.instrument') is null then
    raise exception 'Accounting''s mart.book_position and core.instrument are not in this database, so there are no positions to read'
      using errcode = 'undefined_table';
  end if;
  return query execute $q$
    select bp.instrument_id::uuid,
           bp.symbol::text,
           bp.kind::text,
           bp.name::text,
           bp.display_name::text,
           bp.asset_class::text,
           bp.quantity::numeric,
           bp.direction::text,
           bp.mark::numeric,
           bp.mark_as_of::timestamptz,
           bp.currency::text,
           bp.exposure_usd::numeric,
           bp.venues::text[],
           u.symbol::text,
           i.expiry::date,
           i.strike::numeric,
           i.put_call::text,
           bp.updated_at::timestamptz
      from mart.book_position bp
      join core.instrument i on i.id = bp.instrument_id
      left join core.instrument u on u.id = i.underlying_id
     where bp.quantity <> 0
       and bp.kind <> 'cash'
     order by bp.symbol
  $q$;
end $$;

comment on function macro_core.accounting_open_positions() is
  'Accounting''s open non-cash positions (mart.book_position where quantity <> 0), with the option underlying from core.instrument. Never returns the accounts column. SECURITY DEFINER so macro_desk needs no grant or policy on Accounting''s schemas; dynamic SQL so it holds no dependency on Accounting''s columns.';

create or replace function macro_core.accounting_venue_status()
returns table (
  venue           text,
  state           text,
  reason          text,
  venue_as_of     timestamptz,
  received_at     timestamptz,
  position_count  int
)
language plpgsql
stable
security definer
set search_path = pg_catalog, pg_temp
as $$
begin
  if to_regclass('mart.venue_status') is null then
    raise exception 'Accounting''s mart.venue_status is not in this database'
      using errcode = 'undefined_table';
  end if;
  return query execute $q$
    select vs.venue::text,
           vs.state::text,
           vs.reason::text,
           vs.venue_as_of::timestamptz,
           vs.received_at::timestamptz,
           vs.position_count::int
      from mart.venue_status vs
     order by vs.venue, vs.position_count desc
  $q$;
end $$;

comment on function macro_core.accounting_venue_status() is
  'How fresh each Accounting venue is, so the holdings watch can say when the book it read was stale. Never returns account_id or a balance.';

revoke all on function macro_core.accounting_open_positions() from public, anon, authenticated;
revoke all on function macro_core.accounting_venue_status() from public, anon, authenticated;
grant execute on function macro_core.accounting_open_positions() to macro_desk, service_role;
grant execute on function macro_core.accounting_venue_status() to macro_desk, service_role;
