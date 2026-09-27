"""Alternative.me's Crypto Fear and Greed index.

Ported from `fetch_data.py`'s `fetch_crypto_fng`. The index publishes once a day
on a UTC clock, and the payload dates each reading itself, so the `asof` is the
vendor's timestamp and never the run's own date.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any

from ..http import Client, FetchRefused
from ..model import Observation, RawBatch
from ..units import dec
from .base import FetchContext, Fetched, batch, one

SOURCE = "alternative_me"
SERIES_KEY = "crypto_fear_greed"

URL = "https://api.alternative.me/fng/?limit=30"


class AlternativeMe:
    """The Crypto Fear and Greed source."""

    source = SOURCE
    series = (SERIES_KEY,)

    async def fetch(self, client: Client, ctx: FetchContext) -> Fetched:
        return one(batch(SOURCE, "fng", ctx, await client.get_json(URL)))

    def normalize(self, batches: Sequence[RawBatch], ctx: FetchContext) -> list[Observation]:
        out: list[Observation] = []
        for raw in batches:
            out.extend(normalize_fng(raw.payload, ctx.now))
        return out


def normalize_fng(payload: Any, now: datetime) -> list[Observation]:
    """Every dated reading the response carries, oldest first.

    The request already asks for thirty days, and the index is published once
    a day and never revised, so re-stating them costs nothing and a day a run
    missed fills itself on the next one. The classification travels in
    `display`, not in `value`.
    """
    rows = (payload or {}).get("data")
    if not rows:
        raise FetchRefused("Alternative.me served no Fear and Greed readings")
    by_day: dict[date, Observation] = {}
    for row in rows:
        value = dec(row["value"])
        classification = str(row.get("value_classification") or "").strip()
        asof = datetime.fromtimestamp(int(row["timestamp"]), tz=UTC).date()
        by_day[asof] = Observation(
            series_key=SERIES_KEY,
            asof=asof,
            value=value,
            display=f"{value:.0f} ({classification})" if classification else f"{value:.0f}",
            tier=1,
            source="Alternative.me API",
            observed_at=now,
            extra={"classification": classification},
        )
    return [by_day[day] for day in sorted(by_day)]
