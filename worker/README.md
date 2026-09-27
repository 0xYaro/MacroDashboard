# worker

The twice-daily Python process behind the macro terminal.
It fetches the deterministic sources registered in `src/macro/fetchers/__init__.py`, parses the three markdown files the old OpenClaw crons write for each feed still set to `vault`, evaluates every condition, target band and setup gate, and recomputes `macro_mart`.

Read `docs/DECISIONS.md` and `docs/CONTRACT.md` first.
`CONTRACT.md` defines every table this process writes, `config/series.yaml` defines every series it may write to, and `config/predicates.md` defines the four path-dependent predicates.
None of those three is owned by this directory and none of them is copied into it.

Repository root: `/Users/rayclaw/GreenfieldFund/MacroDashboard`.

## Layout

```
worker/
  pyproject.toml              uv project, Python 3.12, ruff + mypy strict + pytest
  src/macro/
    main.py                   the two runs, and the command line that starts them
    config.py                 os.environ only; python-dotenv is loaded in main.run
    model.py                  the normalized vocabulary; no vendor field name appears on it
    series.py                 config/series.yaml into typed records, and what it refuses
    release.py                pure: the release-rule grammar and the freshness rating
    clock.py                  pure: the NYSE calendar, the federal calendar, the two run times
    units.py                  pure: Decimal at the vendor edge, and the conversions it will make
    observations.py           pure: the read model, and the one refusal that makes `unevaluable` mean something
    expr.py                   pure: the condition expression form, in three-valued logic
    predicates.py             pure: failed_retest, streak_intact, crossed_within, gate_at_entry
    evaluate.py               pure: macro_core and today's tape into every macro_mart row
    calibration.py            pure: Brier and the rolling read, ported unchanged
    grading.py                pure: the Setup Ledger, the Scorecard Ledger, and the lint checks
    premise.py                pure: the premise-moved rule
    drift.py                  pure: what has moved since the active call, re-evaluated at its anchor
    vault.py                  pure: the three cron files, and every way they can fail to parse
    families.py               pure: the recurring family and category of a catalyst, from its name
    archive.py                pure: which catalysts happened, and the backfill file, as archive rows
    store.py                  the only module that talks to the database (asyncpg)
    http.py                   the only module that makes an outbound request
    fetchers/                 one module per source; `normalize` in each one is pure
  tests/
    golden/                   real captures from the live public endpoints, from 2026-09-21 on
  deploy/                     two launchd user agents (the worker, the run queue), an installer and an uninstaller
```

Every module above the `store.py` and `http.py` line is pure.
That is what makes the golden tests and the hand-computed predicate tests possible: a captured payload in, only `model` records out, with no socket and no clock in between.

## Running it

```bash
cd /Users/rayclaw/GreenfieldFund/MacroDashboard/worker
uv sync
uv run ruff check
uv run mypy
uv run pytest
```

A dry run needs no database and writes nothing:

```bash
uv run macro-worker --rescore --dry-run
```

It fetches every deterministic source, normalises, and prints the rows as JSON on stdout with the log on stderr, so `--dry-run | jq` works.

The real process is run from the repository root, so `python-dotenv` finds the environment file:

```bash
cd /Users/rayclaw/GreenfieldFund/MacroDashboard
uv run --project worker macro-worker --full      # the 09:30 Asia/Singapore run
uv run --project worker macro-worker --rescore   # the 21:00 Asia/Singapore run
```

`--at <iso instant>` pins a run to a past moment, which replays it against the same instant the original used.

`--archive-backfill <path>` loads a reviewed JSON array of past catalysts into `macro_core.catalyst_occurrence` and exits.
It derives each row's key and family exactly as the daily run does, refuses a file that names one event twice, and is an upsert, so loading the same file again changes nothing.
With `--dry-run` it validates the file and prints the counts without connecting.
The 2026 backfill lives in `worker/data/catalyst_backfill_2026.json`.

`--breadth-backfill <YYYY-MM-DD>` rebuilds `breadth_spx_above_200dma` for every session from that date to the last close, from two years of Yahoo closes per constituent, and exits.
It reads about 500 symbols at Yahoo's 2.5 s pace, about twenty-one minutes, fills only the sessions not already stored (`--breadth-overwrite` replaces those too), and clears any gap it fills.
With `--dry-run` it prints the rows as JSON and connects to nothing.

`--etf-backfill` loads every session of US spot BTC and ETH ETF flows SoSoValue serves (about 300), checked against Farside, with the week-to-date and streak series derived from them, and exits.
It is the scheduled source's own fetch and arithmetic over the whole history rather than the fourteen-day replay window, takes a few seconds, fills only the readings not already stored with a value (`--etf-overwrite` replaces those too), and clears any gap it fills.
With `--dry-run` it prints the rows as JSON and connects to nothing.

## The two runs

**09:30 Asia/Singapore, `--full`.**
Parse the three vault files the old OpenClaw crons write (the catalyst monitor at 08:45, the watchlist at 09:15, the Blackswan scan on Monday at 06:00), each only while its `MACRO_FEED_SOURCE_*` is `vault`.
Archive every live catalyst that has started while the monitor still listed it into `macro_core.catalyst_occurrence`.
Fetch every deterministic source.
Normalise, upsert observations, record gaps.
Evaluate every condition, target band and setup gate, and the drift since the active call.
Recompute `macro_mart`.
On a Singapore Monday, additionally grade the prior week and recompute the calibration read.

**21:00 Asia/Singapore, `--rescore`.**
Fetch every deterministic source.
Evaluate.
Recompute `macro_mart`.

Both runs sync `macro_quality.finding` once, as their last step, so the findings Monday's grading raises are written with the rest.
A run closes only the findings whose check it evaluated again: the rescore reads no vault, archives nothing and grades nothing, so it leaves those findings, and the authoring agent's, open for the writer that can close them.

Drift re-evaluates the active call at its anchor, the last US session that closed before the set was activated, and compares that with now.
A tail risk the weekly note or the desk retired is never counted as going acute.

The rescore makes no LLM call, reads no vault file and imports nothing that could.
That is not a convention this package promises to keep: there is no LLM client anywhere in it, so a model being unreachable cannot fail this path because there is nothing on it to be unreachable.

## Environment

Read from `os.environ`.
`python-dotenv` loads a `.env` at process start; nothing in the package opens a file to find one.

| Variable | Required | Meaning |
| --- | --- | --- |
| `DATABASE_URL` | for writing | the Supabase **Session pooler** URI, port 5432. Omitted in `--dry-run`. |
| `VAULT_PATH` | no | defaults to `/Users/rayclaw/Obsidian/rayobs`. Read only, always. |
| `SERIES_YAML` | no | defaults to `../config/series.yaml`. |
| `PREDICATES_MD` | no | defaults to `../config/predicates.md`. |
| `DATABASE_SSL` | no | `require` by default. `disable` exists for a local TCP cluster. |
| `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` | no | read and carried so an existing `.env` does not have to change. Neither this worker nor the authoring agent writes through the Data API; see below. |
| `FRED_API_KEY` | no | not needed and not used. `fredgraph.csv?id=<SERIES>` is keyless for every series this system reads. `config.load` still accepts the variable so an existing `.env` does not have to be edited. |
| `LOG_LEVEL` | no | defaults to `INFO`. |
| `MACRO_FEED_SOURCE_CATALYSTS`, `MACRO_FEED_SOURCE_TAIL_RISKS`, `MACRO_FEED_SOURCE_HOLDINGS` | no | `vault` (the default) or `desk`, one per OpenClaw feed. `vault`: the 09:30 run parses that feed's Obsidian note (`Catalyst Monitor.md`, `Blackswan Events.md`, `Market Watchlist.md`). `desk`: `macro-agent desk` writes the feed's tables and the note is not read at all, so the worker can never retire what the desk wrote. Any other value stops the run at start. |

### The worker writes over Postgres, not PostgREST

`macro_raw` and `macro_core` are deliberately not exposed through the Data API, so every PostgREST write to them fails with PGRST106 whatever key is used.

Exposing them would fix that symptom and leave the real problem.
A `macro_mart` flush has to be one transaction, or the web app reads a snapshot from one run beside a condition state from the next and renders a reading that never existed.
PostgREST has no transaction spanning requests.
Postgres does, and `MartFlush.write` takes exactly one.

Use the **Session pooler** URI, on port 5432.
`db.<ref>.supabase.co` is IPv6 only and does not resolve from an IPv4 network, and the transaction pooler on port 6543 has no prepared statements, which `asyncpg` uses for every query.
`config.load` refuses a `:6543` URL at startup rather than letting it fail confusingly later.

## The sources, and what each one taught

### FRED is not blocked, it allowlists the User-Agent

`references/data-contract.md` in the vault skill states that FRED is unreachable at the network level from this machine and routes four series through search agents because of it.
That is false: `fredgraph.csv` answers in about 50 ms.

It is also the likeliest origin of the claim.
FRED allowlists the User-Agent **product token**, and the failure is silent: an agent it does not recognise gets its connection accepted and then never receives a byte, so the request dies on the read timeout rather than on a status code.
Measured here, repeatedly, against `fredgraph.csv?id=WALCL`:

| User-Agent | Result |
| --- | --- |
| none, or `httpx`'s own default | 200 in 0.08 s |
| `curl/8.7.1` | 200 in 0.05 s |
| the Chrome browser string | read timeout |
| `macro-worker/0.1`, and four variants of it | read timeout |

So a polite custom agent reads exactly like a network block, and setting one here would cost five series a day and look like an outage.
`http.py` sets the agent per host and leaves the library's own default everywhere else.
Yahoo gets the bare `Mozilla/5.0`, because it answers a full browser string, `curl` and `httpx` with 429.
TradingView, CoinMarketCap and CNN's dataviz API get the full browser string, and `fetchers/cnn.py` adds the cnn.com `Referer` CNN also checks.
Farside goes through `Client.get_as_browser` (`curl_cffi`), below.

A second undocumented thing: `fredgraph.csv?id=X&cosd=YYYY-MM-DD` resets the connection under repeated requests from one client.
Measured here across dozens of calls: the bare `?id=X` form answers every time and the `cosd` form fails most of the time, with `HTTP/2 stream 1 was not closed cleanly: INTERNAL_ERROR` or a plain timeout.
The full series is a few tens of kilobytes, so the parameter buys nothing and costs the fetch. `fetchers/fred.py` never sends it.

Units are not in the payload, so each series' unit is asserted in that module against the FRED series page.
`BAMLH0A0HYM2` and `BAMLC0A0CM` are percent and are stored in basis points, because every condition referencing them is written in basis points.
`WALCL` and `WTREGEN` are millions of dollars.
**`RRPONTSYD` is billions**, which is the odd one out and the one that would quietly shrink net liquidity by the size of the RRP if it were read like its two neighbours.

Net liquidity is `WALCL − WTREGEN − RRPONTSYD`, all three struck on WALCL's own Wednesday.
A component with no reading at that exact date withholds the whole figure: a total missing a term is not a smaller total.
The old pipeline assembled this from a WALCL point an LLM had found, a TGA balance from FiscalData and an RRP operation from the New York Fed, three producers with three as-ofs.

### Breadth: computed from the constituents, with S5TH as the cross-check

`% S&P above 200dma` used to be Barchart's `S5TH`, read off TradingView's symbol page.
TradingView publishes each session's bar around 09:15 UTC the next day, and the 09:30 Singapore run is 01:30 UTC, so every morning opened a gap for a session that had not been published yet.

`fetchers/breadth.py` now computes the figure.
One query to TradingView's scanner (`POST https://scanner.tradingview.com/america/scan`, `symbolset SYML:SP;SPX`) returns every line of the index with its close and 200-day simple average, within minutes of the close, and the `[1]` and `[2]` offsets return the two sessions before, so each run re-states three sessions and the series is replayable.
Each line's bars are filed under their own dates rather than by offset: before the open a line that traded pre-market has already rolled, and its yesterday sits at `[1]`.
Each company votes once, through its Class A line (GOOGL, FOXA, NWSA), a line with no 200-day average is not counted, and a session resting on fewer than 480 companies is refused.
An older session is re-stated only when every company counted on the newest one is counted on it too: once a line has rolled pre-market its third session back falls off the scan, and re-stating from that subset would overwrite a whole figure with a partial one.

`S5TH` is still read.
When it carries a session this run computed, it travels in `extra` as `s5th_close` and `s5th_divergence`, and more than three points apart opens a `breadth_divergence` finding.
The two differ by about half a point by design: on 2026-09-22 the computed figure was 49.00 and `S5TH` 49.70, because Barchart's averages appear to be dividend-adjusted and count all 503 lines.
When the scanner cannot be read at all, the `S5TH` bar fills its own session as a Tier 2 fallback, only if nothing is stored for it, and opens a `breadth_fallback` finding.

History further back is `--breadth-backfill`, above, which applies the same arithmetic to Yahoo's `close` (split-adjusted, not dividend-adjusted, the same series the scanner averages).

### ETF flows: SoSoValue, checked against Farside

`fetchers/etf_flows.py` writes six series: `btc_etf_net_flow` and `eth_etf_net_flow`, one reading per US session, and the week-to-date flow and the inflow streak computed from them.
Until 2026-09-24 the weekly flow and the streak were a search agent's, on the 09:30 run only.

SoSoValue's `historicalInflowChart` (`POST https://api.sosovalue.xyz/openapi/v2/etf/historicalInflowChart`, body `{"type": "us-btc-spot"}` or `us-eth-spot`) is keyless and answers about 300 sessions of daily totals in raw USD, so the series are replayable and each run re-states the last fourteen days.
Farside's all-data pages carry the same flows per fund in US$ millions, negatives in parentheses.
Farside answers 403 to `httpx` and `curl` whatever the User-Agent, because Cloudflare fingerprints the TLS handshake, and 200 to `curl_cffi` presenting Chrome's; `http.Client.get_as_browser` is that one request, and the table's cell text is what `macro_raw.batch` keeps, not the 800KB page.

A session is Tier 1 when the two agree within 5% or $10M, Tier 2 with an `etf_flow_divergence` finding when they do not, and Tier 2 when only one carries it.
A session that is Tier 2 only because one source was missing on this run does not replace a stored row a past run verified at Tier 1; a disagreement is evidence and always re-states.
On the 300 sessions of 2026-09-24 they agreed on all but two BTC sessions, 8 and 10 April 2026.
A Farside row counts only when every fund that has reported before reports on it: a session still in progress reads `-` in every fund and `0.0` in its Total, and that zero is not a reading.
The week to date sums the ISO week's sessions up to the asof and is withheld while any of them is missing.
The streak is the signed run of same-signed sessions ending at the asof, walked back over NYSE sessions so a holiday never breaks it, `0` only for a session that netted exactly zero, and unknown rather than short when a session inside the run is missing.

### Yahoo rate limits a burst and stays cross

Fourteen symbols at 1.5 seconds apart trips it, and every request for the following hour answers 429.
Two defences: the pace is 2.5 seconds, and `http.Client` opens a breaker after two consecutive 429s from one host, after which every further request in that run gets one attempt and fails immediately.
The breaker changes how long the fault takes to discover and nothing else.
The source still reports `failed` with its sentence, every series it owns keeps its own older as-of, and the next run starts with the breaker closed.

**Most Yahoo tests run on an assembled payload; two run on captures.**
During the build, probing the endpoint rate-limited this machine (the cause turned out to be the user agent, above), so the first tests were written against a payload assembled in the test from a real `^GSPC` response: the in-progress bar, the null-padded holiday and the 200-day average still run on it, and the test's docstring says so.
`yahoo_es_f_5d.json` and `yahoo_gspc_5d.json` were captured from the live chart API on 2026-09-23 and hold the session-boundary rule: a 24-hour future keeps yesterday's settled bar while today's is running, and a cash index after its close keeps the closed session.
Everything else in `tests/golden/` is a byte-for-byte capture.

An in-progress bar is never stored.
Yahoo returns the running session as a bar like any other, and `meta.currentTradingPeriod.regular` is what says whether it has finished.
Storing it would put an unfinished session's close into a day that has not finished, which is the carry-forward rule pointed the other way.
Only the newest bar can be the running one: a 24-hour contract's "regular" period is the whole New York day, and reading every bar inside it as unfinished dropped the settled one before it.

Each run re-states every completed bar of the last 45 days, so a bar Yahoo served null and filled in later still lands.
The upsert replaces `extra` whole, so each re-stated bar carries its own 200-day read, computed from the closes up to that bar, exactly as a run on its own day stored it.

### Open interest is a proxy and says so on the row

The sum of `open_interest_btc` across the top ten exchanges by open interest, converted at spot.
`tier_floor: 2` in the registry says the series can never claim better, and the method travels in the row's `note`.

### A macro print stores the figure the clause refers to

CPI, PCE, payrolls, unemployment, JOLTS, retail sales and the retail control
group come from FRED on the deterministic path. Each one stores the figure a
clause names, which is usually not the figure FRED serves.

A clause says "core PCE at 0.2%". That is a month-over-month percentage, and
`PCEPILFE` reads 130.658. A condition written against the index level would be
false for ever and would look entirely correct, so the change is computed here
and the change is what is stored. Payrolls is the monthly level change in
thousands, because a clause says "payrolls under 125K". JOLTS is stored in
millions, because a clause says "JOLTS under 7.0M" while FRED serves 7,271.

FRED dates a monthly observation at the first of the month it describes and
`config/series.yaml` declares `asof=eom`, so the first is mapped to the month
end. Without that every monthly release rule computes its next print a month
early and the row reads overdue for a fortnight every month.

The retail control group is total retail and food services less motor vehicles,
gasoline, building materials and food services. FRED carries every component and
no aggregate, so the four are subtracted from the total exactly as net liquidity
is computed, and the whole figure is withheld when any one of the five is
missing for the month.

ISM is not here. `NAPM` and `NAPMNOI` are discontinued and both answer 404,
verified against the live endpoint, so the two ISM rows stay on the 09:30 search
agent rather than being given a fetcher that cannot work.

`fedwatch_hike_prob_next` is declared in the registry so a condition naming it
validates, and has no fetcher here: CME's FedWatch page is a client-rendered
application with no keyless endpoint behind it.

### Treasury auctions, and the tail that is deliberately absent

`auction_high_yield_<tenor>` and `auction_bid_to_cover_<tenor>` come from
TreasuryDirect's keyless `auctioned` endpoint, one pair for each of the seven
nominal tenors.

**There is no `auction_tail` series and there must not be one.** A tail is the
high yield less the **when-issued** yield at the bidding deadline, and Treasury
publishes no when-issued: it is a dealer quote from a grey market and it is in
this feed nowhere at all.

`averageMedianYield` is in the feed and is not a substitute. The median of
accepted competitive bids describes the auction's own distribution; a tail is
the gap between the auction and a market quoted outside it. Subtracting one
from the other gives a number in basis points that would look exactly like a
tail, move roughly with one, and be a different quantity, and every reader
would assume it meant what the word means. Nobody should add one later from a
news scrape either: if a tail is genuinely needed it has to arrive as its own
series from a source that publishes when-issued.

The row carries `extra.tail = null` beside a `tail_note` saying so, and it
carries `average_median_yield` and `low_yield` unmixed, so the temptation is
visible and answered rather than merely absent.

**A clause whose text says "tail" must be narrowed before it is typed against
these.** "Clears without a second record tail" becomes a bid-to-cover floor and
a high-yield ceiling per tenor. Typing half a claim while the text still asserts
the whole thing makes the condition report `met` on half its evidence, which is
worse than leaving it manual.

**The filter is not optional.** The same endpoint serves bills, TIPS and
floating-rate notes. A TIPS reopening on 2026-09-17 carries
`originalSecurityTerm: 10-Year` and `highYield: 2.6530`, which is a *real*
yield; the nominal ten-year auctioned on 2026-09-09 at 4.8340. Unfiltered, the
ten-year series would take 2.653 as its newest reading: plausible alone, wrong
in every condition, and invisible on a row that says only "10-Year auction,
2.65%". The discriminators are stated in the feed (`tips`, `floatingRate`,
`securityType`) and this worker reads them rather than inferring anything.

Keyed on `originalSecurityTerm`, so a reopening counts as its original tenor:
the 19-Year 11-Month bond auctioned on 2026-09-15 is the 20-Year, and it is
W39's "the 20Y tailed 2 bp at a record 5.42%".

### An event series can state a schedule it was told

An auction has no cadence, so `config/series.yaml` declares `event` and the
release rule can derive nothing. The date is still knowable: TreasuryDirect's
companion `upcoming` feed publishes it, the fetcher carries it in
`extra.next_expected`, and `evaluate` prefers a stated next date over a derived
one.

A stated date is a fact from the producer; a derived one is a rule's inference.
Where both exist the fact wins, and where only the inference exists nothing is
invented to replace it. It also lets such a row be `due` and then `overdue`,
which `event` alone could never say: right for a series with no schedule, wrong
for one whose schedule is simply published somewhere a release rule cannot
reach.

### A setup says what it is measured against

`macro_core.setup.series_id` names the series its entry, invalidation and target are levels of, and `store` reads it rather than deriving it.

A setup with a null `series_id` is not scored, and it is still shown.
It gets a `setup_state` row with `series_key` null, `gate_state` unevaluable, `entry_state` "not triggered", no R-multiple, no ledger row, and a `setup_series_missing` finding.
Null there is a real state, not a missing value: the page renders it as "names no series, not scored" and can say why it is sitting there ungraded.
Dropping the row instead would make a setup nobody could score indistinguishable from one nobody proposed.
Deriving the series from the setup's name works until it does not, and a Nasdaq setup measured against the S&P would produce an entry state, an R-multiple and a Setup Ledger row that are all wrong and none of them visibly so.
One unscoreable setup costs itself and not the other three.

`setup_state.series_label` carries the series' display name beside the key, so a surface showing the levels can name what the numbers are numbers of without a second lookup.

### A band's comparator is part of the forecast

`scenario_target_state.comparator` carries what the target was written as, and it is not decoration.
`lo` null with `hi` set means "below X" under a `lt` comparator and "we never wrote the floor" under a `between` one.
Those are a forecast and an authoring defect, they render identically from the bounds alone, and a wrong comparator there produces a confidently wrong reading rather than a blank.

`scenario_target_state.text` carries a manual target's own words.
A manual target has no numbers at all and still counts toward `targets_n`, so without its text it is a row with nothing in it.

### A void clause says when, not just why

`macro_mart.condition_state.void_at` and `void_reason` are carried from `macro_core.trigger_clause`, never recomputed.
A clause voided last Tuesday stays voided last Tuesday however many runs have read it since, which is why `store.void_clauses` guards on `where void_at is null` and the evaluate step copies both fields through untouched.
`ConditionState` refuses to hold one without the other: a void clause with no reason is a clause that vanished silently, which is the whole thing the premise-moved rule exists to prevent.

### The 50-week average is fifty weekly closes

`btc_50w_ma` is the crypto framework's anchor for Phase 1 and for both the
5-to-1 and the 1-to-2 transition recipes, and "BTC holds above the 50W MA" was
the one crypto tracker condition that could not be typed at all.

It is the mean of the last fifty *weekly* closes, not a 350-day mean of daily
ones. A week's close is its final daily point, ISO weeks ending Sunday, which is
the boundary every crypto weekly candle already uses. A week that has not
reached its Sunday is dropped, because averaging a partial week in would move
the figure by whatever the last three days did.

The keyless `market_chart` serves 366 daily points at `days=365`, which is
fifty-two weeks: margin for two and no more, and `days=max` answers 401 without
a key. Fewer than fifty complete weeks yields null with a note, because a
forty-three-week average wearing the name of a fifty-week one is worse than an
absent figure.

### The cursor is the data

`supabase/migrations` declares no cursor table, and this worker does not want one.
The newest `asof` stored per series *is* the cursor, read straight out of `macro_core.observation`.
A side table can disagree with what was actually stored; this cannot.

Catch-up is computed from each series' declared release rule, never from how long the worker was down.
A gap on a `replayable: false` series is permanent: the source serves only the present moment, so that reading is gone, and every condition over it reports `unevaluable` rather than standing on yesterday's number.

## The freshness rating is this worker's alone

The web app takes each row's `freshness` as given and never re-rates it.

`fresh` means the next observation is not readable yet, whatever the gap to the last one.
`due` means it became readable and has not arrived.
`overdue` means it missed a release it was owed, after a day of grace.

The distinction is derived from the declared rule and from the *instant* the next print becomes readable, not from the date.
Monday's US session closes at 16:00 New York, which is 04:00 Tuesday in Singapore, so at 09:30 on Monday a series holding Friday's close is current.
Comparing dates would call that `due` from midnight, and with series on six calendars a large share of the board would sit amber every morning, which is the same as the board saying nothing at all.
`tests/test_release.py` asserts that the whole live registry reads `fresh` at both run times on a healthy Monday.

## Conventions worth knowing before changing anything

**Three calendars, never mixed.**
`Asia/Singapore` is the operator's clock and decides only when a run happens; no observation date is ever derived from it.
`America/New_York` decides the `asof` of every session and daily series.
UTC is the crypto day and the clock every stored timestamp is in.

The NYSE calendar and the US federal business calendar are also not the same calendar.
The NYSE is shut on Good Friday and open on Columbus Day and Veterans Day; the Treasury par curve is the other way round.
A series scheduled on the wrong one reports `overdue` on a day it was never going to print.

**Null, never zero.**
A figure that cannot be measured is `None` with a sentence beside it.
A Tier 4 reading is a null with a note, and `Observation.__post_init__` refuses to build one without the note.

**The `field` accessor reads, and does nothing else.**
`{"op": "lt", "series": "spx_cash", "field": "dma200_pct", "value": 0}` reads
one named key from the observation's `extra`, one level deep. No arithmetic, no
derivation, no traversal. A field absent from `extra` is `unevaluable`, exactly
as a missing observation is.

`expr.EXTRA_FIELDS` is the whole vocabulary, and both halves of it are checked:
an unknown key is a typo, and a known key on a series that does not carry it is
a condition that would be `unevaluable` for ever while looking perfectly well
formed. Only `eq` compares against a text field, and that is refused by
`expr.validate` at write time rather than at evaluation, so a malformed
condition fails on the morning it is written and not on the morning it first
matters. `evaluate.malformed_findings` re-checks what is already stored on
every run.

The field table is declared in `expr` and not imported from a fetcher, because
the expression form must not depend on one. A test asserts the declared
`dma200_pct` series are exactly the ones `fetchers.yahoo` computes it for.

**`unevaluable` is not a third kind of false.**
`expr.py` is three-valued, and the combinators propagate the unknown the way three-valued logic requires: `all` is false as soon as one term is false even if another is unknown, `any` is true as soon as one term is true, and `n_of` is unknown only while the known trues plus the unknowns could still reach the threshold.
A threshold is never renormalised because a clause could not be read.

**`failed` is a judgment, not a comparison.**
Nothing in `expr.py` produces it.
The two predicates that do, `failed_retest` and `gate_at_entry`, produce it exactly where `config/predicates.md` says they must: when a level was accepted rather than refused, and when a gate's verdict on a passed entry session is final.

**A manual condition is never computed.**
It has no expression.
Its status is read from `macro_core.condition_state_asserted` and carried through with `source = 'asserted'`, and a manual condition with no assertion is `unevaluable`, which is neither met nor failed.

**Two counters, not one, on a scenario.**
`bands_*` counts only the targets a machine can score. `targets_*` counts every target, and `targets_total` is the `n` that "targets hit x of n" uses at grading.
In W37's grading block the same grader counted one scenario's targets including its manual legs and the next scenario's excluding them, producing two incompatible denominators in one paragraph.
Carrying both makes that unrepresentable.

**`week_grade.other_grades` is an array, never an object.**
`[{"id": "S2", "prob": 30, "grade": "Partial", "note": "..."}]`, built by
`grading.other_grades`. The column defaults to `'[]'::jsonb` and the web app
reads an array, so an object keyed by scenario id parses as a malformed row,
rejects every ledger row it appears in, and empties `/calibration` entirely.
This worker emitted the object shape until it was caught by reading the three
parts against each other, which is why the assertion is now six tests rather
than a comment.

The base case is excluded, because the ledger's own "Other scenarios" column
excludes it and its reasoning is absorbed into `key_miss`.

**`note` is the grading reason, and it is never derived here.**
It is the sentence trailing the grade in section 0, written at grading time
looking backward at what happened. It is not the scenario's `positioning`,
which is forward-looking posture written a week earlier; substituting it would
put last week's intentions in the column that explains last week's outcome, and
it would read plausibly enough that nobody would catch it. This worker briefly
did exactly that.

Nor is it synthesised from the band counts. A scenario graded without a reason
gets an empty string, because a generated sentence that reads like a human one
is worse than a blank: the ledger's value is that its prose was written by
someone who thought about it, and twelve weeks of those sentences are what
R1 to R12 were distilled from.

**A prose row and a failed fetch are not the same shape.**
The rule that a null `value` needs a note is keyed on `value_kind`, not on the
null. The five Macro and Fiscal State rows store `value` null with their
content in `display` and no note; a `numeric` series with a null value still
has to say why. Without the distinction a Tier 4 failure renders as a macro
state.

**`macro_core.event` is not this worker's to write.**
The dated event log comes from the 09:30 search agents and the authoring run.
If it ever moves in-house, per the scraper entry in `docs/FUTURE.md`, it must
be upserted on `(occurred_on, kind, md5(text))`, which migration 20260921001500 declares.
It is the one table where a replayed run used to grow the data rather than
rewrite it, and a later week's grading reads it back as what actually happened,
so a duplicate there corrupts the record the whole calibration series is scored
against.

**The Brier score is never forgiven.**
A scenario whose premise moved is graded normally and the ledger row carries the tag.
`brier` takes one argument and there is nowhere to pass it an excuse.

**A fresh phase call is held against its own four conditions.**
The phase is read fresh every Monday by the authoring run, and each fresh call's four conditions are what must stay true for that phase to hold.
On a fresh call a condition whose comparison is false has `failed`: the expression answers `not yet`, and there is no "yet" on something that must stay true.
`failed` counts them, and two or more raise `read_stale` and a `phase_read_stale` finding: this week's read may be out of date.
Nothing here reclassifies.
The next read is the next Monday's, and drift leads with the same sentence.

A `carried` call is one of the fourteen migrated journal rows, whose conditions counted toward the next phase.
They are scored exactly as written, `not yet` included, and never raise the flag.

**`stage` is carried, never derived.**
It is depth within the phase, authored with a reason, and this worker carries the authored value and leaves it null when none was authored.
Deriving it from the conditions would make two columns one measurement wearing two names.

**`ok` means complete, and only complete.**
A source declares the series it owes. One that delivers fewer records
`partial` with a sentence naming how many of how many and why, plus a
`source_incomplete` finding. One that delivers nothing records `failed`. `ok` is
reserved for a full pass.

This was wrong until the first live run found it. Yahoo was rate-limited, the
breaker opened after two 429s, and the run recorded `yahoo | ok | 1 | -` on a
source that owes fourteen series. No error, no finding, no gap. The screen was
still honest, since those thirteen rows read `overdue` and nothing carried a
stale value forward, but `macro_quality`, whose entire purpose is to reveal a
quietly failing job, said nothing at all.

Two things had to change. `fetch` returns a `Fetched` rather than a bare list,
because a fetcher that reads fourteen symbols used to gather its per-symbol
faults into a local list and drop them the moment one symbol succeeded, so the
"why" did not exist by the time anything could report it. And `status` is
computed from declared against delivered rather than from whether an exception
escaped.

A Tier 4 null counts as delivered: a withheld figure with a note beside it was
reported, and the reason is on the row. Missing means no row at all.

A fault and a notable event stay different things. Findings do not enter into
`status`, so a source that delivered everything it owes is `ok` even when it
opened a finding about what it delivered.

**A gap is cleared when its reading is stored, permanent or not.**
A gap says the reading is missing, so once it is stored the row is a claim about the past.
`permanent` is a prediction made when the gap opened, and it can be wrong: breadth's 09:30 run once opened a permanent gap for a session its source had not published yet, and the gap stayed open beside the reading the 21:00 run stored.
Clearing joins against `macro_core.observation`, so a write that was refused clears nothing.
Without it the table drifts from meaning "is missing" to meaning "was once late".

Note what this means on a cold start: there is no cursor, so nothing is owed,
so no gap is written at all. On that first run `partial` and its finding are the
*only* record that anything went wrong, which is why they are load-bearing
rather than a nicety.

**No module does anything at import time but define things.**
`tests/test_no_import_side_effects.py` walks every module's AST and refuses a
loop, a branch or a mutating call at module level, with `if __name__ ==
"__main__"` the one exception.

It exists because of one bug.
`expr.REGISTRY` was a dict that `macro.predicates` filled on import, and `main` reaches `expr` through `evaluate` and never through `predicates`, so on the real run path it was empty: every predicate condition resolved to "predicate is not one of []" and scored `unevaluable` for ever.
Twenty-one predicate tests passed throughout, because importing the predicates in order to test them was itself what registered them.

The guard is structural rather than behavioural, because no number of extra
cases in those files could have caught it. A module that only defines things
cannot behave differently depending on who imported it.

**A void clause is kept and counted.**
`premise.voidings` returns only `void_at` and `void_reason`; it cannot drop a clause or change a threshold because it never names either field.

## What each failure mode does

None of these kills a run.

| Failure | What happens |
| --- | --- |
| A source is unreachable | `run_one` catches it, records `failed` on its `ingest_run` row, opens a `source_unreadable` finding, and opens a gap for every reading that was due. Every other source runs. |
| A host rate limits | The breaker opens after two consecutive 429s and the rest of that source fails fast. The next run starts clean. |
| One Yahoo symbol fails | That symbol costs itself and nothing else. Only all fourteen failing raises. |
| A vendor changes a payload shape | `normalize` raises `FetchRefused` with a sentence, the source reports `failed`, and no row is written. It cannot produce a plausible wrong number. |
| A vendor serves a holiday as `.` or `null` | The row is skipped, not read as zero. |
| A component of net liquidity is missing | The whole figure is withheld as a Tier 4 null naming the missing term. |
| A series key the registry does not declare | The observation is discarded and `series_key_unknown` is opened, rather than stored under a key no condition can resolve. |
| A vault file is missing or unreadable | `vault_file_unreadable` is opened and the rows it feeds keep whatever they last held. Nothing is reset to empty. |
| A vault line does not parse | That line becomes a finding naming the file and the excerpt. The rest of the file still parses. |
| Two catalysts share a name | The later row wins, because `external_key` is a hash of the name by contract, and `catalyst_name_collision` says so. The live file currently has one such pair. |
| A condition names a series with no reading today | `unevaluable`, with the series key and the date in the evidence. Never `not yet`. |
| A condition's expression is malformed | `unevaluable` with the diagnosis in the evidence. A fault is never a false. |
| A setup names no series | `setup_state` with `series_key` null, gate unevaluable, no R-multiple, and a `setup_series_missing` finding. Shown, not scored, not guessed. |
| A setup gate's own reading is missing | `gate_state: unevaluable`, never `blocked`. Reporting it as blocked would be indistinguishable from a gate that genuinely refused. |
| A `macro_mart` write is refused | The whole flush rolls back, `database_write_refused` is opened, and the next run redoes it. A stale mart beats half of one evaluation beside half of another. |
| The database is unreachable | `Store.connect` raises `WriteRefused`, whose `sentence` names the Session pooler and the IPv6 cause. The run exits 1 without a stack trace. |
| A migration is not applied | The same, with a sentence saying a migration is probably not applied and that it resumes on the next pass with no restart. |
| Replaying a run | Every write is an idempotent upsert on a natural key. Safe, always. |

## Schema drift is a test, not a habit

`tests/test_schema_drift.py` parses `supabase/migrations/*.sql` and checks two directions on every commit.
Every column `docs/CONTRACT.md` declares exists in the DDL, and every column the DDL gives a `macro_mart` table is a column this worker's insert actually names.

The second direction exists because the first one read clean while four columns were null on every row.
`comparator`, `text`, `series_key` and `series_label` were in the migrations, in the live database and in the contract.
The only place they were missing was the insert, which nothing compared against anything.
Its first run then found two more the same way, `condition_state.void_at` and `void_reason`.

The exception list is empty and there is a test that keeps it honest: an entry has to name a column that still exists and is still unwritten, so a stale excuse fails rather than accumulating.
`KNOWN_DRIFT` holds drift this worker has reported and does not own the fix for, as `xfail(strict=True)`, so the day the migration lands the test fails for passing and the entry has to be deleted.
It is also empty, and it has already been through that cycle once: `macro_mart.phase_call_detail` was missing `blind_phase`, `blind_reason`, `stage` and `stage_reason` because the view was never recreated after the columns were added to `macro_core.phase_call`, `20260922000200` rebuilt it, and the entry came out.
An exception that cannot fail for being fixed is just a permanent excuse.

Three parsing notes, each one a bug that was actually written.
An `alter table` is read to its semicolon, because one statement can add several columns and a per-`add column` pattern truncates it to the first.
A view is taken from its last `create or replace`, because an earlier definition is superseded.
The contract's parenthesised `check in (...)` spans are stripped before splitting on commas, or `'due'` parses as a column name.

## Deployment

`deploy/` holds two launchd user agents, an installer and an uninstaller.
Both are installed on the operator's Mac as of 2026-09-23.

```bash
deploy/install.sh     # renders both plists, creates the log directory, loads both agents
deploy/uninstall.sh   # unloads and removes both; logs are kept
```

`com.rayclaw.macro-worker` is this worker's two runs.
`com.rayclaw.macro-runner` ticks the run queue every two minutes (`macro-agent runner tick`, see `agent/README.md`); it needs `claude` on the PATH, which the installer resolves the same way it resolves `uv`.
Logs for both are in `~/Library/Logs/macro-worker/`.

The agent is **not** `KeepAlive`.
Accounting's worker is an always-on process with a websocket to hold open; this one is two scheduled runs a day and nothing in between, and a `KeepAlive` agent over a run that exits in ninety seconds is a crash loop that hammers every public endpoint it reads all day.

`StartCalendarInterval` fires at 09:30 and 21:00 in the machine's local time zone, and the mode flag is chosen from the local hour inside the agent, because one agent with two calendar intervals cannot pass different arguments to each.
launchd has no time-zone key, so `install.sh` prints the machine's zone and warns when it is not `Asia/Singapore`.
Both scripts derive every path from their own location, so moving the repository does not break them: reinstall and it follows.
