"""The next FOMC's hike odds, computed from CBOT 30-day fed funds futures.

`fedwatch_hike_prob_next` used to be a 09:40 search agent reading news that
quoted CME FedWatch. CME's own page is blocked, so the number arrived second
hand, dated whenever an article happened to quote it (73.5% "as quoted Sep 24"
was still the Desk's reading four days later), and a search agent that breaks
returns a plausible number rather than a fault. `docs/DECISIONS.md` 2026-09-28.

FedWatch is arithmetic over public futures prices, so this module does the
arithmetic. A 30-day fed funds contract settles on the average effective rate
over its month, so 100 minus its price is the market's expected average for
that month. For a meeting on day `d` of a month of `N` days, the rate before the
meeting holds for `d` days and the rate after it for `N - d`:

    average(M) = (d * before + (N - d) * after) / N

One of the two rates comes from a neighbouring month with no meeting in it,
where the average is a single rate, and the equation gives the other:

- the month after the meeting has no meeting: `after` is that month's implied
  rate and `before` is solved from the meeting month;
- otherwise the month before has none: `before` is that month's rate and
  `after` is solved.

The hike probability is the implied move in quarter points, clamped to 0 to
100, which is FedWatch's binary 25 bp reading. The signed move in basis points
travels in `extra`, so a priced cut or a priced 50 bp move is never lost.

What this is not: CME's own figure. CME uses the same method with the
effective rate for the running month, so the two agree to within a few points,
not to the decimal, and the source string says it was computed here.

The meeting calendar is the Fed's published schedule (decision day, the second
day of each meeting). The Fed publishes a year ahead; when the calendar below
runs out the source refuses and says which year to add, rather than guess.
"""

from __future__ import annotations

import calendar
import urllib.parse
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from .. import clock
from ..faults import group_faults
from ..http import Client, FetchRefused
from ..model import Observation, RawBatch
from .base import FetchContext, Fetched, batch
from .yahoo import CHART_URL, _bars

SOURCE = "fed_funds_futures"
SERIES_KEY = "fedwatch_hike_prob_next"
SOURCE_NAME = "Computed from CBOT 30-day fed funds futures (Yahoo), FedWatch method"

#: FOMC decision days (the second day of each meeting), from
#: federalreserve.gov/monetarypolicy/fomccalendars.htm. Add the next year's as
#: soon as the Fed publishes it; the source refuses once these run out.
FOMC_DECISIONS: tuple[date, ...] = (
    date(2026, 1, 28),
    date(2026, 3, 18),
    date(2026, 4, 29),
    date(2026, 6, 17),
    date(2026, 7, 29),
    date(2026, 9, 16),
    date(2026, 10, 28),
    date(2026, 12, 9),
)

#: CME month codes, January first.
MONTH_CODES = "FGHJKMNQUVXZ"

#: Sessions re-read on each run, as the other session sources do: a missed run
#: is recovered by the next, and a revised settlement is picked up.
REPLAY_DAYS = 10

QUARTER_POINT = Decimal("0.25")


@dataclass(frozen=True, slots=True)
class Month:
    year: int
    month: int

    @property
    def symbol(self) -> str:
        return f"ZQ{MONTH_CODES[self.month - 1]}{self.year % 100:02d}.CBT"

    @property
    def days(self) -> int:
        return calendar.monthrange(self.year, self.month)[1]

    def shift(self, by: int) -> Month:
        index = self.year * 12 + self.month - 1 + by
        return Month(index // 12, index % 12 + 1)

    @classmethod
    def of(cls, day: date) -> Month:
        return cls(day.year, day.month)


def next_meeting(day: date) -> date | None:
    """The first decision after `day`. On a decision day the close already carries it."""
    return next((one for one in FOMC_DECISIONS if one > day), None)


def _has_meeting(month: Month) -> bool:
    return any(Month.of(one) == month for one in FOMC_DECISIONS)


def _before(one: Month, other: Month) -> bool:
    return (one.year, one.month) < (other.year, other.month)


def contracts_for(meeting: date) -> tuple[Month, Month]:
    """The meeting month and the meeting-free neighbour the arithmetic leans on."""
    month = Month.of(meeting)
    for anchor in (month.shift(1), month.shift(-1)):
        # A month past the calendar's last year might hold a meeting nobody has
        # listed yet (January usually does), so it is never trusted as meeting-free.
        if anchor.year <= FOMC_DECISIONS[-1].year and not _has_meeting(anchor):
            return month, anchor
    raise FetchRefused(
        f"neither month either side of the {meeting.isoformat()} FOMC is known to be "
        "meeting-free, so neither gives a single-rate anchor"
    )


class FedFundsFutures:
    """The fed funds futures source."""

    source = SOURCE
    series = (SERIES_KEY,)

    async def fetch(self, client: Client, ctx: FetchContext) -> Fetched:
        today = ctx.now.astimezone(clock.NEW_YORK).date()
        meeting = next_meeting(today)
        if meeting is None:
            raise FetchRefused(
                f"the FOMC calendar in fed_funds_futures.py ends at "
                f"{FOMC_DECISIONS[-1].isoformat()}; add {today.year + 1}'s decision days "
                "from federalreserve.gov"
            )
        # Every contract any session in the replay window needs: the window can
        # straddle a meeting, and then the earlier sessions price the one before.
        #
        # A contract for a month already over has expired and Yahoo answers 404
        # for it, so it is not asked for: the sessions that needed it (the days
        # before a meeting early in last month's window) simply drop out, rather
        # than every run reporting the source partial.
        current = Month.of(today)
        months: set[Month] = set()
        for offset in range(REPLAY_DAYS + 5):
            upcoming = next_meeting(today - timedelta(days=offset))
            if upcoming is not None:
                months.update(one for one in contracts_for(upcoming) if not _before(one, current))
        out: list[RawBatch] = []
        faults: list[str] = []
        for month in sorted(months, key=lambda one: (one.year, one.month)):
            url = CHART_URL.format(
                symbol=urllib.parse.quote(month.symbol, safe=""), range="1mo"
            )
            try:
                out.append(batch(SOURCE, month.symbol, ctx, await client.get_json(url)))
            except FetchRefused as exc:
                faults.append(f"{month.symbol}: {exc}")
        if not out:
            raise FetchRefused("Yahoo served no fed funds contract: " + group_faults(faults))
        return Fetched(batches=out, faults=faults)

    def normalize(self, batches: Sequence[RawBatch], ctx: FetchContext) -> list[Observation]:
        closes: dict[str, dict[date, Decimal]] = {}
        for raw in batches:
            bars, _zone = _bars(raw.payload, ctx.now)
            closes[raw.kind] = {bar.session: bar.close for bar in bars}
        return normalize_closes(closes, ctx.now)


def normalize_closes(
    closes: dict[str, dict[date, Decimal]], now: datetime
) -> list[Observation]:
    """One reading per completed US session in the window whose contracts both settled."""
    settled = clock.last_completed_us_session(now)
    since = settled - timedelta(days=REPLAY_DAYS)
    sessions = sorted({day for by_day in closes.values() for day in by_day})
    out: list[Observation] = []
    for day in sessions:
        if day < since or day > settled or not clock.is_us_session(day):
            continue
        meeting = next_meeting(day)
        if meeting is None:
            continue
        month, anchor = contracts_for(meeting)
        price_month = closes.get(month.symbol, {}).get(day)
        price_anchor = closes.get(anchor.symbol, {}).get(day)
        if price_month is None or price_anchor is None:
            continue
        reading = implied(meeting, month, anchor, price_month, price_anchor)
        if reading is None:
            continue
        out.append(_observation(day, meeting, month, anchor, price_month, price_anchor, reading))
    return out


@dataclass(frozen=True, slots=True)
class Implied:
    before: Decimal
    after: Decimal

    @property
    def move_bp(self) -> Decimal:
        return (self.after - self.before) * 100

    @property
    def hike_probability(self) -> Decimal:
        share = (self.after - self.before) / QUARTER_POINT * 100
        return min(Decimal(100), max(Decimal(0), share))


def implied(
    meeting: date, month: Month, anchor: Month, price_month: Decimal, price_anchor: Decimal
) -> Implied | None:
    """The rates either side of the meeting. `None` when the arithmetic has no room."""
    days, d = Decimal(month.days), Decimal(meeting.day)
    average = 100 - price_month
    anchored = 100 - price_anchor
    if anchor == month.shift(1):
        if d == 0:
            return None
        after = anchored
        before = (days * average - (days - d) * after) / d
    else:
        if days - d == 0:
            return None
        before = anchored
        after = (days * average - d * before) / (days - d)
    return Implied(before=before, after=after)


def _observation(
    day: date,
    meeting: date,
    month: Month,
    anchor: Month,
    price_month: Decimal,
    price_anchor: Decimal,
    reading: Implied,
) -> Observation:
    value = reading.hike_probability.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    move = reading.move_bp.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    when = f"{meeting.strftime('%b')} {meeting.day}"
    return Observation(
        series_key=SERIES_KEY,
        asof=day,
        value=value,
        display=f"{value}% hike odds for the {when} FOMC (implied {move:+} bp)",
        tier=1,
        source=SOURCE_NAME,
        observed_at=clock.us_session_close_utc(day),
        extra={
            "meeting": meeting.isoformat(),
            "implied_move_bp": str(move),
            "rate_before": str(reading.before.quantize(Decimal("0.0001"))),
            "rate_after": str(reading.after.quantize(Decimal("0.0001"))),
            "contracts": {month.symbol: str(price_month), anchor.symbol: str(price_anchor)},
        },
    )
