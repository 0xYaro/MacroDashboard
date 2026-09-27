-- 20260921000300_core_judgment.sql
-- The asserted-judgment half of macro_core, per docs/CONTRACT.md.
--
-- Kept in separate tables from observed facts, so a later reader can always tell an
-- assertion from a disclosure.
--
-- Lifecycle. Every judgment table here carries the same six columns:
--   status        proposed | active | superseded
--   authored_at   when the authoring agent wrote the row
--   activated_at  when the operator accepted it; null while proposed
--   superseded_by the row that replaced this one; null while it stands
--   amends        the row this one amends (a re-price, per docs/DECISIONS.md)
--   amend_reason  the triggering event, in one sentence
-- Judgment is never updated in place. A new row supersedes the old one and the old
-- one is kept, because Brier scored as-stated against as-amended is the whole point.
--
-- The "at most one active" rules below are partial unique indexes, and a partial unique
-- index cannot be deferred. An activation is therefore always two statements in this
-- order: stand the outgoing row down to superseded, then stand the incoming row up to
-- active. Doing it the other way round leaves two active rows for the length of one
-- statement and is rejected. Both statements belong to one transaction.
--
-- Re-runnable: create table/index use IF NOT EXISTS.

-- ============================================================
-- Phase calls
-- ============================================================

create table if not exists macro_core.phase_call (
  id             uuid primary key default gen_random_uuid(),
  asset          text not null check (asset in ('equities', 'crypto')),
  week           text not null,
  phase          int not null,
  phase_name     text not null,
  prior_phase    int null,
  direction      text not null check (direction in ('up', 'unchanged', 'down')),
  reason         text not null,
  evidence       text[] not null default '{}',
  falsifier      text not null,
  status         text not null check (status in ('proposed', 'active', 'superseded')),
  authored_at    timestamptz not null default now(),
  activated_at   timestamptz null,
  superseded_by  uuid null references macro_core.phase_call (id),
  amends         uuid null references macro_core.phase_call (id),
  amend_reason   text null
);

-- At most one active phase call per asset. Not per (asset, week): a phase call
-- stands until it is superseded, which is what "was Phase M last week" means.
create unique index if not exists uq_macro_phase_call_active_asset
  on macro_core.phase_call (asset)
  where status = 'active';

create index if not exists idx_macro_phase_call_week
  on macro_core.phase_call (week);

create index if not exists idx_macro_phase_call_superseded_by
  on macro_core.phase_call (superseded_by)
  where superseded_by is not null;

-- Exactly four per phase call; enforced by the deferred constraint triggers in
-- 20260921000500_core_constraints.sql. The 3-of-4 rule is scored against these.
create table if not exists macro_core.phase_condition (
  id             uuid primary key default gen_random_uuid(),
  phase_call_id  uuid not null references macro_core.phase_call (id) on delete cascade,
  ordinal        int not null check (ordinal between 1 and 4),
  text           text not null,
  kind           text not null check (kind in ('expr', 'predicate', 'manual')),
  expr           jsonb null,
  unique (phase_call_id, ordinal)
);

-- An expr or predicate condition must carry its expression; a manual one must not.
alter table macro_core.phase_condition
  drop constraint if exists ck_macro_phase_condition_expr_present;
alter table macro_core.phase_condition
  add constraint ck_macro_phase_condition_expr_present
  check ((kind = 'manual' and expr is null) or (kind in ('expr', 'predicate') and expr is not null));

-- ============================================================
-- Scenario sets
-- ============================================================

create table if not exists macro_core.scenario_set (
  id                 uuid primary key default gen_random_uuid(),
  week               text not null,
  synthesis          text null,
  probability_check  text null,
  swing_variables    jsonb not null default '[]'::jsonb,
  rules_waived       int[] not null default '{}',
  status             text not null check (status in ('proposed', 'active', 'superseded')),
  authored_at        timestamptz not null default now(),
  activated_at       timestamptz null,
  superseded_by      uuid null references macro_core.scenario_set (id),
  amends             uuid null references macro_core.scenario_set (id),
  amend_reason       text null
);

-- At most one active scenario set per week.
create unique index if not exists uq_macro_scenario_set_active_week
  on macro_core.scenario_set (week)
  where status = 'active';

create index if not exists idx_macro_scenario_set_superseded_by
  on macro_core.scenario_set (superseded_by)
  where superseded_by is not null;

-- Exactly three per set, probabilities summing to 100; both enforced by the deferred
-- constraint triggers in 20260921000500_core_constraints.sql, because the rows land one at a time.
create table if not exists macro_core.scenario (
  id           uuid primary key default gen_random_uuid(),
  set_id       uuid not null references macro_core.scenario_set (id) on delete cascade,
  ordinal      int not null check (ordinal between 1 and 3),
  title        text not null,
  probability  int not null check (probability between 0 and 100),
  positioning  text null,
  unique (set_id, ordinal)
);

create table if not exists macro_core.trigger_clause (
  id           uuid primary key default gen_random_uuid(),
  scenario_id  uuid not null references macro_core.scenario (id) on delete cascade,
  ordinal      int not null,
  text         text not null,
  kind         text not null check (kind in ('expr', 'predicate', 'manual')),
  expr         jsonb null,
  catalyst_id  uuid null references macro_core.catalyst (id),
  required     boolean not null default false,
  void_at      timestamptz null,
  void_reason  text null,
  unique (scenario_id, ordinal)
);

comment on column macro_core.trigger_clause.required is
  'True means the clause must hold. False means it counts toward the scenario''s n_of threshold.';
comment on column macro_core.trigger_clause.void_at is
  'Set when the Catalyst Monitor retires or reschedules the named catalyst out of the scenario''s horizon. A void clause is kept and counted in the denominator. It is never removed and the threshold is never renormalised.';

alter table macro_core.trigger_clause
  drop constraint if exists ck_macro_trigger_clause_expr_present;
alter table macro_core.trigger_clause
  add constraint ck_macro_trigger_clause_expr_present
  check ((kind = 'manual' and expr is null) or (kind in ('expr', 'predicate') and expr is not null));

alter table macro_core.trigger_clause
  drop constraint if exists ck_macro_trigger_clause_void_reason;
alter table macro_core.trigger_clause
  add constraint ck_macro_trigger_clause_void_reason
  check ((void_at is null and void_reason is null) or (void_at is not null and void_reason is not null));

create index if not exists idx_macro_trigger_clause_scenario
  on macro_core.trigger_clause (scenario_id);

create index if not exists idx_macro_trigger_clause_catalyst
  on macro_core.trigger_clause (catalyst_id)
  where catalyst_id is not null;

-- These are what "targets hit x of n" counts, and n is the row count.
-- Not every target is a band: a leadership statement counts toward the same n.
create table if not exists macro_core.scenario_target (
  id           uuid primary key default gen_random_uuid(),
  scenario_id  uuid not null references macro_core.scenario (id) on delete cascade,
  series_id    uuid null references macro_core.series (id),
  label        text not null,
  kind         text not null check (kind in ('band', 'manual')),
  comparator   text null check (comparator is null or comparator in ('between', 'gt', 'lt')),
  lo           numeric null,
  hi           numeric null,
  text         text null
);

-- A manual target has a null series_id, is never auto-scored, and is presented for
-- judgment at grading. A band must name the series it is measured against, and must
-- carry the bounds its comparator needs.
alter table macro_core.scenario_target
  drop constraint if exists ck_macro_scenario_target_shape;
alter table macro_core.scenario_target
  add constraint ck_macro_scenario_target_shape
  check (
    (kind = 'manual' and series_id is null and comparator is null and lo is null and hi is null)
    or (kind = 'band' and series_id is not null and comparator = 'between' and lo is not null and hi is not null)
    or (kind = 'band' and series_id is not null and comparator = 'gt' and lo is not null and hi is null)
    or (kind = 'band' and series_id is not null and comparator = 'lt' and hi is not null and lo is null)
  );

create index if not exists idx_macro_scenario_target_scenario
  on macro_core.scenario_target (scenario_id);

create index if not exists idx_macro_scenario_target_series
  on macro_core.scenario_target (series_id)
  where series_id is not null;

-- ============================================================
-- Setups and strategy
-- ============================================================

create table if not exists macro_core.setup (
  id                 uuid primary key default gen_random_uuid(),
  week               text not null,
  asset              text not null check (asset in ('equities', 'crypto')),
  name               text not null,
  style              text null,
  entry_lo           numeric not null,
  entry_hi           numeric not null,
  invalidation       numeric not null,
  invalidation_dir   text not null check (invalidation_dir in ('above', 'below')),
  target             numeric not null,
  rr_planned         numeric null,
  gate_expr          jsonb null,
  gate_text          text null,
  sector_focus_id    uuid null,
  rationale          text null,
  status             text not null check (status in ('proposed', 'active', 'superseded')),
  authored_at        timestamptz not null default now(),
  activated_at       timestamptz null,
  superseded_by      uuid null references macro_core.setup (id),
  amends             uuid null references macro_core.setup (id),
  amend_reason       text null,
  check (entry_lo <= entry_hi)
);

-- A setup's natural key inside a week is (week, asset, name); the contract does not
-- state one, and without it a replayed authoring run duplicates the row.
create unique index if not exists uq_macro_setup_active_week_asset_name
  on macro_core.setup (week, asset, name)
  where status = 'active';

create index if not exists idx_macro_setup_week
  on macro_core.setup (week);

create index if not exists idx_macro_setup_superseded_by
  on macro_core.setup (superseded_by)
  where superseded_by is not null;

create table if not exists macro_core.strategy (
  id                     uuid primary key default gen_random_uuid(),
  week                   text not null,
  asset                  text not null check (asset in ('equities', 'crypto')),
  stance                 text not null,
  posture                text not null,
  exposure_lo            numeric null,
  exposure_hi            numeric null,
  exposure_operated_lo   numeric null,
  exposure_operated_hi   numeric null,
  status                 text not null check (status in ('proposed', 'active', 'superseded')),
  authored_at            timestamptz not null default now(),
  activated_at           timestamptz null,
  superseded_by          uuid null references macro_core.strategy (id),
  amends                 uuid null references macro_core.strategy (id),
  amend_reason           text null
);

-- At most one active strategy per (week, asset). Per (week) alone is not available:
-- macro_core.strategy carries an asset, and docs/CONTRACT.md requires an active
-- strategy per book, so a week has two of them (equities and crypto).
create unique index if not exists uq_macro_strategy_active_week_asset
  on macro_core.strategy (week, asset)
  where status = 'active';

create index if not exists idx_macro_strategy_superseded_by
  on macro_core.strategy (superseded_by)
  where superseded_by is not null;

create table if not exists macro_core.sector_focus (
  id           uuid primary key default gen_random_uuid(),
  strategy_id  uuid not null references macro_core.strategy (id) on delete cascade,
  band         text not null check (band in ('CRITICAL', 'HOLD', 'WATCH', 'AVOID')),
  text         text not null,
  unique (strategy_id, band)
);

create table if not exists macro_core.holdings_check (
  id               uuid primary key default gen_random_uuid(),
  strategy_id      uuid not null references macro_core.strategy (id) on delete cascade,
  ticker           text not null,
  action           text not null check (action in ('keep', 'trim', 'add', 'exit')),
  text             text null,
  sector_focus_id  uuid null references macro_core.sector_focus (id),
  unique (strategy_id, ticker)
);

comment on table macro_core.holdings_check is
  'Every row in macro_core.holding where active must have a row here for the active strategy of its book. That is a macro_quality finding, not a constraint: holding is parsed from the Watchlist on every run and strategy is authored once a week, so a holding added on a Wednesday would otherwise fail the parse rather than raise a finding.';

-- ============================================================
-- Scenario rules
-- ============================================================

-- Numbers are never reused. A retired rule is kept with its retired_week set.
create table if not exists macro_core.scenario_rule (
  number        int primary key,
  text          text not null,
  retired_week  text null
);

create table if not exists macro_core.scenario_rule_week (
  rule_number  int not null references macro_core.scenario_rule (number),
  week         text not null,
  primary key (rule_number, week)
);

comment on table macro_core.scenario_rule_week is
  'A rule''s teaching weeks. A real foreign key onto graded weeks (see 0004), so the system can state how many times a rule has fired.';

-- macro_core.setup is declared before macro_core.sector_focus, so its reference to the
-- sector-focus row it sits under is added here rather than inline. A setup names that row
-- by key and not by string, the same way macro_core.holdings_check does: a sector focus is
-- re-authored every week, and a string would keep pointing at wording that no longer exists.
alter table macro_core.setup
  drop constraint if exists macro_setup_sector_focus_fk;
alter table macro_core.setup
  add constraint macro_setup_sector_focus_fk
  foreign key (sector_focus_id) references macro_core.sector_focus (id) on delete set null;

-- A condition of kind 'manual' has no expression, so its status can never be computed.
-- That is true of every migrated week, whose trackers were written as prose, and it stays
-- true going forward for any condition the operator judges rather than measures.
-- macro_mart.condition_state is defined as recomputable, so an asserted status cannot live
-- there: it would be destroyed by the next rebuild. It is a judgment, and judgments live in
-- macro_core.
create table if not exists macro_core.condition_state_asserted (
  condition_id  uuid primary key,
  owner_kind    text not null check (owner_kind in ('phase', 'trigger')),
  status        text not null check (status in ('met', 'not yet', 'failed', 'unevaluable', 'void')),
  current_text  text null,
  asserted_at   timestamptz not null default now(),
  asserted_by   text not null check (asserted_by in ('migration', 'authoring', 'operator'))
);

create index if not exists idx_macro_condition_state_asserted_owner
  on macro_core.condition_state_asserted (owner_kind);

-- A setup's entry, invalidation and target are levels on a specific series, and nothing
-- else in the row says which. Equity setups are quoted against the cash index and crypto
-- setups against BTC or ETH, so the mapping cannot be inferred from `asset` alone.
-- Deriving it from the setup's name works until it does not: a Nasdaq setup measured
-- against the S&P would produce an entry state, an R-multiple and a Setup Ledger row that
-- are all wrong and none of them visibly so.
alter table macro_core.setup
  add column if not exists series_id uuid null;
alter table macro_core.setup
  drop constraint if exists macro_setup_series_fk;
alter table macro_core.setup
  add constraint macro_setup_series_fk
  foreign key (series_id) references macro_core.series (id);
