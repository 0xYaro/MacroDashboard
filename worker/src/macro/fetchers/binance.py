"""Binance USD-M futures: the funding skew and the funding extremes.

Ported from `fetch_data.py`'s `fetch_funding`, with one change forced by the
registry: `funding_extremes` is a `numeric` series here, not a prose one.

`config/series.yaml` reserves `value_kind: text` for exactly the five Macro and
Fiscal State rows, so that a Tier 4 failure and a prose reading are never the
same shape. `funding_extremes` therefore stores the single most negative
eight-hour funding rate, which is the tail the row exists to watch, and keeps
both tails in `extra` so the journal's sentence can still be rendered from it.

This endpoint is read-only and carries no credential. There is no order path in
this system and this module is not one.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

from ..http import Client, FetchRefused
from ..model import Observation, RawBatch
from ..units import dec
from .base import FetchContext, Fetched, batch, one

SOURCE = "binance_futures"

URL = "https://fapi.binance.com/fapi/v1/premiumIndex"

MAJORS: tuple[str, ...] = ("BTC", "ETH", "SOL")

#: How many symbols each tail of the funding distribution carries into `extra`.
#: Two, matching what the journal prints.
TAIL = 2


class Binance:
    """The Binance funding source."""

    source = SOURCE
    series = ("funding_skew", "funding_extremes")

    async def fetch(self, client: Client, ctx: FetchContext) -> Fetched:
        return one(batch(SOURCE, "premium_index", ctx, await client.get_json(URL)))

    def normalize(self, batches: Sequence[RawBatch], ctx: FetchContext) -> list[Observation]:
        out: list[Observation] = []
        for raw in batches:
            out.extend(normalize_premium_index(raw.payload, ctx.now))
        return out


def normalize_premium_index(payload: Any, now: datetime) -> list[Observation]:
    """Every USDT-quoted perpetual's last funding rate, into two series.

    The rate is served as a fraction and stored as a percent per eight hours,
    which is the unit every funding figure in the journal is written in.
    """
    rows = payload or []
    if not isinstance(rows, list):
        raise FetchRefused("Binance's premiumIndex response is not a list")
    rates: list[tuple[str, Decimal]] = []
    for row in rows:
        symbol = str(row.get("symbol") or "")
        raw_rate = row.get("lastFundingRate")
        if not symbol.endswith("USDT") or raw_rate in (None, ""):
            continue
        rates.append((symbol, dec(raw_rate) * Decimal(100)))
    if not rates:
        raise FetchRefused("Binance's premiumIndex response carries no USDT perpetuals")

    rates.sort(key=lambda pair: pair[1])
    by_symbol = dict(rates)
    median = _median([rate for _, rate in rates])
    asof = now.date()

    most_negative = rates[:TAIL]
    most_positive = list(reversed(rates[-TAIL:]))

    skew = Observation(
        series_key="funding_skew",
        asof=asof,
        value=median,
        display=f"Median {median:+.4f}%/8h",
        tier=1,
        source="Binance Futures API",
        observed_at=now,
        extra={
            "n_perps": len(rates),
            **{
                major: _maybe_str(by_symbol.get(f"{major}USDT"))
                for major in MAJORS
            },
        },
    )

    worst = most_negative[0][1]
    extremes = Observation(
        series_key="funding_extremes",
        asof=asof,
        value=worst,
        display=_extremes_display(most_negative, most_positive),
        tier=1,
        source="Binance Futures API",
        observed_at=now,
        extra={
            "most_negative": [[symbol, str(rate)] for symbol, rate in most_negative],
            "most_positive": [[symbol, str(rate)] for symbol, rate in most_positive],
            "n_perps": len(rates),
            "measures": "the single most negative 8-hour funding rate across all listed perpetuals",
        },
    )
    return [skew, extremes]


def _median(values: Sequence[Decimal]) -> Decimal:
    """The median, kept in `Decimal` throughout.

    `statistics.median` averages the middle pair on an even count, and averaging
    two `Decimal`s stays exact, so the value never touches a binary float.
    """
    return statistics.median(values)


def _extremes_display(
    negative: Sequence[tuple[str, Decimal]], positive: Sequence[tuple[str, Decimal]]
) -> str:
    left = "; ".join(f"{symbol} {rate:+.3f}%" for symbol, rate in negative)
    right = "; ".join(f"{symbol} {rate:+.3f}%" for symbol, rate in positive)
    return f"{left} | {right}"


def _maybe_str(value: Decimal | None) -> str | None:
    return None if value is None else str(value)
