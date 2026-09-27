"""`macro-agent desk compare`: the trial's shadow batches against the vault feeds.

The pure comparison is tested on rows shaped exactly as the desk archives them
and as `DeskStore` reads the live tables. One test then runs the whole path
against a throwaway Postgres (the same cluster `test_desk_postgres` builds): real
dry-run submissions, real vault rows, `load`, `compare`, and nothing written.
"""

from __future__ import annotations

import shutil
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

import pytest

from macro_agent import desk, desk_compare
from macro_agent.desk_compare import CompareStore, Inputs, compare, render

from .test_desk_postgres import (  # noqa: F401  (fixtures)
    NOW,
    admin,
    cluster,
    desk_url,
    pg_settings,
    risk,
    store,
    upsert,
)

DAY = date(2026, 9, 21)
AT = datetime(2026, 9, 21, 0, 50, tzinfo=UTC)  # 08:50 in Singapore


def batch(feed: str, record: dict[str, Any], *, at: datetime = AT,
          origin: str = desk.ORIGIN) -> dict[str, Any]:
    return {"id": 1, "source": f"desk:{feed}", "kind": "submission", "fetched_at": at,
            "payload": {"origin": origin, **record}}


def catalyst(name: str, key: str, starts: str, **over: Any) -> dict[str, Any]:
    row = {"id": f"id-{key}", "external_key": key, "name": name,
           "starts_at": datetime.fromisoformat(starts), "ends_at": None,
           "date_precision": "day", "status": "tracked", "family": None,
           "first_seen": date(2026, 9, 1), "retired_at": None, "feed_source": "vault"}
    row.update(over)
    return row


def inputs(**over: Any) -> Inputs:
    base: dict[str, Any] = {
        "batches": [], "catalysts": [], "aliases": {}, "tail_risks": [], "holdings": [],
        "watchlist_changes": [],
    }
    base.update(over)
    return Inputs(**base)


def one_day(found: Inputs) -> desk_compare.Day:
    days = compare(found, since=DAY, until=DAY)
    assert len(days) == 1
    return days[0]


def test_catalysts_new_dates_retires_and_the_vault_side():
    live = [
        catalyst("U.S. August CPI", "k-cpi", "2026-10-14T12:30:00+00:00", date_precision="exact"),
        catalyst("FOMC decision", "k-fomc", "2026-10-28T18:00:00+00:00", date_precision="exact"),
        catalyst("Jackson Hole", "k-jh", "2026-08-22T00:00:00+00:00"),
        catalyst("September Employment Situation / NFP", "k-nfp",
                 "2026-10-02T12:30:00+00:00", family="us_nfp"),
        catalyst("Added today by the vault", "k-new-vault", "2026-10-09T00:00:00+00:00",
                 first_seen=DAY),
        catalyst("Retired today by the vault", "k-gone", "2026-09-20T00:00:00+00:00",
                 status="retired", retired_at=DAY),
        catalyst("Renamed event", "k-renamed", "2026-10-20T00:00:00+00:00"),
    ]
    applied = [
        {"op": "upsert", "result": "updated", "id": "id-k-cpi", "name": "U.S. August CPI",
         "external_key": "k-cpi", "starts_at": "2026-10-15T12:30:00+00:00",
         "date_precision": "exact"},
        {"op": "upsert", "result": "unchanged", "id": "id-k-fomc", "name": "FOMC decision",
         "external_key": "k-fomc", "starts_at": "2026-10-28T18:00:00+00:00",
         "date_precision": "exact"},
        {"op": "upsert", "result": "inserted", "id": None,
         "name": "U.S. September Employment Situation", "external_key": "k-nfp-2",
         "family": "us_nfp", "starts_at": "2026-10-02T12:30:00+00:00", "date_precision": "exact"},
        {"op": "upsert", "result": "updated", "id": None, "name": "Old name",
         "external_key": "k-old", "starts_at": "2026-10-20T00:00:00+00:00",
         "date_precision": "day"},
        {"op": "retire", "result": "retired", "id": "id-k-jh", "name": "Jackson Hole"},
    ]
    found = inputs(
        catalysts=live,
        aliases={"k-old": {"catalyst_id": "id-k-renamed", "name": "Old name"}},
        batches=[
            batch("catalysts", {"applied": applied, "refusals": ["items[5] was refused"]}),
            # The smoke test's own batches never count.
            batch("catalysts", {"applied": [{"op": "retire", "result": "retired",
                                             "id": "id-k-fomc", "name": "FOMC decision"}]},
                  origin="openclaw:macro-desk-smoke"),
        ],
    )
    day = one_day(found).catalysts
    assert day is not None
    assert day.submissions == 1 and day.refused == 1
    assert day.results == {"inserted": 1, "retired": 1, "unchanged": 1, "updated": 2}
    assert day.new == ["U.S. September Employment Situation (2 Oct 12:30 UTC), maybe the "
                       "calendar's 'September Employment Situation / NFP' (2 Oct)"]
    assert day.dates_disagree == ["U.S. August CPI: desk 15 Oct 12:30 UTC (exact), vault "
                                  "14 Oct 12:30 UTC (exact)"]
    assert day.desk_retired_vault_tracks == ["Jackson Hole"]
    assert day.vault_added_not_desk == ["Added today by the vault (9 Oct)"]
    assert day.vault_retired_not_desk == ["Retired today by the vault"]


def test_a_day_without_a_run_says_so_and_days_are_singapore_days():
    late = datetime(2026, 9, 21, 17, 0, tzinfo=UTC)  # 01:00 on the 22nd in Singapore
    found = inputs(batches=[batch("catalysts", {"applied": []}, at=late)])
    days = compare(found, since=DAY, until=DAY + timedelta(days=1))
    assert days[0].catalysts is None and days[1].catalysts is not None
    text = render(days, since=DAY, until=DAY + timedelta(days=1))
    assert "Mon 21 Sep 2026\n  Catalysts: no desk run." in text
    assert "Tail risks: no desk run." in text


def test_tail_risks_by_id_severity_retire_and_weekly_coverage():
    live = [
        {"risk_id": "iran-oil-chokepoints", "severity": "acute", "retired_on": None},
        {"risk_id": "ai-capex-unwind", "severity": "elevated", "retired_on": None},
        {"risk_id": "us-funding-deadline", "severity": "elevated", "retired_on": None},
        {"risk_id": "private-credit-insurers", "severity": "watch", "retired_on": None},
        {"risk_id": "old-risk", "severity": "watch", "retired_on": date(2026, 9, 1)},
    ]
    applied = [
        {"op": "upsert", "result": "updated", "risk_id": "iran-oil-chokepoints",
         "severity": "acute"},
        {"op": "upsert", "result": "de-escalated", "risk_id": "ai-capex-unwind",
         "severity": "watch"},
        {"op": "upsert", "result": "inserted", "risk_id": "japan-bond-selloff",
         "severity": "elevated"},
        {"op": "retire", "result": "retired", "risk_id": "us-funding-deadline"},
    ]
    found = inputs(tail_risks=live, batches=[
        batch("tail_risks", {"payload": {"kind": "weekly"}, "applied": applied, "refusals": []}),
    ])
    day = one_day(found).tail_risks
    assert day is not None
    assert day.submissions == ["weekly"] and day.checked == 3
    assert day.new == ["japan-bond-selloff (elevated)"]
    assert day.severity_differs == ["ai-capex-unwind: desk watch, vault elevated"]
    assert day.desk_retired_vault_live == ["us-funding-deadline"]
    assert day.weekly_unchecked == ["private-credit-insurers"]


def test_the_same_finding_from_two_runs_is_reported_once():
    live = [{"risk_id": "us-funding-deadline", "severity": "elevated", "retired_on": None}]
    retire = {"payload": {"kind": "daily"}, "refusals": [],
              "applied": [{"op": "retire", "result": "retired", "risk_id": "us-funding-deadline"}]}
    found = inputs(tail_risks=live, batches=[batch("tail_risks", retire),
                                             batch("tail_risks", retire)])
    text = render([one_day(found)], since=DAY, until=DAY)
    assert "Retired by the desk, live on the vault: us-funding-deadline.\n" in text
    assert "2 daily submission(s)" in text


def test_a_daily_recheck_is_not_held_to_weekly_coverage():
    live = [{"risk_id": "private-credit-insurers", "severity": "watch", "retired_on": None}]
    found = inputs(tail_risks=live, batches=[
        batch("tail_risks", {"payload": {"kind": "daily"}, "applied": [], "refusals": []}),
    ])
    day = one_day(found).tail_risks
    assert day is not None and day.weekly_unchecked == []


def test_holdings_coverage_beside_the_vault_list_and_changes():
    watch = [
        {"symbol": "GOOG", "source": "accounting", "change_24h_pct": "-1.41",
         "material": True, "driver_summary": "Alphabet fell after a court ruling."},
        {"symbol": "MU", "source": "manual", "change_24h_pct": None, "material": False,
         "driver_summary": None},
        {"symbol": "HYPE", "source": "accounting", "change_24h_pct": "3.79", "material": False,
         "driver_summary": None},
    ]
    found = inputs(
        batches=[batch("holdings", {"watch": watch, "events": [{"symbol": "GOOG"}],
                                    "refusals": []})],
        holdings=[{"ticker": "MU", "active": True}, {"ticker": "RIOT", "active": True},
                  {"ticker": "OLD", "active": False}],
        watchlist_changes=[
            {"ticker": "MU", "observed_at": datetime(2026, 9, 21, 1, 15, tzinfo=UTC),
             "change_pct": 3.1},
            {"ticker": "MU", "observed_at": datetime(2026, 9, 20, 1, 15, tzinfo=UTC),
             "change_pct": 1.0},
        ],
    )
    day = one_day(found).holdings
    assert day is not None
    assert day.names == ["GOOG", "MU", "HYPE"]
    assert (day.with_move, day.material, day.with_driver, day.events) == (2, ["GOOG"], 1, 1)
    assert day.vault_list == ["MU", "RIOT"]
    assert day.vault_changes == ["MU (+3.1%)"]
    text = render([one_day(found)], since=DAY, until=DAY)
    assert "Names on both lists: MU." in text
    assert "\u2014" not in text  # the voice guide bans the em dash


def test_a_resend_of_one_item_keeps_the_days_other_names():
    """A resend carries only the fixed item; the day's watch is every name, the latest
    row for each, not whichever submission came last."""
    first = [
        {"symbol": "GOOG", "source": "accounting", "change_24h_pct": "-1.41",
         "material": True, "driver_summary": "Alphabet fell."},
        {"symbol": "META", "source": "accounting", "change_24h_pct": "0.54",
         "material": True, "driver_summary": "Meta rose on an ad note."},
        {"symbol": "HYPE", "source": "accounting", "change_24h_pct": None,
         "material": False, "driver_summary": None},
    ]
    resend = [{"symbol": "GOOG", "source": "accounting", "change_24h_pct": "-1.50",
               "material": False, "driver_summary": "Alphabet slipped after a ruling."}]
    found = inputs(batches=[
        batch("holdings", {"watch": first, "events": [{"symbol": "GOOG", "name": "Q3"}],
                           "refusals": ["items[3] (MU) was not applied"]}),
        batch("holdings", {"watch": resend, "events": [{"symbol": "GOOG", "name": "Q3"}],
                           "refusals": []}, at=AT + timedelta(minutes=20)),
    ])
    day = one_day(found).holdings
    assert day is not None and day.submissions == 2
    assert day.names == ["GOOG", "META", "HYPE"]
    assert (day.with_move, day.material, day.with_driver) == (2, ["META"], 2)
    assert day.events == 1, "the same event resent is one event"
    assert day.refused == 1


# ---------------------------------------------------------------------------
# Against Postgres
# ---------------------------------------------------------------------------


@pytest.fixture
async def compare_store(cluster):  # noqa: F811
    found = cast(CompareStore, await CompareStore.connect(cluster, ssl="disable"))
    try:
        yield found
    finally:
        await found.aclose()


@pytest.mark.skipif(not shutil.which("initdb"), reason="no local Postgres")
async def test_compare_reads_the_real_tables_and_writes_nothing(
    store, pg_settings, admin, compare_store  # noqa: F811
):
    # The vault's calendar: one row the worker wrote, dated 14 October.
    await desk.catalysts_submit(store, pg_settings, {"items": [upsert()]}, now=NOW,
                                dry_run=False)
    await admin.execute("update macro_core.catalyst set feed_source = 'vault'")
    await admin.execute(
        "insert into macro_core.tail_risk (key, name, severity, first_seen, last_seen) "
        "values ('k1', 'Opaque private-credit leverage and insurer feedback loop', 'watch', "
        "current_date, current_date)")
    await admin.execute("delete from macro_core.watchlist_change")
    await admin.execute("delete from macro_core.holding")
    await admin.execute(
        "insert into macro_core.holding (ticker, book, active) values ('MU', 'equities', true)")
    await admin.execute(
        "insert into macro_core.watchlist_change (ticker, observed_at, change_pct, summary) "
        "values ('MU', $1, 3.1, 'Memory prices rose.')", NOW)

    # The trial's dry runs: a different date for CPI, a new catalyst, a weekly scan.
    later = upsert(when={"date": "2026-10-15", "time": "08:30", "tz": "America/New_York"},
                   date_text="2026-10-15 08:30 ET")
    await desk.catalysts_submit(
        store, pg_settings,
        {"items": [later, upsert("Treasury refunding announcement",
                                 when={"date": "2026-11-04"}, date_text="2026-11-04")]},
        now=NOW, dry_run=True)
    await desk.tail_risks_submit(store, pg_settings, {"kind": "weekly", "items": [risk()]},
                                 now=NOW, dry_run=True)
    before = await admin.fetchval("select count(*) from macro_raw.batch")
    catalyst_rows = await admin.fetchval("select count(*) from macro_core.catalyst")

    found = await desk_compare.load(compare_store, DAY)
    days = compare(found, since=DAY, until=DAY)

    assert await admin.fetchval("select count(*) from macro_raw.batch") == before
    assert await admin.fetchval("select count(*) from macro_core.catalyst") == catalyst_rows
    cat = days[0].catalysts
    assert cat is not None
    assert [one.split(" (")[0] for one in cat.new] == ["Treasury refunding announcement"]
    assert cat.dates_disagree and cat.dates_disagree[0].startswith("U.S. August CPI: desk 15 Oct")
    risks_day = days[0].tail_risks
    assert risks_day is not None
    assert risks_day.new == ["iran-oil-chokepoints (elevated)"]
    assert risks_day.weekly_unchecked == ["opaque-private-credit-leverage-and-insurer"]
    assert days[0].holdings is None
    text = render(days, since=DAY, until=DAY)
    assert "Holdings: no desk run." in text

    await admin.execute("delete from macro_core.watchlist_change")
    await admin.execute("delete from macro_core.holding")
