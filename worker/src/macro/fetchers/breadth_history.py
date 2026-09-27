"""Breadth history, rebuilt from constituent closes. A one-off, not a source.

The daily source in `breadth.py` re-states three sessions per run, because the
scanner serves no further back. Everything older comes from here: today's S&P
500 list from the same scanner query, two years of daily closes per line from
Yahoo's chart API, and the same arithmetic the scanner applies, which is the
plain mean of the last 200 closes including the session's own, split-adjusted
and not dividend-adjusted. Yahoo's `close` is exactly that series; checked by
hand for AAPL and T on 2026-09-22 against the scanner's own `SMA200`.

Why Yahoo and not the journal. `config/predicates.md` carries five daily values
from W39 and the journal a weekly figure before that. Five points do not make
a history, and a weekly S5TH print is a different method (Barchart's averages
appear to be dividend-adjusted) that would put a half-point step into the
series wherever the two joined. Rebuilding every session with the live method
gives one series with one definition.

**Survivorship.** The constituent list is today's. A company that left the
index in the last two years is missing from every past session and one that
joined is counted before it joined. At the size of the index's annual turnover
that moves a past figure by well under a point, and every row says so in
`extra.constituents`. It is still a bias the live figure does not carry, so
every backfilled row is Tier 2.

**Pacing.** One Yahoo request per line, about 500, at the 2.5 s spacing
`http._PACE_SECONDS` already enforces for Yahoo with the `Mozilla/5.0` agent
Yahoo requires: about twenty-one minutes. The rate-limit breaker still applies,
and a line Yahoo refuses is a fault, not a stop; a session that ends up resting
on fewer than `breadth.MIN_CONSTITUENTS` companies is skipped, not guessed.

`compute_history` is pure. `fetch_history` is the only half that talks to the
network, and nothing in this module writes to the database: `main.py` does
that, behind `--breadth-backfill`.
"""

from __future__ import annotations

import logging
import urllib.parse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from .. import clock
from ..http import Client, FetchRefused
from ..model import Observation
from . import breadth, yahoo

LOG = logging.getLogger(__name__)

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=2y&interval=1d"

WINDOW = 200

_CENT = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class Constituent:
    """One line of the index as the scanner lists it today."""

    symbol: str
    description: str

    @property
    def yahoo_symbol(self) -> str:
        """`NYSE:BRK.B` is `BRK-B` on Yahoo."""
        return self.symbol.split(":", 1)[-1].replace(".", "-")


@dataclass(slots=True)
class Fetched:
    """Every line's closes, oldest first, and every line that could not be read."""

    listed_on: date
    constituents: list[Constituent]
    closes: dict[str, list[tuple[date, Decimal]]] = field(default_factory=dict)
    faults: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Skipped:
    asof: date
    counted: int


def constituents(scan_payload: Any) -> list[Constituent]:
    """Today's lines, from the scanner answer `breadth.SCAN_BODY` asks for."""
    return [
        Constituent(symbol=symbol, description=str(row.get("description") or symbol))
        for symbol, row in breadth._rows(scan_payload)
    ]


async def fetch_history(client: Client, now: datetime) -> Fetched:
    """The index list, then two years of closes for every line in it."""
    lines = constituents(await client.query_json(breadth.SCAN_URL, breadth.SCAN_BODY))
    out = Fetched(listed_on=now.astimezone(clock.NEW_YORK).date(), constituents=lines)
    for count, line in enumerate(lines, start=1):
        url = CHART_URL.format(symbol=urllib.parse.quote(line.yahoo_symbol, safe=""))
        try:
            bars, _ = yahoo._bars(await client.get_json(url), now)
        except FetchRefused as exc:
            out.faults.append(f"{line.symbol} ({line.yahoo_symbol}): {exc}")
            continue
        out.closes[line.symbol] = [(bar.session, bar.close) for bar in bars]
        if count % 50 == 0:
            LOG.info("breadth backfill: %d of %d lines read", count, len(lines))
    return out


def compute_history(
    closes: Mapping[str, Sequence[tuple[date, Decimal]]],
    descriptions: Mapping[str, str],
    *,
    since: date,
    until: date,
) -> tuple[list[breadth.Session], list[Skipped]]:
    """Every US session from `since` to `until` inclusive, oldest first.

    A line counts on a session when it has a close that day and 200 closes up
    to and including it. The same one-vote-per-company rule as the live source
    applies. A session resting on fewer than `breadth.MIN_CONSTITUENTS`
    companies is skipped and reported, never stored.
    """
    series: dict[str, tuple[dict[date, int], list[Decimal], list[Decimal]]] = {}
    for symbol, points in closes.items():
        ordered = sorted(points)
        index = {day: position for position, (day, _) in enumerate(ordered)}
        values = [value for _, value in ordered]
        running = [Decimal(0)]
        for value in values:
            running.append(running[-1] + value)
        series[symbol] = (index, values, running)

    sessions: list[breadth.Session] = []
    skipped: list[Skipped] = []
    for day in clock.us_sessions_between(since, until):
        usable: dict[str, tuple[str, bool]] = {}
        excluded: list[str] = []
        for symbol, description in descriptions.items():
            found = series.get(symbol)
            position = None if found is None else found[0].get(day)
            if found is None or position is None or position < WINDOW - 1:
                excluded.append(symbol)
                continue
            _, values, running = found
            average = (running[position + 1] - running[position + 1 - WINDOW]) / WINDOW
            usable[symbol] = (description, values[position] > average)
        votes, folded = breadth._one_line_per_company(usable)
        counted = len(votes)
        if counted < breadth.MIN_CONSTITUENTS:
            skipped.append(Skipped(asof=day, counted=counted))
            continue
        above = sum(1 for is_above in votes.values() if is_above)
        sessions.append(
            breadth.Session(
                asof=day,
                value=(Decimal(above) * Decimal(100) / Decimal(counted)).quantize(
                    _CENT, rounding=ROUND_HALF_UP
                ),
                above=above,
                counted=counted,
                excluded=tuple(sorted(excluded)),
                folded=tuple(sorted(folded)),
            )
        )
    return sessions, skipped


def history_observations(sessions: Sequence[breadth.Session], listed_on: date) -> list[Observation]:
    return [
        Observation(
            series_key=breadth.SERIES_KEY,
            asof=one.asof,
            value=one.value,
            display=f"{one.value:.2f}%",
            # Never Tier 1: today's constituent list applied to past sessions
            # is survivorship-biased, which the live scanner figure is not.
            tier=2,
            source="Computed from Yahoo constituent closes (breadth backfill)",
            observed_at=clock.us_session_close_utc(one.asof),
            extra={
                "method": (
                    "share of S&P 500 companies whose close is above its 200-day simple "
                    "average of closes; one vote per company, through its Class A line"
                ),
                "provider": "Yahoo chart API closes; constituents from the TradingView scanner",
                "constituents": (
                    f"the S&P 500 as listed on {listed_on.isoformat()}, applied to every past "
                    "session; a company that left or joined the index since is miscounted"
                ),
                "above": one.above,
                "counted": one.counted,
                "excluded": list(one.excluded),
                "folded": list(one.folded),
                "backfill": True,
            },
        )
        for one in sessions
    ]
