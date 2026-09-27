-- A phase call that was never accepted is not history.
--
-- `macro_mart.phase_call_detail` (20260923000900_fresh_phase.sql) found the call before
-- the active one, and the start of the current run of a phase, among rows with status
-- `active` or `superseded`. `superseded` has two meanings: a call that stood and was
-- replaced, and a proposed call that was stood down without ever standing, by a Reject
-- on the Desk, by the runner clearing a failed run, or by a second proposal for the same
-- week. The second kind was counted as a week the phase ran, or as the week before, so a
-- rejected W40 read could break a five-week run or become the "prior week" of the call
-- the operator did accept.
--
-- A call that stood carries `activated_at`, and one that never stood does not: every
-- migrated journal call carries it (checked read-only against the live rows on
-- 2026-09-25: all fourteen), and `Store.activate` stamps it. So the history is the rows
-- with `activated_at is not null`, and nothing else changes.
--
-- `create or replace` with the same columns in the same order, so the existing grants
-- (authenticated, service_role, macro_desk) are kept; they are restated below anyway,
-- so this file leaves the view granted exactly as before whatever order it runs in.
-- Re-runnable.

create or replace view macro_mart.phase_call_detail
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
    p.stage,
    p.stage_reason,
    p.week,
    p.activated_at,
    p.classification,
    p.classified_at,
    p.change_note,
    prior.week    as prior_week,
    run.since_week,
    case
      when run.since_week is null then null
      else (to_date(p.week, 'IYYY-"W"IW') - to_date(run.since_week, 'IYYY-"W"IW')) / 7 + 1
    end           as weeks_running
  from macro_core.phase_call p
  left join lateral (
    select q.week
      from macro_core.phase_call q
     where q.asset = p.asset
       and q.week < p.week
       and q.status in ('active', 'superseded')
       and q.activated_at is not null
     order by q.week desc, q.activated_at desc nulls last
     limit 1
  ) prior on true
  left join lateral (
    select min(q.week) as since_week
      from macro_core.phase_call q
     where q.asset = p.asset
       and q.week <= p.week
       and q.status in ('active', 'superseded')
       and q.activated_at is not null
       and q.phase = p.phase
       and q.week > coalesce(
         (
           select max(b.week)
             from macro_core.phase_call b
            where b.asset = p.asset
              and b.week < p.week
              and b.status in ('active', 'superseded')
              and b.activated_at is not null
              and b.phase <> p.phase
         ),
         ''
       )
  ) run on true
  where p.status = 'active';

comment on view macro_mart.phase_call_detail is
  'The active phase call per asset: the read, its argument, when it was classified, what changed since last week, and how many weeks the current phase has run. classification says whether the four conditions are what must hold (fresh) or the old signs of the next phase (carried). Only calls that were accepted (activated_at set) count as history.';

revoke all on macro_mart.phase_call_detail from public;
revoke all on macro_mart.phase_call_detail from anon;
grant select on macro_mart.phase_call_detail to authenticated;
grant select on macro_mart.phase_call_detail to service_role;

do $$
begin
  if exists (select 1 from pg_roles where rolname = 'macro_desk') then
    execute 'grant select on macro_mart.phase_call_detail to macro_desk';
  end if;
end
$$;
