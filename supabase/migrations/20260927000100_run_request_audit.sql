-- 20260927000100_run_request_audit.sql
-- What an unattended Claude run did to the repository, recorded on its request.
--
-- The runner starts `claude -p` with permissions skipped, and SKILL.md lets a run
-- change agent/src/macro_agent/prompts.py when a prompt needs it. On 2026-09-23 the
-- first narrative run did that and more ("made the agent test databases start
-- without a locale; nothing is committed"), and nothing kept a record of which
-- files it touched or where its transcript was. docs/DECISIONS.md 2026-09-27:
--
--   claude_session_id   the id the runner hands `claude --session-id`, so the full
--                       transcript is `claude --resume <id>` from the repo root;
--   repo_before/after   `git status --porcelain` just before and just after the
--                       Claude session (not the rescore that follows it);
--   repo_changed        the paths whose content differs between the two, tracked
--                       or untracked-but-not-ignored. A non-empty list also opens a
--                       `run_changed_repo` finding: a record, not a failure.

alter table macro_core.run_request
  add column if not exists claude_session_id uuid null,
  add column if not exists repo_before text null,
  add column if not exists repo_after text null,
  add column if not exists repo_changed text[] null;

comment on column macro_core.run_request.claude_session_id is
  'The Claude Code session the runner started for this request; `claude --resume <id>` from the repo root replays its transcript.';
comment on column macro_core.run_request.repo_changed is
  'Repository paths whose content the Claude session changed (null until the run ends; empty when it changed nothing).';
