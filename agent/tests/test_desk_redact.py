"""Nothing Accounting says reaches the agent with an account identifier in it.

`desk positions` is read by a model and may be posted to Telegram, so a venue
reason like "Tiger account 50400376 holds cash only" is said without its number.
"""

from __future__ import annotations

import re

import pytest

from macro_agent.desk_store import aggregate_venues, redact_accounts

LONG_DIGITS = re.compile(r"\d{6,}")


@pytest.mark.parametrize(
    ("said", "shown"),
    [
        (
            "Tiger account 50400376 holds cash only, so it has no price that could be out of "
            "date. It is answering normally.",
            "A Tiger account holds cash only, so it has no price that could be out of date. "
            "It is answering normally.",
        ),
        ("Stale since 02:00. IBKR account U1234567 did not answer.",
         "Stale since 02:00. An IBKR account did not answer."),
        ("no fills for account #50400376", "no fills for an account"),
        ("the account no. 50400376 is closed", "the account is closed"),
        ("Hyperliquid sub-account 0x8c1f2e3d4b5a69788766554433221100aabbccdd lagged",
         "A Hyperliquid account lagged"),
        ("vault 0x8c1f2e3d4b5a69788766554433221100aabbccdd lagged",
         "vault [address withheld] lagged"),
        ("reference 50400376 failed", "reference [number withheld] failed"),
        ("answering normally", "answering normally"),
    ],
)
def test_account_identifiers_are_said_without_their_number(said, shown):
    assert redact_accounts(said) == shown


def test_venue_rows_are_one_per_venue_never_one_per_account():
    rows = [
        {"venue": "tiger", "state": "fresh", "reason": None, "position_count": 7,
         "venue_as_of": "2026-09-23T18:49:37+00:00", "received_at": "2026-09-23T18:49:46+00:00"},
        {"venue": "tiger", "state": "fresh",
         "reason": "Tiger account 50400376 holds cash only.", "position_count": 0,
         "venue_as_of": "2026-09-23T18:40:00+00:00", "received_at": "2026-09-23T18:49:46+00:00"},
        {"venue": "tiger", "state": "stale",
         "reason": "Tiger account 50400377 holds cash only.", "position_count": 0,
         "venue_as_of": None, "received_at": None},
        {"venue": "hyperliquid", "state": "fresh", "reason": None, "position_count": 4,
         "venue_as_of": "2026-09-23T18:49:42+00:00", "received_at": "2026-09-23T18:49:46+00:00"},
    ]
    got = aggregate_venues(rows)
    assert [one["venue"] for one in got] == ["hyperliquid", "tiger"]
    tiger = got[1]
    assert tiger["accounts"] == 3 and tiger["position_count"] == 7
    assert tiger["state"] == "stale", "the venue is as fresh as its stalest account"
    assert tiger["venue_as_of"] == "2026-09-23T18:40:00+00:00", "the oldest, never the newest"
    assert tiger["reasons"] == ["A Tiger account holds cash only."]
    assert not LONG_DIGITS.search(repr(got))
