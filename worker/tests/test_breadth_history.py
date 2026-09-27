"""The breadth backfill's arithmetic, on a synthetic index worked by hand.

500 lines over 220 sessions ending Tuesday 2026-09-22. Lines 0 to 299 rise one
cent a session, so every close sits above its own 200-session mean; lines 300
to 499 fall one cent a session, so every close sits below it. Two of the rising
lines are one company's Class A and Class C, which leaves 499 companies with
299 above: 59.92%.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from macro import clock
from macro.fetchers import breadth, breadth_history

LAST = date(2026, 9, 22)
SESSIONS = 220


def _sessions() -> list[date]:
    out = [LAST]
    while len(out) < SESSIONS:
        out.append(clock.previous_us_session(out[-1]))
    return sorted(out)


def _index() -> tuple[dict[str, list[tuple[date, Decimal]]], dict[str, str]]:
    days = _sessions()
    closes: dict[str, list[tuple[date, Decimal]]] = {}
    descriptions: dict[str, str] = {}
    for line in range(500):
        step = Decimal("0.01") if line < 300 else Decimal("-0.01")
        symbol = f"NYSE:L{line:03d}"
        closes[symbol] = [(day, Decimal(100) + step * n) for n, day in enumerate(days)]
        descriptions[symbol] = f"Line {line} Inc."
    descriptions["NYSE:L000"] = "Twin Co Class A"
    descriptions["NYSE:L001"] = "Twin Co Class C"
    return closes, descriptions


def test_a_session_is_the_share_of_companies_above_their_own_average():
    closes, descriptions = _index()
    sessions, skipped = breadth_history.compute_history(
        closes, descriptions, since=LAST, until=LAST
    )
    assert skipped == []
    [one] = sessions
    assert (one.asof, one.above, one.counted, one.value) == (LAST, 299, 499, Decimal("59.92"))
    assert one.folded == ("NYSE:L001",)


def test_a_session_without_two_hundred_closes_behind_it_is_skipped():
    """The 200th session is the first with a full window; the 199th is not."""
    closes, descriptions = _index()
    days = _sessions()
    sessions, skipped = breadth_history.compute_history(
        closes, descriptions, since=days[198], until=days[199]
    )
    assert [one.asof for one in sessions] == [days[199]]
    assert [(one.asof, one.counted) for one in skipped] == [(days[198], 0)]


def test_a_line_with_no_close_that_day_is_left_out_not_carried():
    closes, descriptions = _index()
    closes["NYSE:L499"] = [(day, value) for day, value in closes["NYSE:L499"] if day != LAST]
    [one] = breadth_history.compute_history(closes, descriptions, since=LAST, until=LAST)[0]
    assert one.counted == 498
    assert "NYSE:L499" in one.excluded


def test_the_average_includes_the_session_s_own_close():
    """The scanner's SMA200 includes the day's close, so the backfill's must too.

    A falling line, redrawn: flat at 100, except 300 exactly 200 sessions
    before the last, and 100.5 on the last. The window that includes the last
    close leaves the 300 out and averages 100.0025, so the line is above. The
    window that stops the day before takes the 300 in and averages 101, which
    would put it below.
    """
    days = _sessions()
    closes, descriptions = _index()
    line = [(day, Decimal(100)) for day in days]
    line[-201] = (days[-201], Decimal(300))
    line[-1] = (LAST, Decimal("100.5"))
    closes["NYSE:L499"] = line
    [one] = breadth_history.compute_history(closes, descriptions, since=LAST, until=LAST)[0]
    assert one.above == 300


def test_yahoo_spells_a_share_class_with_a_hyphen():
    line = breadth_history.Constituent(symbol="NYSE:BRK.B", description="Berkshire Class B")
    assert line.yahoo_symbol == "BRK-B"


def test_a_backfilled_session_is_tier_two():
    """Today's constituents applied to two years of sessions carry survivorship bias.

    A company that left the index is missing from every past session and one
    that joined is counted before it did. The live figure, over the list the
    index actually held that day, is the Tier 1 one.
    """
    closes, descriptions = _index()
    sessions, _ = breadth_history.compute_history(
        closes, descriptions, since=LAST, until=LAST
    )
    rows = breadth_history.history_observations(sessions, LAST)
    assert rows and {one.tier for one in rows} == {2}
    assert all(one.extra["backfill"] is True for one in rows)


def test_an_overwrite_replaces_only_what_the_backfill_itself_wrote():
    """`--breadth-overwrite` redoes the backfill; it never replaces another method's row.

    The live scanner's sessions are Tier 1 over the list the index held that
    day, and the journal's S5TH rows are what past gates were read against.
    Neither is replaced by a survivorship-biased rebuild.
    """
    from macro import main
    from macro.observations import Reading

    def stored(day: date, **extra) -> Reading:
        return Reading(breadth.SERIES_KEY, day, Decimal("50"), "50%", 1, dict(extra))

    closes, descriptions = _index()
    days = _sessions()[-4:]
    sessions, _ = breadth_history.compute_history(
        closes, descriptions, since=days[0], until=days[-1]
    )
    rows = breadth_history.history_observations(sessions, LAST)
    held = [
        stored(days[0], backfill=True),
        stored(days[1], counted=498),
        stored(days[2], s5th_close="49.5"),
    ]
    assert [one.asof for one in main.backfill_rows(rows, held, overwrite=False)] == [days[3]]
    assert [one.asof for one in main.backfill_rows(rows, held, overwrite=True)] == [
        days[0],
        days[3],
    ]
