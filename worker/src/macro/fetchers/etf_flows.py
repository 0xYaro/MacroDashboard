"""US spot BTC and ETH ETF net flows, one reading per US session, and what follows from them.

Six series from one source, the way breadth is one source: the daily net flow
is read, and the week-to-date flow and the inflow streak are computed from it
here rather than searched for. Until this module existed all four of the older
series were an LLM search agent's job on the 09:30 run only, so the S1 band on
`btc_etf_weekly_flow` and the crypto phase condition on the streak stood on a
number a model had found in a news flash.

## SoSoValue is the reading, Farside is the check

SoSoValue's `historicalInflowChart` takes one POST per product
(`us-btc-spot`, `us-eth-spot`), needs no key, and answers the whole daily
history it holds, about 300 sessions, newest first: `date` and
`totalNetInflow` in raw USD. Measured on 2026-09-24: BTC for 2026-09-22 was
714,748,985.505, and no NYSE session in the 300 was missing.

Farside publishes the same flows per fund, in US$ millions to one decimal, as
an HTML table (`bitcoin-etf-flow-all-data`, `ethereum-etf-flow-all-data`)
behind a Cloudflare challenge that only a browser's handshake passes, so it is
read through `Client.get_as_browser`. Negatives are in parentheses. A `-`
cell is a fund that has not reported yet, or a fund that has not launched yet.

A session is Tier 1 when both carry it and they agree within `AGREE_SHARE` of
SoSoValue's figure or within `AGREE_USD`, whichever is wider. On the 300
sessions of 2026-09-24 they agreed on all but two BTC sessions, 8 and 10 April
2026, by $30.6M and $16.3M. A session they disagree on is Tier 2 with an
`etf_flow_divergence` finding. A session only SoSoValue carries is Tier 2,
a single authoritative source. A session only Farside carries (SoSoValue
unreadable, or not yet updated) is Tier 2 from Farside's total and says so.

## A missing session is missing, never zero

A session enters only when a source carries it. Farside's row for a session
counts only when every fund that has already reported on an earlier session
reports on this one too: the row for a session still in progress carries `-`
in every fund cell and `0.0` in its Total, and that zero is not a reading.
A day that is not an NYSE session is dropped from both sources.

## Week to date and the streak

`*_etf_weekly_flow` on session `d` is the sum of every session of `d`'s ISO
week up to and including `d`, which is what the journal and the scenario band
`btc_etf_weekly_flow lt 700000000` have always meant by it. A week with a
session missing has no week-to-date figure until that session arrives.

`*_etf_inflow_streak` on session `d` is the signed length of the run of
same-signed sessions ending at `d`: `+4` is four inflow sessions in a row, `-2`
two outflow sessions, and `0` only a session whose net flow was exactly zero.
The run is walked back over NYSE sessions, so a holiday never breaks it, and a
session missing inside the run makes the streak unknown rather than short.

Each derived reading's tier is the worst tier among the sessions it stands on.
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from .. import clock
from ..faults import group_faults
from ..http import Client, FetchRefused
from ..model import Finding, Observation, RawBatch
from ..units import dec
from .base import FetchContext, Fetched

SOURCE = "etf_flows"

#: The two products, in the order the snapshot lists them.
ASSETS: tuple[str, ...] = ("btc", "eth")

SOSOVALUE_URL = "https://api.sosovalue.xyz/openapi/v2/etf/historicalInflowChart"
SOSOVALUE_TYPE: dict[str, str] = {"btc": "us-btc-spot", "eth": "us-eth-spot"}

FARSIDE_URL: dict[str, str] = {
    "btc": "https://farside.co.uk/bitcoin-etf-flow-all-data/",
    "eth": "https://farside.co.uk/ethereum-etf-flow-all-data/",
}

#: How far back a scheduled run re-states. The current and the previous ISO
#: week whole, so a session either source revised is re-stated with the week
#: and the streak that stand on it. The backfill writes everything instead.
REPLAY_DAYS = 14

#: The two sources agree when they are this close, as a share of SoSoValue's
#: figure or in dollars, whichever is wider. Farside rounds to $0.1M per fund,
#: and a quiet session's flows are small enough for rounding to be most of it.
AGREE_SHARE = Decimal("0.05")
AGREE_USD = Decimal(10_000_000)

_MILLION = Decimal(1_000_000)
_MILL = Decimal("0.001")
_BILLION = Decimal(1_000_000_000)

#: The checks `review` raises. Evaluated on every run that fetches the flows.
CHECK_KEYS: tuple[str, ...] = (
    "etf_flow_divergence",
    "etf_flow_fallback",
    "etf_flow_crosscheck_unreadable",
)

_TABLE = re.compile(r"<table\b.*?</table>", re.DOTALL | re.IGNORECASE)
_ROW = re.compile(r"<tr\b.*?</tr>", re.DOTALL | re.IGNORECASE)
_CELL = re.compile(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", re.DOTALL | re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")
_TICKER = re.compile(r"^[A-Z][A-Z0-9]{1,5}$")
#: `714.7`, `1,119.9`, `(528.3)`, `0.0`. A bare `-` is not a number.
_NUMBER = re.compile(r"^\(?-?\d[\d,]*(?:\.\d+)?\)?$")


def series_key(asset: str, kind: str) -> str:
    """`btc_etf_net_flow`, `eth_etf_weekly_flow`, `btc_etf_inflow_streak`."""
    return f"{asset}_etf_{kind}"


@dataclass(frozen=True, slots=True)
class Session:
    """One asset's net flow for one US session, with where it came from."""

    asof: date
    value: Decimal
    tier: int
    source: str
    extra: dict[str, Any] = field(default_factory=dict)
    #: Tier 2 only because one source was missing on this run, not because the
    #: two disagreed. Such a session does not replace one a past run verified.
    provisional: bool = False


class EtfFlows:
    """The spot ETF flow source: SoSoValue, checked against Farside."""

    source = SOURCE
    series = tuple(
        series_key(asset, kind)
        for asset in ASSETS
        for kind in ("net_flow", "weekly_flow", "inflow_streak")
    )

    async def fetch(self, client: Client, ctx: FetchContext) -> Fetched:
        return await fetch_all(client, ctx.now)

    def normalize(self, batches: Sequence[RawBatch], ctx: FetchContext) -> list[Observation]:
        since = ctx.now.date() - timedelta(days=REPLAY_DAYS)
        out = [one for one in normalize(batches, ctx.now) if one.asof >= since]
        if not out:
            raise FetchRefused(
                f"neither SoSoValue nor Farside carried a settled session since {since}"
            )
        return out

    def review(self, observations: Sequence[Observation], _ctx: FetchContext) -> list[Finding]:
        return review(observations)


# ---------------------------------------------------------------------------
# The network half
# ---------------------------------------------------------------------------


async def fetch_all(client: Client, now: datetime) -> Fetched:
    """Both sources for both products. Raises only when nothing was read at all.

    Farside's page is reduced to its flow table's cell text before it is kept:
    the table is every figure the page carries, and the page around it is
    800KB of markup a day that would add nothing to `macro_raw.batch`.
    """
    out: list[RawBatch] = []
    faults: list[str] = []
    for asset in ASSETS:
        try:
            payload = await client.query_json(SOSOVALUE_URL, {"type": SOSOVALUE_TYPE[asset]})
            out.append(RawBatch(SOURCE, f"sosovalue_{asset}", now, payload))
        except FetchRefused as exc:
            faults.append(f"SoSoValue {asset.upper()}: {exc}")
        try:
            page = await client.get_as_browser(FARSIDE_URL[asset])
            out.append(RawBatch(SOURCE, f"farside_{asset}", now, table_cells(page.text)))
        except FetchRefused as exc:
            # The cross-check failing costs the cross-check and nothing else.
            faults.append(f"Farside {asset.upper()}: {exc}")
    if not out:
        raise FetchRefused("ETF flows read nothing: " + group_faults(faults))
    return Fetched(batches=out, faults=faults)


def table_cells(page: str) -> list[list[str]]:
    """The first table on a Farside page, as the text of each cell, row by row."""
    found = _TABLE.search(page)
    if found is None:
        raise FetchRefused(
            "Farside's page carries no table, so the Cloudflare check or the page shape changed"
        )
    return [
        [html.unescape(_TAG.sub("", cell)).strip() for cell in _CELL.findall(row)]
        for row in _ROW.findall(found.group(0))
    ]


# ---------------------------------------------------------------------------
# The two readers
# ---------------------------------------------------------------------------


def read_sosovalue(payload: Any) -> dict[date, Decimal]:
    """SoSoValue's daily totals by session, in USD. Non-sessions are dropped."""
    if not isinstance(payload, Mapping) or payload.get("code") != 0:
        raise FetchRefused(
            "SoSoValue's historicalInflowChart did not answer code 0, so its data is not a reading"
        )
    points = payload.get("data")
    if not isinstance(points, list) or not points:
        raise FetchRefused("SoSoValue's historicalInflowChart answered no data points")
    out: dict[date, Decimal] = {}
    for point in points:
        raw = point.get("totalNetInflow") if isinstance(point, Mapping) else None
        if raw is None:
            continue
        try:
            day = date.fromisoformat(str(point["date"]))
        except (KeyError, ValueError):
            continue
        if clock.is_us_session(day):
            # JSON floats: `-20448299.535` can arrive as `...53499999`. SoSoValue
            # states thousandths of a dollar and nothing finer.
            out[day] = dec(raw).quantize(_MILL)
    if not out:
        raise FetchRefused("SoSoValue's historicalInflowChart carried no US session")
    return out


@dataclass(frozen=True, slots=True)
class FarsideDay:
    """One complete Farside row: every reporting fund numeric."""

    total: Decimal
    funds: int


def read_farside(rows: Sequence[Sequence[str]]) -> dict[date, FarsideDay]:
    """Farside's complete session rows, by session, with the Total in USD.

    Rows are walked oldest first so that a fund's first number marks it
    launched. From then on a `-` in its cell means it has not reported for
    that session, and the whole row is left out: its Total is the sum of the
    funds that have, which is a smaller number and not the session's flow.
    """
    header = _header(rows)
    width = len(header)
    dated: list[tuple[date, Sequence[str]]] = []
    for row in rows:
        if len(row) != width or not row:
            continue
        day = _farside_date(row[0])
        if day is not None:
            dated.append((day, row))
    if not dated:
        raise FetchRefused("Farside's flow table carries no dated row")

    launched: set[int] = set()
    out: dict[date, FarsideDay] = {}
    for day, row in sorted(dated, key=lambda one: one[0]):
        cells = row[1 : width - 1]
        numeric = {index for index, cell in enumerate(cells) if _NUMBER.match(cell)}
        complete = launched <= numeric and bool(numeric)
        launched |= numeric
        total = row[width - 1]
        if not complete or not _NUMBER.match(total) or not clock.is_us_session(day):
            continue
        out[day] = FarsideDay(total=_farside_number(total) * _MILLION, funds=len(numeric))
    return out


def _header(rows: Sequence[Sequence[str]]) -> Sequence[str]:
    """The row naming the funds: tickers between a blank or `Date` and `Total`."""
    for row in rows:
        middle = row[1:-1]
        if len(middle) >= 3 and all(_TICKER.match(cell) for cell in middle):
            return row
    raise FetchRefused(
        "Farside's flow table has no row of fund tickers, so the page shape this reads changed"
    )


def _farside_date(text: str) -> date | None:
    try:
        return datetime.strptime(text, "%d %b %Y").date()
    except ValueError:
        return None


def _farside_number(text: str) -> Decimal:
    negative = text.startswith("(") and text.endswith(")")
    value = dec(text.strip("()").replace(",", ""))
    return -value if negative else value


# ---------------------------------------------------------------------------
# Combining and deriving: pure
# ---------------------------------------------------------------------------


def combine(
    sosovalue: Mapping[date, Decimal] | None,
    farside: Mapping[date, FarsideDay] | None,
    *,
    farside_fault: str | None = None,
) -> dict[date, Session]:
    """Every session either source carries, tiered by whether they agree."""
    soso = dict(sosovalue or {})
    far = dict(farside or {})
    out: dict[date, Session] = {}
    for day in sorted(set(soso) | set(far)):
        primary = soso.get(day)
        check = far.get(day)
        if primary is None:
            assert check is not None
            out[day] = Session(
                asof=day,
                value=check.total,
                tier=2,
                source="Farside Investors (SoSoValue did not carry this session)",
                extra={"farside_total": str(check.total), "fallback": True},
                provisional=True,
            )
            continue
        extra: dict[str, Any] = {"sosovalue_total": str(primary)}
        if check is None:
            extra["crosscheck"] = (
                f"Farside unreadable: {farside_fault}" if farside_fault else
                "Farside has not published this session complete"
            )
            out[day] = Session(day, primary, 2, "SoSoValue", extra, provisional=True)
            continue
        gap = abs(primary - check.total)
        allowed = max(abs(primary) * AGREE_SHARE, AGREE_USD)
        extra["farside_total"] = str(check.total)
        extra["divergence"] = str(primary - check.total)
        if gap <= allowed:
            extra["crosscheck"] = "agrees with Farside"
            out[day] = Session(day, primary, 1, "SoSoValue, agreeing with Farside", extra)
        else:
            extra["crosscheck"] = "disagrees with Farside"
            out[day] = Session(day, primary, 2, "SoSoValue, disagreeing with Farside", extra)
    return out


def week_to_date(sessions: Mapping[date, Session], day: date) -> tuple[Decimal, int, int] | None:
    """Sum, session count and worst tier of `day`'s ISO week up to `day`.

    `None` when any session of that span is missing: a partial sum would read
    as a small week rather than an unknown one.
    """
    monday = day - timedelta(days=day.weekday())
    span = clock.us_sessions_between(monday, day)
    if not span or any(one not in sessions for one in span):
        return None
    return (
        sum((sessions[one].value for one in span), Decimal(0)),
        len(span),
        max(sessions[one].tier for one in span),
    )


def streak(sessions: Mapping[date, Session], day: date) -> tuple[int, int] | None:
    """The signed run length ending at `day`, and the worst tier it stands on.

    Walked back over NYSE sessions, so a holiday is not a break. `None` when a
    session inside the run is missing, or when the run reaches back past the
    oldest session carried, because either way its length is not known.
    """
    today = sessions.get(day)
    if today is None:
        return None
    sign = _sign(today.value)
    if sign == 0:
        return 0, today.tier
    oldest = min(sessions)
    length = 1
    worst = today.tier
    cursor = day
    while True:
        cursor = clock.previous_us_session(cursor)
        if cursor < oldest:
            return None
        earlier = sessions.get(cursor)
        if earlier is None:
            return None
        worst = max(worst, earlier.tier)
        if _sign(earlier.value) != sign:
            return sign * length, worst
        length += 1


def _only_provisional(sessions: Mapping[date, Session], days: Iterable[date]) -> bool:
    """Whether a reading over `days` is below Tier 1 only for want of a cross-check.

    True when some session it stands on is below Tier 1 and every such session
    is provisional. A disagreement is evidence and always re-states.
    """
    lowered = [sessions[one] for one in days if one in sessions and sessions[one].tier > 1]
    return bool(lowered) and all(one.provisional for one in lowered)


def _run_days(day: date, length: int) -> list[date]:
    """The sessions a streak of `length` ending at `day` stands on."""
    out = [day]
    while len(out) < max(abs(length), 1):
        out.append(clock.previous_us_session(out[-1]))
    return out


def _sign(value: Decimal) -> int:
    return (value > 0) - (value < 0)


def observations(
    asset: str, sessions: Mapping[date, Session], *, days: Iterable[date] | None = None
) -> list[Observation]:
    """The three rows per session for one asset, for `days` or for every session."""
    out: list[Observation] = []
    label = asset.upper()
    for day in sorted(sessions if days is None else days):
        one = sessions.get(day)
        if one is None:
            continue
        closed = clock.us_session_close_utc(day)
        out.append(
            Observation(
                series_key=series_key(asset, "net_flow"),
                asof=day,
                value=one.value,
                display=money(one.value),
                tier=one.tier,
                source=one.source,
                observed_at=closed,
                extra=dict(one.extra),
                defer_to_better_tier=one.provisional,
            )
        )
        week = week_to_date(sessions, day)
        if week is not None:
            total, count, tier = week
            out.append(
                Observation(
                    series_key=series_key(asset, "weekly_flow"),
                    asof=day,
                    value=total,
                    display=(
                        f"{money(total)} week to date "
                        f"({count} session{'s' if count != 1 else ''} of {clock.iso_week(day)})"
                    ),
                    tier=tier,
                    source=f"Computed from {label} ETF daily net flows (SoSoValue, Farside)",
                    observed_at=closed,
                    extra={"week": clock.iso_week(day), "sessions": count},
                    defer_to_better_tier=_only_provisional(
                        sessions,
                        clock.us_sessions_between(day - timedelta(days=day.weekday()), day),
                    ),
                )
            )
        run = streak(sessions, day)
        if run is not None:
            length, tier = run
            out.append(
                Observation(
                    series_key=series_key(asset, "inflow_streak"),
                    asof=day,
                    value=Decimal(length),
                    display=streak_display(length),
                    tier=tier,
                    source=f"Computed from {label} ETF daily net flows (SoSoValue, Farside)",
                    observed_at=closed,
                    defer_to_better_tier=_only_provisional(sessions, _run_days(day, length)),
                )
            )
    return out


def normalize(batches: Sequence[RawBatch], now: datetime) -> list[Observation]:
    """Every row both products support, settled at `now`, oldest first.

    A session whose close is still ahead of `now` is not stored, whatever a
    source says about it.
    """
    soso: dict[str, dict[date, Decimal]] = {}
    far: dict[str, dict[date, FarsideDay]] = {}
    refused: dict[str, str] = {}
    for raw in batches:
        kind, _, asset = str(raw.kind).partition("_")
        try:
            if kind == "sosovalue":
                soso[asset] = read_sosovalue(raw.payload)
            elif kind == "farside":
                far[asset] = read_farside(raw.payload)
        except FetchRefused as exc:
            refused[f"{kind}_{asset}"] = str(exc)

    out: list[Observation] = []
    for asset in ASSETS:
        if asset not in soso and asset not in far:
            continue
        sessions = {
            day: one
            for day, one in combine(
                soso.get(asset),
                far.get(asset),
                farside_fault=(
                    refused.get(f"farside_{asset}", "the page could not be fetched")
                    if asset not in far
                    else None
                ),
            ).items()
            if clock.us_session_close_utc(day) <= now
        }
        # The backfill's range: what SoSoValue serves, with Farside's older
        # sessions used only to settle a streak that began before it.
        first = min(soso[asset]) if asset in soso else min(sessions, default=None)
        if first is None:
            continue
        out.extend(observations(asset, sessions, days=[d for d in sessions if d >= first]))
    return out


def review(observations: Sequence[Observation]) -> list[Finding]:
    """A finding for each session the two sources disagree on, and for a fallback.

    And one per product when Farside could not be read at all. Every session
    then stands on SoSoValue alone, and the run is not complete even though it
    delivered every series: without the finding the only trace is a tier that
    quietly dropped, and a stored Tier 1 session is kept rather than demoted,
    which leaves no trace at all.
    """
    out: list[Finding] = []
    unchecked: dict[str, list[Observation]] = {}
    for one in observations:
        if not one.series_key.endswith("_etf_net_flow"):
            continue
        if str(one.extra.get("crosscheck", "")).startswith("Farside unreadable"):
            unchecked.setdefault(one.series_key, []).append(one)
        if one.extra.get("crosscheck") == "disagrees with Farside":
            out.append(
                Finding(
                    check_key="etf_flow_divergence",
                    subject=f"{one.series_key} {one.asof.isoformat()}",
                    sentence=(
                        f"{one.series_key} for {one.asof} reads {money(one.value)} from SoSoValue "
                        f"and {money(dec(one.extra['farside_total']))} from Farside, further "
                        "apart than 5% or $10M. SoSoValue's figure is stored at Tier 2, and the "
                        "week and streak that stand on it are Tier 2 with it."
                    ),
                    severity=2,
                )
            )
        if one.extra.get("fallback"):
            out.append(
                Finding(
                    check_key="etf_flow_fallback",
                    subject=f"{one.series_key} {one.asof.isoformat()}",
                    sentence=(
                        f"{one.series_key} for {one.asof} is Farside's total, not SoSoValue's, "
                        "because SoSoValue did not carry that session on this run. It is Tier 2 "
                        "and is re-stated from SoSoValue when SoSoValue carries it."
                    ),
                    severity=3,
                )
            )
    for key, rows in unchecked.items():
        why = str(rows[-1].extra["crosscheck"]).removeprefix("Farside unreadable: ")
        out.append(
            Finding(
                check_key="etf_flow_crosscheck_unreadable",
                subject=key,
                sentence=(
                    f"Farside could not be read on this run ({why}), so the {len(rows)} "
                    f"{key} session{'s' if len(rows) != 1 else ''} it re-stated stand on "
                    "SoSoValue alone at Tier 2. A session already stored at Tier 1 keeps its "
                    "verified row; a new one is Tier 2 until Farside is read again."
                ),
                severity=2,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------


def money(value: Decimal | None) -> str:
    """`+$714.7M`, `-$46.6M`, `+$1.71B`, `$0`: the way the journal prints a flow."""
    if value is None:
        return ""
    if value == 0:
        return "$0"
    sign = "+" if value > 0 else "-"
    size = abs(value)
    if size >= _BILLION:
        return f"{sign}${size / _BILLION:,.2f}B"
    return f"{sign}${size / _MILLION:,.1f}M"


def streak_display(length: int) -> str:
    """`+4 sessions`, `-1 session`, `0 (a flat session)`."""
    if length == 0:
        return "0 (a flat session)"
    sessions = "session" if abs(length) == 1 else "sessions"
    return f"{length:+d} {sessions}"
