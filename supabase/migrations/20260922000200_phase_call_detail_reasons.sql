-- 20260922000100 added blind_phase, blind_reason, stage and stage_reason to
-- macro_core.phase_call but did not rebuild the view that serves them, so the two
-- REASONS were unreachable: the cards could show that a blind read disagreed, and not
-- the argument for why.
--
-- That is the wrong half to lose. The contract says a disagreement is a finding and never
-- an override, which only works if the operator can read both cases and decide. A
-- disagreement with no argument beside it is a flag with nothing behind it, and the
-- honest response to one is to ignore it.
drop view if exists macro_mart.phase_call_detail;
create view macro_mart.phase_call_detail
with (security_invoker = false)
as
  select
    p.asset,
    p.id          as phase_call_id,
    p.phase,
    p.phase_name,
    p.prior_phase,
    p.direction,
    p.reason,
    p.evidence,
    p.falsifier,
    p.blind_phase,
    p.blind_reason,
    p.stage,
    p.stage_reason,
    p.week,
    p.activated_at
  from macro_core.phase_call p
  where p.status = 'active';

comment on view macro_mart.phase_call_detail is
  'The active phase call per asset, both readings and both arguments. blind_phase and stage are null for every migrated week, whose journals recorded only the carried call.';

revoke all on macro_mart.phase_call_detail from public;
revoke all on macro_mart.phase_call_detail from anon;
grant select on macro_mart.phase_call_detail to authenticated;
grant select on macro_mart.phase_call_detail to service_role;
