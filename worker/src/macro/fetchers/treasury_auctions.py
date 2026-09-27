"""TreasuryDirect auction results: the high yield and the bid-to-cover, per tenor.

Three clauses in a single W40 scenario set turn on how an auction went, and none
of them could be typed: "the Oct 6-8 3Y/10Y/30Y cluster clears without a second
record tail", "a second tailing auction in the Oct 6-8 cluster", "the Oct 6-8
auction cluster clears cleanly".

`https://www.treasurydirect.gov/TA_WS/securities/auctioned?format=json&days=N`
serves one record per auction, keyless, with a hundred and twenty fields. Two of
them are the demand measures those clauses are about.

## There is no tail here, and this module does not invent one

A tail is the high yield minus the **when-issued** yield at the bidding
deadline. Treasury does not publish when-issued: it is a dealer quote from a
grey market, and it appears in this feed nowhere at all.

So there is no `auction_tail` series, and there must not be one.
`averageMedianYield` is in the feed and is *not* a substitute: the median of
accepted competitive bids is a fact about the auction's own distribution, while
a tail is the gap between the auction and a market that was quoted outside it.
Subtracting one from the other yields a number in basis points that would look
exactly like a tail, move roughly with one, and be a different quantity. A
series called `tail` carrying that would be wrong in the way that is hardest to
catch, because every reader would assume it meant what the word means.

What the feed does give is better than a tail in one respect that matters here:
`bidToCoverRatio` and `highYield` are official results rather than quotes, so a
condition over them is checkable against a published record rather than against
whatever a desk said the when-issued was.

If a tail is ever genuinely needed it has to come from a source that publishes
when-issued, and it must arrive as its own series from that source. It must
never be computed here.

## The filter, and why it is not optional

The same endpoint serves bills, TIPS and floating-rate notes alongside nominal
coupon securities, and two of those three would corrupt a nominal-yield series
silently.

A TIPS reopening on 2026-09-17 carries `originalSecurityTerm: 10-Year` and
`highYield: 2.6530`. That is a **real** yield. The nominal ten-year auctioned on
2026-09-09 at 4.8340, and the ten-year par yield that week was 5.01%. Without
the filter the 10-Year series would take 2.653 as its newest reading: plausible
in isolation, catastrophic in a condition, and impossible to spot on a snapshot
row that says only "10-Year auction, 2.65%".

Bills carry an empty `highYield` and put their result in `highDiscountRate`,
and their `originalSecurityTerm` describes the bill being reopened rather than
anything a clause means by a tenor: the 4-week bill auctioned on 2026-09-17
reads `17-Week`.

The discriminators are explicit in the feed and this module uses them rather
than inferring anything: `tips`, `floatingRate` and `securityType`.

## The as-of, and the schedule

An auction happens on a date Treasury announces; it has no cadence.
`config/series.yaml` declares `event` for that reason, which means
`next_expected` is null and the row is never `overdue` on a day no auction was
scheduled.

The next auction date is knowable, just not from a rule: the companion
`upcoming` endpoint publishes it. Each observation carries it in
`extra.next_expected`, and `evaluate.snapshot` prefers a stated next date over a
derived one. So the row can say when the next ten-year auction is because
Treasury said so, rather than because a cadence was invented for it.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from ..http import Client, FetchRefused
from ..model import Observation, RawBatch
from ..units import maybe_dec
from .base import FetchContext, Fetched, batch

SOURCE = "treasury_auctions"

AUCTIONED_URL = (
    "https://www.treasurydirect.gov/TA_WS/securities/auctioned?format=json&days={days}"
)
UPCOMING_URL = "https://www.treasurydirect.gov/TA_WS/securities/upcoming?format=json"

#: How far back to ask. Long enough that every one of the seven tenors has
#: auctioned at least twice, so a missed run still finds the newest result, and
#: short enough to stay one small response.
DAYS = 120

#: `originalSecurityTerm` to the suffix each series key carries. A reopening is
#: still an auction of its original tenor: the 19-Year 11-Month bond auctioned
#: on 2026-09-15 is the 20-Year, and it is what the journal calls "the 20Y".
TENORS: dict[str, str] = {
    "2-Year": "2y",
    "3-Year": "3y",
    "5-Year": "5y",
    "7-Year": "7y",
    "10-Year": "10y",
    "20-Year": "20y",
    "30-Year": "30y",
}


def high_yield_key(tenor: str) -> str:
    return f"auction_high_yield_{TENORS[tenor]}"


def bid_to_cover_key(tenor: str) -> str:
    return f"auction_bid_to_cover_{TENORS[tenor]}"


class TreasuryAuctions:
    """The auction-results source."""

    source = SOURCE
    series = tuple(
        key
        for tenor in TENORS
        for key in (high_yield_key(tenor), bid_to_cover_key(tenor))
    )

    async def fetch(self, client: Client, ctx: FetchContext) -> Fetched:
        out = [
            batch(
                SOURCE,
                "auctioned",
                ctx,
                await client.get_json(AUCTIONED_URL.format(days=DAYS)),
            )
        ]
        faults: list[str] = []
        # The schedule is a convenience, not a result. Losing it costs the
        # stated next auction date and nothing else, so the results still land,
        # and every series is still delivered, so the run is still complete.
        try:
            out.append(batch(SOURCE, "upcoming", ctx, await client.get_json(UPCOMING_URL)))
        except FetchRefused as exc:
            faults.append(f"upcoming: {exc}")
        return Fetched(batches=out, faults=faults)

    def normalize(self, batches: Sequence[RawBatch], ctx: FetchContext) -> list[Observation]:
        auctioned: Any = None
        upcoming: Any = []
        for raw in batches:
            if str(raw.kind) == "auctioned":
                auctioned = raw.payload
            elif str(raw.kind) == "upcoming":
                upcoming = raw.payload
        if auctioned is None:
            return []
        return normalize_auctions(auctioned, upcoming, ctx.now)


def is_nominal_coupon(record: dict[str, Any]) -> bool:
    """True only for a nominal Treasury note or bond.

    Three exclusions, each read from a field the feed states rather than
    inferred. A TIPS `highYield` is a real yield and would be a different
    quantity in the same column. An FRN's result is a discount margin. A bill
    has no `highYield` at all and its `originalSecurityTerm` names the bill it
    reopens rather than a tenor any clause means.
    """
    return (
        record.get("tips") == "No"
        and record.get("floatingRate") == "No"
        and record.get("securityType") in ("Note", "Bond")
        and record.get("originalSecurityTerm") in TENORS
    )


def newest_per_tenor(records: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The most recent nominal auction of each tenor.

    A condition names a tenor's most recent auction, so the newest by auction
    date wins and everything older is history this run did not go looking for.
    """
    out: dict[str, dict[str, Any]] = {}
    for record in records:
        if not is_nominal_coupon(record):
            continue
        tenor = str(record["originalSecurityTerm"])
        auctioned_on = _auction_date(record)
        if auctioned_on is None:
            continue
        held = out.get(tenor)
        if held is None or auctioned_on > _auction_date(held):  # type: ignore[operator]
            out[tenor] = record
    return out


def next_auction_dates(upcoming: Any) -> dict[str, date]:
    """The next scheduled auction date per tenor, from the `upcoming` feed.

    The same filter, because the same three kinds are in this feed too, and an
    upcoming TIPS reopening of the ten-year would otherwise set the ten-year
    nominal's next date to an auction that will not produce a nominal yield.
    """
    out: dict[str, date] = {}
    for record in upcoming or []:
        if not isinstance(record, dict) or not is_nominal_coupon(record):
            continue
        tenor = str(record["originalSecurityTerm"])
        when = _auction_date(record)
        if when is None:
            continue
        held = out.get(tenor)
        if held is None or when < held:
            out[tenor] = when
    return out


def normalize_auctions(auctioned: Any, upcoming: Any, now: datetime) -> list[Observation]:
    """The newest nominal auction of each tenor, as two observations apiece."""
    if not isinstance(auctioned, list):
        raise FetchRefused("TreasuryDirect's auctioned response is not a list")
    newest = newest_per_tenor(auctioned)
    if not newest:
        raise FetchRefused(
            "TreasuryDirect served no nominal note or bond auction in the window, which "
            "after filtering out bills, TIPS and floating-rate notes leaves nothing to store"
        )
    scheduled = next_auction_dates(upcoming)

    out: list[Observation] = []
    for tenor, record in newest.items():
        auctioned_on = _auction_date(record)
        assert auctioned_on is not None  # narrowed in newest_per_tenor
        extra = _extra(record, tenor, scheduled.get(tenor))
        out.extend(_observations(tenor, record, auctioned_on, extra, now))
    return out


def _observations(
    tenor: str,
    record: dict[str, Any],
    auctioned_on: date,
    extra: dict[str, Any],
    now: datetime,
) -> list[Observation]:
    source = f"TreasuryDirect auction results ({tenor})"
    out: list[Observation] = []

    high_yield = maybe_dec(record.get("highYield"))
    if high_yield is None:
        out.append(
            Observation(
                series_key=high_yield_key(tenor),
                asof=auctioned_on,
                value=None,
                display="",
                tier=4,
                source=source,
                observed_at=now,
                note=(
                    f"the {tenor} auction on {auctioned_on.isoformat()} carries no highYield, "
                    "which a nominal note or bond always does, so the record is not the shape "
                    "this worker reads"
                ),
                extra=extra,
            )
        )
    else:
        out.append(
            Observation(
                series_key=high_yield_key(tenor),
                asof=auctioned_on,
                value=high_yield,
                display=f"{high_yield:.3f}%",
                tier=1,
                source=source,
                observed_at=now,
                extra=extra,
            )
        )

    ratio = maybe_dec(record.get("bidToCoverRatio"))
    if ratio is None:
        out.append(
            Observation(
                series_key=bid_to_cover_key(tenor),
                asof=auctioned_on,
                value=None,
                display="",
                tier=4,
                source=source,
                observed_at=now,
                note=(
                    f"the {tenor} auction on {auctioned_on.isoformat()} carries no "
                    "bidToCoverRatio"
                ),
                extra=extra,
            )
        )
    else:
        out.append(
            Observation(
                series_key=bid_to_cover_key(tenor),
                asof=auctioned_on,
                value=ratio,
                display=f"{ratio:.2f}x",
                tier=1,
                source=source,
                observed_at=now,
                extra=extra,
            )
        )
    return out


def _extra(record: dict[str, Any], tenor: str, next_auction: date | None) -> dict[str, Any]:
    """What travels with the row, including the figures a tail is not.

    `average_median_yield` and `low_yield` are carried because they describe the
    accepted bid distribution and a reader may want them. They are deliberately
    not combined into anything: see this module's docstring.
    """
    extra: dict[str, Any] = {
        "tenor": tenor,
        "security_term": record.get("securityTerm"),
        "security_type": record.get("securityType"),
        "reopening": record.get("reopening"),
        "cusip": record.get("cusip"),
        "offering_amount": record.get("offeringAmount") or None,
        "allocation_percentage": record.get("allocationPercentage") or None,
        "low_yield": record.get("lowYield") or None,
        "average_median_yield": record.get("averageMedianYield") or None,
        "tail": None,
        "tail_note": (
            "not published: a tail is the high yield less the when-issued yield, and Treasury "
            "does not publish when-issued"
        ),
    }
    if next_auction is not None:
        # An auction has no cadence, so `event` leaves `next_expected` null.
        # Treasury publishes the schedule, so the row can state it anyway, and
        # `evaluate.snapshot` prefers a stated next date over a derived one.
        extra["next_expected"] = next_auction.isoformat()
    return extra


def _auction_date(record: dict[str, Any]) -> date | None:
    raw = str(record.get("auctionDate") or "")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw).date()
    except ValueError:
        return None
