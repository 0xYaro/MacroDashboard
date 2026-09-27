-- 20260923000900_fresh_phase.sql
-- The phase is read fresh every week. docs/DECISIONS.md 2026-09-23, "The phase is
-- classified fresh every Monday".
--
-- The operator's words: reclassify every week, and make it a fresh classification,
-- not last week's read checked for an upgrade in phase order 1 to 5, because a market
-- can go from 3 to 4 and back to 2 and every snapshot needs a fresh view.
--
-- Three things change, and this file carries the schema half of each.
--
--   1. The Monday run classifies each asset from the data and the frameworks with the
--      prior call out of its context, and writes that read before the prior call is
--      shown to it. `classified_at` is when the read was written and `revealed_at` is
--      when the prior call was first served to the same run, so the order is a fact
--      in the row rather than a promise in a runbook: a fresh row whose revealed_at
--      precedes its classified_at is refused. `change_note` is the one sentence the
--      run writes after the reveal, saying what changed since last week.
--
--   2. The carried call and the blind read collapse into one reading. The fresh read
--      IS the blind read, so `blind_phase` and `blind_reason` are dropped rather than
--      kept beside it. They were added by 20260922000100 and never written: all
--      fourteen migrated calls carry nulls, and no call was authored under that
--      design. The drop is guarded, so it refuses if any row ever did record one.
--
--   3. The four conditions stop counting toward the NEXT phase and become what must
--      stay true for THIS phase to hold. There is no upgrade any more, so
--      `macro_mart.phase_score.upgrade_eligible` and its 3-of-4 constraint go, and
--      `failed` and `read_stale` replace them: two or more failed conditions on a
--      fresh call mean this week's read may be out of date. It is flagged, never
--      acted on: the next read is the next Monday's.
--
-- `classification` tells the two kinds of row apart. The fourteen migrated calls are
-- `carried`: their conditions still mean "toward the next phase", as their journals
-- wrote them, and nothing here rewrites what they claimed.
--
-- Re-runnable: IF [NOT] EXISTS on every column, drop-then-add on every constraint,
-- drop-then-create on the view.

-- ============================================================
-- 1. macro_core.phase_call
-- ============================================================

alter table macro_core.phase_call
  add column if not exists classification text not null default 'carried',
  add column if not exists classified_at  timestamptz null,
  add column if not exists revealed_at    timestamptz null,
  add column if not exists change_note    text null;

alter table macro_core.phase_call
  drop constraint if exists ck_macro_phase_call_classification;
alter table macro_core.phase_call
  add constraint ck_macro_phase_call_classification
  check (classification in ('carried', 'fresh'));

-- A fresh read has a moment it was taken, and the prior call cannot have been shown
-- to the run before it.
alter table macro_core.phase_call
  drop constraint if exists ck_macro_phase_call_fresh_order;
alter table macro_core.phase_call
  add constraint ck_macro_phase_call_fresh_order
  check (
    classification = 'carried'
    or (
      classified_at is not null
      and (revealed_at is null or revealed_at >= classified_at)
    )
  );

comment on column macro_core.phase_call.classification is
  'fresh: classified from the data and the frameworks with the prior call out of view, and its four conditions are what must stay true for this phase to hold. carried: one of the fourteen migrated journal calls, whose four conditions counted toward the next phase.';
comment on column macro_core.phase_call.classified_at is
  'When the fresh read was written. Null on carried rows.';
comment on column macro_core.phase_call.revealed_at is
  'When the prior call was first served to the run that wrote this read. Always at or after classified_at.';
comment on column macro_core.phase_call.change_note is
  'One sentence, written after the reveal: what changed since last week''s call.';
comment on column macro_core.phase_call.prior_phase is
  'Last week''s phase, filled in by the command that wrote the read, never by the read itself.';
comment on column macro_core.phase_call.direction is
  'The phase number''s move from prior_phase: up, down or unchanged. Any phase may follow any phase.';

-- The blind read is retired. Refuse rather than lose anything if one was ever written.
do $$
begin
  if exists (
    select 1 from information_schema.columns
     where table_schema = 'macro_core' and table_name = 'phase_call'
       and column_name = 'blind_phase'
  ) then
    if exists (
      select 1 from macro_core.phase_call
       where blind_phase is not null or blind_reason is not null
    ) then
      raise exception 'macro_core.phase_call carries a recorded blind read; it would be lost by this migration';
    end if;
  end if;
end $$;

-- The view reads the two columns, so it goes first and is rebuilt below.
drop view if exists macro_mart.phase_call_detail;

alter table macro_core.phase_call
  drop column if exists blind_phase,
  drop column if exists blind_reason;

-- ============================================================
-- 2. macro_mart.phase_score
-- ============================================================

-- Evaluated, rebuilt on every worker run, so nothing here is a record to preserve.
alter table macro_mart.phase_score
  drop constraint if exists ck_macro_phase_score_upgrade;
alter table macro_mart.phase_score
  drop column if exists upgrade_eligible,
  drop column if exists blind_phase,
  drop column if exists blind_agrees;

alter table macro_mart.phase_score
  add column if not exists failed     int not null default 0,
  add column if not exists read_stale boolean not null default false;

alter table macro_mart.phase_score
  drop constraint if exists ck_macro_phase_score_counts;
alter table macro_mart.phase_score
  add constraint ck_macro_phase_score_counts
  check (met >= 0 and failed >= 0 and unevaluable >= 0 and met + failed + unevaluable <= of);

-- The flag is only ever raised on evidence: two of the four things the read holds on
-- have failed.
alter table macro_mart.phase_score
  drop constraint if exists ck_macro_phase_score_stale;
alter table macro_mart.phase_score
  add constraint ck_macro_phase_score_stale
  check (read_stale = false or failed >= 2);

comment on column macro_mart.phase_score.failed is
  'How many of the call''s four conditions have failed. On a fresh call, a condition that no longer holds has failed; there is no "not yet".';
comment on column macro_mart.phase_score.read_stale is
  'True when two or more of a fresh call''s four conditions have failed: this week''s read may be out of date. Flagged, never acted on; the phase is read again on Monday.';

-- ============================================================
-- 3. macro_mart.phase_call_detail
-- ============================================================

-- The active call per asset, and the three facts the Phase view needs about the call
-- before it: which week it was, where the current run of this phase began, and how
-- many calendar weeks that run spans. A run is broken by any earlier call on another
-- phase; proposed rows never count, because nobody accepted them.
--
-- Weeks are ISO weeks (`2026-W39`), so a run is measured between the Mondays of its
-- first and current week: a week with no journal (W33) still passed.
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
     order by q.week desc, q.activated_at desc nulls last
     limit 1
  ) prior on true
  left join lateral (
    select min(q.week) as since_week
      from macro_core.phase_call q
     where q.asset = p.asset
       and q.week <= p.week
       and q.status in ('active', 'superseded')
       and q.phase = p.phase
       and q.week > coalesce(
         (
           select max(b.week)
             from macro_core.phase_call b
            where b.asset = p.asset
              and b.week < p.week
              and b.status in ('active', 'superseded')
              and b.phase <> p.phase
         ),
         ''
       )
  ) run on true
  where p.status = 'active';

comment on view macro_mart.phase_call_detail is
  'The active phase call per asset: the read, its argument, when it was classified, what changed since last week, and how many weeks the current phase has run. classification says whether the four conditions are what must hold (fresh) or the old signs of the next phase (carried).';

revoke all on macro_mart.phase_call_detail from public;
revoke all on macro_mart.phase_call_detail from anon;
grant select on macro_mart.phase_call_detail to authenticated;
grant select on macro_mart.phase_call_detail to service_role;
