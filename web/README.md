# web/

The Next.js front end of the regime read: forty-seven series, two phase calls, three scenarios and a week's setups, visible to exactly one logged-in operator.

Read `docs/DECISIONS.md` and `docs/CONTRACT.md` first.
`CONTRACT.md` is binding and defines every relation this app reads.
This README only covers how to run and deploy what those two describe.

## What it shows

Three routes.

`/macro` is one reading with two named views, walked by `[` and `]`.

The split is the system's own. `docs/DECISIONS.md` divides the clock in two: evidence moves daily and judgment moves weekly.

- **Snapshot** is the evidence: every declared series with its value, the change over its own cadence, the change since the week's anchor, its as-of, its next expected observation and its freshness.
  One line per row. Opening a row gives that series' read, its trend and every observation on record.
- **Judgment** is the weekly half, in the order it is read: the house view, then the phase calls, then the setups.
  All three were authored once on Monday and are only re-scored between authorings, so a reader checking whether the week's call still holds wants them in one pass rather than three tab presses apart.

A view is not a subject.
Switching never refetches: every query behind the panel is keyed on the source alone, so the two views are two reads of one response under one set of clocks.
A tab change issues no request and cannot move a clock.

### The phase is read fresh every week

Every Monday the phase is classified from the data and the frameworks with last week's call out of view (`docs/DECISIONS.md`, 2026-09-23).
Any phase may follow any phase, so there is no upgrade, no 3-of-4 rule and no "toward the next phase".
Last week's call is revealed only afterwards, to record the move and one sentence on what changed.

The card says when the phase was read and when it is read next (the Monday of the following ISO week, overdue only once the Tuesday fallback has passed).
It says how the read stands to last week's in plain words: `Same as last week, 8 weeks running`, or `Was P3 last week, now P2`, with the change note under it.

The four conditions sit under **Holds while**: what must stay true for this read to hold, each `MET` or `FAILED`.
A fresh call has no `not yet`, because nothing is being waited for.
When two or more have failed, `read_stale` is set and the card says the read may be out of date, in amber; nothing reclassifies on it, and the next read is Monday's.

The fourteen migrated journal calls are `carried`.
Their conditions were written as signs of the next phase, so they are labelled `Signs of Phase N (old tracker)` rather than relabelled as something they never were.

`stage` (early, mid, late) is still shown: where inside the phase the reading sits.

The ledger tile carries the phase and stage as one line, a three-segment stage strip, and one small line: `same 8 wks · 4/4 hold`, `was P1`, or `may be out of date`.

`macro_mart.phase_score` and `macro_mart.phase_call_detail` should name the same call.
Between an accept and the rescore that follows it they may not, and the card then says the scores belong to the previous call.

### The snapshot row disclosure

The read used to sit beside the number, and forty-seven of them made a page of prose with figures in it.
The table is now the figures; opening a row gives the argument.

Each disclosure holds three things.

A **chart**, walkable across 7D, 30D, 90D and All, drawn as inline SVG.
No chart library: the stack is pinned and Accounting's NAV chart is the precedent, so its geometry doctrine is followed exactly.
Horizontal positions are percentages of the SVG's own width, so the plot scales with the panel; vertical positions are pixels, so the axis type never stretches.
The cursor reads the nearest observation, never a value interpolated between two, and the readout carries that observation's own read.

It breaks the line at a gap rather than bridging it, draws nothing across a single observation, and carries no direction colour.
The break threshold is three times the series' own median step, not a number of days, or every weekly line would break at every point.
The timeframe buttons narrow what is drawn, not what was fetched: the whole series is already in hand, so a window is a question about the plot and never another read.
A window holding fewer than two observations is offered and marked, so the reader sees that the series lacks the history rather than wondering why the plot is empty.

The **read** the current observation carried, in full.

Every **observation on record**, with the read *it* was written with, its
as-of, value, release, tier and source.

A missing read prints exactly `No insights.` and nothing further.
It is not re-derived from the value, which would be this app manufacturing a judgment nobody made, and it is not filled in from the current read or a neighbour's, which is worse: a sentence shown beside a value it was not written about is attributed to a day that did not produce it.
The policy is enforced in the code and pinned by four probes; it is not narrated on screen, because a reader wants the insight or its absence, not a paragraph about how absences are handled.

The history is fetched when the disclosure opens and cached per series, so a page load reads none of them and a row opened twice is read once.
`macro_mart.observation_history` holds every observation of every series, so it is the one filtered read in the app.

### The scenario comparison table

Three cards became one table, which is the journal's own shape: section 4 writes one row per scenario with columns for the trigger, the asset targets and the positioning.
It is written that way because the useful question is the difference between the three, not the contents of any one.
Three cards make the reader hold S1's breadth band in their head while they scroll to S2's; a table puts them one above the other.

What a card had room for and a row does not is the live state, so it is compressed into a stack of labelled counters in the scenario cell: clauses fired, bands holding, targets holding, with the void, required-unmet, awaiting-judgment and unevaluable counts named underneath.

**A target is words and figures, not a drawn extent.**
`S&P  7,650.50  IN  7,520 – 7,800`: what it is now, whether it holds, what was forecast.
At 76 pixels inside a cell holding thirteen of them the mark-on-a-span was a diagram the reader had to be taught to read, and a reader scanning three scenarios across one row is comparing bands to each other rather than studying one.
`IN`, `NEAR` and `OUT` need no key, and the current value is the same figure in all three rows, which is what makes the state word the thing that moves and therefore the thing the eye lands on.
The colours are unchanged: `--flat` holding, `--urgency-2` within a tenth of the band's width of an edge, and no direction colour anywhere.

This departs from `docs/CONTRACT.md`, which specifies a small horizontal extent with the current value marked on it. It is listed as a contract issue.

`/history` picks a date and renders the scenario set and phase calls as they stood then, and diffs two dates.
Judgment is never updated in place, so every row has a validity interval and the superseded row is kept.

`/calibration` holds both ledgers, the rolling calibration read, and R1 to R12 with their teaching weeks and fire counts.

## Local run

```
pnpm install
pnpm dev
```

Without `NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_ANON_KEY` set, every route redirects to `/login`, which says which variables are missing.
To run against a real project, put the two variables in `web/.env.local`, which git ignores.

Four gates, all of which must be green:

```
pnpm lint
pnpm typecheck
pnpm test
pnpm build
```

**Run one dev server at a time from this directory, and do not run `pnpm build` while one is up.**
Everything that writes `.next` shares it.
Two `next dev` processes from the same checkout overwrite each other's route manifest, and `pnpm build` replaces `.next/server` under a running dev server, which then serves a blank page with `Cannot find module './790.js'` in the console.
Both look exactly like a broken build and neither is one.
Stop the server, `rm -rf .next`, and start again; or use a second checkout.

## Fixture mode

```
NEXT_PUBLIC_FIXTURES=1 pnpm dev
```

Two datasets, no Supabase, no auth gate.
A switch in the header moves between them.

Both are built from the real 2026-W39 reading.
Every level, every probability, every band edge, all eighteen Setup Ledger rows and all twelve rules are the journal's own, so the app can be judged on content rather than on placeholder numbers.
Keys, labels, ordinals, units, decimals and cadences are `config/series.yaml` verbatim.

- **W39 · all evaluable**: every series fresh or on cadence, every condition evaluated, no gap, no void clause, no refused gate. What a good Monday looks like.
- **W39 · the honesty cases**: the same week with the six cases this product exists to render truthfully.

The six are:

1. A weekly series between prints.
   The CFTC pair is dated Tuesday and released Friday, so between them it reads `due`, not `overdue` and not stale.
2. A permanent gap on a point-in-time series.
   CNN's own API answers 451 and the aggregators answer 403, so the Friday reading was missed and can never be recovered.
   Nothing is interpolated and the prior week's 33 is not carried forward into a day it did not describe.
3. A condition that is `unevaluable`, because the series it names is the one with the gap.
   It is never scored `not yet` or counted as failed.
4. A scenario flagged `premise moved`, with a void clause.
   The Senior Credit Officer survey it named was rescheduled out of the horizon; the clause is struck through, stays in the denominator, and the threshold is not renormalised.
5. A setup whose band was touched and whose gate refused.
   That happened in W37, W38 and W39, and each time it was found the following Monday.
6. A search-agent series that did not run, reading `overdue`, beside an asserted series that is not a complaint at all, beside a series declared in config that nothing has ever written.

The fixtures are raw PostgREST JSON, so they go through the same validators a live row does.
A validator that would reject production data rejects the fixture too, in development, where it is cheap to notice.

Fixture mode is read from the environment rather than from a cookie or a query string, so it cannot be switched on against a deployment by a link.
A build that did not set it has no fixture code path reachable at all.

## How the data gets here

One module, `lib/data.ts`, reads `macro_mart.*` and `macro_quality.*` through `supabase.schema(...)`.
Every row goes through a hand-written validator in `lib/contract.ts`, and the reading that comes back carries both the rows that validated and a named reason for each that did not.

Two rules are enforced at that seam.

Null, never zero.
A numeric that cannot be parsed is `null` with a reason, never `0` and never `NaN`.

Envelope and row failures stay separate.
A query that failed means the whole reading is untrusted and nothing is shown.
A trustworthy response holding one malformed row keeps its valid neighbours and counts the bad one.

TanStack Query polls at the run interval.
There is no socket, no Supabase Realtime subscription and no `refetchInterval` shorter than a run, per `docs/DECISIONS.md`: Accounting departed from the poll-don't-stream rule because Hyperliquid marks move every two seconds, and macro has no tick.
`staleTime` and `refetchInterval` are both the run interval, so a page that sits open through 21:00 picks the rescore up and a page that is merely refocused does not re-ask a database that cannot have moved.

A render tick re-renders once a second so "7h ago" stays true without new data.
The clock is sampled at render, never carried on the tick.

## Freshness, and the hardest thing on this screen

Forty-seven series carry six different release rules.
At any moment a large minority of them are between observations.

`docs/CONTRACT.md` gives `macro_mart.snapshot_row.freshness` seven states, and the distinction between two of them is the whole point.

| State | What it claims | Fault |
| --- | --- | --- |
| `fresh` | The latest observation is current and the next print is not yet due | no |
| `due` | Between observations. The reading shown is the current one for this cadence | **no** |
| `overdue` | `next_expected` has passed and nothing arrived | yes |
| `gap` | A point-in-time source was missed and cannot be recovered | yes |
| `asserted` | Hand-asserted rather than fetched: a different kind of claim | no |
| `not_connected` | Declared in `config/series.yaml`, never written | yes |
| `failed` | The source was called on the last run and did not answer | yes |

Three consequences, and they are the design.

**`due` is never a fault and never dims.**
A CFTC reading dated Tuesday and released Friday is *current* on a Wednesday.
Rendering it amber would put a permanent warning on a terminal that is working perfectly, the operator would stop reading the colour by Wednesday, and the one week a source actually failed would look like every other week.

**The State column is empty when nothing is wrong.**
`fresh` and `due` get no chip.
Only the five abnormal states light up, so an empty column is itself the reading.
The explanation for an older as-of sits one column to its right, as the next-expected date, rather than behind a hover.

**The reading's headline is the worst *fault*, not the worst state.**
A panel that read `DUE` every day of the week would be describing the calendar rather than the data.
The counts line beside the chip says `37 fresh · 3 due · 4 asserted · 1 overdue · 1 gap · 1 not connected`, which is where `due` is visible.

Three clocks, never collapsed: **evaluated** (when the worker last recomputed), **as-of** (the day the reading describes, in the source's own market calendar) and **released** (when the source published it).
`next_expected` sits beside them as the fourth column; it is a forecast, not a clock, and it is the only thing `due` and `overdue` are decided against.
An observation date is a calendar day and is never converted into the desk's zone: a Friday US close observed at 05:00 Saturday Singapore time has a Friday as-of, and shifting it would rename the observation.

## What each failure mode shows

Every one of these has a test in `test/failure-modes.test.tsx`, because each is a way the app could be wrong on screen while looking right.
On a terminal whose data moves twice a day, a wrong figure can sit unnoticed for eleven hours.

`test/stylesheet.test.ts` checks that every class a component names exists in `app/globals.css`, and that every token shared with Accounting still carries Accounting's value.
It exists because a block replacement once swallowed four hundred and thirty lines of that file: type checking passed, lint passed, every test passed, and the page was visibly broken.
CSS has no compiler, and a class that resolves to nothing looks exactly like a class that was never needed, so no other gate sees it.
The audit caught it when run by hand; a habit is one distracted evening from lapsing, so it is a test.

`test/schema-contract.test.ts` checks a different thing and exists because of a bug the first file could not catch.
`lib/contract.ts` once read `sector_focus_row` from `macro_mart.setup_state`; the column is `sector_focus_label`.
Every test passed, because `lib/fixtures.ts` emitted `sector_focus_row` too.
Fixtures catch a validator that disagrees with production data only when the two were not written by the same hand in the same hour, and they were.

So the third party is the SQL.
That file parses `supabase/migrations/*.sql` at test time and asserts that every column the fixtures emit exists, that every column a validator reads exists, and that no validator requires a column the schema allows to be null.
The last rule is the one that catches the quietest failure: `macro_mart.calibration` is seeded with every measured column null, and a validator that required them would reject the seed row and make a correctly migrated project report that its migrations had not run.

| Failure | What the operator sees |
| --- | --- |
| A weekly series between its as-of and its release | `due` in the calm dated token, with its next-expected date beside it, and no chip on the row |
| A monthly asserted series beside a live daily one | Two as-of dates a month apart in one table, both correct, neither dimmed |
| A point-in-time source missed | `GAP`, `permanent`, the absent dash, and no value at all — never the prior week carried forward |
| A condition whose series has no reading | `UNEVALUABLE` in the unrated token, never `NOT YET` and never counted as failed |
| A trigger clause whose catalyst was rescheduled | `VOID`, struck through, kept in the denominator, with the reason under it; `PREMISE MOVED` on the scenario |
| A scenario set whose probabilities do not sum to 100 | A banner saying so, and that the Brier score will not be comparable |
| An entry band reached and the gate refused | The row tinted, marked `band touched · gate refused`, the gate's own reason beside it, and a banner counting them |
| A target band whose series has no reading | A dashed extent in the unrated token with no marker — never a marker pinned to an edge |
| A band broken to the upside | The same `--faint` as one broken to the downside; direction colour is never used on a forecast |
| A prose series and a Tier 4 failure | Both are a null value with a string beside them; the prose row is tinted and spans, the failure carries `FAILED` |
| An index future beside its cash index | One row carrying both levels, with the cash week-over-week; never the future's percentage |
| A read longer than one line | Wrapped in full; never clipped, never behind a hover, and the row height does not move |
| The seeded calibration row, every measured column null | The tiles print the absent dash and say "seeded, never graded", not "the migrations have not run" |
| A week graded before this system existed | A dash in Targets, Brier and the grading moment, never a zero and never a derived Monday |
| A holding whose sector-focus id names no row the strategy carries | "unresolved", never a guessed band |
| A judgment row still only proposed | In force on no day at all; a proposal was never believed |
| A `between` band whose floor was never written | `… – 95.00` and an unevaluable extent, never `< 95.00`; only one of those is a forecast |
| A manual target with no numbers | Its stated sentence, not its label, and still inside `targets_n` |
| A setup that names no series | `names no series · not scored`, an `unevaluable` gate, and a banner; never a row that looks broken |
| A past week's probabilities | Read from `scenario_history`; today's three are never printed under a past date |
| A sector-focus sentence matching no row the strategy carries | Shown as written, never resolved to a guessed band |
| A series with one observation and no more | The absence stated; never a flat line claiming it did not move |
| A gap in a series' history | The sparkline breaks; never a straight segment across a stretch nobody looked at |
| A weekly series' own seven-day step | One continuous line; the break rule is relative to the series, not a number of days |
| An observation whose read was never stored | `none written`; never re-derived from the value and never borrowed from the current read or a neighbour |
| A strategy with no holdings check | The absence named, never an empty table read as a flat book |
| A fresh phase read with two of its four conditions failed | `2 of 4 failed` and "This read may be out of date", in amber; nothing reclassifies until Monday |
| A carried journal call | Labelled `carried call`, its conditions shown as the old tracker, and the first fresh read named |
| A phase with no stage authored | Three dashed segments, none filled; never a guess at where inside the phase it sits |
| Supabase unreachable at load | No figures at all, and a red banner naming the network rather than the worker |
| Supabase unreachable mid-session | The last validated figures, dimmed, under an amber banner saying they are the last ones this session received |
| Session expires while the page is open | A real sign-out, the cache cleared, and a redirect to `/login` |
| Missing env vars | `/login`, naming both variables and the fixture flag |
| `macro_mart` or `macro_quality` not exposed | The PostgREST sentence with its code, so `PGRST205` and `42501` are told apart |
| A relation the contract does not define yet | That one section shows a named empty state; the route is unaffected |
| A permission error dressed as a missing relation | Reported as a fault, not swallowed |
| Empty database, seed row only | No children rendered, and "the worker has not completed a pass against this project" |
| An empty Scorecard Ledger | Named as empty, never rendered as a clean record |
| A numeric with an exponent | Parsed; `1e5` is a valid `numeric` on the wire |
| A numeric too large for a double | Refused with a reason, never stored as `Infinity` |
| Clock skew putting a stamp in the future | "just now", never a negative age |
| A calendar date west of Greenwich | Parsed as UTC midnight, so `2026-09-18` cannot become the seventeenth |
| Two judgment rows on the handover day | The interval is half-open, so exactly one set is in force |
| Tab hidden longer than ten minutes | Everything refetched on return, because a throttled tab cannot know what it missed |
| A panel throws while rendering | That panel shows a one-line failure; every other panel and the page are untouched |

## Relations this app reads

Twenty-one relations, all defined in `supabase/migrations/`.

Thirteen are worker-written tables or the two original history views:
`macro_mart.snapshot_row`, `condition_state`, `phase_score`, `scenario_state`, `scenario_target_state`, `setup_state`, `calibration`, `catalyst_window`, `scenario_set_history`, `phase_call_history`, and `macro_quality.finding`, `gap`, `ingest_run`.

Eight were raised as contract issues and have since landed, seven as views in migration 20260921001200 and one in 0014:
`phase_call_detail`, `scenario_set_state`, `strategy_state`, `week_grade`, `setup_grade`, `scenario_rule`, `setup_history`, `scenario_history`.

Each is read through `optionalTable`, so a project on an older migration degrades that one section to a named empty state rather than failing the route.

### What migration 20260921001400 made sayable

**A past week's three probabilities.**
`macro_mart.scenario_set_history` projects the set and not the scenarios inside it, so until 0014 the titles and probabilities a past week carried were unreadable.
`/history` said so rather than rendering today's three under a past date, which would have been misattribution and not staleness: a probability the operator never wrote, printed under a day they did not write it, against which a Brier score would later be read.
`macro_mart.scenario_history` now answers it, and the fallback sentence remains for a project that predates the view.

**A one-sided band, told apart from a band missing an edge.**
`scenario_target_state.comparator` arrived in 0014.
A `lo` of null with `hi` set means "below X" under `lt` and "the floor was never written" under `between`, and only the first is a forecast.
A `between` band missing an edge now draws as unevaluable and prints `… – 95.00`, never as `< 95.00`.
`text` arrived with it and carries the manual targets, which hold no numbers at all and still count toward `targets_n`.

**Why a setup is sitting there ungraded.**
`setup_state.series_key` and `series_label` arrived in 0014.
A setup's entry, invalidation and target are levels on a specific series, and the worker refuses to score one that names none.
A null `series_key` now renders as `names no series · not scored` with a gate of `unevaluable`, so a refusal reads as a refusal rather than as a bug.
`gateRefusedATouch` returns false for such a row, because a band-touch flag that was never computed is not evidence of anything.

### Still deliberately unwired

`macro_mart.strategy_history` landed in 0012 and is not read.
`/history` shows the set, its scenarios, the phase calls and the setups as written; the strategy behind them is one more section on a page that already has four.

### The read travels with its own observation

`macro_mart.observation_history` projects `macro_core.observation.note` since migration 20260921001600, so the disclosure shows the read each observation actually carried rather than the current one repeated down a column.

`SeriesDetail` fetches; `SeriesHistory` renders.
The split is deliberate: the rule above is one a single `??` would quietly undo, and a component that only renders behind a live query is a component whose rendering rules are not covered.
Four probes are run against it, and each is caught: falling back to the row's current read, borrowing the previous observation's, copying one series' read onto another's, and reading `note` as required when it is null on every migrated row.

### One thing the mart still does not carry

`macro_core.setup.sector_focus_id` is resolved into `setup_state.sector_focus_label`, which is the row's **sentence**, not its band.
The band is recovered by matching that sentence against the active strategy's own `sector_focus` rows, the same way a holding's band is resolved through `sector_focus_id`.
One fact, one place: storing the band on the setup as well would give a reader two places to disagree.
When the sentence matches no row the strategy carries, it is shown as written and never guessed at.

## Vercel

The project root directory is `web`.
Set it in Vercel under Settings, General, Root Directory: the repository holds `supabase/`, `worker/`, `config/` and `web/` side by side, and a build from the repository root finds no Next.js app.

Framework preset is Next.js, and the default build and install commands are correct.

Two environment variables, both of which reach the browser, so neither may be a secret:

| Variable | Where it comes from |
| --- | --- |
| `NEXT_PUBLIC_SUPABASE_URL` | Supabase dashboard, Project Settings, API, Project URL |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Supabase dashboard, Project Settings, API Keys, the **publishable** key, which begins `sb_publishable_` |

Neither `@supabase/supabase-js` nor `@supabase/ssr` inspects the key: both send it as the `apikey` header, so a publishable key is a drop-in and the variable keeps its name.
It is safe to ship, because RLS denies `anon` on every relation: it can read nothing until the operator has signed in.

`macro_mart` and `macro_quality` both have to be added to the Data API's exposed-schema list by hand.
Until they are, every read answers `PGRST205`, which this app renders as a sentence carrying its code rather than as a blank panel.

Never set `SUPABASE_SERVICE_ROLE_KEY` or the secret key on Vercel.
It bypasses RLS, and anything in a browser bundle is published.

Never set `NEXT_PUBLIC_FIXTURES` on a deployment.
It disables the auth gate.

Set both variables for Production, Preview and Development, or a preview deployment will redirect to `/login` and stay there.

## Auth

Email and password through Supabase Auth, exactly one user, no signup UI anywhere.
It is the same operator account Accounting uses, on the same Supabase project.

`middleware.ts` is the only place the gate is decided.
Everything but `/login` requires a session, so a page added later is protected by existing.
The middleware uses `getUser`, not `getSession`: the session cookie is client-supplied, and only `getUser` asks the auth server whether the token is real.
It also refreshes the token on every request.

## Design

Ported from Accounting's `web/app/globals.css`, which ports it from GreenfieldDash's `packages/ui/tokens.css`, by reading it and never by importing from it: the repositories share no code and no runtime dependency in either direction, per `docs/DECISIONS.md`.

The `:root` block is byte-identical to Accounting's, along with the chip vocabulary, the shell, the panel chrome, the ledger strip and the table rules.
A page that rendered `#64bd8b` instead of `#63bc8a` would be a different product wearing the same name.

Dark only.
IBM Plex Mono for every figure, at 13px terminal density, with tabular numerals, fixed decimals and fixed column widths in `ch` so nothing jitters.
A data table takes the width its columns need and no more.
No CSS framework, no component library, no chart library.

Two token groups are added below the ported ones, because this system makes two claims Accounting never has to.

**Condition status.** `met` and `failed` borrow the market tokens, because a tracker condition is a genuine binary claim about the world rather than a direction of travel.
`not yet` is the muted token rather than a third colour, because "has not happened" is the resting state of most conditions most of the time and four trackers should not read as twelve warnings.
`unevaluable` takes `--state-unrated` and `void` takes `--faint` with a strikethrough, per the contract.
A void clause and a failed condition mean opposite things: one is a premise that disappeared, the other is evidence against the scenario.

**Band occupancy.** A target band is a small horizontal extent with the current value marked on it, in `--flat` while inside the band, `--urgency-2` within a tenth of its width of an edge, and `--faint` once outside.
Direction colour is deliberately absent: a band is a statement about a forecast, not about a position moving in your favour, and painting a band that broke upward in the green a winning trade wears would make a forecast read as a P&L.

Two further places direction colour is withheld, for the same reason.
A grade on the Scorecard Ledger is a rating, not a direction, so Materialized is not "up".
A diff on `/history` is marked in the accent, because a probability that moved from 45% to 30% is a change of mind and not a loss.

Direction colour is used in exactly one place: the two delta columns on the snapshot, where a move today is genuinely what is being shown.

### The futures and cash pair

`config/series.yaml` gives each index a front-month row and a cash row at consecutive ordinals, and every phase condition, scenario band and setup level is written against the cash one.

They are folded into one row, the way the journal writes the cell: `7,657.35 (cash 7,650.50, -0.08%)`.
Three pairs only, S&P, Dow and Russell; the Nasdaq is cash-only.
Price & Levels is therefore 16 rows over 19 series, and the group header says both.

**The data model does not change.**
`spx_front` and `spx_cash` remain two rows in `macro_mart.snapshot_row`, and every condition still resolves against the cash series.
This folds the presentation and nothing else.

Two properties are load-bearing and are covered by tests.

Both numbers stay visible.
R1 exists because the two were once conflated in the week-over-week arithmetic, and burying the value a condition reads inside another row's formatting would be that mistake again.
Showing both in one cell answers it; showing only the future would not.

The percentage is the cash index's, never the future's, per R1.
Contract rolls contaminate a front-month week-over-week, which is the arithmetic the rule is about.

### Nothing is truncated

A read, a condition's evidence and a gate's reason are wrapped, never clipped and never hidden behind a hover.
A truncated interpretation is worse than none: the reader cannot tell whether the half that was cut changes the meaning.

The ragged-row-height problem that truncation solves is solved the other way instead.
Two lines are reserved on every row whether or not it has a read, so the rows in a group are a uniform height and nothing reflows when a value ticks.

## The rules this app is built on

- **`hasData: false` renders no children.** Structural, not advisory: `Panel` refuses, so no view can fall through to a plausible zero or to an empty table that reads as "nothing happened". The empty state names the relations that were read and what writes them.
- **Never a spinner over data already held.** A stale reading dims and keeps its number readable, with its as-of beside it. Only an envelope fault dims the body; a single faulted series dims its own cells and nothing else.
- **One fault, one owner.** The banner carries the producer's sentence whole and never clips it; the header carries a chip. When the panel is showing its empty state, the banner is suppressed, because that state already carries the sentence.
- **Three clocks, never collapsed.** Evaluated, as-of and released are separate columns, and an observation date is never converted into the desk's zone.
- **The web app computes no metric.** Every figure is selected from `macro_mart`. `read_stale`, `holding` and `clauses_fired` are the worker's verdicts and are never recomputed here.
- **Every panel has its own error boundary.** One relation returning something the renderer cannot handle degrades one panel, never the page.

## Layout

```
app/            routes: /macro, /history, /calibration, /login, and globals.css
components/     the panel, the two views, the scenario table, the band extent, the sparkline, the chrome
hooks/          the source, the three readings, the render tick, the view tabs
lib/            contract validators, the data layer, numbers, clocks, freshness, conditions, fixtures
test/           vitest: failure modes, the schema guard, and the stylesheet audit
middleware.ts   the auth gate
```
