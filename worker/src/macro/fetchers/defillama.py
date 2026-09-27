"""DefiLlama: the stablecoin float and total DeFi TVL.

Ported from `fetch_data.py`'s `fetch_stablecoins` and `fetch_tvl`.

The stablecoin endpoint has no date on it at all: it states the circulating
supply as of the moment it is called, so the `asof` is the UTC day of the run
and `observed_at` is the run instant, which is what `calendar_day` in
`config/series.yaml` declares.

The TVL endpoint serves a dated history, so its `asof` is the newest point's own
date and the series is `replayable`.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from ..faults import group_faults
from ..http import Client, FetchRefused
from ..model import Observation, RawBatch
from ..units import dec
from .base import FetchContext, Fetched, batch

SOURCE = "defillama"

STABLECOINS_URL = "https://stablecoins.llama.fi/stablecoins?includePrices=false"
TVL_URL = "https://api.llama.fi/v2/historicalChainTvl"

#: How far back each run re-states the TVL history. The same window as Yahoo's.
#: The stablecoin endpoint has no history to re-state: its chart lives at a
#: different endpoint, which this source does not call.
REPLAY_DAYS = 45

#: The two components the journal prints beside the total.
COMPONENTS: tuple[str, ...] = ("USDT", "USDC")


class DefiLlama:
    """The DefiLlama source."""

    source = SOURCE
    series = ("stablecoin_mcap", "defi_tvl")

    async def fetch(self, client: Client, ctx: FetchContext) -> Fetched:
        out: list[RawBatch] = []
        faults: list[str] = []
        for kind, url in (("stablecoins", STABLECOINS_URL), ("tvl", TVL_URL)):
            try:
                out.append(batch(SOURCE, kind, ctx, await client.get_json(url)))
            except FetchRefused as exc:
                faults.append(f"{kind}: {exc}")
        if not out:
            raise FetchRefused("DefiLlama served nothing: " + group_faults(faults))
        return Fetched(batches=out, faults=faults)

    def normalize(self, batches: Sequence[RawBatch], ctx: FetchContext) -> list[Observation]:
        out: list[Observation] = []
        for raw in batches:
            if str(raw.kind) == "stablecoins":
                out.extend(normalize_stablecoins(raw.payload, ctx.now))
            elif str(raw.kind) == "tvl":
                out.extend(normalize_tvl(raw.payload, ctx.now))
        return out


def normalize_stablecoins(payload: Any, now: datetime) -> list[Observation]:
    """Total pegged-USD circulating supply, with USDT and USDC beside it."""
    assets = (payload or {}).get("peggedAssets")
    if not assets:
        raise FetchRefused("DefiLlama's stablecoins response carries no pegged assets")
    total = Decimal(0)
    split: dict[str, Decimal] = {}
    for asset in assets:
        circulating = (asset.get("circulating") or {}).get("peggedUSD")
        if circulating is None:
            continue
        value = dec(circulating)
        total += value
        symbol = str(asset.get("symbol") or "")
        if symbol in COMPONENTS:
            split[symbol] = value
    if total == 0:
        raise FetchRefused(
            "DefiLlama reported zero total stablecoin supply, which is not a reading"
        )
    return [
        Observation(
            series_key="stablecoin_mcap",
            asof=now.date(),
            value=total,
            display=f"${total / Decimal(10) ** 9:,.2f}B",
            tier=1,
            source="DefiLlama stablecoins API",
            observed_at=now,
            extra={symbol: str(split.get(symbol)) for symbol in COMPONENTS if symbol in split},
        )
    ]


def normalize_tvl(payload: Any, now: datetime) -> list[Observation]:
    """Every dated point of the chain-TVL history in the last `REPLAY_DAYS`.

    The response is the whole history, so re-stating the recent part costs
    nothing and a day a run missed fills itself on the next one. The newest
    point is the running UTC day, which DefiLlama keeps updating until the day
    ends; the older ones are settled.
    """
    points = payload or []
    if not isinstance(points, list) or not points:
        raise FetchRefused("DefiLlama's historicalChainTvl response carries no points")
    by_day: dict[date, Decimal] = {}
    for point in sorted(points, key=lambda one: int(one["date"])):
        by_day[datetime.fromtimestamp(int(point["date"]), tz=UTC).date()] = dec(point["tvl"])
    since = max(by_day) - timedelta(days=REPLAY_DAYS)
    return [
        Observation(
            series_key="defi_tvl",
            asof=day,
            value=value,
            display=f"${value / Decimal(10) ** 9:,.2f}B",
            tier=1,
            source="DefiLlama historicalChainTvl",
            observed_at=now,
        )
        for day, value in sorted(by_day.items())
        if day >= since
    ]
