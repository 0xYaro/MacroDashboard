"""Yahoo's chart API: index futures, cash indices, metals, energy, the dollar, vol.

Ported from `fetch_data.py`'s `fetch_index_futures`, `fetch_cash_index` and
`fetch_yahoo_simple`, with three changes the contract requires.

**Cash indices are their own series.** The old script buried `^GSPC` in the
future's `extra` as a display string. Every phase condition, scenario band and
setup level written to date refers to the cash index, and a condition cannot
reference a value buried in another series' `extra`. R1 exists because the two
were once conflated in the week-over-week arithmetic.

**Session high and low are stored.** The old script kept only the close. The
setup band-touch flag is derived from the high and the low, and without them a
setup whose entry band was reached and refused intraday is indistinguishable
from one price never approached.

**An in-progress bar is never stored.** Yahoo returns the running session as a
bar like any other. Storing it would put an unfinished session's close into a
day that has not finished, which is the same fault as carrying a value forward,
pointed the other way.
"""

from __future__ import annotations

import urllib.parse
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from ..faults import group_faults
from ..http import Client, FetchRefused
from ..model import Observation, RawBatch
from ..units import dec
from .base import FetchContext, Fetched, batch

SOURCE = "yahoo"

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range={range}&interval=1d"


@dataclass(frozen=True, slots=True)
class Spec:
    """One Yahoo symbol and the series it becomes."""

    series_key: str
    symbol: str
    #: How much history to ask for. Two years for anything carrying a 200-day
    #: average, because 200 sessions plus the ten-session slope lookback needs
    #: 210 closes and a one-year window is about 250 with holidays: enough until
    #: a long outage, and not enough after one.
    window: str = "3mo"
    #: Whether the snapshot row carries the distance to, and slope of, the
    #: 200-day average. Only the two indices whose Read must state it.
    dma200: bool = False


SPECS: tuple[Spec, ...] = (
    Spec("spx_front", "ES=F"),
    Spec("spx_cash", "^GSPC", window="2y", dma200=True),
    Spec("dow_front", "YM=F"),
    Spec("dow_cash", "^DJI", window="2y"),
    Spec("nasdaq_cash", "^IXIC", window="2y", dma200=True),
    Spec("rut_front", "RTY=F"),
    Spec("rut_cash", "^RUT", window="2y"),
    Spec("gold", "GC=F"),
    Spec("silver", "SI=F"),
    Spec("wti", "CL=F"),
    Spec("brent", "BZ=F"),
    Spec("dxy", "DX-Y.NYB"),
    Spec("vix", "^VIX"),
    Spec("move", "^MOVE"),
)

BY_KEY: dict[str, Spec] = {spec.series_key: spec for spec in SPECS}


@dataclass(frozen=True, slots=True)
class Bar:
    """One completed daily bar, in the exchange's own calendar."""

    session: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal


class Yahoo:
    """The Yahoo chart source."""

    source = SOURCE
    series = tuple(spec.series_key for spec in SPECS)

    async def fetch(self, client: Client, ctx: FetchContext) -> Fetched:
        out: list[RawBatch] = []
        faults: list[str] = []
        for spec in SPECS:
            url = CHART_URL.format(
                symbol=urllib.parse.quote(spec.symbol, safe=""), range=spec.window
            )
            try:
                out.append(batch(SOURCE, spec.series_key, ctx, await client.get_json(url)))
            except FetchRefused as exc:
                # One symbol failing costs that symbol and nothing else. A
                # delisted or renamed ticker must not take the whole snapshot
                # down with it.
                #
                # The fault travels out rather than being dropped once some
                # other symbol succeeds. Dropping it is how a run that read one
                # symbol of fourteen recorded itself as `ok`: the breaker opened
                # after two 429s, the remaining twelve were never attempted, and
                # they produced no rows, no gaps and no sentence.
                faults.append(f"{spec.symbol}: {exc}")
        if not out:
            raise FetchRefused("Yahoo served no symbol: " + group_faults(faults))
        return Fetched(batches=out, faults=faults)

    def normalize(self, batches: Sequence[RawBatch], ctx: FetchContext) -> list[Observation]:
        out: list[Observation] = []
        for raw in batches:
            spec = BY_KEY.get(raw.kind)
            if spec is None:
                continue
            out.extend(normalize_chart(spec, raw.payload, ctx.now))
        return out


#: How far back each run re-reads completed bars. Long enough to cover the
#: snapshot's thirty-day trend column and any bar Yahoo served null and filled
#: in later; short enough that a run upserts a few hundred rows, not thousands.
REPLAY_DAYS = 45


def normalize_chart(spec: Spec, payload: Any, now: datetime) -> list[Observation]:
    """One chart payload into every completed bar of the last `REPLAY_DAYS`.

    Every bar carries its own 200-day read where the spec asks for one,
    computed from the closes up to that bar. The older bars are re-stated on
    every run, which is what lets a replayable gap actually be replayed: Yahoo
    serves a null bar for some 24-hour contracts on some days and fills it in
    later, and a fetcher that stored only the newest bar would move its cursor
    past the hole and never ask for it again. Every write is an upsert on
    `(series_id, asof)` that replaces `extra` whole, which is why each
    re-stated bar must carry the same read the run on its own day stored.
    """
    bars, exchange_tz = _bars(payload, now)
    if not bars:
        return []
    latest = bars[-1]
    since = latest.session - timedelta(days=REPLAY_DAYS)
    out: list[Observation] = []
    for index, bar in enumerate(bars):
        if bar.session < since:
            continue
        extra: dict[str, Any] = {
            "symbol": spec.symbol,
            "session_high": str(bar.high),
            "session_low": str(bar.low),
            "session_open": str(bar.open),
            "exchange_timezone": exchange_tz,
        }
        if spec.dma200:
            # Every re-stated bar carries its own read, computed from the closes
            # up to and including it. The upsert replaces `extra` wholesale, so
            # a re-stated row without the read would erase the one an earlier
            # run stored for that day.
            distance, slope = dma200(bars[: index + 1])
            extra["dma200_pct"] = None if distance is None else str(distance)
            extra["dma200_slope"] = slope
        out.append(
            Observation(
                series_key=spec.series_key,
                asof=bar.session,
                value=bar.close,
                display=_display(bar.close),
                tier=1,
                source=f"Yahoo chart API ({spec.symbol})",
                observed_at=_close_instant(bar.session, exchange_tz, payload),
                extra=extra,
            )
        )
    return out


def _bars(payload: Any, now: datetime) -> tuple[list[Bar], str]:
    """Every completed daily bar in the payload, oldest first."""
    try:
        chart = payload["chart"]
        error = chart.get("error")
        if error:
            raise FetchRefused(f"Yahoo answered with an error: {error}")
        result = chart["result"][0]
        meta = result["meta"]
        stamps = result["timestamp"]
        quote = result["indicators"]["quote"][0]
    except (KeyError, IndexError, TypeError) as exc:
        raise FetchRefused(
            f"Yahoo's chart payload is not the shape this worker reads: {exc}"
        ) from exc

    zone = ZoneInfo(str(meta.get("exchangeTimezoneName") or "America/New_York"))
    running = _running_session_start(meta, now)

    bars: list[Bar] = []
    for index, stamp in enumerate(stamps):
        moment = datetime.fromtimestamp(stamp, tz=UTC)
        if running is not None and index == len(stamps) - 1 and moment >= running:
            # Only the newest bar can be the running session. A bar with a newer
            # one after it has finished, whatever the trading period says.
            continue
        close = quote["close"][index]
        high = quote["high"][index]
        low = quote["low"][index]
        open_ = quote["open"][index]
        if close is None or high is None or low is None or open_ is None:
            # Yahoo pads a holiday, and a halted session, with nulls. A bar
            # missing any leg is not a session this worker can describe.
            continue
        bars.append(
            Bar(
                session=moment.astimezone(zone).date(),
                open=dec(open_),
                high=dec(high),
                low=dec(low),
                close=dec(close),
            )
        )
    return bars, str(zone)


def _running_session_start(meta: dict[str, Any], now: datetime) -> datetime | None:
    """When the session still running at `now` began, or `None` if none is.

    Yahoo stamps a daily bar at the session's open, and returns the running
    session as the newest bar. A bar that is the newest one and started at or
    after this instant is that running session, and is not stored.

    **Only the newest bar.** For a 24-hour instrument (index futures, metals,
    energy, the dollar) Yahoo's "regular" period is the whole New York calendar
    day, 04:00 to 03:59 UTC, and the previous day's settled bar is stamped 04:00
    inside it. Reading every bar inside the period as unfinished dropped that
    settled bar on every 09:30 Singapore run, put nine series a day behind the
    cash indices, and opened a false gap for each of them. The running bar
    itself is stamped at its last trade, which in New York is still the previous
    day, so keeping it would overwrite the settled close with a live quote.
    """
    period = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
    start, end = period.get("start"), period.get("end")
    if start is None or end is None:
        # No trading period in the payload. At 09:30 and 21:00 Singapore the US
        # regular session is closed either way, so nothing is treated as running.
        return None
    if now >= datetime.fromtimestamp(int(end), tz=UTC):
        return None
    return datetime.fromtimestamp(int(start), tz=UTC)


def _close_instant(session: date, exchange_tz: str, payload: Any) -> datetime:
    """The moment the reading described, which for a session series is the close."""
    meta = payload["chart"]["result"][0]["meta"]
    period = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
    end = period.get("end")
    if end is not None:
        candidate = datetime.fromtimestamp(int(end), tz=UTC)
        if candidate.astimezone(ZoneInfo(exchange_tz)).date() == session:
            return candidate
    # Not the current session, so its own close is not in the payload. The
    # exchange's own 16:00 is the honest stand-in and it is recorded as such.
    return datetime.combine(
        session, datetime.min.time().replace(hour=16), tzinfo=ZoneInfo(exchange_tz)
    ).astimezone(UTC)


def dma200(bars: Sequence[Bar]) -> tuple[Decimal | None, str | None]:
    """Distance from the 200-day average, in percent, and its slope.

    Ported from `fetch_data.py`'s `dma200` unchanged, including its thresholds:
    the slope compares today's average against the one ten sessions ago and
    calls anything inside a tenth of a percent flat.

    Null, not zero, when there are fewer than 200 closes. A 200-day average
    computed over 180 days is a different statistic wearing the same name.
    """
    closes = [bar.close for bar in bars]
    if len(closes) < 200:
        return None, None
    now = sum(closes[-200:], Decimal(0)) / Decimal(200)
    distance = (closes[-1] / now - Decimal(1)) * Decimal(100)
    if len(closes) < 210:
        return distance, None
    earlier = sum(closes[-210:-10], Decimal(0)) / Decimal(200)
    change = (now / earlier - Decimal(1)) * Decimal(100)
    if change > Decimal("0.1"):
        return distance, "rising"
    if change < Decimal("-0.1"):
        return distance, "falling"
    return distance, "flat"


def _display(value: Decimal) -> str:
    return f"{value:,.2f}"
