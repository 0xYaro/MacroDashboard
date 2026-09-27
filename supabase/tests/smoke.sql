-- smoke.sql
-- NOT part of the migration set. Applied against a throwaway local Postgres after
-- local_shim.sql and every migration, to check that the constraints docs/CONTRACT.md
-- names actually bite. It leaves no rows behind: every case runs inside a plpgsql
-- block whose exception handler rolls its inserts back, and the two happy paths are
-- deleted at the end.
--
-- The deferred constraint triggers fire at COMMIT, which a plpgsql exception frame
-- cannot see, so each negative case forces them early with SET CONSTRAINTS ALL
-- IMMEDIATE and catches the check_violation there.
--
--   psql -d macro_shim_test -f supabase/tests/smoke.sql

\set ON_ERROR_STOP on

-- A distinct errcode for "the constraint did not fire", so the catch-all handler in
-- each block cannot swallow the failure it is meant to report.
create or replace function pg_temp.expect_failed(what text)
returns void language plpgsql as $$
begin
  raise exception 'NOT ENFORCED: %', what using errcode = 'triggered_action_exception';
end $$;

-- ============================================================
-- 1. A scenario set has exactly three scenarios summing to 100
-- ============================================================

do $$
declare s uuid;
begin
  insert into macro_core.scenario_set (week, status, activated_at)
    values ('2026-W39', 'active', now()) returning id into s;
  insert into macro_core.scenario (set_id, ordinal, title, probability) values
    (s, 1, 'S1', 45), (s, 2, 'S2', 35), (s, 3, 'S3', 20);
  set constraints all immediate;
  raise notice 'PASS 1a: a 45/35/20 set commits';
  delete from macro_core.scenario_set where id = s;
  set constraints all immediate;
end $$;

do $$
declare s uuid;
begin
  insert into macro_core.scenario_set (week, status, activated_at)
    values ('2026-W39', 'active', now()) returning id into s;
  insert into macro_core.scenario (set_id, ordinal, title, probability) values
    (s, 1, 'S1', 45), (s, 2, 'S2', 35), (s, 3, 'S3', 25);
  set constraints all immediate;
  perform pg_temp.expect_failed('probabilities summing to 105');
exception when check_violation then
  raise notice 'PASS 1b: %', sqlerrm;
end $$;

do $$
declare s uuid;
begin
  insert into macro_core.scenario_set (week, status, activated_at)
    values ('2026-W39', 'active', now()) returning id into s;
  insert into macro_core.scenario (set_id, ordinal, title, probability) values
    (s, 1, 'S1', 60), (s, 2, 'S2', 40);
  set constraints all immediate;
  perform pg_temp.expect_failed('a two-scenario set');
exception when check_violation then
  raise notice 'PASS 1c: %', sqlerrm;
end $$;

do $$
begin
  insert into macro_core.scenario_set (week, status, activated_at)
    values ('2026-W39', 'active', now());
  set constraints all immediate;
  perform pg_temp.expect_failed('a set with no scenarios at all');
exception when check_violation then
  raise notice 'PASS 1d: %', sqlerrm;
end $$;

-- ============================================================
-- 2. A phase call has exactly four conditions
-- ============================================================

do $$
declare p uuid;
begin
  insert into macro_core.phase_call
    (asset, week, phase, phase_name, direction, reason, falsifier, status, activated_at)
    values ('equities', '2026-W39', 3, 'Distribution', 'unchanged', 'r', 'f', 'active', now())
    returning id into p;
  insert into macro_core.phase_condition (phase_call_id, ordinal, text, kind, expr) values
    (p, 1, 'HY above 300 bps', 'expr', '{"op":"gt","series":"hy_oas_bps","value":300}'),
    (p, 2, 'Breadth under 45', 'expr', '{"op":"lt","series":"breadth_spx_above_200dma","value":45}'),
    (p, 3, 'Failed retest of 7450', 'predicate', '{"predicate":"failed_retest","args":{"series":"spx_cash","level":7450,"within_sessions":5}}'),
    (p, 4, 'Rallies fail at prior support', 'manual', null);
  set constraints all immediate;
  raise notice 'PASS 2a: a four-condition phase call commits';
  delete from macro_core.phase_call where id = p;
  set constraints all immediate;
end $$;

do $$
declare p uuid;
begin
  insert into macro_core.phase_call
    (asset, week, phase, phase_name, direction, reason, falsifier, status, activated_at)
    values ('equities', '2026-W39', 3, 'Distribution', 'unchanged', 'r', 'f', 'active', now())
    returning id into p;
  insert into macro_core.phase_condition (phase_call_id, ordinal, text, kind, expr) values
    (p, 1, 'a', 'manual', null), (p, 2, 'b', 'manual', null), (p, 3, 'c', 'manual', null);
  set constraints all immediate;
  perform pg_temp.expect_failed('a three-condition phase call');
exception when check_violation then
  raise notice 'PASS 2b: %', sqlerrm;
end $$;

do $$
begin
  insert into macro_core.phase_call
    (asset, week, phase, phase_name, direction, reason, falsifier, status, activated_at)
    values ('crypto', '2026-W39', 2, 'Recovery', 'up', 'r', 'f', 'active', now());
  set constraints all immediate;
  perform pg_temp.expect_failed('a phase call with no conditions at all');
exception when check_violation then
  raise notice 'PASS 2c: %', sqlerrm;
end $$;

-- A manual condition may not carry an expression, and an expr one must.
do $$
declare p uuid;
begin
  insert into macro_core.phase_call
    (asset, week, phase, phase_name, direction, reason, falsifier, status, activated_at)
    values ('equities', '2026-W39', 3, 'x', 'unchanged', 'r', 'f', 'active', now())
    returning id into p;
  insert into macro_core.phase_condition (phase_call_id, ordinal, text, kind, expr)
    values (p, 1, 'a', 'expr', null);
  set constraints all immediate;
  perform pg_temp.expect_failed('an expr condition with a null expr');
exception when check_violation then
  raise notice 'PASS 2d: %', sqlerrm;
end $$;

-- ============================================================
-- 3. At most one active row
-- ============================================================

do $$
begin
  insert into macro_core.phase_call
    (asset, week, phase, phase_name, direction, reason, falsifier, status, activated_at)
  values
    ('equities', '2026-W38', 3, 'x', 'unchanged', 'r', 'f', 'active', now()),
    ('equities', '2026-W39', 3, 'y', 'unchanged', 'r', 'f', 'active', now());
  perform pg_temp.expect_failed('two active equities phase calls');
exception when unique_violation then
  raise notice 'PASS 3a: at most one active phase call per asset';
end $$;

do $$
begin
  insert into macro_core.scenario_set (week, status, activated_at)
  values ('2026-W39', 'active', now()), ('2026-W39', 'active', now());
  perform pg_temp.expect_failed('two active scenario sets in one week');
exception when unique_violation then
  raise notice 'PASS 3b: at most one active scenario set per week';
end $$;

do $$
begin
  insert into macro_core.strategy (week, asset, stance, posture, status, activated_at)
  values ('2026-W39', 'equities', 'Selective', 'Hold', 'active', now()),
         ('2026-W39', 'crypto',   'Selective', 'Hold', 'active', now());
  raise notice 'PASS 3c: a week carries an active strategy per book';
  delete from macro_core.strategy where week = '2026-W39';
end $$;

do $$
begin
  insert into macro_core.strategy (week, asset, stance, posture, status, activated_at)
  values ('2026-W39', 'equities', 'Selective', 'Hold', 'active', now()),
         ('2026-W39', 'equities', 'Cash',      'Cash', 'active', now());
  perform pg_temp.expect_failed('two active equities strategies in one week');
exception when unique_violation then
  raise notice 'PASS 3d: at most one active strategy per week and book';
end $$;

-- ============================================================
-- 4. Lifecycle
-- ============================================================

do $$
begin
  insert into macro_core.scenario_set (week, status) values ('2026-W40', 'active');
  perform pg_temp.expect_failed('an active row with a null activated_at');
exception when check_violation then
  raise notice 'PASS 4a: an active row must carry activated_at';
end $$;

do $$
begin
  insert into macro_core.scenario_set (week, status, amends)
    values ('2026-W40', 'proposed', gen_random_uuid());
  perform pg_temp.expect_failed('an amendment with no amend_reason');
exception when check_violation or foreign_key_violation then
  raise notice 'PASS 4b: an amendment must carry its reason';
end $$;

-- ============================================================
-- 5. Scenario target shape
-- ============================================================

do $$
declare s uuid; sc uuid;
begin
  insert into macro_core.scenario_set (week, status, activated_at)
    values ('2026-W41', 'active', now()) returning id into s;
  insert into macro_core.scenario (set_id, ordinal, title, probability) values
    (s, 1, 'S1', 45), (s, 2, 'S2', 35), (s, 3, 'S3', 20);
  select id into sc from macro_core.scenario where set_id = s and ordinal = 1;
  -- A manual target carries no series and no bounds; it still counts toward n.
  insert into macro_core.scenario_target (scenario_id, label, kind, text)
    values (sc, 'Leadership: Nasdaq beats the Dow again', 'manual', 'judged at grading');
  set constraints all immediate;
  raise notice 'PASS 5a: a manual target with no series_id is accepted';
  delete from macro_core.scenario_set where id = s;
  set constraints all immediate;
end $$;

do $$
declare s uuid; sc uuid;
begin
  insert into macro_core.scenario_set (week, status, activated_at)
    values ('2026-W41', 'active', now()) returning id into s;
  insert into macro_core.scenario (set_id, ordinal, title, probability) values
    (s, 1, 'S1', 45), (s, 2, 'S2', 35), (s, 3, 'S3', 20);
  select id into sc from macro_core.scenario where set_id = s and ordinal = 1;
  insert into macro_core.scenario_target (scenario_id, label, kind, comparator, lo, hi)
    values (sc, 'S&P 7500-7800', 'band', 'between', 7500, null);
  perform pg_temp.expect_failed('a between band with no upper bound');
exception when check_violation then
  raise notice 'PASS 5b: %', sqlerrm;
end $$;

-- ============================================================
-- 6. read_stale is raised only on evidence, and the counts add up
-- ============================================================

do $$
begin
  insert into macro_mart.phase_score
    (asset, phase_call_id, phase, phase_name, met, of, machine_checked, manual, unevaluable,
     failed, read_stale)
    values ('equities', gen_random_uuid(), 3, 'x', 3, 4, 4, 0, 0, 1, true);
  perform pg_temp.expect_failed('read_stale with one failed condition');
exception when check_violation then
  raise notice 'PASS 6a: read_stale needs two or more failed conditions';
end $$;

do $$
begin
  insert into macro_mart.phase_score
    (asset, phase_call_id, phase, phase_name, met, of, machine_checked, manual, unevaluable,
     failed, read_stale)
    values ('equities', gen_random_uuid(), 3, 'x', 3, 4, 4, 0, 1, 1, false);
  perform pg_temp.expect_failed('five conditions counted on a call of four');
exception when check_violation then
  raise notice 'PASS 6b: met, failed and unevaluable never add up to more than the call carries';
end $$;

do $$
begin
  insert into macro_core.phase_call
    (asset, week, phase, phase_name, direction, reason, falsifier, status, classification)
    values ('equities', '2026-W40', 3, 'x', 'unchanged', 'r', 'f', 'proposed', 'fresh');
  perform pg_temp.expect_failed('a fresh read with no classified_at');
exception when check_violation then
  raise notice 'PASS 6c: a fresh read carries the moment it was classified';
end $$;

-- ============================================================
-- 7. Grants, RLS and the history views
-- ============================================================

do $$
declare n int;
begin
  set local role authenticated;

  select count(*) into n from macro_mart.calibration;
  select count(*) into n from macro_mart.scenario_set_history;
  select count(*) into n from macro_mart.phase_call_history;
  select count(*) into n from macro_quality.finding;
  raise notice 'PASS 7a: authenticated reads macro_mart (tables and both history views) and macro_quality';

  reset role;
end $$;

do $$
declare n int;
begin
  set local role authenticated;
  select count(*) into n from macro_core.series;
  reset role;
  perform pg_temp.expect_failed('authenticated reading macro_core');
exception when insufficient_privilege then
  raise notice 'PASS 7b: authenticated cannot reach macro_core';
end $$;

do $$
declare n int;
begin
  set local role authenticated;
  select count(*) into n from macro_raw.batch;
  reset role;
  perform pg_temp.expect_failed('authenticated reading macro_raw');
exception when insufficient_privilege then
  raise notice 'PASS 7c: authenticated cannot reach macro_raw';
end $$;

do $$
declare n int;
begin
  set local role anon;
  select count(*) into n from macro_mart.calibration;
  reset role;
  perform pg_temp.expect_failed('anon reading macro_mart');
exception when insufficient_privilege then
  raise notice 'PASS 7d: anon cannot reach macro_mart';
end $$;

do $$
declare n int;
begin
  set local role anon;
  select count(*) into n from macro_quality.finding;
  reset role;
  perform pg_temp.expect_failed('anon reading macro_quality');
exception when insufficient_privilege then
  raise notice 'PASS 7e: anon cannot reach macro_quality';
end $$;

-- ============================================================
-- 8. A superseded judgment is still visible through the history view
-- ============================================================

do $$
declare old_id uuid; new_id uuid; n int;
begin
  insert into macro_core.phase_call
    (asset, week, phase, phase_name, direction, reason, falsifier, status, activated_at)
    values ('equities', '2026-W38', 3, 'Distribution', 'unchanged', 'r', 'f', 'active', '2026-09-14T00:00:00Z')
    returning id into old_id;
  insert into macro_core.phase_condition (phase_call_id, ordinal, text, kind) values
    (old_id, 1, 'a', 'manual'), (old_id, 2, 'b', 'manual'),
    (old_id, 3, 'c', 'manual'), (old_id, 4, 'd', 'manual');

  insert into macro_core.phase_call
    (asset, week, phase, phase_name, prior_phase, direction, reason, falsifier, status, activated_at, amends, amend_reason)
    values ('equities', '2026-W39', 4, 'Markdown', 3, 'down', 'r', 'f', 'proposed', null, old_id, 'Breadth broke 45')
    returning id into new_id;
  insert into macro_core.phase_condition (phase_call_id, ordinal, text, kind) values
    (new_id, 1, 'a', 'manual'), (new_id, 2, 'b', 'manual'),
    (new_id, 3, 'c', 'manual'), (new_id, 4, 'd', 'manual');

  -- The old row is stood down before the new one is stood up. A partial unique index
  -- cannot be deferred, so this order is not a preference: activating first would
  -- leave two active equities calls for the length of one statement and be rejected.
  update macro_core.phase_call set status = 'superseded', superseded_by = new_id where id = old_id;
  update macro_core.phase_call set status = 'active', activated_at = '2026-09-21T00:00:00Z' where id = new_id;
  set constraints all immediate;

  select count(*) into n from macro_mart.phase_call_history
   where id = old_id and valid_from = '2026-09-14T00:00:00Z' and valid_to = '2026-09-21T00:00:00Z';
  if n <> 1 then
    raise exception 'history view did not close the superseded interval at its successor''s activated_at';
  end if;

  select count(*) into n from macro_mart.phase_call_history where id = new_id and valid_to is null;
  if n <> 1 then
    raise exception 'history view closed an interval that still stands';
  end if;

  raise notice 'PASS 8: the superseded call keeps its interval and the standing one is open';

  update macro_core.phase_call set superseded_by = null where id = old_id;
  update macro_core.phase_call set amends = null, amend_reason = null where id = new_id;
  delete from macro_core.phase_call where id in (old_id, new_id);
  set constraints all immediate;
end $$;

-- ============================================================
-- 9. Accounting's four schemas are untouched
-- ============================================================
--
-- Conditional, because this file runs against two kinds of database: a bare one that
-- holds only this system, and one where the harness has planted a simulated Accounting
-- alongside. Asserting unconditionally would turn the bare case into a failure about
-- something this file is not checking there.

do $$
declare n int; planted boolean;
begin
  select count(*) = 4 into planted from information_schema.schemata
   where schema_name in ('raw', 'core', 'mart', 'quality');

  if not planted then
    raise notice 'SKIP 9: no Accounting schemas in this database, nothing to be untouched';
    return;
  end if;

  select count(*) into n from pg_policies
   where schemaname in ('raw', 'core', 'mart', 'quality') and policyname = 'authenticated_select';
  if n < 1 then
    raise exception 'an Accounting policy went missing';
  end if;

  set local role authenticated;
  select count(*) into n from mart.book_summary;
  reset role;
  raise notice 'PASS 9: Accounting''s raw/core/mart/quality grants and policies still stand';
end $$;


-- ============================================================
-- 10. The columns 0011 added, and the CHECKs that come with them
-- ============================================================

do $$
declare n int;
begin
  select count(*) into n from information_schema.columns
   where (table_schema, table_name, column_name) in (
     ('macro_mart', 'snapshot_row',          'cadence'),
     ('macro_mart', 'snapshot_row',          'observed_at'),
     ('macro_mart', 'condition_state',       'source'),
     ('macro_mart', 'scenario_state',        'positioning'),
     ('macro_mart', 'scenario_state',        'targets_holding'),
     ('macro_mart', 'scenario_state',        'targets_total'),
     ('macro_mart', 'scenario_target_state', 'kind'),
     ('macro_mart', 'setup_state',           'invalidation_dir'),
     ('macro_mart', 'setup_state',           'gate_text'),
     ('macro_mart', 'setup_state',           'sector_focus_label'),
     ('macro_quality', 'gap',                'series_key'));
  if n <> 11 then
    raise exception 'expected 11 added columns, found %', n;
  end if;
  raise notice 'PASS 10a: every column 0011 adds is present';
end $$;

do $$
begin
  insert into macro_mart.scenario_state
    (scenario_id, set_id, ordinal, title, probability, clauses_fired, clauses_total,
     clauses_void, required_unmet, bands_holding, bands_total, targets_holding, targets_total)
    values (gen_random_uuid(), gen_random_uuid(), 1, 'S1', 45, 1, 3, 0, 0, 7, 13, 8, 11);
  perform pg_temp.expect_failed('targets_total smaller than bands_total');
exception when check_violation then
  raise notice 'PASS 10b: targets_total can never be smaller than bands_total';
end $$;

do $$
begin
  insert into macro_mart.scenario_state
    (scenario_id, set_id, ordinal, title, probability, clauses_fired, clauses_total,
     clauses_void, required_unmet, bands_holding, bands_total, targets_holding, targets_total)
    values (gen_random_uuid(), gen_random_uuid(), 1, 'S1', 45, 1, 3, 0, 0, 7, 13, 8, 14);
  raise notice 'PASS 10c: 7 of 13 bands inside 8 of 14 targets is accepted';
  delete from macro_mart.scenario_state;
end $$;

do $$
begin
  insert into macro_mart.condition_state
    (condition_id, owner_kind, owner_id, ordinal, text, kind, status, source)
    values (gen_random_uuid(), 'phase', gen_random_uuid(), 1, 'a', 'manual', 'met', 'guessed');
  perform pg_temp.expect_failed('a condition source outside computed/asserted');
exception when check_violation then
  raise notice 'PASS 10d: condition_state.source is computed or asserted';
end $$;

-- ============================================================
-- 11. Every view 0012 adds resolves, returns, and is readable by authenticated only
-- ============================================================
--
-- One full week of judgment is written, every view is read back as `authenticated`, and
-- the fixture is removed. The point is not that the SQL parses: it is that each view
-- returns the row the route needs, with the child rows aggregated where the contract
-- says they are aggregated.

do $$
declare
  v_series   uuid;
  v_strategy uuid;
  v_focus    uuid;
  v_phase    uuid;
  v_set      uuid;
  v_scenario uuid;
  v_setup    uuid;
  n          int;
  j          jsonb;
  t          text;
begin
  -- Reference data and one observation.
  insert into macro_core.series
    (key, label, table_group, ordinal, value_kind, unit, decimals, cadence,
     release_rule, replayable, source_name, fetch_path, tier_floor)
    values ('smoke_spx_cash', 'S&P 500 (cash)', 'Price & Levels', 9001, 'numeric',
            'index_points', 2, 'session', 'us_session', true, 'smoke', 'deterministic', 1)
    returning id into v_series;

  insert into macro_core.observation
    (series_id, asof, value, display, tier, source, released, observed_at)
    values (v_series, date '2026-09-15', 7585.73, '7,585.73', 1, 'smoke',
            date '2026-09-15', '2026-09-15T20:00:00Z');

  -- Strategy, with the two child tables the Setups view needs aggregated.
  insert into macro_core.strategy
    (week, asset, stance, posture, exposure_lo, exposure_hi, status, activated_at)
    values ('2026-W39', 'equities', 'Selective', 'Hold', 25, 50, 'active', '2026-09-21T00:00:00Z')
    returning id into v_strategy;

  insert into macro_core.sector_focus (strategy_id, band, text) values
    (v_strategy, 'AVOID',    'Long-duration unprofitable tech'),
    (v_strategy, 'CRITICAL', 'Memory and AI infrastructure'),
    (v_strategy, 'WATCH',    'Energy on the Hormuz premium'),
    (v_strategy, 'HOLD',     'Defensive dividend payers');

  select id into v_focus from macro_core.sector_focus
   where strategy_id = v_strategy and band = 'CRITICAL';

  insert into macro_core.holdings_check (strategy_id, ticker, action, text, sector_focus_id)
    values (v_strategy, 'SMH', 'keep', 'Sits under CRITICAL', v_focus),
           (v_strategy, 'ARKK', 'exit', 'Sits under AVOID', null);

  -- Phase call and its four conditions.
  insert into macro_core.phase_call
    (asset, week, phase, phase_name, prior_phase, direction, reason, evidence, falsifier,
     status, activated_at)
    values ('equities', '2026-W39', 3, 'Distribution', 3, 'unchanged',
            'Breadth kept falling into a flat index',
            array['Breadth 49.50 against 56.26 on Monday', 'HY flat at 270 bps'],
            'Breadth repairs above 60 and HY holds under 285',
            'active', '2026-09-21T00:00:00Z')
    returning id into v_phase;

  insert into macro_core.phase_condition (phase_call_id, ordinal, text, kind, expr) values
    (v_phase, 1, 'Breadth above 60', 'expr',
       '{"op":"gt","series":"breadth_spx_above_200dma","value":60}'),
    (v_phase, 2, 'HY under 285 bps', 'expr', '{"op":"lt","series":"hy_oas_bps","value":285}'),
    (v_phase, 3, 'Failed retest of 7450', 'predicate',
       '{"predicate":"failed_retest","args":{"series":"spx_cash","level":7450,"within_sessions":5}}'),
    (v_phase, 4, 'Rallies fail at prior support', 'manual', null);

  -- The manual condition's status is asserted, not computed.
  insert into macro_core.condition_state_asserted
    (condition_id, owner_kind, status, current_text, asserted_by)
    select id, 'phase', 'not yet', 'Rallies have not been tested yet', 'authoring'
    from macro_core.phase_condition where phase_call_id = v_phase and ordinal = 4;

  -- Scenario set and its three scenarios.
  insert into macro_core.scenario_set
    (week, synthesis, probability_check, swing_variables, rules_waived, status, activated_at)
    values ('2026-W39', 'Breadth is the game.', 'Base deserves the same as last week.',
            '[{"name":"Oct FOMC","cool":"relief","hot":"repricing"}]'::jsonb,
            array[10], 'active', '2026-09-21T00:00:00Z')
    returning id into v_set;

  insert into macro_core.scenario (set_id, ordinal, title, probability, positioning) values
    (v_set, 1, 'Grind higher on narrow leadership', 45, 'Stay selective'),
    (v_set, 2, 'Breadth-led unwind', 35, 'Raise cash'),
    (v_set, 3, 'Credit cracks', 20, 'Hedge');

  select id into v_scenario from macro_core.scenario where set_id = v_set and ordinal = 1;

  -- Setup, pointing at a sector-focus row by key rather than by string.
  insert into macro_core.setup
    (week, asset, name, style, entry_lo, entry_hi, invalidation, invalidation_dir, target,
     rr_planned, gate_text, sector_focus_id, rationale, status, activated_at)
    values ('2026-W39', 'equities', 'S&P FOMC-shelf FTR long', 'FTR, HTF',
            7500, 7560, 7450, 'below', 7800, 3.4,
            'HY under 292 bps and breadth not below 45 on the entry close',
            v_focus, 'Credit-and-floor gate after three breadth weeks',
            'active', '2026-09-21T00:00:00Z')
    returning id into v_setup;

  -- Both ledgers, and a rule with a teaching week.
  insert into macro_core.week_grade
    (week, base_scenario_id, base_scenario_label, base_grade, other_grades,
     eq_call, crypto_call, key_miss, targets_hit, targets_n, brier, graded_at)
    values ('2026-W38', null, 'S2 (35%) "Breadth-led unwind"', 'Partial',
            '[{"id":"S1","prob":45,"grade":"Not","note":"no grind"}]'::jsonb,
            'valid', 'valid', 'Breadth gate was self-contradicting', 7, 11, 0.41, null);

  insert into macro_core.setup_grade (setup_id, graded_week, outcome, r_realised, note)
    values (v_setup, '2026-W40', 'open', null, 'Proposed W39');

  insert into macro_core.scenario_rule (number, text)
    values (9001, 'Do not write a rising-breadth gate on a falling-price entry.');
  insert into macro_core.scenario_rule_week (rule_number, week) values (9001, '2026-W38');

  set constraints all immediate;

  -- ---------------------------------------------------------
  -- Read every new view back as authenticated.
  -- ---------------------------------------------------------
  set local role authenticated;

  select count(*) into n from macro_mart.phase_call_detail where asset = 'equities';
  if n <> 1 then raise exception 'phase_call_detail returned % rows for equities', n; end if;
  select falsifier into t from macro_mart.phase_call_detail where asset = 'equities';
  if t is null then raise exception 'phase_call_detail lost the falsifier'; end if;
  select array_length(evidence, 1) into n from macro_mart.phase_call_detail where asset = 'equities';
  if n <> 2 then raise exception 'phase_call_detail lost the evidence array'; end if;

  select count(*) into n from macro_mart.scenario_set_state where week = '2026-W39';
  if n <> 1 then raise exception 'scenario_set_state returned % rows', n; end if;

  select sector_focus into j from macro_mart.strategy_state where week = '2026-W39' and asset = 'equities';
  if jsonb_array_length(j) <> 4 then
    raise exception 'strategy_state aggregated % sector focus rows, expected 4', jsonb_array_length(j);
  end if;
  if j -> 0 ->> 'band' <> 'CRITICAL' or j -> 3 ->> 'band' <> 'AVOID' then
    raise exception 'strategy_state ordered sector focus as %, not CRITICAL..AVOID', j;
  end if;
  select holdings_check into j from macro_mart.strategy_state where week = '2026-W39' and asset = 'equities';
  if jsonb_array_length(j) <> 2 then
    raise exception 'strategy_state aggregated % holdings check rows, expected 2', jsonb_array_length(j);
  end if;

  select count(*) into n from macro_mart.week_grade where week = '2026-W38' and graded_at is null;
  if n <> 1 then raise exception 'week_grade lost the migrated row with a null graded_at'; end if;

  select count(*) into n from macro_mart.setup_grade
   where proposed_week = '2026-W39' and asset = 'equities'
     and name = 'S&P FOMC-shelf FTR long' and graded_week = '2026-W40' and outcome = 'open'
     and entry_lo = 7500 and target = 7800;
  if n <> 1 then raise exception 'setup_grade did not carry the setup it grades'; end if;

  select fire_count into n from macro_mart.scenario_rule where number = 9001;
  if n <> 1 then raise exception 'scenario_rule counted % fires, expected 1', n; end if;
  select teaching_weeks[1] into t from macro_mart.scenario_rule where number = 9001;
  if t <> '2026-W38' then raise exception 'scenario_rule lost its teaching week'; end if;

  select count(*) into n from macro_mart.observation_history
   where series_key = 'smoke_spx_cash' and asof = date '2026-09-15'
     and value = 7585.73 and unit = 'index_points';
  if n <> 1 then raise exception 'observation_history did not carry the series key and unit'; end if;

  select count(*) into n from macro_mart.strategy_history where week = '2026-W39' and valid_to is null;
  if n <> 1 then raise exception 'strategy_history did not leave the standing row open'; end if;

  select sector_focus_label into t from macro_mart.setup_history where setup_id = v_setup;
  if t <> 'Memory and AI infrastructure' then
    raise exception 'setup_history did not resolve sector_focus_id to its label, got %', t;
  end if;

  reset role;
  raise notice 'PASS 11a: all nine views in 0012 resolve and return what their route needs';

  -- ---------------------------------------------------------
  -- Clean up, leaving no rows behind.
  -- ---------------------------------------------------------
  delete from macro_core.scenario_rule_week where rule_number = 9001;
  delete from macro_core.scenario_rule where number = 9001;
  delete from macro_core.setup_grade where setup_id = v_setup;
  delete from macro_core.week_grade where week = '2026-W38';
  delete from macro_core.setup where id = v_setup;
  delete from macro_core.condition_state_asserted
   where condition_id in (select id from macro_core.phase_condition where phase_call_id = v_phase);
  delete from macro_core.phase_call where id = v_phase;
  delete from macro_core.scenario_set where id = v_set;
  delete from macro_core.strategy where id = v_strategy;
  delete from macro_core.observation where series_id = v_series;
  delete from macro_core.series where id = v_series;
  set constraints all immediate;
end $$;

-- anon still sees nothing, including the nine new views.
do $$
declare n int; v text;
begin
  foreach v in array array[
    'phase_call_detail', 'scenario_set_state', 'strategy_state', 'week_grade',
    'setup_grade', 'scenario_rule', 'observation_history', 'strategy_history',
    'setup_history'
  ] loop
    begin
      set local role anon;
      execute format('select count(*) from macro_mart.%I', v) into n;
      reset role;
      perform pg_temp.expect_failed(format('anon reading macro_mart.%s', v));
    exception when insufficient_privilege then
      reset role;
    end;
  end loop;
  raise notice 'PASS 11b: anon is refused on all nine new views';
end $$;

-- Every view in macro_mart is readable by authenticated, with nothing left ungranted.
--
-- The count is read from the catalogue rather than written here. A test that pins a
-- count goes stale the first time a view is added and then fails for a reason that has
-- nothing to do with what it was checking. What is asserted is the property: every view
-- that exists is granted to authenticated and to nobody else. The floor is there only so
-- that an empty catalogue, which would satisfy the property vacuously, still fails.
do $$
declare v record; n int := 0; names text := '';
begin
  for v in select viewname from pg_views where schemaname = 'macro_mart' order by viewname loop
    if not has_table_privilege('authenticated', 'macro_mart.' || quote_ident(v.viewname), 'select') then
      raise exception 'authenticated cannot select macro_mart.%', v.viewname;
    end if;
    if has_table_privilege('anon', 'macro_mart.' || quote_ident(v.viewname), 'select') then
      raise exception 'anon can select macro_mart.%', v.viewname;
    end if;
    n := n + 1;
    names := names || case when names = '' then '' else ', ' end || v.viewname;
  end loop;
  if n < 11 then
    raise exception 'expected at least the 11 views the contract names in macro_mart, found %: %', n, names;
  end if;
  raise notice 'PASS 11c: all % macro_mart views grant select to authenticated and nothing to anon', n;
end $$;

-- ============================================================
-- 12. The desk: macro_desk reaches the three feeds and nothing else
-- ============================================================

do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'macro_desk') then
    raise exception 'the macro_desk role is missing';
  end if;
  if not has_table_privilege('macro_desk', 'macro_core.catalyst', 'insert')
     or not has_table_privilege('macro_desk', 'macro_core.tail_risk_check', 'insert')
     or not has_table_privilege('macro_desk', 'macro_core.holding_watch', 'update')
     or not has_table_privilege('macro_desk', 'macro_mart.snapshot_row', 'select') then
    raise exception 'macro_desk is missing a privilege the desk command needs';
  end if;
  if has_table_privilege('macro_desk', 'macro_core.catalyst', 'delete')
     or has_table_privilege('macro_desk', 'macro_core.tail_risk_check', 'update')
     or has_table_privilege('macro_desk', 'macro_core.phase_call', 'select')
     or has_table_privilege('macro_desk', 'macro_core.observation', 'select')
     or has_table_privilege('macro_desk', 'macro_core.manual_position', 'update')
     or has_function_privilege('macro_desk', 'macro_mart.request_refresh(text)', 'execute') then
    raise exception 'macro_desk can reach beyond the three feeds';
  end if;
  if has_function_privilege('authenticated', 'macro_core.accounting_open_positions()', 'execute')
     or has_function_privilege('anon', 'macro_core.accounting_open_positions()', 'execute') then
    raise exception 'the Accounting positions function is callable by a web role';
  end if;
  raise notice 'PASS 12a: macro_desk writes the feeds, reads the board, and reaches nothing else';
end $$;

-- A catalyst retired without a reason is refused.
do $$
begin
  insert into macro_core.catalyst (external_key, name, date_precision, status, first_seen, last_seen)
  values ('smoke-retired', 'smoke', 'day', 'retired', current_date, current_date);
  perform pg_temp.expect_failed('a retired catalyst with no reason');
exception when check_violation then
  raise notice 'PASS 12b: a retirement always says why';
end $$;

-- The operator's lists are edited only by a signed-in operator.
do $$
begin
  set local role authenticated;
  perform macro_mart.save_watch_ticker(null, 'SMOKE', null, 'equity', null);
  reset role;
  perform pg_temp.expect_failed('save_watch_ticker without auth.uid()');
exception when raise_exception then
  reset role;
  raise notice 'PASS 12c: the list functions refuse a caller with no auth.uid()';
end $$;

do $$
declare n int;
begin
  set local role authenticated;
  select count(*) into n from macro_mart.feed_freshness;
  select count(*) into n from macro_mart.holding_watch_state;
  select count(*) into n from macro_mart.tail_risk_history;
  reset role;
  raise notice 'PASS 12d: authenticated reads the desk''s views';
end $$;

-- ============================================================
-- 13. The desk after its review (20260925000200)
-- ============================================================

-- The vault parser lists and re-lists risks with no risk_id. Three names broke the
-- slug: a 44-character cut ending on a hyphen made '--2' on the re-listing, a
-- 48-character cut ending on one failed on the first listing, and 'AI' was too short.
do $$
declare
  n text;
  first_id text;
  again_id text;
begin
  foreach n in array array[
    'Sovereign debt ceiling brinkmanship trigger Treasury bill panic',
    'Internationally coordinated sovereign liquidity withdrawal cascade',
    'AI'] loop
    insert into macro_core.tail_risk (key, name, severity, first_seen, last_seen)
    values ('smoke-slug', n, 'watch', current_date, current_date)
    on conflict (key) do update set severity = excluded.severity;
    select risk_id into first_id from macro_core.tail_risk where key = 'smoke-slug';
    insert into macro_core.tail_risk (key, name, severity, first_seen, last_seen)
    values ('smoke-slug', n, 'elevated', current_date, current_date)
    on conflict (key) do update set severity = excluded.severity;
    select risk_id into again_id from macro_core.tail_risk where key = 'smoke-slug';
    if first_id is distinct from again_id then
      raise exception 'a re-listing of % changed its risk_id from % to %', n, first_id, again_id;
    end if;
    delete from macro_core.tail_risk where key = 'smoke-slug';
  end loop;
  raise notice 'PASS 13a: any name lists and re-lists with a valid, stable risk_id';
end $$;

do $$
begin
  if not has_function_privilege('macro_desk', 'macro_core.desk_confirm_catalysts_listed(date)', 'execute')
     or has_function_privilege('authenticated', 'macro_core.desk_confirm_catalysts_listed(date)', 'execute') then
    raise exception 'desk_confirm_catalysts_listed is not granted to macro_desk alone';
  end if;
  if not exists (select 1 from pg_roles where rolname = 'macro_desk'
                   and rolconfig @> array['idle_in_transaction_session_timeout=60s', 'lock_timeout=10s']) then
    raise exception 'macro_desk has no idle-in-transaction or lock timeout';
  end if;
  if exists (select 1 from pg_policies where schemaname = 'macro_core'
               and tablename in ('catalyst', 'tail_risk') and 'macro_desk' = any(roles)
               and cmd in ('INSERT', 'UPDATE')
               and coalesce(with_check, '') not like '%openclaw:macro-desk%') then
    raise exception 'macro_desk may write a catalyst or tail risk as another feed';
  end if;
  raise notice 'PASS 13b: macro_desk writes as the desk only, and its sessions time out';
end $$;

do $$
begin
  insert into macro_core.catalyst (external_key, name, date_precision, status, first_seen, last_seen, source_url)
  values ('smoke-url', 'smoke', 'day', 'tracked', current_date, current_date, 'javascript:alert(1)');
  perform pg_temp.expect_failed('a catalyst source_url that is not http(s)');
exception when check_violation then
  raise notice 'PASS 13c: a source_url is http(s) or empty';
end $$;

select 'SMOKE OK' as result;
