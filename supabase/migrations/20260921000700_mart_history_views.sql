-- 20260921000700_mart_history_views.sql
-- macro_mart.scenario_set_history and macro_mart.phase_call_history.
--
-- These are the one exception to "every macro_mart table is derived": they are views,
-- not recomputations, and they must never be dropped and rebuilt the way the evaluated
-- tables are. macro_core is not on the Data API and /history has to read what was once
-- believed, so this projection is the only route to it.
--
-- Security model, the same one Accounting uses for its core -> mart mirror:
-- both views are created WITH (security_invoker = false), so they run with the
-- privileges of their owner (the migration role, which owns the macro_core tables)
-- rather than the querying role's. authenticated has no USAGE on schema macro_core
-- and no grant on any table in it, so security_invoker = true would make every query
-- fail with "permission denied for schema macro_core" whatever was granted on the view.
-- The bypass is narrow on purpose: these are the only two objects reading macro_core
-- from outside the service-role key, they select named columns from two tables, and
-- they grant SELECT only.
--
-- Validity intervals:
--   valid_from  activated_at. Null while the row is still proposed, which is correct:
--               a proposal was never believed, so it has no validity interval yet.
--   valid_to    the activated_at of the row that superseded this one. Null while the
--               row still stands, or while its successor is itself only proposed.
-- valid_to is read through superseded_by rather than computed from the next row by
-- authored_at, so a row retired without a replacement reads as still-open history
-- rather than silently closing at the next unrelated authoring.
--
-- Re-runnable: CREATE OR REPLACE VIEW and GRANT/REVOKE are idempotent.

create or replace view macro_mart.scenario_set_history
with (security_invoker = false)
as
  select
    s.id,
    s.week,
    s.synthesis,
    s.probability_check,
    s.swing_variables,
    s.rules_waived,
    s.status,
    s.authored_at,
    s.activated_at                as valid_from,
    successor.activated_at        as valid_to,
    s.superseded_by,
    s.amends,
    s.amend_reason
  from macro_core.scenario_set s
  left join macro_core.scenario_set successor
    on successor.id = s.superseded_by;

comment on view macro_mart.scenario_set_history is
  'Read-only projection of macro_core.scenario_set including superseded rows, with valid_from, valid_to, amends and amend_reason. Runs with the owning role''s privileges (security_invoker = false) so authenticated can read it without any USAGE on schema macro_core. Never drop and rebuild this with the evaluated macro_mart tables.';

create or replace view macro_mart.phase_call_history
with (security_invoker = false)
as
  select
    p.id,
    p.asset,
    p.week,
    p.phase,
    p.phase_name,
    p.prior_phase,
    p.direction,
    p.reason,
    p.evidence,
    p.falsifier,
    p.status,
    p.authored_at,
    p.activated_at            as valid_from,
    successor.activated_at    as valid_to,
    p.superseded_by,
    p.amends,
    p.amend_reason
  from macro_core.phase_call p
  left join macro_core.phase_call successor
    on successor.id = p.superseded_by;

comment on view macro_mart.phase_call_history is
  'Read-only projection of macro_core.phase_call including superseded rows, with valid_from, valid_to, amends and amend_reason. Runs with the owning role''s privileges (security_invoker = false) so authenticated can read it without any USAGE on schema macro_core. Never drop and rebuild this with the evaluated macro_mart tables.';

revoke all on macro_mart.scenario_set_history from public, anon;
revoke all on macro_mart.phase_call_history from public, anon;

grant select on macro_mart.scenario_set_history to authenticated;
grant select on macro_mart.phase_call_history to authenticated;

grant select on macro_mart.scenario_set_history to service_role;
grant select on macro_mart.phase_call_history to service_role;
