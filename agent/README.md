# agent

The judgment run.
It is the part of the system that a model has to be in, and it is deliberately the smallest such part.

Read `docs/DECISIONS.md` and `docs/CONTRACT.md` first, then `config/series.yaml` and `config/predicates.md`.
None of those four is owned by this directory and none of them is copied into it.

Repository root: `/Users/rayclaw/GreenfieldFund/MacroDashboard`.

## What it replaces

The Obsidian vault's `/journal-research` skill authored a 40KB markdown journal every Monday and a linter parsed it back into JSON.
This package does the same judgment against the database instead, and splits the one weekly run into two, because the two halves run on different clocks.

`narrative` is the daily half.
`author` is the weekly one.

## Layout

```
agent/
  SKILL.md                    the runbook the Claude run follows
  pyproject.toml              uv project, Python 3.12, ruff + mypy strict + pytest
  src/macro_agent/
    config.py                 os.environ only; python-dotenv is loaded in cli.run
    worker_bridge.py          the ONE seam onto worker/src, and the only file that may import it
    model.py                  the vocabulary: what a proposal is before it has been accepted
    prompts.py                the search-agent prompts, ported from gather.workflow.js
    narrative.py              mode A: tier discipline, the retry merge, and what gets written
    context.py                what each mode hands the model before asking it for anything
    validate.py               what a model produced, checked before any of it reaches the database
    preflight.py              R1 to R12 as twelve functions, plus the phase read
    grade.py                  closing the week, using the worker's Brier and its target counting
    authoring.py              writing a week's judgment as `proposed`, and the amendment path
    store.py                  the database (asyncpg): the four gateways and every judgment statement
    cli.py                    the command line both modes are driven through
    desk.py                   `macro-agent desk`: the OpenClaw feeds (docs/DESK.md)
    desk_validate.py          what a desk submission may say, decided without a database
    desk_store.py             every statement the desk sends, on Store's gateways
    desk_cli.py               the `desk` subcommands
    prices.py                 a watched name's price and 24h change, from market data
  tests/
    fixtures/w40_proposal.json   a realistic week, used to measure how much of it can be typed
```

## Running it

```bash
cd /Users/rayclaw/GreenfieldFund/MacroDashboard/agent
uv sync
uv run ruff check
uv run mypy
uv run pytest
```

The database tests create a throwaway Postgres cluster in a temporary directory, apply `supabase/migrations`, use it once and delete it.
They skip when `initdb`, `pg_ctl` and `psql` are not on the path.
Nothing in this package's tests touches the live Supabase project.

The real commands are run from the repository root, so `python-dotenv` finds the environment file:

```bash
cd /Users/rayclaw/GreenfieldFund/MacroDashboard
uv run --project agent macro-agent narrative context --asof 2026-09-18
uv run --project agent macro-agent author context --week 2026-W40 --graded-week 2026-W39
```

## The runner

Every Claude run goes through `macro_core.run_request`, and `macro-agent runner tick` works the queue.
launchd runs it every two minutes from the repository root (`worker/deploy/com.rayclaw.macro-runner.plist`), and launchd never starts a second instance while one is running, so there is only ever one Claude session.

One tick does, in order:

1. Fails any request left `running` for over seventy minutes, which frees the queue after a crash.
2. Applies the oldest decision the operator recorded on the Desk, every activation of it in one transaction, then rescores.
   Nothing else happens on that tick.
3. Schedules what is owed: the `narrative` on a Singapore weekday after 09:40 once the worker's full run has landed, and the `author` on Monday after 10:00 once that day's narrative is `done`.
   A scheduled run that failed is retried once, fifteen minutes after it ended, and then left: a second failure is a finding, not a loop.
   If Monday produces no proposal, the `author` is owed again on Tuesday, after Tuesday's narrative, or without it if that narrative failed twice.
   A run the operator rejected is a decision and is never re-run.
4. Claims the oldest queued request and runs `claude -p` against `SKILL.md` with `--dangerously-skip-permissions`, killed after fifty minutes.

The outcome is read from the database rather than from what the model said.
Judgment rows `proposed` since the claim make the request `proposed` and wait for the operator.
An `amend` that wrote nothing must end on a `NO_CHANGE:` line saying why, or it is recorded as `failed`.
A `narrative` that exits cleanly is `done` and is followed by a worker rescore, because the mart is the worker's and the prose rows would otherwise wait for 21:00.

`MACRO_CLAUDE` and `MACRO_UV` override the two binaries; the tests point them at stand-ins so no test starts a session or touches the live project.

Every session runs on one pinned model, `claude-opus-5-5`, so the calibration record keeps one author; `MACRO_CLAUDE_MODEL` in `.env` overrides it.
The runner names each session itself (`--session-id`) and stores the id on the request before the session starts, so `claude --resume <id>` replays any run, one that was killed included.
It also takes `git status --porcelain` and a content hash of every dirty or untracked path just before and just after the session.
The paths whose content moved are stored in `run_request.repo_changed`, and a non-empty list opens a `run_changed_repo` finding on the Desk: a record for review, not a failure.

## Mode A, `narrative`

**When.** On each Singapore weekday, queued by the runner after 09:40 once the worker's 09:30 full run has landed, and followed by a worker rescore.

**Why it exists.** The worker holds no LLM client, by decision, so without this run the series whose `fetch_path` is `agent_0930` would never be written and every one of them would report `not_connected` for ever.

The set is read from `config/series.yaml` through `Registry.agent_only()` and is never listed in this package's source, because a second list is a second place for a series to move path without both of them knowing.
It has grown and shrunk as series moved to deterministic fetchers, and no test pins the number: what is asserted is that every owned series is covered by exactly one search category, that no category claims a series the worker fetches, and that none is claimed twice.
Those hold whatever the count becomes, and they are what fails loudly when a series is added to the registry and no prompt asks for it.

**What it reads.**

- `macro_core.observation`, for the newest reading each owned series carries.
  This is the sanity block the prompt pastes in, and it is read from the rows rather than from last week's sidecar.
- `macro_core.watchlist_change`, `macro_core.tail_risk` and `macro_core.catalyst`.
  While a feed is `vault` the worker parsed its rows from `Market Watchlist.md`, `Blackswan Events.md` or `Catalyst Monitor.md` on this morning's run; once it is `desk`, `macro-agent desk` wrote the catalyst and tail-risk rows, and nothing writes `watchlist_change` any more (the desk's holdings watch is `macro_core.holding_watch`, which this mode does not read).
  The events agent is told they are already logged so it searches only what they do not cover.
- `config/series.yaml`, for each series' canonical unit and its `value_kind`.

It reads no vault file in this mode.
The three notes the old crons write are the worker's to parse, and re-parsing them here would put two parsers on one grammar.

**What it writes.**

- `macro_core.observation`, one row per series per observation date, upserted on `(series_id, asof)`.
- `macro_core.event`, the dated event log, which nothing in this repository wrote before.
- `macro_raw.batch` with kind `search`, one row per category, so a narrative point can be traced to the text it came from.
- `macro_quality.gap`, permanent, for every owned series that came back with no usable reading.
  Landing is decided on the tier, not on the value: the five prose rows carry `value` null by contract, so a value test would report every one of them missing on a run that fetched all five perfectly.
- `macro_quality.finding`, one sentence per refusal.
- `macro_quality.ingest_run`, one row, `run_kind` `full`.

**What it refuses.**

- A point under a series key this run does not own.
  The old pipeline matched returns by display label and filed a FedWatch reading under the Next FOMC row; here the key is the identity and an unknown one is dropped with a finding, not guessed at.
- A number on a `text` series.
  The five Macro & Fiscal State rows carry `value` null and their sentence in `display`.
  A number stored there could never be read by anything, because a text series may only appear in a `manual` condition.
- A Tier 4 point that carries a value.
  Tier 4 is an empty value and a note naming what to check.
  Never a figure that looks about right, never a zero, never yesterday's reading stamped onto today.
- A tier better than the series' own `tier_floor`.
  An aggregator quote does not become a primary source because a model said so.

**The two stages.** Gather each category in parallel, then re-fetch only the Tier 3 and Tier 4 points against a named fallback source order, keeping a recovered point only when its tier is strictly better than the one it replaces.
The events category has no retry stage, because it carries no data points to recover.
Both behaviours are `gather.workflow.js`'s, unchanged.

**An ETF streak is a signed integer.**
`+2` is two consecutive inflow sessions, `-4` is four consecutive outflow sessions, `0` a session whose net flow was exactly zero.
`streak_intact` needs the integer.
Since 2026-09-24 the worker computes the ETF flows and streaks, and the crypto-flows category asks only for 24-hour liquidations.

## Mode B, `author`

**When.** Monday after 10:00, once the day's narrative is `done`, so that the worker has already graded the setups and recomputed `macro_mart`.
If Monday produces no proposal, the runner owes it again on Tuesday.

**In order.**

0. **Classify the phase fresh.** `author phase-context` hands the run this week's data and the two cycle frameworks with no prior call in it, and `author classify` writes both reads before last week's call is shown.
   Any phase may follow any phase.
   `author context` is refused until the reads exist, and serving it stamps the reveal on them; after that only a one-sentence change note is added.
   `author propose` refuses reads that were never revealed, and `preflight` stamps the reveal itself before it prints the prior.
   Conditions are scored on the worker's evaluation date, the newest session the equity tape carries, unless `--asof` says otherwise.
1. **Grade the prior week.** The scenario grades are judgment and are the run's own.
   The worker deliberately does not machine-assign them, because a machine grade entering the Brier series beside eleven hand-assigned ones would make the whole record incomparable.
   The setups are already graded deterministically by the worker: their outcomes are read, never recomputed.
2. **Append to the ledgers**, and when a miss repeats a pattern, add the week to a `scenario_rule` or propose a new one.
   A rule cannot cite a week nobody graded: the teaching-week foreign key is real, so the `week_grade` row is written first.
3. **Author** the scenario set, setups and strategy, and the change note on each phase read, as `proposed`.
   Running `author propose` again for the same week supersedes the earlier unaccepted proposal (`replaced_earlier_proposal`) rather than adding a second one; an amendment is never replaced this way.
4. **Run the preflight** and surface it: R1 to R12 against the scenario table, and beside them two phase lines per book: the move since last week, and how many of the read's four conditions hold today.
   Neither phase line refuses; a move of two or more steps is a warning for the operator to read against its change note.
5. **Stop.** The operator accepts, which is what moves rows from `proposed` to `active`.

**What it computes rather than authors.**

- `targets_hit` and `targets_n`, by the worker's `grading.count_targets` over states produced by the worker's `evaluate.target_state`.
  `targets_n` is the row count of `macro_core.scenario_target` for the scenario, counting **both** `band` and `manual` kinds.
  `targets_hit` counts only the bands that were measured and held.
- `other_grades`, by the worker's `grading.other_grades`: an array of `{"id", "prob", "grade", "note"}` in scenario order, excluding the base case, whose grading reasoning is `key_miss`.
  The `note` rides on the judgment the authoring run supplies and nothing derives it; a scenario graded without one carries an empty string and the blank is reported.
- The Brier score, by the worker's `calibration.brier`, unchanged since the vault script.
  It takes one argument and there is nowhere to pass it an excuse.
- The base case, by the worker's `grading.week_grade`: the scenario stated at the highest probability, a tie going to the lower ordinal, labelled the way the eleven migrated rows are labelled so the calibration read's S2-habit check keeps working across both.

**What it refuses.**

- A scenario set that does not carry exactly three scenarios summing to 100.
  The database also refuses it, at `COMMIT`, through a deferred trigger; it is checked here as well so that the sentence the operator reads names the three numbers rather than a trigger.
- A typed expression the worker's own evaluator will not resolve.
  Every `expr` and `predicate` is handed to `macro.expr.evaluate` against the live registry before it is written, so an unknown series key, an unlisted predicate, a malformed shape and a numeric comparison against a `text` series all fail on Monday rather than reporting `unevaluable` for ever on a Tuesday night.
- A band target that names no series, and a setup that names no series.
  A setup with a null `series_id` is refused by the worker: no `setup_state` row, no ledger row, and a `setup_series_missing` finding.
  Refusing it here means the operator never sees a proposal that could not have been scored.
- A scenario whose targets are all `manual`.
  A scenario with nothing checkable is not a forecast.
- An equity setup quoted on a front-month future while the cash series exists.
  R1.
- A setup whose invalidation sits on the same side as its target, which has no risk leg and whose planned R:R is not a number.
- An `amends` without an `amend_reason`, or the other way round.
- A phase read that does not carry exactly four conditions with ordinals 1 to 4, or whose four are all manual: a read nothing can check can never be flagged as out of date.
- A phase read with no stage, or one that states last week's phase, a direction or a change note: it is taken with the prior out of view.
- A move of two or more steps whose change note does not name both phases and argue the jump.
- A proposal that restates a phase after the reveal, or a weekly proposal with no fresh read behind it.

**What it will not do, ever.**

- Activate anything.
  `macro-agent accept` exists, is tested, and is the operator's command.
  It is not called by either mode.
- Re-grade a setup.
- Write `macro_core.series`.
  `config/series.yaml` is the one authority and the worker applies it on every run.
- Write into the Obsidian vault.
  Vault files are read only, always, and in mode B only the five framework files are read at all.
- Recompute `macro_mart`.
  That is the worker's, and a second writer would mean the page could show a snapshot from one run beside a condition state from another.
- Reclassify a phase between Mondays, or in an amendment.
  Two failed conditions say the read may be out of date, and the next read is the next Monday's.
- Derive the stage from the conditions.
  The stage is depth within the phase and is authored with its reason.
- Re-price on ordinary daily noise.
  A re-price is an amendment and it needs a material change: a trigger firing in full, a phase condition flipping, a tail risk going acute, or a premise moving.

## The typed-condition rule, which is the point of the whole package

Every condition and trigger clause migrated from the journals is `kind='manual'` with a null expression, because the journals were prose.
A week authored here that carried the same proportion would mean the daily evaluation had nothing to do, and the daily evaluation is what this system exists for.

So: a condition gets a typed `expr` or `predicate` whenever the sentence permits, and `manual` is reserved for what genuinely needs judgment.

The reference week in `agent/tests/fixtures/w40_proposal.json` types 100% of its phase conditions, 79% of its trigger clauses and 88% of its scenario targets, against 0% for every migrated week.
It typed 60% overall on the first pass and 85% once four gaps in `config/series.yaml` were closed: the macro prints, a numeric companion to the prose FedWatch row, `btc_50w_ma`, and bid-to-cover and high yield per auction tenor.

Expressible, and therefore typed:

```jsonc
{"op": "lt", "series": "breadth_spx_above_200dma", "value": 45}
{"op": "gt", "series": "hy_oas_bps", "value": 300}
{"all": [{"op": "gt", "series": "vix", "value": 22},
         {"op": "gt", "series": "move", "value": 90}]}
{"n_of": 1, "of": [{"op": "gt",  "series": "vix", "value": 20},
                   {"op": "gt",  "series": "hy_oas_bps", "value": 300},
                   {"op": "lt",  "series": "breadth_spx_above_200dma", "value": 45}]}
{"predicate": "failed_retest",
 "args": {"series": "spx_cash", "level": 7450, "within_sessions": 5, "direction": "above"}}
```

Not expressible, and therefore `manual`:

```jsonc
{"manual": "Micron confirms HBM pricing on Sep 30"}
{"manual": "a tightening Senior Credit Officer survey"}
{"manual": "a dateless shock: a model-efficiency release, a hyperscaler capex trim"}
```

**There is no auction tail series and none may be added.**
A tail is the high yield less the when-issued yield at the bidding deadline, Treasury does not publish when-issued, and `averageMedianYield` describes the auction's own distribution rather than the gap to a market quoted outside it.
A clause naming a tail is narrowed to a bid-to-cover floor and a high-yield ceiling for the tenor.
Two tests assert the absence, one over the authored clauses and one over the registry itself, because a future reader with a news scrape is exactly who will try to add one.

Two rules go with it and are checked:

- **A trigger clause that names a catalyst is bound to it.** `catalyst_id` is what makes premise-moved detection work: a clause that names a dated event and does not bind it is a clause the Catalyst Monitor can never void, so the scenario keeps standing on an event that has been retired and nothing says so.
- **Every target states its `kind` and, when it is a band, its `series_id`.** Those two are what `targets hit x of n` counts and what a machine can score.

Note the schema's asymmetry, because it is easy to get backwards: a `gt` band carries its bound in `lo` with `hi` null, and an `lt` band carries its bound in `hi` with `lo` null.

## The desk, `macro-agent desk`

The commands the OpenClaw `macro-desk` agent's tools call, so it writes the catalyst calendar, the tail risks and the holdings watch straight into the database instead of into Obsidian notes the worker parses.
`docs/DESK.md` is the tool contract: every command's input and output, with examples.

```bash
cd /Users/rayclaw/GreenfieldFund/MacroDashboard
uv run --project agent macro-agent desk context
uv run --project agent macro-agent desk catalysts list [--status tracked|retired|all] [--within-days N] [--search TEXT] [--full]
uv run --project agent macro-agent desk catalysts submit --json-in items.json [--dry-run]
uv run --project agent macro-agent desk tail-risks list [--all]
uv run --project agent macro-agent desk tail-risks submit --json-in - [--dry-run] < risks.json
uv run --project agent macro-agent desk positions [--dry-run]
uv run --project agent macro-agent desk holdings submit --json-in - [--dry-run] < watch.json
```

- Every command prints one JSON object, when it fails too.
  Exit 0 is everything accepted; 1 is something refused, with one sentence per refusal naming the item and the fix; 2 is an environment that cannot run (no `macro_desk` login, the database unreachable); 3 is a failure after the run started, with nothing from the call applied.
  `docs/DESK.md` has the failure categories.
- The desk is loaded apart from the authoring commands: if its module fails to import, `desk` answers with category `desk_unavailable` and exit 3, and `narrative`, `author`, `preflight`, `accept` and `runner` still work.
- `--dry-run` validates in full, archives the submission and what it would have done as a shadow `macro_raw.batch`, and writes nothing else.
- A live submit is one `macro_quality.ingest_run` (kind `desk`), the archived payload, every surviving item in one transaction, and a finding per refusal.
- The rules are the vault parser's, called through the bridge: a catalyst's identity is `vault.external_key`, its date is `vault.parse_when`, its estimate is `vault.is_estimated`, its category is `families.classify`.
- Numbers never come from the model.
  A tail risk's gauges are read from `macro_mart.snapshot_row`; a holding's price and 24h change are computed by `prices.py` from Yahoo (close to close, last completed session), Hyperliquid or CoinGecko (rolling 24 hours), with `config/symbols.yaml` for non-US listings and tokens.
- It connects with `MACRO_DESK_DATABASE_URL`, the `macro_desk` role, which can reach the feed tables and nothing else.
  Without it a desk command exits 2; an operator may pass `desk --use-database-url` to fall back to `DATABASE_URL` by hand.
  Accounting's positions are read through `macro_core.accounting_open_positions()`, never directly.

| Module | What it holds |
| --- | --- |
| `desk.py` | the commands: context, list, submit, positions, and what each submit does with the stored rows |
| `desk_validate.py` | what a submission may say, decided without a database |
| `desk_store.py` | every statement the desk sends |
| `desk_cli.py` | the `desk` subcommands and their JSON |
| `prices.py` | the price and 24h change of a watched name |

## The seam onto the worker

`worker_bridge.py` is the only file in this package that imports anything from `worker/`.
It puts `worker/src` on `sys.path` at call time; this package declares no dependency on the worker, builds without it, and edits nothing inside it.

Twelve modules are reachable and the list is in the bridge.
Two of them are the reason it exists:

- `macro.calibration`, because the Brier arithmetic must not be reimplemented.
  Twelve graded weeks were scored with it and the series is only comparable if the formula does not move.
  A second implementation is a second formula the day one of them is touched.
- `macro.expr` and `macro.predicates`, because a typed expression authored here has to be evaluated by the evaluator that will actually run it at 21:00.
  Validating it against a lookalike written here would prove that two of our own modules agree, which is not the thing worth knowing.

The desk added four, for the same reason: it writes the rows the vault parser wrote, so it applies the parser's rules rather than a copy.

- `macro.vault` (`external_key`, `parse_when`, `is_estimated`) and `macro.families` (`classify`, `CATEGORIES`): a catalyst's identity, date, estimate and category.
- `macro.clock`: the US session calendar, so a holding's close-to-close change names the last completed session.
- `macro.http`: the one place the Yahoo user agent, the per-host pacing and the 429 breaker are written.
  It is the one module on the list that is not standard-library only; it needs `httpx`, which this package declares.

## Environment

Read from `os.environ`.
`python-dotenv` loads a `.env` at process start inside `cli.run`.

| Variable | Required | Meaning |
| --- | --- | --- |
| `DATABASE_URL` | yes | the Supabase **Session pooler** URI, port 5432. A `:6543` URL is refused at startup. |
| `MACRO_DESK_DATABASE_URL` | for `desk` | the `macro_desk` login, written by `supabase/desk_role.py`. Without it a desk command exits 2, unless the operator passes `desk --use-database-url`. |
| `SYMBOLS_YAML` | no | defaults to `../config/symbols.yaml`: how a held or watched name is priced. |
| `DATABASE_SSL` | no | `require` by default. `disable` exists for a local TCP cluster. |
| `VAULT_PATH` | no | defaults to `/Users/rayclaw/Obsidian/rayobs`. Read only, always. |
| `SERIES_YAML` | no | defaults to `../config/series.yaml`. |
| `PREDICATES_MD` | no | defaults to `../config/predicates.md`. |
| `WORKER_SRC` | no | defaults to `../worker/src`. What the bridge puts on `sys.path`. |
| `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` | no | read and carried, not used to write `macro_core`. See below. |
| `LOG_LEVEL` | no | defaults to `INFO`. |

### Why this package does not use the Data API

`macro_core` is not exposed through the Data API, so PostgREST answers `PGRST106` for every table this package writes whatever key is presented, and a scenario set has to be written whole in one transaction, which PostgREST cannot span at all.

So this package connects over `DATABASE_URL`, exactly as the worker does, and still reads the two Supabase variables so an existing `.env` does not have to change.
`docs/CONTRACT.md` used to say both this and the opposite; the Environment line has been corrected and the Schemas line, which was right, stands.

## Conventions

Restated here because they cannot be imported.

- `Decimal(str(v))` at every edge, and `float` nowhere.
- Null, never zero.
  A figure that cannot be measured is `None` with a sentence beside it, and a null value with no note is refused when the record is built.
- `unevaluable` is not a third kind of false.
  An expression that resolves to `unevaluable` during validation is not a fault; one the evaluator *refuses* is.
- Three calendars, never mixed.
  `Asia/Singapore` decides only when a run happens and never an observation date; `America/New_York` decides the `asof` of a session series; UTC is the crypto day and the clock every stored timestamp is in.
- Every write is an idempotent upsert on a natural key.
  Replaying a run is always safe.
- Every name is fully schema-qualified.
  `search_path` is never set.
- Judgment is never updated in place.
  A new row supersedes the old one and the old one is kept.

## The event log's natural key

`macro_core.event` is declared in `supabase/migrations/0002` and nothing wrote it before this package.
It had no natural key, which made it the one table in the system where a replayed run was not idempotent: a second 09:30 pass on the same day appended the whole log again.

`supabase/migrations/20260921001500_event_natural_key.sql` now declares `uq_macro_event_natural` over `(occurred_on, kind, md5(text))`, and `Store.write_events` upserts on it.
The digest rather than the text, because an event line is long and a btree index over it would sit near the page limit.
