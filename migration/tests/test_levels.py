"""The Setup Ledger's level cells, as the journals actually wrote them.

Found on 2026-09-23 in the History view: W32's and W34's BTC box-low setups
were loaded with an entry floor of 62 and W34's target as 65, because the
journal writes a band's magnitude once, at its end (`$62.0-62.5K`).
"""

from __future__ import annotations

from decimal import Decimal

from macro_migrate.model import _level, parse_entry, parse_invalidation


def test_a_magnitude_written_once_scales_both_ends_of_an_entry():
    lo, hi, gate = parse_entry("$62.0-62.5K on a retest that holds")
    assert (lo, hi) == (Decimal("62000"), Decimal("62500"))
    assert gate == "on a retest that holds"
    lo, hi, _ = parse_entry("$62.2-62.6K only on a defended sweep")
    assert (lo, hi) == (Decimal("62200"), Decimal("62600"))


def test_a_band_with_a_magnitude_on_each_end_is_unchanged():
    assert parse_entry("$78.5K-$80.5K")[:2] == (Decimal("78500"), Decimal("80500"))
    assert parse_entry("7,500-7,560 on the close")[:2] == (Decimal("7500"), Decimal("7560"))


def test_a_target_written_as_a_range_takes_the_ranges_magnitude():
    assert _level("$65-66K box high") == Decimal("65000")
    assert _level("$66K, the box top rather than $70K") == Decimal("66000")
    assert _level("7,800") == Decimal("7800")


def test_a_side_written_as_a_symbol_is_read():
    assert parse_invalidation("daily close <$62K") == (Decimal("62000"), "below")
    assert parse_invalidation("daily close > $27,300") == (Decimal("27300"), "above")
    assert parse_invalidation("close below $61.5K") == (Decimal("61500"), "below")
