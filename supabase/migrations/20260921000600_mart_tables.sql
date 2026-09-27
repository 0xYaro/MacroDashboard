-- 20260921000600_mart_tables.sql
-- macro_mart, per docs/CONTRACT.md.
--
-- Every table in this file is derived and recomputable by re-running the worker's
-- evaluate step over macro_core. The two history views in 0007 are the stated
-- exception and must never be dropped and rebuilt the way these are.
--
-- Re-runnable: create table/index use IF NOT EXISTS.

-- ============================================================
-- Snapshot
-- ============================================================

create table if not exists macro_mart.snapshot_row (
  series_id           uuid primary key references macro_core.series (id),
  key                 text not null,
  label               text not null,
  table_group         text not null,
  ordinal             int not null,
  value               numeric null,
  display             text null,
  unit                text null,
  value_kind          text not null check (value_kind in ('numeric', 'text')),
  delta_native        numeric null,
  delta_native_label  text null,
  delta_week_open     numeric null,
  anchor_asof         date null,
  tier                int null check (tier is null or tier between 1 and 4),
  source              text null,
  asof                date null,
  released            date null,
  next_expected       date null,
  freshness           text not null check (freshness in ('fresh', 'due', 'overdue', 'gap', 'asserted', 'not_connected', 'failed')),
  note                text null,
  extra               jsonb not null default '{}'::jsonb,
  updated_at          timestamptz not null default now()
);

comment on column macro_mart.snapshot_row.delta_native is
  'The change over the series'' own cadence. Never a one-day delta on a weekly series.';
comment on column macro_mart.snapshot_row.delta_native_label is
  'Names the cadence the delta was taken over: 1d, WoW, MoM.';
comment on column macro_mart.snapshot_row.delta_week_open is
  'Measured against anchor_asof, the observation this series carried at the active scenario set''s authoring as-of. Not a calendar Monday, and not each series'' own prior week.';
comment on column macro_mart.snapshot_row.anchor_asof is
  'One anchor for the whole snapshot, carried on the row so the reader can see what the week-over-week column was measured from.';
comment on column macro_mart.snapshot_row.freshness is
  'due, not overdue, between observations of a weekly or monthly series.';

-- The snapshot is read whole, in journal order.
create index if not exists idx_macro_snapshot_row_group_ordinal
  on macro_mart.snapshot_row (table_group, ordinal);

create unique index if not exists uq_macro_snapshot_row_key
  on macro_mart.snapshot_row (key);

-- ============================================================
-- Conditions
-- ============================================================

-- condition_id is the phase_condition.id or the trigger_clause.id the row scores.
-- No foreign key: the two owners are different tables and the column holds either.
create table if not exists macro_mart.condition_state (
  condition_id     uuid primary key,
  owner_kind       text not null check (owner_kind in ('phase', 'trigger')),
  owner_id         uuid not null,
  ordinal          int not null,
  text             text not null,
  kind             text not null check (kind in ('expr', 'predicate', 'manual')),
  status           text not null check (status in ('met', 'not yet', 'failed', 'unevaluable', 'void')),
  current_display  text null,
  evidence         jsonb not null default '{}'::jsonb,
  evaluated_at     timestamptz not null default now()
);

comment on column macro_mart.condition_state.status is
  'A condition that cannot be evaluated is unevaluable, never not yet.';

-- The access pattern: every condition belonging to one phase call or one scenario.
create index if not exists idx_macro_condition_state_owner
  on macro_mart.condition_state (owner_kind, owner_id, ordinal);

-- ============================================================
-- Phase
-- ============================================================

create table if not exists macro_mart.phase_score (
  asset             text primary key check (asset in ('equities', 'crypto')),
  phase_call_id     uuid not null,
  phase             int not null,
  phase_name        text not null,
  met               int not null,
  of                int not null,
  machine_checked   int not null,
  manual            int not null,
  unevaluable       int not null,
  upgrade_eligible  boolean not null,
  evaluated_at      timestamptz not null default now(),
  constraint ck_macro_phase_score_upgrade
    check (upgrade_eligible = false or (met >= 3 and unevaluable = 0))
);

comment on constraint ck_macro_phase_score_upgrade on macro_mart.phase_score is
  'upgrade_eligible is false whenever met < 3, without exception, and false whenever any condition is unevaluable.';

-- ============================================================
-- Scenarios
-- ============================================================

create table if not exists macro_mart.scenario_state (
  scenario_id     uuid primary key,
  set_id          uuid not null,
  ordinal         int not null,
  title           text not null,
  probability     int not null,
  clauses_fired   int not null,
  clauses_total   int not null,
  clauses_void    int not null,
  required_unmet  int not null,
  bands_holding   int not null,
  bands_total     int not null,
  premise_moved   boolean not null default false,
  evaluated_at    timestamptz not null default now()
);

create index if not exists idx_macro_scenario_state_set
  on macro_mart.scenario_state (set_id, ordinal);

create table if not exists macro_mart.scenario_target_state (
  target_id            uuid primary key,
  scenario_id          uuid not null,
  series_id            uuid null,
  label                text not null,
  lo                   numeric null,
  hi                   numeric null,
  current              numeric null,
  holding              boolean null,
  distance_to_breach   numeric null,
  evaluated_at         timestamptz not null default now()
);

comment on column macro_mart.scenario_target_state.holding is
  'Null means unevaluable.';

create index if not exists idx_macro_scenario_target_state_scenario
  on macro_mart.scenario_target_state (scenario_id);

-- ============================================================
-- Setups
-- ============================================================

create table if not exists macro_mart.setup_state (
  setup_id             uuid primary key,
  week                 text not null,
  asset                text not null,
  name                 text not null,
  entry_lo             numeric not null,
  entry_hi             numeric not null,
  invalidation         numeric not null,
  target               numeric not null,
  rr_planned           numeric null,
  gate_state           text not null check (gate_state in ('open', 'blocked', 'unevaluable')),
  gate_reason          text null,
  band_touched_today   boolean not null default false,
  band_touched_close   boolean not null default false,
  entry_state          text not null check (entry_state in ('not triggered', 'open', 'stopped', 'target')),
  sessions_carried     int not null default 0,
  r_running            numeric null,
  evaluated_at         timestamptz not null default now()
);

comment on column macro_mart.setup_state.band_touched_today is
  'Derived from the session high and low in macro_core.observation.extra. "Entry band reached, gate refused, trade forgone" is the failure that has occurred three times, and it surfaces the same day.';

create index if not exists idx_macro_setup_state_week_asset
  on macro_mart.setup_state (week, asset);

-- ============================================================
-- Calibration
-- ============================================================

-- Single row, id = 1. The sentence section 0 currently pastes from calibration.py.
create table if not exists macro_mart.calibration (
  id                 int primary key check (id = 1),
  graded_weeks       int null,
  base_hit_score     numeric null,
  base_materialized  int null,
  mean_brier         numeric null,
  brier_weeks        int null,
  base_was_s2        int null,
  "window"           int null,
  computed_at        timestamptz null
);

-- ============================================================
-- Catalysts
-- ============================================================

-- The next thirty days, with which trigger clauses depend on each one.
create table if not exists macro_mart.catalyst_window (
  catalyst_id     uuid primary key references macro_core.catalyst (id),
  name            text not null,
  starts_at       timestamptz null,
  date_precision  text not null,
  affected        text[] not null default '{}',
  days_out        int null,
  referenced_by   uuid[] not null default '{}',
  source_url      text null
);

create index if not exists idx_macro_catalyst_window_starts_at
  on macro_mart.catalyst_window (starts_at);
