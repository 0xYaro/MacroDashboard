"""The authoring context lists only the tail risks the latest note still lists.

`Blackswan Events.md` is written weekly, and the worker retires a risk the
latest note dropped by setting `retired_on` rather than deleting the row. The
context read every row regardless, so a risk the cron had stopped listing weeks
ago was still put in front of the Monday author and the events agent as live.

Against a real Postgres with the real schema. Skipped when no local Postgres is
installed; nothing here touches the live project.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

from macro_agent.store import Store

from .conftest import MIGRATIONS, ROOT

SHIM = ROOT / "supabase" / "tests" / "local_shim.sql"

PORT = 55441

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
    base = Path(tempfile.mkdtemp(prefix="riskpg", dir="/tmp"))
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
        # The Supabase roles and auth stubs, so every migration applies whole.
        shim = _psql("-d", "macro", "-v", "ON_ERROR_STOP=1", "-q", "-f", str(SHIM))
        assert shim.returncode == 0, shim.stderr
        for one in sorted(MIGRATIONS.glob("*.sql")):
            done = _psql("-d", "macro", "-v", "ON_ERROR_STOP=1", "-q", "-f", str(one))
            assert done.returncode == 0, f"{one.name} failed: {done.stderr}"
        yield f"postgresql://postgres@127.0.0.1:{PORT}/macro"
    finally:
        subprocess.run(["pg_ctl", "-D", str(data), "-m", "immediate", "stop"], capture_output=True)
        shutil.rmtree(base, ignore_errors=True)


async def test_a_retired_tail_risk_is_not_in_the_context(cluster):
    store = await Store.connect(cluster, ssl="disable")
    try:
        await store._execute(
            "insert into macro_core.tail_risk "
            "(key, name, severity, summary, first_seen, last_seen, retired_on) values "
            "('tanker', 'Tanker attacks', 'acute', 'insurance repriced', "
            " '2026-09-21', '2026-09-21', null), "
            "('cyber', 'Exchange cyberattack', 'elevated', 'dropped by the note', "
            " '2026-09-07', '2026-09-14', '2026-09-21')"
        )
        lines = await store.tail_risk_lines()
    finally:
        await store.aclose()
    assert len(lines) == 1
    assert "Tanker attacks" in lines[0]
    assert not any("Exchange cyberattack" in one for one in lines)


async def test_the_desk_holdings_watch_reaches_the_watchlist_deltas(cluster):
    """After the cutover nothing writes `watchlist_change`; the desk's rows must still reach the prompt."""
    store = await Store.connect(cluster, ssl="disable")
    try:
        await store._execute(
            "insert into macro_core.holding_watch "
            "(as_of, symbol, source, change_24h_pct, change_basis, material, "
            " driver_summary, source_url, no_clear_reason, unavailable_reason) values "
            "('2026-09-24', 'APP', 'accounting', 6.84, 'close_to_close', true, "
            " 'Two brokers raised their targets.', 'https://example.com/app', false, null), "
            "('2026-09-24', 'TLT', 'accounting', -0.62, 'close_to_close', false, "
            " null, null, false, null), "
            "('2026-09-24', 'MAX', 'accounting', null, null, false, "
            " null, null, false, 'did not trade')"
        )
        lines = await store.recent_watchlist_changes(since=date(2026, 9, 20))
    finally:
        await store.aclose()
    assert lines == ["[2026-09-24] APP 6.84%: Two brokers raised their targets."]
