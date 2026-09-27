-- 20260921000200_raw_core_observed.sql
-- macro_raw and the observed-fact half of macro_core, per docs/CONTRACT.md.
-- Re-runnable: create table/index use IF NOT EXISTS.

-- ============================================================
-- macro_raw
-- ============================================================

create table if not exists macro_raw.batch (
  id          bigserial primary key,
  source      text not null,
  kind        text not null,
  fetched_at  timestamptz not null,
  payload     jsonb not null
);

comment on table macro_raw.batch is
  'Every API response and every scraped document lands here before normalisation. Search-agent returns land here as kind = ''search'', so a narrative point can be traced to the text it came from.';

create index if not exists idx_macro_raw_batch_source_fetched_at
  on macro_raw.batch (source, fetched_at desc);

create index if not exists idx_macro_raw_batch_kind_fetched_at
  on macro_raw.batch (kind, fetched_at desc);

-- ============================================================
-- macro_core: observed facts
-- ============================================================

-- Asserted reference data, seeded from config/series.yaml, never written by a fetch.
--
-- Two columns are here that docs/CONTRACT.md's column list for this table omits, and
-- both are load-bearing elsewhere in the same contract:
--   ordinal    macro_mart.snapshot_row carries an `ordinal` and nothing else declares
--              the order of rows inside a table_group. Row order is asserted reference
--              data, not something the evaluate step can derive.
--   fetch_path the contract's "Sources and their cadence" table has a Path column with
--              exactly two values, and the 21:00 rescore run is defined as "fetch every
--              deterministic source". Without this column that run cannot tell which
--              sources it owns.
create table if not exists macro_core.series (
  id           uuid primary key default gen_random_uuid(),
  key          text not null unique,
  label        text not null,
  table_group  text not null check (table_group in ('Price & Levels', 'Sentiment & Positioning', 'Flows & Structure', 'Macro & Fiscal State')),
  ordinal      int not null,
  value_kind   text not null check (value_kind in ('numeric', 'text')),
  unit         text null,
  decimals     int null,
  cadence      text not null check (cadence in ('session', 'daily', 'weekly', 'monthly', 'event')),
  release_rule text not null,
  replayable   boolean not null,
  source_name  text not null,
  fetch_path   text not null check (fetch_path in ('deterministic', 'agent_0930')),
  tier_floor   int not null default 1 check (tier_floor between 1 and 4),
  unique (table_group, ordinal)
);

comment on table macro_core.series is
  'Asserted reference data, seeded from config/series.yaml, never written by a fetch.';
comment on column macro_core.series.key is
  'The stable identifier every condition expression resolves against. Never reused, never renamed.';
comment on column macro_core.series.label is
  'The display string as the journal writes it. May change without breaking a condition.';
comment on column macro_core.series.unit is
  'The one canonical unit for this series. Every condition referencing it is written in this unit. hy_oas_bps is basis points because every condition against it is written in basis points; a condition comparing 2.70 against 300 is silently false and looks correct.';
comment on column macro_core.series.value_kind is
  'text for the five Macro & Fiscal State rows, which are prose. A text series stores value null and its content in display, and may only appear in a manual condition. Without this a prose row and a Tier 4 failure are the same shape.';
comment on column macro_core.series.release_rule is
  'Declarative, grammar documented in the header of config/series.yaml. What next_expected is computed from.';
comment on column macro_core.series.replayable is
  'False for a source that only serves the present moment. A missed point-in-time reading is a permanent macro_quality.gap, never an interpolation.';
comment on column macro_core.series.tier_floor is
  'The best tier this source can ever attain. A top-ten-exchange proxy or an aggregator quote can never be Tier 1.';

-- One row per series per observation date.
--
-- observed_at is in the contract's prose for this table but not in its column list;
-- it is the column that says which of the day's two runs produced the row.
create table if not exists macro_core.observation (
  series_id    uuid not null references macro_core.series (id),
  asof         date not null,
  value        numeric null,
  display      text null,
  tier         int not null check (tier between 1 and 4),
  source       text not null,
  released     date null,
  note         text null,
  extra        jsonb not null default '{}'::jsonb,
  observed_at  timestamptz null,
  received_at  timestamptz not null default now(),
  primary key (series_id, asof)
);

comment on table macro_core.observation is
  'One row per series per observation date. value null with a note is a Tier 4 reading, never a zero. Two runs on one day upsert the same (series_id, asof) row and the later one wins.';
comment on column macro_core.observation.asof is
  'The observation date in the source''s own market calendar, never the operator''s local date. A Friday US close observed at 05:00 Saturday Singapore time has a Friday asof.';
comment on column macro_core.observation.observed_at is
  'The moment the reading described: the run time for a 24-hour series, the close for a session series. What says which of the day''s two runs wrote this row.';
comment on column macro_core.observation.extra is
  'Components: the 200-day distance and slope, the CFTC regime band and open interest, the three net-liquidity components, funding by symbol. Price series additionally carry session_high and session_low, from which the setup band-touch flag is derived.';

-- The access pattern: latest observations for one series, newest first.
create index if not exists idx_macro_observation_series_asof
  on macro_core.observation (series_id, asof desc);

create index if not exists idx_macro_observation_asof
  on macro_core.observation (asof desc);

-- Parsed from Catalyst Monitor.md.
create table if not exists macro_core.catalyst (
  id              uuid primary key default gen_random_uuid(),
  external_key    text not null unique,
  name            text not null,
  starts_at       timestamptz null,
  ends_at         timestamptz null,
  date_precision  text not null,
  source_url      text null,
  affected        text[] not null default '{}',
  status          text not null check (status in ('tracked', 'retired', 'rescheduled')),
  first_seen      date not null,
  last_seen       date not null,
  retired_at      date null,
  retired_reason  text null
);

comment on column macro_core.catalyst.external_key is
  'A stable hash of the catalyst name, so a reschedule updates rather than duplicates.';

-- The access pattern: the next thirty days, chronological (macro_mart.catalyst_window).
create index if not exists idx_macro_catalyst_starts_at
  on macro_core.catalyst (starts_at);

create index if not exists idx_macro_catalyst_tracked_starts_at
  on macro_core.catalyst (starts_at)
  where status = 'tracked';

-- Parsed from Market Watchlist.md.
create table if not exists macro_core.watchlist_change (
  ticker       text not null,
  observed_at  timestamptz not null,
  change_pct   numeric null,
  summary      text null,
  source_url   text null,
  primary key (ticker, observed_at)
);

create index if not exists idx_macro_watchlist_change_observed_at
  on macro_core.watchlist_change (observed_at desc);

-- Parsed from the Watchlist ## Holdings list.
create table if not exists macro_core.holding (
  ticker  text primary key,
  label   text null,
  book    text not null check (book in ('equities', 'crypto')),
  active  boolean not null default true
);

create index if not exists idx_macro_holding_active_book
  on macro_core.holding (book)
  where active;

-- Parsed from Blackswan Events.md.
create table if not exists macro_core.tail_risk (
  key         text primary key,
  name        text not null,
  severity    text not null check (severity in ('acute', 'elevated', 'watch')),
  summary     text null,
  priced_in   text null,
  assets      text[] not null default '{}',
  source_url  text null,
  first_seen  date not null,
  last_seen   date not null
);

-- The dated event log.
create table if not exists macro_core.event (
  id           uuid primary key default gen_random_uuid(),
  occurred_on  date not null,
  kind         text not null check (kind in ('macro', 'structure')),
  text         text not null,
  assets       text[] not null default '{}',
  surprise     boolean not null default false,
  source       text null
);

create index if not exists idx_macro_event_occurred_on
  on macro_core.event (occurred_on desc);
