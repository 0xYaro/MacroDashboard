"""CoinGecko: BTC, ETH and SOL spot, BTC dominance, and the open-interest proxy.

Ported from `fetch_data.py`'s `fetch_crypto_prices`, `fetch_btc_dominance` and
`fetch_open_interest`.

Two corrections the contract requires.

**A price gets the UTC day it was read on, not the day the run started.** The
old script took the last point's own timestamp, which is right, and this keeps
that. `config/series.yaml` declares `calendar_day` for all three, so a 24-hour
market has one observation per UTC day and `observed_at` is the run instant.

**Open interest is never Tier 1.** It is the sum of `open_interest_btc` across
the top ten exchanges by open interest, converted at spot. `tier_floor: 2` in
the registry says the series can never claim better, and the note travels with
the row so the proxy is visible wherever the number is.

**`btc_50w_ma` is the mean of fifty weekly closes, not a 350-day mean.** It is
the crypto framework's anchor for Phase 1 and for both the 5-to-1 and the 1-to-2
transition recipes, and "BTC holds above the 50W MA" was the one crypto tracker
condition that could not be typed at all.

A week's close is its final daily point, ISO weeks ending Sunday, which is the
boundary every crypto weekly candle already uses. The keyless `market_chart`
serves 366 daily points at `days=365`, which is fifty-two weeks: margin for two
and no more, and `days=max` answers 401 without a key. Fewer than fifty complete
weeks yields null with a note, because a forty-three-week average wearing the
name of a fifty-week one is worse than an absent figure.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from ..faults import group_faults
from ..http import Client, FetchRefused
from ..model import Observation, RawBatch
from ..units import dec
from .base import FetchContext, Fetched, batch

SOURCE = "coingecko"

CHART_URL = (
    "https://api.coingecko.com/api/v3/coins/{coin}/market_chart?vs_currency=usd&days={days}"
)

#: How much history each coin is asked for. BTC needs a year because the 50-week
#: average does; the other two need only enough to date the newest point.
CHART_DAYS: dict[str, int] = {"bitcoin": 365, "ethereum": 30, "solana": 30}

#: Weeks in the moving average. Fifty, and never forty-nine.
MA_WEEKS = 50
GLOBAL_URL = "https://api.coingecko.com/api/v3/global"
DERIVATIVES_URL = (
    "https://api.coingecko.com/api/v3/derivatives/exchanges?order=open_interest_btc_desc&per_page=10"
)


@dataclass(frozen=True, slots=True)
class Coin:
    series_key: str
    coin_id: str
    decimals: int


COINS: tuple[Coin, ...] = (
    Coin("btc", "bitcoin", 0),
    Coin("eth", "ethereum", 0),
    Coin("sol", "solana", 2),
)

BY_ID: dict[str, Coin] = {coin.coin_id: coin for coin in COINS}


class CoinGecko:
    """The CoinGecko source."""

    source = SOURCE
    series = (
        *[coin.series_key for coin in COINS],
        "btc_50w_ma",
        "btc_dominance",
        "crypto_open_interest",
    )

    async def fetch(self, client: Client, ctx: FetchContext) -> Fetched:
        out: list[RawBatch] = []
        faults: list[str] = []
        for coin in COINS:
            try:
                payload = await client.get_json(
                    CHART_URL.format(coin=coin.coin_id, days=CHART_DAYS[coin.coin_id])
                )
            except FetchRefused as exc:
                faults.append(f"{coin.coin_id}: {exc}")
                continue
            out.append(batch(SOURCE, f"chart:{coin.coin_id}", ctx, payload))
        for kind, url in (("global", GLOBAL_URL), ("derivatives", DERIVATIVES_URL)):
            try:
                out.append(batch(SOURCE, kind, ctx, await client.get_json(url)))
            except FetchRefused as exc:
                faults.append(f"{kind}: {exc}")
        if not out:
            raise FetchRefused("CoinGecko served nothing: " + group_faults(faults))
        return Fetched(batches=out, faults=faults)

    def normalize(self, batches: Sequence[RawBatch], ctx: FetchContext) -> list[Observation]:
        out: list[Observation] = []
        spot: dict[str, Decimal] = {}
        for raw in batches:
            kind = str(raw.kind)
            if kind.startswith("chart:"):
                coin = BY_ID.get(kind.removeprefix("chart:"))
                if coin is None:
                    continue
                produced = normalize_chart(
                    coin, raw.payload, ctx.now, after=ctx.last_asof(coin.series_key)
                )
                out.extend(produced)
                # The newest row is last, and it is the spot the proxy converts at.
                for one in produced:
                    if one.value is not None:
                        spot[one.series_key] = one.value
                if coin.coin_id == "bitcoin":
                    out.extend(normalize_50w_ma(raw.payload, ctx.now))
            elif kind == "global":
                out.extend(normalize_global(raw.payload, ctx.now))
        for raw in batches:
            if str(raw.kind) == "derivatives":
                out.extend(normalize_open_interest(raw.payload, spot.get("btc"), ctx.now))
        return out


def normalize_chart(
    coin: Coin, payload: Any, now: datetime, *, after: date | None = None
) -> list[Observation]:
    """The newest point of a `market_chart` response, and any day the store missed.

    The `asof` is the UTC date of the point's own timestamp. It is the newest
    *complete* daily point that CoinGecko serves plus, usually, a partial point
    for the running day; the partial one is the reading this system wants,
    because a 24-hour market has no close and the contract says `observed_at`
    for such a series is the run time.

    A day strictly after `after`, the newest as-of already stored, and before
    the running day has no row at all, so its last served point fills it.
    Days already stored are never re-stated: they hold a sample taken at a run,
    and the chart's point for that day is a different moment, so replacing one
    with the other would be a substitution rather than a correction. With no
    cursor only the newest point is stored. Oldest first.
    """
    prices = (payload or {}).get("prices")
    if not prices:
        raise FetchRefused(f"CoinGecko served no prices for {coin.coin_id}")
    by_day: dict[date, tuple[int, Decimal]] = {}
    for stamp_ms, price in sorted(prices, key=lambda point: int(point[0])):
        day = datetime.fromtimestamp(int(stamp_ms) / 1000, tz=UTC).date()
        by_day[day] = (int(stamp_ms), dec(price))
    newest = max(by_day)
    out: list[Observation] = []
    for day in sorted(by_day):
        if day != newest and (after is None or day <= after):
            continue
        stamp_ms, value = by_day[day]
        out.append(
            Observation(
                series_key=coin.series_key,
                asof=day,
                value=value,
                display=f"${value:,.{coin.decimals}f}",
                tier=1,
                source="CoinGecko market_chart",
                observed_at=now,
                extra={"coin_id": coin.coin_id, "point_ms": str(stamp_ms)},
            )
        )
    return out


def weekly_closes(payload: Any) -> list[tuple[date, Decimal]]:
    """Every complete ISO week in the payload, as `(week ending Sunday, close)`.

    A week's close is its final daily point. The newest week is dropped unless
    the payload reaches its Sunday, because a week that has not ended has no
    close and averaging a partial one in would move the figure by whatever the
    last three days did.
    """
    prices = (payload or {}).get("prices") or []
    by_week: dict[date, tuple[date, Decimal]] = {}
    for stamp_ms, price in prices:
        day = datetime.fromtimestamp(int(stamp_ms) / 1000, tz=UTC).date()
        ending = day + timedelta(days=6 - day.weekday())
        previous = by_week.get(ending)
        if previous is None or day >= previous[0]:
            by_week[ending] = (day, dec(price))
    out: list[tuple[date, Decimal]] = []
    for ending in sorted(by_week):
        last_day, close = by_week[ending]
        if last_day == ending:  # the week reached its Sunday
            out.append((ending, close))
    return out


def normalize_50w_ma(payload: Any, now: datetime) -> list[Observation]:
    """The mean of the last fifty weekly closes, or a null with its reason."""
    weeks = weekly_closes(payload)
    if not weeks:
        raise FetchRefused("CoinGecko served no BTC history to average")
    asof = weeks[-1][0]
    extra: dict[str, Any] = {
        "weeks": MA_WEEKS,
        "weeks_available": len(weeks),
        "week_ends": asof.isoformat(),
        "method": "mean of the last 50 weekly closes, ISO weeks ending Sunday",
    }
    if len(weeks) < MA_WEEKS:
        return [
            Observation(
                series_key="btc_50w_ma",
                asof=asof,
                value=None,
                display="",
                tier=4,
                source="Computed from CoinGecko market_chart daily history",
                observed_at=now,
                note=(
                    f"the 50-week average is withheld: CoinGecko served {len(weeks)} complete "
                    f"weeks and it needs {MA_WEEKS}, and an average over fewer weeks is a "
                    "different statistic wearing the same name"
                ),
                extra=extra,
            )
        ]
    window = [close for _, close in weeks[-MA_WEEKS:]]
    value = sum(window, Decimal(0)) / Decimal(MA_WEEKS)
    extra["oldest_week"] = weeks[-MA_WEEKS][0].isoformat()
    return [
        Observation(
            series_key="btc_50w_ma",
            asof=asof,
            value=value,
            display=f"${value:,.0f}",
            tier=1,
            source="Computed from CoinGecko market_chart daily history",
            observed_at=now,
            extra=extra,
        )
    ]


def normalize_global(payload: Any, now: datetime) -> list[Observation]:
    """BTC dominance from the `global` endpoint."""
    data = (payload or {}).get("data") or {}
    share = (data.get("market_cap_percentage") or {}).get("btc")
    if share is None:
        raise FetchRefused("CoinGecko's global response carries no BTC market-cap percentage")
    updated = data.get("updated_at")
    asof = (
        datetime.fromtimestamp(int(updated), tz=UTC).date() if updated is not None else now.date()
    )
    value = dec(share)
    total = (data.get("total_market_cap") or {}).get("usd")
    return [
        Observation(
            series_key="btc_dominance",
            asof=asof,
            value=value,
            display=f"{value:.2f}%",
            tier=1,
            source="CoinGecko global API",
            observed_at=now,
            extra={"total_market_cap_usd": None if total is None else str(dec(total))},
        )
    ]


def normalize_open_interest(
    payload: Any, btc_spot: Decimal | None, now: datetime
) -> list[Observation]:
    """The top-ten open-interest proxy, in dollars, or a null with its reason."""
    exchanges = payload or []
    if not isinstance(exchanges, list) or not exchanges:
        raise FetchRefused("CoinGecko's derivatives response carries no exchanges")
    oi_btc = sum(
        (dec(row.get("open_interest_btc") or 0) for row in exchanges),
        Decimal(0),
    )
    names = [str(row.get("name")) for row in exchanges]
    extra: dict[str, Any] = {
        "oi_btc": str(oi_btc),
        "exchanges": names,
        "method": (
            "sum of open_interest_btc across the top ten exchanges by open interest, "
            "converted at BTC spot"
        ),
    }
    if btc_spot is None:
        return [
            Observation(
                series_key="crypto_open_interest",
                asof=now.date(),
                value=None,
                display="",
                tier=4,
                source="CoinGecko derivatives top-10 proxy",
                observed_at=now,
                note=(
                    "open interest is withheld: the proxy is denominated in BTC and this run "
                    "has no BTC spot price to convert it at"
                ),
                extra=extra,
            )
        ]
    usd = oi_btc * btc_spot
    extra["btc_px"] = str(btc_spot)
    return [
        Observation(
            series_key="crypto_open_interest",
            asof=now.date(),
            value=usd,
            display=f"${usd / Decimal(10) ** 9:,.2f}B proxy; {oi_btc / Decimal(10) ** 6:.3f}M BTC",
            tier=2,
            source="CoinGecko derivatives top-10 proxy",
            observed_at=now,
            note=(
                "proxy: the sum of open_interest_btc across the top ten exchanges by open "
                "interest, converted at spot. It is not a measurement of total crypto open "
                "interest and can never be Tier 1."
            ),
            extra=extra,
        )
    ]
