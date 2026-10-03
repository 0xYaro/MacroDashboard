"""Setup state, the band-touch flags, and the premise-moved rule.

Every setup is evaluated on the close, because every entry, invalidation, gate
and planned R:R in the Setup Ledger is written against closes and eighteen
graded rows depend on that meaning holding still.

The band-touch flags are two facts and not one. `band_touched_close` is an
entry. `band_touched_today` is the session's high and low reaching into the band
without one, which is the "entry band reached, gate refused, trade forgone"
case that has happened three times.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from macro.evaluate import setup_state
from macro.grading import OUTCOMES, count_targets, grade_setups
from macro.model import (
    Catalyst,
    Scenario,
    ScenarioSet,
    ScenarioTarget,
    ScenarioTargetState,
    Setup,
    Status,
    TriggerClause,
)
from macro.premise import HORIZON_DAYS, voidings

from .conftest import context, history, reading

W38_GATE = {
    "all": [
        {"op": "gt", "series": "breadth_spx_above_200dma", "value": 55},
        {"op": "lt", "series": "hy_oas_bps", "value": 285},
    ]
}


def setup(**overrides) -> Setup:
    """The W38 equities setup, as the Setup Ledger records it."""
    base = {
        "setup_id": "s1",
        "week": "2026-W38",
        "asset": "equities",
        "name": "S&P shelf FTR long (FTR, HTF)",
        "series_key": "spx_cash",
        "entry_lo": Decimal("7540"),
        "entry_hi": Decimal("7590"),
        "invalidation": Decimal("7470"),
        "invalidation_dir": "below",
        "target": Decimal("7780"),
        "rr_planned": Decimal("2.3"),
        "gate_expr": W38_GATE,
        "gate_text": "breadth above 55% and HY under 285 bps",
        "sector_focus_id": None,
        "activated_on": date(2026, 9, 14),
    }
    return Setup(**{**base, **overrides})


def week_tape():
    """The W38 week, with the closes the ledger's grading note names."""
    return history(
        reading("spx_cash", "2026-09-14", "7620.00", high="7640.00", low="7600.00"),
        reading("spx_cash", "2026-09-15", "7585.73", high="7625.00", low="7570.00"),
        reading("spx_cash", "2026-09-16", "7507.77", high="7590.00", low="7495.00"),
        reading("spx_cash", "2026-09-17", "7600.00", high="7610.00", low="7520.00"),
        reading("spx_cash", "2026-09-18", "7650.50", high="7657.17", low="7610.52"),
        reading("breadth_spx_above_200dma", "2026-09-14", "56.26"),
        reading("breadth_spx_above_200dma", "2026-09-15", "52.88"),
        reading("breadth_spx_above_200dma", "2026-09-16", "50.09"),
        reading("breadth_spx_above_200dma", "2026-09-17", "50.29"),
        reading("breadth_spx_above_200dma", "2026-09-18", "49.50"),
        reading("hy_oas_bps", "2026-09-15", "270"),
        reading("hy_oas_bps", "2026-09-16", "270"),
        reading("hy_oas_bps", "2026-09-17", "270"),
        reading("hy_oas_bps", "2026-09-18", "270"),
    )


# ---------------------------------------------------------------------------
# Entry state, on the close
# ---------------------------------------------------------------------------


def test_the_w38_setup_entered_on_the_close_inside_its_band(registry):
    """Tuesday 2026-09-15 closed 7,585.73, inside [7540, 7590].

    The entry state is a fact about the tape and is independent of the gate:
    the ledger records the trade as `not triggered` because the gate kept it
    shut, and this worker reports the two separately so that the forgone entry
    is visible rather than absent.
    """
    state = setup_state(setup(), context(registry, week_tape(), "2026-09-18"))
    assert state.entry_state == "open"
    assert state.gate_state == "blocked"
    assert state.gate_reason == "breadth_spx_above_200dma gt 55 refused at 49.50"
    assert state.invalidation_dir == "below"
    assert state.gate_text == "breadth above 55% and HY under 285 bps"


def test_the_invalidation_and_the_target_are_read_on_closes(registry):
    """A close through 7,470 stops it; a close at or beyond 7,780 completes it."""
    stopped = history(
        reading("spx_cash", "2026-09-15", "7585.73", high="7625", low="7570"),
        reading("spx_cash", "2026-09-16", "7460.00", high="7500", low="7450"),
    )
    assert (
        setup_state(setup(), context(registry, stopped, "2026-09-16")).entry_state == "stopped"
    )

    reached = history(
        reading("spx_cash", "2026-09-15", "7585.73", high="7625", low="7570"),
        reading("spx_cash", "2026-09-16", "7800.00", high="7810", low="7700"),
    )
    assert setup_state(setup(), context(registry, reached, "2026-09-16")).entry_state == "target"


def test_an_intraday_print_through_the_invalidation_does_not_stop_it(registry):
    """Closes decide. A wick to 7,450 with a close back at 7,560 is not a stop."""
    wick = history(
        reading("spx_cash", "2026-09-15", "7585.73", high="7625", low="7570"),
        reading("spx_cash", "2026-09-16", "7560.00", high="7590", low="7450"),
    )
    assert setup_state(setup(), context(registry, wick, "2026-09-16")).entry_state == "open"


def test_the_r_multiple_is_measured_from_the_close_that_opened_the_trade(registry):
    """Hand-computed. Entered at 7,585.73 with the stop at 7,470.

    Risk is 7,585.73 - 7,470 = 115.73. A close at 7,780 is a move of 194.27, so
    the realised multiple is 194.27 / 115.73 = 1.678648, which is 1.6786 at
    four places.

    The entry price is the close that actually opened the trade, not the middle
    of the band: a mid-band entry would state an R the operator never had.
    """
    tape = history(
        reading("spx_cash", "2026-09-15", "7585.73", high="7625", low="7570"),
        reading("spx_cash", "2026-09-16", "7780.00", high="7800", low="7700"),
    )
    state = setup_state(setup(), context(registry, tape, "2026-09-16"))
    assert state.entry_state == "target"
    assert state.r_running is not None
    assert state.r_running.quantize(Decimal("0.0001")) == Decimal("1.6786")


def test_a_setup_cannot_enter_on_a_session_before_it_was_activated(registry):
    """The activation clip, at the setup level rather than inside a predicate.

    The same tape and the same band, activated on 2026-09-17 instead: the
    2026-09-15 close that would have opened it is outside the window, so the
    setup is `not triggered`.
    """
    late = setup(activated_on=date(2026, 9, 17))
    state = setup_state(late, context(registry, week_tape(), "2026-09-18"))
    assert state.entry_state == "not triggered"
    assert state.sessions_carried == 2  # only 09-17 and 09-18


# ---------------------------------------------------------------------------
# The band-touch flags
# ---------------------------------------------------------------------------


def test_band_touched_today_and_band_touched_close_are_two_facts(registry):
    """The failure this pair exists to surface.

    On 2026-09-17 the session low of 7,520 reached into [7540, 7590]? No: 7,520
    is below 7,540 and the high of 7,610 is above it, so the session straddled
    the band without closing in it. `band_touched_today` is true and
    `band_touched_close` is false, which is exactly "entry band reached, gate
    refused, trade forgone".
    """
    state = setup_state(setup(), context(registry, week_tape(), "2026-09-17"))
    assert state.band_touched_today is True
    assert state.band_touched_close is False


def test_a_close_inside_the_band_raises_both_flags(registry):
    state = setup_state(setup(), context(registry, week_tape(), "2026-09-15"))
    assert state.band_touched_today is True
    assert state.band_touched_close is True


def test_a_session_that_never_approached_the_band_raises_neither(registry):
    state = setup_state(setup(), context(registry, week_tape(), "2026-09-18"))
    assert state.band_touched_today is False
    assert state.band_touched_close is False


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def test_a_gate_whose_reading_is_missing_is_unevaluable_and_never_blocked(registry):
    """`blocked` and `unevaluable` must not look the same.

    A gate reported as blocked when its breadth reading is simply absent is
    indistinguishable from one that genuinely refused, which is the failure the
    whole `unevaluable` state exists to prevent.
    """
    no_breadth = history(
        reading("spx_cash", "2026-09-18", "7560.00", high="7590", low="7540"),
        reading("hy_oas_bps", "2026-09-18", "270"),
    )
    state = setup_state(setup(), context(registry, no_breadth, "2026-09-18"))
    assert state.gate_state == "unevaluable"
    assert state.gate_state != "blocked"


def test_a_setup_with_no_gate_is_open_and_says_why(registry):
    state = setup_state(
        setup(gate_expr=None, gate_text=None), context(registry, week_tape(), "2026-09-18")
    )
    assert state.gate_state == "open"
    assert "no gate" in state.gate_reason


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------


def test_the_ledger_outcome_vocabulary_is_verbatim():
    """Including the U+2192 arrow, which the schema's check constraint requires."""
    assert set(OUTCOMES.values()) == {
        "not triggered",
        "open",
        "triggered → stopped",
        "triggered → target",
    }


def test_a_setup_grade_carries_the_realised_multiple_only_when_closed(registry):
    tape = history(
        reading("spx_cash", "2026-09-15", "7585.73", high="7625", low="7570"),
        reading("spx_cash", "2026-09-16", "7460.00", high="7500", low="7450"),
    )
    grades = grade_setups(
        [setup()], ctx=context(registry, tape, "2026-09-16"), graded_week="2026-W39"
    )
    assert grades[0].outcome == "triggered → stopped"
    assert grades[0].r_realised is not None
    assert grades[0].r_realised < 0

    still_open = grade_setups(
        [setup()], ctx=context(registry, week_tape(), "2026-09-18"), graded_week="2026-W39"
    )
    assert still_open[0].outcome == "open"
    assert still_open[0].r_realised is None


def band_state(target_id: str, series_key: str, label: str, *, holding: bool):
    return ScenarioTargetState(
        target_id=target_id,
        scenario_id="s1",
        series_key=series_key,
        label=label,
        kind="band",
        comparator="between",
        text=None,
        lo=None,
        hi=None,
        current=None,
        holding=holding,
        distance_to_breach=None,
    )


def manual_state(target_id: str, label: str):
    return ScenarioTargetState(
        target_id=target_id,
        scenario_id="s1",
        series_key=None,
        label=label,
        kind="manual",
        comparator=None,
        text=label,
        lo=None,
        hi=None,
        current=None,
        holding=None,
        distance_to_breach=None,
    )


def test_targets_n_is_the_row_count_including_manual_targets():
    """The W37 grading defect, made unrepresentable.

    In W37's block, W36's S1 was counted as nine targets including three
    non-numeric legs while W36's S2 was counted as six excluding its two
    equivalent legs. Here `n` is `len(targets)` and there is nowhere for a
    second convention to live.
    """
    targets = [
        band_state("t1", "spx_cash", "S&P 7,520-7,800", holding=True),
        band_state("t2", "vix", "VIX 13-19", holding=False),
        manual_state("t3", "Leadership: Nasdaq beats the Dow"),
    ]
    hit, total = count_targets(targets)
    assert total == 3  # the manual leg counts toward the denominator
    assert hit == 1  # and never toward the numerator, because it was not scored


# ---------------------------------------------------------------------------
# The premise-moved rule
# ---------------------------------------------------------------------------


def catalyst(**overrides) -> Catalyst:
    base = {
        "external_key": "k1",
        "name": "September FOMC minutes",
        "starts_at": datetime(2026, 10, 7, 18, 0, tzinfo=UTC),
        "ends_at": None,
        "date_precision": "exact",
        "source_url": "https://www.federalreserve.gov/",
        "affected": ("rates", "USD"),
        "status": "tracked",
        "seen_on": date(2026, 9, 21),
        "raw_date_text": "",
    }
    return Catalyst(**{**base, **overrides})


def scenario_set(*clauses: TriggerClause) -> ScenarioSet:
    return ScenarioSet(
        set_id="set1",
        week="2026-W39",
        authored_asof=date(2026, 9, 21),
        activated_at=datetime(2026, 9, 21, 1, 30, tzinfo=UTC),
        scenarios=(
            Scenario(
                scenario_id="sc1",
                set_id="set1",
                ordinal=2,
                title="Duration fails and the 5% ten-year finally transmits",
                probability=32,
                positioning="Cut high-beta, not quality",
                clauses=clauses,
                targets=(),
            ),
        ),
    )


def clause(**overrides) -> TriggerClause:
    base = {
        "condition_id": "c1",
        "scenario_id": "sc1",
        "ordinal": 1,
        "text": "a second tailing auction Sep 22-24",
        "kind": "expr",
        "expr": {"op": "gt", "series": "ust_10y", "value": 5.12},
        "catalyst_id": "cat1",
        "required": False,
        "void_at": None,
        "void_reason": None,
    }
    return TriggerClause(**{**base, **overrides})


NOW = datetime(2026, 9, 28, 1, 30, tzinfo=UTC)


def vix_band() -> ScenarioTarget:
    return ScenarioTarget(
        target_id="t1",
        scenario_id="sc1",
        series_key="vix",
        label="VIX 20-30",
        kind="band",
        comparator="between",
        lo=Decimal(20),
        hi=Decimal(30),
        text=None,
    )


def leadership_target() -> ScenarioTarget:
    return ScenarioTarget(
        target_id="t2",
        scenario_id="sc1",
        series_key=None,
        label="Leadership: memory and AI infrastructure underperform the index",
        kind="manual",
        comparator=None,
        lo=None,
        hi=None,
        text="the crowded leg goes first",
    )


def test_a_retired_catalyst_voids_its_clause_with_a_reason():
    found, findings = voidings(
        scenario_set(clause()), {"cat1": catalyst(status="retired")}, now=NOW
    )
    assert len(found) == 1
    assert found[0].condition_id == "c1"
    assert "retired" in found[0].reason
    assert any(one.check_key == "scenario_premise_moved" for one in findings)


def test_a_catalyst_retired_after_its_event_took_place_voids_nothing():
    """W40 in production: Micron and payrolls were voided the morning after they printed.

    The Catalyst Monitor drops an event from its note once it has happened, and
    the worker read that retirement as the event being called off. The premise
    did not move: the event the clause was written against took place, and the
    clause is exactly what has to be scored now.
    """
    printed = catalyst(status="retired", starts_at=datetime(2026, 9, 26, 12, 30, tzinfo=UTC))
    found, findings = voidings(scenario_set(clause()), {"cat1": printed}, now=NOW)
    assert found == []
    assert findings == []


def test_a_catalyst_renamed_before_its_event_voids_nothing():
    """W40's PCE: the note retired one name and added the same release under another.

    A tracked catalyst of the same family dated within a day of the retired one is
    the same event under a new name, not a premise that moved.
    """
    when = datetime(2026, 9, 30, 12, 30, tzinfo=UTC)
    old = catalyst(
        status="retired",
        starts_at=when,
        family="us_pce",
        name="Q2 GDP third estimate, annual GDP revisions, and August PCE",
    )
    new = catalyst(
        external_key="k2",
        starts_at=when,
        family="us_pce",
        name="Q2 GDP third estimate and August PCE",
    )
    found, findings = voidings(scenario_set(clause()), {"cat1": old, "cat2": new}, now=NOW)
    assert found == []
    assert findings == []


def test_a_catalyst_retired_before_its_event_with_no_successor_still_voids():
    """A cancellation is still a cancellation, however close the next one in its family."""
    old = catalyst(
        status="retired", starts_at=datetime(2026, 9, 30, 12, 30, tzinfo=UTC), family="us_pce"
    )
    next_month = catalyst(
        external_key="k2", family="us_pce", starts_at=datetime(2026, 10, 29, 12, 30, tzinfo=UTC)
    )
    found, _ = voidings(scenario_set(clause()), {"cat1": old, "cat2": next_month}, now=NOW)
    assert [one.condition_id for one in found] == ["c1"]


def test_a_catalyst_rescheduled_past_the_horizon_voids_its_clause():
    """The horizon is fourteen days from the set's own authoring as-of.

    The set was authored 2026-09-21, so the horizon runs to 2026-10-05. A
    catalyst moved to 2026-10-20 can no longer settle the clause inside the week
    the probability was stated for.
    """
    moved = catalyst(starts_at=datetime(2026, 10, 20, 18, 0, tzinfo=UTC))
    found, _ = voidings(scenario_set(clause()), {"cat1": moved}, now=NOW)
    assert len(found) == 1
    assert "2026-10-20" in found[0].reason
    assert "2026-10-05" in found[0].reason
    assert HORIZON_DAYS == 14


def test_a_catalyst_still_inside_the_horizon_voids_nothing():
    inside = catalyst(starts_at=datetime(2026, 10, 1, 18, 0, tzinfo=UTC))
    found, findings = voidings(scenario_set(clause()), {"cat1": inside}, now=NOW)
    assert found == []
    assert findings == []


def test_an_already_void_clause_keeps_the_moment_its_premise_moved():
    """Re-voiding would move `void_at` forward and lose the only record of when."""
    earlier = datetime(2026, 9, 23, 1, 30, tzinfo=UTC)
    already = clause(void_at=earlier, void_reason="the Catalyst Monitor retired it")
    found, _ = voidings(scenario_set(already), {"cat1": catalyst(status="retired")}, now=NOW)
    assert found == []


def test_a_clause_whose_catalyst_vanished_is_left_standing_with_a_finding():
    """Voiding on a guess is worse than not voiding."""
    found, findings = voidings(scenario_set(clause()), {}, now=NOW)
    assert found == []
    assert [one.check_key for one in findings] == ["trigger_clause_catalyst_missing"]


def test_voiding_never_removes_a_clause_or_renormalises_a_threshold():
    """The rule stated as an assertion over the returned records.

    `voidings` returns which clauses to mark and nothing else. It cannot drop a
    clause from its `n_of` or change a threshold, because it never touches
    either: the only fields it names are `void_at` and `void_reason`.
    """
    three = (
        clause(condition_id="c1", ordinal=1),
        clause(condition_id="c2", ordinal=2, catalyst_id=None),
        clause(condition_id="c3", ordinal=3, catalyst_id=None),
    )
    found, _ = voidings(scenario_set(*three), {"cat1": catalyst(status="retired")}, now=NOW)
    assert [one.condition_id for one in found] == ["c1"]
    import dataclasses

    named = {field.name for field in dataclasses.fields(found[0])}
    assert named == {"condition_id", "scenario_id", "reason", "void_at"}


def test_a_void_clause_is_counted_in_the_denominator_and_never_fires(registry):
    """What the scenario state does with a void clause, end to end."""
    from macro.evaluate import scenarios

    voided = ScenarioSet(
        set_id="set1",
        week="2026-W39",
        authored_asof=date(2026, 9, 21),
        activated_at=datetime(2026, 9, 21, 1, 30, tzinfo=UTC),
        scenarios=(
            Scenario(
                scenario_id="sc1",
                set_id="set1",
                ordinal=2,
                title="Duration fails",
                probability=32,
                positioning=None,
                clauses=(
                    clause(
                        condition_id="c1",
                        void_at=NOW,
                        void_reason="the Catalyst Monitor retired the auction cluster",
                    ),
                    clause(
                        condition_id="c2",
                        ordinal=2,
                        catalyst_id=None,
                        expr={"op": "gt", "series": "vix", "value": 20},
                    ),
                ),
                targets=(vix_band(), leadership_target()),
            ),
        ),
    )
    tape = history(reading("vix", "2026-09-28", "14.81"), reading("ust_10y", "2026-09-28", "5.01"))
    states, targets, conditions = scenarios(
        voided,
        ctx=context(registry, tape, "2026-09-28"),
        asserted={},
        activated_on=date(2026, 9, 21),
    )
    state = states[0]
    assert state.clauses_total == 2  # the void clause stays in the denominator
    assert state.clauses_void == 1
    assert state.clauses_fired == 0
    assert state.premise_moved is True
    assert state.positioning is None

    # bands_* counts only what a machine can score; targets_* counts everything.
    assert state.bands_total == 1
    assert state.targets_total == 2

    void_state = next(one for one in conditions if one.condition_id == "c1")
    assert void_state.status is Status.VOID
    assert "retired" in void_state.evidence["why"]
    # Carried from the clause, not from this run. A clause voided last Tuesday
    # stays voided last Tuesday however many runs have read it since.
    assert void_state.void_at == NOW
    assert void_state.void_reason == "the Catalyst Monitor retired the auction cluster"

    live = next(one for one in conditions if one.condition_id == "c2")
    assert live.void_at is None
    assert live.void_reason is None

    manual_target = next(one for one in targets if one.kind == "manual")
    assert manual_target.holding is None
    assert manual_target.current is None


def test_the_distance_to_breach_is_an_unsigned_magnitude_in_the_series_unit(registry):
    """VIX at 22 inside a 20 to 30 band is two points from the nearer bound.

    Two points, not 0.1 and not 10 percent: a band in basis points and a band in
    index points would read the same if this were a fraction.
    """
    from macro.evaluate import target_state

    tape = history(reading("vix", "2026-09-28", "22"))
    state = target_state(vix_band(), context(registry, tape, "2026-09-28"))
    assert state.holding is True
    assert state.distance_to_breach == Decimal(2)
    assert state.distance_to_breach > 0


def test_a_band_outside_its_bounds_has_no_distance_to_a_breach(registry):
    from macro.evaluate import target_state

    tape = history(reading("vix", "2026-09-28", "14.81"))
    state = target_state(vix_band(), context(registry, tape, "2026-09-28"))
    assert state.holding is False
    assert state.distance_to_breach is None


def test_a_band_with_no_reading_is_unevaluable_rather_than_broken(registry):
    from macro.evaluate import target_state

    state = target_state(vix_band(), context(registry, history(), "2026-09-28"))
    assert state.holding is None
    assert state.current is None


def test_the_horizon_is_measured_from_the_sets_authoring_and_not_from_today():
    """The probability was stated then, so the horizon runs from then.

    The same catalyst, the same run instant, two sets authored a fortnight
    apart: the older set's horizon has passed and the newer one's has not.
    """
    far = catalyst(starts_at=datetime(2026, 10, 12, 18, 0, tzinfo=UTC))
    older = scenario_set(clause())
    newer = ScenarioSet(
        set_id="set2",
        week="2026-W41",
        authored_asof=date(2026, 10, 5),
        activated_at=datetime(2026, 10, 5, 1, 30, tzinfo=UTC),
        scenarios=older.scenarios,
    )
    assert len(voidings(older, {"cat1": far}, now=NOW)[0]) == 1
    assert voidings(newer, {"cat1": far}, now=NOW)[0] == []
    assert timedelta(days=HORIZON_DAYS) == timedelta(days=14)


# ---------------------------------------------------------------------------
# What a setup is measured against
# ---------------------------------------------------------------------------


class Row(dict):
    """A stand-in for one `macro_core.setup` row joined to `macro_core.series`."""


def setup_row(**overrides) -> Row:
    base = {
        "id": "s1",
        "week": "2026-W39",
        "asset": "equities",
        "name": "Nasdaq failed-push fade (O/U, HTF)",
        "entry_lo": Decimal("26900"),
        "entry_hi": Decimal("27100"),
        "invalidation": Decimal("27300"),
        "invalidation_dir": "above",
        "target": Decimal("26100"),
        "rr_planned": Decimal("3.0"),
        "gate_expr": None,
        "gate_text": "breadth still under 52%",
        "sector_focus_id": None,
        "activated_at": datetime(2026, 9, 21, 1, 30, tzinfo=UTC),
        "authored_at": datetime(2026, 9, 21, 1, 0, tzinfo=UTC),
        "series_key": "nasdaq_cash",
    }
    return Row({**base, **overrides})


def test_a_setup_is_measured_on_the_series_its_row_names():
    """`macro_core.setup.series_id` says it, so nothing derives it.

    The W39 Nasdaq fade's levels are Nasdaq levels. Deriving the series from
    the setup's name works until it does not, and a Nasdaq setup measured
    against the S&P would produce an entry state, an R-multiple and a Setup
    Ledger row that are all wrong and none of them visibly so.
    """
    from macro.store import _setup_from

    found = _setup_from(setup_row())
    assert found is not None
    assert found.series_key == "nasdaq_cash"
    assert found.activated_on == date(2026, 9, 21)
    assert found.invalidation_dir == "above"


def test_a_setup_with_no_series_is_shown_unscored_rather_than_guessed_at(registry):
    """A setup that does not say what it is measured against is not scored.

    It still gets a `setup_state` row, with `series_key` null, the gate
    unevaluable and every measured field empty. Dropping it would make a setup
    nobody could score indistinguishable from one nobody proposed, and
    defaulting the series would give it an entry state, an R-multiple and a
    Setup Ledger row that are all wrong and none of them visibly so.
    """
    from macro.store import _setup_from, setup_refused_finding

    found = _setup_from(setup_row(series_key=None))
    assert found.series_key is None

    state = setup_state(found, context(registry, week_tape(), "2026-09-18"))
    assert state.series_key is None
    assert state.series_label is None
    assert state.gate_state == "unevaluable"
    assert "names no series" in state.gate_reason
    assert state.entry_state == "not triggered"
    assert state.r_running is None
    assert state.sessions_carried == 0
    assert state.band_touched_today is False

    finding = setup_refused_finding("s1", "Nasdaq failed-push fade (O/U, HTF)", "2026-W39")
    assert finding.check_key == "setup_series_missing"
    assert "no series_id" in finding.sentence
    assert "not scored" in finding.sentence


def test_a_scored_setup_names_the_series_its_levels_are_quoted_on(registry):
    """The page has to be able to say what the numbers are numbers of."""
    one = setup()
    state = setup_state(one, context(registry, week_tape(), "2026-09-18"))
    assert state.series_key == one.series_key
    assert state.series_label == registry.get(one.series_key).label


def test_the_activation_clip_falls_back_to_the_authoring_instant():
    """A proposed setup has no `activated_at` yet, and still may not reach back."""
    from macro.store import _setup_from

    found = _setup_from(setup_row(activated_at=None))
    assert found is not None
    assert found.activated_on == date(2026, 9, 21)


def test_a_one_sided_band_is_distinguishable_from_a_band_missing_its_floor(registry):
    """`lo` null with `hi` set is two different statements.

    Under `lt` it is the forecast "VIX below 30". Under `between` it is a band
    whose floor was never written, which is an authoring defect. Without the
    comparator both render as "- to 30" and the operator cannot tell a forecast
    from a hole.
    """
    from macro.evaluate import target_state

    one_sided = ScenarioTarget(
        target_id="t9",
        scenario_id="sc1",
        series_key="vix",
        label="VIX under 30",
        kind="band",
        comparator="lt",
        lo=None,
        hi=Decimal(30),
        text=None,
    )
    incomplete = ScenarioTarget(
        target_id="t10",
        scenario_id="sc1",
        series_key="vix",
        label="VIX 20-30",
        kind="band",
        comparator="between",
        lo=None,
        hi=Decimal(30),
        text=None,
    )
    ctx = context(registry, history(reading("vix", "2026-09-28", "22")), "2026-09-28")

    assert target_state(one_sided, ctx).comparator == "lt"
    assert target_state(incomplete, ctx).comparator == "between"
    # The bounds alone are identical, which is the whole point.
    assert target_state(one_sided, ctx).hi == target_state(incomplete, ctx).hi
    assert target_state(one_sided, ctx).lo == target_state(incomplete, ctx).lo is None


def test_a_manual_target_carries_its_own_words(registry):
    """It has no numbers at all and still counts toward `targets_n`."""
    from macro.evaluate import target_state

    state = target_state(leadership_target(), context(registry, history(), "2026-09-28"))
    assert state.kind == "manual"
    assert state.comparator is None
    assert state.text == leadership_target().text
    assert state.lo is None
    assert state.hi is None


def test_a_void_clause_cannot_be_recorded_without_a_reason():
    """The two travel together or the clause disappeared silently.

    `macro_core.trigger_clause` has a check constraint saying exactly this, and
    a `ConditionState` that violated it would roll back the whole MartFlush at
    the last moment instead of failing where the mistake was made.
    """
    from macro.model import ConditionState

    with pytest.raises(ValueError, match="written together"):
        ConditionState(
            condition_id="c1",
            owner_kind="trigger",
            owner_id="sc1",
            ordinal=1,
            text="the auction cluster tails",
            kind="expr",
            status=Status.VOID,
            current_display=None,
            evidence={},
            source="computed",
            void_at=NOW,
            void_reason=None,
        )
