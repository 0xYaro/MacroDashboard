-- 20260921000800_quality_tables.sql
-- macro_quality, per docs/CONTRACT.md.
-- Re-runnable: create table/index use IF NOT EXISTS.

create table if not exists macro_quality.ingest_run (
  id           bigserial primary key,
  source       text not null,
  run_kind     text not null check (run_kind in ('full', 'rescore')),
  started_at   timestamptz not null default now(),
  finished_at  timestamptz null,
  rows         int null,
  status       text not null,
  error        text null
);

comment on table macro_quality.ingest_run is
  'Both runs write one row per source.';

create index if not exists idx_macro_ingest_run_source_started_at
  on macro_quality.ingest_run (source, started_at desc);

create index if not exists idx_macro_ingest_run_started_at
  on macro_quality.ingest_run (started_at desc);

-- One sentence per finding, written by the worker, rendered verbatim by the web app.
create table if not exists macro_quality.finding (
  id          bigserial primary key,
  check_key   text not null,
  subject     text not null,
  sentence    text not null,
  severity    int not null,
  opened_at   timestamptz not null default now(),
  closed_at   timestamptz null
);

comment on table macro_quality.finding is
  'Every check lint_journal.py performs becomes a finding with a stable check_key: probabilities summing to 100, the 3-of-4 rule and its score arithmetic, four tracker rows per phase call, three scenarios per set, every scenario target band parseable, the setup check covering every prior-week setup, every active holding carrying a holdings-check line, and a Scorecard Ledger row existing for each graded week. Three checks exist only because the clock is daily: per-series freshness against next_expected rather than wall-clock, condition coverage, and ledger integrity.';

create index if not exists idx_macro_finding_open
  on macro_quality.finding (opened_at desc)
  where closed_at is null;

create index if not exists idx_macro_finding_check_key
  on macro_quality.finding (check_key, opened_at desc);

-- A permanent gap is a point-in-time source that was missed and can never be recovered.
-- Nothing is ever interpolated, and any condition or band that depended on it reports
-- unevaluable.
create table if not exists macro_quality.gap (
  id         bigserial primary key,
  series_id  uuid not null references macro_core.series (id),
  asof       date not null,
  reason     text not null,
  permanent  boolean not null default false,
  opened_at  timestamptz not null default now(),
  unique (series_id, asof)
);

create index if not exists idx_macro_gap_asof
  on macro_quality.gap (asof desc);
