"""Percent of the S&P 500 above its 200-day average, computed here.

`docs/DECISIONS.md` moved this series off the LLM path because the 21:00 run
evaluates setup gates, every gate written to date is a threshold on this series,
and an LLM fetch cannot sit there. So this module must never call one, and it
does not.

## The figure is computed, not scraped

Barchart's `S5TH`, the series the journal quoted every week, is published on
TradingView around 09:15 UTC the day after the session it describes. The
09:30 Singapore run is 01:30 UTC, so it never had the session it owed: every
morning opened a gap for yesterday, and the series read `MISSING DAY` until
the evening run.

So the figure is computed from the constituents instead. TradingView's scanner
answers one query for the whole index, `symbolset SYML:SP;SPX`, with each
constituent's close and its 200-day simple average of closes, and it answers
within minutes of the US close. The offsets `[1]` and `[2]` give the same two
columns for the two sessions before, so every run re-states three sessions and
a run that was missed fills itself on the next one. Offsets past `[2]` answer
null, measured on 2026-09-23; history further back comes from the backfill in
`fetchers/breadth_history.py`, which rebuilds the same figure from Yahoo closes.

`SMA200` is the plain mean of the last 200 closes including the session's own,
split-adjusted and not dividend-adjusted. Checked by hand against Yahoo's
`close` for AAPL and T on 2026-09-22: 286.8831 against the scanner's 286.8834,
and 25.1469 against 25.1470.

**Share classes count once.** The index lists 503 lines for 500 companies:
Alphabet, Fox and News Corp each list two classes. They almost always sit on
the same side of their averages, so counting both gives three companies a
second vote. Each company counts once, through its Class A line. A line with no
200-day average (a spin-off's when-issued shares, a listing younger than 200
sessions) is not counted either way.

**Fewer than `MIN_CONSTITUENTS` refuses.** A figure over a fraction of the index
is a different statistic wearing the same name, and a symbol set that quietly
shrank would otherwise produce a plausible number.

**The session is the bar's own.** `time` is the open of each line's daily bar,
so every close is filed under the New York date of its own bar and never under
a date the calendar suggested. A session whose close has not happened at the run
instant is the running session and is not stored.

## S5TH is the cross-check

`S5TH` is still read, from TradingView's symbol page, for two jobs.

- **Cross-check.** When it carries a session this run also computed, the two are
  compared, the vendor's figure travels in `extra`, and a gap wider than
  `DIVERGENCE_POINTS` opens a `breadth_divergence` finding. The two differ by
  design, by about half a point: Barchart's averages appear to be
  dividend-adjusted, which lowers a payer's average and puts more names above
  it, and it appears to count all 503 lines. On 2026-09-22 the computed figure
  was 49.00 and `S5TH` 49.70.
- **Fallback.** When the scanner could not be read at all, the `S5TH` bar fills
  its own session, Tier 2, if and only if that session is newer than anything
  stored. It never overwrites a computed figure, and it opens a
  `breadth_fallback` finding saying why it was used.

The page's `application/ld+json` `price` carries no date and is never read. The
`application/prs.init-data+json` block's `data.symbol.daily_bar` carries a
`time`, which is the bar's own date, and that is what is read.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from .. import clock
from ..faults import group_faults
from ..http import Client, FetchRefused
from ..model import Finding, Observation, RawBatch
from ..units import dec
from .base import FetchContext, Fetched, batch

SOURCE = "breadth"
SERIES_KEY = "breadth_spx_above_200dma"

SCAN_URL = "https://scanner.tradingview.com/america/scan"
S5TH_URL = "https://www.tradingview.com/symbols/INDEX-S5TH/"

#: How many sessions each run re-states: the newest, and the two the scanner
#: serves through its `[1]` and `[2]` offsets.
OFFSETS: tuple[int, ...] = (0, 1, 2)

#: The scanner's columns, in the order each row's `d` list answers them.
COLUMNS: tuple[str, ...] = (
    "name",
    "description",
    *(
        f"{column}{'' if k == 0 else f'[{k}]'}"
        for k in OFFSETS
        for column in ("close", "SMA200", "time")
    ),
)

SCAN_BODY: dict[str, Any] = {
    "symbols": {"symbolset": ["SYML:SP;SPX"]},
    "columns": list(COLUMNS),
    "range": [0, 1000],
}

#: The fewest counted companies a session may rest on. The index has 500; a
#: handful can lack a 200-day average at any time, and fewer than this means the
#: symbol set or the columns changed rather than the market.
MIN_CONSTITUENTS = 480

#: How far apart the computed figure and `S5TH` may sit before it is a finding,
#: in percentage points. The methodological gap is about half a point.
DIVERGENCE_POINTS = Decimal(3)

#: The `extra` keys the S5TH cross-check writes onto the session it describes.
S5TH_EXTRA: tuple[str, ...] = ("s5th_close", "s5th_divergence")

#: The checks `review` raises. Evaluated on every run that fetches breadth.
CHECK_KEYS: tuple[str, ...] = ("breadth_divergence", "breadth_fallback")

_CLASS = re.compile(r"\s+Class\s+([A-Z])\b.*$")

#: The symbol page embeds several of these. Only the one carrying a `daily_bar`
#: under `data.symbol` is the quote; the rest are page configuration.
_INIT_DATA = re.compile(
    r'<script type="application/prs\.init-data\+json">(.*?)</script>', re.DOTALL
)

_CENT = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class Session:
    """One session's breadth, as computed from the scanner."""

    asof: date
    value: Decimal
    above: int
    counted: int
    #: Lines not counted: no average, or a bar from another session.
    excluded: tuple[str, ...]
    #: Second share classes folded into their company's Class A line.
    folded: tuple[str, ...]
    #: Every line that had both a close and an average for this session.
    members: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class Published:
    """One `S5TH` daily bar."""

    asof: date
    value: Decimal
    extra: dict[str, Any]


class Breadth:
    """The `% S&P above 200dma` source: the scanner, and `S5TH` beside it."""

    source = SOURCE
    series = (SERIES_KEY,)

    async def fetch(self, client: Client, ctx: FetchContext) -> Fetched:
        out: list[RawBatch] = []
        faults: list[str] = []
        try:
            out.append(batch(SOURCE, "scan", ctx, await client.query_json(SCAN_URL, SCAN_BODY)))
        except FetchRefused as exc:
            faults.append(f"scanner: {exc}")
        try:
            out.append(batch(SOURCE, "s5th", ctx, (await client.get(S5TH_URL)).text))
        except FetchRefused as exc:
            # The cross-check failing costs the cross-check and nothing else.
            faults.append(f"S5TH page: {exc}")
        if not out:
            raise FetchRefused("breadth read nothing: " + group_faults(faults))
        return Fetched(batches=out, faults=faults)

    def normalize(self, batches: Sequence[RawBatch], ctx: FetchContext) -> list[Observation]:
        sessions: list[Session] = []
        refused: str | None = None
        published: Published | None = None
        published_refused: str | None = None
        for raw in batches:
            if raw.kind == "scan":
                try:
                    sessions = compute_sessions(raw.payload, ctx.now)
                except FetchRefused as exc:
                    refused = str(exc)
            elif raw.kind == "s5th":
                try:
                    published = read_s5th(str(raw.payload))
                except FetchRefused as exc:
                    published_refused = str(exc)

        out = [session_observation(one, published) for one in sessions]
        cursor = ctx.last_asof(SERIES_KEY)
        if (
            published is not None
            and not sessions
            and (cursor is None or published.asof > cursor)
        ):
            # Only a session nothing has filled. A stored computed figure is
            # never replaced by the vendor's, which is a different method.
            out.append(
                fallback_observation(
                    published, refused or "the scanner could not be read", ctx.now
                )
            )
        if not out:
            raise FetchRefused(
                refused or published_refused or "breadth produced no session from either source"
            )
        return out

    def review(self, observations: Sequence[Observation], _ctx: FetchContext) -> list[Finding]:
        return review(observations)


# ---------------------------------------------------------------------------
# The computation
# ---------------------------------------------------------------------------


def compute_sessions(payload: Any, now: datetime) -> list[Session]:
    """Every settled session the scan carries, oldest first.

    Each line's three bars are filed under their own dates rather than read by
    offset, because the offsets do not line up across the index. Before the
    open a line that has traded pre-market has already rolled: its offset 0 is
    today's unfinished bar and yesterday's close sits at `[1]`. Measured at
    10:25 UTC on 2026-09-23, CINF and LRCX had rolled and the other 501 had
    not. Reading by offset would have compared their unfinished bars with
    everyone else's settled ones.

    Refuses when the newest settled session rests on fewer than
    `MIN_CONSTITUENTS` companies. An older one is kept only when every line
    counted on the newest session is counted on it too. Once a line has rolled
    pre-market its third session back falls off the scan, so at 21:00 Singapore
    the oldest session is served for 496 or 497 companies, not 498. It was
    stored whole by the run that saw it newer, and a re-statement from the
    subset would overwrite that whole figure with a partial one.
    """
    rows = _rows(payload)
    bars: dict[str, dict[date, tuple[Any, Any]]] = {}
    descriptions: dict[str, str] = {}
    for symbol, row in rows:
        descriptions[symbol] = str(row.get("description") or symbol)
        for k in OFFSETS:
            suffix = "" if k == 0 else f"[{k}]"
            stamp = row.get(f"time{suffix}")
            if stamp is None:
                continue
            day = datetime.fromtimestamp(int(stamp), tz=UTC).astimezone(clock.NEW_YORK).date()
            bars.setdefault(symbol, {})[day] = (
                row.get(f"close{suffix}"),
                row.get(f"SMA200{suffix}"),
            )

    # A session whose close has not happened at the run instant is the running
    # one, and a figure for it would describe a day that has not finished.
    settled = sorted(
        {
            day
            for by_day in bars.values()
            for day in by_day
            if clock.is_us_session(day) and clock.us_session_close_utc(day) <= now
        },
        reverse=True,
    )
    out: list[Session] = []
    for day in settled:
        session = _session(bars, descriptions, day)
        if not out:
            if session.counted < MIN_CONSTITUENTS:
                raise FetchRefused(
                    f"the scanner's S&P 500 set counts {session.counted} companies with a "
                    f"200-day average for {session.asof}, fewer than {MIN_CONSTITUENTS}, so "
                    "the symbol set or its columns have changed"
                )
        elif not session.members >= out[0].members:
            continue
        out.append(session)
    if not out:
        raise FetchRefused("the scanner's S&P 500 set carries no settled session")
    return sorted(out, key=lambda one: one.asof)


def _rows(payload: Any) -> list[tuple[str, dict[str, Any]]]:
    try:
        data = payload["data"]
        if not isinstance(data, list):
            raise TypeError("data is not a list")
        rows = [(str(row["s"]), dict(zip(COLUMNS, row["d"], strict=True))) for row in data]
    except (KeyError, TypeError, ValueError) as exc:
        raise FetchRefused(
            f"the scanner's answer is not the shape this worker reads: {exc}"
        ) from exc
    if len(rows) < MIN_CONSTITUENTS:
        raise FetchRefused(
            f"the scanner's S&P 500 set returned {len(rows)} lines, fewer than "
            f"{MIN_CONSTITUENTS}, so the symbol set has changed"
        )
    return rows


def _session(
    bars: dict[str, dict[date, tuple[Any, Any]]], descriptions: dict[str, str], asof: date
) -> Session:
    usable: dict[str, tuple[str, bool]] = {}
    excluded: list[str] = []
    for symbol, description in descriptions.items():
        close, average = bars.get(symbol, {}).get(asof, (None, None))
        if close is None or average is None:
            # No bar for this session (halted, newly listed, or its third bar
            # back already fell off the scan), or no 200-day average yet.
            excluded.append(symbol)
            continue
        usable[symbol] = (description, dec(close) > dec(average))

    companies, folded = _one_line_per_company(usable)
    above = sum(1 for is_above in companies.values() if is_above)
    counted = len(companies)
    value = (
        (Decimal(above) * Decimal(100) / Decimal(counted)).quantize(_CENT, rounding=ROUND_HALF_UP)
        if counted
        else Decimal(0)
    )
    return Session(
        asof=asof,
        value=value,
        above=above,
        counted=counted,
        excluded=tuple(sorted(excluded)),
        folded=tuple(sorted(folded)),
        members=frozenset(usable),
    )


def _one_line_per_company(
    usable: dict[str, tuple[str, bool]],
) -> tuple[dict[str, bool], list[str]]:
    """Each company's vote, through its Class A line where it lists several.

    The company is the description with its class designation removed. Where no
    line is Class A, the first symbol in sort order stands in, so the choice is
    the same every day and a company never flips between its classes.
    """
    by_company: dict[str, list[tuple[str, str | None, bool]]] = {}
    for symbol, (description, is_above) in usable.items():
        match = _CLASS.search(description)
        company = _CLASS.sub("", description).strip() if match else description.strip()
        by_company.setdefault(company, []).append(
            (symbol, match.group(1) if match else None, is_above)
        )
    votes: dict[str, bool] = {}
    folded: list[str] = []
    for company, lines in by_company.items():
        lines.sort(key=lambda line: (line[1] != "A", line[0]))
        votes[company] = lines[0][2]
        folded.extend(line[0] for line in lines[1:])
    return votes, folded


def session_observation(session: Session, published: Published | None) -> Observation:
    extra: dict[str, Any] = {
        "method": (
            "share of S&P 500 companies whose close is above its 200-day simple average of "
            "closes; one vote per company, through its Class A line"
        ),
        "provider": "TradingView scanner, symbolset SYML:SP;SPX",
        "above": session.above,
        "counted": session.counted,
        "excluded": list(session.excluded),
        "folded": list(session.folded),
    }
    if published is not None and published.asof == session.asof:
        extra["s5th_close"] = str(published.value)
        extra["s5th_divergence"] = str(session.value - published.value)
    return Observation(
        series_key=SERIES_KEY,
        asof=session.asof,
        value=session.value,
        display=f"{session.value:.2f}%",
        tier=1,
        source="Computed from TradingView scanner closes and 200-day averages",
        observed_at=clock.us_session_close_utc(session.asof),
        extra=extra,
        # S5TH serves only its newest bar, so the cross-check exists only on
        # the run that saw that bar. A later re-statement of the session keeps
        # the one stored rather than erasing it.
        keep_extra=S5TH_EXTRA,
    )


def fallback_observation(published: Published, why: str, now: datetime) -> Observation:
    return Observation(
        series_key=SERIES_KEY,
        asof=published.asof,
        value=published.value,
        display=f"{published.value:.2f}%",
        # Never Tier 1. An aggregator's calculation over a constituent list
        # this system does not hold and cannot check.
        tier=2,
        source="TradingView INDEX:S5TH (Barchart S5TH)",
        observed_at=now,
        extra={**published.extra, "fallback": True, "computed_refused": why},
    )


def review(observations: Sequence[Observation]) -> list[Finding]:
    """A finding for a cross-check that failed, and one for a fallback used."""
    out: list[Finding] = []
    for one in observations:
        if one.series_key != SERIES_KEY:
            continue
        raw = one.extra.get("s5th_divergence")
        if raw is not None and abs(dec(raw)) > DIVERGENCE_POINTS:
            out.append(
                Finding(
                    check_key="breadth_divergence",
                    subject=SERIES_KEY,
                    sentence=(
                        f"Breadth for {one.asof} computed from the constituents reads "
                        f"{one.value}% and Barchart's S5TH reads {one.extra['s5th_close']}%, "
                        f"{abs(dec(raw))} points apart where the two normally sit within one. "
                        "Either the constituent set or one of the two averages has changed."
                    ),
                    severity=2,
                )
            )
        if one.extra.get("fallback"):
            out.append(
                Finding(
                    check_key="breadth_fallback",
                    subject=SERIES_KEY,
                    sentence=(
                        f"Breadth for {one.asof} is Barchart's S5TH, not the figure computed "
                        f"from the constituents, because {one.extra.get('computed_refused')}. "
                        "The two differ by about half a point by construction."
                    ),
                    severity=2,
                )
            )
    return out


# ---------------------------------------------------------------------------
# S5TH, from the symbol page
# ---------------------------------------------------------------------------


def read_s5th(html: str) -> Published:
    """The page's dated daily bar, or a refusal."""
    bar = _daily_bar(html)
    close = bar.get("close")
    stamp = bar.get("time")
    if close is None or stamp is None:
        raise FetchRefused(
            "TradingView's S5TH daily bar carries no close or no time, so the reading has no "
            "date it can honestly be filed under"
        )
    return Published(
        asof=datetime.fromtimestamp(int(float(stamp)), tz=UTC).date(),
        value=dec(close),
        extra={
            "symbol": "INDEX:S5TH",
            "provider": "barchart",
            "session_open": _maybe(bar.get("open")),
            "session_high": _maybe(bar.get("high")),
            "session_low": _maybe(bar.get("low")),
            # The vendor's own freshness marker. It moves when the underlying
            # datum is refreshed rather than when the page is rendered.
            "vendor_update_time": _maybe(bar.get("data_update_time")),
        },
    )


def _daily_bar(html: str) -> dict[str, Any]:
    blocks = _INIT_DATA.findall(html)
    if not blocks:
        raise FetchRefused(
            "TradingView's S5TH page carries no application/prs.init-data+json block, so the "
            "page shape this scraper reads has changed"
        )
    for block in blocks:
        try:
            payload = json.loads(block)
        except json.JSONDecodeError:
            continue
        found = _find_daily_bar(payload)
        if found is not None:
            return found
    raise FetchRefused(
        "TradingView's S5TH page has no data.symbol.daily_bar in any init-data block, so the "
        "page shape this scraper reads has changed"
    )


def _find_daily_bar(node: Any) -> dict[str, Any] | None:
    """The first `daily_bar` mapping anywhere in the payload.

    The block is keyed by a per-render identifier, so the path to the quote is
    not stable and cannot be written down. Searching for the shape is what
    survives a re-render; hard-coding the key would not.
    """
    if isinstance(node, dict):
        bar = node.get("daily_bar")
        if isinstance(bar, dict) and "close" in bar:
            return bar
        for value in node.values():
            found = _find_daily_bar(value)
            if found is not None:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _find_daily_bar(value)
            if found is not None:
                return found
    return None


def _maybe(value: Any) -> str | None:
    return None if value is None else str(value)
