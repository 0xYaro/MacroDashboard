-- 20260924000500_desk_feed_freshness.sql
-- Where each of the three feeds last came from and when, for the page's freshness line
-- ("Catalysts: OpenClaw, 08:51 today").
--
-- One row per feed that has ever run: catalysts, tail_risks, holdings. Read from
-- macro_quality.ingest_run rows that carry a `feed` (20260924000300): the desk command
-- writes one per live submission, and the worker writes one per vault note it reads
-- while that feed is still sourced from the vault. A dry run writes no ingest run, so
-- the trial week never shows here.
--
-- A feed that fails keeps its last good time in last_ok_at while last_status says it
-- failed, which is what lets the page show it as stale instead of quietly empty.
--
-- Re-runnable: the view is dropped and recreated with its grants.

drop view if exists macro_mart.feed_freshness;
create view macro_mart.feed_freshness
with (security_invoker = false)
as
  select f.feed,
         l.origin,
         l.started_at as last_run_at,
         l.finished_at as last_finished_at,
         l.status as last_status,
         l.error as last_error,
         ok.finished_at as last_ok_at,
         ok.origin as last_ok_origin,
         ok.rows
  from (select distinct feed from macro_quality.ingest_run where feed is not null) f
  cross join lateral (
    select r.origin, r.started_at, r.finished_at, r.status, r.error
      from macro_quality.ingest_run r
     where r.feed = f.feed
     order by r.started_at desc, r.id desc
     limit 1
  ) l
  left join lateral (
    select r.origin, r.finished_at, r.rows
      from macro_quality.ingest_run r
     where r.feed = f.feed and r.status = 'ok' and r.finished_at is not null
     order by r.finished_at desc, r.id desc
     limit 1
  ) ok on true;

comment on view macro_mart.feed_freshness is
  'Per feed (catalysts, tail_risks, holdings): the latest run, who sent it (origin: openclaw:macro-desk or vault), its status, and the last run that succeeded with how many rows it wrote.';

revoke all on macro_mart.feed_freshness from public, anon;
grant select on macro_mart.feed_freshness to authenticated, service_role, macro_desk;
