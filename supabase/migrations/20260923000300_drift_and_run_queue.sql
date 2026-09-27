-- 20260923000300_drift_and_run_queue.sql
-- Three things the Desk needs, per docs/DECISIONS.md 2026-09-23 "The call refreshes on
-- request, and the system says when to ask":
--
--   1. macro_mart.drift            what has changed since the active call was made,
--                                  scored deterministically on every run
--   2. macro_core.run_request      the queue every Claude run goes through: the
--                                  09:30 narrative, Monday's authoring, and the
--                                  operator's Refresh button
--   3. the Catalysts page's reads  tail risks and watchlist changes, projected onto
--                                  macro_mart the way 0012 projects judgment
--
-- The web app has been read-only by contract since 0009, and it stays read-only in
-- every table. The Refresh button and the accept/reject of a proposal go through two
-- SECURITY DEFINER functions that insert or update exactly one row of the queue and
-- nothing else. Activation itself is still `macro-agent accept`, run by the runner on
-- the operator's Mac: the web records a decision, it never moves a row from proposed
-- to active.
--
-- Re-runnable: IF NOT EXISTS, CREATE OR REPLACE, drop-then-create views, idempotent
-- grants.

-- ============================================================
-- 1. Drift
-- ============================================================

-- One row per active scenario set. Rebuilt with the other evaluated macro_mart tables
-- on every run.
create table if not exists macro_mart.drift (
  set_id             uuid primary key,
  week               text not null,
  anchor_asof        date null,
  asof               date null,
  score              int not null check (score between 0 and 10),
  material           int not null default 0,
  refresh_suggested  boolean not null default false,
  reason             text not null,
  items              jsonb not null default '[]'::jsonb,
  evaluated_at       timestamptz not null default now()
);

comment on table macro_mart.drift is
  'What has moved since the active call was authored: every condition, band and setup gate re-evaluated at anchor_asof and compared with its state now. items is an array of {kind, subject, before, after, material, weight, sentence}. material counts the changes docs/DECISIONS.md says may permit a re-price.';
comment on column macro_mart.drift.score is
  'Weighted count of changes, capped at 10. A material change weighs 3, a setup gate 2, a band or clause 1.';

-- ============================================================
-- 2. The run queue
-- ============================================================

create table if not exists macro_core.run_request (
  id              bigserial primary key,
  kind            text not null check (kind in ('narrative', 'author', 'amend')),
  reason          text null,
  origin          text not null check (origin in ('schedule', 'operator')),
  requested_by    uuid null,
  requested_at    timestamptz not null default now(),
  status          text not null default 'queued'
                    check (status in ('queued', 'running', 'proposed', 'done', 'failed', 'rejected', 'accepted')),
  claimed_at      timestamptz null,
  finished_at     timestamptz null,
  decided_at      timestamptz null,
  decision        text null check (decision is null or decision in ('accept', 'reject')),
  -- The judgment rows the run wrote as proposed, as {"table": ..., "id": ...,
  -- "supersede": ..., "subject": [...]}: exactly the arguments `macro-agent accept`
  -- takes, so the runner replays them without re-deriving anything.
  proposed        jsonb not null default '[]'::jsonb,
  summary         text null,
  error           text null,
  constraint ck_macro_run_request_decision
    check (decision is null or status in ('proposed', 'accepted', 'rejected', 'done', 'failed'))
);

comment on table macro_core.run_request is
  'Every Claude run, queued. origin schedule is the runner''s own daily narrative and Monday author; origin operator is the Refresh button. A run that writes judgment stops at proposed; the operator''s decision is recorded here and the runner applies it with macro-agent accept.';

-- At most one run of each kind in flight, so a double-click queues one run, not two.
create unique index if not exists uq_macro_run_request_in_flight
  on macro_core.run_request (kind)
  where status in ('queued', 'running');

create index if not exists idx_macro_run_request_requested_at
  on macro_core.run_request (requested_at desc);

-- What the web reads. No requested_by: the operator does not need their own uuid.
drop view if exists macro_mart.run_request_state;
create view macro_mart.run_request_state
with (security_invoker = false)
as
  select
    id,
    kind,
    reason,
    origin,
    requested_at,
    status,
    claimed_at,
    finished_at,
    decided_at,
    decision,
    proposed,
    summary,
    error
  from macro_core.run_request
  where requested_at > now() - interval '30 days';

comment on view macro_mart.run_request_state is
  'The last thirty days of the run queue, for the Desk''s Refresh control and its proposal review.';

-- The Refresh button. Only an amend may be requested from the web: the narrative and
-- the Monday author are the runner's own schedule.
create or replace function macro_mart.request_refresh(p_reason text)
returns bigint
language plpgsql
security definer
set search_path = macro_core, pg_temp
as $$
declare
  v_id bigint;
begin
  if auth.uid() is null then
    raise exception 'request_refresh needs a signed-in operator';
  end if;
  if p_reason is null or length(btrim(p_reason)) < 3 then
    raise exception 'a refresh names what changed, in at least a few words';
  end if;
  select id into v_id
    from macro_core.run_request
   where kind = 'amend' and status in ('queued', 'running');
  if v_id is not null then
    return v_id;
  end if;
  insert into macro_core.run_request (kind, reason, origin, requested_by)
  values ('amend', left(btrim(p_reason), 500), 'operator', auth.uid())
  returning id into v_id;
  return v_id;
end $$;

-- The accept/reject of a proposal. Records the decision; the runner applies it.
create or replace function macro_mart.decide_proposal(p_id bigint, p_decision text)
returns void
language plpgsql
security definer
set search_path = macro_core, pg_temp
as $$
begin
  if auth.uid() is null then
    raise exception 'decide_proposal needs a signed-in operator';
  end if;
  if p_decision not in ('accept', 'reject') then
    raise exception 'a decision is accept or reject, not %', p_decision;
  end if;
  update macro_core.run_request
     set decision = p_decision,
         decided_at = now()
   where id = p_id
     and status = 'proposed'
     and decision is null;
  if not found then
    raise exception 'run % is not a proposal awaiting a decision', p_id;
  end if;
end $$;

revoke all on function macro_mart.request_refresh(text) from public, anon;
revoke all on function macro_mart.decide_proposal(bigint, text) from public, anon;
grant execute on function macro_mart.request_refresh(text) to authenticated, service_role;
grant execute on function macro_mart.decide_proposal(bigint, text) to authenticated, service_role;

-- ============================================================
-- 3. The Catalysts page's reads
-- ============================================================

drop view if exists macro_mart.tail_risk_state;
create view macro_mart.tail_risk_state
with (security_invoker = false)
as
  select key, name, severity, summary, priced_in, assets, source_url, first_seen, last_seen
  from macro_core.tail_risk;

comment on view macro_mart.tail_risk_state is
  'Blackswan Events.md as the worker parsed it on the last full run. last_seen older than the newest full run means the cron stopped listing it.';

drop view if exists macro_mart.watchlist_change_state;
create view macro_mart.watchlist_change_state
with (security_invoker = false)
as
  select ticker, observed_at, change_pct, summary, source_url
  from macro_core.watchlist_change
  where observed_at > now() - interval '14 days';

comment on view macro_mart.watchlist_change_state is
  'The last fourteen days of Market Watchlist.md deltas.';

-- ============================================================
-- Grants
-- ============================================================

alter table macro_mart.drift enable row level security;
alter table macro_core.run_request enable row level security;

revoke all on macro_mart.drift from public, anon;
grant select on macro_mart.drift to authenticated;
grant all on macro_mart.drift to service_role;

revoke all on macro_core.run_request from public, anon, authenticated;
grant all on macro_core.run_request to service_role;
grant usage, select on sequence macro_core.run_request_id_seq to service_role;

do $$
declare
  v text;
begin
  foreach v in array array['run_request_state', 'tail_risk_state', 'watchlist_change_state'] loop
    execute format('revoke all on macro_mart.%I from public, anon', v);
    execute format('grant select on macro_mart.%I to authenticated', v);
    execute format('grant select on macro_mart.%I to service_role', v);
  end loop;
end $$;

-- An authenticated SELECT on an RLS-enabled mart table needs a policy, as in 0009.
drop policy if exists authenticated_select on macro_mart.drift;
create policy authenticated_select on macro_mart.drift
  for select to authenticated using (true);
