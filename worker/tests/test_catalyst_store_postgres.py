"""The Catalysts page's three feeds, written by `store.py` and read back through
the `macro_mart` relations the page reads, against a real Postgres with the
real schema.

Skipped when no local Postgres is installed. The cluster is created in a
temporary directory, used once and thrown away. Nothing here touches the live
Supabase project.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from macro.evaluate import catalyst_window
from macro.model import Catalyst, TailRisk, WatchlistChange
from macro.store import Store, _flush_catalyst_window

from .conftest import CONFIG

MIGRATIONS = CONFIG.parents[1] / "supabase" / "migrations"
PORT = 55436

pytestmark = pytest.mark.skipif(
    not (
        shutil.which("initdb")
        and shutil.which("pg_ctl")
        and shutil.which("psql")
        and MIGRATIONS.is_dir()
    ),
    reason="no local Postgres, or supabase/migrations is absent",
)


def _psql(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["psql", "-h", "127.0.0.1", "-p", str(PORT), "-U", "postgres", *args],
        capture_output=True,
        text=True,
    )


@pytest.fixture(scope="module")
def cluster() -> Iterator[str]:
    base = Path(tempfile.mkdtemp(prefix="catalystpg", dir="/tmp"))
    data = base / "data"
    subprocess.run(
        ["initdb", "-D", str(data), "-U", "postgres", "--encoding=UTF8", "--locale=C"],
        capture_output=True,
        check=True,
    )
    subprocess.run(
        [
            "pg_ctl", "-D", str(data),
            "-o", f"-k {base} -p {PORT} -c listen_addresses=127.0.0.1",
            "-l", str(base / "log"), "start",
        ],
        capture_output=True,
        check=True,
        env={**os.environ, "LC_ALL": "C"},
    )
    try:
        for _ in range(40):
            if _psql("-d", "postgres", "-tAc", "select 1").returncode == 0:
                break
            time.sleep(0.25)
        assert _psql("-d", "postgres", "-c", "create database macro").returncode == 0
        # The Supabase roles and auth stubs, so every migration applies whole
        # and a failing one fails the test rather than being skipped.
        shim = _psql("-d", "macro", "-v", "ON_ERROR_STOP=1", "-q", "-f",
                     str(MIGRATIONS.parent / "tests" / "local_shim.sql"))
        assert shim.returncode == 0, shim.stderr
        for one in sorted(MIGRATIONS.glob("*.sql")):
            done = _psql("-d", "macro", "-v", "ON_ERROR_STOP=1", "-q", "-f", str(one))
            assert done.returncode == 0, f"{one.name} failed: {done.stderr}"
        yield f"postgresql://postgres@127.0.0.1:{PORT}/macro"
    finally:
        subprocess.run(["pg_ctl", "-D", str(data), "-m", "immediate", "stop"], capture_output=True)
        shutil.rmtree(base, ignore_errors=True)


def _risk(key: str, seen_on: date) -> TailRisk:
    return TailRisk(
        key=key,
        name=key,
        severity="watch",
        summary="",
        priced_in="",
        assets=(),
        source_url="https://example.com",
        seen_on=seen_on,
    )


async def test_the_weekly_note_dates_its_risks_and_retires_the_ones_it_dropped(cluster):
    store = await Store.connect(cluster, ssl="disable")
    try:
        # A row the old code wrote: dated by the run that read it, not the note.
        await store._execute(
            "insert into macro_core.tail_risk (key, name, severity, first_seen, last_seen) "
            "values ('tanker', 'tanker', 'acute', '2026-09-23', '2026-09-23')"
        )
        monday = date(2026, 9, 21)
        await store.write_tail_risks([_risk("tanker", monday), _risk("cyber", monday)])
        shown = await store._fetch(
            "select key, first_seen, last_seen from macro_mart.tail_risk_state order by key"
        )
        assert [(r["key"], r["first_seen"], r["last_seen"]) for r in shown] == [
            ("cyber", monday, monday),
            ("tanker", monday, monday),
        ]

        # The next Monday's note drops the cyber risk: it leaves the page and
        # stays in the table with the day it was dropped.
        next_monday = date(2026, 9, 28)
        await store.write_tail_risks([_risk("tanker", next_monday)])
        shown = await store._fetch("select key from macro_mart.tail_risk_state")
        assert [r["key"] for r in shown] == ["tanker"]
        kept = await store._fetchrow(
            "select retired_on, last_seen from macro_core.tail_risk where key = 'cyber'"
        )
        assert (kept["retired_on"], kept["last_seen"]) == (next_monday, monday)
        # Drift reads the retirement too: a dropped risk is not a live one.
        seen = {one.name: one.retired_on for one in await store.tail_risks_seen()}
        assert seen == {"tanker": None, "cyber": next_monday}

        # Listed again, it comes back.
        again = date(2026, 10, 5)
        await store.write_tail_risks([_risk("tanker", again), _risk("cyber", again)])
        shown = await store._fetch("select key from macro_mart.tail_risk_state order by key")
        assert [r["key"] for r in shown] == ["cyber", "tanker"]
    finally:
        await store.aclose()


async def test_a_weekend_repeat_of_fridays_move_is_one_row_dated_to_friday(cluster):
    """MU +3.92% and SKHY +2.47%, reported on Sunday 20 and Monday 21 September."""
    store = await Store.connect(cluster, ssl="disable")
    now = datetime.now(UTC)
    # Rebased onto this week so the view's fourteen-day horizon keeps them:
    # the Friday before the most recent Monday, and the three 09:15 reports.
    today = now.date()
    monday = date.fromordinal(today.toordinal() - today.weekday())

    def at(day: date) -> datetime:
        return datetime(day.year, day.month, day.day, 1, 15, tzinfo=UTC)

    saturday, sunday = (date.fromordinal(monday.toordinal() - n) for n in (2, 1))
    friday = date.fromordinal(monday.toordinal() - 3)
    try:
        await store.write_watchlist_changes(
            [
                WatchlistChange("MU", at(saturday), Decimal("3.10"), "sat", "https://a"),
                WatchlistChange("MU", at(sunday), Decimal("3.92"), "sun", "https://b"),
                WatchlistChange("MU", at(monday), Decimal("3.92"), "mon", "https://c"),
                WatchlistChange("SKHY", at(sunday), Decimal("2.47"), "sun", "https://d"),
                WatchlistChange("SKHY", at(monday), Decimal("2.47"), "mon", "https://e"),
            ]
        )
        rows = await store._fetch(
            "select ticker, change_pct, summary, session_on "
            "from macro_mart.watchlist_change_state order by ticker, observed_at"
        )
        seen = [(r["ticker"], str(r["change_pct"]), r["summary"], r["session_on"]) for r in rows]
        assert seen == [
            # A different number for the same session is a different claim, kept.
            ("MU", "3.10", "sat", friday),
            ("MU", "3.92", "sun", friday),
            ("SKHY", "2.47", "sun", friday),
        ]
    finally:
        await store.aclose()


async def test_a_catalyst_reaches_the_window_with_its_category_and_estimate(cluster):
    store = await Store.connect(cluster, ssl="disable")
    try:
        late_october = Catalyst(
            external_key="msft",
            name="Microsoft FY2027 Q1 earnings",
            starts_at=datetime(2026, 10, 21, tzinfo=UTC),
            ends_at=datetime(2026, 10, 31, tzinfo=UTC),
            date_precision="month",
            source_url="https://www.microsoft.com/en-us/Investor",
            affected=("Azure",),
            status="tracked",
            seen_on=date(2026, 9, 23),
            raw_date_text="late October 2026 expected; exact date/time TBD",
            date_estimated=True,
            family="earnings_msft",
            category="earnings",
        )
        ids = await store.write_catalysts([late_october])
        stored = await store.catalysts_by_id()
        (read_back,) = stored.values()
        assert (read_back.category, read_back.date_estimated) == ("earnings", True)
        assert read_back.raw_date_text.startswith("late October")

        rows = catalyst_window(
            [read_back], [], now=datetime(2026, 9, 23, 1, 30, tzinfo=UTC), ids=ids
        )
        async with store.transaction() as connection:
            await _flush_catalyst_window(connection, rows)
        window = await store._fetchrow(
            "select category, family, date_estimated, date_text, ends_at, days_out "
            "from macro_mart.catalyst_window"
        )
        assert window["category"] == "earnings"
        assert window["family"] == "earnings_msft"
        assert window["date_estimated"] is True
        assert window["date_text"] == "late October 2026 expected; exact date/time TBD"
        assert window["ends_at"] == datetime(2026, 10, 31, tzinfo=UTC)
        assert window["days_out"] == 28

        # The category is checked by the database, not only by the parser.
        refused = _psql(
            "-d", "macro", "-c",
            "update macro_core.catalyst set category = 'sports' where external_key = 'msft'",
        )
        assert refused.returncode != 0
        assert "ck_macro_catalyst_category" in refused.stderr
    finally:
        await store.aclose()
