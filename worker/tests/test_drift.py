"""Drift since the call, against the W39 tape.

Every case is hand-built so the expected score can be counted on one hand: the
set is activated on Monday 21 September against Friday the 18th's close, the
tape moves on the 22nd and 23rd, and the test says what the Desk should print
on the 23rd.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from macro import drift, evaluate
from macro.model import (
    PhaseCall,
    PhaseCondition,
    Scenario,
    ScenarioSet,
    ScenarioTarget,
    Setup,
    TriggerClause,
)

from .conftest import context, history, reading

ANCHOR = date(2026, 9, 21)
ACTIVATED = datetime(2026, 9, 21, 1, 30, tzinfo=UTC)


def tape(*later):
    """Friday's close, the tape the Monday call was written on, then the test's own."""
    return history(
        reading("vix", "2026-09-18", "14.87"),
        reading("hy_oas_bps", "2026-09-18", "270"),
        reading("breadth_spx_above_200dma", "2026-09-18", "50.49"),
        reading("spx_cash", "2026-09-18", "7764.70", high="7779", low="7691"),
        *later,
    )


def clause(ordinal: int, expr: dict, *, scenario_id: str = "sc2") -> TriggerClause:
    return TriggerClause(
        condition_id=f"{scenario_id}-c{ordinal}",
        scenario_id=scenario_id,
        ordinal=ordinal,
        text=f"clause {ordinal}",
        kind="expr",
        expr=expr,
        catalyst_id=None,
        required=False,
        void_at=None,
        void_reason=None,
    )


def band(target_id: str, series: str, lo: str, hi: str, *, scenario_id: str = "sc2"):
    return ScenarioTarget(
        target_id=target_id,
        scenario_id=scenario_id,
        series_key=series,
        label=f"{series} {lo}-{hi}",
        kind="band",
        comparator="between",
        lo=Decimal(lo),
        hi=Decimal(hi),
        text=None,
    )


def w39(clauses=(), targets=()) -> ScenarioSet:
    return ScenarioSet(
        set_id="set39",
        week="2026-W39",
        authored_asof=ANCHOR,
        activated_at=ACTIVATED,
        scenarios=(
            Scenario(
                scenario_id="sc2",
                set_id="set39",
                ordinal=2,
                title="Duration fails and the 5% ten-year finally transmits",
                probability=32,
                positioning=None,
                clauses=tuple(clauses),
                targets=tuple(targets),
            ),
        ),
    )


def equities_call() -> PhaseCall:
    return PhaseCall(
        phase_call_id="pc-eq",
        asset="equities",
        week="2026-W39",
        phase=3,
        phase_name="Late Cycle / Distribution",
        activated_at=ACTIVATED,
        conditions=(
            PhaseCondition(
                condition_id="pc-eq-2",
                phase_call_id="pc-eq",
                ordinal=2,
                text="Breadth depressed and the decline broad-based",
                kind="expr",
                expr={"op": "lt", "series": "breadth_spx_above_200dma", "value": 45},
            ),
        ),
    )


def run(registry, the_tape, the_set, *, calls=(), setups=(), risks=(), asof="2026-09-23"):
    ctx = context(registry, the_tape, asof)
    conditions = []
    for call in calls:
        _, states = evaluate.phase(
            call, ctx=ctx, asserted={}, activated_on=call.activated_at.date()
        )
        conditions.extend(states)
    scenarios, targets, clause_states = evaluate.scenarios(
        the_set, ctx=ctx, asserted={}, activated_on=the_set.activated_at.date()
    )
    conditions.extend(clause_states)
    setup_states = [evaluate.setup_state(one, ctx) for one in setups]
    return drift.compute(
        scenario_set=the_set,
        phase_calls=list(calls),
        setups=list(setups),
        asserted={},
        ctx=ctx,
        now_conditions=conditions,
        now_scenarios=scenarios,
        now_targets=targets,
        now_setups=setup_states,
        tail_risks=list(risks),
    )


def test_a_quiet_tape_scores_nothing_and_says_the_call_stands(registry):
    the_set = w39(clauses=[clause(1, {"op": "gt", "series": "vix", "value": 20})])
    found = run(registry, tape(reading("vix", "2026-09-23", "14.21")), the_set)
    assert found.score == 0
    assert found.items == ()
    assert found.refresh_suggested is False
    assert found.reason == "Nothing the call depends on has changed state since it was made."


def test_no_session_since_the_call_is_not_measured_at_all(registry):
    """Monday 09:30 Singapore: the newest close is still Friday's, the call's own tape."""
    the_set = w39(clauses=[clause(1, {"op": "gt", "series": "vix", "value": 20})])
    found = run(registry, tape(), the_set, asof="2026-09-18")
    assert found.score == 0
    assert found.anchor_asof == date(2026, 9, 18)
    assert found.reason.startswith("No session has closed since the call")


def test_the_first_session_after_the_call_is_measured_against_the_tape_it_was_written_on(
    registry,
):
    """The set activated Monday 09:30 Singapore was written against Friday's close.

    VIX closed 15 on Friday 18 September and 25 on Monday 21 September. On
    Tuesday the newest session is Monday's, and `vix gt 20` has flipped since
    the call. Anchoring on the UTC date of activation (Monday) compared Monday
    with itself and reported that no session had closed since.
    """
    the_set = w39(clauses=[clause(1, {"op": "gt", "series": "vix", "value": 20})])
    found = run(
        registry,
        history(reading("vix", "2026-09-18", "15"), reading("vix", "2026-09-21", "25")),
        the_set,
        asof="2026-09-21",
    )
    assert found.anchor_asof == date(2026, 9, 18)
    assert ("clause", "not yet", "met") in [
        (one.kind, one.before, one.after) for one in found.items
    ]
    assert found.refresh_suggested is True


def test_one_clause_flipping_is_minor(registry):
    the_set = w39(
        clauses=[
            clause(1, {"op": "gt", "series": "vix", "value": 20}),
            clause(2, {"op": "gt", "series": "hy_oas_bps", "value": 300}),
        ]
    )
    found = run(registry, tape(reading("vix", "2026-09-23", "21.40")), the_set)
    assert [(one.kind, one.subject, one.before, one.after) for one in found.items] == [
        ("clause", "S2 clause 1", "not yet", "met")
    ]
    assert found.score == 1
    assert found.material == 0
    assert found.refresh_suggested is False


def test_a_trigger_firing_in_full_is_material(registry):
    """VIX through 20 and HY through 300 fire both of S2's clauses: 1 + 1 + 3."""
    the_set = w39(
        clauses=[
            clause(1, {"op": "gt", "series": "vix", "value": 20}),
            clause(2, {"op": "gt", "series": "hy_oas_bps", "value": 300}),
        ]
    )
    found = run(
        registry,
        tape(reading("vix", "2026-09-23", "21.40"), reading("hy_oas_bps", "2026-09-23", "305")),
        the_set,
    )
    kinds = [one.kind for one in found.items]
    assert kinds[0] == "trigger_fired"
    assert found.items[0].sentence == "S2's trigger has fired in full: 2 of 2 clauses met."
    assert found.material == 1
    assert found.score == 5
    assert found.refresh_suggested is True
    assert "1 of them material" in found.reason


def test_a_phase_condition_flipping_is_material(registry):
    the_set = w39()
    found = run(
        registry,
        tape(reading("breadth_spx_above_200dma", "2026-09-23", "44.10")),
        the_set,
        calls=[equities_call()],
    )
    assert [(one.kind, one.material, one.weight) for one in found.items] == [
        ("phase_condition", True, 3)
    ]
    assert found.refresh_suggested is True


def fresh_equities_call() -> PhaseCall:
    """A fresh Phase 3 read, held against what it rests on."""

    def holds(ordinal: int, text: str, expr: dict) -> PhaseCondition:
        return PhaseCondition(
            condition_id=f"pc-eq-{ordinal}",
            phase_call_id="pc-eq",
            ordinal=ordinal,
            text=text,
            kind="expr",
            expr=expr,
        )

    return PhaseCall(
        phase_call_id="pc-eq",
        asset="equities",
        week="2026-W39",
        phase=3,
        phase_name="Late Cycle / Distribution",
        activated_at=ACTIVATED,
        classification="fresh",
        conditions=(
            holds(1, "VIX stays under 22", {"op": "lt", "series": "vix", "value": 22}),
            holds(2, "HY stays under 300 bps", {"op": "lt", "series": "hy_oas_bps", "value": 300}),
            holds(
                3,
                "Breadth stays under 60%",
                {"op": "lt", "series": "breadth_spx_above_200dma", "value": 60},
            ),
            holds(
                4,
                "Breadth stays over 45%",
                {"op": "gt", "series": "breadth_spx_above_200dma", "value": 45},
            ),
        ),
    )


def test_two_fresh_conditions_failing_flag_the_read_and_lead_the_list(registry):
    """Two of the four things the read holds on failed since Monday.

    The flag is a sentence the Desk leads with, weighed at nothing because the
    two flips under it already count, and it never reclassifies anything.
    """
    found = run(
        registry,
        tape(reading("vix", "2026-09-23", "24.10"), reading("hy_oas_bps", "2026-09-23", "312")),
        w39(),
        calls=[fresh_equities_call()],
    )
    first = found.items[0]
    assert (first.kind, first.material, first.weight) == ("phase_stale", False, 0)
    assert first.before == "0 of 4 failed" and first.after == "2 of 4 failed"
    assert first.sentence.startswith("This week's equities phase read may be out of date")
    flips = [one for one in found.items if one.kind == "phase_condition"]
    assert [one.sentence for one in flips] == [
        'The equities phase condition "VIX stays under 22" no longer holds.',
        'The equities phase condition "HY stays under 300 bps" no longer holds.',
    ]
    assert found.score == 6
    assert found.reason.endswith("This week's equities phase read may be out of date.")


def test_one_fresh_condition_failing_is_not_a_stale_read(registry):
    found = run(
        registry,
        tape(reading("vix", "2026-09-23", "24.10")),
        w39(),
        calls=[fresh_equities_call()],
    )
    assert [one.kind for one in found.items] == ["phase_condition"]
    assert "out of date" not in found.reason


def test_a_series_arriving_is_not_a_flip(registry):
    """Unevaluable at the anchor and met now is data arriving, not the tape moving."""
    the_set = w39(clauses=[clause(1, {"op": "gt", "series": "move", "value": 90})])
    found = run(registry, tape(reading("move", "2026-09-23", "95")), the_set)
    assert found.items == ()


def test_a_band_left_counts_once_and_names_the_level(registry):
    the_set = w39(targets=[band("t-vix", "vix", "13", "19")])
    found = run(registry, tape(reading("vix", "2026-09-23", "22.00")), the_set)
    assert [(one.kind, one.before, one.after) for one in found.items] == [
        ("band", "in band", "out of band")
    ]
    assert found.items[0].sentence == "vix 13-19 left S2's band at 22.00."


def test_a_large_level_is_printed_whole():
    assert drift._level(Decimal("86119.13286275722")) == "86,119"
    assert drift._level(Decimal("14.2100")) == "14.21"


def test_a_setup_gate_opening_weighs_two(registry):
    gated = Setup(
        setup_id="st1",
        week="2026-W39",
        asset="equities",
        name="S&P FOMC-shelf FTR long",
        series_key="spx_cash",
        entry_lo=Decimal("7500"),
        entry_hi=Decimal("7560"),
        invalidation=Decimal("7450"),
        invalidation_dir="below",
        target=Decimal("7800"),
        rr_planned=Decimal("3.4"),
        gate_expr={"op": "gt", "series": "breadth_spx_above_200dma", "value": 52},
        gate_text="breadth above 52%",
        sector_focus_id=None,
        activated_on=ANCHOR,
    )
    found = run(
        registry,
        tape(
            reading("breadth_spx_above_200dma", "2026-09-23", "53.00"),
            reading("spx_cash", "2026-09-23", "7770.00", high="7790", low="7750"),
        ),
        w39(),
        setups=[gated],
    )
    assert [(one.kind, one.before, one.after, one.weight) for one in found.items] == [
        ("setup_gate", "blocked", "open", 2)
    ]


def test_only_a_newly_listed_acute_tail_risk_is_material(registry):
    risks = [
        # Connected on the 19th: everything listed that day was already there.
        drift.TailRiskSeen("Houthi strikes on Saudi lines", "acute", date(2026, 9, 19)),
        drift.TailRiskSeen("Hormuz enforcement returns", "acute", date(2026, 9, 22)),
        drift.TailRiskSeen("Private credit markdowns", "elevated", date(2026, 9, 22)),
    ]
    found = run(registry, tape(), w39(), risks=risks)
    assert [one.subject for one in found.items] == ["Hormuz enforcement returns"]
    assert found.material == 1


def test_a_retired_acute_tail_risk_is_not_news(registry):
    """Dropped from the weekly note, the risk is off the page and out of drift."""
    risks = [
        drift.TailRiskSeen("the feed's first day", "watch", date(2026, 9, 19)),
        drift.TailRiskSeen(
            "Hormuz enforcement returns", "acute", date(2026, 9, 22), retired_on=date(2026, 9, 23)
        ),
    ]
    found = run(registry, tape(), w39(), risks=risks)
    assert found.items == ()


def test_the_first_ingest_of_the_tail_risk_file_is_not_news(registry):
    """The feed was first read on the 23rd; the Iran risk had been listed since the 9th."""
    risks = [
        drift.TailRiskSeen("Iran-war tanker and oil-chokepoint escalation", "acute",
                           date(2026, 9, 23)),
        drift.TailRiskSeen("Crowded AI-hardware unwind", "elevated", date(2026, 9, 23)),
    ]
    found = run(registry, tape(), w39(), risks=risks)
    assert found.items == ()


def test_the_score_is_capped_at_ten(registry):
    risks = [drift.TailRiskSeen("the feed's first day", "watch", date(2026, 9, 19))]
    risks += [drift.TailRiskSeen(f"risk {n}", "acute", date(2026, 9, 22)) for n in range(5)]
    found = run(registry, tape(), w39(), risks=risks)
    assert found.score == 10
    assert len(found.items) == 5


def test_a_weekly_series_is_compared_with_its_last_reading_before_the_call(registry):
    """The W39 ETF band, as it happened on 23 September.

    The ETF week printed +$6.2M on the 18th and nothing on the 21st anchor, so
    an exact-day baseline read unevaluable and drift missed the week turning
    into +$1.71B. The baseline is the last day the band was measured.
    """
    the_set = w39(
        targets=[
            ScenarioTarget(
                target_id="t-etf",
                scenario_id="sc2",
                series_key="btc_etf_weekly_flow",
                label="BTC ETF week positive but under +$700M",
                kind="band",
                comparator="lt",
                lo=None,
                hi=Decimal("700000000"),
                text=None,
            )
        ]
    )
    found = run(
        registry,
        tape(
            reading("btc_etf_weekly_flow", "2026-09-18", "6200000"),
            reading("btc_etf_weekly_flow", "2026-09-23", "1713950000"),
        ),
        the_set,
    )
    assert [(one.kind, one.before, one.after) for one in found.items] == [
        ("band", "in band", "out of band")
    ]


def test_a_reading_older_than_the_lookback_is_not_a_baseline(registry):
    the_set = w39(targets=[band("t-vix", "vix", "13", "19")])
    old = history(
        reading("vix", "2026-09-01", "14.00"),
        reading("vix", "2026-09-23", "22.00"),
    )
    assert run(registry, old, the_set).items == ()
