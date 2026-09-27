-- 20260924000300_desk_catalysts_tail_risks.sql
-- What the desk command needs to write the catalyst calendar and the tail risks as
-- explicit operations instead of a whole markdown file re-read every morning.
--
-- 1. Every desk submission is an ingest run. macro_quality.ingest_run gains the kind
--    `desk`, the `feed` it wrote (catalysts, tail_risks, holdings) and the `origin`
--    that sent it ('openclaw:macro-desk'), so the page can say "Catalysts: OpenClaw,
--    08:51 today". The worker's own feed reads use the same two columns.
--
-- 2. A dry run is archived and nothing else. macro_raw.batch gains `shadow`: a
--    submission run with --dry-run is validated in full, stored here with shadow =
--    true beside what it would have done, and writes no other row anywhere. The
--    trial week compares these against the old crons.
--
-- 3. Catalysts keep their identity. `external_key` stays the sha256 of the normalised
--    name (worker/src/macro/vault.py external_key), so the 72 live rows, the 335
--    archived occurrences and every trigger_clause/catalyst_window reference carry
--    over untouched. New: which feed last wrote the row and in which run, a CHECK on
--    date_precision's five values, and catalyst_alias, which is what a rename leaves
--    behind: the row keeps its id, takes the new name and key, and the old key maps
--    to it so a resubmission under the old name is recognised rather than duplicated.
--
-- 4. Tail risks get a stable `risk_id` slug. `key` (a hash of the name) stays the
--    primary key, so tail_risk_state and every reader keep working, but rewording a
--    risk no longer forks it: the desk addresses a risk by `risk_id`. The five live
--    rows get hand-picked slugs; any other row, and every row the worker's vault
--    parser inserts, gets one derived from its name by a trigger. Every check the desk
--    makes is kept in macro_core.tail_risk_check, so the page can show a risk
--    escalating. A retirement carries its reason.
--
-- 5. Grants and policies for macro_desk on exactly these tables, and the mart views
--    the web reads.
--
-- Re-runnable: IF NOT EXISTS, constraints dropped before they are added, CREATE OR
-- REPLACE, policies dropped and recreated.

-- ============================================================
-- 1. Ingest runs for desk submissions and feed reads
-- ============================================================

alter table macro_quality.ingest_run drop constraint if exists ingest_run_run_kind_check;
alter table macro_quality.ingest_run drop constraint if exists ck_macro_ingest_run_kind;
alter table macro_quality.ingest_run add constraint ck_macro_ingest_run_kind
  check (run_kind in ('full', 'rescore', 'desk'));

alter table macro_quality.ingest_run
  add column if not exists feed text null,
  add column if not exists origin text null;

alter table macro_quality.ingest_run drop constraint if exists ck_macro_ingest_run_feed;
alter table macro_quality.ingest_run add constraint ck_macro_ingest_run_feed
  check (feed is null or feed in ('catalysts', 'tail_risks', 'holdings'));

comment on column macro_quality.ingest_run.feed is
  'Which of the three feeds this run wrote, for a desk submission or a worker read of the matching vault note. Null for a data source.';
comment on column macro_quality.ingest_run.origin is
  'Who sent the feed: openclaw:macro-desk for the desk command, vault for the worker''s read of an Obsidian note.';

create index if not exists idx_macro_ingest_run_feed_started_at
  on macro_quality.ingest_run (feed, started_at desc)
  where feed is not null;

-- ============================================================
-- 2. Shadow batches
-- ============================================================

alter table macro_raw.batch
  add column if not exists shadow boolean not null default false;

comment on column macro_raw.batch.shadow is
  'True for a desk submission made with --dry-run: validated and archived with what it would have written, and nothing else was written.';

-- ============================================================
-- 3. Catalysts: provenance, precision, renames
-- ============================================================

alter table macro_core.catalyst
  add column if not exists feed_source text not null default 'vault',
  add column if not exists last_run_id bigint null
    references macro_quality.ingest_run (id) on delete set null,
  add column if not exists updated_at timestamptz null;

alter table macro_core.catalyst drop constraint if exists ck_macro_catalyst_feed_source;
alter table macro_core.catalyst add constraint ck_macro_catalyst_feed_source
  check (feed_source in ('vault', 'openclaw:macro-desk'));

alter table macro_core.catalyst drop constraint if exists ck_macro_catalyst_date_precision;
alter table macro_core.catalyst add constraint ck_macro_catalyst_date_precision
  check (date_precision in ('exact', 'day', 'window', 'month', 'unknown'));

-- A retirement always says why.
alter table macro_core.catalyst drop constraint if exists ck_macro_catalyst_retired_reason;
alter table macro_core.catalyst add constraint ck_macro_catalyst_retired_reason
  check (status <> 'retired' or retired_reason is not null);

comment on column macro_core.catalyst.feed_source is
  'Which feed last wrote this row: vault (the worker parsing Catalyst Monitor.md) or openclaw:macro-desk (macro-agent desk catalysts submit).';
comment on column macro_core.catalyst.last_run_id is
  'The macro_quality.ingest_run of the desk submission that last wrote this row. Null for a row the vault parser wrote.';
comment on column macro_core.catalyst.updated_at is
  'When the desk last changed this row. Null for a row only the vault parser has written.';

create table if not exists macro_core.catalyst_alias (
  external_key  text primary key,
  catalyst_id   uuid not null references macro_core.catalyst (id) on delete restrict,
  name          text not null,
  renamed_at    timestamptz not null default now(),
  reason        text null,
  run_id        bigint null references macro_quality.ingest_run (id) on delete set null
);

comment on table macro_core.catalyst_alias is
  'The names a catalyst was known by before a rename. A rename keeps the row''s id and history and changes its name and external_key; the old key is kept here so a resubmission under the old name maps to the same row instead of forking it.';

create index if not exists idx_macro_catalyst_alias_catalyst
  on macro_core.catalyst_alias (catalyst_id);

-- ============================================================
-- 4. Tail risks: a stable slug, retirement reasons, a check history
-- ============================================================

alter table macro_core.tail_risk
  add column if not exists risk_id text null,
  add column if not exists retired_reason text null,
  add column if not exists feed_source text not null default 'vault',
  add column if not exists last_run_id bigint null
    references macro_quality.ingest_run (id) on delete set null;

alter table macro_core.tail_risk drop constraint if exists ck_macro_tail_risk_feed_source;
alter table macro_core.tail_risk add constraint ck_macro_tail_risk_feed_source
  check (feed_source in ('vault', 'openclaw:macro-desk'));

-- A slug from a name: lower case, runs of anything else become one hyphen, at most the
-- first six words and 48 characters. Used for the backfill and by the insert trigger,
-- so the vault parser's rows get the same shape of id the desk writes.
create or replace function macro_core.tail_risk_slug(p_name text)
returns text
language sql
immutable
set search_path = pg_catalog, pg_temp
as $$
  select coalesce(
    nullif(
      left(
        array_to_string(
          (regexp_split_to_array(
             trim(both '-' from regexp_replace(lower(coalesce(p_name, '')), '[^a-z0-9]+', '-', 'g')),
             '-'))[1:6],
          '-'),
        48),
      ''),
    'risk');
$$;

-- The five risks live on 2026-09-24, named by hand. `key` is the worker's hash of each
-- name, so this is a no-op on any database that never held them.
update macro_core.tail_risk
   set risk_id = case key
     when 'e4bfe255867d8c777a84b4fe58740d14' then 'iran-oil-chokepoints'
     when '1d8462d5697169ff5a63594f34ea03d9' then 'us-funding-deadline'
     when 'c0107801a2bfd7751f9ead9d183c5614' then 'ai-capex-unwind'
     when '1d4b8d7401c59b13f88c69e2bacc965b' then 'private-credit-insurers'
     when '5aeb5090cf115175e4ce842df164a9b6' then 'iran-cyber-water'
   end
 where risk_id is null
   and key in ('e4bfe255867d8c777a84b4fe58740d14', '1d8462d5697169ff5a63594f34ea03d9',
               'c0107801a2bfd7751f9ead9d183c5614', '1d4b8d7401c59b13f88c69e2bacc965b',
               '5aeb5090cf115175e4ce842df164a9b6');

-- Any other row: a slug from its name, suffixed -2, -3 ... on a clash.
do $$
declare
  r record;
  base text;
  candidate text;
  n int;
begin
  for r in select key, name from macro_core.tail_risk where risk_id is null order by first_seen, key loop
    base := macro_core.tail_risk_slug(r.name);
    candidate := base;
    n := 1;
    while exists (select 1 from macro_core.tail_risk where risk_id = candidate) loop
      n := n + 1;
      candidate := left(base, 44) || '-' || n;
    end loop;
    update macro_core.tail_risk set risk_id = candidate where key = r.key;
  end loop;
end $$;

-- The vault parser inserts without a risk_id. Give it one the same way.
create or replace function macro_core.tail_risk_default_id()
returns trigger
language plpgsql
set search_path = pg_catalog, pg_temp
as $$
declare
  base text;
  candidate text;
  n int := 1;
begin
  if new.risk_id is not null then
    return new;
  end if;
  base := macro_core.tail_risk_slug(new.name);
  candidate := base;
  while exists (select 1 from macro_core.tail_risk where risk_id = candidate) loop
    n := n + 1;
    candidate := left(base, 44) || '-' || n;
  end loop;
  new.risk_id := candidate;
  return new;
end $$;

drop trigger if exists trg_macro_tail_risk_default_id on macro_core.tail_risk;
create trigger trg_macro_tail_risk_default_id
  before insert on macro_core.tail_risk
  for each row execute function macro_core.tail_risk_default_id();

alter table macro_core.tail_risk alter column risk_id set not null;

create unique index if not exists uq_macro_tail_risk_risk_id
  on macro_core.tail_risk (risk_id);

alter table macro_core.tail_risk drop constraint if exists ck_macro_tail_risk_risk_id;
alter table macro_core.tail_risk add constraint ck_macro_tail_risk_risk_id
  check (risk_id ~ '^[a-z0-9]+(-[a-z0-9]+)*$' and length(risk_id) between 3 and 48);

comment on column macro_core.tail_risk.risk_id is
  'The risk''s stable identity, a slug such as iran-oil-chokepoints. Rewording the name does not change it, so its check history stays one line. The desk addresses risks by it; `key` stays the primary key for the vault parser and every existing reader.';
comment on column macro_core.tail_risk.retired_reason is
  'Why the risk was retired. Set by the desk''s explicit retire; null for a risk the weekly note simply stopped listing.';

create table if not exists macro_core.tail_risk_check (
  id          bigserial primary key,
  risk_id     text not null references macro_core.tail_risk (risk_id) on update cascade on delete restrict,
  checked_at  timestamptz not null,
  kind        text not null check (kind in ('weekly', 'daily')),
  severity    text not null check (severity in ('acute', 'elevated', 'watch')),
  summary     text null,
  priced_in   text null,
  gauges      jsonb not null default '{}'::jsonb,
  source_url  text null,
  retired     boolean not null default false,
  run_id      bigint null references macro_quality.ingest_run (id) on delete set null,
  constraint uq_macro_tail_risk_check unique (risk_id, checked_at)
);

comment on table macro_core.tail_risk_check is
  'Every read of every tail risk: the Monday full scan (weekly) and the daily recheck of the elevated and acute ones (daily). gauges is {series_key: {value, display, asof}} as the desk command read it from macro_mart.snapshot_row, never as the model quoted it. retired marks the check that retired the risk, with its reason in summary.';

create index if not exists idx_macro_tail_risk_check_risk
  on macro_core.tail_risk_check (risk_id, checked_at desc);

-- One starting point per live risk, so the history is not empty before the desk's
-- first run. Dated by the note that last listed it.
insert into macro_core.tail_risk_check (risk_id, checked_at, kind, severity, summary, priced_in, source_url)
select risk_id, (last_seen::timestamp at time zone 'UTC'), 'weekly', severity, summary, priced_in, nullif(source_url, '')
  from macro_core.tail_risk
 where retired_on is null
on conflict (risk_id, checked_at) do nothing;

-- ============================================================
-- Views the web reads
-- ============================================================

-- Columns appended at the end, so CREATE OR REPLACE keeps every existing reader.
create or replace view macro_mart.tail_risk_state
with (security_invoker = false)
as
  select r.key, r.name, r.severity, r.summary, r.priced_in, r.assets, r.source_url,
         r.first_seen, r.last_seen,
         r.risk_id,
         r.feed_source,
         c.checked_at as last_checked_at,
         c.kind as last_check_kind,
         p.severity as previous_severity
  from macro_core.tail_risk r
  left join lateral (
    select checked_at, kind
      from macro_core.tail_risk_check
     where risk_id = r.risk_id
     order by checked_at desc
     limit 1
  ) c on true
  left join lateral (
    select severity
      from macro_core.tail_risk_check
     where risk_id = r.risk_id and severity <> r.severity
     order by checked_at desc
     limit 1
  ) p on true
  where r.retired_on is null;

comment on view macro_mart.tail_risk_state is
  'The live tail risks. risk_id is the stable identity; previous_severity is the last different severity a check recorded, so an escalation can be shown. A retired risk leaves this view and stays in macro_core.tail_risk.';

drop view if exists macro_mart.tail_risk_history;
create view macro_mart.tail_risk_history
with (security_invoker = false)
as
  select c.risk_id, r.name, c.checked_at, c.kind, c.severity, c.summary, c.priced_in,
         c.gauges, c.source_url, c.retired
  from macro_core.tail_risk_check c
  join macro_core.tail_risk r on r.risk_id = c.risk_id
  where c.checked_at > now() - interval '180 days';

comment on view macro_mart.tail_risk_history is
  'Every check of every tail risk over the last 180 days, for a risk''s escalation line.';

-- ============================================================
-- 5. Grants and policies
-- ============================================================

alter table macro_core.catalyst_alias enable row level security;
alter table macro_core.tail_risk_check enable row level security;

revoke all on macro_core.catalyst_alias from public, anon, authenticated;
revoke all on macro_core.tail_risk_check from public, anon, authenticated;
grant all on macro_core.catalyst_alias to service_role;
grant all on macro_core.tail_risk_check to service_role;
grant usage, select on sequence macro_core.tail_risk_check_id_seq to service_role;

do $$
declare
  v text;
begin
  foreach v in array array['tail_risk_state', 'tail_risk_history'] loop
    execute format('revoke all on macro_mart.%I from public, anon', v);
    execute format('grant select on macro_mart.%I to authenticated', v);
    execute format('grant select on macro_mart.%I to service_role', v);
  end loop;
end $$;

-- macro_desk: the schemas it touches. USAGE lets it name objects; every table below
-- carries its own grant and, because RLS is on everywhere, its own policy.
grant usage on schema macro_raw, macro_core, macro_mart, macro_quality to macro_desk;

-- Runs: its own only. The source of every desk run starts with 'desk:'.
grant select, insert, update on macro_quality.ingest_run to macro_desk;
grant usage, select on sequence macro_quality.ingest_run_id_seq to macro_desk;
drop policy if exists macro_desk_select on macro_quality.ingest_run;
create policy macro_desk_select on macro_quality.ingest_run
  for select to macro_desk using (true);
drop policy if exists macro_desk_insert on macro_quality.ingest_run;
create policy macro_desk_insert on macro_quality.ingest_run
  for insert to macro_desk with check (source like 'desk:%' and run_kind = 'desk');
drop policy if exists macro_desk_update on macro_quality.ingest_run;
create policy macro_desk_update on macro_quality.ingest_run
  for update to macro_desk
  using (source like 'desk:%') with check (source like 'desk:%' and run_kind = 'desk');

-- Findings: it may open its own, and nothing else.
grant insert on macro_quality.finding to macro_desk;
grant usage, select on sequence macro_quality.finding_id_seq to macro_desk;
drop policy if exists macro_desk_insert on macro_quality.finding;
create policy macro_desk_insert on macro_quality.finding
  for insert to macro_desk with check (check_key like 'desk_%');

-- The raw archive: its own batches, including the shadow ones.
grant select, insert on macro_raw.batch to macro_desk;
grant usage, select on sequence macro_raw.batch_id_seq to macro_desk;
drop policy if exists macro_desk_select on macro_raw.batch;
create policy macro_desk_select on macro_raw.batch
  for select to macro_desk using (source like 'desk:%');
drop policy if exists macro_desk_insert on macro_raw.batch;
create policy macro_desk_insert on macro_raw.batch
  for insert to macro_desk with check (source like 'desk:%');

-- The calendar and its aliases. No DELETE is granted anywhere: a catalyst is retired,
-- never removed, because a trigger clause bound to it is voided and kept.
grant select, insert, update on macro_core.catalyst to macro_desk;
drop policy if exists macro_desk_rw on macro_core.catalyst;
create policy macro_desk_rw on macro_core.catalyst
  for all to macro_desk using (true) with check (true);

grant select, insert on macro_core.catalyst_alias to macro_desk;
drop policy if exists macro_desk_rw on macro_core.catalyst_alias;
create policy macro_desk_rw on macro_core.catalyst_alias
  for all to macro_desk using (true) with check (true);

-- Tail risks and their checks.
grant select, insert, update on macro_core.tail_risk to macro_desk;
drop policy if exists macro_desk_rw on macro_core.tail_risk;
create policy macro_desk_rw on macro_core.tail_risk
  for all to macro_desk using (true) with check (true);

grant select, insert on macro_core.tail_risk_check to macro_desk;
grant usage, select on sequence macro_core.tail_risk_check_id_seq to macro_desk;
drop policy if exists macro_desk_rw on macro_core.tail_risk_check;
create policy macro_desk_rw on macro_core.tail_risk_check
  for all to macro_desk using (true) with check (true);

-- The board `desk context` reads. Views over macro_core run as their owner; the four
-- tables are evaluated read models with RLS on, so each gets a select policy.
grant select on macro_mart.snapshot_row, macro_mart.phase_score, macro_mart.scenario_state,
  macro_mart.drift, macro_mart.phase_call_detail, macro_mart.scenario_set_state,
  macro_mart.tail_risk_state, macro_mart.catalyst_window
  to macro_desk;

do $$
declare
  t text;
begin
  foreach t in array array['snapshot_row', 'phase_score', 'scenario_state', 'drift', 'catalyst_window'] loop
    execute format('drop policy if exists macro_desk_select on macro_mart.%I', t);
    execute format('create policy macro_desk_select on macro_mart.%I for select to macro_desk using (true)', t);
  end loop;
end $$;
