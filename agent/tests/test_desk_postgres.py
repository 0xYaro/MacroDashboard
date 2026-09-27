"""`macro-agent desk` against a real Postgres, connected as the real `macro_desk` role.

The cluster takes `supabase/tests/local_shim.sql` (the Supabase roles and
`auth.uid()`) and then every migration, strictly: a migration that fails here
fails the suite. A simulated Accounting (`core.instrument`, `mart.book_position`,
`mart.venue_status`, with Accounting's own RLS and its `authenticated` policy)
is planted first, so the desk's read of positions goes through the same
SECURITY DEFINER function it uses on the live project.

Every desk call runs as `macro_desk`, so a statement that needed a privilege
the role does not have fails here exactly as it would in production. The
setup and the assertions use the superuser.

Skipped when no local Postgres is installed. Nothing here touches the live
Supabase project.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

import asyncpg
import pytest

from macro_agent import desk, prices
from macro_agent.config import Settings
from macro_agent.desk_store import DeskStore
from macro_agent.store import WriteRefused

from .conftest import CONFIG, MIGRATIONS, ROOT, WORKER_SRC

PORT = 55443
SHIM = ROOT / "supabase" / "tests" / "local_shim.sql"
# Monday 21 September 2026, 08:45 in Singapore: the catalyst run.
NOW = datetime(2026, 9, 21, 0, 45, tzinfo=UTC)
TODAY = date(2026, 9, 21)

pytestmark = pytest.mark.skipif(
    not (shutil.which("initdb") and shutil.which("pg_ctl") and shutil.which("psql")),
    reason="no local Postgres",
)

ACCOUNTING = """
create schema core;
create schema mart;
create table core.account (id uuid primary key default gen_random_uuid(), venue text not null);
create table core.instrument (
  id uuid primary key default gen_random_uuid(), kind text not null, symbol text not null,
  currency text not null, multiplier numeric not null default 1,
  underlying_id uuid null references core.instrument (id), expiry date null,
  strike numeric null, put_call text null, unique (kind, symbol));
create table mart.book_position (
  instrument_id uuid primary key references core.instrument (id), symbol text not null,
  kind text not null, name text null, quantity numeric not null, avg_cost numeric null,
  mark numeric null, mark_as_of timestamptz null, currency text not null,
  value_usd numeric null, premium_usd numeric null, delta numeric null,
  exposure_usd numeric null, exposure_pct numeric null, pnl_pct numeric null,
  venues text[] not null default '{}', residual boolean not null default false,
  withheld_reason text null, updated_at timestamptz not null default now(),
  pnl_usd numeric null, direction text null, leverage numeric null, liq_price numeric null,
  realized_pnl_usd numeric null, accounts text[] not null default '{}',
  asset_class text null, display_name text null);
create table mart.venue_status (
  account_id uuid primary key references core.account (id), venue text not null,
  state text not null, reason text null, producer_at timestamptz null,
  received_at timestamptz null, venue_as_of timestamptz null, sub_nav_usd numeric null,
  position_count int not null default 0, in_totals boolean not null default false);
alter table core.instrument enable row level security;
alter table core.account enable row level security;
alter table mart.book_position enable row level security;
alter table mart.venue_status enable row level security;
grant usage on schema mart to authenticated;
grant select on all tables in schema mart to authenticated;
create policy authenticated_select on mart.book_position for select to authenticated using (true);
create policy authenticated_select on mart.venue_status for select to authenticated using (true);

with i as (
  insert into core.instrument (kind, symbol, currency) values
    ('equity', 'GOOG', 'USD'), ('equity', 'META', 'USD'), ('spot', 'HYPE', 'USDC'),
    ('cash', 'USD', 'USD'), ('spot', 'USDE', 'USDC')
  returning id, kind, symbol, currency)
insert into mart.book_position (instrument_id, symbol, kind, name, quantity, currency,
  exposure_usd, venues, accounts, asset_class, display_name, direction, mark)
select id, symbol, kind, symbol || ' Inc', case symbol when 'USDE' then 0 else 10 end,
       currency, 1000, array[case kind when 'spot' then 'hyperliquid' else 'tiger' end],
       array['U1234567'], case kind when 'spot' then 'crypto' when 'cash' then 'cash' else 'equity' end,
       case symbol when 'GOOG' then 'Alphabet' end, 'long', 100
  from i;
insert into core.instrument (kind, symbol, currency, underlying_id, expiry, strike, put_call)
select 'option', 'META  260925C00737500', 'USD', id, date '2026-09-25', 737.5, 'C'
  from core.instrument where symbol = 'META';
insert into mart.book_position (instrument_id, symbol, kind, quantity, currency, venues,
  accounts, asset_class, direction)
select id, symbol, kind, 1, currency, array['tiger'], array['U1234567'], 'option', 'long'
  from core.instrument where kind = 'option';
insert into core.account (venue) values ('tiger');
insert into mart.venue_status (account_id, venue, state, venue_as_of, position_count, in_totals)
select id, venue, 'fresh', now(), 3, true from core.account;
"""


def _psql(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["psql", "-h", "127.0.0.1", "-p", str(PORT), "-U", "postgres", "-v", "ON_ERROR_STOP=1",
         *args],
        capture_output=True,
        text=True,
    )


@pytest.fixture(scope="module")
def cluster():
    base = Path(tempfile.mkdtemp(prefix="deskpg", dir="/tmp"))
    data = base / "data"
    subprocess.run(
        ["initdb", "-D", str(data), "-U", "postgres", "--encoding=UTF8", "--locale=C",
         "--auth=trust"],
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["pg_ctl", "-D", str(data), "-o", f"-k {base} -p {PORT} -c listen_addresses=127.0.0.1",
         "-l", str(base / "log"), "start"],
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
        done = _psql("-d", "macro", "-q", "-f", str(SHIM))
        assert done.returncode == 0, done.stderr
        done = _psql("-d", "macro", "-q", "-c", ACCOUNTING)
        assert done.returncode == 0, done.stderr
        for one in sorted(MIGRATIONS.glob("*.sql")):
            done = _psql("-d", "macro", "-q", "-f", str(one))
            assert done.returncode == 0, f"{one.name}: {done.stderr}"
        # What supabase/desk_role.py does on the live project. The cluster trusts
        # loopback, so the password is not checked here; the role's privileges are.
        assert _psql("-d", "macro", "-c", "alter role macro_desk login").returncode == 0
        yield f"postgresql://postgres@127.0.0.1:{PORT}/macro"
    finally:
        subprocess.run(["pg_ctl", "-D", str(data), "-m", "immediate", "stop"],
                       capture_output=True)
        shutil.rmtree(base, ignore_errors=True)


@pytest.fixture(scope="module")
def desk_url(cluster) -> str:
    return f"postgresql://macro_desk@127.0.0.1:{PORT}/macro"


@pytest.fixture
def pg_settings(cluster, desk_url, tmp_path) -> Settings:
    symbols = tmp_path / "symbols.yaml"
    symbols.write_text("equities: {}\ncrypto:\n  HYPE: {hyperliquid: HYPE}\n", encoding="utf-8")
    return Settings(
        database_url=cluster,
        database_ssl="disable",
        supabase_url=None,
        supabase_service_role_key=None,
        vault_path=Path("/nonexistent-vault"),
        series_yaml=CONFIG,
        predicates_md=ROOT / "config" / "predicates.md",
        worker_src=WORKER_SRC,
        log_level="WARNING",
        desk_database_url=desk_url,
        symbols_yaml=symbols,
    )


@pytest.fixture
async def admin(cluster):
    connection = await asyncpg.connect(cluster)
    await connection.execute(
        "truncate macro_core.catalyst_alias, macro_core.tail_risk_check, macro_core.tail_risk, "
        "macro_core.holding_event, macro_core.holding_watch, macro_core.catalyst, "
        "macro_core.manual_position, macro_core.watch_ticker, macro_mart.snapshot_row, "
        "macro_mart.catalyst_window, macro_quality.finding, macro_quality.ingest_run, "
        "macro_raw.batch cascade"
    )
    try:
        yield connection
    finally:
        await connection.close()


@pytest.fixture
async def store(admin, desk_url):
    found = cast(DeskStore, await DeskStore.connect(desk_url, ssl="disable"))
    try:
        yield found
    finally:
        await found.aclose()


async def counts(admin) -> dict[str, int]:
    tables = (
        "macro_core.catalyst", "macro_core.catalyst_alias", "macro_core.tail_risk",
        "macro_core.tail_risk_check", "macro_core.holding_watch", "macro_core.holding_event",
        "macro_quality.ingest_run", "macro_quality.finding",
    )
    return {t: await admin.fetchval(f"select count(*) from {t}") for t in tables}


def upsert(name="U.S. August CPI", **over):
    base = {
        "op": "upsert",
        "name": name,
        "when": {"date": "2026-10-14", "time": "08:30", "tz": "America/New_York"},
        "date_text": "2026-10-14 08:30 ET",
        "estimated": False,
        "source_url": "https://www.bls.gov/schedule/news_release/cpi.htm",
        "affected": ["rates", "USD"],
    }
    base.update(over)
    return base


async def submit(store, pg_settings, *items, now=NOW, dry_run=False):
    return await desk.catalysts_submit(
        store, pg_settings, {"items": list(items)}, now=now, dry_run=dry_run
    )


# ---------------------------------------------------------------------------
# The role
# ---------------------------------------------------------------------------


async def test_the_desk_runs_as_macro_desk(store):
    assert await store.whoami() == "macro_desk"


@pytest.mark.parametrize(
    "statement",
    [
        "select count(*) from macro_core.phase_call",
        "select count(*) from macro_core.observation",
        "select count(*) from macro_core.run_request",
        "delete from macro_core.catalyst",
        "delete from macro_core.tail_risk",
        "update macro_core.manual_position set active = false",
        "insert into macro_quality.ingest_run (source, run_kind, status) values ('fred', 'full', 'ok')",
        "insert into macro_quality.finding (check_key, subject, sentence, severity) "
        "values ('worker_thing', 's', 's', 1)",
        "select count(*) from mart.book_position",
        "select count(*) from core.instrument",
        "select macro_mart.request_refresh('because I can')",
    ],
)
async def test_macro_desk_cannot_reach_beyond_the_feeds(store, statement):
    with pytest.raises(WriteRefused):
        await store._fetch(statement)


async def test_accounting_is_read_through_the_definer_function_only(store, admin):
    rows = await store.accounting_positions()
    symbols = sorted(one["symbol"] for one in rows)
    assert symbols == ["GOOG", "HYPE", "META", "META  260925C00737500"]
    assert "accounts" not in rows[0]
    option = next(one for one in rows if one["kind"] == "option")
    assert option["underlying_symbol"] == "META"
    assert await admin.fetchval(
        "select count(*) from pg_policies where schemaname in ('mart', 'core')"
    ) == 2, "no policy was added on Accounting's tables"
    assert not await admin.fetchval(
        "select has_schema_privilege('macro_desk', 'mart', 'usage')"
    ), "no privilege was granted on Accounting's schemas"


# ---------------------------------------------------------------------------
# Catalysts
# ---------------------------------------------------------------------------


async def test_an_upsert_inserts_then_a_reschedule_updates_the_same_row(store, pg_settings, admin):
    first = await submit(store, pg_settings, upsert())
    assert first.refusals == [] and first.applied[0]["result"] == "inserted"
    row = await admin.fetchrow("select * from macro_core.catalyst")
    assert row["starts_at"] == datetime(2026, 10, 14, 12, 30, tzinfo=UTC)
    assert row["date_precision"] == "exact" and row["category"] == "us_macro"
    assert row["feed_source"] == "openclaw:macro-desk" and row["last_run_id"] == first.run_id

    moved = await submit(store, pg_settings, upsert(
        name="u.s. august  cpi", when={"date": "2026-10-15"}, date_text="2026-10-15"))
    assert moved.applied[0]["result"] == "updated"
    assert "starts_at" in moved.applied[0]["changed"]
    again = await admin.fetchrow("select * from macro_core.catalyst")
    assert again["id"] == row["id"], "a reschedule keeps the row"
    assert again["date_precision"] == "day"

    same = await submit(store, pg_settings, upsert(
        name="u.s. august  cpi", when={"date": "2026-10-15"}, date_text="2026-10-15"))
    assert same.applied[0]["result"] == "unchanged"


async def test_every_live_run_is_an_ingest_run_the_page_can_read(store, pg_settings, admin):
    reply = await submit(store, pg_settings, upsert())
    run = await admin.fetchrow("select * from macro_quality.ingest_run where id = $1",
                               reply.run_id)
    assert (run["source"], run["run_kind"], run["feed"], run["origin"], run["status"]) == (
        "desk:catalysts", "desk", "catalysts", "openclaw:macro-desk", "ok")
    fresh = await admin.fetchrow("select * from macro_mart.feed_freshness where feed = 'catalysts'")
    assert fresh["origin"] == "openclaw:macro-desk" and fresh["last_status"] == "ok"
    assert fresh["rows"] == 1
    batch = await admin.fetchrow("select * from macro_raw.batch where id = $1", reply.batch_id)
    assert batch["source"] == "desk:catalysts" and batch["shadow"] is False


async def test_a_dry_run_writes_nothing_but_its_shadow_batch(store, pg_settings, admin):
    await submit(store, pg_settings, upsert())
    before = await counts(admin)
    batches = await admin.fetchval("select count(*) from macro_raw.batch")
    reply = await submit(
        store, pg_settings,
        upsert(when={"date": "2026-10-20"}, date_text="2026-10-20"),
        upsert(name="Nvidia Q3 FY2027 earnings", when={"month": "2026-11", "part": "late"},
               date_text="late November 2026 expected", estimated=True),
        {"op": "retire", "name": "Nobody's catalyst", "reason": "it never existed at all"},
        dry_run=True,
    )
    assert reply.dry_run and reply.run_id is None
    assert [one["result"] for one in reply.applied] == ["updated", "inserted"]
    assert len(reply.refusals) == 1
    assert await counts(admin) == before, "a dry run writes no row outside macro_raw.batch"
    assert await admin.fetchval("select count(*) from macro_raw.batch") == batches + 1
    shadow = await admin.fetchrow("select * from macro_raw.batch where id = $1", reply.batch_id)
    assert shadow["shadow"] is True
    assert (await admin.fetchval("select starts_at from macro_core.catalyst")).day == 14


async def test_retire_keeps_the_row_and_its_reason_and_a_resubmission_revives_it(
    store, pg_settings, admin
):
    await submit(store, pg_settings, upsert())
    reply = await submit(store, pg_settings,
                         {"op": "retire", "name": "U.S. August CPI", "reason": "moved to November"})
    assert reply.applied[0]["result"] == "retired"
    row = await admin.fetchrow("select status, retired_reason, retired_at from macro_core.catalyst")
    assert (row["status"], row["retired_reason"], row["retired_at"]) == (
        "retired", "moved to November", TODAY)

    twice = await submit(store, pg_settings,
                         {"op": "retire", "name": "U.S. August CPI", "reason": "moved to November"})
    assert twice.refusals == [] and "already retired" in twice.warnings[0]

    back = await submit(store, pg_settings, upsert())
    assert back.applied[0]["result"] == "revived"
    assert await admin.fetchval("select status from macro_core.catalyst") == "tracked"
    assert await admin.fetchval("select retired_reason from macro_core.catalyst") is None


async def test_retiring_an_unknown_catalyst_is_refused_and_found(store, pg_settings, admin):
    reply = await submit(store, pg_settings,
                         {"op": "retire", "name": "No such event", "reason": "it has passed now"})
    assert "no catalyst is named" in reply.refusals[0]
    finding = await admin.fetchrow("select * from macro_quality.finding")
    assert finding["check_key"] == "desk_catalyst_refused"
    assert "no catalyst is named" in finding["sentence"]


async def test_a_rename_keeps_the_id_and_the_old_name_maps_to_it(store, pg_settings, admin):
    first = await submit(store, pg_settings, upsert())
    catalyst_id = first.applied[0]["id"]
    reply = await submit(store, pg_settings, {
        "op": "rename", "name": "U.S. August CPI",
        "new_name": "U.S. August Consumer Price Index", "reason": "the release's own title"})
    assert reply.refusals == [] and reply.applied[0]["result"] == "renamed"
    row = await admin.fetchrow("select id, name, external_key from macro_core.catalyst")
    assert str(row["id"]) == catalyst_id
    assert row["name"] == "U.S. August Consumer Price Index"
    alias = await admin.fetchrow("select * from macro_core.catalyst_alias")
    assert str(alias["catalyst_id"]) == catalyst_id and alias["name"] == "U.S. August CPI"

    stale = await submit(store, pg_settings, upsert())
    assert "was renamed to 'U.S. August Consumer Price Index'" in stale.refusals[0]
    assert await admin.fetchval("select count(*) from macro_core.catalyst") == 1

    by_old = await submit(store, pg_settings, {
        "op": "retire", "name": "U.S. August CPI", "reason": "released on schedule today"})
    assert by_old.applied[0]["id"] == catalyst_id, "a former name still finds the row"


async def test_a_rename_onto_another_catalyst_or_a_former_name_is_refused(
    store, pg_settings, admin
):
    await submit(store, pg_settings, upsert(), upsert(name="U.S. September CPI",
                                                      when={"date": "2026-11-12"},
                                                      date_text="2026-11-12"))
    onto = await submit(store, pg_settings, {
        "op": "rename", "name": "U.S. August CPI", "new_name": "U.S. September CPI"})
    assert "already exists" in onto.refusals[0]
    await submit(store, pg_settings, {
        "op": "rename", "name": "U.S. August CPI", "new_name": "U.S. August CPI release"})
    back = await submit(store, pg_settings, {
        "op": "rename", "name": "U.S. August CPI release", "new_name": "U.S. August CPI"})
    assert "former name" in back.refusals[0]


async def test_a_rename_then_an_upsert_of_the_new_name_in_one_run_updates_one_row(
    store, pg_settings, admin
):
    await submit(store, pg_settings, upsert())
    reply = await submit(
        store, pg_settings,
        {"op": "rename", "name": "U.S. August CPI", "new_name": "U.S. August CPI release"},
        upsert(name="U.S. August CPI release", when={"date": "2026-10-15"},
               date_text="2026-10-15"),
    )
    assert reply.refusals == []
    assert await admin.fetchval("select count(*) from macro_core.catalyst") == 1
    assert await admin.fetchval("select date_precision from macro_core.catalyst") == "day"


async def test_a_live_run_confirms_the_rest_of_the_calendar_is_still_listed(
    store, pg_settings, admin
):
    """The archive keeps an occurrence only if it was still listed within three days,
    and the desk submits changes, not the whole calendar."""
    await submit(store, pg_settings, upsert(), now=NOW - timedelta(days=2))
    reply = await submit(store, pg_settings,
                         upsert(name="Micron Q1 FY2027 earnings", when={"date": "2026-09-23"},
                                date_text="2026-09-23"))
    assert reply.extra["still_listed_confirmed"] == 1
    assert set(await admin.fetchval(
        "select array_agg(distinct last_seen) from macro_core.catalyst")) == {TODAY}


# ---------------------------------------------------------------------------
# Tail risks
# ---------------------------------------------------------------------------


def risk(**over):
    base = {
        "op": "upsert",
        "risk_id": "iran-oil-chokepoints",
        "name": "Iran-war tanker and oil-chokepoint escalation",
        "severity": "elevated",
        "summary": "Tanker attacks near Hormuz raised shipping insurance again.",
        "priced_in": "Oil has priced some of it. Stocks have not.",
        "gauges": ["vix", "brent", "not_a_series"],
        "source_url": "https://www.reuters.com/world/middle-east/x",
        "assets": ["oil", "equities"],
    }
    base.update(over)
    return base


async def risks(store, pg_settings, kind, *items, now=NOW):
    return await desk.tail_risks_submit(
        store, pg_settings, {"kind": kind, "items": list(items)}, now=now, dry_run=False
    )


async def _snapshot(admin) -> None:
    await admin.execute(
        "insert into macro_core.series (key, label, table_group, ordinal, value_kind, unit, "
        "decimals, cadence, release_rule, replayable, source_name, fetch_path, tier_floor) "
        "values ('vix', 'VIX', 'Price & Levels', 16, 'numeric', 'index_points', 2, 'session', "
        "'us_session', true, 'Yahoo', 'deterministic', 1) on conflict (key) do nothing"
    )
    series = await admin.fetchval("select id from macro_core.series where key = 'vix'")
    await admin.execute(
        "insert into macro_mart.snapshot_row (series_id, key, label, table_group, ordinal, "
        "value, display, unit, value_kind, asof, freshness) values ($1, 'vix', 'VIX', "
        "'Price & Levels', 16, 17.4, '17.40', 'index_points', 'numeric', '2026-09-18', 'fresh')",
        series,
    )


async def test_the_vault_parser_still_inserts_and_gets_a_slug(admin):
    """The worker's insert names no risk_id; the trigger derives one, deduplicated."""
    for name, key in (("Opaque private-credit leverage and insurer feedback loop", "k1"),
                      ("Opaque private-credit leverage and insurer feedback loop!", "k2")):
        await admin.execute(
            "insert into macro_core.tail_risk (key, name, severity, first_seen, last_seen) "
            "values ($1, $2, 'watch', current_date, current_date)", key, name)
    ids = [r["risk_id"] for r in await admin.fetch(
        "select risk_id from macro_core.tail_risk order by key")]
    assert ids == ["opaque-private-credit-leverage-and-insurer",
                   "opaque-private-credit-leverage-and-insurer-2"]


async def test_a_weekly_scan_writes_the_risk_and_its_check_with_our_gauges(
    store, pg_settings, admin
):
    await _snapshot(admin)
    reply = await risks(store, pg_settings, "weekly", risk())
    assert reply.refusals == [] and reply.applied[0]["result"] == "inserted"
    assert "not_a_series" in reply.warnings[0]
    row = await admin.fetchrow("select * from macro_core.tail_risk")
    assert (row["key"], row["risk_id"], row["feed_source"]) == (
        "iran-oil-chokepoints", "iran-oil-chokepoints", "openclaw:macro-desk")
    check = await admin.fetchrow("select * from macro_core.tail_risk_check")
    assert check["kind"] == "weekly" and check["run_id"] == reply.run_id
    assert '"17.4' in check["gauges"] and "brent" not in check["gauges"]
    state = await admin.fetchrow("select * from macro_mart.tail_risk_state")
    assert state["risk_id"] == "iran-oil-chokepoints" and state["last_check_kind"] == "weekly"


async def test_a_rewording_keeps_the_risk_and_its_history(store, pg_settings, admin):
    await risks(store, pg_settings, "weekly", risk())
    reply = await risks(store, pg_settings, "weekly",
                        risk(name="Hormuz tanker war", severity="acute"),
                        now=NOW + timedelta(days=7))
    assert reply.applied[0]["result"] == "escalated"
    assert await admin.fetchval("select count(*) from macro_core.tail_risk") == 1
    history = await admin.fetch(
        "select severity, name from macro_mart.tail_risk_history order by checked_at")
    assert [(r["severity"], r["name"]) for r in history] == [
        ("elevated", "Hormuz tanker war"), ("acute", "Hormuz tanker war")]
    state = await admin.fetchrow("select * from macro_mart.tail_risk_state")
    assert (state["severity"], state["previous_severity"]) == ("acute", "elevated")


async def test_a_second_id_for_the_same_named_risk_is_refused(store, pg_settings):
    await risks(store, pg_settings, "weekly", risk())
    reply = await risks(store, pg_settings, "weekly", risk(risk_id="hormuz"))
    assert "already tracked as iran-oil-chokepoints" in reply.refusals[0]


async def test_the_daily_recheck_may_not_add_or_touch_watch_level_risks(store, pg_settings, admin):
    await risks(store, pg_settings, "weekly",
                risk(risk_id="private-credit", name="Private credit stress", severity="watch"))
    new_watch = await risks(store, pg_settings, "daily",
                            risk(risk_id="new-thing", name="A new thing", severity="watch"))
    assert "does not add watch-level risks" in new_watch.refusals[0]
    stays = await risks(store, pg_settings, "daily",
                        risk(risk_id="private-credit", name=None, severity="watch"))
    assert "stays at watch" in stays.refusals[0]
    retire = await risks(store, pg_settings, "daily",
                         {"op": "retire", "risk_id": "private-credit",
                          "reason": "spreads normalised over the month"})
    assert "retired by the Monday scan" in retire.refusals[0]
    assert await admin.fetchval("select count(*) from macro_core.tail_risk") == 1


async def test_the_daily_recheck_escalates_updates_and_adds_serious_risks(
    store, pg_settings, admin
):
    await risks(store, pg_settings, "weekly",
                risk(risk_id="private-credit", name="Private credit stress", severity="watch"),
                risk())
    reply = await risks(
        store, pg_settings, "daily",
        risk(risk_id="private-credit", name=None, severity="elevated"),
        risk(severity="acute"),
        risk(risk_id="bank-run", name="A regional bank run", severity="acute"),
        now=NOW + timedelta(days=1),
    )
    assert reply.refusals == []
    assert [one["result"] for one in reply.applied] == ["escalated", "escalated", "inserted"]
    kinds = await admin.fetch("select distinct kind from macro_core.tail_risk_check")
    assert {r["kind"] for r in kinds} == {"weekly", "daily"}
    kept = await admin.fetchval(
        "select name from macro_core.tail_risk where risk_id = 'private-credit'")
    assert kept == "Private credit stress", "an update without a name keeps the name"


async def test_a_retirement_carries_its_reason_and_leaves_the_page(store, pg_settings, admin):
    await risks(store, pg_settings, "weekly", risk())
    reply = await risks(store, pg_settings, "daily",
                        {"op": "retire", "risk_id": "iran-oil-chokepoints",
                         "reason": "the ceasefire has held for two weeks"},
                        now=NOW + timedelta(days=1))
    assert reply.applied[0]["result"] == "retired"
    row = await admin.fetchrow("select retired_on, retired_reason from macro_core.tail_risk")
    assert row["retired_reason"] == "the ceasefire has held for two weeks"
    assert await admin.fetchval("select count(*) from macro_mart.tail_risk_state") == 0
    assert await admin.fetchval(
        "select retired from macro_core.tail_risk_check order by checked_at desc limit 1")


# ---------------------------------------------------------------------------
# Positions and the holdings watch
# ---------------------------------------------------------------------------


def fake_quotes(table):
    async def quotes(routes, *, http, clock, now):
        out = {}
        for one in routes:
            symbol = one.yahoo or one.hyperliquid or one.symbol
            change = table.get(symbol)
            out[one.key] = prices.Quote(
                market_symbol=symbol,
                price=Decimal("100"),
                price_at=now,
                currency="USD",
                change_pct=None if change is None else Decimal(change),
                basis=None if change is None else (
                    "rolling_24h" if one.hyperliquid else "close_to_close"),
                session=None,
                source=f"test ({symbol})",
                reason=None if change is not None else "no test price",
                underlying_only=one.underlying_only,
            )
        return out

    return quotes


@pytest.fixture
def market(monkeypatch):
    table = {"GOOG": "-1.41", "META": "0.54", "HYPE": "-3.54", "012330.KS": "-1.47"}
    monkeypatch.setattr(prices, "quotes", fake_quotes(table))
    return table


async def test_positions_list_open_accounting_names_and_the_two_lists(
    store, pg_settings, admin, market
):
    await admin.execute(
        "insert into macro_core.manual_position (symbol, name, venue, asset_class) values "
        "('012330.KS', 'Hyundai Mobis', 'Samsung Securities', 'equity')")
    await admin.execute(
        "insert into macro_core.watch_ticker (symbol, name, asset_class) values "
        "('NVDA', 'Nvidia', 'equity')")
    found = await desk.positions(store, pg_settings, now=NOW)
    public = desk.public_positions(found, batch_id=1)
    by = {(one["symbol"], one["source"]): one for one in public["positions"]}
    assert set(by) == {("GOOG", "accounting"), ("META", "accounting"),
                       ("HYPE", "accounting"), ("META 260925C00737500", "accounting"),
                       ("012330.KS", "manual"), ("NVDA", "watch")}
    assert "USD" not in {one["symbol"] for one in public["positions"]}, "cash is not a position"
    assert "USDE" not in {one["symbol"] for one in public["positions"]}, "closed is not open"
    assert by[("META 260925C00737500", "accounting")]["underlying_only"] is True
    assert by[("HYPE", "accounting")]["change_basis"] == "rolling_24h"
    assert by[("NVDA", "watch")]["change_24h_pct"] is None
    assert by[("NVDA", "watch")]["unavailable_reason"] == "no test price"
    assert "quantity" not in by[("GOOG", "accounting")], "size stays out of the model's view"
    assert public["counts"] == {"accounting": 4, "manual": 1, "watch": 1,
                                "change_unavailable": 1}
    assert public["accounting"]["ok"] and public["accounting"]["venues"][0]["venue"] == "tiger"


#: The fields whose digits are market data or instrument names, not Accounting's words.
MARKET_FIELDS = frozenset({"symbol", "market_symbol", "underlying", "price", "change_24h_pct"})


def _texts(value, key=None):
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _texts(v, k)
    elif isinstance(value, list):
        for v in value:
            yield from _texts(v, key)
    elif isinstance(value, str) and key not in MARKET_FIELDS:
        yield key, value


async def test_positions_never_show_an_account_number_or_a_per_account_row(
    store, pg_settings, admin, market
):
    """Accounting's venue reasons name accounts; the agent's view (and Telegram) may not."""
    await admin.execute("""
        with a as (insert into core.account (venue) values ('tiger'), ('hyperliquid')
                   returning id, venue)
        insert into mart.venue_status (account_id, venue, state, reason, venue_as_of,
                                       position_count, in_totals)
        select id, venue, 'fresh', case venue
            when 'tiger' then 'Tiger account 50400376 holds cash only, so it has no price that '
                              || 'could be out of date. It is answering normally.'
            else 'Hyperliquid sub-account 0x8c1f2e3d4b5a69788766554433221100aabbccdd is idle'
          end, now(), 0, true
          from a""")
    await admin.execute(
        "update mart.book_position set name = 'Meta Platforms (acct U50400376)' "
        "where symbol = 'META'")
    try:
        found = await desk.positions(store, pg_settings, now=NOW)
        public = desk.public_positions(found, batch_id=1)
    finally:
        await admin.execute(
            "delete from mart.venue_status where reason is not null; "
            "delete from core.account where id not in "
            "(select account_id from mart.venue_status); "
            "update mart.book_position set name = symbol || ' Inc' where symbol = 'META'")
    venues = public["accounting"]["venues"]
    assert sorted(one["venue"] for one in venues) == ["hyperliquid", "tiger"]
    tiger = next(one for one in venues if one["venue"] == "tiger")
    assert tiger["accounts"] == 2
    # Redacted inside Accounting's function now, before the desk's own pass sees it.
    assert tiger["reasons"][0].startswith("Tiger account holds cash only")
    stamp = re.compile(r"\d{4}-\d{2}-\d{2}T[\d:.]+(?:Z|[+-]\d{2}:\d{2})?")  # microseconds
    leaks = [
        (k, v) for k, v in _texts(public)
        if re.search(r"\d{6,}|0x[0-9a-fA-F]{6,}", stamp.sub("", v))
    ]
    assert leaks == []
    assert not re.search(r"50400376|U50400376|0x8c1f", json.dumps(found, default=str)), (
        "the archived batch is clean too"
    )


async def test_holdings_never_take_a_price_from_the_model(store, pg_settings, admin, market):
    reply = await desk.holdings_submit(store, pg_settings, {"items": [{
        "symbol": "GOOG", "change_24h_pct": "+12.0", "price": 1,
        "driver_summary": "Alphabet fell after a court ruling on search deals.",
        "material": True, "source_url": "https://www.reuters.com/technology/x",
    }]}, now=NOW, dry_run=False)
    assert reply.refusals == []
    assert any("computed by the desk" in one for one in reply.warnings)
    row = await admin.fetchrow(
        "select * from macro_mart.holding_watch_state where symbol = 'GOOG'")
    assert row["change_24h_pct"] == Decimal("-1.41")
    assert row["change_basis"] == "close_to_close"
    assert row["material"] and row["driver_summary"].startswith("Alphabet")
    assert row["quantity"] == Decimal("10")
    assert await admin.fetchval("select count(*) from macro_mart.holding_watch_state") == 4
    unmentioned = await admin.fetchrow(
        "select * from macro_mart.holding_watch_state where symbol = 'META'")
    assert unmentioned["driver_summary"] is None and not unmentioned["material"]


async def test_a_resend_updates_only_the_names_it_carries(store, pg_settings, admin, market):
    """A resend with only the fixed item keeps every other name's driver for the session."""
    goog = {"symbol": "GOOG", "driver_summary": "Alphabet fell after a court ruling.",
            "material": True, "source_url": "https://www.reuters.com/technology/x"}
    meta = {"symbol": "META", "driver_summary": "Meta rose on an ad pricing note.",
            "material": True, "source_url": "https://www.reuters.com/technology/y"}
    first = await desk.holdings_submit(store, pg_settings, {"items": [goog, meta]},
                                       now=NOW, dry_run=False)
    assert first.refusals == []
    market["GOOG"] = "-1.50"  # the resend computes its own prices
    fixed = goog | {"driver_summary": "Alphabet fell after a court limited its search deals."}
    again = await desk.holdings_submit(store, pg_settings, {"items": [fixed]},
                                       now=NOW + timedelta(minutes=10), dry_run=False)
    assert again.refusals == []
    rows = {r["symbol"]: r for r in await admin.fetch(
        "select * from macro_core.holding_watch where as_of = $1", TODAY)}
    assert rows["GOOG"]["driver_summary"].endswith("its search deals.")
    assert rows["GOOG"]["change_24h_pct"] == Decimal("-1.50")
    assert rows["META"]["driver_summary"] == meta["driver_summary"], "kept, not erased"
    assert rows["META"]["material"] and rows["META"]["source_url"].endswith("/y")
    assert rows["META"]["run_id"] == first.run_id, "a kept row is not rewritten at all"
    assert len(rows) == 4
    assert {one["symbol"] for one in again.applied} == {"GOOG"}
    assert set(again.extra["kept"]) == {"META", "HYPE", "META 260925C00737500"}


async def test_a_name_with_no_row_yet_gets_a_quiet_one(store, pg_settings, admin, market):
    await desk.holdings_submit(store, pg_settings, {"items": [{"symbol": "GOOG"}]},
                               now=NOW, dry_run=False)
    await admin.execute(
        "insert into macro_core.watch_ticker (symbol, name, asset_class) values "
        "('NVDA', 'Nvidia', 'equity')")
    reply = await desk.holdings_submit(store, pg_settings, {"items": []},
                                       now=NOW + timedelta(minutes=10), dry_run=False)
    assert {one["symbol"] for one in reply.applied} == {"NVDA"}
    quiet = await admin.fetchrow(
        "select * from macro_core.holding_watch where symbol = 'NVDA' and as_of = $1", TODAY)
    assert quiet["driver_summary"] is None and not quiet["material"]
    assert await admin.fetchval("select count(*) from macro_core.holding_watch") == 5


async def test_holdings_reuse_the_positions_the_agent_was_shown(store, pg_settings, admin, market):
    found = await desk.positions(store, pg_settings, now=NOW)
    batch_id = await store.archive("positions", "positions", found, at=NOW, shadow=False)
    market["GOOG"] = "5.00"  # the market moved after the agent read its positions
    reply = await desk.holdings_submit(store, pg_settings, {
        "positions_batch_id": batch_id, "items": []}, now=NOW + timedelta(minutes=5),
        dry_run=False)
    assert reply.extra["positions_from"] == f"batch {batch_id}"
    assert await admin.fetchval(
        "select change_24h_pct from macro_core.holding_watch where symbol = 'GOOG'"
    ) == Decimal("-1.41")


async def test_a_name_that_is_not_watched_is_refused(store, pg_settings, market):
    reply = await desk.holdings_submit(store, pg_settings, {"items": [{"symbol": "TSLA"}]},
                                       now=NOW, dry_run=False)
    assert "not a watched name today" in reply.refusals[0]


async def test_upcoming_earnings_land_on_the_calendar_once(store, pg_settings, admin, market):
    event = {"kind": "earnings", "name": "Alphabet Q3 2026 earnings",
             "when": {"date": "2026-09-29", "time": "16:05", "tz": "America/New_York"},
             "estimated": False, "source_url": "https://abc.xyz/investor/"}
    first = await desk.holdings_submit(store, pg_settings, {"items": [{
        "symbol": "GOOG", "events": [event]}]}, now=NOW, dry_run=False)
    assert first.refusals == []
    catalyst = await admin.fetchrow("select * from macro_core.catalyst")
    assert catalyst["name"] == "Alphabet Q3 2026 earnings (GOOG)"
    assert catalyst["category"] == "earnings" and catalyst["affected"] == ["GOOG"]
    held = await admin.fetchrow("select * from macro_core.holding_event")
    assert held["catalyst_id"] == catalyst["id"]
    assert await admin.fetchval("select count(*) from macro_mart.holding_event_state") == 1

    again = await desk.holdings_submit(store, pg_settings, {"items": [{
        "symbol": "GOOG", "events": [event | {"name": "GOOG Q3 earnings call"}]}]},
        now=NOW, dry_run=False)
    assert again.refusals == []
    assert await admin.fetchval("select count(*) from macro_core.catalyst") == 1, (
        "a second name for the same print within a week is the same catalyst")


async def test_a_firm_earnings_date_replaces_the_calendars_guess(store, pg_settings, admin, market):
    await submit(store, pg_settings, upsert(
        name="Alphabet Q3 2026 earnings", category="earnings",
        when={"month": "2026-09", "part": "late"}, date_text="late September 2026 expected",
        estimated=True, source_url="https://abc.xyz/investor/"))
    await desk.holdings_submit(store, pg_settings, {"items": [{"symbol": "GOOG", "events": [{
        "kind": "earnings", "name": "Alphabet Q3 2026 earnings", "when": {"date": "2026-09-29"},
        "estimated": False, "source_url": "https://abc.xyz/investor/"}]}]},
        now=NOW, dry_run=False)
    row = await admin.fetchrow("select * from macro_core.catalyst")
    assert (row["date_precision"], row["date_estimated"]) == ("day", False)
    assert await admin.fetchval("select count(*) from macro_core.catalyst") == 1


async def test_a_holdings_dry_run_writes_only_its_shadow_batch(store, pg_settings, admin, market):
    before = await counts(admin)
    reply = await desk.holdings_submit(store, pg_settings, {"items": [{"symbol": "GOOG",
        "events": [{"kind": "earnings", "name": "Alphabet Q3 2026 earnings",
                    "when": {"date": "2026-09-29"}, "estimated": False,
                    "source_url": "https://abc.xyz/investor/"}]}]}, now=NOW, dry_run=True)
    assert reply.refusals == [] and reply.dry_run
    assert await counts(admin) == before
    assert await admin.fetchval(
        "select shadow from macro_raw.batch where id = $1", reply.batch_id) is True


# ---------------------------------------------------------------------------
# The operator's two lists, through the web's functions
# ---------------------------------------------------------------------------


async def test_the_lists_are_edited_only_by_a_signed_in_operator(admin):
    async with admin.transaction():
        await admin.execute("set local role authenticated")
        with pytest.raises(asyncpg.RaiseError, match="signed-in operator"):
            await admin.fetchval(
                "select macro_mart.save_watch_ticker(null, 'nvda', 'Nvidia', 'equity', null)")
    async with admin.transaction():
        await admin.execute(
            "select set_config('request.jwt.claim.sub', '00000000-0000-0000-0000-000000000001', "
            "true)")
        await admin.execute("set local role authenticated")
        watch_id = await admin.fetchval(
            "select macro_mart.save_watch_ticker(null, ' nvda ', 'Nvidia', 'equity', null)")
        position_id = await admin.fetchval(
            "select macro_mart.save_manual_position(null, 'D05', 'DBS', 'DBS Vickers', "
            "'equity', 100, null)")
        rows = await admin.fetch("select symbol from macro_mart.watch_ticker_state")
        assert [r["symbol"] for r in rows] == ["NVDA"]
        await admin.execute("select macro_mart.retire_manual_position($1)", position_id)
        assert await admin.fetchval("select count(*) from macro_mart.manual_position_state") == 0
        with pytest.raises(asyncpg.PostgresError):
            await admin.execute("delete from macro_core.watch_ticker where id = $1", watch_id)


# ---------------------------------------------------------------------------
# Review of 2026-09-25
# ---------------------------------------------------------------------------


async def test_a_rename_by_id_beside_an_upsert_by_the_old_name_is_refused(
    store, pg_settings, admin
):
    """Keyed on the id for one and the name for the other, both passed and the
    upsert inserted a second row under the old name."""
    first = await submit(store, pg_settings, upsert())
    catalyst_id = first.applied[0]["id"]
    reply = await submit(
        store, pg_settings,
        {"op": "rename", "id": catalyst_id, "new_name": "U.S. August CPI release"},
        upsert(when={"date": "2026-10-15"}, date_text="2026-10-15"),
    )
    assert len(reply.refusals) == 2 and all("also touch" in one for one in reply.refusals)
    assert reply.applied == []
    assert await admin.fetchval("select count(*) from macro_core.catalyst") == 1


async def test_a_retire_by_name_beside_a_rename_by_id_is_refused(store, pg_settings, admin):
    first = await submit(store, pg_settings, upsert())
    reply = await submit(
        store, pg_settings,
        {"op": "rename", "id": first.applied[0]["id"], "new_name": "U.S. August CPI release"},
        {"op": "retire", "name": "U.S. August CPI", "reason": "released on schedule today"},
    )
    assert len(reply.refusals) == 2
    row = await admin.fetchrow("select name, status from macro_core.catalyst")
    assert (row["name"], row["status"]) == ("U.S. August CPI", "tracked")


async def test_a_rename_by_former_name_beside_an_upsert_by_current_name_is_refused(
    store, pg_settings, admin
):
    await submit(store, pg_settings, upsert())
    await submit(store, pg_settings, {"op": "rename", "name": "U.S. August CPI",
                                      "new_name": "U.S. August CPI release"})
    reply = await submit(
        store, pg_settings,
        {"op": "rename", "name": "U.S. August CPI", "new_name": "U.S. August CPI print"},
        upsert(name="U.S. August CPI release", when={"date": "2026-10-15"},
               date_text="2026-10-15"),
    )
    assert len(reply.refusals) == 2
    assert await admin.fetchval("select count(*) from macro_core.catalyst") == 1
    assert await admin.fetchval("select name from macro_core.catalyst") == (
        "U.S. August CPI release")


async def test_two_renames_onto_one_new_name_refuse_the_second(store, pg_settings, admin):
    """Both passed validation and the second hit uq on external_key: the whole
    submission aborted with UniqueViolation."""
    await submit(store, pg_settings, upsert(), upsert(name="U.S. September CPI",
                                                      when={"date": "2026-11-12"},
                                                      date_text="2026-11-12"))
    reply = await submit(
        store, pg_settings,
        {"op": "rename", "name": "U.S. August CPI", "new_name": "U.S. CPI"},
        {"op": "rename", "name": "U.S. September CPI", "new_name": "u.s.  cpi"},
    )
    assert [one["index"] for one in reply.applied] == [0]
    assert len(reply.refusals) == 1 and "items[0] already renames" in reply.refusals[0]
    names = sorted(r["name"] for r in await admin.fetch("select name from macro_core.catalyst"))
    assert names == ["U.S. CPI", "U.S. September CPI"]


async def test_an_update_without_a_category_keeps_the_stored_one(store, pg_settings, admin):
    await submit(store, pg_settings, upsert(name="Micron Q1 FY2027 earnings",
                                            category="us_macro"))
    reply = await submit(store, pg_settings, upsert(name="Micron Q1 FY2027 earnings",
                                                    when={"date": "2026-10-15"},
                                                    date_text="2026-10-15"))
    assert reply.applied[0]["category"] == "us_macro"
    assert "category" not in reply.applied[0]["changed"]
    assert await admin.fetchval("select category from macro_core.catalyst") == "us_macro"


async def test_a_live_run_confirms_rows_the_vault_wrote(store, pg_settings, admin):
    """The desk may update only rows it writes as the desk; confirming the vault's
    rows goes through the definer function."""
    await admin.execute(
        "insert into macro_core.catalyst (external_key, name, date_precision, status, "
        "first_seen, last_seen, starts_at, category) values ('vault-row', 'FOMC decision', "
        "'day', 'tracked', $1, $1, '2026-10-28', 'us_macro')", TODAY - timedelta(days=3))
    reply = await submit(store, pg_settings, upsert())
    assert reply.refusals == [] and reply.extra["still_listed_confirmed"] == 1
    row = await admin.fetchrow("select last_seen, feed_source from macro_core.catalyst "
                               "where external_key = 'vault-row'")
    assert (row["last_seen"], row["feed_source"]) == (TODAY, "vault")


def _catalyst_rows(n: int) -> list[tuple]:
    return [
        (f"key-{i}", f"Company number {i:02d} fiscal Q3 2026 earnings release",
         NOW + timedelta(days=i % 40, hours=13), "exact", i % 3 == 0,
         f"{NOW.date() + timedelta(days=i % 40)} 21:00 SGT",
         f"https://investor.example-company-{i:02d}.com/news/q3-2026-results-date",
         ["equities", f"TICK{i:02d}"])
        for i in range(n)
    ]


async def test_the_catalyst_list_is_compact_filtered_and_under_twelve_thousand_characters(
    store, admin
):
    """OpenClaw cuts a tool result at 16,000 characters; 72 rows printed 62,000."""
    await admin.executemany(
        "insert into macro_core.catalyst (external_key, name, starts_at, date_precision, "
        "date_estimated, date_text, source_url, affected, status, first_seen, last_seen, "
        "category, family) values ($1,$2,$3,$4,$5,$6,$7,$8,'tracked',$9,$9,'earnings',"
        "'earnings')",
        [(*one, TODAY) for one in _catalyst_rows(80)],
    )
    await admin.execute(
        "insert into macro_core.catalyst (external_key, name, starts_at, date_precision, "
        "status, first_seen, last_seen, category) values "
        "('far', 'Far away summit', $1, 'day', 'tracked', $2, $2, 'global_macro'), "
        "('undated', 'Undated tariff ruling', null, 'unknown', 'tracked', $2, $2, 'us_macro')",
        NOW + timedelta(days=90), TODAY)
    found = await desk.catalysts_list(store, status="tracked", now=NOW)
    text = json.dumps(found, separators=(",", ":"), default=str)
    assert len(text) < 12_000, len(text)
    assert found["count"] == 81 and found["total"] == 82, "undated kept, 90 days out left"
    row = dict(zip(found["columns"], found["catalysts"][0], strict=True))
    assert set(row) == {"id", "name", "category", "starts_at", "ends_at", "date_precision",
                        "date_estimated"}
    assert row["starts_at"].endswith("Z") and len(row["starts_at"]) == 17
    names = [one[1] for one in found["catalysts"]]
    assert "Undated tariff ruling" in names and "Far away summit" not in names

    hit = await desk.catalysts_list(store, status="tracked", search="FAR AWAY", now=NOW)
    assert [one[1] for one in hit["catalysts"]] == ["Far away summit"]
    full = await desk.catalysts_list(store, status="tracked", search="number 07", full=True,
                                     now=NOW)
    wide = dict(zip(full["columns"], full["catalysts"][0], strict=True))
    assert wide["source_url"].startswith("https://") and wide["affected"] == ["equities",
                                                                               "TICK07"]
    week = await desk.catalysts_list(store, status="tracked", within_days=7, now=NOW)
    assert week["count"] < found["count"]


async def test_the_other_lists_stay_under_twelve_thousand_characters(
    store, pg_settings, admin, market
):
    # Eight live risks, each with a longer summary and priced-in read than any of the
    # five live on 2026-09-25 (which print 4,500 characters together).
    for n in range(8):
        await admin.execute(
            "insert into macro_core.tail_risk (key, risk_id, name, severity, summary, "
            "priced_in, assets, source_url, first_seen, last_seen) values ($1, $2, $3, "
            "'elevated', $4, $5, $6, $7, $8, $8)",
            f"k{n}", f"risk-number-{n}", f"A tail risk with a realistic name number {n}",
            "x" * 450, "y" * 300, ["oil", "equities", "credit"],
            "https://www.reuters.com/world/some-long-article-path-2026-09-24/", TODAY)
        for d in range(6):
            await admin.execute(
                "insert into macro_core.tail_risk_check (risk_id, checked_at, kind, severity) "
                "values ($1, $2, 'daily', 'elevated')", f"risk-number-{n}",
                NOW - timedelta(days=d))
    risks_json = json.dumps(await desk.tail_risks_list(store, include_retired=True),
                            separators=(",", ":"), default=str)
    assert len(risks_json) < 12_000, len(risks_json)
    for n in range(10):
        await admin.execute(
            "insert into macro_core.watch_ticker (symbol, name, asset_class) values ($1, $2, "
            "'equity')", f"W{n}", f"Watched company number {n}")
    found = await desk.positions(store, pg_settings, now=NOW)
    positions_json = json.dumps(desk.public_positions(found, batch_id=1),
                                separators=(",", ":"), default=str)
    assert len(found["rows"]) == 14
    assert len(positions_json) < 12_000, len(positions_json)


async def test_a_name_covered_twice_in_one_submission_is_refused(
    store, pg_settings, admin, market
):
    """{GOOG} and {GOOG, source: accounting} both passed and the second overwrote the first."""
    reply = await desk.holdings_submit(store, pg_settings, {"items": [
        {"symbol": "GOOG", "driver_summary": "Alphabet fell on a court ruling.",
         "material": True, "source_url": "https://www.reuters.com/technology/x"},
        {"symbol": "GOOG", "source": "accounting", "driver_summary": "Alphabet rose.",
         "material": True, "source_url": "https://www.reuters.com/technology/y"},
        {"symbol": "META", "driver_summary": "Meta rose on an ad note.", "material": True,
         "source_url": "https://www.reuters.com/technology/z"},
    ]}, now=NOW, dry_run=False)
    assert len(reply.refusals) == 2
    assert all("covers the same accounting position (GOOG)" in one for one in reply.refusals)
    rows = {r["symbol"]: r for r in await admin.fetch("select * from macro_core.holding_watch")}
    assert rows["GOOG"]["driver_summary"] is None, "a quiet row, not either driver"
    assert rows["META"]["material"]


async def test_a_material_move_with_no_clear_reason_is_stored_as_such(
    store, pg_settings, admin, market
):
    reply = await desk.holdings_submit(store, pg_settings, {"items": [
        {"symbol": "HYPE", "material": True, "no_clear_reason": True}]},
        now=NOW, dry_run=False)
    assert reply.refusals == [] and reply.applied[0]["no_clear_reason"] is True
    row = await admin.fetchrow(
        "select * from macro_mart.holding_watch_state where symbol = 'HYPE'")
    assert row["material"] and row["no_clear_reason"]
    assert row["driver_summary"] is None and row["source_url"] is None


async def test_a_batch_id_out_of_range_is_recomputed_not_a_crash(store, pg_settings, market):
    for wanted in (2**70, -5, True, "12abc", "9" * 25):
        reply = await desk.holdings_submit(store, pg_settings, {
            "positions_batch_id": wanted, "items": []}, now=NOW, dry_run=True)
        assert reply.extra["positions_from"] == "computed for this submission", wanted
        assert any("positions_batch_id" in one for one in reply.warnings)


async def test_a_control_character_is_refused_and_the_submission_archived(
    store, pg_settings, admin
):
    reply = await submit(store, pg_settings, upsert(name="U.S. August\u0000 CPI"),
                         upsert(name="U.S. September CPI", when={"date": "2026-11-12"},
                                date_text="2026-11-12"))
    assert len(reply.refusals) == 1 and "control character" in reply.refusals[0]
    assert [one["name"] for one in reply.applied] == ["U.S. September CPI"]
    archived = await admin.fetchval("select payload::text from macro_raw.batch where id = $1",
                                    reply.batch_id)
    assert "\\u0000" not in archived and "�" in archived


async def test_the_two_accounting_reads_fail_separately(store, pg_settings, admin, market):
    await admin.execute("alter table mart.venue_status rename to venue_status_away")
    try:
        found = await desk.positions(store, pg_settings, now=NOW)
    finally:
        await admin.execute("alter table mart.venue_status_away rename to venue_status")
    accounting = found["accounting"]
    assert accounting["ok"] and accounting["error"] is None
    assert "venue status could not be read" in accounting["venues_error"]
    assert "positions are still Accounting's" in accounting["venues_error"]
    assert sum(1 for one in found["rows"] if one["source"] == "accounting") == 4

    await admin.execute("alter table mart.book_position rename to book_position_away")
    try:
        found = await desk.positions(store, pg_settings, now=NOW)
    finally:
        await admin.execute("alter table mart.book_position_away rename to book_position")
    assert not found["accounting"]["ok"]
    assert "positions could not be read" in found["accounting"]["error"]
    assert found["accounting"]["venues"][0]["venue"] == "tiger"


# ---------------------------------------------------------------------------
# The role, after 20260925000200
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "statement",
    [
        "insert into macro_quality.ingest_run (source, run_kind, status, started_at, origin) "
        "values ('desk:catalysts', 'desk', 'running', now(), 'someone-else')",
        "insert into macro_quality.finding (check_key, subject, sentence, severity) "
        "values ('deskXworker_thing', 's', 's', 1)",
        "insert into macro_core.catalyst (external_key, name, date_precision, status, "
        "first_seen, last_seen, feed_source) values ('k', 'name', 'day', 'tracked', "
        "current_date, current_date, 'vault')",
        "insert into macro_core.tail_risk (key, risk_id, name, severity, first_seen, last_seen, "
        "feed_source) values ('k', 'some-risk', 'name', 'watch', current_date, current_date, "
        "'vault')",
    ],
)
async def test_macro_desk_writes_only_as_the_desk(store, statement):
    with pytest.raises(WriteRefused):
        await store._fetch(statement)


async def test_macro_desk_cannot_relabel_a_vault_row_or_see_other_runs(store, admin):
    await admin.execute(
        "insert into macro_core.catalyst (external_key, name, date_precision, status, "
        "first_seen, last_seen) values ('vault-row', 'FOMC', 'day', 'tracked', current_date, "
        "current_date)")
    await admin.execute(
        "insert into macro_quality.ingest_run (source, run_kind, status) values "
        "('fred', 'full', 'ok')")
    with pytest.raises(WriteRefused):
        await store._fetch("update macro_core.catalyst set name = 'FOMC decision' "
                           "where external_key = 'vault-row'")
    assert (await store._fetch("select count(*) as n from macro_quality.ingest_run"))[0]["n"] == 0


async def test_macro_desk_sessions_time_out(admin):
    config = await admin.fetchval("select rolconfig from pg_roles where rolname = 'macro_desk'")
    assert {"statement_timeout=30s", "idle_in_transaction_session_timeout=60s",
            "lock_timeout=10s"} <= set(config)


@pytest.mark.parametrize(
    ("statement", "constraint"),
    [
        ("insert into macro_core.holding_event (symbol, kind, name, name_key, starts_at, "
         "ends_at, date_precision, source_url, first_seen, last_seen) values ('GOOG', 'other', "
         "'x', 'x', '2026-10-02', '2026-10-01', 'window', 'https://a.b/', current_date, "
         "current_date)", "ck_macro_holding_event_span"),
        ("insert into macro_core.holding_event (symbol, kind, name, name_key, date_precision, "
         "source_url, first_seen, last_seen) values ('GOOG', 'other', 'x', 'x', 'unknown', "
         "'https://a.b/', current_date, current_date - 1)", "ck_macro_holding_event_seen"),
        ("insert into macro_core.manual_position (symbol, venue, asset_class, quantity) "
         "values ('D05', 'DBS', 'equity', -1)", "ck_macro_manual_position_quantity"),
        ("insert into macro_core.catalyst (external_key, name, date_precision, status, "
         "first_seen, last_seen, source_url) values ('k', 'n', 'day', 'tracked', current_date, "
         "current_date, 'javascript:alert(1)')", "ck_macro_catalyst_source_url"),
        ("insert into macro_core.holding_watch (as_of, symbol, source, unavailable_reason, "
         "source_url) values (current_date, 'GOOG', 'watch', 'x', 'ftp://a.b/c')",
         "ck_macro_holding_watch_source_url"),
    ],
)
async def test_the_new_checks_bite(admin, statement, constraint):
    with pytest.raises(asyncpg.CheckViolationError, match=constraint):
        await admin.execute(statement)


async def test_an_empty_source_url_from_the_vault_is_still_allowed(admin):
    await admin.execute(
        "insert into macro_core.catalyst (external_key, name, date_precision, status, "
        "first_seen, last_seen, source_url) values ('k', 'n', 'day', 'tracked', current_date, "
        "current_date, '')")


@pytest.mark.parametrize(
    "name",
    [
        # 44th character of the slug is a hyphen: the re-listing's suffix made '--2'.
        "Sovereign debt ceiling brinkmanship trigger Treasury bill panic",
        # 48th character is a hyphen: the cut itself failed the check.
        "Internationally coordinated sovereign liquidity withdrawal cascade",
        # Under three characters.
        "AI",
    ],
)
async def test_the_vault_parser_can_list_and_relist_any_risk_name(admin, name):
    upsert_sql = (
        "insert into macro_core.tail_risk (key, name, severity, first_seen, last_seen) "
        "values ($1, $2, 'watch', current_date, current_date) "
        "on conflict (key) do update set severity = excluded.severity")
    await admin.execute(upsert_sql, "k", name)
    first = await admin.fetchval("select risk_id from macro_core.tail_risk where key = 'k'")
    await admin.execute(upsert_sql, "k", name)
    assert await admin.fetchval(
        "select risk_id from macro_core.tail_risk where key = 'k'") == first
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", first) and 3 <= len(first) <= 48


async def test_the_list_functions_explain_a_refusal_in_words(admin):
    """The web shows these messages verbatim; before, they named an index or a constraint."""
    async def refused(sql: str, *args: object) -> str:
        with pytest.raises(asyncpg.RaiseError) as caught:
            async with admin.transaction():  # a savepoint: the outer transaction goes on
                await admin.fetchval(sql, *args)
        return str(caught.value)

    async with admin.transaction():
        await admin.execute(
            "select set_config('request.jwt.claim.sub', '00000000-0000-0000-0000-000000000001', "
            "true)")
        await admin.execute("set local role authenticated")
        await admin.fetchval(
            "select macro_mart.save_watch_ticker(null, 'NVDA', 'Nvidia', 'equity', null)")
        assert "NVDA is already on the watch list" in await refused(
            "select macro_mart.save_watch_ticker(null, ' nvda', null, 'equity', null)")
        await admin.fetchval("select macro_mart.save_manual_position(null, 'D05', 'DBS', "
                             "'DBS Vickers', 'equity', 100, null)")
        assert "D05 at dbs vickers is already on the manual list" in await refused(
            "select macro_mart.save_manual_position(null, 'd05', null, 'dbs vickers', "
            "'equity', null, null)")
        assert "at most 40 characters" in await refused(
            "select macro_mart.save_manual_position(null, 'D05', null, $1, 'equity', null, "
            "null)", "x" * 41)
        assert "zero or more" in await refused(
            "select macro_mart.save_manual_position(null, 'SPY', null, 'DBS', 'equity', -3, "
            "null)")
        assert "asset class is one of" in await refused(
            "select macro_mart.save_watch_ticker(null, 'SPY', null, 'stock', null)")


async def test_accounting_venue_reasons_leave_the_function_redacted(store, admin):
    await admin.execute("""
        with a as (insert into core.account (venue) values ('tiger') returning id, venue)
        insert into mart.venue_status (account_id, venue, state, reason, position_count)
        select id, venue, 'fresh', 'Tiger account 50400376 is fine; wallet 0x8c1f2e3d4b5a69 '
               || 'and ref 1234567 too', 0 from a""")
    try:
        rows = await store._fetch("select * from macro_core.accounting_venue_status()")
    finally:
        await admin.execute(
            "delete from mart.venue_status where reason is not null; "
            "delete from core.account where id not in (select account_id from mart.venue_status)")
    said = [str(v) for one in rows for k, v in dict(one).items()
            if k in ("venue", "state", "reason") and v is not None]
    assert said and not any(re.search(r"\d{5,}|0x[0-9a-fA-F]{6,}", one) for one in said)
    assert "Tiger account is fine" in " ".join(said)


# ---------------------------------------------------------------------------
# The command line: one JSON object and a telling exit code, whatever happens
# ---------------------------------------------------------------------------

AGENT_SRC = ROOT / "agent" / "src"


def desk_cli(*argv: str, env: dict[str, str], stdin: str = "") -> tuple[int, dict, str]:
    """`macro-agent desk ...` in a child process, as the OpenClaw plugin runs it.

    Both URLs are always set, blank when a case wants them absent, so the
    repository's own `.env` (which python-dotenv would otherwise load) is never read.
    """
    child = {
        **{k: v for k, v in os.environ.items() if k not in ("DATABASE_URL",
                                                            "MACRO_DESK_DATABASE_URL")},
        "PYTHONPATH": str(AGENT_SRC), "WORKER_SRC": str(WORKER_SRC), "DATABASE_SSL": "disable",
        "DATABASE_URL": "", "MACRO_DESK_DATABASE_URL": "", "LOG_LEVEL": "WARNING", **env,
    }
    done = subprocess.run(
        [sys.executable, "-c", "from macro_agent.cli import run; run()", "desk", *argv],
        input=stdin, capture_output=True, text=True, env=child, cwd=str(ROOT), timeout=120,
    )
    lines = done.stdout.strip().splitlines()
    assert len(lines) == 1, f"one compact JSON line, got {done.stdout!r} / {done.stderr!r}"
    return done.returncode, json.loads(lines[0]), done.stderr


def assert_safe(reply: dict, *secrets: str) -> None:
    text = json.dumps(reply)
    for secret in ("127.0.0.1", "macro_desk@", "Traceback", str(ROOT), *secrets):
        assert secret not in text, secret
    assert reply["ok"] is False and reply["applied"] == [] and len(reply["refusals"]) == 1


def test_without_the_desk_login_a_tool_call_exits_2(cluster):
    code, reply, _ = desk_cli("context", env={"DATABASE_URL": cluster})
    assert code == 2 and reply["error"] == {"category": "environment", "exit": 2}
    assert "MACRO_DESK_DATABASE_URL is not set" in reply["refusals"][0]
    assert_safe(reply)
    code, reply, _ = desk_cli("--use-database-url", "context", env={"DATABASE_URL": cluster})
    assert code == 0 and reply["today"], "an operator's explicit fallback still runs"


def test_an_unreachable_database_exits_2_without_its_address(cluster):
    code, reply, err = desk_cli(
        "catalysts", "list",
        env={"MACRO_DESK_DATABASE_URL": "postgresql://macro_desk@127.0.0.1:1/macro"})
    assert code == 2 and reply["error"]["category"] == "database_unreachable"
    assert_safe(reply, ":1/")
    assert err.startswith("desk: database_unreachable:")


def test_a_refused_login_exits_2_without_the_role(cluster):
    code, reply, _ = desk_cli(
        "catalysts", "list",
        env={"MACRO_DESK_DATABASE_URL": f"postgresql://no_such_role@127.0.0.1:{PORT}/macro"})
    assert code == 2 and reply["error"]["category"] == "database_login_refused"
    assert_safe(reply, "no_such_role")


def test_a_database_fault_mid_run_exits_3(cluster, desk_url):
    """The login works but the database has none of the tables: a migration not applied."""
    elsewhere = desk_url.rsplit("/", 1)[0] + "/postgres"
    code, reply, _ = desk_cli("catalysts", "list", env={"MACRO_DESK_DATABASE_URL": elsewhere})
    assert code == 3 and reply["error"]["category"] == "database_schema"
    assert_safe(reply, "macro_core")


def test_a_bad_argument_and_a_bad_environment_still_answer_in_json(cluster, desk_url):
    code, reply, _ = desk_cli("catalysts", "submit", "--now", "yesterday",
                              env={"MACRO_DESK_DATABASE_URL": desk_url}, stdin='{"items": []}')
    assert code == 2 and reply["error"]["category"] == "invalid_argument"
    code, reply, _ = desk_cli("catalysts", "list", "--within-days", "9999",
                              env={"MACRO_DESK_DATABASE_URL": desk_url})
    assert code == 2 and reply["error"]["category"] == "invalid_argument", "argparse's own error"
    code, reply, _ = desk_cli(
        "context", env={"MACRO_DESK_DATABASE_URL": "postgresql://u:p@pooler.example.com:6543/x"})
    assert code == 2 and "port 6543" in reply["refusals"][0]
    assert_safe(reply, "pooler.example.com", "u:p")
    code, reply, _ = desk_cli("holdings", "submit", "--json-in", "/nonexistent/sub.json",
                              env={"MACRO_DESK_DATABASE_URL": desk_url})
    assert code == 2 and reply["error"]["category"] == "input_unreadable"
    assert_safe(reply, "/nonexistent")


def test_a_control_character_through_the_tool_is_a_refusal_not_a_crash(cluster, desk_url):
    item = upsert() | {"name": "U.S. August\u0000 CPI"}
    code, reply, _ = desk_cli("catalysts", "submit", "--dry-run",
                              env={"MACRO_DESK_DATABASE_URL": desk_url},
                              stdin=json.dumps({"items": [item]}))
    assert code == 1 and "control character" in reply["refusals"][0]
    assert reply["batch_id"] is not None, "the submission was archived"
