-- 20260921000400_core_grades.sql
-- The graded-outcome half of macro_core, per docs/CONTRACT.md.
-- Re-runnable: create table/index use IF NOT EXISTS, the ALTERs are guarded.

-- The Scorecard Ledger, one row per graded week.
create table if not exists macro_core.week_grade (
  week                 text primary key,
  base_scenario_id     uuid null references macro_core.scenario (id),
  base_scenario_label  text not null,
  base_grade           text not null check (base_grade in ('Materialized', 'Partial', 'Not')),
  other_grades         jsonb not null default '[]'::jsonb,
  eq_call              text null,
  crypto_call          text null,
  key_miss             text null,
  targets_hit          int null,
  targets_n            int null,
  brier                numeric null,
  premise_moved        boolean not null default false,
  -- Nullable on purpose. Nine migrated weeks were graded by archive journals this system
  -- deliberately does not read, so their grading moment is unknown. A derived Monday would
  -- be a manufactured fact, and null, never zero applies to a timestamp as much as a figure.
  graded_at            timestamptz null
);

comment on column macro_core.week_grade.base_scenario_id is
  'Null for the weeks migrated from the markdown ledger, whose scenario sets were never imported.';
comment on column macro_core.week_grade.base_scenario_label is
  'Always carries the title and probability as written, so the calibration read works over migrated and native rows alike.';
comment on table macro_core.week_grade is
  'Hit-rate convention: Materialized 1, Partial 0.5, Not 0, over up to the last eight rows. Brier: outcome weights normalised to sum to 1, then the sum over the three scenarios of (p - o) squared.';

alter table macro_core.week_grade
  drop constraint if exists ck_macro_week_grade_targets;
alter table macro_core.week_grade
  add constraint ck_macro_week_grade_targets
  check (
    (targets_hit is null and targets_n is null)
    or (targets_hit is not null and targets_n is not null and targets_hit >= 0 and targets_hit <= targets_n)
  );

create index if not exists idx_macro_week_grade_graded_at
  on macro_core.week_grade (graded_at desc);

-- The Setup Ledger. An open row is rewritten the following week, never duplicated.
create table if not exists macro_core.setup_grade (
  setup_id      uuid primary key references macro_core.setup (id),
  graded_week   text not null,
  outcome       text not null check (outcome in ('not triggered', 'triggered → stopped', 'triggered → target', 'open')),
  r_realised    numeric null,
  note          text null
);

-- The outcome vocabulary is verbatim from docs/CONTRACT.md and the Setup Ledger,
-- including the U+2192 arrow. This file is UTF-8; do not retype those two literals
-- through an editor that will substitute an ASCII '->'.

create index if not exists idx_macro_setup_grade_graded_week
  on macro_core.setup_grade (graded_week);

create index if not exists idx_macro_setup_grade_open
  on macro_core.setup_grade (graded_week)
  where outcome = 'open';

-- A rule's teaching weeks are a real foreign key onto graded weeks (docs/DECISIONS.md,
-- 2026-09-21 "Migrated history"), so the system can state how many times a rule fired.
-- The FK lives here rather than in 0003 because macro_core.week_grade is created above.
do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conname = 'fk_macro_scenario_rule_week_week'
      and conrelid = 'macro_core.scenario_rule_week'::regclass
  ) then
    alter table macro_core.scenario_rule_week
      add constraint fk_macro_scenario_rule_week_week
      foreign key (week) references macro_core.week_grade (week);
  end if;
end $$;
