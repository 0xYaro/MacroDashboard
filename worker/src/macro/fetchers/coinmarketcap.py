"""CoinMarketCap's alt-season index.

Ported from `fetch_data.py`'s `fetch_alt_season`. The endpoint is the public
data API the site's own chart calls, it carries no key, and it dates every
point, so the `asof` is the newest point's own timestamp.

Tier 2 by construction: the index is a vendor's calculation over a constituent
list this system does not hold and cannot check, which is what `tier_floor: 2`
in `config/series.yaml` states.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from ..http import Client, FetchRefused
from ..model import Observation, RawBatch
from ..units import dec
from .base import FetchContext, Fetched, batch, one

SOURCE = "coinmarketcap"
SERIES_KEY = "alt_season_index"

URL = "https://api.coinmarketcap.com/data-api/v3/altcoin-season/chart?start={start}&end={end}"

#: How far back to ask. Sixty days covers the longest gap a fortnight's outage
#: could leave and is still one small response.
WINDOW_DAYS = 60

ALTCOIN_SEASON = Decimal(75)
BITCOIN_SEASON = Decimal(25)


class CoinMarketCap:
    """The alt-season source."""

    source = SOURCE
    series = (SERIES_KEY,)

    async def fetch(self, client: Client, ctx: FetchContext) -> Fetched:
        end = int(ctx.now.timestamp())
        start = end - WINDOW_DAYS * 86400
        return one(
            batch(
                SOURCE,
                "altcoin_season",
                ctx,
                await client.get_json(URL.format(start=start, end=end)),
            )
        )

    def normalize(self, batches: Sequence[RawBatch], ctx: FetchContext) -> list[Observation]:
        out: list[Observation] = []
        for raw in batches:
            out.extend(normalize_chart(raw.payload, ctx.now, after=ctx.last_asof(SERIES_KEY)))
        return out


def normalize_chart(
    payload: Any, now: datetime, *, after: date | None = None
) -> list[Observation]:
    """The newest dated point, and one point per UTC day the store has not reached.

    The newest point is a live sample taken during the running day, which is
    what every stored row is: a `calendar_day` series is read at the run
    instant. The older points are stamped 00:00 UTC, a different moment of
    the day, so a day already stored is never re-stated from them; that
    would swap one sample for another and call it a correction. A day after
    `after`, the newest stored as-of, has no sample at all, and its last
    served point fills it. With no cursor nothing but the newest is stored.
    """
    points = _points(payload)
    if not points:
        raise FetchRefused("CoinMarketCap's alt-season chart carries no points")
    ordered = sorted(points, key=lambda point: int(point["timestamp"]))
    by_day: dict[date, dict[str, Any]] = {}
    for point in ordered:
        by_day[datetime.fromtimestamp(int(point["timestamp"]), tz=UTC).date()] = point
    newest = max(by_day)
    wanted = [
        day for day in sorted(by_day) if day == newest or (after is not None and day > after)
    ]
    return [_observation(by_day[day], day, now) for day in wanted]


def _observation(point: dict[str, Any], asof: date, now: datetime) -> Observation:
    value = dec(point["altcoinIndex"])
    return Observation(
        series_key=SERIES_KEY,
        asof=asof,
        value=value,
        display=f"{value:.0f} ({season(value)})",
        tier=2,
        source="CoinMarketCap public API",
        observed_at=now,
        extra={
            "season": season(value),
            "altcoin_marketcap_usd": _maybe(point.get("altcoinMarketcap")),
            "point_timestamp": str(int(point["timestamp"])),
        },
    )


def season(value: Decimal) -> str:
    """The vendor's own three-way labelling, at 75 and 25."""
    if value >= ALTCOIN_SEASON:
        return "Altcoin Season"
    if value <= BITCOIN_SEASON:
        return "Bitcoin Season"
    return "Neutral"


def _points(payload: Any) -> list[dict[str, Any]]:
    """The point list, wherever this vendor put it this month.

    It has been served both as `data.points` and as a bare `data` list. Both are
    read rather than one being assumed, because the difference is a silent empty
    series and not an error.
    """
    data = (payload or {}).get("data")
    if isinstance(data, dict):
        points = data.get("points")
        return list(points) if isinstance(points, list) else []
    if isinstance(data, list):
        return list(data)
    return []


def _maybe(value: Any) -> str | None:
    return None if value is None else str(dec(value))
