-- 20260925000200_desk_hardening.sql
-- The review of the desk seam (20260924000200 to 20260924000500), 2026-09-25.
--
-- 1. Accounting's venue reasons are redacted inside the definer function. A reason
--    such as "Tiger account 50400376 holds cash only" left the function with the
--    broker account number in it, and only the desk command's own Python took it out.
--    Any other caller of macro_core.accounting_venue_status() got the number. Now the
--    function returns "Tiger account holds cash only", and no string it returns
--    carries a run of five or more digits or a 0x address. The two free-text name
--    columns of accounting_open_positions() get the same treatment; the symbols do
--    not, because an option's symbol is digits by design.
--
-- 2. Tail-risk slugs. Three faults in tail_risk_slug() and the insert trigger:
--    - the worker re-lists a risk with INSERT ... ON CONFLICT (key) DO UPDATE and no
--      risk_id. A BEFORE INSERT trigger runs before the conflict is found, so it
--      derived a fresh slug, found its own row holding it, and suffixed it. When the
--      44-character cut ended on a hyphen the suffix made '--2', which fails
--      ck_macro_tail_risk_risk_id, and the whole upsert failed. The trigger now reuses
--      the risk_id already stored under the same key before deriving anything;
--    - a 48-character cut that ended on a hyphen gave an id the check refuses;
--      every cut is now right-trimmed of hyphens;
--    - a name of one or two characters ("AI") gave an id under the check's three;
--      a short id is padded with "-risk".
--
-- 3. Policies narrowed to what the desk command does:
--    - ingest_run: macro_desk sees only desk runs, and inserts or updates only runs
--      whose origin is 'openclaw:macro-desk';
--    - catalyst and tail_risk: every row macro_desk inserts or updates says
--      feed_source = 'openclaw:macro-desk'. The one write the desk makes to rows
--      another feed wrote, confirming the calendar is still listed (last_seen), goes
--      through macro_core.desk_confirm_catalysts_listed(), which can set nothing else;
--    - finding: check_key like 'desk\_%', with the underscore escaped (unescaped, '_'
--      matched any character, so 'deskX...' passed).
--
-- 4. macro_desk's sessions: a transaction left idle is ended after 60 seconds and a
--    lock is waited on for at most 10, beside the 30-second statement_timeout of
--    20260924000200. These are role settings, so they take effect on the next login
--    and supabase/desk_role.py does not need to run again.
--
-- 5. Checks: a holding event ends no earlier than it starts and was first seen no
--    later than last seen; a manual position's quantity is not negative; every
--    source_url on the five feed tables is http(s), or empty, which is what the vault
--    parser writes for a missing source.
--
-- 6. holding_watch.no_clear_reason: the holdings agent looked for why a name moved
--    and found no sourced story. A material move may then carry no driver and no
--    source_url. holding_watch_state gains the column at its end.
--
-- 7. The dashboard's two list functions say what went wrong in words a person can act
--    on (the web shows the message verbatim): a duplicate row, a venue name over 40
--    characters, an unknown asset class, a negative quantity. Before, the operator saw
--    a unique_violation or check_violation naming an index.
--
-- Re-runnable: CREATE OR REPLACE, IF NOT EXISTS, constraints and policies dropped
-- before they are added. Every existing row satisfies the new checks (verified
-- against the live project on 2026-09-25).

-- ============================================================
-- 1. Account identifiers never leave Accounting's functions
-- ============================================================

create or replace function macro_core.redact_account_ids(p_text text)
returns text
language sql
immutable
set search_path = pg_catalog, pg_temp
as $$
  select regexp_replace(
           regexp_replace(
             regexp_replace(
               p_text,
               '(account|acct|a/c)(\s+(no\.?|number|id|#))?\s*[:#]?\s*[A-Za-z]{0,4}[0-9][0-9-]{3,}[0-9]',
               '\1', 'gi'),
             '0x[0-9a-fA-F]{6,}', '[withheld]', 'g'),
           '[0-9]{5,}', '[withheld]', 'g');
$$;

comment on function macro_core.redact_account_ids(text) is
  'Accounting free text with account numbers and wallet addresses taken out: "account 50400376" becomes "account", a 0x address and any other run of five or more digits become [withheld].';

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
    select macro_core.redact_account_ids(vs.venue::text),
           macro_core.redact_account_ids(vs.state::text),
           macro_core.redact_account_ids(vs.reason::text),
           vs.venue_as_of::timestamptz,
           vs.received_at::timestamptz,
           vs.position_count::int
      from mart.venue_status vs
     order by vs.venue, vs.position_count desc
  $q$;
end $$;

comment on function macro_core.accounting_venue_status() is
  'How fresh each Accounting venue is, so the holdings watch can say when the book it read was stale. Never returns account_id or a balance, and every string is passed through redact_account_ids, so no account number or address leaves it.';

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
           macro_core.redact_account_ids(bp.name::text),
           macro_core.redact_account_ids(bp.display_name::text),
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
  'Accounting''s open non-cash positions (mart.book_position where quantity <> 0), with the option underlying from core.instrument. Never returns the accounts column, and the two name columns pass through redact_account_ids. SECURITY DEFINER so macro_desk needs no grant or policy on Accounting''s schemas; dynamic SQL so it holds no dependency on Accounting''s columns.';

revoke all on function macro_core.accounting_open_positions() from public, anon, authenticated;
revoke all on function macro_core.accounting_venue_status() from public, anon, authenticated;
grant execute on function macro_core.accounting_open_positions() to macro_desk, service_role;
grant execute on function macro_core.accounting_venue_status() to macro_desk, service_role;

-- ============================================================
-- 2. Tail-risk slugs that always pass the check
-- ============================================================

create or replace function macro_core.tail_risk_slug(p_name text)
returns text
language sql
immutable
set search_path = pg_catalog, pg_temp
as $$
  with cut as (
    select coalesce(
             nullif(
               rtrim(
                 left(
                   array_to_string(
                     (regexp_split_to_array(
                        trim(both '-' from regexp_replace(lower(coalesce(p_name, '')), '[^a-z0-9]+', '-', 'g')),
                        '-'))[1:6],
                     '-'),
                   48),
                 '-'),
               ''),
             'risk') as slug
  )
  select case when length(slug) < 3 then slug || '-risk' else slug end from cut;
$$;

create or replace function macro_core.tail_risk_default_id()
returns trigger
language plpgsql
set search_path = pg_catalog, pg_temp
as $$
declare
  base text;
  candidate text;
  stored text;
  n int := 1;
begin
  if new.risk_id is not null then
    return new;
  end if;
  -- A re-listing of a risk already stored (the worker's upsert on key) keeps its id.
  -- The trigger runs before ON CONFLICT finds the row, so without this it derived a
  -- second id for a row that already has one.
  select r.risk_id into stored from macro_core.tail_risk r where r.key = new.key;
  if stored is not null then
    new.risk_id := stored;
    return new;
  end if;
  base := macro_core.tail_risk_slug(new.name);
  candidate := base;
  while exists (select 1 from macro_core.tail_risk where risk_id = candidate) loop
    n := n + 1;
    candidate := rtrim(left(base, 44), '-') || '-' || n;
  end loop;
  new.risk_id := candidate;
  return new;
end $$;

-- ============================================================
-- 3. Policies: macro_desk writes as the desk, and only as the desk
-- ============================================================

drop policy if exists macro_desk_select on macro_quality.ingest_run;
create policy macro_desk_select on macro_quality.ingest_run
  for select to macro_desk using (source like 'desk:%');
drop policy if exists macro_desk_insert on macro_quality.ingest_run;
create policy macro_desk_insert on macro_quality.ingest_run
  for insert to macro_desk
  with check (source like 'desk:%' and run_kind = 'desk' and origin = 'openclaw:macro-desk');
drop policy if exists macro_desk_update on macro_quality.ingest_run;
create policy macro_desk_update on macro_quality.ingest_run
  for update to macro_desk
  using (source like 'desk:%')
  with check (source like 'desk:%' and run_kind = 'desk' and origin = 'openclaw:macro-desk');

drop policy if exists macro_desk_insert on macro_quality.finding;
create policy macro_desk_insert on macro_quality.finding
  for insert to macro_desk with check (check_key like 'desk\_%');

do $$
declare
  t text;
begin
  foreach t in array array['catalyst', 'tail_risk'] loop
    execute format('drop policy if exists macro_desk_rw on macro_core.%I', t);
    execute format('drop policy if exists macro_desk_select on macro_core.%I', t);
    execute format('drop policy if exists macro_desk_insert on macro_core.%I', t);
    execute format('drop policy if exists macro_desk_update on macro_core.%I', t);
    execute format('create policy macro_desk_select on macro_core.%I for select to macro_desk using (true)', t);
    execute format('create policy macro_desk_insert on macro_core.%I for insert to macro_desk with check (feed_source = %L)', t, 'openclaw:macro-desk');
    execute format('create policy macro_desk_update on macro_core.%I for update to macro_desk using (true) with check (feed_source = %L)', t, 'openclaw:macro-desk');
  end loop;
end $$;

-- The desk confirms the calendar it read is still listed. That touches rows the vault
-- wrote, so it cannot pass the feed_source check above, and it may only move
-- last_seen forward to a day that has started somewhere.
create or replace function macro_core.desk_confirm_catalysts_listed(p_on date)
returns integer
language plpgsql
security definer
set search_path = pg_catalog, pg_temp
as $$
declare
  n integer;
begin
  if p_on is null or p_on > current_date + 1 then
    raise exception 'desk_confirm_catalysts_listed takes a day no later than tomorrow, not %', p_on;
  end if;
  update macro_core.catalyst
     set last_seen = p_on
   where status = 'tracked'
     and last_seen < p_on;
  get diagnostics n = row_count;
  return n;
end $$;

comment on function macro_core.desk_confirm_catalysts_listed(date) is
  'Every tracked catalyst is still on the desk''s calendar as of p_on: moves last_seen forward and changes nothing else. SECURITY DEFINER so macro_desk needs no update right over rows another feed wrote.';

revoke all on function macro_core.desk_confirm_catalysts_listed(date) from public, anon, authenticated;
grant execute on function macro_core.desk_confirm_catalysts_listed(date) to macro_desk, service_role;

-- ============================================================
-- 4. macro_desk's sessions
-- ============================================================

alter role macro_desk set idle_in_transaction_session_timeout = '60s';
alter role macro_desk set lock_timeout = '10s';

-- ============================================================
-- 5. Checks
-- ============================================================

alter table macro_core.holding_event drop constraint if exists ck_macro_holding_event_span;
alter table macro_core.holding_event add constraint ck_macro_holding_event_span
  check (ends_at is null or starts_at is null or ends_at >= starts_at);
alter table macro_core.holding_event drop constraint if exists ck_macro_holding_event_seen;
alter table macro_core.holding_event add constraint ck_macro_holding_event_seen
  check (first_seen <= last_seen);

alter table macro_core.manual_position drop constraint if exists ck_macro_manual_position_quantity;
alter table macro_core.manual_position add constraint ck_macro_manual_position_quantity
  check (quantity is null or quantity >= 0);

do $$
declare
  t text;
begin
  foreach t in array array['catalyst', 'tail_risk', 'tail_risk_check', 'holding_watch', 'holding_event'] loop
    execute format('alter table macro_core.%I drop constraint if exists %I', t, 'ck_macro_' || t || '_source_url');
    execute format(
      'alter table macro_core.%I add constraint %I check (source_url is null or source_url = %L or source_url ~ %L)',
      t, 'ck_macro_' || t || '_source_url', '', '^https?://');
  end loop;
end $$;

-- ============================================================
-- 6. A move with no clear reason
-- ============================================================

alter table macro_core.holding_watch
  add column if not exists no_clear_reason boolean not null default false;

comment on column macro_core.holding_watch.no_clear_reason is
  'The holdings agent looked for why the name moved and found no sourced story. A material row may then carry no driver_summary and no source_url.';

alter table macro_core.holding_watch drop constraint if exists ck_macro_holding_watch_material;
alter table macro_core.holding_watch add constraint ck_macro_holding_watch_material
  check (not material or no_clear_reason or (driver_summary is not null and source_url is not null));

create or replace view macro_mart.holding_watch_state
with (security_invoker = false)
as
  select w.as_of, w.symbol, w.source, w.name, w.asset_class, w.market_symbol, w.price,
         w.price_at, w.currency, w.change_24h_pct, w.change_basis, w.change_session,
         w.price_source, w.unavailable_reason, w.underlying_only, w.quantity,
         w.exposure_usd, w.material, w.driver_summary, w.source_url, w.recorded_at,
         w.no_clear_reason
  from macro_core.holding_watch w
  where w.as_of = (select max(as_of) from macro_core.holding_watch);

-- ============================================================
-- 7. The dashboard's list functions, in plain words
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
  v_venue text := btrim(coalesce(p_venue, ''));
begin
  if auth.uid() is null then
    raise exception 'save_manual_position needs a signed-in operator';
  end if;
  if v_symbol !~ '^[A-Z0-9][A-Z0-9.^=/:-]{0,23}$' then
    raise exception 'a symbol is letters, digits and . ^ = / : - only, up to 24 characters, not %', p_symbol;
  end if;
  if length(v_venue) = 0 then
    raise exception 'a manual position names the broker or venue it is held at';
  end if;
  if length(v_venue) > 40 then
    raise exception 'the broker or venue name is at most 40 characters; this one is %', length(v_venue);
  end if;
  if p_asset_class is null or p_asset_class not in ('equity', 'etf', 'crypto', 'option', 'commodity', 'fixed_income', 'other') then
    raise exception 'the asset class is one of equity, etf, crypto, option, commodity, fixed_income or other, not %', coalesce(p_asset_class, 'blank');
  end if;
  if p_quantity is not null and p_quantity < 0 then
    raise exception 'a quantity is zero or more; the watch needs the name and the size, not the direction';
  end if;
  if exists (
    select 1 from macro_core.manual_position
     where active and symbol = v_symbol and lower(venue) = lower(v_venue)
       and id is distinct from p_id
  ) then
    raise exception '% at % is already on the manual list; edit that row instead', v_symbol, v_venue;
  end if;
  if p_id is null then
    insert into macro_core.manual_position (symbol, name, venue, asset_class, quantity, note)
    values (v_symbol, nullif(btrim(p_name), ''), v_venue, p_asset_class, p_quantity,
            nullif(btrim(p_note), ''))
    returning id into v_id;
  else
    update macro_core.manual_position
       set symbol = v_symbol,
           name = nullif(btrim(p_name), ''),
           venue = v_venue,
           asset_class = p_asset_class,
           quantity = p_quantity,
           note = nullif(btrim(p_note), ''),
           updated_at = now()
     where id = p_id and active
    returning id into v_id;
    if v_id is null then
      raise exception 'that manual position was removed or is not on the list any more; reload the page';
    end if;
  end if;
  return v_id;
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
  if p_asset_class is not null and p_asset_class not in ('equity', 'etf', 'crypto', 'option', 'commodity', 'fixed_income', 'other') then
    raise exception 'the asset class is one of equity, etf, crypto, option, commodity, fixed_income or other, not %', p_asset_class;
  end if;
  if exists (
    select 1 from macro_core.watch_ticker
     where active and symbol = v_symbol and id is distinct from p_id
  ) then
    raise exception '% is already on the watch list; edit that row instead', v_symbol;
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
      raise exception 'that watched ticker was removed or is not on the list any more; reload the page';
    end if;
  end if;
  return v_id;
end $$;
