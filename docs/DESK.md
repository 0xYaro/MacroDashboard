# The desk tool contract

This is the contract the OpenClaw `macro-desk` plugin builds its typed tools from.
Each tool runs one `macro-agent desk ...` command in this repository and hands the JSON it prints back to the agent.
The command holds the database login; the agent never sees it.
Read `docs/DECISIONS.md` 2026-09-24, "OpenClaw writes the three feeds through the desk command", for why it is shaped this way.

## Setup

Once, on the operator's machine, after the migrations are applied:

```
python3 supabase/desk_role.py
```

It gives the `macro_desk` role a login and a generated password, and writes `MACRO_DESK_DATABASE_URL` into `.env` (owner-only; `.env*` is ignored by git).
`--rotate` replaces the password.
Role settings a migration adds (the timeouts of 20260925000200) need no re-run.

## How a tool calls a command

```
cd <repo> && uv run --quiet --project agent macro-agent desk <command> [--json-in -] [--dry-run]
```

- Input, where a command takes any, is one JSON object on stdin (`--json-in -`, the default) or in a file (`--json-in path`).
- Output is always one JSON object on stdout, on one line with no indentation.
  OpenClaw cuts a Codex tool result at 16,000 characters unless the agent raises the limit; `openclaw/install.sh` sets 64,000 for `macro-desk`.
  The default catalyst and tail-risk lists print well under 16,000; the calendar with `--full` does not.
  Nothing else is written to stdout; diagnostics go to stderr.
  This holds when the command fails too: see "When a command fails" below.
- Exit code 0: everything was accepted.
  Exit code 1: something was refused, and `refusals` says what; everything else in the submission was still applied (or, in a dry run, would have been).
  Exit code 2: the environment cannot support a run (no `macro_desk` login, the database unreachable or refusing the login, the worker package missing).
  Exit code 3: the run failed after it started (the database refused a statement, or a bug); nothing from the call was applied.
- The plugin should relay `refusals`, `warnings` and `voice` to the agent verbatim.
  Each refusal names the item (`items[3] ('U.S. August CPI')`) and what to fix, so the agent can resend only that item.
- `--dry-run` validates everything, including against the stored rows, archives the submission and what it would have done in `macro_raw.batch` with `shadow = true`, and writes nothing else.
  The trial week runs every submit with `--dry-run`.
- The command connects with `MACRO_DESK_DATABASE_URL` from the repository's `.env` (the `macro_desk` role).
  The plugin passes no credential and needs none.
  Without that variable every command exits 2: a tool call never falls back to `DATABASE_URL`, which can write far more than the three feeds.
  An operator running a command by hand may allow the fallback with `macro-agent desk --use-database-url <command>`; the plugin never passes it.
  `desk compare` is an operator's report and connects with `DATABASE_URL`.

### When a command fails

A failure prints the same shape as a refusal, with the category it falls in:

```json
{"ok": false, "applied": [], "refusals": ["The desk could not reach the database, so nothing was read or written. Try once more; if it fails again, report it."],
 "error": {"category": "database_unreachable", "exit": 2}}
```

The sentence is fixed per category, so it never carries a file path, a host name, a role name or a traceback.
stderr gets one line, `desk: <category>: <sentence>`; the traceback goes to stderr only when `LOG_LEVEL=DEBUG`.

| Category | Exit | Meaning |
| --- | --- | --- |
| `environment` | 2 | the desk is not configured (no `MACRO_DESK_DATABASE_URL`, a URL on the transaction pooler) |
| `worker_unavailable` | 2 | the worker package the rules come from could not be loaded (`WORKER_SRC`) |
| `database_unreachable` | 2 | no connection, or it was lost |
| `database_login_refused` | 2 | the database refused the `macro_desk` login |
| `input_unreadable` | 2 | `--json-in` names a file that cannot be read |
| `invalid_argument` | 2 | an argument the command does not take, or a malformed one (`--within-days 9999`, `--now yesterday`) |
| `database_permission` | 3 | the login may not make a read or write the command needs; a migration is probably missing |
| `database_schema` | 3 | a table, column or function is missing; a migration is probably missing |
| `database_constraint` | 3 | a write broke a database rule; a desk bug |
| `database_busy` | 3 | a statement ran or waited too long (the role's 30 s statement, 10 s lock and 60 s idle-transaction limits) |
| `database_refused` | 3 | any other statement the database refused |
| `internal` | 3 | a bug |
| `desk_unavailable` | 3 | the desk module itself failed to import; the refusal says so and stderr names the exception type (the authoring commands are loaded apart and keep working) |

Input that is not JSON, or not UTF-8, is a refusal (exit 1), because the agent can fix it.

| Tool | Command | Writes |
| --- | --- | --- |
| `desk_context` | `desk context` | nothing |
| `catalysts_list` | `desk catalysts list [--status tracked\|retired\|all] [--within-days N] [--search TEXT] [--full]` | nothing |
| `catalysts_submit` | `desk catalysts submit [--dry-run]` | the calendar |
| `tail_risks_list` | `desk tail-risks list [--all]` | nothing |
| `tail_risks_submit` | `desk tail-risks submit [--dry-run]` | tail risks and their checks |
| `positions_read` | `desk positions [--dry-run]` | an archive of the prices it computed |
| `holdings_watch_submit` | `desk holdings submit [--dry-run]` | the holdings watch |

## Rules every submission shares

- Every catalyst, tail risk, holding driver and holding event carries a `source_url`, an `https` link to the page that says it.
- Dates are sent as a structured `when` (preferred) or as `date_text` in the old Catalyst Monitor grammar.
  Both are parsed by the worker's own `vault.parse_when`, so the same words give the same row they always did.
- A `date_text` sent beside a `when` must agree with it: when the words name a date, it must overlap `when`'s days, give or take one day for the time zone.
  "2026-10-20" beside `{"date": "2026-10-14"}` is refused; "late October 2026" beside `{"date": "2026-10-28"}` and "release time TBD" beside anything stand.
  A dated `date_text` beside `{"unknown": true}` is refused.
- `estimated` is required wherever a date is.
  `false` means the source publishes the date.
  A month or an unknown date is always `true`.
  `false` next to words that hedge the date ("expected", "around 2026-10-20", "date TBD") is refused as a contradiction.
- Numbers are never taken from the agent.
  A tail risk names its gauges by series key and the command reads their values from the dashboard.
  A holding's price and 24h change are computed by the command from market data.
- Reader-facing text (a tail risk's `summary` and `priced_in`, a holding's `driver_summary`) is checked against `config/voice.md`.
  Problems come back in `voice` as warnings and never refuse anything.
  A field of one or two short sentences is not given a reading grade, which is noise at that length.
- An item carrying a control character (U+0000 and the rest of C0 but tab and newlines, DEL, a lone surrogate), in any string or key, is refused: PostgreSQL cannot store U+0000.
  The rest of the submission goes on, and the archived copy has the characters replaced with U+FFFD.

### The `when` object

| Shape | Precision | Example |
| --- | --- | --- |
| `{"date", "time", "tz"}` | exact | `{"date": "2026-10-14", "time": "08:30", "tz": "America/New_York"}` |
| `{"date"}` | day | `{"date": "2026-10-14"}` |
| `{"from", "to"}` with dates | window | `{"from": "2026-10-06", "to": "2026-10-08"}` |
| `{"from", "to", "tz"}` with times | exact, with an end | `{"from": "2026-10-06T13:00", "to": "2026-10-08T13:00", "tz": "America/New_York"}` |
| `{"month", "part"}` | month | `{"month": "2026-10", "part": "late"}`; `part` is `early`, `mid`, `late`, `end`, a range such as `mid-to-late`, or omitted for the whole month |
| `{"unknown": true}` | unknown | |

`tz` is an IANA zone and is required with a time, so a New York 08:30 is never read as Singapore's.
A day is stored at UTC midnight with precision `day`; "late October" is stored as 21 to 31 October, never the 1st.

### The reply every submit returns

```json
{
  "feed": "catalysts",
  "ok": false,
  "dry_run": false,
  "run_id": 4812,
  "batch_id": 9931,
  "applied": [ ... one entry per accepted item ... ],
  "refusals": ["items[2] ('Old event') was not retired: no catalyst is named 'Old event'; `desk catalysts list` shows the calendar with ids."],
  "warnings": ["items[0] ('U.S. August CPI') was retired (moved) and is tracked again."],
  "voice": [],
  "next": "Fix each refusal and resend only those items; the rest were applied."
}
```

`run_id` is the `macro_quality.ingest_run` row (null in a dry run), and `batch_id` the archived submission.
Each command adds its own fields, below.

## `desk context`

The board the agent reasons from, in place of the weekly journal.
No input.

```json
{
  "now": "2026-09-24T00:45:03+00:00",
  "today": "2026-09-24",
  "weekday": "Thursday",
  "week": "2026-W39",
  "us_session": {"last_completed": "2026-09-23", "trading_today": true, "open_now": false},
  "phase": [
    {"asset": "crypto", "phase": 1, "phase_name": "...", "stage": "late", "week": "2026-W39",
     "classification": "carried", "conditions_met": 2, "conditions_of": 4,
     "conditions_failed": 1, "read_stale": false, "evaluated_at": "..."}
  ],
  "scenarios": {"week": "2026-W39", "activated_at": "...", "items": [
    {"ordinal": 1, "title": "...", "probability": 45, "premise_moved": false,
     "clauses_fired": 1, "clauses_total": 4, "targets_holding": 7, "targets_total": 13}
  ]},
  "levels": [
    {"key": "vix", "label": "VIX", "value": "16.84", "display": "16.84", "unit": "index_points",
     "asof": "2026-09-23", "freshness": "fresh", "change": "-0.41", "change_label": "1d"}
  ],
  "drift": {"week": "2026-W39", "score": 3, "material": 0, "refresh_suggested": false,
            "reason": "...", "evaluated_at": "...", "top": ["...", "..."]},
  "feeds": [{"feed": "catalysts", "origin": "openclaw:macro-desk", "last_run_at": "...",
             "last_status": "ok", "last_ok_at": "...", "last_ok_origin": "openclaw:macro-desk",
             "rows": 6}],
  "notes": ["..."]
}
```

`levels` carries S&P 500, Nasdaq, VIX, MOVE, high-yield and investment-grade spreads, breadth, the 10-year yield, the dollar index, Brent, gold, BTC and ETH, each with its own as-of date.
`change` is over each series' own cadence (`change_label`), not always one day.

## `desk catalysts list`

A compact table: `columns` names the fields once, and each catalyst is one array in that order.

```json
{
  "status": "tracked", "within_days": 45, "search": null, "count": 69, "total": 73,
  "columns": ["id", "name", "category", "starts_at", "ends_at", "date_precision", "date_estimated"],
  "catalysts": [
    ["0b6c...", "U.S. August CPI", "us_macro", "2026-10-14T12:30Z", null, "exact", false],
    ["5d2e...", "Nvidia Q3 FY2027 earnings", "earnings", "2026-11-21", "2026-11-30", "month", true]
  ],
  "notes": ["...", "4 more tracked catalyst(s) are outside within_days or search; ..."]
}
```

- `starts_at` and `ends_at` are UTC: a minute (`2026-10-14T12:30Z`) when `date_precision` is `exact`, otherwise a day.
- `--within-days N` (default 45) keeps catalysts starting within N days of today.
  Undated catalysts and ones that have already started (a passed one still tracked is one to retire) are always kept.
  `0` keeps every date.
- `--search TEXT` keeps catalysts whose name, or a former name, contains the text, ignoring case.
  With a search and no `--within-days`, every date is searched: a search is how the agent checks an event is not already listed under another name.
- `--full` adds `affected`, `former_names`, `date_text`, `source_url`, `external_key`, `family`, `first_seen`, `last_seen` and `feed_source`.
  It roughly triples the size, so pair it with `--search` or a short window: the whole 45-day calendar with `--full` is about 29,000 characters.
- `--status retired` or `all` adds `status` and `retired_reason`.
- `count` is the rows printed and `total` the rows in the status before the filters.
  The 73 tracked catalysts of 2026-09-25 print about 10,000 characters (they printed 62,000 as one indented object each); a test holds 80 at under 12,000.

`category` is one of the sixteen in `worker/src/macro/families.py`; the page folds them into eight groups (`web/lib/catalysts.ts`).
`former_names` lists what a renamed catalyst used to be called.

## `desk catalysts submit`

Submits changes, not the whole calendar.
Anything not mentioned stays as it is, and a live run marks every tracked catalyst as still listed today.

```json
{"items": [
  {"op": "upsert", "name": "U.S. August CPI", "category": "us_macro",
   "when": {"date": "2026-10-14", "time": "08:30", "tz": "America/New_York"},
   "date_text": "2026-10-14 08:30 ET", "estimated": false,
   "source_url": "https://www.bls.gov/schedule/news_release/cpi.htm",
   "affected": ["rates", "USD", "equities"]},
  {"op": "retire", "name": "Jackson Hole symposium", "reason": "It took place on 22 August."},
  {"op": "rename", "id": "0b6c...", "new_name": "U.S. August Consumer Price Index",
   "reason": "The release's own title."}
]}
```

- **upsert** is identified by its name, compared ignoring case and spacing (`external_key`, a hash of the normalised name).
  An existing name is updated (a reschedule keeps its id and its history), a new one is inserted, and a retired one is revived with a warning.
  `category` is optional.
  Without it a new catalyst takes the category its name gives, the same way the vault parser did it, and an existing one keeps the category it has.
  `family` always comes from the name.
  The date must end no more than 2 days ago and start no more than 120 days ahead.
- **retire** names the catalyst by `name` or `id` and says why in `reason` (three words or more).
  The row is kept: a trigger clause bound to it is voided, never removed.
- **rename** names the catalyst by `name` or `id` and gives `new_name`.
  The row keeps its id and history; the old name becomes an alias, so a later submission under the old name is refused with the current name rather than creating a duplicate, and a retire under the old name still finds it.
  A rename onto another catalyst's name, or onto any former name, is refused.
- Two items touching the same catalyst in one submission are all refused.
  Each item is first resolved to the stored catalyst it would change, by id, current name or former name, so a rename by id beside an upsert or a retire by the old name, or a rename by a former name beside an upsert by the current name, is caught (before 2026-09-25 these passed and the upsert inserted a duplicate).
- The one pair allowed is a rename and an upsert of its new name; they apply in that order.
- Two renames onto the same new name: the second is refused and the first applies.

Each accepted item comes back in `applied`:

```json
{"index": 0, "op": "upsert", "result": "updated", "id": "0b6c...", "name": "U.S. August CPI",
 "external_key": "3f1a...", "category": "us_macro", "family": "us_cpi",
 "starts_at": "2026-10-14T12:30:00+00:00", "ends_at": null, "date_precision": "exact",
 "parsed_from": "2026-10-14 20:30 SGT", "date_estimated": false, "changed": ["starts_at"]}
```

`result` is `inserted`, `updated`, `unchanged`, `revived`, `retired` or `renamed`.
A live reply also carries `still_listed_confirmed`, the count of tracked rows marked as still listed.

New and changed catalysts reach the page's calendar (`macro_mart.catalyst_window`) on the worker's next run, 09:30 or 21:00.

## `desk tail-risks list`

```json
{"count": 5, "tail_risks": [
  {"risk_id": "iran-oil-chokepoints", "name": "Iran-war tanker and oil-chokepoint escalation",
   "severity": "acute", "summary": "...", "priced_in": "...", "assets": ["oil", "equities"],
   "source_url": "https://...", "retired_on": null, "retired_reason": null,
   "daily_recheck": true,
   "recent_checks": ["2026-09-24 daily acute", "2026-09-21 weekly elevated"]}
]}
```

`--all` includes retired risks.
`daily_recheck` marks the risks the daily run covers: live and elevated or acute.
`recent_checks` is the last five checks, newest first, one line each (`<day> <kind> <severity>`, with `retired` on the check that retired it).
The five live risks of 2026-09-25 print about 4,500 characters; a test holds eight longer ones under 12,000.

## `desk tail-risks submit`

```json
{"kind": "weekly", "items": [
  {"op": "upsert", "risk_id": "iran-oil-chokepoints",
   "name": "Iran-war tanker and oil-chokepoint escalation", "severity": "acute",
   "summary": "Tanker attacks near Hormuz raised shipping insurance again this week.",
   "priced_in": "Oil has priced some of it. Stocks and credit have not.",
   "gauges": ["vix", "move", "hy_oas_bps", "brent"],
   "source_url": "https://www.reuters.com/...", "assets": ["oil", "equities", "EM"]},
  {"op": "retire", "risk_id": "iran-cyber-water", "reason": "No incident in six weeks and the advisory was withdrawn."}
]}
```

- `kind` is `weekly` (the Monday full scan) or `daily` (the recheck).
- `risk_id` is the risk's fixed identity: a lower-case slug of 3 to 48 characters, words joined by hyphens.
  Rewording `name` keeps the id and the history.
  A new risk needs a `name`; a second id for a risk already tracked under the same name is refused.
- `severity` is `watch`, `elevated` or `acute`.
  `summary` and `source_url` are required on every upsert; `name`, `priced_in` and `assets` keep their stored values when omitted.
- `gauges` is a list of series keys from `desk context`'s `levels`.
  The command records each one's value and as-of date from the dashboard; the agent sends no number.
  An unknown key is dropped with a warning.
- **The daily recheck** may update any live elevated or acute risk (including de-escalating it), may escalate a watch-level risk to elevated or acute, and may add a new risk only at elevated or acute.
  It may not add a watch-level risk, update a watch-level risk that stays at watch, or retire a watch-level risk: those wait for Monday.
- **retire** says why in `reason`; the risk leaves the page and stays in the record.
- Every accepted item writes one check to `macro_core.tail_risk_check`, so the page can draw a risk's severity over time.

`applied` entries carry `result` (`inserted`, `updated`, `escalated`, `de-escalated`, `revived` or `retired`), `previous_severity` and the recorded `gauges`.

## `desk positions`

The names the holdings watch covers, each with a price and 24h change computed here.
No input.
The command archives what it computed and returns the archive's id, so the submit that follows records the same numbers.

```json
{
  "as_of": "2026-09-24",
  "computed_at": "2026-09-24T01:05:12+00:00",
  "positions_batch_id": 10233,
  "accounting": {"ok": true, "error": null, "venues_error": null,
                 "venues": [{"venue": "tiger", "state": "fresh", "accounts": 2,
                             "position_count": 7, "venue_as_of": "...", "received_at": "...",
                             "reasons": ["Tiger account holds cash only, ..."]}]},
  "counts": {"accounting": 10, "manual": 0, "watch": 0, "change_unavailable": 3},
  "positions": [
    {"symbol": "GOOG", "name": "Alphabet", "source": "accounting", "asset_class": "equity",
     "venues": ["tiger"], "direction": "long", "underlying": null, "expiry": null,
     "market_symbol": "GOOG", "price": "334.57", "price_at": "2026-09-23T20:00:00+00:00",
     "currency": "USD", "change_24h_pct": "-1.41", "change_basis": "close_to_close",
     "change_session": "2026-09-23", "price_source": "Yahoo chart API (GOOG)",
     "unavailable_reason": null, "underlying_only": false},
    {"symbol": "NEKO", "source": "accounting", "asset_class": "crypto", "price": "0.000105",
     "change_24h_pct": null, "change_basis": null,
     "unavailable_reason": "NEKO did not trade on Hyperliquid in the last 24 hours, so its mark has not moved and there is no change to report"}
  ],
  "notes": ["..."]
}
```

- **Sources.** Accounting's open positions (`mart.book_position` where the quantity is not zero, never cash), then the operator's manual list (brokers Accounting does not connect to), then the watch list, both edited on the dashboard.
  If Accounting's positions cannot be read, `accounting.ok` is false, `accounting.error` says so, and the other two lists are still priced.
  Its venue status is a separate read: when only that fails, the positions are still listed and `accounting.venues_error` says the book's freshness is unknown.
- **No account identifiers.** `accounting.venues` has one row per venue, never one per account: `state` is the stalest account's, `venue_as_of` and `received_at` the oldest, `accounts` how many there are, and `reasons` the distinct reasons.
  Account numbers and wallet addresses are taken out inside Accounting's functions (`macro_core.redact_account_ids`, 20260925000200), so no caller receives one: "Tiger account 50400376 holds cash only" reads "Tiger account holds cash only", and any other address or run of five or more digits reads `[withheld]`.
  The desk passes every string through its own redaction again before it is archived or printed.
  The output is read by a model and may be posted to Telegram.
- **`close_to_close`** (stocks and ETFs): the last completed regular session's close against the session before it, from Yahoo's chart API.
  For a US listing the session comes from the US market calendar, so a Singapore morning reports the previous New York session, never a live quote.
  Before the open and during the session that is the previous session, and the running session's partial bar is ignored; after the close it is the day's session.
  Yahoo's daily chart serves the bar of the session before a running one empty; when a close the change needs is empty, the command asks Yahoo's one-day chart, whose `chartPreviousClose` is that session's close, and `price_source` says so.
- **`rolling_24h`** (crypto): the Hyperliquid mark against its price 24 hours ago, or CoinGecko's 24h change for a token Hyperliquid has no liquid market for.
- **Which Hyperliquid market.** Hyperliquid lists unrelated spot tokens under the names of its perps (TRUMP, BERA, MON and PUMP each had a spot pair that had not traded in a day beside a perp trading millions), so:
  a perp position (Accounting's kind `perp`) is priced on the perp only;
  a `market: spot` or `market: perp` pin in `config/symbols.yaml` prices on that book only;
  anything else takes the spot pair when it traded at least $10,000 in 24 hours, then the perp when it did, then CoinGecko.
  A market under $10,000 moves a tick at a time, so with no fallback it reports its price and a null change with the reason.
  MAX, a spot token held on Hyperliquid with no perp, trades about $34 a day between 0.0000002 and 0.0000004: its -33% and -50% readings of 23 and 24 September were single ticks, and it now reports no change.
- **Options** carry the underlying's move with `underlying_only: true`.
- **Non-US listings** are priced through `config/symbols.yaml` (012330 on KRX is `012330.KS`, D05 on SGX is `D05.SI`), or by entering the Yahoo symbol itself in the manual list.
- **Null, never guessed.** A change that cannot be computed is null with `unavailable_reason`; the agent says the move is unavailable.
- Quantities and dollar exposure are recorded for the page but not shown to the agent.

## `desk holdings submit`

The reason for each move, and what is coming up.

```json
{
  "positions_batch_id": 10233,
  "items": [
    {"symbol": "GOOG", "material": true,
     "driver_summary": "Alphabet fell after a court limited its search deals with phone makers.",
     "source_url": "https://www.reuters.com/...",
     "events": [
       {"kind": "earnings", "name": "Alphabet Q3 2026 earnings",
        "when": {"date": "2026-10-27", "time": "16:05", "tz": "America/New_York"},
        "date_text": "Oct 27 after the close", "estimated": false,
        "source_url": "https://abc.xyz/investor/"}
     ]}
  ]
}
```

- `positions_batch_id` is the id `desk positions` returned.
  When it is from the last three hours the command records exactly those prices; otherwise, or when it is not a whole number from 1 to 2^63 - 1, it computes them again and says so.
- A submission is a merge per day (`as_of`) and name.
  A name the payload carries has its row written with its driver and this submission's prices.
  A name the payload leaves out keeps the row an earlier submission wrote for the day, untouched; it gets a quiet row (no driver, not material) only if it has none yet.
  So a resend with only the fixed item erases no other name's driver.
  An item for a name that is not watched today is refused.
  `source` (`accounting`, `manual`, `watch`) narrows an item to one list when a symbol is on two.
- `driver_summary` needs a `source_url`; `material: true` needs a `driver_summary`.
- When the agent looked and found no sourced story for a move, it says `no_clear_reason: true`.
  The item then needs no `source_url`, and `driver_summary` is optional (it may say what was checked).
  The row is stored with `no_clear_reason` true, a material one included, and the page shows "no clear reason found" (`holding_watch_state.no_clear_reason`).
- Two items reaching the same row, such as `{"symbol": "GOOG"}` and `{"symbol": "GOOG", "source": "accounting"}`, are both refused, and the name gets a quiet row if it has none.
  Before 2026-09-25 the second silently overwrote the first.
- Price fields sent anyway (`price`, `change_24h_pct` and the like) are ignored with a warning; the recorded numbers are always the command's.
- `events` are what falls in the next 14 days: `kind` is `earnings`, `product`, `regulatory`, `unlock` or `other`, and each needs a date, `estimated` and a `source_url`.
- An `earnings` event is also placed on the catalyst calendar under `earnings`.
  If the calendar already has it (the same name, a former name, or an earnings catalyst naming the ticker or the company within a week of it), the event is linked to that row instead of duplicating it.
  A firm date replaces the calendar's estimate; a disagreeing date is left alone and reported as a warning.

The reply's `applied` lists each name written (carried, or quiet because it had no row) with the change recorded and the driver; `kept` lists the names left as an earlier submission wrote them; `events` lists each event with what happened on the calendar.

## The page's side

The web reads, and never writes, these `macro_mart` views: `holding_watch_state`, `holding_event_state`, `manual_position_state`, `watch_ticker_state`, `tail_risk_state` (now with `risk_id` and `previous_severity`), `tail_risk_history` and `feed_freshness`.
It edits the two lists through `save_manual_position`, `retire_manual_position`, `save_watch_ticker` and `retire_watch_ticker`, each requiring a signed-in operator.
Their refusals are sentences the page can show as they are: "NVDA is already on the watch list; edit that row instead", "D05 at DBS Vickers is already on the manual list", "the broker or venue name is at most 40 characters", "a quantity is zero or more", an unknown asset class.

## What `macro_desk` may do

- Read and write the three feeds, and read the mart views they need; nothing else (20260924000200 to 20260924000400).
- Every catalyst and tail risk it inserts or updates says `feed_source = 'openclaw:macro-desk'`, and every ingest run `origin = 'openclaw:macro-desk'`; it sees only desk runs, and opens only findings whose key starts `desk_` (20260925000200).
  Confirming the rest of the calendar is still listed touches rows the vault wrote, so it goes through `macro_core.desk_confirm_catalysts_listed(date)`, which moves `last_seen` and nothing else.
- Its sessions stop a statement after 30 seconds, a lock wait after 10 and an idle transaction after 60.
  These are role settings: they take effect at the next login, with no change to `.env`.
`docs/CONTRACT.md` has the columns.
