-- 20260923000400_catalyst_occurrence.sql
-- The catalyst archive, per docs/DECISIONS.md 2026-09-23 "Every catalyst that happened
-- is kept, one row per occurrence".
--
-- macro_core.catalyst is the live calendar: keyed on a hash of the name so that a
-- reschedule updates rather than duplicates, which also means a recurring name such as
-- "U.S. monthly options expiration" is one row whose date moves forward every month.
-- It cannot answer "what happened this year". This table can: one row per event that
-- actually took place, tagged with the recurring family it belongs to, so a later
-- review can line up every CPI, every FOMC and every NVIDIA print of the year.
--
-- Rows arrive two ways:
--   - the worker's 09:30 full run archives a live catalyst once its start has passed
--     while the Catalyst Monitor was still listing it (provenance catalyst_monitor);
--   - a one-off backfill loaded the year before the worker began, from the monitor's
--     git history and the weekly journals (provenance catalyst_monitor or weekly_journal).
--
-- Also adds macro_core.catalyst.date_text, the monitor's own "Date & Time" words, so an
-- archived row can say whether its date was confirmed or only ever "expected".
--
-- Re-runnable: IF NOT EXISTS throughout, idempotent grants.

alter table macro_core.catalyst
  add column if not exists date_text text null;

comment on column macro_core.catalyst.date_text is
  'The Catalyst Monitor''s "Date & Time" field as written, e.g. "late October 2026 expected". starts_at is parsed from it; this keeps the words, which say whether the date was confirmed.';

create table if not exists macro_core.catalyst_occurrence (
  id              uuid primary key default gen_random_uuid(),
  name            text not null,
  name_key        text not null,
  occurred_on     date not null,
  starts_at       timestamptz null,
  ends_at         timestamptz null,
  date_precision  text not null check (date_precision in ('exact', 'day', 'window')),
  family          text null,
  category        text not null check (category in (
                    'us_macro', 'global_macro', 'fed', 'treasury_liquidity', 'index_options',
                    'global_central_bank', 'earnings', 'ai_tech', 'crypto', 'trade_tariffs',
                    'policy_geopolitics', 'commodities_energy', 'market_closure',
                    'investor_forum', 'market_structure', 'other')),
  source_url      text null,
  affected        text[] not null default '{}',
  provenance      text not null check (provenance in ('catalyst_monitor', 'weekly_journal')),
  provenance_ref  text null,
  confidence      text not null check (confidence in ('confirmed', 'approximate')),
  moved_markets   boolean null,
  market_note     text null,
  note            text null,
  catalyst_id     uuid null references macro_core.catalyst (id) on delete set null,
  first_tracked   date null,
  last_tracked    date null,
  archived_at     timestamptz not null default now(),
  constraint uq_macro_catalyst_occurrence unique (name_key, occurred_on)
);

comment on table macro_core.catalyst_occurrence is
  'Every catalyst that took place, one row per occurrence, kept for ever. The live calendar is macro_core.catalyst; this is its history.';
comment on column macro_core.catalyst_occurrence.name_key is
  'The same hash of the name that macro_core.catalyst.external_key uses, so a live row and its archived occurrence share it.';
comment on column macro_core.catalyst_occurrence.occurred_on is
  'The UTC date of starts_at, or the stated date when there is no time. With name_key it is the natural key: the same event on two dates is two rows.';
comment on column macro_core.catalyst_occurrence.family is
  'The recurring kind of event, e.g. us_cpi, fomc_decision, earnings_nvda, from the worker''s families table. Null for a one-off.';
comment on column macro_core.catalyst_occurrence.confidence is
  'confirmed when the date was firm before the event passed; approximate when the source only ever had an expected or TBD date, or a journal gave the date without confirming it.';
comment on column macro_core.catalyst_occurrence.moved_markets is
  'What a weekly journal said about the event after the fact: true when it records a material move. Null when no source says. Not measured from prices.';

create index if not exists idx_macro_catalyst_occurrence_occurred_on
  on macro_core.catalyst_occurrence (occurred_on);

create index if not exists idx_macro_catalyst_occurrence_family
  on macro_core.catalyst_occurrence (family, occurred_on)
  where family is not null;

-- ============================================================
-- Grants: macro_core stays unexposed. RLS on with no policies, service_role only.
-- ============================================================

alter table macro_core.catalyst_occurrence enable row level security;

revoke all on macro_core.catalyst_occurrence from public, anon, authenticated;
grant all on macro_core.catalyst_occurrence to service_role;
