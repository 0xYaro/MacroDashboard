# Decisions

Append-only.
Never edit an existing entry; supersede it with a new dated one.

## 2026-09-21: The database is the system of record, not the journal

`/journal-research` authors a markdown journal, and `lint_journal.py --sidecar` then parses that markdown back into JSON.
That is a renderer feeding the model, and it is why `journal_parse.py` needs 652 lines of regex over headings.
Here the structured state is canonical and prose is generated from it.
The vault's own `AGENTS.md` already asserted this ("the WMJ Vercel app is the system of record for structured weekly market data"); this repository makes it true.

A worded markdown interpretation of the dashboard will be built later.
Obsidian write-back is deferred; see `FUTURE.md`.

## 2026-09-21: Scope and cadence

Equities and crypto carry phase calls.
Metals, oil, rates and the dollar are snapshot rows and scenario target rows, not phases, exactly as the journal treats them.

The state updates twice a day.
There is no staged rollout: the build targets the complete system, not a subset that is extended later.
The Notion port that `/journal-research` Phase 7 performs is dropped and will not be carried over.

## 2026-09-21: One living state with a weekly commit point

Evidence is daily; judgment is weekly.

The snapshot and every condition derived from it are re-evaluated on each run and carry their own as-of.
The phase call, the scenario set, the setups and the strategy are authored once a week, and between authorings they are only re-scored, never rewritten.
Monday's run closes the week: it grades the prior set, appends the ledger rows, computes the Brier score and opens the new set.

The reason is the calibration record.
Twelve graded weeks and twelve standing scenario rules were distilled from a fixed one-to-two-week prediction horizon.
Re-authoring scenarios daily would destroy the Brier series and allow the goalposts to move after the tape is known.

## 2026-09-21: Who may change a probability

Daily runs compute scenario *state* and never touch a stated probability.
State means which trigger clauses have fired, how many target bands currently hold, and how far each is from breaching.

A probability may be re-priced only on a material change: a trigger firing in full, a phase tracker condition flipping, a tail risk going acute, or a scenario's premise moving.
A re-price is an amendment.
It is a new row superseding the old one, carrying the triggering event, and Brier can be scored against the first statement or the amended one.

The reason is that rules R2, R4, R5, R6, R10, R11 and R12 all exist because probabilities were set badly *once a week, with deliberation*.
Nudging them daily on noise would manufacture exactly the over-anchoring those rules were written to stop.

## 2026-09-21: Hosting, and one Supabase project shared with Accounting

Database and auth: the existing Supabase project `GreenfieldFund` (`rryoahloktazbyakyjif`, AWS ap-south-1, Free plan).
Worker: Python, always on, under a launchd user agent on the operator's Mac mini, started through `uv`, no container.
Frontend: Next.js on Vercel.
Access: Supabase Auth, the same single user as Accounting, public signups disabled.

Accounting already owns the schema names `raw`, `core`, `mart` and `quality` in that project, so this system uses `macro_raw`, `macro_core`, `macro_mart` and `macro_quality`.

A second project was rejected.
The two dashboards may be merged later, and Supabase cannot join across projects: with one project the merge is adding routes to one shell, with two it is a data migration.
Free-plan headroom is not the constraint — the project holds about 1.5 MB against a 500 MB ceiling, and this system's whole series history is well under 100k rows.
The Free plan pauses a project after seven days of inactivity, and Accounting's worker already keeps this one awake, which is a further argument for sharing.
If a limit is reached the plan is upgraded rather than the projects split.

## 2026-09-21: No realtime transport

Accounting departed from GreenfieldDash's "poll, don't stream" rule because Hyperliquid marks move every two seconds.
Macro has no tick.
This repository returns to polling: no websocket, no Supabase Realtime subscription, no `refetchInterval` shorter than a run interval.
Every other front-end rule from both siblings carries over: stale-while-revalidate, dim with as-of, three clocks never collapsed, `hasData: false` renders no children, one fault one owner.

## 2026-09-21: History is append-only in Postgres

Every scenario set, phase call, setup and observation is a row with a validity interval, superseded rather than updated.
Nothing that was once believed is overwritten.

Obsidian was considered for this and rejected.
The amendment model above requires the prior stated probability to be a queryable row, and Brier scored as-stated against as-amended is unreachable from markdown.
The most valuable screen this system can offer is "what did we believe on 16 September, and what changed since" — that is a query, not a note.

## 2026-09-21: Conditions are a narrow typed expression form, not a language

Phase tracker conditions and scenario trigger clauses are stored as typed JSON expressions over named series, composed with `all`, `any` and `n_of`.
Path-dependent conditions use a fixed library of named predicates implemented in the worker, each with a unit test carrying a hand-computed expected value.
Anything that genuinely needs judgment is typed `manual` and is reported as awaiting judgment, never scored.

A general expression language was rejected.
Every added operator is another way to write a condition that cannot be verified by reading it, and the failure mode recorded across R1 to R12 is bad judgment about which variable matters, never insufficient expressiveness.
The `manual` type is the load-bearing part: a tracker that says "3 of 4 machine-checked, 1 awaiting judgment" is trustworthy unattended, and one that silently scores an unevaluated condition as `not yet` is not.

## 2026-09-21: Every series declares its own cadence

The journal's single `Previous (Wxx)` column and its week-over-week move do not survive a daily clock.
Prices are continuous, breadth and ETF flows are daily, CFTC positions are weekly (Tuesday, released Friday), State Street is monthly.

Each series declares its cadence and its release rule, and the delta shown is the delta over *that* cadence.
A fixed "since week open" column is kept beside it so the weekly rendering still has its week-over-week figure.
Every row carries `asof`, `released` and `next_expected`, so a weekly series four days from its next print reports as current rather than stale.

Showing a one-day change on a weekly series would be a fabricated number.
This is the numeric form of the rule both siblings already hold: a producer with no documented staleness convention is never called fresh.

## 2026-09-21: Two runs a day, only one of them expensive

09:30 Asia/Singapore is the full run.
The US close is settled, and the two existing OpenClaw crons have already written at 08:45 and 09:15, so the narrative agents consume their output rather than re-searching the same ground.

21:00 Asia/Singapore is a deterministic re-score.
No LLM: prices, crypto, credit, breadth, every condition, every target band, every setup gate — an hour before the US open.

The expensive tier buys narrative, which does not change twice a day.
The cheap tier buys state, which does, and every daily-cadence outcome this system exists to catch lives entirely in the cheap tier.

## 2026-09-21: FRED is not blocked, and breadth moves off an LLM path

`references/data-contract.md` in the vault skill states that FRED is unreachable at the network level from this machine, and routes four series through search agents because of it.
That is false as of this date: `fredgraph.csv` answers in about 110ms, and Accounting's worker already holds a `FRED_API_KEY`.
HY and IG option-adjusted spreads, WALCL, the TGA and the reverse repo facility are therefore fetched directly, and net liquidity is computed from one source instead of three.

Percent of the S&P above its 200-day moving average is fetched by a deterministic scraper and the series is stored here.
It is not optional: the 21:00 run evaluates setup gates, every gate written to date is a breadth threshold, and an LLM fetch cannot sit on that path.

Everything else keeps working as it does today.
ETF flows and streaks, the CNN Fear and Greed index, 24-hour liquidations and CME FedWatch stay search agents on the 09:30 run.
State Street and the tariff and geopolitical state stay asserted.
Replacing those with scrapers, and computing breadth in-house from constituent closes, are in `FUTURE.md`.
(Superseded in part: 2026-09-23 "CNN Fear & Greed is fetched, not searched", 2026-09-23 "Breadth is computed from the constituents, and S5TH is the cross-check" and 2026-09-24 "ETF flows are daily, fetched, and checked against a second source".)

## 2026-09-21: Setups are evaluated on the close

Every entry, invalidation and gate in the Setup Ledger is already written against closes, and every planned R:R is computed from close levels.
Evaluating intraday would silently change what a recorded R-multiple means and break comparability with the eighteen rows already logged.

A band-touch flag derived from the session high and low is recorded alongside, because "entry band reached, gate refused, trade forgone" is the exact failure that has occurred three times and it should surface the same day.

## 2026-09-21: Migrated history

The Scorecard Ledger (from 2026-W23), the Setup Ledger (from 2026-W35) and scenario rules R1 to R12 are migrated in full and verified by hand.
Sidecars are imported for 2026-W36 to 2026-W39, and 2026-W32 to 2026-W35 are bootstrapped through `journal_parse.py --legacy`.
The 2026-W03 to 2026-W31 archive is not imported.

The calibration read is defined over the last eight graded weeks, so starting empty would silence the probability check until December.
The older journals are already distilled into the ledgers' "Key miss" column and into the rules' teaching weeks, so importing them would add bulk and no signal.

Each rule's teaching weeks are a real foreign key onto graded weeks, so the system can state how many times a rule has fired.

## 2026-09-21: The weekly authoring run stays an agent run

Writing a new scenario set, phase call and setups is judgment informed by twelve weeks of ledger and twelve standing rules, and it stays a Claude agent run.
What changes is that it reads and writes the database instead of markdown.

The single human review gate moves into the app as a lifecycle: `proposed`, `active`, `superseded`.
`lint_journal.py`'s structural checks become database constraints and `macro_quality` findings, and the walk down R1 to R12 becomes a pre-flight the app runs and displays before the operator accepts.

## 2026-09-21: Recovery, backfill and the two kinds of source

Each source persists a cursor and catches up due work on start, computed from each series' declared release rule rather than guessed.

Sources are replayable or point-in-time, and the distinction is structural.
FRED, Yahoo, the Treasury par curve, CoinGecko and the CFTC all serve history, so a missed day is recoverable.
The CNN Fear and Greed index, ETF flows and 24-hour liquidations are point-in-time: a missed day is gone.
(Superseded in part: the CNN index and ETF flows became replayable with their fetchers, 2026-09-23 "CNN Fear & Greed is fetched, not searched" and 2026-09-24 "ETF flows are daily, fetched, and checked against a second source".)

A missing point-in-time reading is recorded as a named permanent gap in `macro_quality`, and any condition or band that depended on it reports unevaluable.
Nothing is ever interpolated, and a prior value is never carried forward into a day it did not describe.

## 2026-09-21: The OpenClaw crons keep running, and their output lands in Postgres

`daily-market-catalyst-monitor` (daily, 08:45 SGT), `daily-holdings-watch` (daily, 09:15 SGT) and the Blackswan cron (weekly, Mondays 06:00 SGT) are not rewritten and keep their Telegram delivery.
(Corrected 2026-09-23: the watchlist cron was misnamed `daily-market-watchlist-update` here, and the Blackswan cron is weekly, not daily.)
They write `Catalyst Monitor.md`, `Market Watchlist.md` and `Blackswan Events.md` in a fixed line grammar their own prompts enforce.
The worker parses those three files on each run and lands catalysts, holdings deltas and tail risks in `macro_core`.

This is a feed, not the Obsidian integration that was deferred; that was journal write-back.
(Superseded by 2026-09-24 "OpenClaw writes the three feeds through the desk command": at the cutover the desk writes the three feeds and the worker stops parsing the notes.)

## 2026-09-21: A scenario whose premise moves is flagged, not rescored

A scenario trigger clause may name a catalyst.
When the Catalyst Monitor retires or reschedules that catalyst out of the scenario's horizon, the clause is marked void with a reason and the scenario is flagged `premise moved`.

The clause is never dropped from its `n_of` and the trigger is never renormalised.
Renormalising would quietly make the scenario easier to satisfy and corrupt the Brier record.

At grading the scenario is scored normally and the ledger row carries a `premise_moved` tag.
The Brier score is not forgiven.
R6 exists because two weeks in ten were driven by an event no calendar carried, so over-anchoring on a dated catalyst is a forecasting error rather than bad luck; forgiving it would remove the pressure that rule applies.
The tag exists so that a rule can later be written from counted evidence rather than from an impression.

## 2026-09-21: No push notifications

There is no Telegram delivery, no digest and no alerting from this system.
The dashboard is the surface, and it is current because the worker runs twice a day.
The two existing crons keep their own Telegram delivery, which is theirs and not this system's.
(Refined by 2026-09-24 "The macro desk runs as a locked OpenClaw agent, in shadow first": after the cutover the desk's OpenClaw jobs carry that delivery; the worker, the runner and the authoring agent still send nothing.)

## 2026-09-21: The cursor is derived, superseding "each source persists a cursor"

The recovery entry above says each source persists a cursor.
It does not.
Each source derives its position from `max(asof)` per series in `macro_core.observation`.

A side table can disagree with what was actually stored, and when it does the source skips a session it never landed, silently.
Deriving the position from the rows themselves makes that disagreement impossible to have.
The rest of that entry stands: catch-up is computed from each series' declared release rule, the replayable and point-in-time split is unchanged, and a missed point-in-time reading is still a permanent gap.

## 2026-09-21: The worker connects over the session pooler, not the Data API

The hosting entry above implies the worker reaches Supabase the way the web app does.
It cannot.
`macro_raw` and `macro_core` are deliberately not exposed on the Data API, so PostgREST returns `PGRST106` for every table the worker writes, and a `macro_mart` flush has to be one transaction, which PostgREST cannot span.

The worker uses `DATABASE_URL` against the Supabase session pooler over asyncpg.
It still holds the two Supabase variables, which the weekly authoring agent uses, since that one does go through the Data API.

No FRED key is needed anywhere: the keyless CSV endpoint serves every series.
FRED allowlists the User-Agent product token, which is the likeliest origin of the vault skill's claim that FRED was blocked at the network level: a custom token is accepted and then never answered, which looks exactly like a firewall.

## 2026-09-21: Migrations are timestamped, because the migration history is shared with Accounting

Supabase records applied migrations in `supabase_migrations.schema_migrations`, which is one table per **database**, not per schema.
Accounting lives in the same project and names its migrations `0001` to `0013`.
This system named its own the same way, so the two collided exactly.

A push would have done two things, and the second is worse than the first.
`0001` to `0007` would have been skipped as already applied, because the remote history holds Accounting's rows under those version strings, so `macro_raw` and `macro_core` would never have been created and every later migration would have failed against schemas that did not exist.
Then this system's `0008` to `0016` would have claimed those version slots, and Accounting's own `0008` to `0013`, which are local and not yet recorded remote, would have been treated as applied and skipped for ever.

The migrations are therefore renamed to Supabase's own convention, a fourteen-digit UTC timestamp.
The sequential scheme was never Supabase's; it was a convention this repository copied from Accounting without noticing that the thing it names is global.

This is a cost of the one-shared-project decision that was not foreseen when that decision was recorded, and it is the kind of cost that decision will keep producing: anything Supabase scopes to the database rather than to the schema is now shared whether or not it should be.
The migration history is the first instance. Database roles, extensions and the `supabase_realtime` publication are the others to watch.
The decision still stands, because a merge remains a routing change rather than a data migration, but a second instance of this class should reopen it.

Nothing had been pushed when this was found.
A dry run showed the skipped range, which is the only reason it was found before rather than after.

## 2026-09-21: This repository applies its own migrations and never uses `supabase db push`

The Supabase CLI cannot manage a database whose migration history it does not wholly own.
With Accounting's versions recorded remotely and absent locally, `db push` refuses, `--include-all` refuses identically, and the only remedy the CLI offers is `migration repair --status reverted`, which rewrites the other product's history.
That is not a remedy; it is the failure in a different place.

So this repository applies its own migrations.
A script reads `supabase/migrations/*.sql` in name order, applies each pending file in its own transaction, and records it in a table this system owns.
`supabase link` stays useful for everything else the CLI does; `db push`, `db pull` and `migration repair` are never run from here.

The alternative was a second Supabase project, and it was rejected for the reason the shared project was chosen in the first place: a merge stays a routing change rather than a data migration, and the operator keeps one login.
What changed is only the assumption that sharing was operationally free.
It is not, and the cost is now known and bounded: one script, one table, and tests against the same local-Postgres harness every other part of this build already uses.

The honest risk is drift.
A hand-rolled migrator fails when somebody applies a change and does not record it, so the ledger table and its tests are the point of the exercise rather than an accessory to it.
A third database-scoped collision should reopen the second-project question; roles, extensions and the `supabase_realtime` publication are the candidates.


## 2026-09-22: The web app is deployed on Vercel from the CLI, not from a git connection

The project is `macro-dashboard` under `0x648w-8030s-projects`, the same scope that holds `accounting`, and its root is `web/`.
`web/` has its own lockfile and no workspace file above it, so it deploys as a self-contained root with nothing hoisted.

The deploy is a CLI upload rather than a GitHub connection because this repository still has no commits.
That is a temporary state and not a preference: a git connection gives preview deployments and removes the local machine from the release path, and it should replace this as soon as the first commit is pushed.
Two consequences follow from the current shape and are worth knowing rather than rediscovering.
`NEXT_PUBLIC_SUPABASE_URL` is set for Production and Development but not Preview, because a preview variable is scoped to a git branch and there are no branches to scope it to.
And nothing redeploys on its own, so a change that is not followed by `vercel deploy --prod` is a change that exists only on this laptop.

The Supabase URL is set as a project variable rather than committed, even though it is not a secret.
The anon key is set the same way and is not held in this repository at any point.

## 2026-09-22: Next is pinned to the maintained 15.x line, upgraded under a security block

Vercel refused the first deploy outright: 15.1.6 is on its blocked-vulnerable list for CVE-2025-29927, the `x-middleware-subrequest` bypass.
The build had already succeeded, so this was a policy gate at the deploy step and not a compile failure.

The block was correct and the vulnerability is worse here than the generic case.
`middleware.ts` is the only authorisation gate in this app, deliberately so: it denies by default and a page added later is protected by existing rather than by somebody remembering a guard.
A middleware bypass is therefore not a partial exposure, it is the whole of the gate.

The move was to 15.5.25, the maintained head of the 15 line, rather than to 16.
A major would have changed the App Router surface underneath two hundred and forty-two tests in the same step as a first deploy, and the two failures would have been indistinguishable.
16 remains open as its own piece of work with its own gate run.

## 2026-09-22: Design tokens are re-ported from Accounting when Accounting moves them

The parity test failed on six shared tokens because Accounting had moved them, not because anything here regressed.
Accounting introduced a numbered surface scale and made `--surface` an alias of `--surface-1`, darkened `--line`, lightened `--muted` and `--faint`, and moved `--sans` onto IBM Plex Sans.

All six were re-ported rather than pinned, because the contract is that shared tokens carry Accounting's values and the stated reason for that contract is a future merge.
Pinning would have preserved today's pixels at the cost of the thing the tokens are for.

`--sans` could not be copied alone.
Its value is `var(--font-plex-sans)` with no fallback, and a `var()` naming an undeclared property is invalid at computed-value time, so the declaration would have been dropped entirely rather than falling through to the next face in the stack.
The `next/font` loader came with it, which is why this repository now carries both Plex faces where it previously carried only mono.

The direction is one-way.
Accounting is the source of these values and this repository follows it; a token invented here stays here and is not pushed back.

## 2026-09-23: A per-item fault is grouped by its explanation, not repeated once per item

Yahoo owns fourteen symbols behind one host.
When the rate-limit breaker in `http.py` opened, every one of the fourteen was refused with the same sentence, and the old join printed that sentence fourteen times inside the `ingest_run` error and fourteen more inside the finding built from it.
The screen rendered one fact twenty-eight times, which is worse than rendering it once badly: an operator scrolling a paragraph of identical clauses stops reading before the clause that differs.

`macro/faults.py` now collects faults under their explanation and lists the subjects once in front of it.
Every subject stays in the sentence.
A grouped sentence that said "and thirteen others" would hide which symbols were lost, and which symbols were lost is the fact the operator is reading for.

The grouping is in the joining and not in the messages, because the messages are right.
`http.py` still says why the breaker opened, and one symbol whose fault differs from the other thirteen still gets its own clause at the front.

## 2026-09-23: The two State Street series each display only their own figure

The journal prints State Street's equity allocation and its Risk Appetite Index in a single snapshot cell.
The migration handed that whole cell to both series, so `state_street_equity_allocation` and `state_street_risk_appetite` both read "57.4% (Aug) (-0.13 pp MoM); Risk Appetite Index +0.36 (Jul +0.18)".

`config/series.yaml` keeps them apart because one is a percentage of assets and the other a standardised index, and no single unit could cover the pair.
A display that states both is the same conflation, moved from the unit to the text, and it defeats the split.

The migrator now cuts the cell at its top-level semicolons before anything reads it.
Parentheses are respected, because "58.0% (Jul) (no new release; +1.42 pp MoM)" is one clause and not two.
The clause naming the index becomes the index's display with its lead-in removed; everything else, including the fixed-income and cash split, stays with the allocation.

`supabase/migrations/20260923000200_state_street_display_split.sql` performs the same cut on the rows already loaded, so the live text is corrected without waiting for a re-load of the seed.

## 2026-09-23: The migration resolves a setup's series from its title, or leaves it null and says so

`macro_core.setup.series_id` has been nullable since the column was added, and the schema comment argued against deriving it because "a Nasdaq setup measured against the S&P would produce an entry state, an R-multiple and a Setup Ledger row that are all wrong and none of them visibly so".
The migration took that as a reason to emit no value at all, so every migrated setup loaded with a null and the worker refused to score the four that are still active.

The argument was against guessing, not against reading.
Every setup written since the Setup Ledger began leads with the instrument in its title, and reading "Nasdaq failed-push fade" as `nasdaq_cash` is not a derivation, it is what the title says.
So the migrator matches the title against a fixed table of instrument names, and that table is scoped by the setup's own asset column, so a crypto setup can never resolve to an equity index.

Nothing is guessed where the title is silent.
A title naming no instrument, or naming two, still resolves to null and now carries a sentence saying which of the two happened.
Six of the twenty-one migrated setups are in that state, all of them from weeks that are already superseded; the entry level is deliberately not consulted, because two indices trading in the same thousands would make that reading wrong and confident at once.

Equity setups resolve to the cash index rather than the future, for the reason R1 exists.

## 2026-09-23: The freshness state keys stay, the words a reader sees change

The seven freshness keys are a contract value shared with the worker and none of them is renamed: `fresh`, `due`, `overdue`, `gap`, `asserted`, `not_connected` and `failed` still travel on the wire, still come out of `macro_mart.snapshot_row.freshness`, and are still what the worker writes.
What changed is the word the operator reads, in `web/lib/freshness.ts` and nowhere else.

`FAILED` was the one that mattered.
A source that did not answer on the last run leaves the previous observation on screen, with its own older as-of beside it, and that figure is still true of the day it describes.
Nothing failed in the sense the word carries, and printing it in red taught the reader that the whole reading was broken whenever one vendor was quiet.
It reads `STALE` now, in amber, with the as-of it is held at beside it, and its tooltip says the source did not answer and the figure is the last one received.

`ASSERTED` became `MANUAL`: hand-asserted data is a provenance note and the old word read as an accusation.
`NOT CONNECTED` became `NOT WIRED`, because the old word read as a network fault rather than as a series nobody has wired yet.
`GAP` became `MISSING DAY`, which is what it is.
`FRESH`, `DUE` and `OVERDUE` are unchanged, and `due` keeps its calm token: a weekly series four days from its next print is current, not late.

The `recoverable` and `permanent` sub-word after a gap is now a tooltip rather than a second chip, and the reason sentence no longer says "holed, unwired", which was an idiom the system had invented for itself.

The header badge no longer reports the worst series.
One silent vendor printed `FAILED` in red beside "The reading · 2026-W39" while forty-nine series were current and every figure on screen was true, which answered a question nobody had asked.
It now states the reading's own condition: green with the evaluation stamp when the mart answered and nothing is in fault, amber `STALE` when it answered and something is, red only when nothing rendered at all.

The counts line under it says "49 of 74 current · 25 not" instead of spelling seven states out.
The seven-way breakdown is the truth and stays one hover away, and it is printed in full inside the expanded health strip; it is not a headline, because a headline that makes the reader do arithmetic before they can trust the screen is read once and then skipped.
Only `fresh` counts as current: a `due` series is inside its cadence and is not a fault, but it is also not a reading taken today, and counting it as current would let a week of missed prints hide behind the word.

## 2026-09-23: The snapshot's slack goes to a trend, and a delta is stated in its value's unit

Four changes to the Snapshot tables, all of them in the rendering layer.
`macro_mart.snapshot_row` is unchanged and the worker is untouched.

**The series column no longer absorbs the table's slack.**
`col.c-series` was `width: auto` inside a table bounded at 1,200px, so on a wide panel the names sat at the far left and the first figure began six hundred pixels away with nothing in between.
The eye crossed a hand's width of empty table on every row to reach the number the row is about.
Every column is now sized to its content and the table fills the panel; a thirty-day sparkline column takes the slack, because it is the one thing on this table that is better for having more of it.

**The sparkline is one read for the page, not one per row.**
`macro_mart.observation_history` was read per series on a disclosure open, and the contract's reason for that stands: every series' full history to draw one sparkline is several thousand rows.
A window is a different question from a history.
`Source.sinceTable` narrows the relation by `asof >= evaluation date - 30 days` at the same seam PostgREST narrows it, which is one request for the whole page and a few hundred rows, and the cost does not grow as the arcs lengthen.
The per-series read stays for the disclosure, which wants the whole arc.
The window is anchored to the newest as-of in the snapshot rather than to the wall clock, so the span a sparkline covers is the span the table above it is dated to.
Nothing is drawn across fewer than two points, which is the doctrine `SeriesChart` was built on and holds more strongly at this size: a single dot in a trend column reads as a flat line, and a flat line is a claim about thirty days that one observation cannot support.

**A delta is stated in the unit and at the magnitude of the value beside it.**
The worker computes every delta as a plain difference of two observations of one series, so a delta is in that series' canonical unit: USD with zero decimals for net liquidity, percentage points for a yield.
The table printed the raw figure, so `-27,855,000,000` sat beside `$5.864T` and the reader had to count digits to learn that the balance sheet had lost twenty-eight billion dollars.
`formatSeriesDelta` reads the value's unit and prints `-$27.9B`, `+$469M`, `+4 bp`.
The suffix is picked from the value's own magnitude, stepping down while the change would round away to nothing in that unit, so a move in the balance sheet is quoted in billions rather than as `-$0.03T`.
`delta_native_label` is trusted where it names a unit of its own: the number is then already a percentage or a point count, and re-reading it as dollars would be a fabricated figure.

**Macro & Fiscal State is a list of standing states, not a table.**
Its five rows are text series, and they were drawn in the numeric grid: a paragraph on the active tariff regime squeezed into a 13ch Value column beside two empty delta cells, at a hundred and sixty pixels of row height.
Four columns of nothing is not an absence of data, it is a table shape that does not fit what it holds.
They render as label, sentence and stated-on date, with the same disclosure every other row has.

Two smaller things follow the same rule, that a cell states one fact.
Where the worker writes two figures in one `display`, separated by a semicolon, the cell keeps the leading one and the disclosure carries the rest under "Also in this reading"; nothing the worker wrote is discarded, and `display` is unchanged.
A row whose as-of equals the evaluation date carries an accent rule on its first cell, so the day's movers are findable in a table where four release rules are all legitimately current at once.

`No insights.` now reads `No read was written for this observation.`
The old wording read as a verdict on the series when the fact is about the record: nothing was written down that day.

## 2026-09-23: `swing_variables` is an array, and the reader was wrong about it, not the data

`macro_core.scenario_set.swing_variables` is declared `jsonb not null default '[]'::jsonb` in migration 0003, and migration 0003's own smoke test writes `[{"name": …, "cool": …, "hot": …}]`.
Every scenario set in the database holds an array.

The web contract read the column with `jsonObject`, which refuses an array, and `readSwingVariables` looked for the list under a `variables` key.
Nothing in the database has ever written that wrapper; the fixtures did, so the fixture path never exercised the real shape and the mismatch survived every gate.
On the live deployment the result was that every row of `macro_mart.scenario_set_history` failed validation and `/history` opened empty behind a banner that blamed the data: "7 rows did not validate and are not shown: row 0: swing_variables must be a json object or null, got an array; …".

The contract now reads the column with a new `jsonBlob`, which accepts an object or an array and refuses a scalar, and `ScenarioSetState.swingVariables` and `ScenarioSetHistory.swingVariables` are typed `unknown` rather than `Readonly<Record<string, unknown>>`.
The blob stays opaque for the reason it always was: this app renders the swing variables, it does not compute over them, so a worker that adds a fourth field to each one should widen a line and never delete a week.
`readSwingVariables` reads both the array and the legacy wrapper, because widening a reader costs nothing and a migration that rewrites stored judgment is not a thing this system does to fix a display bug.
A fixture set now carries the array form so the fixture path exercises what the database actually holds.

The data is not fixed, because the data was never wrong.

## 2026-09-23: A row-level rejection is one line on the history panel, not one line per row

`readReading` prints every rejection whole, joined with semicolons, and that is right for a snapshot of every series, where one bad row is one bad venue.
It is wrong for the judgment record, where a single contract mismatch rejects every week at once: seven identical sentences became a paragraph of banner above an empty page, and the count the operator needed was buried in it.

`/history` now states the count and one short reason, `7 weeks hidden: swing_variables must be a json object or null`, and puts the per-row detail behind a disclosure inside the panel.
Only the row-level banner is rewritten.
An envelope fault and a freshness fault are one sentence each already and both outrank anything a row could say, so both are left exactly as `readReading` wrote them.
Nothing is dropped: every rejection is still on the page, one click away, with its row index and its whole reason.

## 2026-09-23: The judgment surface says one thing once

A UI audit of the Judgment page found the same shape of defect in four places: a fact stated two or three times, in two or three vocabularies, with a footnote explaining how not to misread it.
Each is fixed by removing the repetitions rather than by improving them, and each removal changes something this file or `docs/CONTRACT.md` had previously described.

**The two target denominators are now one line.**
`Bands holding 11 of 13` above `Targets holding 11 of 14`, with a footnote naming the confusion between them as the W36 defect, handed the reader the confusion with the warning attached.
The scenario cell now reads `11 of 14 in band · 1 needs judgment · 2 unevaluable`: the denominator the week is actually graded on, and the split that figure is made of.
`bands_holding` and `bands_total` are still projected and still correct; they are no longer a second figure on screen, because the useful split was never bands against targets but scored against awaiting-a-call against unread.
The footnote's explanation survives as the line's own title.

**A trigger clause is chipped only when it was evaluated.**
Every clause of every scenario wore an `UNEVALUABLE` chip, four per scenario, and the chips were the loudest thing in the column while distinguishing nothing.
An unevaluated clause is now plain text and the scenario carries one faint `n of m need judgment` note beneath its clauses.
`NOT YET` keeps its chip: evaluated and false is a claim, and it is exactly the claim an unevaluated clause must never be read as.

**A phase card opens with the read, not with two absences.**
It opened with bordered `STAGE not authored` and `BLIND READ not recorded` boxes above the sentence that says what the call is, so on every migrated week the first thing on the card was two things nobody had written, drawn at the size of findings.
The order is now the title line, the read, the conditions, the falsifier, with stage and blind read as one small line that is rendered only when at least one of them was authored.
The ledger tile follows the same rule: no stage means no stage strip, and no blind read means no blind line, rather than three hollow dots and the words `blind not recorded`.
A missing blind read is still never drawn as agreement.
It is drawn as nothing, which is the honest rendering of a reading nobody took.
(Superseded by 2026-09-23 "The phase is classified fresh every Monday": the blind read collapsed into the fresh read and its columns were dropped.)

**`IN` wears the met green.**
`docs/CONTRACT.md` says direction colour is used nowhere on a band, and the reason given is sound: green and red mean "this moved in your favour today" on a live book, and a band is a statement about a forecast.
The occupancy chips now read green for `IN`, amber for `NEAR`, muted for `OUT` and the unrated violet for a judgment target, and this is not the thing that rule forbids.
There is no red counterpart: a band that has broken out is muted, not hurt.
Green here is the token a satisfied claim already wears on every condition chip in this app, and a scale that runs green-amber-muted is a gradient of attention, which is what the original rule asked for and what four shades of grey could not deliver.

**Operator copy names no relation.**
`macro_mart.scenario_set_state holds no row, so the synthesis…` is the engineer's half of the sentence, and the operator was left to work out their own.
Every rendered mention of a schema, a relation or a column name on this surface is replaced by what it means: `Synthesis not yet written for W39`, `No phase call is on record`, `names no series · not scored`.
The names stay in the code comments and in this file, where the reader is the person who needs them.

## 2026-09-23: A percent delta is points unless the series is a yield

The registry names `percent` for both a 10Y yield and a breadth share, and the delta formatter had been reading every `percent` move in basis points.
Breadth at `+99 bp` and an equity allocation at `-60 bp` are a yield's vocabulary on figures that are not yields.
The web layer now decides by key: a series whose key carries `ust`, `yield` or `fed_funds` reads its change in basis points, and every other `percent` series reads in percentage points.
The registry unit is unchanged, because the worker's arithmetic is the same either way and the distinction is one of reading, not of storage.

## 2026-09-23: The trend column is the last figure on a snapshot row

The 30-day sparkline first sat between the deltas and the as-of, taking all slack, which put the observation date a hand's width from the number it dates.
It now follows Next print, so value, change, date and next date sit together, and the shape fills whatever width remains to the right.
The sparkline is capped at 560px: past that a month of moves drawn 16px tall reads as a rule rather than a line.

## 2026-09-23: The health line groups stale series by vendor

The source string on a series names the request, `Yahoo chart API (^DJI)`, so grouping by it produced fourteen clauses for one vendor failure.
`sourceFamily` strips the symbol and the API suffix before grouping, and the vendor's own `source_unreadable` finding is folded into that clause rather than stated again.

## 2026-09-23: The worker runs on a schedule, and the page says when it has not

An audit on this date found the worker had run three times, all by hand and all as the rescore; the 09:30 full run, the narrative and the authoring run had never executed.
Every rating on the page was the worker's rating at its last run, and the page, which never re-rates a series, went on saying "43 of 47 current" under a two-day-old stamp.

The launchd agent in `worker/deploy/` is now installed, and a second agent, the runner, ticks the run queue every two minutes.
The web app still never re-rates a series, but it now checks the schedule: once a 09:30 or 21:00 run has passed more than forty-five minutes since the last evaluation, the reading is marked stale and says how many runs were missed.
That is a fact about the schedule, not a threshold invented to colour a row, which is the line the freshness doctrine draws.

## 2026-09-23: Yahoo refuses a browser string, not this machine

The 429s blamed on an IP block from 2026-09-21 were the user agent.
From this machine, in the same minute, Yahoo's chart API answered 200 to the bare `Mozilla/5.0` and 429 to the Chrome/130 and Chrome/140 strings, to `curl` and to `httpx`.
Yahoo is now sent the bare token; TradingView and CoinMarketCap keep the full string; FRED keeps the library default.

Two more faults in the same fetcher were found with it.
A 24-hour contract's "regular" session in Yahoo's metadata is the whole New York day, so the previous day's settled bar was read as still running and every future, metal, energy and dollar series fell a day behind on the 09:30 run.
Only the newest bar can be the running one now.
And the fetcher stored only the newest bar, so a bar Yahoo served null and filled in later could never land: the cursor moved past it.
Each run now re-states every completed bar of the last forty-five days, which also gives the trend column its history.

## 2026-09-23: CNN Fear & Greed is fetched, not searched

Superseding the 2026-09-21 entry that kept it a search agent.
CNN's dataviz endpoint serves the index and its history to a full browser user agent with a cnn.com referer, so the series is deterministic and replayable, and CNN's own later revisions overwrite the migrated tier-2 values.
ETF flows and 24-hour liquidations stay with the narrative until the operator supplies SoSoValue and Coinglass keys; Farside answers only behind a Cloudflare challenge.

## 2026-09-23: The Desk is the first page

The operator reported not being able to get insight from the terminal.
The judgment was a tab behind the snapshot, and the join between them (what has moved against the call, and how close each trigger is) was left to the reader.
The root is now the Desk: drift since the call, the bands closest to changing, the dated events that settle a clause, and the call beneath them.
The snapshot is `/data`, and a new `/catalysts` holds the calendar, tail risks, watchlist moves, the prose policy state and the auction results, which had sat forty rows below the prices.

## 2026-09-23: The call refreshes on request, and the system says when to ask

The operator asked whether the judgment should be reprocessed daily or on demand.
Daily re-authoring stays refused, for the reason the 2026-09-21 weekly-commit entry gives: it destroys the Brier series and moves probabilities on noise.

Instead the worker computes drift on every run: every condition, band and setup gate of the active call is re-evaluated at the set's authoring as-of and compared with now.
The baseline is each item's last measured state within ten days before the anchor, because the evaluator never carries a value forward and a weekly series has no reading on most anchors.
A change counts only between measured states; a series arriving is not a flip.
The four material changes of the 2026-09-21 probability entry weigh 3 each, a setup gate or entry 2, a clause or band 1, capped at 10; any material change, or a score of 6, suggests a refresh.

The Desk's Refresh button queues an amendment run, which proposes and stops.
Accept and Reject record a decision that the runner applies with the same `activate` the `accept` command uses.
This is the first write the web app makes, and it is deliberately two `SECURITY DEFINER` functions that touch one queue row each, never a judgment row.
Monday's author run is still automatic; the button adds a way to ask between Mondays, and every amendment carries the reason it was asked for.

## 2026-09-23: Every Claude run goes through one queue on the operator's Mac

`macro_core.run_request` holds the daily narrative, the Monday author and the operator's refreshes.
The runner schedules the first two itself, after the worker's full run has landed and after the narrative is done respectively, and never runs two sessions at once.
The session is `claude -p` against `agent/SKILL.md` from the repository root, because the full run and the authoring read the Obsidian vault, which exists only on this machine.
A run's outcome is read from the database, not from what the model said: rows `proposed` since the claim make a proposal, and a run that wrote nothing must say `NO_CHANGE` and why, or it is recorded as failed.

## 2026-09-23: Every catalyst that happened is kept, one row per occurrence

The operator wants to look back next year at which catalysts moved markets and which recur.
`macro_core.catalyst` cannot answer that: it is keyed on a hash of the name so a reschedule updates rather than duplicates, which also makes "U.S. monthly options expiration" one row whose date moves on each month, and a passed catalyst is only ever retired.

`macro_core.catalyst_occurrence` is the history beside it, keyed on `(name_key, occurred_on)`.
A live catalyst is archived when it has started and the monitor was still listing it within three days of that day.
The grace covers a missed 09:30 run: the monitor prunes a passed event the next morning, and replaying the monitor's history lost Jackson Hole to three days without a snapshot.
Retirement is not the signal, because the monitor also drops events that were cancelled or rescheduled away, and those did not happen.
A month-precision date is never archived from, because 1 October stands for "late October expected" and is not when anything happened.

Each row carries a recurring `family` from a fixed, ordered table of name patterns, not from a model, so the backfill and every daily run tag the same name the same way.
The table's order matters where names overlap: the Beige Book is often called "pre-FOMC", and a Strait of Hormuz "reopening" is not a Treasury reopening.

The year before the worker began, 1 January to 23 September 2026, was backfilled once: from 30 May out of the vault's git history of `Catalyst Monitor.md`, one snapshot per daily run, and before that out of the weekly journals.
Journal rows are `approximate` unless a later journal confirms the date, and they alone may carry `moved_markets`, because it is what the journal said afterwards.
The operator chose not to measure market reactions from prices.

The same replay found that the vault parser left every FOMC undated.
A line with two Singapore times ("statement ... / 2026-09-17 02:00 SGT, press conference ... / 02:30 SGT") was read from its last SGT part, which carries no date.
The parser now reads the last SGT part that carries a date, and when none does it keeps the line's day and drops its times rather than read a source-zone time as Singapore's.

## 2026-09-23: Breadth is computed from the constituents, and S5TH is the cross-check

Barchart's `S5TH` is published on TradingView around 09:15 UTC the day after the session it describes, and the 09:30 Singapore run is 01:30 UTC.
Declared `us_session` and `replayable: false`, every morning run opened a permanent gap for a session that simply had not been published yet, and the gap stayed open beside the reading the 21:00 run then stored.

Percent of the S&P 500 above its 200-day average is now computed here, which supersedes the `FUTURE.md` entry that deferred it.
TradingView's scanner answers one query for the whole index with every line's close and 200-day simple average, within minutes of the close, and re-serves the two sessions before, so the series is replayable and the 09:30 run has the session it owes.
Each company votes once, through its Class A line, and a session resting on fewer than 480 companies is refused rather than published.
`S5TH` stays as a cross-check, with a finding past three points apart, and as a Tier 2 fallback only for a session nothing else filled.
The two differ by design by about half a point, because Barchart's averages appear to be dividend-adjusted.
History older than three sessions is rebuilt once by `macro-worker --breadth-backfill`, from Yahoo closes with the same arithmetic, and fills only the sessions not already stored.

Separately, a gap is now removed whenever a reading for its date is stored, permanent or not.
`permanent` is a prediction made when the gap opens, and a reading that arrives proves it wrong.

## 2026-09-23: The phase is classified fresh every Monday

The operator's words: reclassify every week, as a fresh classification, not last week's read checked for an upgrade in phase order 1 to 5, because a market can go from 3 to 4 and back to 2 and every snapshot needs a fresh view.

**The read comes before the prior, and the order is enforced, not asked for.**
Monday's run opens with `author phase-context`, which carries this week's board, twelve weeks of every numeric series, the catalysts, the tail risks and the two cycle frameworks, and nothing that names last week's call.
It is searched for the active calls' own sentences and for any numbered phase before it is printed, and refused if either is found.
`author classify` then writes both reads as `proposed`, with last week's phase filled in by the command and not shown.
`author context`, which carries last week's calls, is refused until both reads exist, and serving it stamps `revealed_at` on them; a read may be rewritten before that moment and never after it, and the schema refuses a fresh row revealed before it was classified.
After the reveal the proposal adds one sentence per asset on what changed, and nothing else about the phase.

**Any phase may follow any phase.**
No move is refused for its size and there is no upgrade gating.
A move of two or more steps around the cycle (5 to 1 is one step) must be argued in the change note, naming both phases, and is surfaced to the operator as a warning.

**The blind read collapses into the read.**
A fresh read is what `blind_phase` was meant to be, so the carried-versus-blind pair of 2026-09-22 has nothing left to compare.
(Corrected 2026-09-25: there is no 2026-09-22 entry for that pair; it was added by migration `20260922000100_phase_blind_and_stage.sql` without one.)
The two columns were dropped rather than kept beside the read, because all fourteen rows carry nulls and no call was ever authored under that design.

**The four conditions are what must stay true for this phase to hold.**
They were the four steps toward the next phase, scored against a 3-of-4 upgrade rule; that meaning belonged to the sequential read and goes with it.
Each is typed wherever a series exists, a read with four manual conditions is refused, and the worker scores them at 09:30 and 21:00.
On a fresh call a condition that no longer holds is `failed`, never `not yet`.
Two failed conditions raise `read_stale`, and drift leads with "this week's phase read may be out of date".
Nothing reclassifies on it, and an amendment never carries a phase: the operator wants one fresh read a week, and a mid-week read taken after seeing the standing call would not be fresh.
The fourteen migrated calls are `carried` and keep their journal meaning, labelled on the Desk as the old tracker.

The frameworks' own `Phase Transition Tracker` sections, with their 3-of-4 upgrade rule, are left out of the phase step's context; the vault is read-only to this system, so the notes themselves still state that rule until the operator edits them.

**A Monday is not lost to one failure.**
A failed scheduled narrative or author is retried once, fifteen minutes after it ended.
If Monday never produces a proposal, the author runs on Tuesday after Tuesday's narrative, or without it if that narrative failed twice.
A run that fails has whatever it left `proposed` superseded, so its retry can write fresh reads instead of being refused for ones already revealed.

## 2026-09-24: ETF flows are daily, fetched, and checked against a second source

**Decision.**
US spot BTC and ETH ETF flows are one reading per US session, fetched by the worker on both runs from SoSoValue's keyless `historicalInflowChart` and checked against Farside's per-fund table.
Two new series carry the daily net flow, `btc_etf_net_flow` and `eth_etf_net_flow`.
`*_etf_weekly_flow` and `*_etf_inflow_streak` keep their keys and their meaning and are computed from the daily flows, so all six are `deterministic` and `replayable`, and none is a search agent's any more.

**Why.**
The BTC ETF week is an S1 band and the streak stands under a crypto phase condition, and both stood on a number a search agent found in a news flash at 09:30, once a day.
The entry of 2026-09-23 kept them on the agent until the operator supplied keys; SoSoValue's daily history turned out to need none, and Farside's Cloudflare check passes with `curl_cffi` presenting a browser's TLS handshake, so both reasons fell away.
Over the 300 sessions SoSoValue served on 2026-09-24 the two agreed within 5% or $10M on all but two BTC sessions.

**The rules.**
Tier 1 when both sources carry a session and agree within 5% or $10M, whichever is wider; Tier 2 with an `etf_flow_divergence` finding when they disagree; Tier 2 when only one carries it.
A session a source has not published complete is missing, never zero: Farside shows a session in progress as `-` in every fund and `0.0` in its Total.
The week to date is the sum of the ISO week's sessions up to the asof, withheld while one is missing.
The streak is the signed run of same-signed sessions ending at the asof; a holiday does not break it, and `0` now means a session that netted exactly zero rather than "broke a run without starting one", which the old prose convention could not tell apart from a new run of one.
Each derived reading takes the worst tier of the sessions it stands on.

**What moved with it.**
The crypto-flows search category asks only for 24-hour liquidations; `agent/src/macro_agent/prompts.py` records the change as its seventh.
The two new series sit beside their weekly rows in Flows & Structure, which renumbered every row after them, so `sync_series` now parks a moving row on a negative ordinal before the upsert instead of tripping the non-deferrable `(table_group, ordinal)` key.
`macro-worker --etf-backfill` loads the history once and fills only what is not stored with a value, so the journal-era Friday rows are kept unless `--etf-overwrite` is passed.

## 2026-09-24: OpenClaw writes the three feeds through the desk command

The operator approved the macro desk plan (`.lavish/macro-desk-plan-2026-09-24.html`) with all five recommendations.
The catalyst calendar, the tail risks and the holdings watch stop arriving as Obsidian notes the worker parses at 09:30.
One new OpenClaw agent, `macro-desk`, calls typed tools, and each tool runs `macro-agent desk ...` in this repository (`docs/DESK.md`).
After the cutover the Obsidian notes are no longer written; the old ones stay as an archive.

**The agent holds no credential.**
The command connects as `macro_desk`, a role that can insert and update the feed tables, append its own raw batches, runs and findings, and read the `macro_mart` board and the two holdings lists.
It has no DELETE anywhere and no access to judgment or observations, and every table it touches carries its own `macro_desk` policy because RLS is on everywhere.
The migration creates it without a login; `supabase/desk_role.py` sets one with a generated password, sends the server only a SCRAM verifier, and writes `MACRO_DESK_DATABASE_URL` into `.env`.

**Submissions are explicit operations, not a file.**
A catalyst is upserted, retired with a reason, or renamed; a tail risk is upserted or retired with a reason.
This removes the old failure where one unreadable note retired every row.
A rename keeps the catalyst's id and history and keeps the old key as an alias, so the same event resubmitted under its old name is refused with its current name instead of forking.
A tail risk has a fixed `risk_id` slug, so rewording it keeps its history, and every check is kept in `macro_core.tail_risk_check`.
The five live risks got hand-picked slugs; a row the vault parser inserts gets one from its name by trigger, so the worker keeps working unchanged until the cutover.

**One set of rules.**
The desk calls the vault parser's own `external_key`, `parse_when`, `is_estimated` and `families.classify` through `worker_bridge`, widened to `macro.vault`, `macro.families`, `macro.clock` and `macro.http`.
A structured `when` is rendered into the grammar the Catalyst Monitor wrote and parsed by the same function, so the same words give the same row.
`estimated` is never inferred: the agent must state it, a month or an unknown date is always an estimate, and `false` beside hedging words is refused.

**Numbers never come from the model.**
A tail risk names its gauges by series key and the command records their values from `macro_mart.snapshot_row`.
A holding's price and 24h change are computed by `agent/src/macro_agent/prices.py`: close to close for stocks (the last completed regular session against the one before, from Yahoo with the `Mozilla/5.0` agent), rolling 24 hours for crypto (Hyperliquid, then CoinGecko), the underlying's move for an option.
A change that cannot be computed is null with a reason, never an estimate; on 23 September Yahoo served every US name's 22 September bar empty, and the watch reports that rather than comparing across two sessions.
(Fixed 2026-09-25: when a close the change needs is empty, `prices.py` asks Yahoo's one-day chart, whose `chartPreviousClose` is that session's close, so those moves are computed rather than reported unavailable.)
`holdings submit` records the prices `desk positions` computed in the same run (its archived batch) and ignores any price field the model sends.

**Positions come from Accounting, plus two short lists.**
Accounting only connects to Tiger and Hyperliquid, and its book shares no names with the old Obsidian list, so the operator keeps a manual list for other brokers and a watch list, both edited on the dashboard through four `SECURITY DEFINER` functions that require `auth.uid()`.

**Accounting is read through two functions, not a grant.**
The approved plan named a SELECT grant on `mart.book_position`, `mart.venue_status` and `core.instrument`.
Checked against the live project, a grant alone reads nothing: all three tables have RLS on with a policy for `authenticated` only, so the grant route needs USAGE on two of Accounting's schemas, three grants and three new policies on Accounting's tables.
Instead, `macro_core.accounting_open_positions()` and `macro_core.accounting_venue_status()` are `SECURITY DEFINER` functions owned by the migrating role, which owns Accounting's tables and bypasses their RLS.
They return open non-cash positions and venue freshness and never the `accounts` column, which carries broker account numbers.
Their bodies are dynamic SQL, so they record no dependency on Accounting's columns: Accounting's own migrations can drop or retype a column without tripping over an object of ours, and the desk's read then fails loudly with a sentence.
This touches nothing of Accounting's: no grant, no policy, no object in its schemas, which `supabase/tests/migrate_test.py` asserts by fingerprinting Accounting's policies and ACLs before and after.
It does supersede the contract's line that Accounting is unread by this system: it is read, in exactly these two places.

**Dry runs, and the cutover switch.**
Every submit takes `--dry-run`, which validates against the stored rows, archives the submission and what it would have done as a `macro_raw.batch` with `shadow = true`, and writes nothing else.
The trial week runs that way beside the old crons.
The worker reads `MACRO_FEED_SOURCE_CATALYSTS`, `MACRO_FEED_SOURCE_TAIL_RISKS` and `MACRO_FEED_SOURCE_HOLDINGS`, each `vault` by default; set to `desk`, the worker stops parsing that note, so it can no longer retire what the desk wrote.
Nothing is flipped yet.
Each live desk submission is an `ingest_run` with its feed and origin, and `macro_mart.feed_freshness` gives the page "Catalysts: OpenClaw, 08:51 today", keeping the last good time when a run fails so the page shows the feed stale rather than empty.

**The archive's listing grace keeps working.**
`archive.from_live` keeps an occurrence only if the catalyst was still listed within three days of its date, and it reads `last_seen`.
The vault re-listed every row every morning; the desk submits changes.
So a live catalysts run marks every tracked row as listed that day: the agent read the calendar, and whatever it did not retire is still on it.

## 2026-09-24: The macro desk runs as a locked OpenClaw agent, in shadow first

**Decision.**
The desk commands are reached from OpenClaw through a tool plugin, `macro-desk`, and one agent, `macro-desk`, with four cron jobs: catalysts 08:40, holdings 09:05, the daily tail-risk recheck 09:20, the weekly tail-risk scan Monday 06:10.
Everything OpenClaw needs lives in `openclaw/` and is installed by `openclaw/install.sh`; `openclaw/README.md` has the layout, the jobs and the cutover checklist, and `openclaw/ROLLBACK.md` the way back.
The plugin runs in `shadow` mode until the cutover: it forces `--dry-run` on every submit whatever the model asks, so the trial fills `macro_raw.batch` with shadow rows and nothing else.
`macro-agent desk compare --since <day>` lines those rows up against the vault-fed tables per Singapore day.

**Why a plugin and not the shell.**
The plugin runs the repository command from the gateway process as an argv array, with the model's input only on stdin, a minimal environment and a timeout under the Codex app-server's 90 seconds per tool call.
The agent never holds a credential, and a submission that is the wrong shape is refused with sentences before anything runs.

**The sandbox was not as locked as the plan assumed.**
`tools.exec.security: allowlist` does give Codex `workspace-write` with `network_access: false`, as planned (checked in the rollouts).
But that sandbox still reads every file this user can: a probe under Codex's own seatbelt read this repository's `.env`, `~/.openclaw/.env` and the Telegram bot token.
And every Codex agent is also offered the global MCP servers (playwright with network access, serena with file tools, context7) and Codex apps, none of which the OpenClaw tool policy governs.
So the agent's own Codex home (`~/.openclaw/agents/macro-desk/agent/codex-home/config.toml`) switches off the shell, the three MCP servers, apps, image generation, browser and computer use.
Nothing global changed, and no other agent is affected.
A disabled MCP entry needs a `command`: without one Codex rejects the table and hosted web search silently disappears with it.
`allowlist` with `ask: off` is not an option: the Codex harness refuses to start a turn under it.

**No job-level tool list.**
`cron add --tools` switches off Codex's native tool surface, and with it hosted web search, leaving a managed `web_search` with no provider configured.
The first trial run found search "disabled"; the old `weekly-market-blackswan-events` job has run that way, on Google News RSS alone.
The agent's tool policy is the allowlist instead, and hosted search works.

**The desk rules travel in each job's message.**
The Codex runtime does not inject the workspace `AGENTS.md` into a cron turn, and with no shell the agent cannot read it, so `install.sh` appends `openclaw/workspace/AGENTS.md` to every prompt.
(Superseded by 2026-09-25 "The review fixes across the system": Codex does inject `AGENTS.md` files, the rules moved to `openclaw/desk-rules.md`, and the workspace holds none.)

**No trial topic yet.**
The bot is an administrator without the right to manage topics, so it could not create the "Desk trial" topic; the jobs deliver nowhere until the operator creates one and runs `DESK_THREAD_ID=<id> openclaw/install.sh jobs`.
Each run's report is in `openclaw cron runs`.

**Found in the first runs.**
`desk holdings submit` records every watched name on each call, so the reply's advice to resend only a refused item would erase the other names' drivers once live; the holdings prompt resends the whole list until the command keeps a driver a later call does not mention.
(Fixed 2026-09-25: `desk holdings submit` now merges per day and name, so a name a resend leaves out keeps its row, and the prompt resends only the refused items.)

## 2026-09-25: The review fixes across the system

A review of the whole system on this date found faults in every part; they were fixed the same day, and this entry records what changed rather than restating each file.

**Database.**
`20260925000100_phase_history_fix.sql`: a phase call that was never activated (rejected, cleared after a failed run, or replaced by a second proposal) is no longer counted as history in `phase_call_detail`'s prior week or unbroken run.
`20260925000200_desk_hardening.sql`: Accounting's venue reasons and position names are redacted inside the definer functions (`redact_account_ids`), so no caller receives an account number or a wallet address; the tail-risk slug trigger reuses a stored id and never produces one its check refuses; the `macro_desk` policies are narrowed to rows it wrote as `openclaw:macro-desk`, with `desk_confirm_catalysts_listed` as its one write to other rows; its sessions get lock and idle-transaction timeouts; every `source_url` on the feed tables must be http(s); the holdings-list functions refuse in plain sentences; and `holding_watch.no_clear_reason` lets a material move stand without a driver when nothing sourced explains it.

**The authoring agent.**
One `jsonb` codec on every pooled connection, so a `jsonb` column always reads back as the value it holds.
`author propose` run twice for a week supersedes the earlier unaccepted proposal instead of leaving two, and the runner applies an accepted proposal in one transaction.
Conditions are scored on the worker's evaluation date, the newest session the equity tape carries, not the calendar date.
The reveal is enforced at every door: `author propose` refuses reads that were never revealed, `preflight` stamps the reveal before it prints the prior, and an amendment context is refused before the week's reads exist.
The desk is loaded apart from the other commands, so a broken desk module answers `desk_unavailable` and leaves the authoring commands working.

**The desk.**
`desk holdings submit` merges per day and name, so a resend of one fixed item erases no other name's driver; `desk catalysts list` prints a compact table with `--within-days`, `--search` and `--full`; failures carry a category and exit 2 or 3.

**The worker.**
Every re-stated Yahoo bar carries its own 200-day read.
Drift's baseline is the last US session that closed before the set was activated, and a retired tail risk is never counted as going acute.
Findings are synced once, after Monday's grading, and a run closes only findings whose check it evaluated again, so the rescore no longer closes a vault, grading or authoring finding it cannot raise.
Breadth never re-states an older session from a partial set of companies, and an ETF session that is Tier 2 only for want of its cross-check never replaces a stored Tier 1 row.

**The web app.**
A write the database refuses shows its plain sentence, never a raw Postgres error; an agent-written URL is a link only when it is http(s); a late feed says so in words; a material move with no clear reason says so, with no link; one date format everywhere; glossary popovers open one at a time.

**OpenClaw.**
Codex does inject `AGENTS.md` files, walking up to the nearest git root, and the first workspace sat inside the main agent's repository, so every desk run carried that agent's instructions.
The workspace is now `~/.openclaw/desk-workspaces/macro-desk`, outside any repository, empty and read-only; the rules live in `openclaw/desk-rules.md` and are appended to each job's message; Codex project docs are off (`project_doc_max_bytes = 0` and `--light-context`); and the agent's Codex home points at its own copy of the model catalog with the fields that turn on `apply_patch`, the sub-agent tools and image input cleared.
`openclaw/README.md` has the probe that verified it.

## 2026-09-27: A trigger must be visible to the daily run, and every Claude run leaves a trail

The weekend Refresh (run 5) re-priced W39 on "S2's trigger fired in full" while the Desk showed S2 unfired and drift said nothing was material.
Its numbers were right, but every one of W39's nine conditions and clauses was `manual`, so only the model could say whether the trigger fired.
The audit is `.lavish/audit-2026-09-27.html`; the operator chose three changes.

**A new week's trigger is typed.**
`author propose` refuses a scenario whose trigger has no typed clause, and a risk-off branch whose confirming leg (VIX, MOVE, HY, IG or breadth) is not a typed clause.
This is R4 made a refusal for new weeks.
Manual clauses stay allowed beside typed ones.
An amendment is exempt: it is graded against the statement the operator accepted, and re-typing that statement mid-week would change what the week is scored on.

**The model is pinned.**
The runner passes `--model claude-opus-5-5`, overridable with `MACRO_CLAUDE_MODEL`, so a changed CLI default cannot silently change who writes the call.

**Each run is recorded against the repository.**
The runner passes `--session-id` and stores it with `git status --porcelain` before and after the session (`20260927000100_run_request_audit.sql`).
Paths whose content changed go to `repo_changed` and open a `run_changed_repo` finding.
A run that edits code is recorded, not failed: SKILL.md lets a run fix its own prompt, and the point is that the edit can be seen and reverted.
The repository got its first commit the same day, so the before and after have a base to diff against.

**Not done: catching up a missed 09:30 run.**
launchd skips a calendar job missed while the Mac is powered off.
The operator re-runs a missed morning by hand and asked for no automatic catch-up.

## 2026-09-28: FedWatch hike odds are computed from fed funds futures

`fedwatch_hike_prob_next` was a search agent reading news that quoted CME FedWatch, because CME's page is blocked.
The number arrived second-hand and late (73.5% "as quoted Sep 24" was still the Desk's reading on the 28th), and a search agent that fails returns a plausible number instead of a fault, which `docs/FUTURE.md` names as the reason to replace these.
FedWatch is arithmetic over CBOT 30-day fed funds futures, which Yahoo serves keyless, so the worker now does that arithmetic (`worker/src/macro/fetchers/fed_funds_futures.py`) on every US session, Tier 1, replayable.
It agrees with CME's own figure to within a few points, not to the decimal (69% against a quoted 73% on 24 Sept), and the source string says it was computed.
The key, unit and meaning are unchanged, so every condition already written on it keeps working.
The prose `fedwatch_next_fomc` row stays with the narrative run.

The rest of the FUTURE.md entry was reviewed the same day and left as it is.
ISM is monthly and its report pages are not a stable API; State Street's monthly figures have no reachable source; liquidations need a Coinglass key the operator has not issued.

