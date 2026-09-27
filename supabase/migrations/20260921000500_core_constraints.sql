-- 20260921000500_core_constraints.sql
-- The two cardinality assertions docs/CONTRACT.md makes about judgment, which no
-- single-row CHECK can express:
--
--   "A constraint asserts the three probabilities of a set sum to 100."
--   "Exactly four per phase call. The 3-of-4 rule is enforced against these."
--
-- Both are about a set of sibling rows, and the rows land one at a time, so both are
-- DEFERRABLE INITIALLY DEFERRED constraint triggers: they fire at COMMIT, not at each
-- INSERT. A whole set is therefore written in one transaction and is never observed
-- half-built by the check.
--
-- Two triggers per rule, one on the parent and one on the child. The child trigger
-- alone would never fire for a parent inserted with no children at all, which is the
-- exact mistake the rule exists to catch.
--
-- SET CONSTRAINTS ALL IMMEDIATE inside a transaction will force these to fire early;
-- the authoring path must not do that while a set is half-written.
--
-- Re-runnable: functions use CREATE OR REPLACE, triggers are dropped and recreated.

-- ============================================================
-- A scenario set has exactly three scenarios summing to 100
-- ============================================================

create or replace function macro_core.assert_scenario_set_probabilities(p_set_id uuid)
returns void
language plpgsql
as $$
declare
  n   int;
  tot int;
begin
  -- A set deleted in the same transaction has nothing left to assert.
  if not exists (select 1 from macro_core.scenario_set where id = p_set_id) then
    return;
  end if;

  select count(*), coalesce(sum(probability), 0)
    into n, tot
    from macro_core.scenario
   where set_id = p_set_id;

  if n <> 3 then
    raise exception
      'scenario_set % has % scenarios; exactly 3 are required', p_set_id, n
      using errcode = 'check_violation';
  end if;

  if tot <> 100 then
    raise exception
      'scenario_set % probabilities sum to %, not 100', p_set_id, tot
      using errcode = 'check_violation';
  end if;
end $$;

create or replace function macro_core.tg_scenario_probabilities()
returns trigger
language plpgsql
as $$
begin
  if tg_op = 'DELETE' then
    perform macro_core.assert_scenario_set_probabilities(old.set_id);
  elsif tg_op = 'UPDATE' and old.set_id is distinct from new.set_id then
    perform macro_core.assert_scenario_set_probabilities(old.set_id);
    perform macro_core.assert_scenario_set_probabilities(new.set_id);
  else
    perform macro_core.assert_scenario_set_probabilities(new.set_id);
  end if;
  return null;
end $$;

create or replace function macro_core.tg_scenario_set_probabilities()
returns trigger
language plpgsql
as $$
begin
  perform macro_core.assert_scenario_set_probabilities(new.id);
  return null;
end $$;

drop trigger if exists trg_scenario_probabilities on macro_core.scenario;
create constraint trigger trg_scenario_probabilities
  after insert or update or delete on macro_core.scenario
  deferrable initially deferred
  for each row
  execute function macro_core.tg_scenario_probabilities();

drop trigger if exists trg_scenario_set_probabilities on macro_core.scenario_set;
create constraint trigger trg_scenario_set_probabilities
  after insert on macro_core.scenario_set
  deferrable initially deferred
  for each row
  execute function macro_core.tg_scenario_set_probabilities();

-- ============================================================
-- A phase call has exactly four conditions
-- ============================================================

create or replace function macro_core.assert_phase_condition_count(p_phase_call_id uuid)
returns void
language plpgsql
as $$
declare
  n int;
begin
  if not exists (select 1 from macro_core.phase_call where id = p_phase_call_id) then
    return;
  end if;

  select count(*) into n
    from macro_core.phase_condition
   where phase_call_id = p_phase_call_id;

  if n <> 4 then
    raise exception
      'phase_call % has % conditions; exactly 4 are required', p_phase_call_id, n
      using errcode = 'check_violation';
  end if;
end $$;

create or replace function macro_core.tg_phase_condition_count()
returns trigger
language plpgsql
as $$
begin
  if tg_op = 'DELETE' then
    perform macro_core.assert_phase_condition_count(old.phase_call_id);
  elsif tg_op = 'UPDATE' and old.phase_call_id is distinct from new.phase_call_id then
    perform macro_core.assert_phase_condition_count(old.phase_call_id);
    perform macro_core.assert_phase_condition_count(new.phase_call_id);
  else
    perform macro_core.assert_phase_condition_count(new.phase_call_id);
  end if;
  return null;
end $$;

create or replace function macro_core.tg_phase_call_condition_count()
returns trigger
language plpgsql
as $$
begin
  perform macro_core.assert_phase_condition_count(new.id);
  return null;
end $$;

drop trigger if exists trg_phase_condition_count on macro_core.phase_condition;
create constraint trigger trg_phase_condition_count
  after insert or update or delete on macro_core.phase_condition
  deferrable initially deferred
  for each row
  execute function macro_core.tg_phase_condition_count();

drop trigger if exists trg_phase_call_condition_count on macro_core.phase_call;
create constraint trigger trg_phase_call_condition_count
  after insert on macro_core.phase_call
  deferrable initially deferred
  for each row
  execute function macro_core.tg_phase_call_condition_count();

-- ============================================================
-- Lifecycle integrity
-- ============================================================
-- A proposed row has not been activated and cannot have been superseded; an active row
-- carries the moment the operator accepted it and still stands. Nothing is asserted
-- about a superseded row: a proposal the operator rejects is superseded without ever
-- having been active, and a judgment retired without a replacement is superseded with
-- a null superseded_by.
-- Stated as CHECKs here rather than inline in 0003 so that the whole lifecycle rule
-- reads in one place.

do $$
declare
  t text;
begin
  foreach t in array array['phase_call', 'scenario_set', 'setup', 'strategy'] loop
    execute format(
      'alter table macro_core.%I drop constraint if exists ck_macro_%s_lifecycle', t, t);
    execute format($f$
      alter table macro_core.%I add constraint ck_macro_%s_lifecycle check (
        (status = 'proposed'  and activated_at is null     and superseded_by is null)
        or (status = 'active' and activated_at is not null and superseded_by is null)
        or (status = 'superseded')
      )$f$, t, t);

    execute format(
      'alter table macro_core.%I drop constraint if exists ck_macro_%s_amend', t, t);
    execute format($f$
      alter table macro_core.%I add constraint ck_macro_%s_amend check (
        (amends is null and amend_reason is null) or (amends is not null and amend_reason is not null)
      )$f$, t, t);
  end loop;
end $$;
