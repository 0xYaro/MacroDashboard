"""US spot ETF flows: SoSoValue's daily totals, Farside's per-fund check, and what derives.

The golden files are real captures taken at 01:32 Singapore on 2026-09-24,
byte for byte: SoSoValue's `historicalInflowChart` for `us-btc-spot` and
`us-eth-spot`, and Farside's two all-data pages as `curl_cffi` fetched them
through the Cloudflare check. 23 September's session was still in progress in
New York, so Farside's row for it is all `-` with a `0.0` Total, and that zero
must never become a reading.

The derivation tests use made-up sessions around real NYSE holidays, because
the rules they pin (a holiday does not break a streak, a missing session makes
a week unknown rather than small) are about the calendar, not about any vendor.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta
from decimal import Decimal

import pytest

from macro import clock
from macro.fetchers import etf_flows
from macro.fetchers.base import FetchContext
from macro.fetchers.etf_flows import FarsideDay, Session
from macro.http import FetchRefused
from macro.model import Observation, RawBatch

from .conftest import golden_json, golden_text, instant

NOW = instant("2026-09-23T17:32:00")  # 01:32 Thursday 24 September in Singapore
LATER = instant("2026-09-24T13:00:00")  # the 21:00 Singapore rescore, same day


def raw(kind: str, payload: object) -> RawBatch:
    return RawBatch(source="etf_flows", kind=kind, fetched_at=NOW, payload=payload)


def golden_batches() -> list[RawBatch]:
    return [
        raw("sosovalue_btc", golden_json("sosovalue_us_btc_spot.json")),
        raw("farside_btc", etf_flows.table_cells(golden_text("farside_btc_all_data.html"))),
        raw("sosovalue_eth", golden_json("sosovalue_us_eth_spot.json")),
        raw("farside_eth", etf_flows.table_cells(golden_text("farside_eth_all_data.html"))),
    ]


def rows_by_key(observations: Iterable[Observation]) -> dict[tuple[str, date], Observation]:
    return {(one.series_key, one.asof): one for one in observations}


# ---------------------------------------------------------------------------
# The two readers, against the captures
# ---------------------------------------------------------------------------


def test_sosovalue_serves_300_sessions_in_raw_usd():
    btc = etf_flows.read_sosovalue(golden_json("sosovalue_us_btc_spot.json"))
    assert len(btc) == 300
    assert btc[date(2026, 9, 22)] == Decimal("714748985.505")
    assert min(btc) == date(2025, 7, 15)
    assert date(2026, 9, 7) not in btc  # Labor Day
    assert all(clock.is_us_session(day) for day in btc)


def test_sosovalue_refuses_an_answer_that_is_not_code_zero():
    with pytest.raises(FetchRefused):
        etf_flows.read_sosovalue({"code": 40001, "msg": "rate limited", "data": None})
    with pytest.raises(FetchRefused):
        etf_flows.read_sosovalue({"code": 0, "data": []})


def test_farside_reads_the_total_in_usd_with_negatives_in_parentheses():
    btc = etf_flows.read_farside(etf_flows.table_cells(golden_text("farside_btc_all_data.html")))
    assert btc[date(2026, 9, 22)].total == Decimal("714700000.0")
    assert btc[date(2026, 9, 15)].total == Decimal("-450400000.0")
    eth = etf_flows.read_farside(etf_flows.table_cells(golden_text("farside_eth_all_data.html")))
    assert eth[date(2026, 9, 22)].total == Decimal("162200000.0")


def test_farsides_session_in_progress_is_missing_not_zero():
    """23 September: every fund `-`, Total `0.0`."""
    cells = etf_flows.table_cells(golden_text("farside_btc_all_data.html"))
    pending = next(row for row in cells if row and row[0] == "23 Sep 2026")
    assert set(pending[1:-1]) == {"-"} and pending[-1] == "0.0"
    assert date(2026, 9, 23) not in etf_flows.read_farside(cells)


def test_a_fund_that_has_reported_before_and_not_today_makes_the_row_incomplete():
    """27 July 2026: BTCO is `-` after two years of numbers, so the Total is short."""
    cells = etf_flows.table_cells(golden_text("farside_btc_all_data.html"))
    row = next(row for row in cells if row and row[0] == "27 Jul 2026")
    assert row.count("-") == 1
    assert date(2026, 7, 27) not in etf_flows.read_farside(cells)


def test_a_fund_that_has_not_launched_yet_does_not_make_a_row_incomplete():
    """11 January 2024, the first day: MSBT and BTC had not listed."""
    btc = etf_flows.read_farside(etf_flows.table_cells(golden_text("farside_btc_all_data.html")))
    assert btc[date(2024, 1, 11)].total == Decimal("655300000.0")


def test_farside_without_a_ticker_row_is_refused():
    with pytest.raises(FetchRefused):
        etf_flows.read_farside([["Date", "Flow", "Total"], ["22 Sep 2026", "1.0", "1.0"]])


# ---------------------------------------------------------------------------
# The whole normalisation, against the captures
# ---------------------------------------------------------------------------


def test_the_last_five_btc_sessions_agree_and_are_tier_one():
    rows = rows_by_key(etf_flows.normalize(golden_batches(), NOW))
    expected = {
        date(2026, 9, 16): "-2",
        date(2026, 9, 17): "1",
        date(2026, 9, 18): "2",
        date(2026, 9, 21): "3",
        date(2026, 9, 22): "4",
    }
    for day, run in expected.items():
        daily = rows[("btc_etf_net_flow", day)]
        assert daily.tier == 1, day
        assert daily.extra["crosscheck"] == "agrees with Farside"
        assert rows[("btc_etf_inflow_streak", day)].value == Decimal(run), day
    assert rows[("btc_etf_net_flow", date(2026, 9, 22))].display == "+$714.7M"
    assert rows[("btc_etf_inflow_streak", date(2026, 9, 22))].display == "+4 sessions"


def test_week_to_date_is_the_sum_of_the_iso_weeks_sessions_so_far():
    rows = rows_by_key(etf_flows.normalize(golden_batches(), NOW))
    tuesday = rows[("btc_etf_weekly_flow", date(2026, 9, 22))]
    assert tuesday.value == Decimal("998952286.985") + Decimal("714748985.505")
    assert tuesday.display == "+$1.71B week to date (2 sessions of 2026-W39)"
    # The whole of W38 on its Friday: the figure the journal printed as -$140.6M.
    friday = rows[("eth_etf_weekly_flow", date(2026, 9, 18))]
    assert round(friday.value / Decimal(1_000_000), 1) == Decimal("-140.0")
    # W37 was a four-session week: Labor Day, 7 September.
    week = rows[("btc_etf_weekly_flow", date(2026, 9, 11))]
    assert week.extra == {"week": "2026-W37", "sessions": 4}
    assert round(week.value / Decimal(1_000_000), 2) == Decimal("-462.73")


def test_the_eth_streak_reads_as_the_journal_counted_it():
    """-3 on Wed 17 September and +1 on Fri 18 September, as `config/predicates.md` records."""
    rows = rows_by_key(etf_flows.normalize(golden_batches(), NOW))
    assert rows[("eth_etf_inflow_streak", date(2026, 9, 17))].value == Decimal(-3)
    assert rows[("eth_etf_inflow_streak", date(2026, 9, 18))].value == Decimal(1)
    assert rows[("eth_etf_inflow_streak", date(2026, 9, 22))].value == Decimal(3)


def test_a_session_of_exactly_zero_is_a_zero_streak():
    """ETH on 14 August 2026 netted exactly nothing."""
    rows = rows_by_key(etf_flows.normalize(golden_batches(), NOW))
    zero = rows[("eth_etf_net_flow", date(2026, 8, 14))]
    assert zero.value == 0 and zero.display == "$0"
    assert rows[("eth_etf_inflow_streak", date(2026, 8, 14))].value == 0


def test_a_disagreement_is_tier_two_with_a_finding_and_its_week_follows():
    """BTC on 10 April 2026: SoSoValue $240.4M, Farside $256.7M."""
    out = etf_flows.normalize(golden_batches(), NOW)
    rows = rows_by_key(out)
    daily = rows[("btc_etf_net_flow", date(2026, 4, 10))]
    assert daily.tier == 2 and daily.extra["crosscheck"] == "disagrees with Farside"
    assert rows[("btc_etf_weekly_flow", date(2026, 4, 10))].tier == 2
    findings = [one for one in etf_flows.review(out) if one.check_key == "etf_flow_divergence"]
    assert {one.subject for one in findings} == {
        "btc_etf_net_flow 2026-04-08",
        "btc_etf_net_flow 2026-04-10",
    }


def test_every_session_sosovalue_serves_is_written_and_nothing_older():
    out = etf_flows.normalize(golden_batches(), NOW)
    daily = [one.asof for one in out if one.series_key == "btc_etf_net_flow"]
    assert len(daily) == 300
    assert min(daily) == date(2025, 7, 15)
    # Farside's older sessions settle the first streaks instead of leaving them unknown.
    assert ("btc_etf_inflow_streak", date(2025, 7, 15)) in rows_by_key(out)


def test_the_scheduled_run_re_states_only_the_replay_window():
    fetcher = etf_flows.EtfFlows()
    from macro.series import load

    from .conftest import CONFIG

    ctx = FetchContext(now=LATER, registry=load(CONFIG))
    out = fetcher.normalize(golden_batches(), ctx)
    assert min(one.asof for one in out) >= LATER.date() - timedelta(days=etf_flows.REPLAY_DAYS)
    assert {one.series_key for one in out} == set(fetcher.series)


def test_the_six_series_are_all_declared_deterministic(registry):
    for key in etf_flows.EtfFlows.series:
        assert registry.get(key) is not None, key
        assert registry.get(key).fetch_path == "deterministic", key
        assert registry.get(key).replayable, key


# ---------------------------------------------------------------------------
# The derivations, on the calendar
# ---------------------------------------------------------------------------


def sessions(flows: dict[str, int], tier: int = 1) -> dict[date, Session]:
    return {
        date.fromisoformat(day): Session(date.fromisoformat(day), Decimal(value), tier, "test")
        for day, value in flows.items()
    }


def test_a_holiday_does_not_break_a_streak():
    """Friday 4 September, Labor Day Monday 7 September, Tuesday 8 September."""
    flows = sessions({"2026-09-02": -5, "2026-09-03": 10, "2026-09-04": 20, "2026-09-08": 30})
    assert etf_flows.streak(flows, date(2026, 9, 8)) == (3, 1)


def test_a_holiday_week_sums_the_sessions_it_had():
    flows = sessions({"2026-09-08": 30, "2026-09-09": -10})
    assert etf_flows.week_to_date(flows, date(2026, 9, 8)) == (Decimal(30), 1, 1)
    assert etf_flows.week_to_date(flows, date(2026, 9, 9)) == (Decimal(20), 2, 1)


def test_a_missing_session_makes_the_week_unknown_not_small():
    flows = sessions({"2026-09-21": 100, "2026-09-23": 50})  # Tuesday 22nd missing
    assert etf_flows.week_to_date(flows, date(2026, 9, 21)) == (Decimal(100), 1, 1)
    assert etf_flows.week_to_date(flows, date(2026, 9, 23)) is None


def test_a_missing_session_inside_a_run_makes_the_streak_unknown():
    flows = sessions({"2026-09-17": -1, "2026-09-18": 5, "2026-09-22": 7})  # 21st missing
    assert etf_flows.streak(flows, date(2026, 9, 22)) is None
    # Where the run is already decided before the gap, the gap does not matter.
    flows = sessions({"2026-09-17": 9, "2026-09-18": 5, "2026-09-21": -3, "2026-09-22": -7})
    assert etf_flows.streak(flows, date(2026, 9, 22)) == (-2, 1)


def test_a_run_reaching_past_the_oldest_session_is_unknown():
    flows = sessions({"2026-09-21": 1, "2026-09-22": 2})
    assert etf_flows.streak(flows, date(2026, 9, 22)) is None


def test_a_zero_session_breaks_a_run_and_reads_zero():
    flows = sessions({"2026-09-17": 4, "2026-09-18": 0, "2026-09-21": 3})
    assert etf_flows.streak(flows, date(2026, 9, 18)) == (0, 1)
    assert etf_flows.streak(flows, date(2026, 9, 21)) == (1, 1)


def test_a_derived_reading_takes_the_worst_tier_it_stands_on():
    flows = sessions({"2026-09-21": 100, "2026-09-22": 50})
    flows[date(2026, 9, 21)] = Session(date(2026, 9, 21), Decimal(100), 2, "test")
    assert etf_flows.week_to_date(flows, date(2026, 9, 22)) == (Decimal(150), 2, 2)


def test_a_pending_session_on_both_sources_is_simply_absent():
    """SoSoValue has not reached the 23rd and Farside's row for it is all `-`."""
    soso = {date(2026, 9, 21): Decimal(10), date(2026, 9, 22): Decimal(20)}
    far = {
        date(2026, 9, 21): FarsideDay(Decimal(10), 12),
        date(2026, 9, 22): FarsideDay(Decimal(20), 12),
    }
    combined = etf_flows.combine(soso, far)
    assert date(2026, 9, 23) not in combined
    assert {one.series_key for one in etf_flows.observations("btc", combined)} == {
        "btc_etf_net_flow",
        "btc_etf_weekly_flow",
    }


def test_sosovalue_alone_is_tier_two_and_says_why():
    combined = etf_flows.combine(
        {date(2026, 9, 22): Decimal(714_748_985)}, None, farside_fault="HTTP 403"
    )
    one = combined[date(2026, 9, 22)]
    assert one.tier == 2 and one.source == "SoSoValue"
    assert one.extra["crosscheck"] == "Farside unreadable: HTTP 403"


def test_farside_alone_is_a_tier_two_fallback_with_a_finding():
    combined = etf_flows.combine(None, {date(2026, 9, 22): FarsideDay(Decimal(714_700_000), 12)})
    rows = etf_flows.observations("btc", combined, days=[date(2026, 9, 22)])
    daily = next(one for one in rows if one.series_key == "btc_etf_net_flow")
    assert daily.tier == 2 and daily.extra["fallback"] is True
    assert [one.check_key for one in etf_flows.review(rows)] == ["etf_flow_fallback"]


def test_a_farside_outage_defers_to_verified_rows_and_says_so():
    """Tier 2 for want of a cross-check is not evidence against a verified session.

    Every re-stated row, the week and the streak included, is marked to leave a
    stored Tier 1 row alone, and the run says once per product why its tier fell.
    """
    soso_only = [one for one in golden_batches() if one.kind.startswith("sosovalue")]
    rows = [one for one in etf_flows.normalize(soso_only, NOW) if one.asof >= date(2026, 9, 14)]
    assert rows and all(one.tier == 2 and one.defer_to_better_tier for one in rows)
    unreadable = [
        one for one in etf_flows.review(rows) if one.check_key == "etf_flow_crosscheck_unreadable"
    ]
    assert [one.subject for one in unreadable] == ["btc_etf_net_flow", "eth_etf_net_flow"]
    assert "the page could not be fetched" in unreadable[0].sentence


def test_a_disagreement_still_replaces_a_verified_row():
    """Both sources read and apart: that is evidence, and it re-states."""
    day = date(2026, 9, 22)
    combined = etf_flows.combine(
        {day: Decimal(1_000_000_000)}, {day: FarsideDay(Decimal(1_200_000_000), 12)}
    )
    rows = etf_flows.observations("btc", combined, days=[day])
    assert all(not one.defer_to_better_tier for one in rows)
    verified = [one for one in etf_flows.normalize(golden_batches(), NOW) if one.tier == 1]
    assert verified and all(not one.defer_to_better_tier for one in verified)


def test_agreement_is_five_percent_or_ten_million_whichever_is_wider():
    def tier(soso: int, far: int) -> int:
        day = date(2026, 9, 22)
        return etf_flows.combine({day: Decimal(soso)}, {day: FarsideDay(Decimal(far), 12)})[
            day
        ].tier

    assert tier(1_000_000_000, 1_049_000_000) == 1
    assert tier(1_000_000_000, 1_051_000_000) == 2
    assert tier(2_000_000, 11_900_000) == 1  # a quiet day: the $10M floor
    assert tier(2_000_000, 12_100_000) == 2


def test_a_session_still_in_progress_is_never_stored():
    """At 13:00 UTC on the 22nd the session has not closed, whatever a source says."""
    payload = {
        "code": 0,
        "data": [
            {"date": "2026-09-22", "totalNetInflow": 5},
            {"date": "2026-09-21", "totalNetInflow": 4},
        ],
    }
    out = etf_flows.normalize(
        [raw("sosovalue_btc", payload)], instant("2026-09-22T13:00:00")
    )
    assert {one.asof for one in out} == {date(2026, 9, 21)}


def test_a_non_session_date_from_sosovalue_is_dropped():
    payload = {
        "code": 0,
        "data": [
            {"date": "2026-09-07", "totalNetInflow": 5},  # Labor Day
            {"date": "2026-09-08", "totalNetInflow": 4},
        ],
    }
    assert set(etf_flows.read_sosovalue(payload)) == {date(2026, 9, 8)}


def test_display():
    assert etf_flows.money(Decimal("-46650000")) == "-$46.6M"
    assert etf_flows.money(Decimal("1713701272.49")) == "+$1.71B"
    assert etf_flows.streak_display(-1) == "-1 session"
    assert etf_flows.streak_display(0) == "0 (a flat session)"
