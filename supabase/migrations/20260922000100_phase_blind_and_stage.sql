-- A phase call becomes two readings rather than one.
--
-- `phase` is the call, which knows last week's and may carry it forward. `blind_phase` is
-- what the same data says to a reader given the frameworks and no prior call at all,
-- derived before the carried one is looked at. The Scorecard Ledger already records what
-- happens when only the first exists: for 2026-W32, "the phase call capped the scenario",
-- and the hike's death melted through the chop ceiling that call implied.
--
-- `stage` answers a question the transition tracker cannot. The tracker scores progress
-- toward the NEXT phase, so a Phase 3 that has just begun and a Phase 3 whose internals
-- have deteriorated for six weeks both score 0 of 4 while meaning opposite things.
alter table macro_core.phase_call
  add column if not exists blind_phase  int null,
  add column if not exists blind_reason text null,
  add column if not exists stage        text null,
  add column if not exists stage_reason text null;

alter table macro_core.phase_call
  drop constraint if exists ck_macro_phase_call_stage;
alter table macro_core.phase_call
  add constraint ck_macro_phase_call_stage
  check (stage is null or stage in ('early', 'mid', 'late'));

comment on column macro_core.phase_call.blind_phase is
  'What the data says with no prior call in view. Null for every migrated week, whose journals recorded only the carried call.';
comment on column macro_core.phase_call.stage is
  'Where inside the phase the reading sits. Not the transition tracker, which scores the next phase.';

alter table macro_mart.phase_score
  add column if not exists blind_phase  int null,
  add column if not exists blind_agrees boolean null,
  add column if not exists stage        text null;

comment on column macro_mart.phase_score.blind_agrees is
  'Null when no blind read was recorded. A disagreement is a finding, never an automatic override: the blind read has no memory and memory is sometimes right.';
