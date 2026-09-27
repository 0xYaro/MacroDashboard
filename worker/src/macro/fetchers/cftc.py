"""CFTC Traders in Financial Futures: asset managers and leveraged funds on the ES.

Ported from `fetch_data.py`'s `fetch_cftc` and its `cftc_band`, which come from
the vault's own `cftc-fund-positioning-and-naaim-reference.md`. The band
thresholds are asserted reference values and are not re-derived here: they were
fitted once against history and re-fitting them per run would move the
goalposts under every tracker condition written against them.

Positions are dated Tuesday and published the following Friday afternoon, which
is what `weekly; asof=tue; release=fri` in `config/series.yaml` declares, so a
Thursday reading of Tuesday's positions is `due`, not `overdue`.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from ..http import Client, FetchRefused
from ..model import Observation, RawBatch
from ..units import dec
from .base import FetchContext, Fetched, batch, one

SOURCE = "cftc_tff"

URL = (
    "https://publicreportinghub.cftc.gov/resource/gpe5-46if.json"
    "?market_and_exchange_names=E-MINI%20S%26P%20500%20-%20CHICAGO%20MERCANTILE%20EXCHANGE"
    "&$order=report_date_as_yyyy_mm_dd%20DESC&$limit=8"
)

#: Series key to the vendor's column prefix and to which band table applies.
COHORTS: tuple[tuple[str, str, Literal["asset_manager", "leveraged_fund"]], ...] = (
    ("cftc_asset_managers_es", "asset_mgr", "asset_manager"),
    ("cftc_leveraged_funds_es", "lev_money", "leveraged_fund"),
)


class Cftc:
    """The CFTC TFF source."""

    source = SOURCE
    series = tuple(key for key, _, _ in COHORTS)

    async def fetch(self, client: Client, ctx: FetchContext) -> Fetched:
        return one(batch(SOURCE, "tff", ctx, await client.get_json(URL)))

    def normalize(self, batches: Sequence[RawBatch], ctx: FetchContext) -> list[Observation]:
        out: list[Observation] = []
        for raw in batches:
            out.extend(normalize_tff(raw.payload, ctx.now))
        return out


def normalize_tff(payload: Any, now: datetime) -> list[Observation]:
    """The newest report, as a net position in percent of open interest."""
    reports = payload or []
    if not isinstance(reports, list) or not reports:
        raise FetchRefused("The CFTC TFF endpoint served no reports")
    latest = max(reports, key=lambda row: str(row.get("report_date_as_yyyy_mm_dd") or ""))
    asof = _report_date(latest)
    open_interest = dec(latest["open_interest_all"])
    if open_interest == 0:
        raise FetchRefused(f"The CFTC report for {asof} states zero open interest")

    out: list[Observation] = []
    for key, prefix, table in COHORTS:
        long = dec(latest[f"{prefix}_positions_long"])
        short = dec(latest[f"{prefix}_positions_short"])
        net = (long - short) / open_interest * Decimal(100)
        out.append(
            Observation(
                series_key=key,
                asof=asof,
                value=net,
                display=f"{net:+.2f}% of OI",
                tier=1,
                source="CFTC TFF API (gpe5-46if)",
                observed_at=now,
                # Tuesday positions, released the following Friday. The release
                # date is the report date plus three days by construction, not a
                # guess: the CFTC publishes Friday 15:30 New York.
                released=asof + timedelta(days=3),
                extra={
                    "band": band(table, net),
                    "open_interest_all": str(open_interest),
                    "positions_long": str(long),
                    "positions_short": str(short),
                },
            )
        )
    return out


def band(table: Literal["asset_manager", "leveraged_fund"], net_pct_of_oi: Decimal) -> str:
    """The regime band a net position sits in.

    Asserted from the vault's `cftc-fund-positioning-and-naaim-reference.md` and
    ported from `fetch_data.py` without change. The two cohorts have different
    tables because asset managers are structurally long the ES and leveraged
    funds are structurally short it, so the same number means opposite things.
    """
    if table == "asset_manager":
        if net_pct_of_oi <= Decimal("26.6"):
            return "sidelined"
        if net_pct_of_oi >= Decimal("48.8"):
            return "extreme"
        if net_pct_of_oi >= Decimal("47.8"):
            return "heavy"
        return "neutral"
    if net_pct_of_oi <= Decimal("-20.0"):
        return "sidelined"
    if net_pct_of_oi >= Decimal("-6.7"):
        return "extreme"
    if net_pct_of_oi >= Decimal("-9.9"):
        return "heavy"
    return "neutral"


def _report_date(row: dict[str, Any]) -> date:
    raw = str(row.get("report_date_as_yyyy_mm_dd") or "")
    try:
        return datetime.fromisoformat(raw).date()
    except ValueError as exc:
        raise FetchRefused(f"The CFTC report carries an unreadable date {raw!r}") from exc
