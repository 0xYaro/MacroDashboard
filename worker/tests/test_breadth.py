"""Breadth, computed from the constituents, with Barchart's S5TH beside it.

`golden/tradingview_scan_spx.json` is a real capture of the scanner query
`breadth.SCAN_BODY` sends, taken 2026-09-23 10:25 UTC, before the US open. It
carries 503 lines and three sessions: 2026-09-22 at offset 0, 2026-09-21 at
`[1]` and 2026-09-18 at `[2]`. `golden/tradingview_s5th.html` is the S5TH page
captured 2026-09-21, carrying the 2026-09-18 bar at 49.5.

Every expected figure below was counted by hand from the capture: two lines
have no 200-day average (HONA and FDXF, when-issued spin-off shares), three are
second share classes (GOOG, FOX, NWS), which leaves 498 companies. Two lines,
CINF and LRCX, had traded pre-market and already rolled: their offset 0 is the
unfinished 2026-09-23 bar and their 22nd sits at `[1]`, so they count on the
22nd and the 21st and have no bar for the 18th.
"""

from __future__ import annotations

import copy
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from macro.fetchers import breadth
from macro.fetchers.base import FetchContext, Fetched, run_one
from macro.http import FetchRefused
from macro.model import Observation, RawBatch
from macro.series import load as load_registry

from .conftest import CONFIG, golden_json, golden_text, instant

NOW = instant("2026-09-23T13:00:00")  # Wednesday 21:00 in Singapore
KEY = "breadth_spx_above_200dma"


def scan() -> dict:
    return golden_json("tradingview_scan_spx.json")


def raw(kind: str, payload: object) -> RawBatch:
    return RawBatch(source="breadth", kind=kind, fetched_at=NOW, payload=payload)


def ctx(cursor: date | None = None, now=NOW) -> FetchContext:
    return FetchContext(
        now=now,
        registry=load_registry(CONFIG),
        cursor={} if cursor is None else {KEY: cursor},
    )


# ---------------------------------------------------------------------------
# The computation
# ---------------------------------------------------------------------------


def test_sessions_counted_one_vote_per_company():
    """244 of 498 above on the 22nd and 248 of 498 on the 21st.

    The 18th, 245 of 496, is not re-stated: CINF and LRCX have rolled and no
    longer carry it (see the test below).
    """
    sessions = breadth.compute_sessions(scan(), NOW)
    assert [(one.asof, one.above, one.counted, one.value) for one in sessions] == [
        (date(2026, 9, 21), 248, 498, Decimal("49.80")),
        (date(2026, 9, 22), 244, 498, Decimal("49.00")),
    ]
    newest = sessions[-1]
    # Alphabet, Fox and News Corp vote through their Class A lines.
    assert newest.folded == ("NASDAQ:FOX", "NASDAQ:GOOG", "NASDAQ:NWS")
    assert newest.excluded == ("NASDAQ:HONA", "NYSE:FDXF")
    # The 18th, had it been kept, rests on the subset without the rolled lines.
    friday = breadth._session(*_bars_and_descriptions(scan()), date(2026, 9, 18))
    assert (friday.above, friday.counted, friday.value) == (245, 496, Decimal("49.40"))
    assert set(friday.excluded) == {"NASDAQ:CINF", "NASDAQ:HONA", "NASDAQ:LRCX", "NYSE:FDXF"}


def _bars_and_descriptions(
    payload: dict,
) -> tuple[dict[str, dict[date, tuple[Any, Any]]], dict[str, str]]:
    """The scan filed by line and date, the way `compute_sessions` files it."""
    from datetime import UTC, datetime

    from macro import clock

    bars: dict = {}
    descriptions: dict = {}
    for symbol, row in breadth._rows(payload):
        descriptions[symbol] = str(row.get("description") or symbol)
        for k in breadth.OFFSETS:
            suffix = "" if k == 0 else f"[{k}]"
            stamp = row.get(f"time{suffix}")
            if stamp is None:
                continue
            day = datetime.fromtimestamp(int(stamp), tz=UTC).astimezone(clock.NEW_YORK).date()
            bars.setdefault(symbol, {})[day] = (
                row.get(f"close{suffix}"),
                row.get(f"SMA200{suffix}"),
            )
    return bars, descriptions


def test_an_older_session_short_of_the_newest_ones_companies_is_not_restated():
    """The 18th rests on 496 companies, because CINF and LRCX rolled pre-market.

    The run that saw the 18th as its newest session stored it from all 498. A
    re-statement from the subset the scan still carries would overwrite that
    whole figure with a partial one, so an older session is re-stated only
    when every company counted on the newest session is counted on it too.
    """
    sessions = breadth.compute_sessions(scan(), NOW)
    assert [one.asof for one in sessions] == [date(2026, 9, 21), date(2026, 9, 22)]


def test_a_line_that_rolled_pre_market_is_read_at_its_own_date():
    """LRCX's offset 0 is the unfinished 23rd. Its 22nd, at `[1]`, is above its average.

    Pushing its unfinished bar far below its average must change nothing.
    """
    payload = scan()
    lrcx = next(row for row in payload["data"] if row["s"] == "NASDAQ:LRCX")
    lrcx["d"][2] = 1.0
    assert breadth.compute_sessions(payload, NOW)[-1].above == 244


def test_the_rows_are_tier_one_and_dated_by_the_bar():
    rows = breadth.Breadth().normalize([raw("scan", scan())], ctx())
    assert [one.asof for one in rows] == [date(2026, 9, 21), date(2026, 9, 22)]
    newest = rows[-1]
    assert newest.series_key == KEY
    assert newest.value == Decimal("49.00")
    assert newest.display == "49.00%"
    assert newest.tier == 1
    assert newest.extra["counted"] == 498
    assert "s5th_close" not in newest.extra


def test_the_running_session_is_never_stored():
    """At 19:00 UTC on the 22nd that session has not closed, so it is not stored.

    The 18th is not re-stated either: the rolled lines do not carry it.
    """
    sessions = breadth.compute_sessions(scan(), instant("2026-09-22T19:00:00"))
    assert [one.asof for one in sessions] == [date(2026, 9, 21)]


def test_a_shrunken_symbol_set_refuses_rather_than_answering():
    short = scan()
    short["data"] = short["data"][:400]
    with pytest.raises(FetchRefused, match="fewer than 480"):
        breadth.compute_sessions(short, NOW)


def test_missing_averages_refuse_below_the_floor():
    """503 lines, but most of them lost their 200-day average: a column changed."""
    payload = scan()
    for row in payload["data"][:60]:
        row["d"][3] = row["d"][6] = None  # SMA200 at offsets 0 and 1
    with pytest.raises(FetchRefused, match="fewer than 480"):
        breadth.compute_sessions(payload, NOW)


def test_a_scan_of_another_shape_refuses():
    with pytest.raises(FetchRefused, match="shape"):
        breadth.compute_sessions({"data": [{"s": "NYSE:T", "d": [1, 2]}] * 500}, NOW)


def test_a_class_a_line_speaks_for_its_company():
    """GOOG below its average and GOOGL above: the company counts as above."""
    payload = scan()
    goog = next(row for row in payload["data"] if row["s"] == "NASDAQ:GOOG")
    goog["d"][2] = 1.0  # close far below its average
    assert breadth.compute_sessions(payload, NOW)[-1].above == 244


# ---------------------------------------------------------------------------
# S5TH: the cross-check and the fallback
# ---------------------------------------------------------------------------


def s5th_page_for(day: date) -> str:
    """The captured S5TH page with its bar re-dated to `day`, 00:00 UTC.

    The capture carries the 18th, which this scan no longer re-states. Only
    the bar's `time` changes; its close stays the captured 49.5.
    """
    from datetime import UTC, datetime

    stamp = int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp())
    page = golden_text("tradingview_s5th.html")
    assert '"time":"1789689600"' in page
    return page.replace('"time":"1789689600"', f'"time":"{stamp}"')


def test_s5th_travels_beside_the_session_it_describes():
    """S5TH at 49.5 for the 22nd; the computed figure is 49.00."""
    rows = breadth.Breadth().normalize(
        [raw("scan", scan()), raw("s5th", s5th_page_for(date(2026, 9, 22)))], ctx()
    )
    newest = next(one for one in rows if one.asof == date(2026, 9, 22))
    assert newest.value == Decimal("49.00")
    assert newest.extra["s5th_close"] == "49.5"
    assert newest.extra["s5th_divergence"] == "-0.50"
    assert all("s5th_close" not in one.extra for one in rows if one is not newest)
    # A re-statement of any session keeps a cross-check stored before it.
    assert all(one.keep_extra == ("s5th_close", "s5th_divergence") for one in rows)
    assert breadth.review(rows) == []


def test_a_wide_divergence_is_a_finding():
    row = Observation(
        series_key=KEY,
        asof=date(2026, 9, 22),
        value=Decimal("49.00"),
        display="49.00%",
        tier=1,
        source="test",
        observed_at=NOW,
        extra={"s5th_close": "52.70", "s5th_divergence": "-3.70"},
    )
    findings = breadth.review([row])
    assert [one.check_key for one in findings] == ["breadth_divergence"]
    assert "3.70 points apart" in findings[0].sentence


def test_s5th_fills_a_session_only_when_the_scanner_failed_and_nothing_is_stored():
    page = golden_text("tradingview_s5th.html")
    refused: dict = {"data": []}

    rows = breadth.Breadth().normalize(
        [raw("scan", refused), raw("s5th", page)], ctx(cursor=date(2026, 9, 17))
    )
    assert len(rows) == 1
    assert rows[0].asof == date(2026, 9, 18)
    assert rows[0].tier == 2
    assert rows[0].value == Decimal("49.5")
    assert "fewer than 480" in rows[0].extra["computed_refused"]
    assert [one.check_key for one in breadth.review(rows)] == ["breadth_fallback"]

    # The session is already stored, so the vendor's figure never overwrites it.
    with pytest.raises(FetchRefused, match="fewer than 480"):
        breadth.Breadth().normalize(
            [raw("scan", refused), raw("s5th", page)], ctx(cursor=date(2026, 9, 18))
        )


def test_s5th_never_overwrites_a_computed_session():
    rows = breadth.Breadth().normalize(
        [raw("scan", scan()), raw("s5th", golden_text("tradingview_s5th.html"))],
        ctx(cursor=date(2026, 9, 1)),
    )
    assert all(one.tier == 1 for one in rows)


def test_s5th_reads_the_dated_daily_bar():
    """The captured page carries 49.5 with a bar time of 2026-09-18 00:00 UTC.

    That is the 49.50% the W39 journal recorded for that session, which is the
    point of reading the dated bar over the bare `price` in the JSON-LD block:
    that one carries no date at all.
    """
    published = breadth.read_s5th(golden_text("tradingview_s5th.html"))
    assert published.asof == date(2026, 9, 18)
    assert published.value == Decimal("49.5")
    assert published.extra["session_high"] == "50.49"
    assert published.extra["session_low"] == "48.11"


def test_s5th_refuses_a_page_whose_shape_changed():
    with pytest.raises(FetchRefused, match="init-data"):
        breadth.read_s5th("<html><body>no script here</body></html>")


def test_s5th_refuses_a_bar_with_no_time():
    page = (
        '<script type="application/prs.init-data+json">'
        '{"x":{"data":{"symbol":{"daily_bar":{"close":"49.5"}}}}}'
        "</script>"
    )
    with pytest.raises(FetchRefused, match="no close or no time"):
        breadth.read_s5th(page)


# ---------------------------------------------------------------------------
# Through the run loop
# ---------------------------------------------------------------------------


class Captured(breadth.Breadth):
    """The real normalize and review, with the two captures in place of the network."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    async def fetch(self, client, ctx) -> Fetched:
        return Fetched(
            batches=[
                raw("scan", self._payload),
                raw("s5th", golden_text("tradingview_s5th.html")),
            ]
        )


async def test_the_morning_run_owes_nothing_it_cannot_have():
    """09:30 Singapore on the 23rd: the 22nd has closed and the scanner has it.

    The cursor is the 18th, so the 21st and the 22nd were owed, both arrive,
    and no gap opens. Under S5TH alone the 22nd was not published until about
    09:15 UTC, and this run opened a permanent gap for it every morning.
    """
    morning = instant("2026-09-23T01:30:00")
    result = await run_one(
        Captured(scan()), client=None, ctx=ctx(cursor=date(2026, 9, 18), now=morning)
    )
    assert result.status == "ok"
    assert result.gaps == []
    assert max(one.asof for one in result.observations) == date(2026, 9, 22)


async def test_a_divergence_reaches_the_run_as_a_finding():
    payload = copy.deepcopy(scan())
    # Push every company below its average for the 18th, so 0% against S5TH's 49.5.
    # The rolled lines do not carry the 18th, so they are unrolled first: their
    # 22nd and 21st move to offsets 0 and 1, and their 21st stands in at [2].
    for row in payload["data"]:
        if row["s"] in ("NASDAQ:CINF", "NASDAQ:LRCX"):
            row["d"][2:8] = row["d"][5:11]
            row["d"][10] = 1789689600 + 13 * 3600 + 30 * 60  # 18 September, 09:30 New York
    for row in payload["data"]:
        if row["d"][9] is not None:
            row["d"][8] = row["d"][9] / 2
    result = await run_one(Captured(payload), client=None, ctx=ctx(cursor=date(2026, 9, 17)))
    assert result.status == "ok"
    assert [one.check_key for one in result.findings] == ["breadth_divergence"]
