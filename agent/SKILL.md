---
description: Fill the narrative series (fetch_path agent_0930) and the event log after the 09:30 run, and on Monday grade the prior week and author the new judgment as proposed
disable-model-invocation: true
---

# /macro-agent

The judgment run for `MacroDashboard`.
It replaces the Obsidian vault's `/journal-research` skill, and it keeps that skill's discipline while changing what the run writes to.
`/journal-research` authored a markdown journal and a linter parsed it back into JSON.
Here the database is the system of record, the run writes rows, and the prose is a rendering of them.

Two modes, on two clocks.

**`narrative`** runs on each Singapore weekday from 09:40, once the worker's 09:30 full run has landed.
**`author`** runs on Monday after that day's narrative, or on Tuesday when Monday produced no proposal.

Five standing rules.

1. **No HITL except the final review.**
   Never pause to ask permission mid-run.
   The one gate is at the end of `author`, where the operator accepts and rows move from `proposed` to `active`.
2. **Never write a number you could not source.**
   A metric that cannot be sourced is Tier 4: an empty value and a note naming what to check.
   Not a figure that looks about right, not a zero, and not yesterday's reading stamped onto today.
3. **The vault is read only.**
   In `narrative` no vault file is read at all: while a feed is `vault` the worker already parsed its note, and once it is `desk` the desk writes the tables directly.
   In `author` only the five framework files are read.
   Nothing in this system ever writes into the vault.
4. **The database is the contract.**
   `docs/CONTRACT.md` fixes every table, `config/series.yaml` fixes every series and its canonical unit, `config/predicates.md` fixes the four named predicates.
   None of the three is this skill's to change.
5. **Type the condition.**
   Every migrated week's conditions are `manual` with a null expression, because the journals were prose.
   A week authored here that carried the same proportion would mean the daily evaluation had nothing to do.
   See **Typing a condition** below; it is the part of this runbook that matters most.

## Arguments

- **week ID**, optional: the target week, for example `2026-W40`.
  If omitted, the current ISO week.
- **`--asof`**, optional: the observation date.
  For `narrative` on a Monday this is the previous Friday's close.

## The commands

All paths are relative to the repository root, `/Users/rayclaw/GreenfieldFund/MacroDashboard`.
Run from there, so `python-dotenv` finds the environment file.

| Command | Job |
| --- | --- |
| `uv run --project agent macro-agent narrative context --asof D --prev-week W --prev-expectation "..."` | the four search-agent prompts, already rendered, plus what they need |
| `uv run --project agent macro-agent narrative retry --category K --in P --asof D` | the retry prompt for one category's Tier 3 and Tier 4 points |
| `uv run --project agent macro-agent narrative merge --in P --recovered P` | keep a recovered point only when its tier is strictly better |
| `uv run --project agent macro-agent narrative write --in P --asof D` | validate and write the observations, the events, the gaps and the findings |
| `uv run --project agent macro-agent author phase-context --week W` | step one: the blind context for the fresh phase read, with no prior call in it |
| `uv run --project agent macro-agent author classify --week W --in P` | write the two fresh reads as `proposed`, before last week's call is shown |
| `uv run --project agent macro-agent author context --week W --graded-week P` | everything the Monday judgment reads, in one object; refused until the reads exist, and serving it is the reveal (`--amend` for an amendment run) |
| `uv run --project agent macro-agent author grade --graded-week P --in P` | close the prior week: the ledger row, the Brier, the rule weeks |
| `uv run --project agent macro-agent author propose --in P` | write the new judgment as `proposed`, with the preflight beside it; refused until `author context` has run |
| `uv run --project agent macro-agent preflight --in P --text` | walk R1 to R12 on their own, one line per rule, and the phase lines (`--phase P` offline) |
| `uv run --project agent macro-agent accept --table T --id ID --subject asset=equities` | **the operator's command**, not this run's |

Every command either reads and prints JSON, or takes JSON on `--in` and writes.
Never both.
Write the intermediate files under a scratch directory, never into the repository and never into the vault.

## Mode A: `narrative`

Runs after the worker's 09:30 full run has landed: it has fetched every deterministic source and, for each feed still set to `vault`, parsed that feed's note.

### A1. Get the context and the prompts

```
macro-agent narrative context --asof ASOF --prev-week PREV --prev-expectation "<prior synthesis + base case, 1-2 lines>"
```

The return carries `dispatch`, a list of four search agents with their prompts already rendered, and each one's `series` list and JSON schema.
The prompts are the ones `gather.workflow.js` has run every Monday for twelve weeks, carried across with only the changes the new schema forces.
Do not rewrite them.
If one needs changing, change `agent/src/macro_agent/prompts.py` and say so in the report, because the prompt is the only calibration evidence this system has about how a search agent behaves.

The `previous`, `watchlist_deltas`, `blackswan` and `catalysts` blocks are already pasted into the prompts.
They come out of `macro_core`: while a feed is `vault`, the worker parsed it this morning from `Catalyst Monitor.md`, `Market Watchlist.md` or `Blackswan Events.md`; once it is `desk`, the OpenClaw desk wrote the catalysts and tail risks, and nothing writes `macro_core.watchlist_change` any more, so `watchlist_deltas` empties within nine days.
The events agent is told they are already logged and to search only what they do not cover.

### A2. Gather

Dispatch the four agents in parallel, each with its own prompt and its own schema.
Save each return verbatim.

The three data categories are `macro`, `equities` and `crypto-flows`.
The fourth is `events` and it returns a different shape.

The series this mode owns are exactly the ones `config/series.yaml` marks `fetch_path: agent_0930`.
There is no count in this runbook on purpose: the set has moved as series went to deterministic fetchers, and it will move again.
`macro-agent narrative context` returns the current set under `series`, and each category's own list beside its prompt.

Do not return a series another path owns.
`% S&P above 200dma`, HY and IG OAS, the net-liquidity components and now the macro prints are fetched deterministically, and a search agent on any of those would be a second, worse source for a series that already has a Tier 1 one.

Two rows in the `macro` category are easy to get wrong.
`fedwatch_next_fomc` is prose and `fedwatch_hike_prob_next` is the number beside it; both are asked for, both are returned, and neither replaces the other.
`ism_manufacturing` and `ism_services` are searched rather than fetched because FRED's `NAPM` and `NAPMNOI` are discontinued and both answer 404, so there is no keyless series behind them.

### A3. Retry

For each of the three data categories, take the Tier 3 and Tier 4 points and ask for the retry prompt:

```
macro-agent narrative retry --category crypto-flows --in first.json --asof ASOF
```

It returns `null` when nothing was flagged, and `null` for `events`, which carries no data points to recover.
Run the retry agent, then merge:

```
macro-agent narrative merge --in first.json --recovered retry.json
```

A recovered point wins only when its tier is **strictly** better.
A retry that came back at the same tier is not an improvement, and letting it win swaps one unverified source for another and calls the swap progress.

### A3b. Plain the prose rows

The five text series (`fed_funds`, `next_fomc`, `fedwatch_next_fomc`, `tariff_state`, `geopolitical_state`: every `value_kind: text` series on this path) are the Policy state rows, and their `display` comes back in trader shorthand.
Read `config/voice.md`, then rewrite only the `display` of those rows in the merged returns, in that voice.
Move the search agent's sentence to `display_original` on the same point and put the rewrite in `display`.
Touch no other field and no other series.

The rewrite may not add or drop a fact.
Every number, date, name and condition word stays exactly as the original writes it: `3.75%-4.00%` stays `3.75%-4.00%`, `Oct 28` stays `Oct 28`, `only` stays `only`.
Do not add context the search agent did not return.

`narrative write` archives the agent's sentence in `macro_raw.batch` under `display`, as returned, with the rewrite beside it under `plain`.
It prints `Voice:` lines under `voice`; fix what you can and write again.
A rewrite that lost a number is refused, and the agent's sentence is written instead: the refusal names the lost numbers, so put them back and write again.

### A4. Write

```
macro-agent narrative write --in returns.json --asof ASOF
```

`returns.json` is `{"categories": {"macro": {...}, "equities": {...}, "crypto-flows": {...}}, "events": {...}}`, each holding the merged return with the prose rows plained (A3b).

What it writes: `macro_core.observation`, `macro_core.event`, `macro_raw.batch` as kind `search`, `macro_quality.gap` for every series that came back with no usable reading, `macro_quality.finding` for every refusal, and one `macro_quality.ingest_run` row.

What it refuses, and what to do about each:

- **A point under a key this mode does not own.**
  Dropped with a finding.
  If the model returned a real reading under the wrong key, fix the key and re-run.
- **A number on a `text` series.**
  The five Macro & Fiscal State rows carry `value` null and their sentence in `display`.
  The number is dropped and the sentence kept.
- **A Tier 4 point carrying a value.**
  The value is dropped.
  Tier 4 is an empty value and a note.
- **The same key twice in one category.**
  The first is kept.
  This is the shape of the defect that filed a FedWatch reading under the Next FOMC row, and `next_fomc` and `fedwatch_next_fomc` are two series with two cadences.

Read the `refusals` and the `series_missing` lists in the return.
A missing series is a permanent gap: every series this mode owns is `replayable: false`, so a missed session is gone and every condition over that series reports `unevaluable` until the gap leaves its window.
Surface both in the report.

### A5. The event log

`macro_core.event` is the dated log behind section 2, `What Changed This Week`.
Nothing in this repository wrote it before this package.

Each row is one dated bullet: `kind` is `macro` for a macro or geopolitical event and `structure` for an earnings print, a hack, a key level reclaimed or rejected, a flow-streak transition, a narrative shift or a holdings delta.
`surprise` is true when the event diverged from what the previous week expected, graded against that week's headline call and base case rather than against its catalyst list.

Do not re-search the holdings deltas the context carries.
While the holdings feed is `vault`, the Watchlist logged them and the worker parsed them; include the material ones under their date and search only for what they do not cover.

## Mode B: `author`

Runs on Monday, after the 09:30 run and the day's narrative.
If Monday's run fails twice, the runner tries again on Tuesday; everything below is the same on either day.
Six steps, in order, and the last one is to stop.

**Every sentence a reader sees is written in one voice.**
Before writing any prose, read `config/voice.md`, and write every reader-facing field in that voice: plain words, short sentences, answer first, every number and condition word exact.
The reader is a smart friend who is not in finance.
The reader-facing fields are:

- the phase reads: `reason`, `evidence`, `falsifier`, `stage_reason`, every condition's `text`, and each `change_note`;
- the scenario set: `synthesis` (the weekly summary), `probability_check`, each swing variable's `name`, `cool` and `hot`, and `amend_reason`;
- each scenario's `title`, `positioning`, every clause's `text`, and every `manual` target's `text`;
- each strategy's `stance`, `posture`, `amend_reason`, sector-focus `text` and holdings-check `text`, and each setup's `gate_text`;
- the grades: each scenario grade's `note`, `key_miss`, `eq_call` and `crypto_call`.

Band target labels, setup names and styles, `rationale` and `asserted_text` are not shown to a reader and keep their shorthand.
`classify`, `grade` and `preflight` print `Voice:` lines on these fields: a readability grade over 8, a sentence over 25 words, a jargon term with its plain replacement, or an em dash.
They are warnings, never refusals; fix what you can before the write.

**The first step is the phase, and it is taken blind.**
Every Monday each asset is classified fresh, from this week's data and the frameworks alone.
Do not open anything that could name last week's phase before `author classify` has accepted both reads: not the Desk, not the database, not the journals, not `author context`, and not the fixtures under `agent/tests`.

### B1. Classify the phase fresh

```
macro-agent author phase-context --week 2026-W40
```

It returns this week's snapshot, the last twelve weeks of every numeric series (with the 200dma distance and slope where the worker stores them), the catalysts, the tail risks, the series catalogue, and `13 - Crypto Market Cycle Framework` and `14 - Equities Market Cycle Framework` without their transition-tracker sections.
It carries no phase call, no ledger and no rules, and it refuses to print if anything in it would tell you last week's phase.

Read each framework's phase definitions and classify equities and crypto: which of the five phases is this, on this data.
Any phase may follow any phase.
The question is never "what was it last week, and is it still that"; it is "given these internals, these flows, this breadth and this credit, what phase is this".

Write `phase.json`, one read per asset:

```jsonc
{
  "week": "2026-W40",
  "phase_calls": [
    {
      "asset": "equities",
      "phase": 0,                        // 1 to 5
      "phase_name": "...",               // the framework's own name for that phase
      "stage": "early | mid | late",     // where inside the phase this reading sits
      "stage_reason": "one line",
      "reason": "what in the data puts it here",
      "evidence": ["three facts from this week's data"],
      "falsifier": "what reading would put it in another phase",
      "conditions": [                    // exactly four: what must stay true for this phase to hold
        {"ordinal": 1, "text": "...", "kind": "expr", "expr": {"op": "gt", "series": "...", "value": 0}}
      ]
    }
  ]
}
```

The four conditions are the read's own evidence, not steps toward another phase.
Each one holds while the phase holds, and the worker checks them at 09:30 and 21:00; two failing mid-week puts "this week's phase read may be out of date" on the Desk.
Type every one a series covers (the 200dma through `dma200_pct` and `dma200_slope`, breadth, HY and IG spreads, VIX and MOVE, `btc_50w_ma`, funding, ETF flows, F&G); see **Typing a condition**.
A manual condition carries `asserted_status` `met` or `failed`, and four manual conditions are refused.
Say nothing about last week in any field: no `prior_phase`, no `direction`, no "still" or "held".
The read that carries one is refused or warned about.

```
macro-agent author classify --week 2026-W40 --in phase.json
```

It validates both reads, writes them as `proposed`, and fills in last week's phase itself without showing it.
It scores the four conditions on the date the worker evaluates at, the newest session the equity tape carries (printed as `evaluated_at`), not on the calendar date: on a Monday morning that is Friday's close.
Leave `--asof` off unless you have a reason; the default is the worker's date.
It warns when a condition failed on that date: a condition that is already false is not evidence for the phase.
A condition that could not be evaluated (no reading that day) is reported on its own and is not a failure; check the series name if you did not expect it.
Fix a warning by editing `phase.json` and running `classify` again; that is allowed until B2.
If `classify` refuses because this week's reads were already shown last week's call, stop and say so in the final report: the reads already written stand, and rewriting them now would not be fresh.

### B2. Read the context, which is the reveal

```
macro-agent author context --week 2026-W40 --graded-week 2026-W39
```

It is refused until both reads exist, and serving it stamps the reveal on them: from here the reads are fixed.
It returns, in one object: last week's phase calls and this week's reads beside them, each with its move (`same as last week`, or `P3 to P2`); the graded week's scenario set with every clause and every target row; the target rows with their kinds; the setup grades the worker already wrote; the whole Scorecard Ledger; the rolling calibration read; R1 to R12 with their teaching weeks and fire counts; the current snapshot as the worker last wrote it; the catalysts, the tail risks and the holdings; the five framework files; and the catalogue of every series a condition may name, with its canonical unit.

Now write one `change_note` per asset: one sentence on what changed since last week.
A move of two or more steps around the cycle (5 to 1 is one step) names both phases and says why the step between was skipped; one that does not is refused.
Name a phase by number (`Phase 1`) or by the framework's name for it (`Early Bull`, `Wealth Distribution`); either is accepted.
The change note is the only thing about the phase you write after this point.

Read the rest of the frameworks before writing anything else.
`12 - Complete Market Approach`, its `### Net Exposure Heuristics` section only, for the exposure ladder.
`15 - Ray Trading Blueprint`, its `## Trading Setups` section only, for the setup styles.
`cftc-fund-positioning-and-naaim-reference` in full, for the regime bands.

### B3. Grade the prior week

The scenario grades are **yours**.
The worker deliberately does not machine-assign them: `Materialized`, `Partial` and `Not` are read off how a week went against a prose trigger and a table of bands, and a machine grade entering the Brier series beside eleven hand-assigned ones would make the whole record incomparable.

The setups are **not** yours.
The worker graded them deterministically on this morning's run from the tape: the entry, the stop, the target and the realised multiple.
Read `setup_grades` out of the context and report what it says.
Do not re-derive an outcome, and do not disagree with one; if a row looks wrong, that is a finding about the worker, not a grade to overwrite.

Write one grade per scenario, each with its note, then:

```
macro-agent author grade --graded-week 2026-W39 --in grades.json
```

`--asof` defaults to the last session the tape carries inside the graded week, which is what the bands are scored on; pass it only to override that.

`grades.json`:

```jsonc
{
  "scenario_grades": [
    {"scenario_id": "...", "grade": "Partial", "note": "No trigger fired, yet the four target ranges S1 missed all landed here, and junk bonds ended at the top of theirs."}
  ],
  "eq_call": "valid - <one line on how the equities read did>",
  "crypto_call": "valid - <one line on how the crypto read did>",
  "key_miss": "We read the cause right, but stocks and crypto went separate ways.",
  "rule_weeks": [5, 12],
  "new_rules": [{"text": "...", "weeks": ["2026-W39"]}]
}
```

What the command computes and you do not:

- **`targets_hit` and `targets_n`.**
  `targets_n` is the row count of `macro_core.scenario_target` for the base scenario, counting **both** `band` and `manual` kinds.
  Do not count it yourself and do not quote a different number in the report.
  In W37's grading block the same grader counted one scenario's targets including its manual legs and the next scenario's excluding them, and the two conventions contradict each other.
- **The Brier score.**
  The worker's own arithmetic, unchanged since the vault script.
  A scenario whose premise moved is graded normally and the row carries `premise_moved`: the Brier is never forgiven.
- **The base case.**
  The scenario stated at the highest probability, a tie going to the lower ordinal.
- **`other_grades`.**
  An array of `{"id", "prob", "grade", "note"}` in scenario order, built by the worker.
  The base case is **excluded** from it, on purpose: its grading reasoning is what `key_miss` is, so write that reasoning there and not on the base judgment.

**About `note`.**
It is the one-line grading reason, written looking backward at what happened.
In the journal it is the sentence trailing `targets hit 5 of 11`.

It is **not** the scenario's `positioning`, which is forward-looking posture you authored a week earlier.
Putting one in place of the other files last week's intentions in the column that is supposed to explain last week's outcome, and it reads plausibly enough that nobody catches it.

Where you have no note, leave it out or send an empty string.
Nothing derives one, and a generated sentence that reads like a human one is worse than a blank: the ledger's whole value is that its sentences were written by someone who thought about the week, and twelve weeks of them are what R1 to R12 were distilled from.
The command reports the blank rather than filling it.
- **The calibration read.**
  The sentence section 0 used to paste.

`rule_weeks` adds the graded week to a standing rule when the miss repeats its pattern.
`new_rules` adds a rule when a new pattern has now happened twice across the ledger.
Numbers are never reused, and the ledger row has to exist first, because a rule's teaching week is a real foreign key onto a graded week.
Running `grade` again with the same `new_rules` does not add the rule twice: a rule whose text already stands is reported as `already stands` and cites the week.

### B4. Author the rest of the week

One JSON object holding the rest of the week: the two change notes, one scenario set of three scenarios, two strategies, and the setups.
The shape is in `agent/tests/fixtures/w40_proposal.json`, which is a realistic week rather than a minimal one; open it only now, after B1.

Before proposing, run `macro-agent preflight --in proposal.json --text` and fix the `Voice:` lines you can.

```
macro-agent author propose --in proposal.json
```

It attaches the reads written at B1, validates the whole thing first, refuses as one unit if anything fails, and otherwise writes every part as `proposed` and prints the preflight beside it.
It is refused until B2 has run, because the change notes are about a call the run has to have seen.
`--asof` defaults to the worker's evaluation date, as at B1.
Running it again for the same week replaces the earlier proposal (reported as `replaced_earlier_proposal`) rather than adding a second one, so a fix after a refusal or a warning costs nothing.
Nothing is activated.

**Phase calls.**
`phase_calls` here is `[{"asset": "equities", "change_note": "..."}, {"asset": "crypto", "change_note": "..."}]` and nothing else.
The phase was fixed at B1; restating any field of it is refused.
Equities and crypto only: metals, oil, rates and the dollar are snapshot rows and scenario target rows, not phases.

**Scenario set.**
Three scenarios summing to 100.
Each carries its trigger as clauses, its targets, and its positioning.
Asymmetric is fine; a reflexive 40/30/30 is not, and the validator warns about it.

A scenario's trigger is stored only as its clauses, so the split has to be lossless: joining a scenario's clauses in ordinal order with `"; "` must reproduce the trigger as you wrote it.
`required` true means the clause must hold; false means it counts toward the scenario's threshold.
When a sentence does not say, take `false`, which is the weaker claim.

**Setups.**
Every setup names the series its levels are measured on and the sector-focus row it sits under.
Equity setups are quoted on the cash index and crypto setups on BTC or ETH, so `asset` does not determine it, and a Nasdaq setup scored against the S&P produces an entry state, an R multiple and a ledger row that are all wrong and none of them visibly so.
A setup with no series is refused by the worker and never scored.

**Strategy.**
Per book: stance, posture, an exposure band from the ladder in `12 - Complete Market Approach#Net Exposure Heuristics`, four sector-focus rows, and one holdings-check line per active holding.

### B5. Surface the preflight

```
macro-agent preflight --in proposal.json --text
```

One line per rule, R1 to R12, then two lines per book: the read's move since last week, and how many of its four conditions hold on the evaluation date, with the failed and the unevaluable counted apart.
Each line says what it actually looked at, because some rules are only partly mechanisable and a check that quietly narrowed a rule would be worse than no check.
A rule you consciously waived goes in `rules_waived` and is reported as a note naming the waiver, never hidden.

### B6. Stop

Present: the grading, the ledger row with its Brier, the two phase reads with their moves and change notes, the new set, the preflight, every refusal and every warning, every Tier 4 blank from this morning's narrative run, and every rule waived.

Do not run `macro-agent accept`.
The operator accepts, and that is what moves rows from `proposed` to `active`.

## Typing a condition

This is the part that decides whether the twice-daily evaluation does anything.

A condition gets a typed `expr` or `predicate` **whenever the sentence permits**, and `manual` is reserved for what genuinely needs judgment.

Two of these are refused rather than advised, for a new week (an amendment keeps the accepted clauses and is exempt):
every scenario's trigger carries at least one typed clause, and the risk-off branch (the one whose S&P band sits lowest) types its confirming leg on VIX, MOVE, HY, IG or breadth.
A manual clause may still sit beside the typed ones.
`author propose` refuses the set until both hold, naming the scenario.

Expressible:

```jsonc
{"op": "lt", "series": "breadth_spx_above_200dma", "value": 45}
{"op": "gt", "series": "hy_oas_bps", "value": 300}
{"all": [{"op": "gt", "series": "vix", "value": 22},
         {"op": "gt", "series": "move", "value": 90}]}
{"n_of": 1, "of": [{"op": "gt", "series": "vix", "value": 20},
                   {"op": "gt", "series": "hy_oas_bps", "value": 300},
                   {"op": "lt", "series": "breadth_spx_above_200dma", "value": 45}]}
{"predicate": "failed_retest",
 "args": {"series": "spx_cash", "level": 7450, "within_sessions": 5, "direction": "above"}}

// The field accessor: one named key of the observation's `extra`, one level
// deep, no arithmetic. Only `eq` against a text field.
{"op": "eq",  "series": "spx_cash", "field": "dma200_slope", "value": "falling"}
{"op": "lt",  "series": "spx_cash", "field": "dma200_pct",   "value": 0}
{"op": "gt",  "series": "ust_10y",  "field": "spread_2s10s_bps", "value": 25}
{"op": "eq",  "series": "cftc_asset_managers_es", "field": "band", "value": "heavy"}

// The macro prints, the FOMC odds and the auctions are series now.
{"op": "between", "series": "pce_core_mom", "lo": 0.2, "hi": 0.3}
{"op": "lt",  "series": "payrolls_change", "value": 125}
{"op": "between", "series": "fedwatch_hike_prob_next", "lo": 40, "hi": 60}
{"all": [{"op": "gte", "series": "auction_bid_to_cover_10y", "value": 2.40},
         {"op": "lte", "series": "auction_high_yield_30y",   "value": 5.52}]}
```

Four gaps that used to force `manual` are closed, and the reference week went from 60% typed to 85% on the strength of them: the macro prints exist as series, `fedwatch_hike_prob_next` is the number beside the prose FedWatch row, `btc_50w_ma` exists, and every auction serves bid-to-cover and high yield per tenor.
Check the `series` catalogue in the authoring context before writing `manual`.
It is generated from `config/series.yaml` on every run, and it is the only list worth trusting about what exists.

Not expressible, so `manual` with an `asserted_status` and an `asserted_text` saying why:

```jsonc
{"manual": "Rallies fail at prior support"}
```

Four rules go with it.

**The text has to say what the expression checks.**
A framework recipe is often a conjunction with one checkable half: *funding and OI building but not frothy; ETF inflows sustained*.
Do not type the checkable half and leave the text claiming both, because the condition then reports `met` on evidence for half of it.
Narrow the text to what the expression checks, and say in it what the level is.
Lead with plain words, then give the measurable part exactly as the expression checks it: every number, comparison and condition word unchanged (`config/voice.md`, Conditions and triggers).

**Every value is in the series' canonical unit.**
`hy_oas_bps` takes `292`, never `2.92`.
The unit is on every row of the context's `series` catalogue.
A condition comparing `2.70` against `300` is silently false and looks correct.

**A trigger clause that names a catalyst is bound to it.**
Write `"catalyst_id": "CATALYST:<the catalyst's name as the Monitor carries it>"` and the name is resolved to an id when the clause is written.
A name the Monitor does not carry is refused, not dropped.
This binding is what makes premise-moved detection work: a clause that names a dated event and carries no catalyst is a clause the Catalyst Monitor can never void, so the scenario keeps standing on an event that has been retired and nothing says so.

**Every target states its `kind`, and a band names its series.**
`targets_n` counts every row and `targets_hit` counts only the bands that were measured and held, so a band that names no series is a number nothing can ever score.
Note the schema's asymmetry: a `gt` band carries its bound in `lo` with `hi` null, and an `lt` band carries its bound in `hi` with `lo` null.

**There is no auction tail series, and you may not add one.**
A tail is the high yield less the when-issued yield at the bidding deadline.
Treasury does not publish when-issued: it is a dealer quote from a grey market and it is in no feed this system holds.
`averageMedianYield` is in the same feed and is **not** a substitute, because the median of accepted competitive bids describes the auction's own distribution while a tail is the gap between the auction and a market quoted outside it.
Subtracting one from the other gives a number in basis points that would look exactly like a tail and move roughly with one, which is why this is written down in four places rather than left as an empty column.

If you have a news scrape that reports a tail, that is a reading of somebody else's when-issued and it is not a series.
Narrow the clause instead.
`clears without a second record tail` becomes a bid-to-cover floor and a high-yield ceiling for the tenor; `clears cleanly` needs `cleanly` given a number.

**What is genuinely not typeable**, and none of it is a gap a series would close:

- A company print.
  No series carries a guide, and none should: a company's numbers are not a market measurement.
- A survey nobody publishes as a series.
  The Senior Credit Officer survey is the live case, and it is why `a second tailing auction, or a tightening Senior Credit Officer survey` has to be **split into two clauses** rather than typed as one: the auction half is a series and the survey half is not, and typing the pair would make the clause report `met` on half its evidence.
- A dateless structural shock.
  R6 reserves explicit probability for an event no calendar carries, so by construction it can never be a threshold.
- A judgment about character rather than level: `majors begin large impulsive moves`, `disbelief fading`, `lending tightens`.

Report the typed fraction in the final report, broken down by phase conditions, trigger clauses, setup gates and target bands.
`uv run --project agent pytest tests/test_typed_coverage.py -s` prints the same breakdown for the reference week.

## Amending

A material change permits a re-price between Mondays: a trigger firing in full, a phase condition flipping, a tail risk going acute, or a scenario's premise moving.
Ordinary daily noise does not.
An amendment never carries a phase call: the phase is read fresh once a week, on Monday, and "this week's phase read may be out of date" on the Desk is a flag for the operator, not a cue to reclassify.
Start an amendment run from `author context --amend --week W --graded-week P`.
It is refused for a week whose two phase reads do not exist yet: before Monday's author run has read the phase there is no week to amend, and the standing calls it would show are the ones that run has to classify without seeing.
A week whose active calls were carried in by the migration counts as read: no author run is owed for it, so that call is the one to amend.

An amendment is not an edit.
It is a new row carrying `amends` (the id of the row it replaces) and `amend_reason` (the triggering event, in one sentence), written `proposed` through the same `author propose` path as any other judgment.
The row it amends keeps standing until the operator accepts the replacement, and Brier can then be scored against the first statement or the amended one because both are rows.
An amended scenario set rewrites `synthesis` and every swing variable from the data as of the amendment, never copying them from the row it amends: Monday's text describes events still ahead that have since happened.
Write each swing variable as an object with `name`, `cool` and `hot`; the Desk does not render a bare string.

Setting one of `amends` and `amend_reason` without the other is refused.
An `amend_reason` that names none of the four material changes is surfaced as a warning, because the operator has to see that the row they are about to accept does not name one.

## What this skill never does

- Activate anything.
- Re-grade a setup.
- Write `macro_core.series`, which `config/series.yaml` owns and the worker applies.
- Write into the Obsidian vault.
- Recompute `macro_mart`, which is the worker's.
- Nudge a probability on daily noise.
- Return a number it could not source.
- Push to Notion.
  `/journal-research` Phase 7 is dropped and is not carried over.
- Send a notification.
  Neither this skill, the worker nor the runner delivers anything to Telegram, and there is no digest; the dashboard is the surface.
  The OpenClaw desk jobs send their own reports (`openclaw/README.md`), which are theirs and not this run's.
