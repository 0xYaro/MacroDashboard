-- 20260923001000_catalyst_category.sql
-- The Catalysts page says what kind of event each one is, which dates are only
-- estimates, which tail risks the note still lists, and one watchlist move per
-- trading session.
--
-- 1. macro_core.catalyst gains `family`, `category` and `date_estimated`, and
--    macro_mart.catalyst_window carries them with `ends_at` and `date_text`.
--    The category is one of the sixteen in worker/src/macro/families.py, the same
--    rule table that already tags macro_core.catalyst_occurrence. It is read from
--    the name in Python, so it is not backfilled here: a second copy of a hundred
--    regular expressions in SQL would drift from the first. The next full worker
--    run re-parses Catalyst Monitor.md and writes all three columns on every
--    listed row; until then catalyst_window classifies from the name itself, so
--    a rescore alone already gives every window row a category.
--
--    `date_estimated` is true when the monitor's own words make the date a guess
--    ("late October 2026 expected", "around 2026-10-20 expected"), and false for
--    a firm day whose time is not out yet ("2026-09-25; release time TBD"). A
--    month-only date is now stored as the part of the month it names, start to
--    end ("late October" is 21 to 31 October), where it used to be the 1st.
--
-- 2. macro_core.tail_risk gains `retired_on`. Blackswan Events.md is written
--    weekly and read daily, so a risk the cron drops used to stay on the page for
--    ever, and `last_seen` moved every morning. The worker now dates a listing by
--    the note's own modification day and retires a risk the latest note no longer
--    lists. tail_risk_state shows only the risks still listed; the rest stay in
--    the table as the record.
--
-- 3. watchlist_change_state reports one row per ticker, session and move. The
--    09:15 cron re-reports Friday's session on Saturday, Sunday and Monday
--    mornings, so the same +3.92% arrived three times under three dates. The view
--    derives `session_on`, the last New York session that had closed when the
--    line was written (weekdays only; exchange holidays are not modelled), and
--    keeps the first report of each (ticker, session_on, change_pct). A different
--    move for the same session is a different claim and is kept.
--
-- Re-runnable: IF NOT EXISTS throughout, the constraint dropped before it is
-- added, views dropped and recreated with their grants.

-- ============================================================
-- 1. Catalyst category and estimated dates
-- ============================================================

alter table macro_core.catalyst
  add column if not exists date_estimated boolean not null default false,
  add column if not exists family text null,
  add column if not exists category text null;

alter table macro_core.catalyst drop constraint if exists ck_macro_catalyst_category;
alter table macro_core.catalyst add constraint ck_macro_catalyst_category check (
  category is null or category in (
    'us_macro', 'global_macro', 'fed', 'treasury_liquidity', 'index_options',
    'global_central_bank', 'earnings', 'ai_tech', 'crypto', 'trade_tariffs',
    'policy_geopolitics', 'commodities_energy', 'market_closure',
    'investor_forum', 'market_structure', 'other'));

comment on column macro_core.catalyst.date_estimated is
  'True when the Catalyst Monitor gives the date as a guess (expected, around, a part of a month), not a published schedule. A firm day with its time still TBD is false.';
comment on column macro_core.catalyst.family is
  'The recurring kind of event, e.g. us_cpi or earnings_nvda, from worker/src/macro/families.py. Null for a one-off.';
comment on column macro_core.catalyst.category is
  'One of sixteen categories from worker/src/macro/families.py, read from the name. Null only on a row no full run has re-parsed since 20260923001000.';

alter table macro_mart.catalyst_window
  add column if not exists ends_at timestamptz null,
  add column if not exists date_text text null,
  add column if not exists date_estimated boolean not null default false,
  add column if not exists family text null,
  add column if not exists category text null;

comment on column macro_mart.catalyst_window.ends_at is
  'The last day of a window, or of the part of the month an estimated date names. Null for a single day or time.';
comment on column macro_mart.catalyst_window.date_text is
  'The Catalyst Monitor''s "Date & Time" words, as written.';

-- ============================================================
-- 2. Tail risks the latest note no longer lists
-- ============================================================

alter table macro_core.tail_risk
  add column if not exists retired_on date null;

comment on column macro_core.tail_risk.retired_on is
  'The day of the first Blackswan Events.md that no longer listed this risk. Null while the latest note lists it.';
comment on column macro_core.tail_risk.last_seen is
  'The day of the latest Blackswan Events.md that listed this risk, from the file''s modification time, not the day the worker read it.';

drop view if exists macro_mart.tail_risk_state;
create view macro_mart.tail_risk_state
with (security_invoker = false)
as
  select key, name, severity, summary, priced_in, assets, source_url, first_seen, last_seen
  from macro_core.tail_risk
  where retired_on is null;

comment on view macro_mart.tail_risk_state is
  'The risks the latest Blackswan Events.md lists. A risk the weekly cron dropped is retired in macro_core.tail_risk and leaves this view.';

-- ============================================================
-- 3. One watchlist move per ticker and session
-- ============================================================

drop view if exists macro_mart.watchlist_change_state;
create view macro_mart.watchlist_change_state
with (security_invoker = false)
as
  select distinct on (ticker, session_on, change_pct)
    ticker, observed_at, change_pct, summary, source_url, session_on
  from (
    select w.ticker, w.observed_at, w.change_pct, w.summary, w.source_url,
           case
             when extract(isodow from w.ny) <= 5 and w.ny::time >= time '16:00'
               then w.ny::date
             else w.ny::date - case extract(isodow from w.ny)::int
                                 when 1 then 3
                                 when 7 then 2
                                 else 1
                               end
           end as session_on
    from (
      select c.*, c.observed_at at time zone 'America/New_York' as ny
      from macro_core.watchlist_change c
      where c.observed_at > now() - interval '14 days'
    ) w
  ) s
  order by ticker, session_on, change_pct, observed_at;

comment on view macro_mart.watchlist_change_state is
  'The last fourteen days of Market Watchlist.md deltas, one per ticker, session and move. session_on is the last New York weekday session closed when the cron wrote the line; a weekend repeat of that session''s move is dropped.';

-- ============================================================
-- Grants: the views were dropped, so their grants go back on.
-- ============================================================

do $$
declare
  v text;
begin
  foreach v in array array['tail_risk_state', 'watchlist_change_state'] loop
    execute format('revoke all on macro_mart.%I from public, anon', v);
    execute format('grant select on macro_mart.%I to authenticated', v);
    execute format('grant select on macro_mart.%I to service_role', v);
  end loop;
end $$;
