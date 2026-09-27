"""The US Treasury daily par yield curve.

Ported from `fetch_data.py`'s `fetch_treasury`, including the 2s10s spread it
hangs on the ten-year's `extra`.

The par curve is a Treasury publication on federal business days, not an equity
session series. It prints on Good Friday, when the NYSE is shut, and not on
Columbus Day or Veterans Day, when the NYSE trades. `config/series.yaml`
declares `us_business_day` for exactly that reason.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from ..http import Client, FetchRefused
from ..model import Observation, RawBatch
from ..units import dec
from .base import FetchContext, Fetched, batch

SOURCE = "treasury_par_curve"

URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
    "daily-treasury-rates.csv/{year}/all?type=daily_treasury_yield_curve"
    "&field_tdr_date_value={year}&page&_format=csv"
)

#: Series key to the column heading the CSV uses. The headings are the vendor's
#: and they stop here.
COLUMNS: dict[str, str] = {
    "ust_30y": "30 Yr",
    "ust_10y": "10 Yr",
    "ust_2y": "2 Yr",
}

#: How far back each run re-states the curve. The same window as Yahoo's.
REPLAY_DAYS = 45


class Treasury:
    """The par curve source."""

    source = SOURCE
    series = tuple(COLUMNS)

    async def fetch(self, client: Client, ctx: FetchContext) -> Fetched:
        # Two calendar years, so a run in the first week of January still has
        # the prior December to measure a change against.
        years = sorted({ctx.now.year, (ctx.now.date().replace(month=1, day=1)).year - 1})
        out: list[RawBatch] = []
        faults: list[str] = []
        for year in years:
            try:
                reply = await client.get(URL.format(year=year))
            except FetchRefused as exc:
                if year != ctx.now.year:
                    # Last year's file is a convenience, not a requirement, and
                    # the three series are all read from this year's. It is still
                    # recorded: a source that half worked says so.
                    faults.append(f"{year}: {exc}")
                    continue
                raise
            out.append(batch(SOURCE, f"csv:{year}", ctx, reply.text))
        return Fetched(batches=out, faults=faults)

    def normalize(self, batches: Sequence[RawBatch], ctx: FetchContext) -> list[Observation]:
        """Every published curve of the last `REPLAY_DAYS`, oldest first.

        The CSV is the whole year, so re-stating the recent part costs nothing
        and a business day a run missed fills itself on the next one. Every
        write is an upsert on `(series_id, asof)`, so a curve already stored is
        unchanged.
        """
        rows = _rows(batches)
        if not rows:
            return []
        since = max(rows) - timedelta(days=REPLAY_DAYS)
        out: list[Observation] = []
        for day in sorted(rows):
            if day >= since:
                out.extend(_observations(rows[day], day, ctx.now))
        return out


def _rows(batches: Sequence[RawBatch]) -> dict[date, dict[str, str]]:
    table: dict[date, dict[str, str]] = {}
    for raw in batches:
        for record in csv.DictReader(io.StringIO(str(raw.payload))):
            raw_date = (record.get("Date") or "").strip()
            try:
                day = datetime.strptime(raw_date, "%m/%d/%Y").replace(tzinfo=UTC).date()
            except ValueError:
                continue
            table[day] = record
    return table


def _observations(record: dict[str, str], asof: date, now: datetime) -> list[Observation]:
    values: dict[str, Decimal] = {}
    out: list[Observation] = []
    for key, column in COLUMNS.items():
        cell = (record.get(column) or "").strip()
        if not cell:
            # A tenor the Treasury did not publish that day. Null, never zero:
            # a zero par yield is a statement, and an absent one is not.
            out.append(
                Observation(
                    series_key=key,
                    asof=asof,
                    value=None,
                    display="",
                    tier=4,
                    source="US Treasury daily par curve",
                    observed_at=now,
                    note=f"the par curve for {asof} carries no {column} tenor",
                )
            )
            continue
        value = dec(cell)
        values[key] = value
        out.append(
            Observation(
                series_key=key,
                asof=asof,
                value=value,
                display=f"{value:.2f}%",
                tier=1,
                source="US Treasury daily par curve",
                observed_at=now,
                extra={"tenor": column},
            )
        )

    if "ust_10y" in values and "ust_2y" in values:
        spread = (values["ust_10y"] - values["ust_2y"]) * Decimal(100)
        out = [
            (
                Observation(
                    series_key=one.series_key,
                    asof=one.asof,
                    value=one.value,
                    display=one.display,
                    tier=one.tier,
                    source=one.source,
                    observed_at=one.observed_at,
                    released=one.released,
                    note=one.note,
                    extra={**one.extra, "spread_2s10s_bps": str(spread)},
                )
                if one.series_key == "ust_10y"
                else one
            )
            for one in out
        ]
    return out


def latest_curve(batches: Sequence[RawBatch]) -> tuple[date, dict[str, Any]] | None:
    """The newest dated row across every captured CSV. Exposed for the tests."""
    rows = _rows(batches)
    if not rows:
        return None
    day = max(rows)
    return day, rows[day]
