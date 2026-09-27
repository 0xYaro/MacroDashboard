"""The catalyst archive: what counts as having happened, and how an event is keyed.

The live rows below mirror `macro_core.catalyst` as the 09:30 run reads it. The
names are copied from the Catalyst Monitor's own history.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from macro import archive, families, vault

NOW = datetime(2026, 9, 24, 1, 30, tzinfo=UTC)  # 09:30 Singapore, 24 September


def live(name: str, starts_at: datetime | None, **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": "7b0c1e0e-8d4a-4d7e-9a53-2f1f0f7a1c01",
        "external_key": vault.external_key(name),
        "name": name,
        "starts_at": starts_at,
        "ends_at": None,
        "date_precision": "exact",
        "date_text": "2026-09-22 10:20 ET / 2026-09-22 22:20 SGT",
        "source_url": "https://www.federalreserve.gov/newsevents/2026-september.htm",
        "affected": ["rates", "USD"],
        "first_seen": date(2026, 9, 10),
        "last_seen": date(2026, 9, 22),
    }
    row.update(overrides)
    return row


def test_a_catalyst_listed_on_the_day_it_started_is_archived():
    speech = live(
        "Federal Reserve Vice Chair Philip Jefferson speech on discount-window modernization",
        datetime(2026, 9, 22, 14, 20, tzinfo=UTC),
    )

    [one] = archive.from_live([speech], now=NOW)

    assert one.occurred_on == date(2026, 9, 22)
    assert one.name_key == speech["external_key"]
    assert one.family == "fed_speech"
    assert one.category == "fed"
    assert one.confidence == "confirmed"
    assert one.provenance == "catalyst_monitor"
    assert one.provenance_ref == "2026-09-10..2026-09-22"
    assert one.catalyst_id == speech["id"]


def test_a_catalyst_that_has_not_started_waits():
    later = live("October FOMC meeting", datetime(2026, 10, 28, 18, 0, tzinfo=UTC))

    assert archive.from_live([later], now=NOW) == []


def test_a_dated_catalyst_with_no_time_waits_for_its_day_to_end():
    """Trump-Xi, 24 September, no time: at 09:30 that morning it has not happened yet."""
    visit = live(
        "Trump-Xi White House state visit and U.S.-China talks",
        datetime(2026, 9, 24, tzinfo=UTC),
        date_precision="day",
        date_text="2026-09-24; exact meeting time TBD",
        last_seen=date(2026, 9, 24),
    )

    assert archive.from_live([visit], now=NOW) == []
    [one] = archive.from_live([visit], now=datetime(2026, 9, 25, 1, 30, tzinfo=UTC))
    assert one.occurred_on == date(2026, 9, 24)
    assert one.confidence == "approximate"


def test_a_catalyst_dropped_before_its_date_did_not_happen():
    """Retired a week before its date: cancelled or rescheduled away, never held."""
    dropped = live(
        "SPCX day-90 lockup release",
        datetime(2026, 9, 10, tzinfo=UTC),
        date_precision="day",
        date_text="2026-09-10",
        last_seen=date(2026, 9, 3),
    )

    assert archive.from_live([dropped], now=NOW) == []


def test_a_missed_run_on_the_day_does_not_lose_the_event():
    """Jackson Hole, 2026: last read on the 27th, keynote on the 28th, pruned on the 31st."""
    keynote = live(
        "Jackson Hole Economic Policy Symposium and Chair Warsh keynote",
        datetime(2026, 8, 28, 14, 0, tzinfo=UTC),
        last_seen=date(2026, 8, 27),
    )

    [one] = archive.from_live([keynote], now=NOW)

    assert one.family == "jackson_hole"
    assert one.occurred_on == date(2026, 8, 28)


def test_a_month_is_not_a_date_the_archive_will_file():
    """"Late October expected" parses to 1 October; that is not when it happened."""
    vague = live(
        "Tesla Q3 2026 earnings",
        datetime(2026, 9, 1, tzinfo=UTC),
        date_precision="month",
        date_text="September 2026 expected",
    )

    assert archive.from_live([vague], now=NOW) == []


@pytest.mark.parametrize(
    ("date_text", "precision", "expected"),
    [
        ("2026-09-22 10:20 ET / 2026-09-22 22:20 SGT", "exact", "confirmed"),
        ("2026-09-21; exact time TBD", "day", "approximate"),
        ("2026-10-09 expected", "day", "approximate"),
        ("2026-09-22 to 2026-09-24", "window", "approximate"),
        (None, "day", "approximate"),
    ],
)
def test_confidence_reads_the_monitors_own_hedges(date_text, precision, expected):
    assert archive.confidence_of(date_text, precision) == expected


def test_an_event_after_midnight_utc_is_filed_on_its_utc_date():
    """An FOMC statement at 14:00 ET is 18:00 UTC and 02:00 SGT the next day."""
    fomc = live(
        "September FOMC", datetime(2026, 9, 16, 18, 0, tzinfo=UTC), last_seen=date(2026, 9, 16)
    )

    [one] = archive.from_live([fomc], now=NOW)

    assert one.occurred_on == date(2026, 9, 16)


def write(tmp_path: Path, rows: list[dict[str, Any]]) -> Path:
    path = tmp_path / "backfill.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    return path


BACKFILL_ROW: dict[str, Any] = {
    "name": "June CPI",
    "occurred_on": "2026-07-14",
    "starts_at": "2026-07-14T12:30:00+00:00",
    "ends_at": None,
    "date_precision": "exact",
    "source_url": "https://www.bls.gov/schedule/2026/",
    "affected": ["rates", "USD"],
    "provenance": "catalyst_monitor",
    "provenance_ref": "2026-06-14..2026-07-14",
    "confidence": "confirmed",
    "first_tracked": "2026-06-14",
    "last_tracked": "2026-07-14",
}


def test_a_backfill_row_keys_exactly_as_the_daily_run_would(tmp_path):
    [one] = archive.from_backfill(write(tmp_path, [BACKFILL_ROW]))

    assert one.name_key == vault.external_key("June CPI")
    assert one.occurred_on == date(2026, 7, 14)
    assert one.family == "us_cpi"
    assert one.first_tracked == date(2026, 6, 14)


def test_a_backfill_date_with_no_time_sits_at_utc_midnight(tmp_path):
    row = {
        **BACKFILL_ROW,
        "name": "January FOMC",
        "starts_at": None,
        "occurred_on": "2026-01-28",
        "date_precision": "day",
        "provenance": "weekly_journal",
        "confidence": "approximate",
        "moved_markets": True,
        "market_note": "Held; the statement repriced the front end.",
    }

    [one] = archive.from_backfill(write(tmp_path, [row]))

    assert one.starts_at == datetime(2026, 1, 28, tzinfo=UTC)
    assert one.occurred_on == date(2026, 1, 28)
    assert one.moved_markets is True
    assert one.family == "fomc_decision"


def test_a_backfill_that_names_one_event_twice_is_refused(tmp_path):
    with pytest.raises(ValueError, match="same event on the same day twice"):
        archive.from_backfill(write(tmp_path, [BACKFILL_ROW, dict(BACKFILL_ROW)]))


def test_a_backfill_row_with_a_month_for_a_date_is_refused(tmp_path):
    with pytest.raises(ValueError, match="precision 'month'"):
        archive.from_backfill(write(tmp_path, [{**BACKFILL_ROW, "date_precision": "month"}]))


def test_an_empty_backfill_is_refused(tmp_path):
    with pytest.raises(ValueError, match="no rows"):
        archive.from_backfill(write(tmp_path, []))


@pytest.mark.parametrize(
    ("name", "family", "category"),
    [
        ("July CPI", "us_cpi", "us_macro"),
        ("August Consumer Price Index", "us_cpi", "us_macro"),
        ("FOMC minutes from July 28-29 meeting", "fomc_minutes", "fed"),
        ("September FOMC with Summary of Economic Projections", "fomc_decision", "fed"),
        # Names the Beige Book as "pre-FOMC": the Beige Book rule must win.
        ("Fed Beige Book pre-FOMC growth/inflation read", "fed_beige_book", "fed"),
        ("Post-PCE Fed Williams and Goolsbee speeches", "fed_speech", "fed"),
        ("Fed Governor Waller economic outlook speech", "fed_speech", "fed"),
        ("Senate confirms Warsh as Fed Chair", "fed_leadership", "fed"),
        ("Trump v. Cook (Fed Governor removal)", "fed_leadership", "fed"),
        ("Deribit March quarterly crypto options expiry", "crypto_options_expiry", "crypto"),
        ("U.S. 20Y Treasury bond reopening", "treasury_auction", "treasury_liquidity"),
        # "Reopening" of a strait is not a Treasury reopening.
        ("U.S.-Iran ceasefire / Hormuz reopening decision watch", None, "policy_geopolitics"),
        ("China Q2 GDP / June activity data", "china_activity_data", "global_macro"),
        ("NVIDIA Q2 FY2027 earnings call / AI accelerator demand", "earnings_nvda", "earnings"),
        ("TSMC monthly sales - June 2026", "tsmc_monthly_sales", "earnings"),
        ("September quarterly options expiration", "quad_witching", "index_options"),
        ("Japan equity market Marine Day closure", "market_closure", "market_closure"),
        # "Disclosure" is not a closure.
        ("SEC Investor Advisory Committee meeting on AI disclosure", None, "market_structure"),
        ("AI Infra Summit 2026", None, "ai_tech"),
        ("SpaceX SPCX IPO pricing / trading debut", None, "market_structure"),
        ("Farnborough International Airshow 2026", None, "other"),
    ],
)
def test_families(name, family, category):
    assert families.classify(name) == (family, category)


def test_every_category_the_rules_emit_is_one_the_table_accepts():
    migration = (
        Path(__file__).resolve().parents[2]
        / "supabase"
        / "migrations"
        / "20260923000400_catalyst_occurrence.sql"
    ).read_text(encoding="utf-8")
    for category in families.CATEGORIES:
        assert f"'{category}'" in migration, category
