"""The snapshot's "since week open" column, against the W39 tape.

The W39 scenario set was activated on Monday 2026-09-21, so `anchor_asof` is
that Monday. Week open is the tape the author was looking at, which is each
series' last reading before the anchor day: Friday's close for a session
series, not Monday's. Reading the anchor day itself compared every series that
had printed Monday with itself, and HY, IG, breadth and the dollar sat at 0.00
all week.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from macro import evaluate

from .conftest import history, instant, reading

NOW = instant("2026-09-23T01:30:00")  # Wednesday 09:30 in Singapore
ANCHOR = date(2026, 9, 21)


def _rows(registry, *readings):
    rows = evaluate.snapshot(registry, history(*readings), now=NOW, anchor_asof=ANCHOR)
    return {one.series_key: one for one in rows}


def test_week_open_is_the_last_reading_before_the_anchor_day(registry):
    """HY 268 on Friday, 266 on Monday: two basis points tighter, not zero."""
    rows = _rows(
        registry,
        reading("hy_oas_bps", "2026-09-17", "270"),
        reading("hy_oas_bps", "2026-09-18", "268"),
        reading("hy_oas_bps", "2026-09-21", "266"),
        reading("breadth_spx_above_200dma", "2026-09-18", "49.50"),
        reading("breadth_spx_above_200dma", "2026-09-21", "50.49"),
        reading("breadth_spx_above_200dma", "2026-09-22", "49.30"),
    )
    assert rows["hy_oas_bps"].delta_week_open == Decimal(-2)
    assert rows["breadth_spx_above_200dma"].delta_week_open == Decimal("-0.20")
    assert rows["hy_oas_bps"].anchor_asof == ANCHOR


def test_week_open_skips_a_missing_friday_to_the_last_reading_there_is(registry):
    """A holiday or a gap on the Friday leaves Thursday as the week's opening tape."""
    rows = _rows(
        registry,
        reading("dxy", "2026-09-17", "100.22"),
        reading("dxy", "2026-09-21", "100.43"),
    )
    assert rows["dxy"].delta_week_open == Decimal("0.21")


def test_no_reading_since_the_week_opened_has_no_week_open_delta(registry):
    """Nothing printed this week, so there is nothing to measure yet.

    CPI printed for August before the anchor. A zero there would read as "it
    printed and did not move", which is a different statement from "it has
    not printed".
    """
    rows = _rows(
        registry,
        reading("cpi_core_mom", "2026-07-31", "0.3"),
        reading("cpi_core_mom", "2026-08-31", "0.29"),
        reading("hy_oas_bps", "2026-09-18", "268"),
    )
    assert rows["cpi_core_mom"].delta_week_open is None
    assert rows["hy_oas_bps"].delta_week_open is None


def test_no_anchor_means_no_week_open_delta(registry):
    rows = evaluate.snapshot(
        registry,
        history(reading("hy_oas_bps", "2026-09-18", "268")),
        now=NOW,
        anchor_asof=None,
    )
    assert next(one for one in rows if one.series_key == "hy_oas_bps").delta_week_open is None
