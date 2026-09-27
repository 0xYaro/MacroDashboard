"""`macro_mart.catalyst_window`: what the Catalysts page is handed.

The window is written once a morning and read all day, so what it keeps is a
claim about the whole day: an event that ended an hour ago is still worth
seeing, dimmed, and one that ended two days ago is not, even if a run was
missed in between.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from macro.evaluate import catalyst_window
from macro.model import Catalyst

NOW = datetime(2026, 9, 23, 1, 30, tzinfo=UTC)  # the 09:30 Singapore run


def catalyst(name: str, starts: datetime, **overrides: object) -> Catalyst:
    base: dict[str, object] = {
        "external_key": name,
        "name": name,
        "starts_at": starts,
        "ends_at": None,
        "date_precision": "exact",
        "source_url": "https://example.com",
        "affected": (),
        "status": "tracked",
        "seen_on": date(2026, 9, 23),
        "raw_date_text": "",
    }
    return Catalyst(**{**base, **overrides})  # type: ignore[arg-type]


def names(rows: list) -> set[str]:
    return {row.name for row in rows}


def test_an_event_stays_until_it_is_more_than_a_day_past_its_end():
    rows = catalyst_window(
        [
            catalyst("last night's FOMC", datetime(2026, 9, 22, 18, 0, tzinfo=UTC)),
            catalyst("two days ago", datetime(2026, 9, 21, 0, 0, tzinfo=UTC)),
            catalyst(
                "yesterday, no time",
                datetime(2026, 9, 22, tzinfo=UTC),
                date_precision="day",
            ),
            catalyst(
                "a conference that opened on Monday",
                datetime(2026, 9, 21, tzinfo=UTC),
                ends_at=datetime(2026, 9, 25, tzinfo=UTC),
                date_precision="window",
            ),
            catalyst("tomorrow", datetime(2026, 9, 24, 12, 30, tzinfo=UTC)),
            catalyst("too far out", datetime(2026, 10, 30, tzinfo=UTC)),
        ],
        [],
        now=NOW,
    )
    assert names(rows) == {
        "last night's FOMC",
        "yesterday, no time",
        "a conference that opened on Monday",
        "tomorrow",
    }


def test_the_row_carries_the_category_the_words_and_the_estimate():
    late_october = catalyst(
        "Microsoft FY2027 Q1 earnings",
        datetime(2026, 10, 21, tzinfo=UTC),
        ends_at=datetime(2026, 10, 31, tzinfo=UTC),
        date_precision="month",
        raw_date_text="late October 2026 expected; exact date/time TBD",
        date_estimated=True,
        family="earnings_msft",
        category="earnings",
    )
    (row,) = catalyst_window([late_october], [], now=NOW)
    assert row.category == "earnings"
    assert row.family == "earnings_msft"
    assert row.date_estimated
    assert row.date_text == "late October 2026 expected; exact date/time TBD"
    assert row.ends_at == datetime(2026, 10, 31, tzinfo=UTC)
    assert row.days_out == 28


def test_a_row_stored_before_the_category_existed_is_classified_from_its_name():
    """A rescore reads rows the last full run wrote, possibly before the column."""
    old = catalyst("September Consumer Price Index", datetime(2026, 10, 14, 12, 30, tzinfo=UTC))
    (row,) = catalyst_window([old], [], now=NOW)
    assert (row.family, row.category) == ("us_cpi", "us_macro")
