# Deferred work

Each entry names what was deferred, why, and what would make it worth doing.
Nothing here is a bug.
A bug goes in an issue.

## Equal-weight and sector breadth

Breadth is computed in-house from constituent closes since 2026-09-23 (`docs/DECISIONS.md`), so the same scanner query can also give sector breadth and other cuts.
The journal has wanted those twice (W32, W36) and worked around them in prose both times.
Worth doing when a setup or a scenario clause names one.

## Scrapers replacing the remaining numeric search agents

24-hour liquidations, ISM manufacturing and services, and State Street's two monthly figures are fetched by LLM search agents on the 09:40 narrative run.
FedWatch's hike odds moved to a deterministic computation from fed funds futures on 2026-09-28.
ETF flows and streaks moved to a deterministic fetcher on 2026-09-24: SoSoValue's keyless daily history, checked against Farside through `curl_cffi`.
The CNN Fear and Greed index moved to a deterministic fetcher on 2026-09-23.
Each of the rest is a single number from a page, and each would be cheaper, faster and louder in failure as a deterministic scraper.

Liquidations are waiting on a free Coinglass API key.
The failure mode being traded away is the important one: a scraper that breaks reports a fault, and a search agent that breaks returns a plausible number.

## Removing the vault note parsers

The entry that deferred folding the OpenClaw crons into the worker is superseded by `docs/DECISIONS.md` 2026-09-24, "OpenClaw writes the three feeds through the desk command".
The `macro-desk` agent writes the catalysts, the tail risks and the holdings watch through `macro-agent desk` (dry runs only until the cutover), and the old crons are turned off at the cutover (`openclaw/README.md`, "Cutover checklist").

What is still deferred is the approved plan's Phase 4: deleting the worker's parsers for `Catalyst Monitor.md`, `Market Watchlist.md` and `Blackswan Events.md` and the `MACRO_FEED_SOURCE_*` switch that selects them.
Until then they are the rollback path (`openclaw/ROLLBACK.md`).
Worth doing once the desk has run live for long enough that nobody would point the worker back at the notes.

## Obsidian write-back and the worded rendering

The dashboard's prose rendering, a worded interpretation of the current state in the journal's house style, is not built.
Neither is writing it back into `3 - Work/31 - Macro/`.

Deferred by decision, not by cost: the structured state has to be right before a renderer over it is worth having.
Worth doing once the weekly authoring lifecycle has run for a few weeks and the shape of `macro_core` has stopped moving.
When it is built, it renders from a point in history, so any past date can be rendered as it stood.

## R13 on premises that move

Scenario clauses bound to a catalyst that is later retired are flagged and tagged on the graded week, and the Brier score is not forgiven.
If the `premise_moved` tag accumulates, it becomes a standing scenario rule.

Deferred because a rule needs two recorded instances before it is written, which is how every rule from R1 to R12 was made.
Worth writing when the count reaches two.

## Merging with Accounting

The two systems share a Supabase project, a login and a design system, so a merge is adding `/macro`, `/history` and `/calibration` beside `/book` and `/flows` in one Next.js app.

Deferred until both have run independently long enough to be boring.
The thing to check before merging is the query client: Accounting subscribes to Supabase Realtime and this system polls, and one app holding both needs those two cadences declared in one place rather than fought over per hook.
