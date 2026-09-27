# Build contract

This file is the seam between three parts built in parallel: `supabase/` (SQL), `worker/` (Python), `web/` (Next.js).
If a part needs the contract changed, it edits this file and says so in its final report; it never silently diverges.
Read `docs/DECISIONS.md` first.

## Layout

```
supabase/migrations/*.sql   schema, RLS, seed
config/series.yaml          asserted: every series, its cadence, unit, release rule
config/predicates.md        the named path-dependent predicates and their conventions
worker/                     Python 3.12, uv project, launchd agent, two runs a day
web/                        Next.js 15, React 19, pnpm, Vercel, root directory `web`
.env.example                every key documented, no values
```

## Doctrine that every part obeys

- Null, never zero. A figure that cannot be measured is `null` with a reason beside it.
- Nothing is interpolated, and no value is carried forward into a day it did not describe.
- A condition that cannot be evaluated is `unevaluable`, never `not yet`.
- Vendor and scraper field names stop at the worker's `normalize` step.
- Every write is an idempotent upsert on a natural key. Replaying a run is always safe.
- Judgment is never updated in place. A new row supersedes the old one and the old one is kept.
- All timestamps are `timestamptz` in UTC. Display conversion happens only in the web app.
- All numeric columns are `numeric`. Python uses `Decimal(str(v))`. The web app parses at the edge and treats unparseable as null.
- The web app computes no metric. It selects from `macro_mart` and `macro_quality`.
- The system is read-only toward every source. No order path exists anywhere.

## Schemas

Four Postgres schemas in the shared `GreenfieldFund` project: `macro_raw`, `macro_core`, `macro_mart`, `macro_quality`.
`macro_mart` and `macro_quality` are exposed through the Data API and carry RLS `select` for role `authenticated` only.
`macro_raw` and `macro_core` are not exposed.
The worker, the authoring agent and the desk command reach them over a Postgres connection on the session pooler, never through the Data API (see Environment).
The web app writes nothing to any table.
It calls six `SECURITY DEFINER` functions in `macro_mart`, each requiring `auth.uid()`: `request_refresh(p_reason text)` inserts one `amend` row into `macro_core.run_request`, and `decide_proposal(p_id bigint, p_decision text)` records `accept` or `reject` on one `proposed` request.
Neither moves a judgment row; the runner applies a decision with the same `activate` that `macro-agent accept` calls.
The other four edit the operator's two holdings lists: `save_manual_position(p_id uuid, p_symbol text, p_name text, p_venue text, p_asset_class text, p_quantity numeric, p_note text) returns uuid` and `retire_manual_position(p_id uuid)`, `save_watch_ticker(p_id uuid, p_symbol text, p_name text, p_asset_class text, p_note text) returns uuid` and `retire_watch_ticker(p_id uuid)`.
A null `p_id` inserts; a retire deactivates and never deletes.
`anon` has no access to anything.

**The `macro_desk` role.** `macro-agent desk` (the OpenClaw feeds, `docs/DESK.md`) connects as `macro_desk`, a NOLOGIN role in the migrations whose login and password are set only by `supabase/desk_role.py`.
It can insert and update the feed tables (`catalyst`, `catalyst_alias`, `tail_risk`, `tail_risk_check`, `holding_watch`, `holding_event`), insert its own `macro_raw.batch` rows, `ingest_run` rows (source `desk:*`) and findings (`desk_*`), read the two holdings lists and the `macro_mart` board, and nothing else: no DELETE anywhere, no judgment table, no observation.
Each of those tables carries a `macro_desk` policy, because RLS is on everywhere.
The policies are narrowed to what the command does (20260925000200): every catalyst and tail risk it inserts or updates says `feed_source = 'openclaw:macro-desk'`, it sees and writes only `ingest_run` rows of that origin, and its findings' `check_key` starts with `desk_`.
Confirming that the rest of the calendar is still listed touches rows another feed wrote, so it goes through `macro_core.desk_confirm_catalysts_listed(date)`, which moves `last_seen` and nothing else.
Its sessions end a statement after 30 seconds, a lock wait after 10 and an idle transaction after 60.

**Accounting is read in one place and never written.** Accounting's `raw`, `core`, `mart` and `quality` carry no grant, policy or object of this system's.
Two `SECURITY DEFINER` functions in `macro_core`, executable by `macro_desk` and `service_role` only, read them: `accounting_open_positions()` returns `mart.book_position` rows whose quantity is not zero and whose kind is not cash, with the option underlying from `core.instrument`, and never the `accounts` column; `accounting_venue_status()` returns each venue's state and as-of.
Both pass Accounting's free text through `macro_core.redact_account_ids`, so no string they return carries an account number, a wallet address or any other run of five or more digits (20260925000200); an option's symbol is left alone, because it is digits by design.
Their bodies are dynamic SQL, so no dependency on Accounting's columns is recorded and an Accounting migration is never blocked by them.

### macro_raw

`macro_raw.batch(id bigserial pk, source text, kind text, fetched_at timestamptz, payload jsonb, shadow bool)`.
Every API response and every scraped document lands here before normalisation.
Every desk submission lands here too, as sent, under source `desk:<feed>`; a `--dry-run` submission is archived with `shadow = true` and is the only row its run writes.
Search-agent returns land here too, as the `search` kind, so a narrative point can be traced to the text it came from.

### macro_core: observed facts

- `macro_core.series(id uuid pk, key text unique, label text, table_group text, ordinal int, value_kind text check in ('numeric','text'), unit text, decimals int, cadence text, release_rule text, replayable bool, source_name text, tier_floor int, fetch_path text check in ('deterministic','agent_0930'))`.
  `ordinal` is unique within `table_group` and is the only declaration of row order; `macro_mart.snapshot_row` carries it through.
  `fetch_path` is what the 21:00 rescore selects on.
  Asserted reference data, seeded from `config/series.yaml`, never written by a fetch.
  `key` is the stable identifier used by every condition expression; `label` is the display string and may change without breaking a condition.
  `cadence` is one of `session`, `daily`, `weekly`, `monthly`, `event`.
  `release_rule` states when the next observation is due, and is what `next_expected` is computed from.
  `replayable` is false for a source that only serves the present moment.

  **One canonical unit per series, declared here, and every condition is written in it.**
  High-yield option-adjusted spread is stored in basis points because every condition referencing it is written in basis points (`HY above 300 bps`), even though FRED serves percent and the journal displays both.
  A condition comparing `2.70` against `300` is silently false and looks correct, which is why the unit is a property of the series and not of the reader.

  `value_kind` is `text` for the five Macro & Fiscal State rows, which are prose.
  A text series stores `value` null and its content in `display`, and it may only appear in a `manual` condition.
  Without this, a prose row and a Tier 4 failure are the same shape (both are a null `value` with a string beside it), and a failed fetch would render as a macro state.

  **Cash indices are their own series, not an `extra` field on a future.**
  `spx_cash`, `dow_cash` and `rut_cash` sit beside `spx_front`, `dow_front` and `rut_front`.
  Every phase condition, scenario band and setup level written to date refers to the cash index, and R1 exists because the two were once conflated in the week-over-week arithmetic; a condition cannot reference a value buried in another series' `extra`.
- `macro_core.observation(series_id uuid, asof date, value numeric null, display text, tier int check between 1 and 4, source text, released date null, note text, extra jsonb, observed_at timestamptz, received_at timestamptz, pk(series_id, asof))`.
  One row per series per observation date. `value` null with a `note` is a Tier 4 reading, never a zero.
  `extra` carries components: the 200-day distance and slope, the CFTC regime band and open interest, the three net-liquidity components, funding by symbol.
  `observed_at timestamptz` records the moment the reading described, which for a 24-hour series is the run time and for a session series is the close.
  Two runs on one day upsert the same `(series_id, asof)` row and the later one wins; `observed_at` is what says which run it was.

  A series whose source serves only its latest reading is `replayable: false` whatever its cadence, and a missed session is a permanent gap.
  A gap of either kind is removed the moment a reading for its date is stored: `permanent` is a prediction made when the gap opened, and a reading that arrives proves it wrong.

  Price series additionally carry `session_high` and `session_low` in `extra`.
  These are not decoration: the setup band-touch flag is derived from them, and without them a setup whose entry band was reached and refused intraday is indistinguishable from one price never approached.

  `asof` is the observation date in the source's own market calendar, never the operator's local date.
  A Friday US close observed at 05:00 Saturday Singapore time has a Friday `asof`.
- `macro_core.catalyst(id uuid pk, external_key text unique, name text, starts_at timestamptz null, ends_at timestamptz null, date_precision text check in ('exact','day','window','month','unknown'), source_url text, affected text[], status text check in ('tracked','retired','rescheduled'), first_seen date, last_seen date, retired_at date null, retired_reason text null, date_text text null, date_estimated bool, family text null, category text, feed_source text check in ('vault','openclaw:macro-desk'), last_run_id bigint null fk ingest_run, updated_at timestamptz null)`.
  Parsed from `Catalyst Monitor.md` while the catalysts feed is `vault`; written by `macro-agent desk catalysts submit` once it is `desk`.
  `external_key` is a stable hash of the catalyst name so a reschedule updates rather than duplicates.
  `date_text text null` keeps the monitor's own "Date & Time" words, which say whether a date was confirmed or only expected.
  A retired row always carries `retired_reason` (a CHECK).
  `feed_source` and `last_run_id` say which feed last wrote the row and in which run.
  A desk run marks every tracked row `last_seen` today, because the desk submits changes rather than re-listing the calendar and the archive's three-day listing grace reads `last_seen`.
- `macro_core.catalyst_alias(external_key text pk, catalyst_id uuid fk catalyst, name text, renamed_at timestamptz, reason text null, run_id bigint null)`.
  What a rename leaves behind: the catalyst keeps its id and takes the new name and key, and the old key maps here so a submission under the old name is recognised.
- `macro_core.catalyst_occurrence(id uuid pk, name text, name_key text, occurred_on date, starts_at timestamptz null, ends_at timestamptz null, date_precision text check in ('exact','day','window'), family text null, category text, source_url text null, affected text[], provenance text check in ('catalyst_monitor','weekly_journal'), provenance_ref text null, confidence text check in ('confirmed','approximate'), moved_markets bool null, market_note text null, note text null, catalyst_id uuid null fk catalyst, first_tracked date null, last_tracked date null, archived_at timestamptz, unique(name_key, occurred_on))`.
  The archive: every catalyst that took place, one row per occurrence, never deleted.
  `name_key` is the live table's `external_key` for the same name and `occurred_on` is the UTC date of `starts_at`, so a live catalyst and its archived occurrence share a key.
  `family` is the recurring kind (`us_cpi`, `fomc_decision`, `earnings_nvda`) from `worker/src/macro/families.py`, null for a one-off; `category` is always set.
  The 09:30 run archives a live catalyst once it has started (one dated without a time, once its day has ended), provided the monitor was still listing it within three days of that day (`archive.LISTING_GRACE`, so a missed run does not lose it) and its date is better than a month.
  Rows before the worker began were loaded once with `macro-worker --archive-backfill`; `moved_markets` and `market_note` come only from what a weekly journal wrote afterwards and are never measured from prices.
- `macro_core.watchlist_change(ticker text, observed_at timestamptz, change_pct numeric null, summary text, source_url text, pk(ticker, observed_at))`. Parsed from `Market Watchlist.md`.
- `macro_core.holding(ticker text pk, label text, book text check in ('equities','crypto'), active bool)`. Parsed from the Watchlist `## Holdings` list.
- `macro_core.tail_risk(key text pk, risk_id text unique, name text, severity text check in ('acute','elevated','watch'), summary text, priced_in text, assets text[], source_url text, first_seen date, last_seen date, retired_on date null, retired_reason text null, feed_source text, last_run_id bigint null)`. Parsed from `Blackswan Events.md` while the tail-risks feed is `vault`; written by `macro-agent desk tail-risks submit` once it is `desk`.
  `risk_id` is the stable identity, a slug such as `iran-oil-chokepoints`: rewording a risk keeps it.
  An insert without one (the vault parser's) gets one from its name by trigger.
  `key` stays the primary key; a desk-created risk's `key` is its `risk_id`.
- `macro_core.tail_risk_check(id bigserial pk, risk_id text fk tail_risk, checked_at timestamptz, kind text check in ('weekly','daily'), severity text, summary text, priced_in text, gauges jsonb, source_url text, retired bool, run_id bigint null, unique(risk_id, checked_at))`.
  Every read of every risk, append-only: the Monday scan and the daily recheck of the elevated and acute ones.
  `gauges` is `{series_key: {value, display, unit, asof}}` as the desk read it from `macro_mart.snapshot_row`, never a model's number.
- `macro_core.manual_position(id uuid pk, symbol text, name text, venue text, asset_class text, quantity numeric null, note text, active bool, created_at, updated_at)` and `macro_core.watch_ticker(id uuid pk, symbol text, name text, asset_class text, note text, active bool, created_at, updated_at)`.
  The operator's positions at brokers Accounting does not connect to, and the names followed without being held.
  Edited only through the four list functions named under Schemas; deactivated, never deleted.
- `macro_core.holding_watch(id bigserial pk, as_of date, symbol text, source text check in ('accounting','manual','watch'), name, asset_class, market_symbol, price numeric null, price_at timestamptz null, currency, change_24h_pct numeric null, change_basis text check in ('close_to_close','rolling_24h') null, change_session date null, price_source text, unavailable_reason text null, underlying_only bool, quantity numeric null, exposure_usd numeric null, material bool, driver_summary text null, source_url text null, no_clear_reason bool, run_id bigint null, recorded_at, unique(as_of, symbol, source))`.
  One row per watched name per Singapore day.
  The price and the change are computed by the desk from market data and never taken from a model: `close_to_close` is the last completed regular session against the one before (Yahoo), `rolling_24h` is now against 24 hours ago (Hyperliquid, then CoinGecko), and an option carries its underlying's move with `underlying_only`.
  A change that could not be computed is null with `unavailable_reason` (a CHECK).
  A material move carries its driver and source, unless `no_clear_reason` says the agent looked and found no sourced story (a CHECK, 20260925000200).
  Every `source_url` on the five feed tables is http(s) or empty (a CHECK).
- `macro_core.holding_event(id uuid pk, symbol text, kind text check in ('earnings','product','regulatory','unlock','other'), name text, name_key text, starts_at, ends_at, date_precision, date_estimated bool, date_text, source_url text, catalyst_id uuid null fk catalyst, run_id bigint null, first_seen date, last_seen date, unique(symbol, name_key))`.
  What falls in the next fourteen days for a watched name.
  An earnings event is also placed on the catalyst calendar and `catalyst_id` points at that row.
- `macro_core.event(id uuid pk, occurred_on date, kind text check in ('macro','structure'), text text, assets text[], surprise bool, source text)`. The dated event log.

### macro_core: asserted judgment

Kept in separate tables from observed facts, so a later reader can always tell an assertion from a disclosure.
Every table here carries `status text check in ('proposed','active','superseded')`, `authored_at`, `activated_at null`, `superseded_by uuid null`, and where an amendment is possible `amends uuid null` and `amend_reason text null`.

At most one row may be `active` per subject, enforced by a partial unique index.
The subject is `(asset)` for `phase_call`, because a call stands until superseded rather than expiring with its week; `(week)` for `scenario_set`; `(week, asset)` for `strategy`, because a week has two books; and `(week, asset, name)` for `setup`, so a replayed authoring run upserts rather than duplicates.

A partial unique index cannot be deferred, so activation is always two statements inside one transaction: supersede the outgoing row first, then activate the incoming one.
A scenario set or a phase call is written whole in one transaction, because the cardinality triggers fire at `COMMIT`.

- `macro_core.phase_call(id uuid pk, asset text check in ('equities','crypto'), week text, phase int, phase_name text, prior_phase int null, direction text check in ('up','unchanged','down'), reason text, evidence text[], falsifier text, stage text null check in ('early','mid','late'), stage_reason text null, classification text check in ('carried','fresh'), classified_at timestamptz null, revealed_at timestamptz null, change_note text null, ...lifecycle)`.

  **The phase is classified fresh every week.**
  Each Monday the authoring run reads each asset from this week's data and the two cycle frameworks with last week's call out of its context, and writes the read before that call is shown to it.
  Any phase may follow any phase: a market can go from 3 to 4 and back to 2, and a read that starts from last week's and asks whether to move it one step is anchored on itself, which is the failure the Scorecard Ledger recorded for 2026-W32 ("the phase call capped the scenario").
  `classified_at` is when the read was written and `revealed_at` is when the same run was first shown last week's call; a constraint refuses a fresh row revealed before it was classified.
  `prior_phase` and `direction` are filled in from the call that was active at `classified_at`, by the command that wrote the read, never by the read itself.
  `direction` is the phase number's move and says nothing about whether the move was allowed: every move is.
  `change_note` is the one sentence written after the reveal on what changed; a move of two or more steps around the cycle (5 to 1 is one step) names both phases and argues the jump.

  `classification` is `fresh` on every weekly read and `carried` on the fourteen migrated journal calls, whose four conditions counted toward the next phase and are never reinterpreted.
  The blind read that 20260922000100 added (`blind_phase`, `blind_reason`) is retired: a fresh read is the blind read, and the columns were dropped by 20260923000900, never having been written.

  `stage` is where inside the phase the reading sits: `early`, `mid` or `late`, authored with a reason and never derived from the conditions.
  The operator's question is the test: in Phase 1, is this close to Phase 2, or is everything still undervalued and early.
- `macro_core.phase_condition(id uuid pk, phase_call_id uuid, ordinal int check between 1 and 4, text text, kind text check in ('expr','predicate','manual'), expr jsonb null, unique(phase_call_id, ordinal))`.
  Exactly four per phase call.
  On a fresh call they are what must stay true for this phase to hold: the read's own falsifiable evidence, typed wherever a series exists, scored at 09:30 and 21:00.
  A condition that no longer holds has `failed`; there is no `not yet` on a fresh call.
  Two failed conditions mean this week's read may be out of date, which is flagged and never acted on: the phase is read again on Monday.
- `macro_core.scenario_set(id uuid pk, week text, synthesis text, probability_check text, swing_variables jsonb, rules_waived int[], ...lifecycle)`.
- `macro_core.scenario(id uuid pk, set_id uuid, ordinal int check between 1 and 3, title text, probability int, positioning text, unique(set_id, ordinal))`.
  A constraint asserts the three probabilities of a set sum to 100.
- `macro_core.trigger_clause(id uuid pk, scenario_id uuid, ordinal int, text text, kind text check in ('expr','predicate','manual'), expr jsonb null, catalyst_id uuid null, required bool, void_at timestamptz null, void_reason text null)`.
  `required` true means the clause must hold; false means it counts toward the scenario's `n_of` threshold.
  A scenario's trigger is stored only as its clauses, so the split must be lossless: joining a scenario's clauses in ordinal order with `"; "` must reproduce the trigger as written, and a test asserts it for every scenario.
  `required` is read from the sentence's shape when a journal does not state it, and an undecidable case takes `false`, which is the weaker claim.
  A void clause is kept and counted in the denominator. It is never removed and the threshold is never renormalised.
- `macro_core.scenario_target(id uuid pk, scenario_id uuid, series_id uuid null, label text, kind text check in ('band','manual'), comparator text check in ('between','gt','lt') null, lo numeric null, hi numeric null, text text null)`.
  These are what `targets hit x of n` counts, and `n` is the row count.
  Not every target is a band: W38's S1 carried ten numeric bands plus one written leg, and the W39 grading counted `targets hit 7 of 11` across all eleven.
  W39's own S1 carries thirteen numeric bands plus one written leg.
  A `manual` target has a null `series_id`, is never auto-scored, and is presented for judgment at grading.
  A set whose targets are all `manual` is a `macro_quality` finding, because a scenario with nothing checkable is not a forecast.
- `macro_core.setup(id uuid pk, week text, asset text, series_id uuid, name text, style text, entry_lo numeric, entry_hi numeric, invalidation numeric, invalidation_dir text check in ('above','below'), target numeric, rr_planned numeric, gate_expr jsonb null, gate_text text null, sector_focus_id uuid null, rationale text, ...lifecycle)`.
  Every setup names the sector-focus row it sits under, by key rather than by string, the same way `holdings_check` does.
  It also names the series its levels are measured against.
  Equity setups are quoted on the cash index and crypto setups on BTC or ETH, so `asset` does not determine it, and a Nasdaq setup scored against the S&P would produce an entry state, an R multiple and a ledger row that are all wrong and none of them visibly so.
- `macro_core.strategy(id uuid pk, week text, asset text, stance text, posture text, exposure_lo numeric, exposure_hi numeric, exposure_operated_lo numeric null, exposure_operated_hi numeric null, ...lifecycle)`.
- `macro_core.sector_focus(id uuid pk, strategy_id uuid, band text check in ('CRITICAL','HOLD','WATCH','AVOID'), text text)`.
- `macro_core.holdings_check(id uuid pk, strategy_id uuid, ticker text, action text check in ('keep','trim','add','exit'), text text, sector_focus_id uuid null)`.
  Every active holding should have a row here for the active strategy of its book.
  This is a `macro_quality` finding and not a database constraint: `holding` is re-parsed from the Watchlist on every run while `strategy` is authored weekly, so a ticker added on a Wednesday would otherwise fail the parse that discovered it.
- `macro_core.condition_state_asserted(condition_id uuid pk, owner_kind text check in ('phase','trigger'), status text, current_text text null, asserted_at timestamptz, asserted_by text check in ('migration','authoring','operator'))`.
  The as-written status of a `manual` condition.
  Every migrated week's trackers and trigger clauses are prose and land here; going forward, any condition the operator judges rather than measures does too.
- `macro_core.scenario_rule(number int pk, text text, retired_week text null)` and `macro_core.scenario_rule_week(rule_number int, week text, pk(rule_number, week))`.
  Numbers are never reused. A retired rule is kept with its `retired_week` set.

### macro_core: graded outcomes

- `macro_core.week_grade(week text pk, base_scenario_id uuid null, base_scenario_label text, base_grade text check in ('Materialized','Partial','Not'), other_grades jsonb, eq_call text, crypto_call text, key_miss text, targets_hit int, targets_n int, brier numeric, premise_moved bool, graded_at timestamptz)`.
  The Scorecard Ledger, one row per graded week.
  `base_scenario_id` is null for a migrated week whose scenario set was not imported; `base_scenario_label` always carries the title and probability as written, so the calibration read works over migrated and native rows alike.
  `graded_at` is nullable: nine migrated weeks were graded by archive journals this system deliberately does not read, and a derived grading date would be a manufactured fact.
  Hit-rate convention is unchanged: Materialized counts 1, Partial 0.5, Not 0, over up to the last eight rows.
  Brier is unchanged: outcome weights normalised to sum to 1, then the sum over the three scenarios of `(p − o)²`.
- `macro_core.setup_grade(setup_id uuid pk, graded_week text, outcome text check in ('not triggered','triggered → stopped','triggered → target','open'), r_realised numeric null, note text)`.
  The Setup Ledger. An `open` row is rewritten the following week, never duplicated.

### macro_core: the run queue

- `macro_core.run_request(id bigserial pk, kind text check in ('narrative','author','amend'), reason text null, origin text check in ('schedule','operator'), requested_by uuid null, requested_at, status text check in ('queued','running','proposed','done','failed','rejected','accepted'), claimed_at null, finished_at null, decided_at null, decision text null check in ('accept','reject'), proposed jsonb, summary text null, error text null)`.
  Every Claude run is a row here: the runner schedules the daily `narrative` and Monday's `author`, and the Desk's Refresh inserts an `amend`.
  At most one request of each kind is `queued` or `running`, so a double click queues one run.
  A run that writes judgment stops at `proposed`; the operator's decision is recorded on the row and applied by the runner.
  Not exposed: the web reads it through `macro_mart.run_request_state` and writes it only through the two functions named under Schemas.

### macro_mart: derived, recomputable

Every table here is reproducible by re-running the worker's evaluate step over `macro_core`.

- `macro_mart.snapshot_row`: `series_id pk, key, label, table_group, ordinal, value, display, unit, value_kind, cadence, observed_at, delta_native, delta_native_label, delta_week_open, anchor_asof, tier, source, asof, released, next_expected, freshness text check in ('fresh','due','overdue','gap','asserted','not_connected','failed'), note, extra, updated_at`.
  `delta_native` is the change over the series' own cadence and `delta_native_label` names it (`1d`, `WoW`, `MoM`). Never a one-day delta on a weekly series.
  `delta_week_open` is measured against `anchor_asof`: the change from this series' last observation dated before the active scenario set's authoring as-of, which is the tape the set was written on.
  It is not measured against a calendar Monday, and not against each series' own prior week.
  It is null when the series has printed nothing on or after that day.
  One anchor for the whole snapshot is what makes the weekly rendering's week-over-week column mean one thing, and `anchor_asof` is carried on the row so the reader can see what it was measured from.
  `freshness` is `due` between observations of a weekly or monthly series, not `overdue`.
  `cadence` and `observed_at` are carried from `macro_core` rather than looked up: the page has to be able to say which of the two daily runs a reading came from, which is the whole reason `observed_at` exists.
- `macro_mart.condition_state`: `condition_id pk, owner_kind text check in ('phase','trigger'), owner_id uuid, ordinal, text, kind, status text check in ('met','not yet','failed','unevaluable','void'), current_display text, evidence jsonb, source text check in ('computed','asserted'), void_at, void_reason, evaluated_at`.
  `void_at` and `void_reason` are carried from `macro_core.trigger_clause` and are null unless the status is `void`.
  A scenario flagged `premise moved` whose clause cannot say which catalyst moved, and when, is a claim the operator has to take on trust; the reason the threshold is not renormalised is that the void clause stays visible and checkable.
  A condition of kind `expr` or `predicate` is computed on every run.
  A condition of kind `manual` can never be computed, so its status is read from `macro_core.condition_state_asserted` and carried through with `source = 'asserted'`.
  This is why the asserted status lives in `macro_core` and not here: `macro_mart` is rebuilt, and a judgment that a rebuild destroys is not a record of anything.
- `macro_mart.phase_score`: `asset pk, phase_call_id, phase, phase_name, stage, met int, of int, machine_checked int, manual int, unevaluable int, failed int, read_stale bool, evaluated_at`.
  `failed` counts the conditions that have failed; `unevaluable` is never counted as failed or as met.
  `read_stale` is true when two or more of a fresh call's four conditions have failed, so this week's read may be out of date; it is always false on a carried call, and a CHECK refuses it with fewer than two failed.
  There is no upgrade: `upgrade_eligible` and its 3-of-4 CHECK were dropped with the sequential read they served.

  `phase` and `stage` appear both here and on `macro_mart.phase_call_detail`, and the duplication is deliberate so the ledger tile is one read rather than two.
  **`phase_call_detail` is authoritative**: those are authored judgment, projected straight from `macro_core.phase_call`.
  The copies here are carried by the recompute, so when the two name different calls it is this table that is stale, and the web app says so plainly until the next worker run.
- `macro_mart.scenario_state`: `scenario_id pk, set_id, ordinal, title, probability, positioning, clauses_fired int, clauses_total int, clauses_void int, required_unmet int, bands_holding int, bands_total int, targets_holding int, targets_total int, premise_moved bool, evaluated_at`.
  Two denominators, because they answer different questions and conflating them is what produced the W36 defect.
  `bands_total` counts only `kind = 'band'` targets, which is what can be scored automatically.
  `targets_total` counts every target including `manual` ones, and it is the `n` that `targets hit x of n` uses at grading.
  Both are rendered and both are labelled.
- `macro_mart.scenario_target_state`: `target_id pk, scenario_id, series_id, label, kind, comparator, lo, hi, text, current, holding bool null, distance_to_breach numeric null, evaluated_at`.
  `comparator` is not optional detail: without it a one-sided `lt` band and a two-sided band whose floor was never written render identically, and only one of those is a forecast.
  A `gt` band carries its bound in `lo` and an `lt` band carries it in `hi`, which is easy to write backwards and is otherwise stated only in a migration CHECK. `holding` null means unevaluable.
  `distance_to_breach` is an unsigned magnitude in the series' own canonical unit, never a fraction and never a percentage: one canonical unit per series applies here too.
  A `manual` target carries null `lo`, `hi`, `current` and `holding`, and is presented for judgment.
- `macro_mart.setup_state`: `setup_id pk, week, asset, series_key, series_label, name, entry_lo, entry_hi, invalidation, invalidation_dir, target, rr_planned, gate_text, sector_focus_label, gate_state text check in ('open','blocked','unevaluable'), gate_reason text, band_touched_today bool, band_touched_close bool, entry_state text check in ('not triggered','open','stopped','target'), sessions_carried int, r_running numeric null, evaluated_at`.
- `macro_mart.calibration`: single row `id = 1`: `graded_weeks int, base_hit_score numeric, base_materialized int, mean_brier numeric, brier_weeks int, base_was_s2 int, window int, computed_at`.
  This is the sentence §0 currently pastes from `calibration.py`.
**Judgment projected onto the Data API.**
`macro_core` is not exposed, and the three routes have to render judgment, not only arithmetic.
Each of these is a read-only view over `macro_core`, not a recomputation, and like the history views they are never dropped and rebuilt the way the evaluated tables are.

- `macro_mart.phase_call_detail`: `asset pk, phase_call_id, phase, phase_name, prior_phase, direction, reason, evidence text[], falsifier, stage, stage_reason, week, activated_at, classification, classified_at, change_note, prior_week, since_week, weeks_running int`.
  Without it the Phase view shows a score with no argument behind it, and a falsifier the operator cannot read cannot be checked.
  `prior_week` is the week of the call before this one, `since_week` the first week of the current unbroken run of this phase, and `weeks_running` the calendar weeks between their Mondays inclusive, so a week with no journal still counts as a week that passed.
- `macro_mart.scenario_set_state`: `set_id pk, week, synthesis, probability_check, swing_variables jsonb, rules_waived int[], activated_at`.
  Without it a scenario set is three numbers with no case for them.
- `macro_mart.strategy_state`: `strategy_id pk, week, asset, stance, posture, exposure_lo, exposure_hi, exposure_operated_lo, exposure_operated_hi, sector_focus jsonb, holdings_check jsonb, activated_at`.
  Every setup names the sector-focus row it sits under, so the Setups view has to be able to show what that row says.
- `macro_mart.week_grade` and `macro_mart.setup_grade`: the two ledgers, which `/calibration` is defined as rendering.
  `setup_grade` joins its setup so it carries `proposed_week`, `asset`, `name`, `style`, `entry_lo`, `entry_hi`, `invalidation`, `invalidation_dir`, `target` and `rr_planned`.
  The markdown Setup Ledger prints all of those, `/calibration` is defined as rendering it, and `macro_core` is not on the Data API, so a view missing them forces a query that cannot be made.
  `week_grade.other_grades` is `jsonb` of shape `[{"id": "S1", "prob": 30, "grade": "Not", "note": "..."}]`.
  An array, not an object keyed by scenario id.
  The column defaults to `'[]'::jsonb` and the web app reads an array, so an object keyed `S1` rejects every ledger row and empties the calibration route.
- `macro_mart.scenario_rule`: `number pk, text, retired_week, teaching_weeks text[], fire_count int`.
- `macro_mart.observation_history`: `series_id, series_key, asof, value, display, unit, tier, source, released, observed_at, note, pk(series_id, asof)`.
  `note` is the read as it was written at the time, and is null where none was written.
  A Snapshot row's disclosure shows a series' interpretation over time, and a read generated today from an old value would be the app manufacturing a judgment nobody made.
  `/history` renders the page as it stood on a past date, and the Snapshot view cannot do that from current-state rows alone.
- `macro_mart.scenario_history`: every scenario of every set, including superseded ones, with `valid_from` and `valid_to`.
  `scenario_set_history` projects the set and not its three scenarios, so without this a past week's titles and probabilities are unreadable, and rendering today's three under a past date would be misattribution rather than staleness.
- `macro_mart.setup_history` and `macro_mart.strategy_history`: every superseded version, with `valid_from` and `valid_to`, for the same reason.
  These project the *authored* shape, not the state shape.
  `setup_state` carries `gate_state`, `entry_state`, `band_touched_today`, `band_touched_close`, `sessions_carried` and `r_running`, and every one of those is recomputed from the tape on each run and was never stored per version: there is no past value of them to project, and inventing one would be a reading nobody took.
  `strategy_state` has no such columns, so its history and its state do coincide.

  `sector_focus` is `jsonb` of shape `[{"id", "band", "text"}]` ordered CRITICAL, HOLD, WATCH, AVOID rather than alphabetically.
  `holdings_check` is `[{"id", "ticker", "action", "text", "sector_focus_id"}]`, and it deliberately does not duplicate the band: the `sector_focus` array on the same row resolves the id, and carrying two copies gives a reader two places to disagree.

- `macro_mart.scenario_set_history` and `macro_mart.phase_call_history`: read-only projections of the judgment tables including superseded rows, carrying `valid_from`, `valid_to`, `amends` and `amend_reason`.
  `macro_core` is not on the Data API and `/history` has to read what was once believed, so the projection is the only route to it.
  These are the one exception to "every `macro_mart` table is derived": they are a view, not a recomputation, and they must never be dropped and rebuilt the way the evaluated tables are.
- `macro_mart.catalyst_window`: `catalyst_id pk, name, starts_at, ends_at, date_precision, date_text, date_estimated bool, family, category, affected, days_out int, referenced_by uuid[], source_url`.
  The next thirty days, with which trigger clauses depend on each one.
  A catalyst stays until it is more than a day past its end.
  `category` is one of the sixteen in `worker/src/macro/families.py`; `date_estimated` means the date is a guess ("late October 2026 expected"), and a month-only date spans the part of the month it names.
- `macro_mart.drift`: `set_id pk, week, anchor_asof date, asof date, score int, material int, refresh_suggested bool, reason, items jsonb, evaluated_at`.
  One row for the active scenario set: every condition, band and setup gate re-evaluated at `anchor_asof` and compared with now.
  `anchor_asof` is the last US session that closed before the set was activated, which is the tape the call was written on; each item's baseline is its last measured state within ten days before it.
  `items` is `[{kind, subject, before, after, material, weight, sentence}]`; a material change is one of the four `docs/DECISIONS.md` says may permit a re-price, weighs 3, and suggests a refresh on its own.
  Transitions to or from `unevaluable` are never counted, and a retired tail risk is never counted as going acute.
- `macro_mart.run_request_state`: `id, kind, reason, origin, requested_at, status, claimed_at, finished_at, decided_at, decision, proposed jsonb, summary, error`.
  A read-only view over the last thirty days of `macro_core.run_request`, the queue every Claude run goes through.
- `macro_mart.tail_risk_state`: `key, name, severity, summary, priced_in, assets, source_url, first_seen, last_seen, risk_id, feed_source, last_checked_at, last_check_kind, previous_severity`.
  The live risks (a retired one leaves the view and stays in `macro_core.tail_risk`); `previous_severity` is the last different severity a check recorded.
  While the tail-risks feed is `vault`, the live risks are the ones the latest `Blackswan Events.md` lists and `last_seen` is the note's own date.
- `macro_mart.tail_risk_history`: `risk_id, name, checked_at, kind, severity, summary, priced_in, gauges, source_url, retired`.
  Every check over the last 180 days, for a risk's escalation line.
- `macro_mart.holding_watch_state`: `as_of, symbol, source, name, asset_class, market_symbol, price, price_at, currency, change_24h_pct, change_basis, change_session, price_source, unavailable_reason, underlying_only, quantity, exposure_usd, material, driver_summary, source_url, recorded_at, no_clear_reason`.
  Every row of the latest day the watch ran: one per watched name.
- `macro_mart.holding_event_state`: `id, symbol, kind, name, starts_at, ends_at, date_precision, date_estimated, date_text, source_url, catalyst_id, first_seen, last_seen`.
  Events in the next fourteen days, and those that ended less than a day ago.
- `macro_mart.manual_position_state`: `id, symbol, name, venue, asset_class, quantity, note, created_at, updated_at`.
  The active manual positions, for the edit list.
- `macro_mart.watch_ticker_state`: `id, symbol, name, asset_class, note, created_at, updated_at`.
  The active watched tickers, for the edit list.
- `macro_mart.feed_freshness`: `feed, origin, last_run_at, last_finished_at, last_status, last_error, last_ok_at, last_ok_origin, rows`.
  One row per feed (`catalysts`, `tail_risks`, `holdings`): who sent the latest run (`openclaw:macro-desk` or `vault`), its status, and the last run that succeeded.
  A failed feed keeps its `last_ok_at`, so the page shows it stale rather than empty.
  Dry runs write no ingest run and never appear.
- `macro_mart.watchlist_change_state`: a read-only view for the Catalysts page, the last fourteen days of `macro_core.watchlist_change`, one row per ticker, `session_on` and move, so a weekend re-report of Friday's session is one row.
  `macro_core.watchlist_change` is written only while the holdings feed is `vault`.

### macro_quality

- `macro_quality.ingest_run(id bigserial pk, source text, run_kind text check in ('full','rescore','desk'), started_at, finished_at null, rows int, status text, error text null, feed text null check in ('catalysts','tail_risks','holdings'), origin text null)`.
  A desk submission is one row: `run_kind` `desk`, `source` `desk:<feed>`, its `feed` and `origin`.
  A dry run writes none.
- `macro_quality.finding(id bigserial pk, check_key text, subject text, sentence text, severity int, opened_at, closed_at null)`.
  One sentence per finding, written by the worker, rendered verbatim by the web app.
- `macro_quality.schema_migration(version text pk, filename text unique, content_hash text, applied_at timestamptz, duration_ms int)`.
  The migration ledger, written only by `supabase/migrate.py`.
  It is in an exposed schema and therefore readable by `authenticated` on the Data API, which is a deliberate widening: one operator benefits from reading the migration history in the dashboard, and the table states what was applied rather than any data.
  `supabase_migrations.schema_migrations` belongs to Accounting and is never read for a decision and never written.
- `macro_quality.gap(id bigserial pk, series_id uuid, series_key text, asof date, reason text, permanent bool, opened_at, unique(series_id, asof))`.
  `series_key` is denormalised on purpose: a gap that cannot be named without a second lookup gets rendered as a number.
  A `permanent` gap is a point-in-time source that was missed and can never be recovered.

Every check `lint_journal.py` performs becomes a `finding` with a stable `check_key`, including: probabilities summing to 100, four condition rows per phase call, a fresh phase read that may be out of date (`phase_read_stale`), three scenarios per set, every scenario target band parseable, the setup check covering every prior-week setup, every active holding carrying a holdings-check line, and a Scorecard Ledger row existing for each graded week.
Three checks exist only because the clock is daily: per-series freshness against `next_expected` rather than wall-clock, condition coverage (`machine_checked` against `manual` against `unevaluable`), and ledger integrity (no setup left `open` for more than one week ungraded).

## The condition expression form

Stored as `jsonb`. Resolved against `macro_core.series.key`. Values are in the series' declared unit.

```jsonc
{"op": "lt",  "series": "breadth_spx_above_200dma", "value": 45}
{"op": "gt",  "series": "hy_oas_bps",               "value": 300}
{"op": "between", "series": "brent",  "lo": 92, "hi": 112}
{"all":  [ {"op": "gt", "series": "vix", "value": 22},
           {"op": "gt", "series": "move", "value": 90} ]}
{"any":  [ ... ]}
{"n_of": 1, "of": [ ... ]}
{"predicate": "failed_retest",
 "args": {"series": "spx_cash", "level": 7450, "within_sessions": 5}}
{"manual": "Rallies fail at prior support"}

{"op": "lt", "series": "spx_cash", "field": "dma200_pct", "value": 0}
{"op": "eq", "series": "cftc_asset_managers_es", "field": "band", "value": "heavy"}
```

Operators: `lt`, `lte`, `gt`, `gte`, `eq`, `between`, `outside`.
Combinators: `all`, `any`, `n_of`.

`field` reads one named key from the observation's `extra`, and nothing else.
It performs no arithmetic, derives nothing, and cannot traverse: one key, one level deep, on a value the worker already computed and stored.
A field that is absent from `extra` resolves to `unevaluable`, exactly as a missing observation does.
Only `eq` is permitted against a text field such as the CFTC regime band.

This widens the expression form, which the conditions decision above argued against, and the reason it is justified is narrow.
`dma200_pct`, `dma200_slope`, `spread_2s10s_bps` and the CFTC `band` are already computed, already stored and already first-class conditions in the operator's own frameworks: "below a declining 200dma" is a standing phase-tracker row, not a novel idea someone wanted to express.
Leaving them unreachable does not keep the language narrow, it makes the phase trackers permanently score against a subset of their own recipe.
The accessor adds no operator and no arithmetic, so it cannot express anything that was not already measured.

Named predicates live in `worker/src/macro/predicates.py`, are listed in `config/predicates.md`, and each states its convention in its docstring and carries a unit test with a hand-computed expected value.
Do not invent or improve a predicate's convention; if one is ambiguous, ask rather than choosing.
The initial set is `failed_retest`, `streak_intact`, `crossed_within`, `gate_at_entry`.

An expression referencing a series with no observation for the evaluation date resolves to `unevaluable`, never to false.

A predicate's lookback is clipped at the `activated_at` session of the judgment that owns it.
Without the clip a setup written on 2026-09-19 grades as having entered on 2026-09-16, three days before it existed, and its recorded R-multiple describes a trade that was never available.

## Sources and their cadence

`config/series.yaml` is authoritative.
This table is its summary and must stay in step with it.

| Group | Series | Cadence | Source | Path |
| --- | --- | --- | --- | --- |
| Price & Levels | S&P, Dow, Russell front-month plus cash; Nasdaq cash | session | Yahoo chart API | deterministic |
| Price & Levels | Gold, Silver, WTI, Brent, DXY, VIX | session | Yahoo chart API | deterministic |
| Price & Levels | 30Y, 10Y, 2Y par yields | daily | US Treasury par curve | deterministic |
| Price & Levels | BTC, ETH, SOL | daily, calendar day | CoinGecko | deterministic |
| Sentiment | MOVE | session | Yahoo | deterministic |
| Sentiment | Fear & Greed, crypto | daily | Alternative.me | deterministic |
| Sentiment | CFTC asset managers, leveraged funds | weekly, Tue asof, Fri release | CFTC TFF API | deterministic |
| Sentiment | HY OAS, IG OAS | daily, one-day lag | FRED `fredgraph.csv` | deterministic |
| Sentiment | BTC dominance, funding skew | daily | CoinGecko, Binance | deterministic |
| Sentiment | S&P breadth, % above 200dma | daily | computed from constituents | deterministic |
| Sentiment | Fear & Greed, equities | daily | CNN dataviz graphdata API | deterministic |
| Sentiment | State Street allocation, risk appetite | monthly | search agent | 09:30 only |
| Flows | Net liquidity and its three components | weekly on WALCL | FRED WALCL, WTREGEN, RRPONTSYD | deterministic |
| Flows | Stablecoin cap, DeFi TVL, crypto OI, funding extremes, alt season | daily | DefiLlama, CoinGecko, Binance, CMC | deterministic |
| Flows | BTC and ETH ETF daily net flow, week to date, inflow streak | every US session | SoSoValue, checked against Farside | deterministic |
| Flows | Liquidations, 24h | daily | search agent | 09:30 only |
| Macro | Fed funds rate, next FOMC | event | search agent | 09:30 only |
| Macro | FedWatch quote, next FOMC | daily | search agent | 09:30 only |
| Macro | Tariff regime, geopolitical state | event | search agent | 09:30 only |
| Macro prints | Core and headline CPI, core and headline PCE, payrolls, unemployment, JOLTS openings, retail sales and its control group | monthly, on the release calendar | FRED | deterministic |
| Macro prints | ISM manufacturing and services | monthly | search agent | 09:30 only |
| Sentiment | FedWatch hike odds, next FOMC | daily | search agent | 09:30 only |
| Price & Levels | BTC 50-week moving average | weekly | computed from CoinGecko history | deterministic |
| Rates | Auction high yield and bid-to-cover, per tenor | event, scheduled irregularly | TreasuryDirect `TA_WS/securities` | deterministic |

The daily net flow is read and the other two are computed from it: the week to date sums the ISO week's sessions up to the asof, and a week with a session missing has no figure.
Tier 1 where SoSoValue and Farside agree within 5% or $10M, Tier 2 otherwise; a session a source has not published complete is missing, never zero.
A streak is a signed session count, not prose: `+2` is two consecutive inflow sessions, `-4` is four consecutive outflow sessions, and `0` a session whose net flow was exactly zero.
The `streak_intact` predicate needs an integer, and a `text` series may only appear in a `manual` condition.

The macro prints exist because almost every trigger clause ever written names one, and none of them was a series, which is the single largest reason trigger clauses type at 38% while phase conditions type at 88%.
A print is stored as the figure the clause refers to: the month-over-month percentage for CPI, PCE and retail sales, the level change for payrolls, the level for unemployment, JOLTS and ISM.
Where FRED serves an index rather than a change, the worker computes the change and stores that, because "core PCE at 0.2%" refers to the change and a condition written against the index level would be quietly wrong.

`fedwatch_hike_prob_next` is the numeric companion to the `text` FedWatch row and does not replace it.
The odds for the next meeting are the most-quoted number in any trigger the operator writes, and a `text` series can only ever appear in a `manual` condition.

`btc_50w_ma` is the crypto framework's anchor for Phase 1 and for both the 5 to 1 and 1 to 2 transition recipes, and it was the one crypto tracker condition that could not be typed at all.

**The auction series carry no tail, and none may be added.**
A tail is the high yield minus the when-issued yield at the bidding deadline, and Treasury does not publish when-issued, so it cannot be computed from anything this system holds.
`averageMedianYield` is in the same feed and is not a substitute: the median of accepted competitive bids describes the auction's own distribution, while a tail is the gap between the auction and a market quoted outside it.
Subtracting one from the other produces a number in basis points that would look exactly like a tail and move roughly with one, which is precisely why the absence is stated in the fetcher, in `config/series.yaml` and in the worker's README rather than merely left empty.
What the feed does serve, bid-to-cover and high yield, is official rather than quoted, and a clause written against those is a better condition than one written against a tail.

A clause that names a tail must therefore be narrowed to what its expression checks before it is typed.
Typing half a claim while its text still asserts the whole thing makes the condition report `met` on half its evidence, which is worse than leaving it manual.

**A producer-stated next date beats a rule-derived one.**
An auction has no cadence, so its release rule is `event`, but the Treasury schedule publishes the next date per tenor and the fetcher carries it in `extra.next_expected`.
Where a producer states when the next observation is due, that date is a fact and the release rule's inference is not, so the stated one wins; where neither exists, nothing is invented and the series simply has no next date.
This is what lets an event series go `due` and then `overdue` at all, which a bare `event` rule can never say.
Freshness compares the instant rather than the date, because an auction is struck in the New York afternoon and has not happened at 21:00 in Singapore on its own day.

The `deterministic` path runs on both the 09:30 and 21:00 runs.
The `09:30 only` path carries its previous observation forward *as an observation with its own older `asof`*, which is not the same as carrying a value forward into a day it did not describe.

One defect is carried over from the current pipeline and must be fixed during migration: `2026-W39.points.json` holds two rows labelled `Next FOMC`, because the search agent returned the FedWatch reading under the wrong metric label and the merge mapped both to the same row.
The FedWatch series gets its own key.

## The runs

**09:30 Asia/Singapore, full**, the worker under launchd.
Parse the three vault notes the old OpenClaw crons write (the catalyst monitor at 08:45, the watchlist at 09:15, the Blackswan scan on Monday at 06:00), each only while its `MACRO_FEED_SOURCE_*` is `vault`.
Fetch every deterministic source.
Normalise, upsert observations, record gaps.
Evaluate every condition, target band and setup gate, and the drift since the active call.
Recompute `macro_mart`.
On Monday, additionally: grade the prior week's setups, append `setup_grade`, and recompute calibration.

**09:40, narrative**, a queued Claude run the runner schedules on each Singapore weekday once the full run has landed.
Mode A of `agent/SKILL.md`: the search agents for the series whose `fetch_path` is `agent_0930`, then a rescore so the mart reads what they wrote.

**Monday 10:00, author**, queued once the day's narrative is `done`.
Mode B: grade the week, propose the next, stop at `proposed`.
If Monday produces no proposal, the author is owed again on Tuesday, after Tuesday's narrative or without it if that narrative failed twice.

A failed scheduled narrative or author is retried once, fifteen minutes after it ended, and then left for the operator.

**Any time, amend**, queued by the Desk's Refresh.
Re-read the active call against `macro_mart.drift` and propose an amendment, or finish with `NO_CHANGE` and a reason.

**21:00 Asia/Singapore, rescore.**
Fetch every deterministic source.
Evaluate.
Recompute `macro_mart`.
No LLM call is made on this path, and a failure to reach an LLM cannot fail this run.

The worker writes one `macro_quality.ingest_run` row per source on each of its runs.
Findings are synced once, after every step (Monday's grading included), and a run closes only findings whose check it evaluated again, so a finding the 21:00 rescore cannot raise (vault, archive, grading, the authoring agent's) is left open for the writer that can.

**The desk feeds**, on OpenClaw's schedule: catalysts at 08:40, the holdings watch at 09:05, the daily tail-risk recheck at 09:20 and the full tail-risk scan on Monday at 06:10.
Each is a `macro-agent desk ... submit` (`docs/DESK.md`), one `ingest_run` per live submission.
What they write reaches `macro_mart.catalyst_window` on the worker's next run; the holdings and tail-risk views read `macro_core` directly.
The runner ticks every two minutes, runs at most one Claude session at a time, and applies at most one recorded decision per tick, followed by a rescore.

## Web app

Root directory `web`.
Five routes.

- `/`, the **Desk**: the drift since the call, the bands closest to changing, the dated events that settle a clause, the Refresh control and any proposal awaiting a decision, then the call itself (house view, scenarios, phase calls, setups, strategy).
- `/data`, the evidence: every snapshot row except the auctions and the prose policy rows, with the day's unusual moves ranked in standard deviations of each series' own daily changes.
  A row answers to `#series-<key>` and opens on arrival.
- `/catalysts`: the thirty-day calendar with the scenarios each catalyst settles, tail risks, watchlist moves, the policy state and the auction results.
- `/history`: pick a date, render the same page as it stood then, and diff two dates.
- `/calibration`: both ledgers, the rolling calibration read, and R1 to R12 with their teaching weeks and fire counts.

`/` and `/data` are one reading under one set of query keys, so walking between them never refetches.
`/macro` redirects to `/`.

One module reads `macro_mart` and `macro_quality` through `supabase.schema(...)`.
Every row passes a hand-written validator, and the reading that comes back carries both the rows that validated and a named reason for each that did not.
Envelope failure and row failure stay separate: a failed query means nothing is shown; a trustworthy response holding one malformed row keeps its valid neighbours and counts the bad one.

Freshness is declared per row by the worker and the app never re-rates it.
What the app does check is the schedule: when a 09:30 or 21:00 run has passed more than forty-five minutes since the last evaluation, the reading is marked stale and says how many runs were missed, because a row rated `fresh` by a worker that has since stopped is not fresh.
A reading-level state is the worst *fault* among its rows, not the worst state: `due` and `asserted` are normal conditions of a board of many series on six release rules, and if they could become the headline the page would sit permanently amber and the one week a source actually failed would look like every other week.
Only an envelope fault dims a body; a single faulted series dims its own cells and leaves its neighbours alone.

Rendering the page as it stood on a past date, and comparing two such readings, is selection and not derivation: `/history` diffs rows it was served and computes no number from them.

TanStack Query with `staleTime` set to the run interval and no socket.
A render tick re-renders once a second so an age stays true without new data; the clock is sampled at render, never carried on the tick.

## Design system

`web/app/globals.css` ports `:root`, the chip vocabulary and the panel chrome from Accounting verbatim, which ports them from GreenfieldDash's `packages/ui/tokens.css`.
Token values are copied exactly: a page that renders `#64bd8b` instead of `#63bc8a` is a different product wearing the same name.
No CSS framework. Dark only. IBM Plex Mono for every number, `font-variant-numeric: tabular-nums`, fixed column widths.
Nothing is imported across the repository boundary, by decision.

Two additions this system needs and Accounting does not, defined once in `:root` beside the existing tokens:

- Condition status: `met`, `not yet`, `failed`, `unevaluable`, `void`. `unevaluable` uses the existing `--state-unrated`; `void` uses `--faint` with a strikethrough rule.
- Band occupancy: inside the scenario comparison table a target renders as the current value, a state word (`IN`, `OUT`, `NEAR`, `JUDGE` for a manual target, `—` for an unevaluable one) and the band's bounds.
  The drawn extent it replaced was correct for studying one band and wrong for the job: a reader scanning three scenarios across a row is comparing bands to each other, and reading down a column of state words is immediate in a way three 76-pixel extents are not.
  The extent variant is kept for any single-scenario surface.
  Direction colour is used in neither, for the same reason the comparison table does not use it: a band is a statement about a forecast, not about a position moving in your favour.
  If a band rendering needs a legend it has already failed.

## Environment

Worker: `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `VAULT_PATH`.
`MACRO_FEED_SOURCE_CATALYSTS`, `MACRO_FEED_SOURCE_TAIL_RISKS` and `MACRO_FEED_SOURCE_HOLDINGS` are each `vault` (the default: the worker parses that Obsidian note) or `desk` (the desk writes the tables and the worker stops reading the note); any other value stops the worker at start.

Desk: `MACRO_DESK_DATABASE_URL`, the `macro_desk` login on the session pooler (user `macro_desk.<project-ref>`), written into `.env` by `supabase/desk_role.py` and never by hand.
Without it every desk command exits 2 (`environment`); only an operator running a command by hand may fall back to `DATABASE_URL`, with `macro-agent desk --use-database-url`, and `desk compare` always connects with `DATABASE_URL`.
Market data for the holdings watch needs no key: Yahoo's chart API (sent `Mozilla/5.0` and nothing else), Hyperliquid's info API and CoinGecko's simple price.

The worker connects over `DATABASE_URL` (the Supabase session pooler, asyncpg), not through the Data API.
`macro_raw` and `macro_core` are deliberately not exposed, so PostgREST cannot reach them at all, and a `macro_mart` flush has to be one transaction, which PostgREST cannot span.
The two Supabase variables are carried for the authoring agent but it does not write through the Data API either, for the same two reasons: `macro_core` is unexposed and a scenario set has to be written in one transaction.
No `FRED_API_KEY` is needed: the keyless `fredgraph.csv?id=<SERIES>` endpoint serves every series this system reads.
FRED allowlists the User-Agent product token: `curl` and `httpx` defaults answer in 50ms, while a browser string or a custom `macro-worker/0.1` token is accepted and then never sends a byte.
`worker/src/macro/http.py` sets the agent per host and sends the client default everywhere else.
Yahoo gets the bare `Mozilla/5.0` and nothing more: it answers a full browser string, `curl` and `httpx` with 429.
TradingView, CoinMarketCap and CNN's dataviz API get the full browser string, and CNN also needs a cnn.com `Referer`.
Farside is read through `Client.get_as_browser`, which uses `curl_cffi` to present Chrome's TLS handshake, because Cloudflare refuses `httpx` and `curl` whatever the agent.
Web: `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`.
The service-role key never appears in `web/`.
`web/` also reads `NEXT_PUBLIC_FIXTURES`, a development-only switch never set on a deployment.
