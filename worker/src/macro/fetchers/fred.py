"""FRED, over `fredgraph.csv`, and net liquidity computed from one source.

`references/data-contract.md` in the vault skill says FRED is unreachable at the
network level from this machine and routes four series through search agents
because of it. That is false: `fredgraph.csv` answers in about 110 ms, verified
against the live endpoint on 2026-09-21.

Two things this module knows that the endpoint does not tell you.

**No `cosd`.** `fredgraph.csv?id=X&cosd=YYYY-MM-DD` is served by a path that
resets the connection under repeated requests from one client, measured
repeatedly on this machine: the bare `?id=X` form answers every time and the
`cosd` form fails most of the time. The full series is a few tens of kilobytes,
so the parameter buys nothing and costs the fetch.

**Units are not in the payload.** `fredgraph.csv` carries a header row and dates
and nothing else, so each series' unit is asserted here against the FRED series
page and converted before the value reaches an `Observation`:

- `BAMLH0A0HYM2`, `BAMLC0A0CM`: percent. Stored in basis points, because every
  condition referencing them is written in basis points.
- `WALCL`, `WTREGEN`: millions of dollars.
- `RRPONTSYD`: **billions** of dollars. The odd one out, and the one that would
  quietly shrink net liquidity by about a thousandth of the RRP if it were read
  as millions like its two neighbours.

Net liquidity is `WALCL − WTREGEN − RRPONTSYD`, all three struck on the same
Wednesday. The old pipeline assembled it from a WALCL point an LLM had found, a
TGA balance from FiscalData and an RRP operation from the New York Fed, three
producers with three as-ofs. One source with one as-of replaces all of it.

## The macro prints, and the figure a clause actually refers to

`PRINTS` below is the second family this module serves: CPI, PCE, payrolls,
unemployment, JOLTS, retail sales and the retail control group. Almost every
trigger clause ever written names one of them, and none of them was a series.

**Each one stores the figure the clause refers to, not the figure FRED serves.**
A clause says "core PCE at 0.2%" and "core CPI 0.3%". Those are month-over-month
percentages, and FRED serves an index level: `PCEPILFE` reads 130.658 for July
2026. A condition written against the index level would be false for ever and
would look entirely correct. So the change is computed here and the change is
what is stored.

Two further conversions of the same kind. Payrolls is stored as the monthly
level change in thousands, because a clause says "payrolls under 125K" and never
"total nonfarm employment under 159,075". JOLTS is stored in millions, because a
clause says "JOLTS under 7.0M" while FRED serves 7,271 thousand.

**The as-of is the end of the data month.** FRED dates a monthly observation at
the first of the month it describes. `config/series.yaml` declares
`monthly; asof=eom`, so the first is mapped to the month end before the
observation is built; without that every monthly release rule would compute its
next print a month early.

ISM is not here. `NAPM` and `NAPMNOI` are discontinued and both answer 404,
verified against the live endpoint, so the two ISM rows stay on the 09:30 search
agent rather than being given a fetcher that cannot work.

## How much history each run re-states

The spreads and net liquidity re-state the last `REPLAY_DAYS` of what the CSV
already carries, like Yahoo and CNN. Storing only the newest point moved the
cursor past any day a run missed and never asked for it again, so HY and IG
held a handful of scattered points and nothing between them. Every write is an
upsert on `(series_id, asof)`, so re-stating a point already stored changes
nothing, and a point ICE later restates is overwritten with the restatement,
which is the same rule CNN's revisions follow.

The macro prints stay newest-month only, on purpose. `fredgraph.csv` serves the
current vintage, and payrolls, retail sales and PCE are revised for months
after they print. A clause that said "payrolls under 125K" was about the first
print, so re-stating an older month from today's vintage would rewrite the
figure the clause was graded against. Point-in-time for those needs ALFRED's
`realtime_start`, which is a different endpoint and is not read here.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from ..faults import group_faults
from ..http import Client, FetchRefused
from ..model import Observation, RawBatch
from ..units import maybe_dec
from .base import FetchContext, Fetched, batch

SOURCE = "fred"

URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"

Scale = Literal["percent_to_bps", "millions_to_usd", "billions_to_usd"]

#: How far back each run re-states the daily and weekly series. The same window
#: as Yahoo's and CNN's: it covers the thirty-day trend column and any day a run
#: missed, and keeps a run to a few dozen upserts per series.
REPLAY_DAYS = 45


@dataclass(frozen=True, slots=True)
class Spec:
    """One FRED series, its asserted unit, and what it becomes here."""

    fred_id: str
    series_key: str | None
    scale: Scale
    decimals: int


SPECS: tuple[Spec, ...] = (
    Spec("BAMLH0A0HYM2", "hy_oas_bps", "percent_to_bps", 0),
    Spec("BAMLC0A0CM", "ig_oas_bps", "percent_to_bps", 0),
    # The three net-liquidity components are not series of their own:
    # `config/series.yaml` carries one `net_liquidity` row and keeps the three
    # components, with their own as-ofs, in its `extra`.
    Spec("WALCL", None, "millions_to_usd", 0),
    Spec("WTREGEN", None, "millions_to_usd", 0),
    Spec("RRPONTSYD", None, "billions_to_usd", 0),
)

BY_ID: dict[str, Spec] = {spec.fred_id: spec for spec in SPECS}

_SCALES: dict[Scale, Decimal] = {
    "percent_to_bps": Decimal(100),
    "millions_to_usd": Decimal(10) ** 6,
    "billions_to_usd": Decimal(10) ** 9,
}


Measure = Literal["mom_percent", "mom_change", "level"]


@dataclass(frozen=True, slots=True)
class Print:
    """One macro print, and which figure of it a condition names.

    `fred_ids` is one series, or the five the retail control group is built
    from. `measure` decides what is stored: the month-over-month percentage,
    the month-over-month level change, or the level itself.
    """

    series_key: str
    fred_ids: tuple[str, ...]
    measure: Measure
    #: Applied after the measure, to reach the unit the clause quotes.
    factor: Decimal
    decimals: int
    suffix: str


#: `RSAFS` less motor vehicles and parts, gasoline stations, building materials
#: and food services: the Census control group, which FRED carries every
#: component of and no aggregate for.
_RETAIL_CONTROL = ("RSAFS", "RSMVPD", "RSGASS", "RSBMGESD", "RSFSDP")

PRINTS: tuple[Print, ...] = (
    Print("cpi_core_mom", ("CPILFESL",), "mom_percent", Decimal(1), 2, "%"),
    Print("cpi_headline_mom", ("CPIAUCSL",), "mom_percent", Decimal(1), 2, "%"),
    Print("pce_core_mom", ("PCEPILFE",), "mom_percent", Decimal(1), 2, "%"),
    Print("pce_headline_mom", ("PCEPI",), "mom_percent", Decimal(1), 2, "%"),
    # PAYEMS is thousands of persons, so its month-over-month change is already
    # the "+150K" a clause quotes.
    Print("payrolls_change", ("PAYEMS",), "mom_change", Decimal(1), 0, "K"),
    Print("unemployment_rate", ("UNRATE",), "level", Decimal(1), 1, "%"),
    # JTSJOL is thousands of openings and a clause quotes millions.
    Print("jolts_openings", ("JTSJOL",), "level", Decimal(1) / Decimal(1000), 3, "M"),
    Print("retail_sales_mom", ("RSAFS",), "mom_percent", Decimal(1), 2, "%"),
    Print("retail_control_mom", _RETAIL_CONTROL, "mom_percent", Decimal(1), 2, "%"),
)

PRINT_IDS: tuple[str, ...] = tuple(
    dict.fromkeys(one for spec in PRINTS for one in spec.fred_ids)
)


class Fred:
    """The FRED source: two credit spreads, net liquidity, and the macro prints."""

    source = SOURCE
    series = (
        "hy_oas_bps",
        "ig_oas_bps",
        "net_liquidity",
        *(one.series_key for one in PRINTS),
    )

    async def fetch(self, client: Client, ctx: FetchContext) -> Fetched:
        out: list[RawBatch] = []
        faults: list[str] = []
        wanted = [spec.fred_id for spec in SPECS] + list(PRINT_IDS)
        for series_id in wanted:
            try:
                reply = await client.get(URL.format(series_id=series_id))
            except FetchRefused as exc:
                # One series failing costs that series and nothing else. Seventeen
                # ids behind one source means a single discontinued one must not
                # take the credit spreads down with it. The fault travels out, so
                # the run reports an incomplete pass rather than a clean one.
                faults.append(f"{series_id}: {exc}")
                continue
            out.append(batch(SOURCE, series_id, ctx, reply.text))
        if not out:
            raise FetchRefused("FRED served no series: " + group_faults(faults))
        return Fetched(batches=out, faults=faults)

    def normalize(self, batches: Sequence[RawBatch], ctx: FetchContext) -> list[Observation]:
        parsed: dict[str, list[tuple[date, Decimal]]] = {}
        for raw in batches:
            series_id = str(raw.kind)
            spec = BY_ID.get(series_id)
            factor = _SCALES[spec.scale] if spec is not None else Decimal(1)
            parsed[series_id] = read_csv(str(raw.payload), series_id, factor)

        out: list[Observation] = []
        for spec in SPECS:
            if spec.series_key is None:
                continue
            points = parsed.get(spec.fred_id) or []
            if not points:
                continue
            since = points[-1][0] - timedelta(days=REPLAY_DAYS)
            out.extend(
                Observation(
                    series_key=spec.series_key,
                    asof=asof,
                    value=value,
                    display=_spread_display(value),
                    tier=1,
                    source=f"FRED {spec.fred_id} (fredgraph.csv)",
                    observed_at=ctx.now,
                    extra={"fred_id": spec.fred_id, "served_unit": "percent"},
                )
                for asof, value in points
                if asof >= since
            )

        walcl = parsed.get("WALCL") or []
        if walcl:
            since = walcl[-1][0] - timedelta(days=REPLAY_DAYS)
            for index, (asof, _) in enumerate(walcl):
                if asof < since:
                    continue
                liquidity = net_liquidity(
                    walcl[: index + 1],
                    parsed.get("WTREGEN") or [],
                    parsed.get("RRPONTSYD") or [],
                    ctx.now,
                )
                if liquidity is None:
                    continue
                # A withheld week is reported only when it is the newest one.
                # An older Wednesday already stored with a value must not be
                # overwritten by a null because a component is absent today.
                if liquidity.value is None and index != len(walcl) - 1:
                    continue
                out.append(liquidity)

        for print_spec in PRINTS:
            print_row = macro_print(print_spec, parsed, ctx.now)
            if print_row is not None:
                out.append(print_row)
        return out


def read_csv(text: str, series_id: str, factor: Decimal = Decimal(1)) -> list[tuple[date, Decimal]]:
    """Every dated observation in one `fredgraph.csv` body, oldest first.

    FRED writes `.` for a day the series has no reading, which is a holiday and
    not a zero. Those rows are skipped: a series of nothing but full stops
    yields no value rather than a floor of zeroes.
    """
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        raise FetchRefused(f"FRED served an empty body for {series_id}")
    header = rows[0]
    if len(header) < 2:
        raise FetchRefused(f"FRED's CSV for {series_id} has no value column")
    out: list[tuple[date, Decimal]] = []
    for row in rows[1:]:
        if len(row) < 2:
            continue
        try:
            day = date.fromisoformat(row[0].strip())
        except ValueError:
            continue
        value = maybe_dec(row[1])
        if value is None:
            continue
        out.append((day, value * factor))
    out.sort()
    return out


def net_liquidity(
    walcl: Sequence[tuple[date, Decimal]],
    tga: Sequence[tuple[date, Decimal]],
    rrp: Sequence[tuple[date, Decimal]],
    now: datetime,
) -> Observation | None:
    """`WALCL − WTREGEN − RRPONTSYD`, all three struck on WALCL's own Wednesday.

    WALCL is the H.4.1 Wednesday balance, so the as-of is WALCL's. The other two
    are read at that same date: TGA is itself a Wednesday series, and RRP is
    daily, so it is taken on that Wednesday and on no other day.

    A component with no reading at that date withholds the whole figure. A total
    missing a term is not a smaller total, and net liquidity computed from two
    of three components would be wrong by the size of the third.
    """
    if not walcl:
        return None
    asof, balance_sheet = walcl[-1]
    components: dict[str, Decimal | None] = {"WALCL": balance_sheet}
    components["WTREGEN"] = _on(tga, asof)
    components["RRPONTSYD"] = _on(rrp, asof)

    missing = [name for name, value in components.items() if value is None]
    extra = {
        "WALCL": str(balance_sheet),
        "WALCL_asof": asof.isoformat(),
        "WTREGEN": None if components["WTREGEN"] is None else str(components["WTREGEN"]),
        "RRPONTSYD": None if components["RRPONTSYD"] is None else str(components["RRPONTSYD"]),
        "component_asof": asof.isoformat(),
    }
    if missing:
        return Observation(
            series_key="net_liquidity",
            asof=asof,
            value=None,
            display="",
            tier=4,
            source="FRED WALCL, WTREGEN, RRPONTSYD",
            observed_at=now,
            note=(
                f"net liquidity is withheld for {asof}: FRED has no reading for "
                f"{', '.join(missing)} on that date, and a total missing a term is not a "
                "smaller total"
            ),
            extra=extra,
        )

    tga_value = components["WTREGEN"]
    rrp_value = components["RRPONTSYD"]
    assert tga_value is not None and rrp_value is not None
    value = balance_sheet - tga_value - rrp_value
    return Observation(
        series_key="net_liquidity",
        asof=asof,
        value=value,
        display=_trillions(value),
        tier=1,
        source="FRED WALCL, WTREGEN, RRPONTSYD",
        observed_at=now,
        extra=extra,
    )


def macro_print(
    spec: Print,
    parsed: Mapping[str, Sequence[tuple[date, Decimal]]],
    now: datetime,
) -> Observation | None:
    """One macro print, as the figure a clause refers to.

    `None` when the series was not served at all, which the run already records
    as a gap. A component missing for the month withholds the whole figure as a
    Tier 4 null naming what was absent: a total missing a term is not a smaller
    total, and a control group computed from four of five parts would be wrong
    by the size of the fifth and would look right.
    """
    head = parsed.get(spec.fred_ids[0]) or []
    if not head:
        return None

    months = [_month_end(day) for day, _ in head]
    asof = months[-1]
    previous = months[-2] if len(months) >= 2 else None

    current, missing = _combine(spec, parsed, asof)
    prior = (
        (None, [])
        if previous is None or spec.measure == "level"
        else _combine(spec, parsed, previous)
    )
    prior_value, prior_missing = prior

    extra: dict[str, Any] = {
        "fred_ids": list(spec.fred_ids),
        "measure": spec.measure,
        "data_month": asof.isoformat(),
    }
    if len(spec.fred_ids) > 1:
        extra["components"] = {
            one: _text(_at_month(parsed.get(one) or [], asof)) for one in spec.fred_ids
        }

    absent = sorted(set(missing) | set(prior_missing))
    needs_prior = spec.measure != "level"
    if current is None or absent or (needs_prior and prior_value is None):
        return Observation(
            series_key=spec.series_key,
            asof=asof,
            value=None,
            display="",
            tier=4,
            source=f"FRED {', '.join(spec.fred_ids)} (fredgraph.csv)",
            observed_at=now,
            note=_withheld_note(spec, asof, absent, previous),
            extra=extra,
        )

    if spec.measure == "level":
        figure = current * spec.factor
    elif spec.measure == "mom_change":
        assert prior_value is not None  # narrowed above
        figure = (current - prior_value) * spec.factor
    else:
        assert prior_value is not None  # narrowed above
        if prior_value == 0:
            return None
        figure = (current / prior_value - Decimal(1)) * Decimal(100) * spec.factor

    if previous is not None:
        extra["prior_month"] = previous.isoformat()
    return Observation(
        series_key=spec.series_key,
        asof=asof,
        value=figure,
        display=_print_display(figure, spec),
        tier=1,
        source=f"FRED {', '.join(spec.fred_ids)} (fredgraph.csv)",
        observed_at=now,
        extra=extra,
    )


def _combine(
    spec: Print,
    parsed: Mapping[str, Sequence[tuple[date, Decimal]]],
    month: date,
) -> tuple[Decimal | None, list[str]]:
    """The print's figure for one data month, and which components were absent.

    With one id it is that series' reading. With five it is the first less the
    other four, which is the Census control group's own definition.
    """
    values: dict[str, Decimal | None] = {
        one: _at_month(parsed.get(one) or [], month) for one in spec.fred_ids
    }
    missing = [one for one, value in values.items() if value is None]
    if missing:
        return None, missing
    total = values[spec.fred_ids[0]]
    assert total is not None  # every value is present
    for one in spec.fred_ids[1:]:
        part = values[one]
        assert part is not None
        total -= part
    return total, []


def _at_month(points: Sequence[tuple[date, Decimal]], month: date) -> Decimal | None:
    """The reading whose data month ends on `month`, or null.

    FRED dates a monthly observation at the first of the month it describes, and
    `config/series.yaml` declares `asof=eom`, so the two are matched on the
    month rather than on the day.
    """
    for day, value in reversed(points):
        if _month_end(day) == month:
            return value
    return None


def _month_end(day: date) -> date:
    """The last day of `day`'s own month.

    FRED's first-of-month dating becomes the end-of-month `asof` the release
    rule is written against. Without this every monthly rule would compute its
    next print a month early.
    """
    first_next = date(day.year + day.month // 12, day.month % 12 + 1, 1)
    return first_next - timedelta(days=1)


def _withheld_note(
    spec: Print, asof: date, absent: Sequence[str], previous: date | None
) -> str:
    if absent:
        return (
            f"{spec.series_key} is withheld for {asof.isoformat()}: FRED has no reading for "
            f"{', '.join(absent)} in that month or the one before it, and a total missing a "
            "term is not a smaller total"
        )
    if previous is None:
        return (
            f"{spec.series_key} is withheld for {asof.isoformat()}: it is a month-over-month "
            "figure and FRED served only one month"
        )
    return f"{spec.series_key} is withheld for {asof.isoformat()}: the prior month is missing"


def _print_display(figure: Decimal, spec: Print) -> str:
    sign = "+" if spec.measure != "level" and figure > 0 else ""
    return f"{sign}{figure:,.{spec.decimals}f}{spec.suffix}"


def _text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _on(points: Sequence[tuple[date, Decimal]], asof: date) -> Decimal | None:
    """The reading dated exactly `asof`, or null.

    Exactly, not on-or-before. Reading the nearest earlier value would carry a
    number into a day it did not describe, which is the one thing the contract
    forbids outright.
    """
    for day, value in points:
        if day == asof:
            return value
    return None


def _spread_display(bps: Decimal) -> str:
    """`2.70% (270 bps)`, the way the journal prints it, from the stored bps."""
    percent = bps / Decimal(100)
    return f"{percent:.2f}% ({bps:.0f} bps)"


def _trillions(usd: Decimal) -> str:
    return f"${usd / Decimal(10) ** 12:.3f}T"
