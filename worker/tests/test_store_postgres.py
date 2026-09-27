"""`store.py` against a real Postgres with the real schema.

Only what a database can show: which gap rows a landed observation removes.
Skipped when no local Postgres is installed. The cluster is created in a
temporary directory, used once and thrown away. Nothing here touches the live
Supabase project.
"""

from __future__ import annotations

import json
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

from macro.model import Gap, Observation
from macro.series import load as load_registry
from macro.store import Store

from .conftest import CONFIG

MIGRATIONS = CONFIG.parents[1] / "supabase" / "migrations"
PORT = 55435
NOW = datetime(2026, 9, 23, 13, 0, tzinfo=UTC)
KEY = "breadth_spx_above_200dma"

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
    # A unix socket path is capped at about 103 bytes, which a pytest tmp path
    # can exceed, so the cluster lives under the system temp root and listens
    # on loopback: asyncpg connects over TCP.
    base = Path(tempfile.mkdtemp(prefix="workerpg", dir="/tmp"))
    data = base / "data"
    subprocess.run(
        ["initdb", "-D", str(data), "-U", "postgres", "--encoding=UTF8", "--locale=C"],
        capture_output=True,
        check=True,
    )
    # macOS Postgres refuses to start without a locale in the environment.
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


def _breadth(asof: date, value: str | None = "50.49") -> Observation:
    return Observation(
        series_key=KEY,
        asof=asof,
        value=None if value is None else Decimal(value),
        display="" if value is None else f"{value}%",
        tier=1 if value is not None else 4,
        source="test",
        observed_at=NOW,
        note=None if value is not None else "withheld for the test",
    )


async def _open_gaps(store: Store) -> set[tuple[date, bool]]:
    rows = await store._fetch(
        "select asof, permanent from macro_quality.gap where series_key = $1", KEY
    )
    return {(row["asof"], row["permanent"]) for row in rows}


async def test_a_gap_closes_when_its_reading_lands_permanent_or_not(cluster):
    """Gap #15 in production: 2026-09-21 held a permanent gap beside its own reading.

    The 09:30 run opened it before the source had published, the 21:00 run
    stored 50.49 for the same session, and the gap stayed open for good because
    only non-permanent gaps were ever cleared. A gap says a reading is missing,
    so the moment the reading exists the row is false, whatever it was
    predicted to be when it was opened.
    """
    store = await Store.connect(cluster, ssl="disable")
    try:
        ids_map = await store.sync_series(load_registry(CONFIG))
        monday, tuesday, wednesday = date(2026, 9, 21), date(2026, 9, 22), date(2026, 9, 23)
        await store.write_gaps(
            [
                Gap(KEY, monday, "missed", permanent=True),
                Gap(KEY, tuesday, "missed", permanent=False),
                Gap(KEY, wednesday, "missed", permanent=True),
            ],
            ids_map,
        )
        # Monday lands with a value, Tuesday lands withheld, Wednesday not at all.
        landed = [_breadth(monday), _breadth(tuesday, None)]
        await store.write_observations(landed, ids_map, received_at=NOW)
        await store.clear_filled_gaps(landed, ids_map)

        # A withheld Tier 4 row is not the reading, so Tuesday's gap stands, and
        # Wednesday never arrived.
        assert await _open_gaps(store) == {(tuesday, False), (wednesday, True)}

        # A reading already stored clears a gap on any later run that touches
        # the series, which is what retires a gap opened before this fix.
        await store.write_gaps([Gap(KEY, monday, "stale", permanent=True)], ids_map)
        await store.clear_filled_gaps([_breadth(wednesday)], ids_map)
        assert await _open_gaps(store) == {(tuesday, False), (wednesday, True)}
    finally:
        await store.aclose()


async def test_the_registry_can_move_a_series_to_a_slot_another_one_holds(cluster):
    """Adding the two daily ETF series shifted every Flows row after them by one.

    `(table_group, ordinal)` is unique and not deferrable, and the sync upserts
    row by row, so inserting `btc_etf_net_flow` at ordinal 2 while the stored
    `btc_etf_weekly_flow` still held 2 was refused, and every run's sync with it.
    The database here starts from the numbering the registry had before.
    """
    store = await Store.connect(cluster, ssl="disable")
    registry = load_registry(CONFIG)
    before = {
        "btc_etf_weekly_flow": 2,
        "btc_etf_inflow_streak": 3,
        "eth_etf_weekly_flow": 4,
        "eth_etf_inflow_streak": 5,
        "stablecoin_mcap": 6,
        "defi_tvl": 7,
        "crypto_open_interest": 8,
        "funding_extremes": 9,
        "liquidations_24h": 10,
        "alt_season_index": 11,
    }
    try:
        await store.sync_series(registry)
        async with store.transaction() as connection:
            await connection.execute(
                "delete from macro_core.series "
                "where key in ('btc_etf_net_flow', 'eth_etf_net_flow')"
            )
            await connection.execute(
                "update macro_core.series set ordinal = -ordinal "
                "where table_group = 'Flows & Structure'"
            )
            for key, ordinal in before.items():
                await connection.execute(
                    "update macro_core.series set ordinal = $2 where key = $1", key, ordinal
                )
            await connection.execute(
                "update macro_core.series set ordinal = 1 where key = 'net_liquidity'"
            )

        await store.sync_series(registry)
        stored = {
            row["key"]: row["ordinal"]
            for row in await store._fetch(
                "select key, ordinal from macro_core.series "
                "where table_group = 'Flows & Structure'"
            )
        }
        assert stored == {
            one.key: one.ordinal
            for one in registry.ordered()
            if one.table_group == "Flows & Structure"
        }
    finally:
        await store.aclose()


async def test_a_series_dropped_from_the_registry_does_not_block_its_old_slot(cluster):
    """A row whose key left `config/series.yaml` keeps its slot unless it is parked.

    The parking step moved only rows the registry still names, so a retired
    series sitting on `(table_group, ordinal)` refused the upsert of the series
    now declared there, and every run's sync with it. Parked once, it stays
    parked: a second sync must not flip it back onto a live slot.
    """
    store = await Store.connect(cluster, ssl="disable")
    registry = load_registry(CONFIG)
    try:
        await store.sync_series(registry)
        slot = registry.get("defi_tvl").ordinal
        async with store.transaction() as connection:
            await connection.execute(
                "update macro_core.series set ordinal = 999 where key = 'defi_tvl'"
            )
            await connection.execute(
                """
                insert into macro_core.series
                  (key, label, table_group, ordinal, value_kind, cadence, release_rule,
                   replayable, source_name, fetch_path)
                values ('a_retired_series', 'Retired', 'Flows & Structure', $1, 'numeric',
                        'daily', 'daily', true, 'test', 'deterministic')
                """,
                slot,
            )

        await store.sync_series(registry)
        await store.sync_series(registry)
        rows = {
            row["key"]: row["ordinal"]
            for row in await store._fetch(
                "select key, ordinal from macro_core.series "
                "where key in ('defi_tvl', 'a_retired_series')"
            )
        }
        assert rows["defi_tvl"] == slot
        assert rows["a_retired_series"] < 0
    finally:
        await store.aclose()


async def test_a_farside_outage_does_not_demote_verified_etf_sessions(cluster):
    """Fourteen verified Tier 1 sessions must survive a run that could not read Farside.

    The replay re-states every session of the last two weeks. With Farside
    unreadable, SoSoValue alone is Tier 2, and the upsert replaced each stored
    Tier 1 row with it, silently. A session this run is the first to store is
    still written at Tier 2, because that is what is known of it.
    """
    from macro.fetchers import etf_flows

    from .test_etf_flows import NOW as CAPTURED
    from .test_etf_flows import golden_batches

    store = await Store.connect(cluster, ssl="disable")
    try:
        ids_map = await store.sync_series(load_registry(CONFIG))
        keys = ("btc_etf_net_flow", "btc_etf_weekly_flow", "btc_etf_inflow_streak")
        await store._execute(
            "delete from macro_core.observation where series_id = any($1::uuid[])",
            [ids_map[key] for key in keys],
        )
        verified = [
            one
            for one in etf_flows.normalize(golden_batches(), CAPTURED)
            if one.series_key in keys and date(2026, 9, 8) <= one.asof < date(2026, 9, 22)
        ]
        assert {one.tier for one in verified} == {1}
        await store.write_observations(verified, ids_map, received_at=NOW)

        soso_only = [one for one in golden_batches() if one.kind == "sosovalue_btc"]
        restated = [
            one
            for one in etf_flows.normalize(soso_only, CAPTURED)
            if one.series_key in keys and one.asof >= date(2026, 9, 8)
        ]
        assert {one.tier for one in restated} == {2}
        await store.write_observations(restated, ids_map, received_at=NOW)

        stored = {
            (row["key"], row["asof"]): row["tier"]
            for row in await store._fetch(
                "select s.key, o.asof, o.tier from macro_core.observation o "
                "join macro_core.series s on s.id = o.series_id "
                "where s.key = any($1::text[]) and o.asof >= '2026-09-08'",
                list(keys),
            )
        }
        assert stored[("btc_etf_net_flow", date(2026, 9, 18))] == 1
        assert stored[("btc_etf_weekly_flow", date(2026, 9, 18))] == 1
        assert stored[("btc_etf_net_flow", date(2026, 9, 22))] == 2
        assert {tier for (_, day), tier in stored.items() if day < date(2026, 9, 22)} == {1}
    finally:
        await store.aclose()


async def test_a_restated_breadth_session_keeps_its_s5th_cross_check(cluster):
    """The evening run re-states yesterday's session after S5TH has moved on to today's.

    S5TH carries one bar, so the cross-check can only be attached to the session
    it describes on the run that sees it. A later re-statement of that session
    cannot supply it, and replacing `extra` wholesale erased it.
    """
    from macro.fetchers import breadth

    store = await Store.connect(cluster, ssl="disable")
    try:
        ids_map = await store.sync_series(load_registry(CONFIG))
        day = date(2026, 9, 22)
        session = breadth.Session(
            asof=day, value=Decimal("49.00"), above=244, counted=498, excluded=(), folded=()
        )
        published = breadth.Published(asof=day, value=Decimal("49.70"), extra={})
        await store.write_observations(
            [breadth.session_observation(session, published)], ids_map, received_at=NOW
        )
        await store.write_observations(
            [breadth.session_observation(session, None)], ids_map, received_at=NOW
        )
        row = await store._fetchrow(
            "select extra from macro_core.observation where series_id = $1 and asof = $2",
            ids_map[KEY],
            day,
        )
        extra = row["extra"] if isinstance(row["extra"], dict) else json.loads(row["extra"])
        assert extra["s5th_close"] == "49.70"
        assert extra["s5th_divergence"] == "-0.70"
        assert extra["counted"] == 498
    finally:
        await store.aclose()
