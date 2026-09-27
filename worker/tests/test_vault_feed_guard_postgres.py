"""One bad cron file must not empty the rows another file feeds.

The 09:30 run reads three files and used to decide whether to write the vault
feed at all by asking whether *any* of them had produced a row. So a Catalyst
Monitor that could not be read, beside a Watchlist that parsed, retired every
tracked catalyst: the catalyst list it compared against was empty. The mirror
case deactivated every holding. Each feed now retires or deactivates only when
its own file was read whole.

These run the real `_persist` against a real Postgres with the real schema, from
real files on disk, because the fault lived in how the run wired the feeds
together and not in any one parser. Skipped when no local Postgres is installed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest

from macro import main, vault
from macro import series as series_module
from macro.store import Store

from .conftest import CONFIG, instant

MIGRATIONS = CONFIG.parents[1] / "supabase" / "migrations"
PORT = 55439
NOW = instant("2026-09-24T01:30:00")  # Thursday 09:30 in Singapore

pytestmark = pytest.mark.skipif(
    not (
        shutil.which("initdb")
        and shutil.which("pg_ctl")
        and shutil.which("psql")
        and MIGRATIONS.is_dir()
    ),
    reason="no local Postgres, or supabase/migrations is absent",
)

CATALYSTS = """# Catalyst Monitor

## Tracked Catalysts

- Catalyst: Micron Q4 FY2026 earnings. Date & Time: 2026-09-30 16:30 ET. Source: Micron IR \
https://investors.micron.com. Affected: MU, memory, semis.
- Catalyst: U.S. September CPI. Date & Time: 2026-10-14 08:30 ET. Source: BLS \
https://www.bls.gov/schedule. Affected: rates, USD, equities.

## Newly Added

- Catalyst: FOMC decision. Date & Time: 2026-10-28 14:00 ET. Source: Federal Reserve \
https://www.federalreserve.gov. Affected: rates, USD, equities, crypto.
"""

#: The Monitor with one section gone: its rows are missing, not dropped.
CATALYSTS_NO_NEWLY_ADDED = CATALYSTS.split("## Newly Added")[0]

BLACKSWAN = """# Blackswan Events

## Active Risks
- Risk: Tanker attacks in the Strait of Hormuz. Date & Time: 2026-09-21. Severity: acute. \
Summary: shipping insurance repriced. Priced-in: partly. Source: Reuters https://reuters.com. \
Assets: brent, equities.

## Newly Flagged
- Risk: Crowded AI unwind. Date & Time: 2026-09-21. Severity: elevated. Summary: positioning \
stretched. Priced-in: no. Source: FT https://ft.com. Assets: semis, nasdaq.
"""

WATCHLIST = """# Market Watchlist

## Holdings
- MU
- RIOT
- 012330 — Hyundai Mobis (KRX)

## Active Changes

## Newly Flagged
"""


def _psql(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["psql", "-h", "127.0.0.1", "-p", str(PORT), "-U", "postgres", *args],
        capture_output=True,
        text=True,
    )


@pytest.fixture(scope="module")
def cluster() -> Iterator[str]:
    base = Path(tempfile.mkdtemp(prefix="vaultpg", dir="/tmp"))
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


async def _run(
    store: Store,
    folder: Path,
    *,
    catalysts: str | None,
    watchlist: str | None,
    blackswan: str | None = None,
) -> None:
    """One 09:30 vault pass over files on disk. `None` leaves that file absent."""
    for name, text in (
        ("Catalyst Monitor.md", catalysts),
        ("Market Watchlist.md", watchlist),
        ("Blackswan Events.md", blackswan),
    ):
        path = folder / name
        if path.exists():
            path.unlink()
        if text is not None:
            path.write_text(text, encoding="utf-8")
    feed = vault.read_all(
        catalyst_file=folder / "Catalyst Monitor.md",
        watchlist_file=folder / "Market Watchlist.md",
        blackswan_file=folder / "Blackswan Events.md",
        seen_on=NOW.date(),
    )
    registry = series_module.load(CONFIG)
    series_ids = await store.sync_series(registry)
    outcome = main.RunOutcome(run_kind="full", now=NOW)
    outcome.findings.extend(feed.findings)
    await main._persist(store, outcome, feed, registry, series_ids, now=NOW)


async def _statuses(store: Store) -> dict[str, str]:
    rows = await store._fetch("select name, status from macro_core.catalyst")
    return {row["name"]: row["status"] for row in rows}


async def _active(store: Store) -> dict[str, bool]:
    rows = await store._fetch("select ticker, active from macro_core.holding")
    return {row["ticker"]: row["active"] for row in rows}


@pytest.fixture
async def store(cluster: str) -> AsyncIterator[Store]:
    connected = await Store.connect(cluster, ssl="disable")
    await connected._execute("delete from macro_mart.catalyst_window")
    await connected._execute("delete from macro_core.catalyst")
    await connected._execute("delete from macro_core.holding")
    await connected._execute("delete from macro_core.tail_risk")
    try:
        yield connected
    finally:
        await connected.aclose()


async def test_an_unreadable_catalyst_monitor_retires_nothing(store, tmp_path):
    await _run(store, tmp_path, catalysts=CATALYSTS, watchlist=WATCHLIST)
    assert set((await _statuses(store)).values()) == {"tracked"}

    # The Monitor is gone this morning; the Watchlist parsed as usual.
    await _run(store, tmp_path, catalysts=None, watchlist=WATCHLIST)
    assert set((await _statuses(store)).values()) == {"tracked"}
    assert set((await _active(store)).values()) == {True}


async def test_an_empty_catalyst_monitor_retires_nothing(store, tmp_path):
    await _run(store, tmp_path, catalysts=CATALYSTS, watchlist=WATCHLIST)
    await _run(store, tmp_path, catalysts="", watchlist=WATCHLIST)
    assert set((await _statuses(store)).values()) == {"tracked"}


async def test_a_monitor_missing_a_section_retires_nothing(store, tmp_path):
    await _run(store, tmp_path, catalysts=CATALYSTS, watchlist=WATCHLIST)
    await _run(store, tmp_path, catalysts=CATALYSTS_NO_NEWLY_ADDED, watchlist=WATCHLIST)
    assert (await _statuses(store))["FOMC decision"] == "tracked"


async def test_a_whole_monitor_still_retires_what_it_dropped(store, tmp_path):
    await _run(store, tmp_path, catalysts=CATALYSTS, watchlist=WATCHLIST)
    dropped = CATALYSTS.replace(
        "- Catalyst: U.S. September CPI. Date & Time: 2026-10-14 08:30 ET. Source: BLS "
        "https://www.bls.gov/schedule. Affected: rates, USD, equities.\n",
        "",
    )
    assert dropped != CATALYSTS
    await _run(store, tmp_path, catalysts=dropped, watchlist=None)
    statuses = await _statuses(store)
    assert statuses["U.S. September CPI"] == "retired"
    assert statuses["FOMC decision"] == "tracked"


async def test_an_unreadable_watchlist_deactivates_nothing(store, tmp_path):
    await _run(store, tmp_path, catalysts=CATALYSTS, watchlist=WATCHLIST)
    assert set((await _active(store)).values()) == {True}

    # The Watchlist is gone this morning; the Monitor parsed as usual.
    await _run(store, tmp_path, catalysts=CATALYSTS, watchlist=None)
    assert await _active(store) == {"MU": True, "RIOT": True, "012330": True}


async def test_a_watchlist_with_no_holdings_section_deactivates_nothing(store, tmp_path):
    await _run(store, tmp_path, catalysts=CATALYSTS, watchlist=WATCHLIST)
    await _run(
        store, tmp_path, catalysts=CATALYSTS, watchlist="# Market Watchlist\n\n## Active Changes\n"
    )
    assert set((await _active(store)).values()) == {True}


async def test_a_whole_watchlist_still_deactivates_what_it_dropped(store, tmp_path):
    await _run(store, tmp_path, catalysts=CATALYSTS, watchlist=WATCHLIST)
    await _run(store, tmp_path, catalysts=None, watchlist=WATCHLIST.replace("- RIOT\n", ""))
    assert await _active(store) == {"MU": True, "RIOT": False, "012330": True}


async def _retired(store: Store) -> dict[str, bool]:
    rows = await store._fetch("select name, retired_on from macro_core.tail_risk")
    return {row["name"]: row["retired_on"] is not None for row in rows}


async def test_a_blackswan_note_missing_a_section_retires_nothing(store, tmp_path):
    await _run(store, tmp_path, catalysts=CATALYSTS, watchlist=WATCHLIST, blackswan=BLACKSWAN)
    assert await _retired(store) == {
        "Tanker attacks in the Strait of Hormuz": False,
        "Crowded AI unwind": False,
    }
    await _run(
        store,
        tmp_path,
        catalysts=CATALYSTS,
        watchlist=WATCHLIST,
        blackswan=BLACKSWAN.split("## Newly Flagged")[0],
    )
    assert set((await _retired(store)).values()) == {False}
