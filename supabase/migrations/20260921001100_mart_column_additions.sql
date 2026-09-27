-- 20260921001100_mart_column_additions.sql
-- Widens the worker-populated macro_mart tables and macro_quality.gap to the column
-- lists docs/CONTRACT.md now carries, after the web app's routes were checked against
-- them. These are all additive: no column is dropped, renamed or retyped, so a worker
-- written against the 0006 shape keeps working and simply leaves the new columns null.
--
-- ADD COLUMN appends, so the physical column order here does not match the order the
-- contract prints. Column order is not part of the contract: every read is by name,
-- through PostgREST or through a named select.
--
-- Grants need no repeat. macro_mart and macro_quality are granted SELECT at the table
-- level in 0009, which covers every column a table later acquires.
--
-- Re-runnable: ADD COLUMN IF NOT EXISTS, and the CHECKs are dropped before they are added.

-- ============================================================
-- macro_mart.snapshot_row
-- ============================================================
-- The page has to be able to say which of the two daily runs a reading came from, which
-- is the whole reason observed_at exists, and it has to name the cadence a delta was
-- taken over without a second lookup into macro_core.series.

alter table macro_mart.snapshot_row add column if not exists cadence     text null;
alter table macro_mart.snapshot_row add column if not exists observed_at timestamptz null;

alter table macro_mart.snapshot_row drop constraint if exists ck_macro_snapshot_row_cadence;
alter table macro_mart.snapshot_row add constraint ck_macro_snapshot_row_cadence
  check (cadence is null or cadence in ('session', 'daily', 'weekly', 'monthly', 'event'));

comment on column macro_mart.snapshot_row.cadence is
  'Carried from macro_core.series, not looked up. The cadence delta_native was taken over.';
comment on column macro_mart.snapshot_row.observed_at is
  'Carried from macro_core.observation. Which of the two daily runs this reading came from.';

-- ============================================================
-- macro_mart.condition_state
-- ============================================================
-- A condition of kind expr or predicate is computed on every run. A condition of kind
-- manual can never be computed, so its status is read from
-- macro_core.condition_state_asserted and carried through with source = 'asserted'.
-- Without this column an asserted judgment and a machine evaluation are the same row
-- shape, and the condition-coverage check cannot tell them apart.

alter table macro_mart.condition_state add column if not exists source text null;

alter table macro_mart.condition_state drop constraint if exists ck_macro_condition_state_source;
alter table macro_mart.condition_state add constraint ck_macro_condition_state_source
  check (source is null or source in ('computed', 'asserted'));

comment on column macro_mart.condition_state.source is
  'computed for an expr or predicate condition; asserted for a manual one, whose status is read from macro_core.condition_state_asserted. The asserted status lives in macro_core and not here because macro_mart is rebuilt, and a judgment a rebuild destroys is not a record of anything.';

-- ============================================================
-- macro_mart.scenario_state
-- ============================================================
-- Two denominators, because they answer different questions and conflating them is what
-- produced the W36 defect. bands_total counts only kind = 'band' targets, which is what
-- can be scored automatically. targets_total counts every target including manual ones,
-- and it is the n that "targets hit x of n" uses at grading. Both are rendered and both
-- are labelled, so both are stored.

alter table macro_mart.scenario_state add column if not exists positioning      text null;
alter table macro_mart.scenario_state add column if not exists targets_holding  int null;
alter table macro_mart.scenario_state add column if not exists targets_total    int null;

alter table macro_mart.scenario_state drop constraint if exists ck_macro_scenario_state_targets;
alter table macro_mart.scenario_state add constraint ck_macro_scenario_state_targets
  check (
    (targets_holding is null and targets_total is null)
    or (targets_holding is not null and targets_total is not null
        and targets_holding >= 0 and targets_holding <= targets_total
        and targets_total >= bands_total)
  );

comment on column macro_mart.scenario_state.targets_total is
  'Every target, band and manual alike. The n that "targets hit x of n" uses at grading. Never smaller than bands_total, which counts only the automatically scorable ones.';

-- ============================================================
-- macro_mart.scenario_target_state
-- ============================================================
-- A manual target carries null lo, hi, current and holding, and is presented for
-- judgment. Without kind on the row, that is indistinguishable from a band whose series
-- had no observation, which is the unevaluable case and means something else entirely.

alter table macro_mart.scenario_target_state add column if not exists kind text null;

alter table macro_mart.scenario_target_state drop constraint if exists ck_macro_scenario_target_state_kind;
alter table macro_mart.scenario_target_state add constraint ck_macro_scenario_target_state_kind
  check (kind is null or kind in ('band', 'manual'));

comment on column macro_mart.scenario_target_state.distance_to_breach is
  'An unsigned magnitude in the series'' own canonical unit, never a fraction and never a percentage. One canonical unit per series applies here too.';

-- ============================================================
-- macro_mart.setup_state
-- ============================================================
-- sector_focus_label is the text of the sector-focus row the setup sits under, resolved
-- through macro_core.setup.sector_focus_id. The setup carries the row by key rather than
-- by string, and macro_core is not on the Data API, so the label has to be carried here
-- or the Setups view cannot show what the row it references says.

alter table macro_mart.setup_state add column if not exists invalidation_dir    text null;
alter table macro_mart.setup_state add column if not exists gate_text           text null;
alter table macro_mart.setup_state add column if not exists sector_focus_label  text null;

alter table macro_mart.setup_state drop constraint if exists ck_macro_setup_state_invalidation_dir;
alter table macro_mart.setup_state add constraint ck_macro_setup_state_invalidation_dir
  check (invalidation_dir is null or invalidation_dir in ('above', 'below'));

comment on column macro_mart.setup_state.gate_text is
  'The gate as written. Rendered beside gate_state so a refusal can be read without decoding gate_expr.';

-- ============================================================
-- macro_quality.gap
-- ============================================================
-- Denormalised on purpose: a gap that cannot be named without a second lookup gets
-- rendered as a number, and the second lookup would be into macro_core, which is not on
-- the Data API at all.

alter table macro_quality.gap add column if not exists series_key text null;

comment on column macro_quality.gap.series_key is
  'Denormalised from macro_core.series.key. A gap that cannot be named without a second lookup gets rendered as a number, and that lookup would be into macro_core, which is not exposed.';

create index if not exists idx_macro_gap_series_key
  on macro_quality.gap (series_key);
