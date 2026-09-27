-- 20260921000100_schemas.sql
-- Four schemas per docs/CONTRACT.md: macro_raw, macro_core, macro_mart, macro_quality.
--
-- This project is SHARED with Accounting, which already owns the schema names
-- raw, core, mart and quality in the same Postgres database. Nothing in this
-- directory reads, writes, references or assumes anything about those four.
-- Every object below is fully qualified and every name carries the macro_ prefix.
--
-- Re-runnable: every statement is guarded.

create extension if not exists pgcrypto;

create schema if not exists macro_raw;
create schema if not exists macro_core;
create schema if not exists macro_mart;
create schema if not exists macro_quality;

comment on schema macro_raw is
  'Every API response and scraped document as fetched, plus search-agent returns. Not exposed through the Data API.';
comment on schema macro_core is
  'Observed facts, asserted judgment and graded outcomes. Not exposed through the Data API; the worker and the weekly authoring agent reach it with the service-role key only.';
comment on schema macro_mart is
  'Derived read models for the web app, recomputable by re-running the worker evaluate step, plus the two history views. Exposed through the Data API, select-only for authenticated.';
comment on schema macro_quality is
  'Ingest run log, findings and gaps. Exposed through the Data API, select-only for authenticated.';
