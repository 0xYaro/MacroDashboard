-- Load order. Run against the macro_core schema after
-- supabase/migrations has created it and config/series.yaml has been
-- seeded into macro_core.series.
\i 10_judgment.sql
\i 20_week_grade.sql
\i 30_scenario_rule.sql
\i 40_setup.sql
\i 50_observation.sql
\i 60_condition_state.sql
