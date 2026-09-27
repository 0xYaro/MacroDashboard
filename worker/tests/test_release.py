"""The release-rule grammar, and the freshness rating the web app takes as given.

The web app never re-rates a row's `freshness`, so that rating is load-bearing
and belongs entirely to this worker. Forty-seven series on six release bases
means a large minority are always between prints: if `due` and `overdue` were
conflated, the board would sit permanently amber and a genuine source failure
would be invisible on it.

`due` means a weekly or monthly series is between prints and behaving. `overdue`
means it missed a release it was owed. Both are derived from the declared rule
in `config/series.yaml` and never from wall-clock age.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from macro import clock, release
from macro.clock import SINGAPORE, is_us_business_day, is_us_session


def rule(text: str) -> release.ReleaseRule:
    return release.parse(text)


def at(text: str) -> datetime:
    """An instant written on the operator's clock, as the runs are."""
    return datetime.fromisoformat(text).replace(tzinfo=SINGAPORE)


# ---------------------------------------------------------------------------
# The grammar
# ---------------------------------------------------------------------------


def test_every_rule_in_the_config_parses(registry):
    """The nine shapes `config/series.yaml` documents, over every series."""
    shapes = {one.release_rule.raw for one in registry.ordered()}
    assert len(registry) == 76
    assert shapes == {
        "us_session",
        "us_session; release=+1d",
        "us_business_day",
        "us_business_day; release=+1b",
        "calendar_day",
        "weekly; asof=sun",
        "weekly; asof=tue; release=fri",
        "weekly; asof=wed; release=thu",
        "monthly; asof=eom; release=next_month_day:15",
        # The macro prints, each offset set to the latest its release has landed
        # rather than the typical, so the row reads `fresh` until the figure is
        # reliably out and never `overdue` on a day it was never going to print.
        "monthly; asof=eom; release=+2b",
        "monthly; asof=eom; release=+4b",
        "monthly; asof=eom; release=+6b",
        "monthly; asof=eom; release=+10b",
        "monthly; asof=eom; release=+11b",
        "monthly; asof=eom; release=+22b",
        "monthly; asof=eom; release=+24b",
        "event",
    }


def test_a_rule_outside_the_grammar_is_refused_rather_than_guessed_at():
    for bad in ("hourly", "weekly", "monthly", "weekly; asof=xyz", "us_session; release=soon"):
        with pytest.raises(release.RuleRefused):
            rule(bad)


def test_an_event_rule_promises_nothing():
    """`next_expected` is null and the row is never overdue."""
    event = rule("event")
    assert event.is_scheduled is False
    assert release.next_expected(event, date(2026, 9, 18)) is None
    assert release.freshness(event, date(2020, 1, 1), at("2026-09-21 09:30")) == "fresh"


# ---------------------------------------------------------------------------
# An event the 09:30 agents re-read every day
# ---------------------------------------------------------------------------
#
# Hand-computed. New York is on EDT (UTC-4) in September and EST (UTC-5) in
# December; Singapore is UTC+8 all year.


def test_the_reconfirmation_run_is_the_first_0930_after_the_new_york_day():
    """Monday 21 September 2026, New York.

    The New York Monday ends at 00:00 EDT Tuesday, which is 04:00 UTC and 12:00
    Tuesday in Singapore. Tuesday's 09:30 run is before that, so the first full
    run after it is Wednesday 23 September 09:30 Singapore, 01:30 UTC.
    """
    assert release.reconfirm_due_at(date(2026, 9, 21)) == at("2026-09-23 09:30")


def test_the_reconfirmation_run_in_winter_is_the_same_run():
    """Monday 7 December 2026. The New York day ends at 05:00 UTC, 13:00 Singapore.

    An hour later than in September, and still after Tuesday's 09:30 run, so the
    answer is Wednesday 9 December 09:30 Singapore again.
    """
    assert release.reconfirm_due_at(date(2026, 12, 7)) == at("2026-12-09 09:30")


def test_an_agent_event_row_goes_due_then_overdue_when_no_run_confirms_it():
    """`fed_funds` dated Monday 21 September: due at Wednesday's 09:30, overdue after Thursday's.

    Due from 2026-09-23 09:30 Singapore. The grace is the module's one day, so
    it stays due through 2026-09-24 09:30 inclusive and is overdue after it.
    """
    event = rule("event")
    for moment, expected in (
        ("2026-09-22 09:30", "fresh"),
        ("2026-09-22 21:00", "fresh"),
        ("2026-09-23 09:29", "fresh"),
        ("2026-09-23 09:30", "due"),
        ("2026-09-23 21:00", "due"),
        ("2026-09-24 09:30", "due"),
        ("2026-09-24 09:31", "overdue"),
        ("2026-09-24 21:00", "overdue"),
    ):
        rating = release.freshness(
            event, date(2026, 9, 21), at(moment), fetch_path="agent_0930"
        )
        assert rating == expected, (moment, rating)


def test_a_row_the_morning_run_just_wrote_is_fresh_whichever_day_it_carries():
    """The 09:30 run on Singapore Wednesday 23 September writes the row.

    It may date it Wednesday (Singapore) or Tuesday (New York). Neither may read
    amber at that run or at the 21:00 rescore after it.
    """
    event = rule("event")
    for asof in (date(2026, 9, 22), date(2026, 9, 23)):
        for moment in ("2026-09-23 09:30", "2026-09-23 21:00"):
            rating = release.freshness(event, asof, at(moment), fetch_path="agent_0930")
            assert rating == "fresh", (asof, moment, rating)


def test_the_migrated_weekly_fed_funds_row_reads_overdue():
    """The last migrated `fed_funds` row is dated Friday 18 September.

    Due at Sunday 20 September 09:30 Singapore, overdue after Monday's 09:30,
    so on Wednesday 23 September it is plainly overdue, which is the truth: no
    agent run has confirmed it since.
    """
    assert release.reconfirm_due_at(date(2026, 9, 18)) == at("2026-09-20 09:30")
    assert (
        release.freshness(
            rule("event"), date(2026, 9, 18), at("2026-09-23 09:30"), fetch_path="agent_0930"
        )
        == "overdue"
    )


def test_a_deterministic_event_row_is_still_never_overdue_by_rule():
    """A Treasury auction owes nothing until the producer holds one.

    Its lateness comes from the schedule TreasuryDirect publishes, carried in
    `extra.next_expected`, never from the run clock.
    """
    event = rule("event")
    old = date(2026, 8, 25)
    now = at("2026-09-23 09:30")
    assert release.freshness(event, old, now, fetch_path="deterministic") == "fresh"
    assert release.freshness(event, old, now) == "fresh"


def test_fetch_path_changes_nothing_for_a_scheduled_rule():
    session = rule("us_session")
    for path in (None, "deterministic", "agent_0930"):
        assert (
            release.freshness(session, date(2026, 9, 18), at("2026-09-22 09:30"), fetch_path=path)
            == "due"
        )


# ---------------------------------------------------------------------------
# Advancing each calendar
# ---------------------------------------------------------------------------


def test_us_session_skips_the_weekend():
    """Friday's close is followed by Monday's, not by Saturday's."""
    assert release.next_expected(rule("us_session"), date(2026, 9, 18)) == date(2026, 9, 21)


def test_us_session_skips_good_friday_and_us_business_day_does_not():
    """The NYSE and the Treasury do not keep the same calendar.

    Good Friday 2026 is 3 April. The NYSE is shut and the par curve prints.
    """
    good_friday = date(2026, 4, 3)
    assert is_us_session(good_friday) is False
    assert is_us_business_day(good_friday) is True
    assert release.next_expected(rule("us_session"), date(2026, 4, 2)) == date(2026, 4, 6)
    assert release.next_expected(rule("us_business_day"), date(2026, 4, 2)) == good_friday


def test_us_business_day_skips_columbus_day_and_us_session_does_not():
    """The other way round: 12 October 2026 is federal, and the NYSE trades."""
    columbus = date(2026, 10, 12)
    assert is_us_business_day(columbus) is False
    assert is_us_session(columbus) is True


def test_the_credit_spread_lag_is_one_business_day():
    """`us_business_day; release=+1b`. ICE publishes a day late.

    Thursday's reading becomes readable on Friday, so Friday holding Thursday's
    as-of is fresh and not stale.
    """
    oas = rule("us_business_day; release=+1b")
    assert release.released_on(oas, date(2026, 9, 17)) == date(2026, 9, 18)
    assert release.next_expected(oas, date(2026, 9, 17)) == date(2026, 9, 21)
    assert release.freshness(oas, date(2026, 9, 17), at("2026-09-18 09:30")) == "fresh"


def test_the_cftc_week_is_tuesday_dated_and_friday_released():
    """`weekly; asof=tue; release=fri`."""
    cftc = rule("weekly; asof=tue; release=fri")
    assert release.released_on(cftc, date(2026, 9, 15)) == date(2026, 9, 18)
    assert release.next_expected(cftc, date(2026, 9, 15)) == date(2026, 9, 25)


def test_the_h41_week_is_wednesday_dated_and_thursday_released():
    h41 = rule("weekly; asof=wed; release=thu")
    assert release.released_on(h41, date(2026, 9, 16)) == date(2026, 9, 17)
    assert release.next_expected(h41, date(2026, 9, 16)) == date(2026, 9, 24)


def test_the_monthly_rule_lands_mid_the_following_month():
    """`monthly; asof=eom; release=next_month_day:15`.

    August's data month ends 31 August and becomes readable on 15 September;
    the next observation is September's, readable on 15 October.
    """
    monthly = rule("monthly; asof=eom; release=next_month_day:15")
    assert release.released_on(monthly, date(2026, 8, 31)) == date(2026, 9, 15)
    assert release.next_asof(monthly, date(2026, 8, 31)) == date(2026, 9, 30)
    assert release.next_expected(monthly, date(2026, 8, 31)) == date(2026, 10, 15)


def test_calendar_day_has_no_closed_days():
    assert release.next_expected(rule("calendar_day"), date(2026, 9, 19)) == date(2026, 9, 20)


# ---------------------------------------------------------------------------
# due is not overdue
# ---------------------------------------------------------------------------


def test_a_weekly_series_four_days_from_its_next_print_is_fresh():
    """The whole reason the rule is declared rather than inferred.

    CFTC positions dated Tuesday 15 September are the newest there are on
    Monday 21 September. The next print is due Friday 25 September, so the row
    is fresh and the board does not go amber for it.
    """
    cftc = rule("weekly; asof=tue; release=fri")
    assert release.freshness(cftc, date(2026, 9, 15), at("2026-09-21 09:30")) == "fresh"


def test_a_session_series_is_fresh_all_of_the_next_morning_in_singapore():
    """The Monday 09:30 run, holding Friday's close.

    Monday's US session closes at 16:00 New York, which is 04:00 Tuesday in
    Singapore. At 09:30 Monday it has not happened, so Friday's close is the
    newest there is and the row is fresh. A rating that compared dates would
    call this `due` from midnight, and twenty-odd session and daily rows would
    sit amber every single morning.
    """
    session = rule("us_session")
    assert release.freshness(session, date(2026, 9, 18), at("2026-09-21 09:30")) == "fresh"
    assert release.freshness(session, date(2026, 9, 18), at("2026-09-21 21:00")) == "fresh"


def test_a_session_series_goes_due_once_its_close_has_passed_without_a_reading():
    """The Tuesday 09:30 run, still holding Friday's close: Monday was missed."""
    session = rule("us_session")
    assert release.freshness(session, date(2026, 9, 18), at("2026-09-22 09:30")) == "due"


def test_due_escalates_to_overdue_after_the_grace_day():
    """One day of grace, then it is a fault rather than a lag."""
    session = rule("us_session")
    assert release.freshness(session, date(2026, 9, 18), at("2026-09-22 09:30")) == "due"
    assert release.freshness(session, date(2026, 9, 18), at("2026-09-23 09:30")) == "overdue"


def test_the_cftc_week_goes_due_only_after_its_friday_release():
    cftc = rule("weekly; asof=tue; release=fri")
    assert release.freshness(cftc, date(2026, 9, 15), at("2026-09-25 09:30")) == "fresh"
    assert release.freshness(cftc, date(2026, 9, 15), at("2026-09-26 09:30")) == "due"
    assert release.freshness(cftc, date(2026, 9, 15), at("2026-09-27 09:30")) == "overdue"


def test_a_series_with_no_observation_at_all_is_overdue():
    assert release.freshness(rule("us_session"), None, at("2026-09-21 09:30")) == "overdue"


def healthy_asof(rule: release.ReleaseRule, now: datetime) -> date | None:
    """The newest `asof` a healthy run would be holding for a rule at `now`.

    Start at the newest date the rule's own calendar could be describing, then
    walk back one step at a time until a print has already become readable.
    That is what the fetchers themselves converge on.

    Derived per rule rather than per cadence, because a `weekly; asof=tue`
    series and a `weekly; asof=sun` one are both weekly and hold different days.
    """
    if not rule.is_scheduled:
        return now.date()
    cursor = _newest_candidate(rule, now.date())
    for _ in range(60):
        readable = release.readable_at(rule, cursor)
        if readable is not None and readable <= now:
            return cursor
        cursor = _step_back(rule, cursor)
    return None


_WEEKDAY = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def _newest_candidate(rule: release.ReleaseRule, today: date) -> date:
    """The newest date the rule's calendar could be describing."""
    if rule.base == "weekly":
        wanted = _WEEKDAY[rule.asof_key or "mon"]
        return today - timedelta(days=(today.weekday() - wanted) % 7)
    if rule.base == "monthly":
        # The current month has not ended, so the newest data month is the last.
        return today.replace(day=1) - timedelta(days=1)
    if rule.base == "us_session":
        return clock.us_session_on_or_before(today)
    if rule.base == "us_business_day":
        cursor = today
        while not clock.is_us_business_day(cursor):
            cursor -= timedelta(days=1)
        return cursor
    return today


def _step_back(rule: release.ReleaseRule, day: date) -> date:
    """One step back along the rule's own calendar."""
    if rule.base == "us_session":
        return clock.previous_us_session(day)
    if rule.base == "us_business_day":
        cursor = day - timedelta(days=1)
        while not clock.is_us_business_day(cursor):
            cursor -= timedelta(days=1)
        return cursor
    if rule.base == "weekly":
        return day - timedelta(days=7)
    if rule.base == "monthly":
        return day.replace(day=1) - timedelta(days=1)
    return day - timedelta(days=1)


def test_the_whole_live_registry_is_fresh_on_a_normal_monday(registry):
    """The rating has to discriminate, or it says nothing.

    Every series is given the newest as-of a healthy run would hold for its own
    rule on Monday 2026-09-21, and the board reads entirely `fresh`. Sixty
    series on six calendars is enough that a rating which went amber here would
    hide a real failure among fifty false ones.

    The as-of is derived per rule rather than per cadence, because a
    `weekly; asof=tue` series and a `weekly; asof=sun` one are both weekly and
    hold different days, and a test that fed them the same date would be
    asserting against a fixture rather than against the rule.
    """
    for moment in (at("2026-09-21 09:30"), at("2026-09-21 21:00")):
        amber = {}
        for one in registry.ordered():
            asof = healthy_asof(one.release_rule, moment)
            rating = release.freshness(
                one.release_rule, asof, moment, fetch_path=one.fetch_path
            )
            if rating != "fresh":
                amber[one.key] = (rating, str(asof), one.release_rule.raw)
        assert amber == {}, amber


def test_a_daily_us_session_series_holding_fridays_reading_is_fresh_on_monday():
    """The par curve and breadth both sit here on a Monday morning."""
    for text in ("us_session", "us_business_day"):
        assert release.freshness(rule(text), date(2026, 9, 18), at("2026-09-21 09:30")) == "fresh"


# ---------------------------------------------------------------------------
# Catch-up
# ---------------------------------------------------------------------------


def test_due_work_is_computed_from_the_rule_and_not_from_the_outage():
    """Three sessions missed over a weekend is three sessions, not five days.

    The cursor is Wednesday 2026-09-16 and the run is Tuesday 2026-09-22 at
    09:30 Singapore. Thursday, Friday and Monday closed; Tuesday's has not, so
    it is not owed.
    """
    due = release.due_asofs(rule("us_session"), date(2026, 9, 16), at("2026-09-22 09:30"))
    assert due == (date(2026, 9, 17), date(2026, 9, 18), date(2026, 9, 21))


def test_due_work_does_not_count_a_session_that_has_not_closed():
    """The 09:30 run is before every US close of its own day."""
    assert release.due_asofs(rule("us_session"), date(2026, 9, 18), at("2026-09-21 09:30")) == ()
    assert release.due_asofs(rule("us_session"), date(2026, 9, 18), at("2026-09-22 09:30")) == (
        date(2026, 9, 21),
    )


def test_due_work_stops_at_what_is_readable_now():
    """A weekly print not yet released is not owed."""
    cftc = rule("weekly; asof=tue; release=fri")
    assert release.due_asofs(cftc, date(2026, 9, 15), at("2026-09-25 09:30")) == ()
    assert release.due_asofs(cftc, date(2026, 9, 15), at("2026-09-26 09:30")) == (
        date(2026, 9, 22),
    )


def test_an_event_series_owes_nothing():
    assert release.due_asofs(rule("event"), date(2026, 1, 1), at("2026-09-21 09:30")) == ()


# ---------------------------------------------------------------------------
# The snapshot's own rating, over the live registry
# ---------------------------------------------------------------------------


def test_the_snapshot_rates_an_empty_database_honestly(registry):
    """A cold start, before anything has ever been written.

    Every deterministic row is `overdue`, because the worker owns it and has not
    written one. Every `agent_0930` row but the four asserted ones is
    `not_connected`, because the 09:30 agents are outside this worker and until
    one runs the row is not late, it is not wired up. The four asserted rows are
    `asserted`, because a row a human writes has no release to be late for.
    """
    from collections import Counter
    from datetime import UTC

    from macro import evaluate
    from macro.observations import History

    rows = evaluate.snapshot(
        registry,
        History(),
        now=datetime(2026, 9, 21, 1, 30, tzinfo=UTC),
        anchor_asof=None,
    )
    counted = Counter(one.freshness for one in rows)
    assert counted["asserted"] == 4
    assert counted["not_connected"] == len(registry.agent_only()) - 4
    assert counted["overdue"] == len(registry.deterministic())
    assert sum(counted.values()) == len(registry) == 76
    assert {one.series_key for one in rows if one.freshness == "asserted"} == {
        "state_street_equity_allocation",
        "state_street_risk_appetite",
        "tariff_state",
        "geopolitical_state",
    }


def test_a_failed_source_is_reported_as_failed_and_not_as_overdue(registry):
    """A source that is down says so, rather than looking like a slow series."""
    from datetime import UTC

    from macro import evaluate
    from macro.observations import History

    rows = evaluate.snapshot(
        registry,
        History(),
        now=datetime(2026, 9, 21, 1, 30, tzinfo=UTC),
        anchor_asof=None,
        source_status={"spx_cash": "failed"},
    )
    spx = next(one for one in rows if one.series_key == "spx_cash")
    assert spx.freshness == "failed"


def test_the_snapshot_carries_the_cadence_and_the_delta_label(registry):
    """A weekly series never offers a one-day delta, because it has no prior day."""
    from datetime import UTC

    from macro import evaluate
    from macro.observations import History

    rows = evaluate.snapshot(
        registry,
        History(),
        now=datetime(2026, 9, 21, 1, 30, tzinfo=UTC),
        anchor_asof=None,
    )
    by_key = {one.series_key: one for one in rows}
    assert by_key["spx_cash"].cadence == "session"
    assert by_key["spx_cash"].delta_native_label == "1d"
    assert by_key["cftc_asset_managers_es"].delta_native_label == "WoW"
    assert by_key["state_street_equity_allocation"].delta_native_label == "MoM"
    assert by_key["fed_funds"].delta_native_label is None


# ---------------------------------------------------------------------------
# A producer-stated next date beats a derived one
# ---------------------------------------------------------------------------


def snapshot_row(registry, key: str, reading_extra: dict, asof: str, moment: datetime):
    from macro import evaluate
    from macro.observations import History, Reading

    row = Reading(
        series_key=key,
        asof=date.fromisoformat(asof),
        value=None,
        display="5.420%",
        tier=1,
        extra=reading_extra,
    )
    rows = evaluate.snapshot(registry, History([row]), now=moment, anchor_asof=None)
    return next(one for one in rows if one.series_key == key)


def test_an_event_series_states_the_next_date_its_producer_published(registry):
    """An auction has no cadence, so `event` can derive nothing.

    TreasuryDirect publishes the schedule, the fetcher carries it, and the row
    states it. A stated date is a fact from the producer; a derived one is a
    rule's inference, and where both exist the fact wins.
    """
    row = snapshot_row(
        registry,
        "auction_high_yield_2y",
        {"next_expected": "2026-09-22"},
        "2026-08-25",
        at("2026-09-21 09:30"),
    )
    assert row.next_expected == date(2026, 9, 22)
    assert row.freshness == "fresh"


def test_an_event_series_with_no_stated_date_derives_nothing(registry):
    """`event` promises nothing, so nothing is invented to replace it."""
    row = snapshot_row(
        registry, "auction_high_yield_20y", {}, "2026-09-15", at("2026-09-21 09:30")
    )
    assert row.next_expected is None
    assert row.freshness == "fresh"


def test_a_stated_date_that_has_passed_makes_the_row_due_then_overdue(registry):
    """`event` alone could only ever say `fresh`.

    That is right for a series with no schedule and wrong for one whose
    schedule is simply published somewhere a release rule cannot reach: an
    auction that happened two days ago with no result stored is a real fault.
    """
    for moment, expected in (
        ("2026-09-22 21:00", "fresh"),
        ("2026-09-23 09:30", "due"),
        ("2026-09-24 09:30", "overdue"),
    ):
        row = snapshot_row(
            registry,
            "auction_high_yield_2y",
            {"next_expected": "2026-09-22"},
            "2026-08-25",
            at(moment),
        )
        assert row.freshness == expected, (moment, row.freshness)


def test_an_unreadable_stated_date_is_ignored_rather_than_crashing(registry):
    row = snapshot_row(
        registry,
        "auction_high_yield_2y",
        {"next_expected": "not a date"},
        "2026-08-25",
        at("2026-09-21 09:30"),
    )
    assert row.next_expected is None
    assert row.freshness == "fresh"
