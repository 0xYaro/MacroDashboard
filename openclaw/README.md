# OpenClaw macro desk

The OpenClaw side of the dashboard's three agent-written feeds: the catalyst calendar, the tail risks and the holdings watch.
One agent, `macro-desk`, researches with web search and writes only through seven typed tools.
Each tool runs one `macro-agent desk ...` command in this repository (`docs/DESK.md`), which holds the database login and applies the same checks the worker uses.
The approved plan is `.lavish/macro-desk-plan-2026-09-24.html`; the decision is in `docs/DECISIONS.md` (2026-09-24, "OpenClaw writes the three feeds through the desk command" and "The macro desk runs as a locked OpenClaw agent").

This folder is the source of truth.
Nothing under `~/.openclaw` is edited by hand: change a file here and run `openclaw/install.sh`.

## What is here

| Path | What it is | Installed to |
| --- | --- | --- |
| `plugin/macro-desk/` | The OpenClaw tool plugin (`defineToolPlugin`, TypeScript). | `~/.openclaw/extensions/macro-desk/` |
| `desk-rules.md` | The desk rules: tools, sources, dates, submitting, trial mode, numbers, house style. | Appended to every job message (never a file in the workspace) |
| `prompts/*.md` | One prompt per job. | The four cron jobs' messages |
| `install.sh` | Installs or updates all of the above; idempotent; prints each `openclaw` command. | |
| `ROLLBACK.md` | The backup and every way back. | |

## How it fits together

```
cron job (isolated, GPT-5.6 via the Codex app-server)
  -> agent macro-desk: hosted web search, web_fetch, the seven desk tools, nothing else
     -> plugin macro-desk (runs in the gateway process, outside the agent's sandbox)
        -> uv run --project agent macro-agent desk <command> [--dry-run]   (cwd = this repo, JSON on stdin)
           -> Supabase as the macro_desk role (MACRO_DESK_DATABASE_URL from .env)
```

- **The plugin** validates the shape of what the model sent, runs the command as an argv array (no shell; model input only ever reaches the command as JSON on stdin, or as a single `--search=<text>` element), gives the child a minimal environment (none of the gateway's secrets), and returns the command's JSON: `applied`, `refusals`, `warnings`, `voice`.
  A shape problem comes back as refusal sentences without running anything.
  Every tool refuses a field it does not take (`catalysts_list` takes `status`, `within_days`, `search` and `full`); nothing is silently ignored.
  Timeouts: 60 s for reads, 85 s for positions and submits, under the Codex app-server's 90 s limit per tool call.
- **A desk failure reaches the model as a category only**: `{"ok": false, "error": "<category>", "exit_code": N, "next": ...}`.
  The category is the desk's own (`database_unreachable`, `internal`, ...; `docs/DESK.md`) or the plugin's (`timeout`, `could_not_start`, `unreadable_output`, `desk_failed`, `shadow_guard`, `not_configured`).
  The full stderr, credentials redacted, goes to the gateway log (`~/.openclaw/logs/`) and never to the model.
  Failures and refusals carry no `mode` and no `dry_run`, so a failed run never prints the trial line; the prompts print `⚠️ Desk write failed: <category>` instead.
- **Plugin config** (`plugins.entries.macro-desk.config`): `repoPath`, `uvPath`, and `mode`.
  `shadow` forces `--dry-run` on every submit and marks the positions archive as a shadow batch, whatever the model asks, and a last check refuses to run any write in shadow mode whose argv lacks `--dry-run` (`shadow_guard`).
  `live` writes, and still honours a dry run the model asks for.
- **Tool results up to 64,000 characters.** OpenClaw cuts a Codex dynamic tool's result at 16,000 characters unless the agent sets `contextLimits.toolResultMaxChars` (`docs/gateway/config-agents.md`); the full calendar was 62,000 and the model parsed a cut copy.
  `install.sh` sets 64,000 for this agent, and `desk catalysts list` now prints a compact table (about 10,000 characters for its default 45 days; 30,000 for 60 days with `full`).
- **The agent's tool surface is locked twice.**
  OpenClaw's policy (`agents.list[].tools`): profile `minimal`, `alsoAllow` only `read`, `web_search`, `web_fetch` and the seven desk tools, `exec.security: allowlist` with `ask: on-miss`, `fs.workspaceOnly`.
  That makes Codex run `workspace-write` with `network_access: false` and approvals `on-request`.
  But OpenClaw's deny list does not reach Codex's own tools, and that sandbox can still read any file this user can, `.env` files included.
  So `install.sh` also writes the agent's own Codex home (`agents/macro-desk/agent/codex-home/config.toml`, regenerated whole on each install):
  shell and unified exec off; the three global MCP servers (serena, playwright, context7) disabled; apps, plugins, tool suggestions, goals, memories, image generation, browser and computer use off; the skills list and project docs (`project_doc_max_bytes = 0`, `project_root_markers = []`) kept out of the prompt.
  Three tools cannot be switched off by a feature flag with GPT-5.6, because the model catalog turns them on: `apply_patch` (`apply_patch_tool_type`), the sub-agent tools and their "you are /root" prompt (`multi_agent_version`), and `view_image` (image input).
  So the Codex home points `model_catalog_json` at its own copy of Codex's cached catalog with those three fields cleared (`macro-desk-model-catalog.json`, rebuilt from `models_cache.json` on each install).
- **Verified by probe** (`openclaw agent --agent macro-desk --session-id desk-probe-20260925a`, 25 September 01:10 SGT, rollout `2026/09/25/rollout-2026-09-25T01-09-52-...`):
  the direct tools are `exec` and `wait` (Codex code mode), and `ALL_TOOLS` holds only the seven desk tools, `web_fetch`, `session_status` and `heartbeat_respond` (OpenClaw's), `update_plan`, `web__run` (hosted search) and `view_image`, which refuses every call ("not allowed because you do not support image inputs").
  No `apply_patch`, no `collaboration.*`, no goals, no shell, no MCP, no apps.
  Reading a canary file placed beside `~/.openclaw/.env` and beside this repository's `.env` (same owner, mode 600) failed every way: the code-mode JavaScript has no `require`, `import`, `fetch`, `process`, `Deno` or `Bun`; `web_fetch` takes only http and https (and blocks private and loopback hosts); `view_image` refuses; nothing else takes a path.
  The canary text appears nowhere in the rollout.
- **No job-level tool list.** `openclaw cron add --tools ...` switches off Codex's native tool surface and with it hosted web search, leaving a managed `web_search` that has no provider here ("web_search is disabled or no provider is available").
  The agent's policy is the allowlist instead.
- **The rules travel in the message, and nothing else is injected.**
  Codex does inject `AGENTS.md` files: it walks from the workspace up to the nearest git root and loads every one it finds.
  The first install put the workspace at `~/.openclaw/workspace/macro-desk`, inside the main agent's git repository, so every desk run carried the main agent's 9 KB `AGENTS.md` ("You are RayClaw Main ...") and the desk's own copy on top of the copy in the message.
  Now the workspace is `~/.openclaw/desk-workspaces/macro-desk`: outside any git repository, empty, and mode 555, so there is no instruction file a run could rewrite for the next one.
  Project docs are also off twice: `project_doc_max_bytes = 0` in the Codex home, and `--light-context` on each job, which sets the same for the run and keeps OpenClaw's workspace bootstrap out.
  `install.sh` appends `desk-rules.md` to each job's prompt, and that is the only copy of the rules the model sees.
  A run's rollout shows just four injected blocks besides the job message: Codex's sandbox note, OpenClaw's runtime note, the cron collaboration-mode note and the environment context.

## Install and update

```bash
openclaw/install.sh            # files, config and jobs
openclaw/install.sh files      # plugin (npm install, build, validate) and the empty, read-only workspace
openclaw/install.sh config     # plugin entry and allowlist, the agent, its tools and tool-result limit, its Codex home and model catalog
openclaw/install.sh jobs       # create the four jobs, or update prompt, schedule, model, light context and failure alert in place
```

| Variable | Effect |
| --- | --- |
| `DESK_MODE=shadow\|live` | Plugin mode. Unset keeps the current mode (`shadow` on a first install). |
| `DESK_THREAD_ID=<n>` | Telegram topic for all four jobs' reports. Unset leaves an existing job's delivery alone; new jobs get none. Failure alerts never go to a topic (see "The jobs"). |
| `DESK_THREAD_CATALYSTS`, `DESK_THREAD_HOLDINGS`, `DESK_THREAD_TAIL_RISKS` | A topic per feed, overriding `DESK_THREAD_ID`. |
| `DESK_TELEGRAM_TO=<id>` | The group. Unset reads it from the `daily-market-catalyst-monitor` job, so the id never lives in this repository. |

A change to the prompts, `desk-rules.md`, the agent's config or the jobs applies on the next run (a change to the Codex home applies when the agent's Codex app-server next starts).
**A change to the plugin's code needs a gateway restart**, because the gateway imports it once.
Restart only when nothing is running and nothing is due in the next 15 minutes:

```bash
sqlite3 ~/.openclaw/state/openclaw.sqlite "select name from cron_jobs where running_at_ms is not null"
sqlite3 ~/.openclaw/state/openclaw.sqlite "select name, datetime(next_run_at_ms/1000,'unixepoch','localtime') from cron_jobs where enabled and next_run_at_ms < (strftime('%s','now')+900)*1000"
openclaw status | grep Tasks          # 0 active, 0 running
openclaw gateway restart
openclaw gateway status               # Runtime: running, Connectivity probe: ok
openclaw cron status                  # every job still has its next run
```

Plugin checks:

```bash
cd openclaw/plugin/macro-desk
npm install && npm test               # 22 unit tests: argv and stdin, shadow forcing and guard, validation, unknown fields, failure categories, timeouts, env
npm run plugin:validate               # the manifest matches the code
npm run smoke                         # every tool once against the real repo (list flags included), shadow mode, tagged openclaw:macro-desk-smoke
```

## The jobs

All four run as agent `macro-desk`, isolated sessions, `openai/gpt-5.6-sol` with fallbacks `openai/gpt-5.6-terra`, `openai/gpt-5.5`, times in Asia/Singapore, no stagger.

| Job | Id | Schedule | Thinking | Timeout | Replaces |
| --- | --- | --- | --- | --- | --- |
| `desk-catalysts` | `9fe6323f-2c9d-4079-85e5-ffddf39939de` | 08:40 daily | high | 20 min | `daily-market-catalyst-monitor` (08:45, thread 471) |
| `desk-holdings` | `e2feef66-be3d-4f33-839b-5c699e9cd671` | 09:05 daily | high | 30 min | `daily-holdings-watch` (09:15, thread 500) |
| `desk-tail-risks-daily` | `29871a0a-9d5d-4c91-82a5-50a6f58e6a90` | 09:20 daily | medium | 15 min | new: the daily recheck of elevated and acute risks |
| `desk-tail-risks-weekly` | `37bb08ae-4a4f-4254-bf7f-be9a0948fe6f` | Monday 06:10 | high | 30 min | `weekly-market-blackswan-events` (Mon 06:00, thread 686) |

**Delivery is off during the trial.**
The plan was a "Desk trial" topic in the same Telegram group, but the bot is an administrator without the "Manage topics" right, so `createForumTopic` was refused ("not enough rights to create a topic thread").
To turn delivery on: create the topic in Telegram (or give the bot that right), open it, copy the thread id from any message link (`https://t.me/c/<group>/<thread>/<message>`), and run

```bash
DESK_THREAD_ID=<thread> openclaw/install.sh jobs
```

That sets announce delivery to that topic for all four jobs.
Failure alerts are separate and already on: each desk job alerts where the old job for the same feed sends its own failure alerts (the operator's alert chat, not a public topic), after 1 error with a 6 h cooldown, copied from that job by `install.sh jobs`.
Until delivery is on, each run's report is in `openclaw cron runs --id <job id>` and in the agent's session files.

Trigger a run by hand with `openclaw cron run <id>`; follow it with `openclaw cron runs --id <id>`.

## The trial and the comparison

Every submit during the trial is a dry run: the desk validates it against the stored rows, archives it in `macro_raw.batch` with `shadow = true`, and writes nothing else.
Compare the trial with what the old jobs wrote through the vault:

```bash
uv run --project agent macro-agent desk compare --since 2026-09-25          # a plain report per Singapore day
uv run --project agent macro-agent desk compare --since 2026-09-25 --json
```

It reads only, and connects with `DATABASE_URL` because it reads two vault tables the `macro_desk` role cannot (`macro_core.holding`, `macro_core.watchlist_change`).
Per day it lists: catalysts the desk found that the calendar lacks (with a likely duplicate named when one of the same family starts within 3 days), dates that disagree, retirements on either side the other did not make; tail risks that are new, whose severity differs, that the desk retired, or that a weekly scan left unchecked; and the holdings the desk covered (computed moves, material names, drivers, events) beside the vault's holdings list and that day's changes.
The first scheduled trial runs were at 08:40, 09:05 and 09:20 SGT on 24 September, so compare from that day, but leave out the batches written before the trial started, which carry the same origin: ids 692 to 694 (a manual test at 01:55 SGT on the 24th) and 700 to 708 (manual and probe runs between 02:14 and 02:39 SGT on the 24th).
`desk compare` has no option to drop batches yet, so read `--json` and discard those ids, or add an `--exclude-batches` option to `desk_compare.py`:

```bash
uv run --project agent macro-agent desk compare --since 2026-09-24 --json   # then drop batch ids 692-694, 700-708 and 946-950
```

The four verification runs triggered by hand at 01:15 and 01:19 SGT on 25 September, after the review fixes, are also outside the schedule: leave out batches 946 to 950 as well (the smoke script's batches carry their own origin, `openclaw:macro-desk-smoke`, and are left out already).

## Cutover checklist (Phase 3)

Do it after 3 to 5 good trial days, in a quiet window: after the 09:30 worker run and before the next 08:40 desk run.

1. **Judge the trial.** `macro-agent desk compare --since <first trial day>`.
   Every material catalyst the vault added is on the desk's side or explained; date disagreements are the desk being right (check two by hand); the weekly scan left no live risk unchecked; every held name has a computed move or a stated reason, and material names have drivers with specific source pages.
2. **Make the plugin write.**
   `DESK_MODE=live openclaw/install.sh config`, then restart the gateway with the checks above.
   Confirm with the next run: its submit replies carry `"dry_run": false` and a `run_id`, and `macro_mart.feed_freshness` shows `openclaw:macro-desk`.
3. **Point the worker at the desk.**
   The worker's launchd agent (`com.rayclaw.macro-worker`, from `worker/deploy/`) sets only `PATH`, `HOME`, `LOG_LEVEL` and `PYTHONUNBUFFERED`; everything else comes from `worker/.env`, not the repository-root `.env`.
   `macro-worker` calls `load_dotenv()`, which walks up from `worker/src/macro/` and stops at the first `.env` it finds, so the root file is never read by the worker (the first cutover attempt on 30 September appended the lines there and the worker still resolved every feed to `vault`).
   So add these three lines to `worker/.env` (no reinstall of the agent needed; the next 09:30 run reads them), and check with `cd worker && uv run python -c "from dotenv import load_dotenv; load_dotenv(); import macro.config as c; print(c.load().feed_sources)"`:

   ```
   MACRO_FEED_SOURCE_CATALYSTS=desk
   MACRO_FEED_SOURCE_TAIL_RISKS=desk
   MACRO_FEED_SOURCE_HOLDINGS=desk
   ```

   From then on the worker no longer parses the three notes, so it can never retire what the desk wrote.
4. **Turn the old jobs off** (disabled, not deleted):

   ```bash
   openclaw cron disable c9baf20d-0f0f-42d7-9cb2-1ecbe1e85376   # daily-market-catalyst-monitor
   openclaw cron disable 8f11f550-c4b8-483e-b4c0-5add3366a15b   # daily-holdings-watch
   openclaw cron disable f9b7c501-2cea-45d7-9dc7-5be4f265c5f7   # weekly-market-blackswan-events
   ```

5. **Move delivery to the three topics:**

   ```bash
   DESK_THREAD_CATALYSTS=471 DESK_THREAD_HOLDINGS=500 DESK_THREAD_TAIL_RISKS=686 openclaw/install.sh jobs
   ```

   The "Trial run" line at the end of each report disappears by itself: it is printed only when a submit reply had `"dry_run": true`.
   The topics' own chat routing (`channels.telegram.groups.<group>.topics.471/500/686.agentId`) still points at the old agents for @-mentions; leave it until Phase 4.
6. **Update the docs**: `docs/DECISIONS.md` (the cutover date), and the approved plan's Phase 4 (remove the note parsers).
7. **Rollback** is in `ROLLBACK.md`, "After the cutover": point the worker back at the notes, set the plugin back to shadow, re-enable the three old jobs.

## Known gaps

- **The model catalog copy can go stale.** The Codex home uses its own copy of Codex's model catalog (see above); if Codex or OpenClaw is updated and the models change, run `openclaw/install.sh config` to rebuild it, then check a run's tool list again.
  If `models_cache.json` does not exist yet (a new machine), the install warns and `apply_patch`, the sub-agent tools and `view_image` stay on until the agent has run once and the install is repeated.
- **`view_image` is still listed**, though it refuses every call; no Codex setting removes it from the list.
- **The old jobs** still run with what this setup removes: `market-catalyst-monitor` and `market-watchlist` get `danger-full-access` (they inherit the global `exec.security: full`; their rollouts show it), and every Codex agent is offered the global MCP servers unless `mcp.servers.<name>.codex.agents` scopes them (`docs/gateway/configuration-reference.md` in the OpenClaw package); the macro desk saw all three before its Codex home was locked.
  `weekly-market-blackswan-events` carries a job-level tool list, so its `web_search` has had no provider and it has searched through Google News RSS only.
  Turning them off at the cutover closes all of this.
