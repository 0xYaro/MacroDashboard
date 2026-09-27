"""The search-agent prompts, ported from `gather.workflow.js`.

These are not new prompts. They are the ones that have run every Monday for
twelve weeks, carried across with the smallest set of changes the new schema
forces, and each change is named below. Rewriting them would throw away the only
calibration evidence this package has about how a search agent behaves.

## What was carried unchanged

The tier ladder, word for word: Tier 1 is an official API or primary release, or
two independent sources agreeing within 1% on a price and 5% on a flow; Tier 2
is a single authoritative source; Tier 3 is sources disagreeing by more than 5%
or a partial extraction; Tier 4 is not found, and it is **an empty value with a
note**, never a number the model felt was about right.

The `asof` discipline: never blank, and the observation date rather than the
publication date, with `released` carrying the publication date when the two
differ. The instruction that a weekly or monthly series is fresh until its next
scheduled release and is never stale on the observation date alone.

The two-stage shape: gather in parallel per category, then re-fetch only the
Tier 3 and Tier 4 points against a named fallback source order, and keep the
recovered point only when its tier is strictly better.

## What changed, and why

1. **The `fred` category is gone.** `docs/DECISIONS.md` established that FRED is
   not blocked, it allowlists the User-Agent product token, and the worker now
   fetches HY OAS, IG OAS and the three net-liquidity components directly.
   A search agent on that path would be a second, worse source for a series
   that already has a Tier 1 one.

2. **`% S&P above 200dma` is gone from the equities category.** Every setup gate
   written to date is a threshold on it and the 21:00 rescore evaluates gates,
   so it moved to a deterministic scraper. An LLM fetch cannot sit on that path.

3. **A point names a `series` key, not a metric label.** The old workflow matched
   returns to rows by the display label, and `2026-W39.points.json` holds two
   rows both labelled `Next FOMC` because the FedWatch reading came back under
   the meeting-date label and the merge mapped both onto one row. A key cannot
   collide: `next_fomc` is the meeting and `fedwatch_next_fomc` is the
   market-implied path into it, and they have different cadences. The label is
   still printed in the prompt, because the prompt's vocabulary is what twelve
   weeks of behaviour was measured against.

4. **An ETF streak is a signed integer.** `+2` is two consecutive inflow
   sessions, `-4` is four consecutive outflow sessions, `0` broke a run without
   starting one. The journal writes it as a sentence and `display` keeps that
   sentence, but `streak_intact` needs an integer and a `text` series may only
   appear in a `manual` condition. Superseded by change 7: the worker computes
   the streak now, and `0` is only a session whose net flow was exactly zero.

5. **The five prose series are prose, and the numbers beside them are not.**
   `fed_funds`, `next_fomc`, `fedwatch_next_fomc`, `tariff_state` and
   `geopolitical_state` carry `value` null and their sentence in `display`. The
   prompt asks for the sentence and for no number, because a number in a text
   series is a number no condition can ever read and a null in one is
   indistinguishable from a Tier 4 failure.

   `fedwatch_hike_prob_next` sits in the same table group and is the opposite.
   It is the numeric companion to the prose FedWatch row and does not replace
   it: the implied odds for the next meeting are the most-quoted figure in any
   trigger the operator writes, and a `text` series can only ever appear in a
   `manual` condition. Both are asked for and both are returned.

6. **ISM is a search target because it is not free.** `ism_manufacturing` and
   `ism_services` are `agent_0930` rather than deterministic for a measured
   reason: FRED's `NAPM` and `NAPMNOI` are discontinued and both answer 404, so
   there is no keyless series to fetch.

7. **ETF flows left the crypto-flows category on 2026-09-24.**
   `btc_etf_weekly_flow`, `btc_etf_inflow_streak`, `eth_etf_weekly_flow` and
   `eth_etf_inflow_streak` are `deterministic` now: the worker reads SoSoValue's
   daily totals, checks them against Farside, and computes the week to date and
   the streak from them (`worker/src/macro/fetchers/etf_flows.py`). A search
   agent on that path would be a second, worse source for four series that
   already have a Tier 1 one, which is change 1's reasoning again. The category
   keeps `liquidations_24h`, and its prompt and fallback list lost the four ETF
   targets and nothing else.

The set these categories cover is not listed in this module. It is read from
`config/series.yaml` through `Registry.agent_only()`, and the `series` tuples
below are checked against it by a test rather than counted, so a series added to
the registry fails loudly as uncovered rather than silently never being fetched.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

#: The JSON shape a data category returns. `gather.workflow.js`'s DATA_SCHEMA,
#: with `metric` replaced by `series` and `display` added for the prose rows.
DATA_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "points": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "series": {"type": "string"},
                    "value": {"type": "string"},
                    "display": {"type": "string"},
                    "source": {"type": "string"},
                    "tier": {"type": "integer"},
                    "asof": {"type": "string"},
                    "released": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["series", "value", "display", "source", "tier", "asof", "released"],
            },
        }
    },
    "required": ["points"],
}

#: `gather.workflow.js`'s EVENTS_SCHEMA, unchanged but for the assets field,
#: which becomes a list because `macro_core.event.assets` is `text[]`.
EVENTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "event": {"type": "string"},
                    "date": {"type": "string"},
                    "kind": {"type": "string", "enum": ["macro", "structure"]},
                    "whatHappened": {"type": "string"},
                    "whyItMatters": {"type": "string"},
                    "assets": {"type": "array", "items": {"type": "string"}},
                    "surprise": {"type": "boolean"},
                    "source": {"type": "string"},
                },
                "required": ["event", "date", "kind", "whatHappened", "whyItMatters"],
            },
        }
    },
    "required": ["events"],
}


@dataclass(frozen=True, slots=True)
class Category:
    """One search agent: its label, its schema, its prompt and its fallbacks."""

    key: str
    label: str
    schema: dict[str, Any]
    prompt: str
    fallbacks: str
    series: tuple[str, ...]


CRAWL = """You are an autonomous market-data fetcher. As-of date: {asof} (on a Monday run this is the previous Friday's close).
Use ToolSearch to load WebFetch and WebSearch, then fetch each target metric below.
For EACH metric: (1) get the current value from the PRIMARY source listed; (2) cross-check against a second
independent source via WebSearch; (3) assign a confidence tier; (4) record asof - the exact observation date the
value is from; (5) record released - the publication date when it differs from asof, otherwise an empty string.
NEVER leave asof blank. Use the series key EXACTLY as written below in the `series` field; the label beside it is
for your reading only and is never matched on.
Tiers:
1 = official API / primary release, or 2+ sources agree (prices within 1%, flows within 5%);
2 = single authoritative source / clean fetch;
3 = sources disagree >5% or extraction partial (include value, set note to the reason);
4 = not found / fetch failed / stale beyond the series' own cadence (leave value empty, set note to "check <source> for <datapoint>").
A Tier 4 point is an EMPTY value and a note. It is never a number you believe is about right, and it is never zero.
Weekly and monthly series are fresh until their next scheduled release; never mark them stale on the observation date alone.
Compute nothing you can't source. Never fabricate. Return one entry per metric.
`value` is the machine-readable number in the series' declared unit, as a plain decimal string with no thousands
separators, no currency symbol and no percent sign. `display` is the sentence the journal prints.
For a prose series (marked PROSE below) leave `value` empty and put the whole sentence in `display`; a prose row
carries no number at all.
Previous observations, for sanity only: {previous}."""


MACRO_PROMPT = """{crawl}
Targets, POINT-IN-TIME state only - the dated event log is the events agent's job.
The first five are PROSE: value empty, the sentence in display. A prose row's asof is the as-of date above, the day
you confirmed the state, never the date of the event the sentence describes: the decision day goes in the sentence.
- `fed_funds` "Fed funds rate" - current target range, midpoint, and the last decision's vote (Fed statement / news).
- `next_fomc` "Next FOMC" - date(s) of the next meeting, statement and press-conference times in ET (Fed calendar).
- `fedwatch_next_fomc` "FedWatch quote, next FOMC" - hold / cut / hike odds for the next meeting. Do NOT fetch cmegroup.com (blocked); use news / aggregators quoting FedWatch same-day, and say the date the odds were quoted. This is its OWN series and is never returned under the `next_fomc` key: one is the meeting date and does not move, the other is the market-implied path into it and moves daily.
- `tariff_state` "Tariff regime" - one line on the current posture and any dated implementation inside 30 days.
- `geopolitical_state` "Geopolitical state" - one line on the active flashpoints as they stand now.
The last three are NUMBERS: the value in the unit named, the sentence in display.
- `fedwatch_hike_prob_next` "FedWatch hike odds, next FOMC" - the implied probability of a hike at the next meeting, in PERCENT (50.9, not 0.509), from the same same-day quote as `fedwatch_next_fomc`, with its quote date as asof. It is the number beside that prose row and never replaces it: return both.
- `ism_manufacturing` "ISM manufacturing PMI" - the latest ISM Manufacturing PMI headline, index points, one decimal. asof = the last day of the data month, released = the release date. Fresh until the next release; never flag it stale.
- `ism_services` "ISM services PMI" - the latest ISM Services PMI headline, index points, one decimal, same asof convention."""

MACRO_FALLBACKS = """- Fed funds / next FOMC: federalreserve.gov statement and calendar pages, then Reuters / CNBC.
- FedWatch: news / aggregators quoting CME FedWatch same-day. Do NOT fetch cmegroup.com.
- Tariff / geopolitical state: Reuters, then AP.
- ISM: the ISM Report On Business release page (ismworld.org), then Reuters / Trading Economics quoting it."""


EQUITIES_PROMPT = """{crawl}
Targets:
- `state_street_equity_allocation` "State Street equity allocation" - latest monthly State Street Institutional Investor Indicators: the equity allocation as a PERCENT of assets, one decimal. Use the official release; label the data month and release month; report the month-over-month change in display. Between monthly releases repeat the latest print (Tier 2, asof = the last day of the data month, released = the release date), never flag it stale.
- `state_street_risk_appetite` "State Street risk appetite" - the standardised index from the same release, two decimals. It is a separate series from the allocation because one is a percentage of assets and the other is an index, so no single unit could cover the pair."""

EQUITIES_FALLBACKS = """- State Street: official monthly Institutional Investor Indicators release, then reputable coverage quoting it. Retain the latest release between monthly updates."""


CRYPTO_PROMPT = """{crawl}
Targets:
- `liquidations_24h` "Liquidations, 24h" - latest 24h total in USD with the long/short split in display, via news quoting Coinglass (CoinDesk / The Block); do not fetch coinglass.com. This is a rolling window read at the moment of the run, not a daily total: say in display the hour the window was read at."""

CRYPTO_FALLBACKS = """- 24h liquidations: news quoting Coinglass (CoinDesk / The Block); not direct Coinglass."""


EVENTS_PROMPT = """As-of {asof}.
Use ToolSearch to load WebSearch/WebFetch. Find every material market-moving event since {prev_date}:
macro prints (CPI/PCE/NFP/FOMC), geopolitics, earnings of bellwethers, hacks/exploits, key level reclaims/breaks,
flow-streak transitions, narrative shifts. SPECIFICALLY check the outcome of each catalyst the previous week
was watching: {prev_catalysts}
The previous week's headline call + base-case scenario was: {prev_expectation}
For each event give what happened, why it matters, assets affected, and mark surprise=true if it diverged from
what the previous week expected - grade divergence against that prior headline call + base case, not just the
catalyst list. Do not fabricate; omit if unverifiable.
Set kind to "macro" for a macro or geopolitical event and "structure" for a market-structure event: an earnings
print, a hack or exploit, a key level reclaimed or rejected, a flow-streak transition, a narrative shift, or a
holdings delta.
The Market Watchlist has ALREADY logged these holdings deltas, and the worker has already parsed them into the
database - include the material ones as events under their date WITHOUT re-searching them, and only search for
what they do not cover:
{watchlist_deltas}
Standing tail-risk watchlist, already parsed from Blackswan Events (surface any event that touches one of these,
do not re-search the list itself):
{blackswan}
The catalysts already tracked in the database, so you do not re-derive the forward calendar:
{catalysts}"""

RETRY_PROMPT = """{crawl}
These metrics came back FLAGGED (Tier 3+) on the first pass. Re-fetch ONLY these, working the fallback source
order below in sequence until one yields a clean value. Return one entry per metric (series/value/display/source/tier/asof/released).
Return the `series` field EXACTLY as listed below, character for character, or the recovered value cannot be merged.
Flagged metrics:
{flagged}
Fallback sources for this category:
{fallbacks}"""


def crawl(asof: str, previous: dict[str, str]) -> str:
    """The shared preamble, with the previous observations pasted in for sanity."""
    return CRAWL.format(asof=asof, previous=json.dumps(previous, sort_keys=True))


def categories(
    *,
    asof: str,
    previous: dict[str, str],
    prev_date: str,
    prev_catalysts: str,
    prev_expectation: str,
    watchlist_deltas: str,
    blackswan: str,
    catalysts: str,
) -> list[Category]:
    """The four search agents the 09:30 run dispatches, prompts already rendered."""
    head = crawl(asof, previous)
    return [
        Category(
            key="macro",
            label="macro-search",
            schema=DATA_SCHEMA,
            prompt=MACRO_PROMPT.format(crawl=head),
            fallbacks=MACRO_FALLBACKS,
            series=(
                "fed_funds",
                "next_fomc",
                "fedwatch_next_fomc",
                "fedwatch_hike_prob_next",
                "ism_manufacturing",
                "ism_services",
                "tariff_state",
                "geopolitical_state",
            ),
        ),
        Category(
            key="equities",
            label="equities-search",
            schema=DATA_SCHEMA,
            prompt=EQUITIES_PROMPT.format(crawl=head),
            fallbacks=EQUITIES_FALLBACKS,
            series=(
                "state_street_equity_allocation",
                "state_street_risk_appetite",
            ),
        ),
        Category(
            key="crypto-flows",
            label="crypto-flows-search",
            schema=DATA_SCHEMA,
            prompt=CRYPTO_PROMPT.format(crawl=head),
            fallbacks=CRYPTO_FALLBACKS,
            series=("liquidations_24h",),
        ),
        Category(
            key="events",
            label="events-since-last",
            schema=EVENTS_SCHEMA,
            prompt=EVENTS_PROMPT.format(
                asof=asof,
                prev_date=prev_date,
                prev_catalysts=prev_catalysts or "(none recorded)",
                prev_expectation=prev_expectation or "(no prior expectation recorded)",
                watchlist_deltas=watchlist_deltas or "(none)",
                blackswan=blackswan or "(none)",
                catalysts=catalysts or "(none)",
            ),
            fallbacks="(no fallback sources listed)",
            series=(),
        ),
    ]


def retry(category: Category, flagged: list[tuple[str, int, str]], *, asof: str) -> str:
    """The Retry-stage prompt for one category's Tier 3 and Tier 4 points.

    `gather.workflow.js` skips this stage for the events category, which carries
    no data points to recover, and so does the caller here.
    """
    listing = "\n".join(
        f"- {key} (currently Tier {tier}{'; note: ' + note if note else ''})"
        for key, tier, note in flagged
    )
    return RETRY_PROMPT.format(
        crawl=crawl(asof, {}),
        flagged=listing,
        fallbacks=category.fallbacks,
    )
