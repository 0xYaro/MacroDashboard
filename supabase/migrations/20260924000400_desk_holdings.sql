-- 20260924000400_desk_holdings.sql
-- The holdings watch, per docs/DECISIONS.md 2026-09-24 "OpenClaw writes the three feeds
-- through the desk command".
--
-- Which names are watched: Accounting's open positions (read through
-- macro_core.accounting_open_positions(), 20260924000200), plus two short lists the
-- operator edits on the dashboard:
--
--   macro_core.manual_position  positions at brokers Accounting does not connect to
--                               (a Korean or Singapore account)
--   macro_core.watch_ticker     names not held but followed
--
-- The web app still writes no table. It edits the two lists through four SECURITY
-- DEFINER functions in macro_mart that require auth.uid(), exactly like
-- request_refresh and decide_proposal in 20260923000300.
--
-- What the watch records:
--
--   macro_core.holding_watch    one row per name per Singapore day: the price and the
--                               24h change the desk command computed from market data
--                               (never a number the model supplied), how it was
--                               computed, and the model's reason for the move
--   macro_core.holding_event    what is coming up for a held name in the next 14 days
--
-- and two views for the page, holding_watch_state and holding_event_state, plus the
-- two lists as manual_position_state and watch_ticker_state.
--
-- Re-runnable: IF NOT EXISTS, CREATE OR REPLACE, views dropped and recreated with
-- their grants, policies dropped and recreated.

-- ============================================================
-- 1. The two lists the operator edits
-- ============================================================

create table if not exists macro_core.manual_position (
  id           uuid primary key default gen_random_uuid(),
  symbol       text not null,
  name         text null,
  venue        text not null,
  asset_class  text not null check (asset_class in ('equity', 'etf', 'crypto', 'option', 'commodity', 'fixed_income', 'other')),
  quantity     numeric null,
  note         text null,
  active       boolean not null default true,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  constraint ck_macro_manual_position_symbol check (symbol ~ '^[A-Z0-9][A-Z0-9.^=/:-]{0,23}$'),
  constraint ck_macro_manual_position_venue check (length(btrim(venue)) between 1 and 40)
);

comment on table macro_core.manual_position is
  'Positions the operator holds at a broker Accounting does not connect to, edited on the dashboard. Deactivated, never deleted. quantity is optional: the watch needs the name, not the size.';

create unique index if not exists uq_macro_manual_position_symbol_venue
  on macro_core.manual_position (symbol, lower(venue))
  where active;

create table if not exists macro_core.watch_ticker (
  id           uuid primary key default gen_random_uuid(),
  symbol       text not null,
  name         text null,
  asset_class  text not null default 'equity' check (asset_class in ('equity', 'etf', 'crypto', 'option', 'commodity', 'fixed_income', 'other')),
  note         text null,
  active       boolean not null default true,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  constraint ck_macro_watch_ticker_symbol check (symbol ~ '^[A-Z0-9][A-Z0-9.^=/:-]{0,23}$')
);

comment on table macro_core.watch_ticker is
  'Names the operator follows without holding them, edited on the dashboard. Deactivated, never deleted.';

create unique index if not exists uq_macro_watch_ticker_symbol
  on macro_core.watch_ticker (symbol)
  where active;

-- ============================================================
-- 2. What the watch records
-- ============================================================

create table if not exists macro_core.holding_watch (
  id                  bigserial primary key,
  as_of               date not null,
  symbol              text not null,
  source              text not null check (source in ('accounting', 'manual', 'watch')),
  name                text null,
  asset_class         text null,
  market_symbol       text null,
  price               numeric null,
  price_at            timestamptz null,
  currency            text null,
  change_24h_pct      numeric null,
  change_basis        text null check (change_basis is null or change_basis in ('close_to_close', 'rolling_24h')),
  change_session      date null,
  price_source        text null,
  unavailable_reason  text null,
  underlying_only     boolean not null default false,
  quantity            numeric null,
  exposure_usd        numeric null,
  material            boolean not null default false,
  driver_summary      text null,
  source_url          text null,
  run_id              bigint null references macro_quality.ingest_run (id) on delete set null,
  recorded_at         timestamptz not null default now(),
  constraint uq_macro_holding_watch unique (as_of, symbol, source),
  -- Null, never zero: a change the desk could not compute says why.
  constraint ck_macro_holding_watch_change_reason
    check (change_24h_pct is not null or unavailable_reason is not null),
  constraint ck_macro_holding_watch_change_basis
    check (change_24h_pct is null or change_basis is not null),
  -- A material move names its reason and where it was read.
  constraint ck_macro_holding_watch_material
    check (not material or (driver_summary is not null and source_url is not null))
);

comment on table macro_core.holding_watch is
  'One row per watched name per Singapore day (as_of). price and change_24h_pct are computed by macro-agent desk from market data: close_to_close is the last completed regular session against the one before (equities and ETFs, from Yahoo); rolling_24h is now against 24 hours ago (crypto, from Hyperliquid or CoinGecko). An option carries its underlying''s move with underlying_only true. driver_summary, material and source_url are the model''s. Upserted on (as_of, symbol, source), so a second run on one day restates the row.';
comment on column macro_core.holding_watch.change_session is
  'For close_to_close, the session whose close the change ends on, in the exchange''s own calendar.';

create index if not exists idx_macro_holding_watch_as_of
  on macro_core.holding_watch (as_of desc);

create table if not exists macro_core.holding_event (
  id              uuid primary key default gen_random_uuid(),
  symbol          text not null,
  kind            text not null check (kind in ('earnings', 'product', 'regulatory', 'unlock', 'other')),
  name            text not null,
  name_key        text not null,
  starts_at       timestamptz null,
  ends_at         timestamptz null,
  date_precision  text not null check (date_precision in ('exact', 'day', 'window', 'month', 'unknown')),
  date_estimated  boolean not null default false,
  date_text       text null,
  source_url      text not null,
  catalyst_id     uuid null references macro_core.catalyst (id) on delete set null,
  run_id          bigint null references macro_quality.ingest_run (id) on delete set null,
  first_seen      date not null,
  last_seen       date not null,
  constraint uq_macro_holding_event unique (symbol, name_key)
);

comment on table macro_core.holding_event is
  'What is coming up for a watched name in the next fourteen days, as the holdings watch found it. name_key is the same hash of the name catalyst.external_key uses. An earnings event is also placed on the catalyst calendar, and catalyst_id points at that row.';

create index if not exists idx_macro_holding_event_starts_at
  on macro_core.holding_event (starts_at);

-- ============================================================
-- 3. The operator's edits, through four functions
-- ============================================================

create or replace function macro_mart.save_manual_position(
  p_id uuid,
  p_symbol text,
  p_name text,
  p_venue text,
  p_asset_class text,
  p_quantity numeric,
  p_note text
)
returns uuid
language plpgsql
security definer
set search_path = macro_core, pg_temp
as $$
declare
  v_id uuid;
  v_symbol text := upper(btrim(coalesce(p_symbol, '')));
begin
  if auth.uid() is null then
    raise exception 'save_manual_position needs a signed-in operator';
  end if;
  if v_symbol !~ '^[A-Z0-9][A-Z0-9.^=/:-]{0,23}$' then
    raise exception 'a symbol is letters, digits and . ^ = / : - only, up to 24 characters, not %', p_symbol;
  end if;
  if p_venue is null or length(btrim(p_venue)) = 0 then
    raise exception 'a manual position names the broker or venue it is held at';
  end if;
  if p_id is null then
    insert into macro_core.manual_position (symbol, name, venue, asset_class, quantity, note)
    values (v_symbol, nullif(btrim(p_name), ''), btrim(p_venue), p_asset_class, p_quantity,
            nullif(btrim(p_note), ''))
    returning id into v_id;
  else
    update macro_core.manual_position
       set symbol = v_symbol,
           name = nullif(btrim(p_name), ''),
           venue = btrim(p_venue),
           asset_class = p_asset_class,
           quantity = p_quantity,
           note = nullif(btrim(p_note), ''),
           updated_at = now()
     where id = p_id and active
    returning id into v_id;
    if v_id is null then
      raise exception 'manual position % is not an active row', p_id;
    end if;
  end if;
  return v_id;
end $$;

create or replace function macro_mart.retire_manual_position(p_id uuid)
returns void
language plpgsql
security definer
set search_path = macro_core, pg_temp
as $$
begin
  if auth.uid() is null then
    raise exception 'retire_manual_position needs a signed-in operator';
  end if;
  update macro_core.manual_position set active = false, updated_at = now()
   where id = p_id and active;
  if not found then
    raise exception 'manual position % is not an active row', p_id;
  end if;
end $$;

create or replace function macro_mart.save_watch_ticker(
  p_id uuid,
  p_symbol text,
  p_name text,
  p_asset_class text,
  p_note text
)
returns uuid
language plpgsql
security definer
set search_path = macro_core, pg_temp
as $$
declare
  v_id uuid;
  v_symbol text := upper(btrim(coalesce(p_symbol, '')));
begin
  if auth.uid() is null then
    raise exception 'save_watch_ticker needs a signed-in operator';
  end if;
  if v_symbol !~ '^[A-Z0-9][A-Z0-9.^=/:-]{0,23}$' then
    raise exception 'a symbol is letters, digits and . ^ = / : - only, up to 24 characters, not %', p_symbol;
  end if;
  if p_id is null then
    insert into macro_core.watch_ticker (symbol, name, asset_class, note)
    values (v_symbol, nullif(btrim(p_name), ''), coalesce(p_asset_class, 'equity'),
            nullif(btrim(p_note), ''))
    returning id into v_id;
  else
    update macro_core.watch_ticker
       set symbol = v_symbol,
           name = nullif(btrim(p_name), ''),
           asset_class = coalesce(p_asset_class, asset_class),
           note = nullif(btrim(p_note), ''),
           updated_at = now()
     where id = p_id and active
    returning id into v_id;
    if v_id is null then
      raise exception 'watched ticker % is not an active row', p_id;
    end if;
  end if;
  return v_id;
end $$;

create or replace function macro_mart.retire_watch_ticker(p_id uuid)
returns void
language plpgsql
security definer
set search_path = macro_core, pg_temp
as $$
begin
  if auth.uid() is null then
    raise exception 'retire_watch_ticker needs a signed-in operator';
  end if;
  update macro_core.watch_ticker set active = false, updated_at = now()
   where id = p_id and active;
  if not found then
    raise exception 'watched ticker % is not an active row', p_id;
  end if;
end $$;

revoke all on function macro_mart.save_manual_position(uuid, text, text, text, text, numeric, text) from public, anon;
revoke all on function macro_mart.retire_manual_position(uuid) from public, anon;
revoke all on function macro_mart.save_watch_ticker(uuid, text, text, text, text) from public, anon;
revoke all on function macro_mart.retire_watch_ticker(uuid) from public, anon;
grant execute on function macro_mart.save_manual_position(uuid, text, text, text, text, numeric, text) to authenticated, service_role;
grant execute on function macro_mart.retire_manual_position(uuid) to authenticated, service_role;
grant execute on function macro_mart.save_watch_ticker(uuid, text, text, text, text) to authenticated, service_role;
grant execute on function macro_mart.retire_watch_ticker(uuid) to authenticated, service_role;

-- ============================================================
-- 4. The views the page reads
-- ============================================================

drop view if exists macro_mart.manual_position_state;
create view macro_mart.manual_position_state
with (security_invoker = false)
as
  select id, symbol, name, venue, asset_class, quantity, note, created_at, updated_at
  from macro_core.manual_position
  where active;

comment on view macro_mart.manual_position_state is
  'The active manual positions, for the dashboard''s edit list. Edited through save_manual_position and retire_manual_position.';

drop view if exists macro_mart.watch_ticker_state;
create view macro_mart.watch_ticker_state
with (security_invoker = false)
as
  select id, symbol, name, asset_class, note, created_at, updated_at
  from macro_core.watch_ticker
  where active;

comment on view macro_mart.watch_ticker_state is
  'The active watched tickers, for the dashboard''s edit list. Edited through save_watch_ticker and retire_watch_ticker.';

-- The latest watch: every row of the most recent day that has one. The desk writes a
-- row for every watched name on every live run, so the latest day is the whole list;
-- a name that stopped being held is simply absent from it.
drop view if exists macro_mart.holding_watch_state;
create view macro_mart.holding_watch_state
with (security_invoker = false)
as
  select w.as_of, w.symbol, w.source, w.name, w.asset_class, w.market_symbol, w.price,
         w.price_at, w.currency, w.change_24h_pct, w.change_basis, w.change_session,
         w.price_source, w.unavailable_reason, w.underlying_only, w.quantity,
         w.exposure_usd, w.material, w.driver_summary, w.source_url, w.recorded_at
  from macro_core.holding_watch w
  where w.as_of = (select max(as_of) from macro_core.holding_watch);

comment on view macro_mart.holding_watch_state is
  'The latest holdings watch, one row per watched name. change_24h_pct is null with unavailable_reason when the desk could not compute it; it is never a model''s number.';

drop view if exists macro_mart.holding_event_state;
create view macro_mart.holding_event_state
with (security_invoker = false)
as
  select e.id, e.symbol, e.kind, e.name, e.starts_at, e.ends_at, e.date_precision,
         e.date_estimated, e.date_text, e.source_url, e.catalyst_id, e.first_seen,
         e.last_seen
  from macro_core.holding_event e
  where e.starts_at is not null
    and coalesce(e.ends_at, e.starts_at) >= now() - interval '1 day'
    and e.starts_at <= now() + interval '14 days';

comment on view macro_mart.holding_event_state is
  'What is coming up for a watched name in the next fourteen days, and what ended less than a day ago.';

-- ============================================================
-- Grants and policies
-- ============================================================

alter table macro_core.manual_position enable row level security;
alter table macro_core.watch_ticker enable row level security;
alter table macro_core.holding_watch enable row level security;
alter table macro_core.holding_event enable row level security;

revoke all on macro_core.manual_position, macro_core.watch_ticker, macro_core.holding_watch,
  macro_core.holding_event from public, anon, authenticated;
grant all on macro_core.manual_position, macro_core.watch_ticker, macro_core.holding_watch,
  macro_core.holding_event to service_role;
grant usage, select on sequence macro_core.holding_watch_id_seq to service_role;

do $$
declare
  v text;
begin
  foreach v in array array['manual_position_state', 'watch_ticker_state', 'holding_watch_state',
                           'holding_event_state'] loop
    execute format('revoke all on macro_mart.%I from public, anon', v);
    execute format('grant select on macro_mart.%I to authenticated', v);
    execute format('grant select on macro_mart.%I to service_role', v);
  end loop;
end $$;

-- macro_desk reads the two lists and writes the watch. It never edits the lists.
grant select on macro_core.manual_position, macro_core.watch_ticker to macro_desk;
drop policy if exists macro_desk_select on macro_core.manual_position;
create policy macro_desk_select on macro_core.manual_position
  for select to macro_desk using (true);
drop policy if exists macro_desk_select on macro_core.watch_ticker;
create policy macro_desk_select on macro_core.watch_ticker
  for select to macro_desk using (true);

grant select, insert, update on macro_core.holding_watch, macro_core.holding_event to macro_desk;
grant usage, select on sequence macro_core.holding_watch_id_seq to macro_desk;
drop policy if exists macro_desk_rw on macro_core.holding_watch;
create policy macro_desk_rw on macro_core.holding_watch
  for all to macro_desk using (true) with check (true);
drop policy if exists macro_desk_rw on macro_core.holding_event;
create policy macro_desk_rw on macro_core.holding_event
  for all to macro_desk using (true) with check (true);

grant select on macro_mart.holding_watch_state, macro_mart.holding_event_state to macro_desk;
