-- 20260921001000_seed.sql
-- Seed data. Every write is an idempotent upsert on a natural key (docs/CONTRACT.md doctrine).
--
-- macro_core.series is NOT seeded here. docs/CONTRACT.md fixes it as "asserted reference
-- data, seeded from config/series.yaml", so config/series.yaml is its one source and the
-- worker applies it. Writing the same 47 rows into SQL as well would create a second
-- place for a unit or a release rule to be wrong, which is exactly the failure the
-- "one canonical unit per series" rule exists to prevent.
--
-- macro_core.scenario_rule (R1 to R12) is not seeded here either: docs/DECISIONS.md
-- migrates the rules with their teaching weeks, and a teaching week is a foreign key
-- onto macro_core.week_grade, which the migration run populates first.
--
-- macro_mart.calibration has exactly one row, id = 1. It is seeded with every measured
-- column null; the worker fills them in on the Monday run after grading.

insert into macro_mart.calibration (
  id,
  graded_weeks,
  base_hit_score,
  base_materialized,
  mean_brier,
  brier_weeks,
  base_was_s2,
  "window",
  computed_at
)
values (1, null, null, null, null, null, null, 8, null)
on conflict (id) do nothing;

comment on column macro_mart.calibration."window" is
  'The calibration read is defined over the last eight graded weeks (docs/DECISIONS.md). Seeded at 8; the worker restates it on every recompute.';
