"""Which findings a run opens and which it is entitled to close, against a real Postgres.

`macro_quality.finding` is shared by three writers: the 09:30 run (vault,
archive, Monday grading), the 21:00 rescore (fetch and evaluate only) and the
authoring agent. A run that closes every open finding it did not raise itself
closes what only another writer can raise, and a run that syncs before its last
step drops what that step found.

These drive the real `run_once` with the network fetch replaced by nothing,
because both faults lived in how the run was ordered and scoped, not in any one
check. Skipped when no local Postgres is installed.
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

from macro import config, main
from macro.store import Store

from .conftest import CONFIG, instant

MIGRATIONS = CONFIG.parents[1] / "supabase" / "migrations"
PORT = 55440
MONDAY_MORNING = instant("2026-09-28T01:30:00")  # Monday 09:30 in Singapore
MONDAY_EVENING = instant("2026-09-28T13:00:00")  # Monday 21:00 in Singapore

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
    base = Path(tempfile.mkdtemp(prefix="findingpg", dir="/tmp"))
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


@pytest.fixture
async def store(cluster: str) -> AsyncIterator[Store]:
    connected = await Store.connect(cluster, ssl="disable")
    await connected._execute("delete from macro_quality.finding")
    try:
        yield connected
    finally:
        await connected.aclose()


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fetch_nothing(*_args: object, **_kwargs: object) -> list[object]:
        return []

    monkeypatch.setattr(main, "fetch_all", fetch_nothing)


def _settings(cluster: str, vault: Path) -> config.Settings:
    return config.load(
        {"DATABASE_URL": cluster, "DATABASE_SSL": "disable", "VAULT_PATH": str(vault)}
    )


async def _open(store: Store) -> set[tuple[str, str]]:
    rows = await store._fetch(
        "select check_key, subject from macro_quality.finding where closed_at is null"
    )
    return {(row["check_key"], row["subject"]) for row in rows}


async def _seed(store: Store, *rows: tuple[str, str]) -> None:
    for check_key, subject in rows:
        await store._execute(
            "insert into macro_quality.finding (check_key, subject, sentence, severity, opened_at)"
            " values ($1, $2, 'seeded', 2, $3)",
            check_key,
            subject,
            MONDAY_MORNING,
        )


async def test_mondays_grading_findings_are_stored(cluster, store, tmp_path, no_network):
    """The week closes after the mart is flushed, and what it finds reaches the table.

    On an empty ledger the prior week has no scorecard row, so Monday's grading
    raises `scorecard_row_missing` for W39. Syncing inside `_persist`, before
    `_close_the_week` ran, dropped it on every Monday.
    """
    await main.run_once(
        _settings(cluster, tmp_path), run_kind="full", now=MONDAY_MORNING, dry_run=False
    )
    assert ("scorecard_row_missing", "2026-W39") in await _open(store)


async def test_the_rescore_closes_only_what_the_rescore_evaluates(
    cluster, store, tmp_path, no_network
):
    """21:00 reads no vault file, grades nothing and never runs the agent's checks.

    A finding only the 09:30 run, the Monday grading or the authoring agent can
    raise stays open through the evening. One the rescore does evaluate, a
    source that did not finish, closes when the rescore no longer raises it.
    """
    await _seed(
        store,
        ("vault_file_unreadable", "/vault/Catalyst Monitor.md"),
        ("catalyst_line_unparsed", "Catalyst Monitor.md: a line"),
        ("setup_ledger_open_too_long", "st-1"),
        ("scorecard_row_missing", "2026-W39"),
        ("database_write_refused", "the catalyst archive"),
        ("week_grade_incomplete", "2026-W39"),
        ("source_incomplete", "yahoo"),
    )
    await main.run_once(
        _settings(cluster, tmp_path), run_kind="rescore", now=MONDAY_EVENING, dry_run=False
    )
    assert await _open(store) == {
        ("vault_file_unreadable", "/vault/Catalyst Monitor.md"),
        ("catalyst_line_unparsed", "Catalyst Monitor.md: a line"),
        ("setup_ledger_open_too_long", "st-1"),
        ("scorecard_row_missing", "2026-W39"),
        ("database_write_refused", "the catalyst archive"),
        ("week_grade_incomplete", "2026-W39"),
    }


async def test_the_morning_run_still_closes_its_own_vault_findings(
    cluster, store, tmp_path, no_network
):
    """A vault finding that stopped being true is closed by the run that reads the vault."""
    await _seed(
        store,
        ("catalyst_line_unparsed", "Catalyst Monitor.md: a line fixed since"),
        ("week_grade_incomplete", "2026-W39"),
    )
    await main.run_once(
        _settings(cluster, tmp_path), run_kind="full", now=MONDAY_MORNING, dry_run=False
    )
    still = await _open(store)
    assert ("catalyst_line_unparsed", "Catalyst Monitor.md: a line fixed since") not in still
    # The agent's own finding is the agent's to close.
    assert ("week_grade_incomplete", "2026-W39") in still
