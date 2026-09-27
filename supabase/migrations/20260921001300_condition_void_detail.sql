-- macro_mart.condition_state can say a clause is `void` but not why, because the reason
-- and the moment live on macro_core.trigger_clause, which is not on the Data API.
--
-- A scenario flagged `premise moved` whose clause cannot state which catalyst moved, and
-- when, is a claim the operator has to take on trust. The whole point of not renormalising
-- the threshold is that the void clause stays visible and checkable, so the reason has to
-- travel with it.
alter table macro_mart.condition_state
  add column if not exists void_at timestamptz null;
alter table macro_mart.condition_state
  add column if not exists void_reason text null;

comment on column macro_mart.condition_state.void_reason is
  'Carried from macro_core.trigger_clause. Null unless status = ''void''.';
