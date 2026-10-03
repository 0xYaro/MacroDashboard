"""The condition expression form, and the one rule the whole system rests on.

An expression referencing a series with no observation for the evaluation date
resolves to `unevaluable`, never to false. Everything in this file is either
that rule or a comparison whose answer can be checked by reading it.
"""

from __future__ import annotations

import pytest

from macro.evaluate import phase, score_condition
from macro.expr import Context, ExpressionRefused, evaluate
from macro.model import AssertedCondition, PhaseCall, PhaseCondition, Status

from .conftest import context, history, instant, reading


def tape():
    return history(
        reading("breadth_spx_above_200dma", "2026-09-18", "49.50"),
        reading("hy_oas_bps", "2026-09-18", "270"),
        reading("vix", "2026-09-18", "14.81"),
        reading("move", "2026-09-18", "80.64"),
        reading("brent", "2026-09-18", "103.87"),
    )


def ctx(registry, asof: str = "2026-09-18") -> Context:
    return context(registry, tape(), asof)


# ---------------------------------------------------------------------------
# Comparisons
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ({"op": "lt", "series": "breadth_spx_above_200dma", "value": 45}, Status.NOT_YET),
        ({"op": "lt", "series": "breadth_spx_above_200dma", "value": 50}, Status.MET),
        ({"op": "gt", "series": "hy_oas_bps", "value": 300}, Status.NOT_YET),
        ({"op": "gt", "series": "hy_oas_bps", "value": 250}, Status.MET),
        ({"op": "gte", "series": "hy_oas_bps", "value": 270}, Status.MET),
        ({"op": "lte", "series": "hy_oas_bps", "value": 270}, Status.MET),
        ({"op": "eq", "series": "hy_oas_bps", "value": 270}, Status.MET),
        ({"op": "between", "series": "brent", "lo": 92, "hi": 112}, Status.MET),
        ({"op": "between", "series": "brent", "lo": 80, "hi": 95}, Status.NOT_YET),
        ({"op": "outside", "series": "brent", "lo": 80, "hi": 95}, Status.MET),
    ],
)
def test_operators(registry, expression, expected):
    assert evaluate(expression, ctx(registry)).status is expected


def test_a_band_is_inclusive_at_both_ends(registry):
    """A band is a region a price occupies, so a value on a bound is inside it.

    103.87 is Brent's close. A band of exactly [103.87, 103.87] holds.
    """
    assert (
        evaluate(
            {"op": "between", "series": "brent", "lo": "103.87", "hi": "103.87"}, ctx(registry)
        ).status
        is Status.MET
    )


def test_the_unit_is_the_series_own(registry):
    """`hy_oas_bps` takes 292, never 2.92.

    The stored value is 270 basis points. A threshold written in percent is not
    converted and not rescued: it is compared as given, which is exactly why the
    unit is a property of the series and every condition is written in it.
    """
    assert (
        evaluate({"op": "lt", "series": "hy_oas_bps", "value": 292}, ctx(registry)).status
        is Status.MET
    )
    assert (
        evaluate({"op": "lt", "series": "hy_oas_bps", "value": "2.92"}, ctx(registry)).status
        is Status.NOT_YET
    )


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------


def test_a_missing_observation_is_unevaluable_and_never_false(registry):
    """The load-bearing rule. Both halves of it are asserted here.

    Not `met`, which would be an invented pass, and not `not yet`, which is what
    a two-valued evaluator would return and what the whole `unevaluable` state
    exists to prevent.
    """
    empty = context(registry, history(), "2026-09-18")
    outcome = evaluate({"op": "lt", "series": "breadth_spx_above_200dma", "value": 45}, empty)
    assert outcome.status is Status.UNEVALUABLE
    assert outcome.status is not Status.MET
    assert outcome.status is not Status.NOT_YET
    assert "no observation" in outcome.evidence["why"]


def test_a_null_value_is_unevaluable_and_never_zero(registry):
    """A Tier 4 reading is a null with a note, and a null is not a small number."""
    tier_four = history(
        reading("breadth_spx_above_200dma", "2026-09-18", None, display="couldn't retrieve", tier=4)
    )
    outcome = evaluate(
        {"op": "lt", "series": "breadth_spx_above_200dma", "value": 45},
        context(registry, tier_four, "2026-09-18"),
    )
    assert outcome.status is Status.UNEVALUABLE


def test_a_reading_from_another_day_does_not_satisfy_today(registry):
    """No value is carried forward into a day it did not describe."""
    yesterday = history(reading("breadth_spx_above_200dma", "2026-09-17", "50.29"))
    outcome = evaluate(
        {"op": "lt", "series": "breadth_spx_above_200dma", "value": 45},
        context(registry, yesterday, "2026-09-18"),
    )
    assert outcome.status is Status.UNEVALUABLE


# ---------------------------------------------------------------------------
# Combinators
# ---------------------------------------------------------------------------


def test_all_is_false_as_soon_as_one_term_is(registry):
    """A conjunction with a false term cannot become true, so the false wins.

    VIX 14.81 is not above 22, which settles it whatever the unreadable term
    turns out to be.
    """
    outcome = evaluate(
        {
            "all": [
                {"op": "gt", "series": "vix", "value": 22},
                {"op": "gt", "series": "cnn_fear_greed", "value": 50},  # no reading
            ]
        },
        ctx(registry),
    )
    assert outcome.status is Status.NOT_YET


def test_all_is_unevaluable_when_every_readable_term_holds(registry):
    """Nothing is false, one thing is unknown, so the answer is unknown."""
    outcome = evaluate(
        {
            "all": [
                {"op": "lt", "series": "vix", "value": 22},
                {"op": "gt", "series": "cnn_fear_greed", "value": 50},  # no reading
            ]
        },
        ctx(registry),
    )
    assert outcome.status is Status.UNEVALUABLE


def test_any_is_true_as_soon_as_one_term_is(registry):
    outcome = evaluate(
        {
            "any": [
                {"op": "lt", "series": "vix", "value": 22},
                {"op": "gt", "series": "cnn_fear_greed", "value": 50},  # no reading
            ]
        },
        ctx(registry),
    )
    assert outcome.status is Status.MET


def test_any_is_unevaluable_while_an_unknown_term_could_still_fire(registry):
    outcome = evaluate(
        {
            "any": [
                {"op": "gt", "series": "vix", "value": 22},
                {"op": "gt", "series": "cnn_fear_greed", "value": 50},  # no reading
            ]
        },
        ctx(registry),
    )
    assert outcome.status is Status.UNEVALUABLE


def test_n_of_counts_met_and_never_renormalises(registry):
    """One of three, where one holds: `met`. The threshold does not move.

    breadth 49.50 is under 60, so clause one holds. VIX is not above 22 and HY
    is not above 300, so the other two do not. One of one is enough.
    """
    outcome = evaluate(
        {
            "n_of": 1,
            "of": [
                {"op": "lt", "series": "breadth_spx_above_200dma", "value": 60},
                {"op": "gt", "series": "vix", "value": 22},
                {"op": "gt", "series": "hy_oas_bps", "value": 300},
            ],
        },
        ctx(registry),
    )
    assert outcome.status is Status.MET
    assert outcome.evidence["n_of"] == 1
    assert outcome.evidence["met"] == 1


def test_n_of_is_unevaluable_while_the_unknowns_could_still_reach_the_threshold(registry):
    """Two of three, one met and one unknown: the threshold is still reachable.

    The threshold stays at two. It is never reduced because a clause could not
    be read, which would quietly make the scenario easier to satisfy.
    """
    outcome = evaluate(
        {
            "n_of": 2,
            "of": [
                {"op": "lt", "series": "breadth_spx_above_200dma", "value": 60},
                {"op": "gt", "series": "cnn_fear_greed", "value": 50},  # no reading
                {"op": "gt", "series": "vix", "value": 22},
            ],
        },
        ctx(registry),
    )
    assert outcome.status is Status.UNEVALUABLE
    assert outcome.evidence["n_of"] == 2
    assert outcome.evidence["met"] == 1
    assert outcome.evidence["unevaluable"] == 1


def test_n_of_is_false_once_the_unknowns_cannot_reach_the_threshold(registry):
    outcome = evaluate(
        {
            "n_of": 3,
            "of": [
                {"op": "lt", "series": "breadth_spx_above_200dma", "value": 60},
                {"op": "gt", "series": "cnn_fear_greed", "value": 50},  # no reading
                {"op": "gt", "series": "vix", "value": 22},
            ],
        },
        ctx(registry),
    )
    assert outcome.status is Status.NOT_YET


def test_n_of_refuses_a_threshold_outside_its_clause_count(registry):
    with pytest.raises(ExpressionRefused, match=r"outside 1\.\.2"):
        evaluate(
            {"n_of": 3, "of": [{"op": "gt", "series": "vix", "value": 1}] * 2},
            ctx(registry),
        )


# ---------------------------------------------------------------------------
# Manual, and what the registry refuses
# ---------------------------------------------------------------------------


def test_a_manual_expression_is_reported_and_never_scored(registry):
    outcome = evaluate({"manual": "Rallies fail at prior support"}, ctx(registry))
    assert outcome.status is Status.UNEVALUABLE
    assert "awaiting judgment" in outcome.evidence["why"]


def test_a_prose_series_may_not_appear_in_a_comparison(registry):
    """Without this a prose row and a Tier 4 failure compare the same."""
    with pytest.raises(ExpressionRefused, match="prose row"):
        evaluate({"op": "gt", "series": "fed_funds", "value": 4}, ctx(registry))


def test_an_unknown_series_is_refused_rather_than_read_as_missing(registry):
    """A typo in a key is a configuration fault, not a series that went quiet."""
    with pytest.raises(ExpressionRefused, match="does not declare"):
        evaluate({"op": "gt", "series": "breadth_spx_above_200sma", "value": 45}, ctx(registry))


def test_a_malformed_expression_scores_as_unevaluable_not_false(registry):
    """A fault is never a false, even at the condition boundary.

    `evaluate` raises, and `score_condition` turns that into `unevaluable` with
    the diagnosis in the evidence, because a tracker that reads "not yet"
    because someone mistyped a key is worse than one that says it cannot tell.
    """
    scored = score_condition(
        condition_id="c1",
        owner_kind="phase",
        owner_id="p1",
        ordinal=1,
        text="breadth under 45",
        kind="expr",
        expression={"op": "lt", "series": "no_such_series", "value": 45},
        ctx=ctx(registry),
    )
    assert scored.state.status is Status.UNEVALUABLE
    assert scored.state.source == "computed"
    assert "does not declare" in scored.state.evidence["why"]


# ---------------------------------------------------------------------------
# Manual conditions and the asserted status
# ---------------------------------------------------------------------------


def test_a_manual_condition_with_no_assertion_is_unevaluable(registry):
    scored = score_condition(
        condition_id="c1",
        owner_kind="phase",
        owner_id="p1",
        ordinal=1,
        text="Rallies fail at prior support",
        kind="manual",
        expression=None,
        ctx=ctx(registry),
    )
    assert scored.state.status is Status.UNEVALUABLE
    assert scored.state.source == "asserted"


def test_a_manual_condition_carries_its_asserted_status(registry):
    scored = score_condition(
        condition_id="c1",
        owner_kind="phase",
        owner_id="p1",
        ordinal=1,
        text="Rallies fail at prior support",
        kind="manual",
        expression=None,
        ctx=ctx(registry),
        asserted=AssertedCondition(
            condition_id="c1",
            owner_kind="phase",
            status=Status.MET,
            current_text="the Sep 16 low was bought back in two sessions",
            asserted_by="authoring",
        ),
    )
    assert scored.state.status is Status.MET
    assert scored.state.source == "asserted"
    assert scored.state.current_display is not None


# ---------------------------------------------------------------------------
# A phase call's four conditions
# ---------------------------------------------------------------------------
#
# A fresh call's four are what must stay true for its phase to hold, so each
# one holds or has failed. A carried call is one of the fourteen migrated
# journal rows, whose four counted toward the next phase and are scored as
# written.


def call(conditions, **overrides) -> PhaseCall:
    return PhaseCall(
        phase_call_id="p1",
        asset="equities",
        week="2026-W39",
        phase=3,
        phase_name="Late Cycle / Distribution",
        activated_at=instant("2026-09-21T01:30:00"),
        conditions=tuple(conditions),
        **overrides,
    )


def four(kinds, exprs, **overrides) -> PhaseCall:
    return call(
        (
            PhaseCondition(
                condition_id=f"c{index}",
                phase_call_id="p1",
                ordinal=index,
                text=f"condition {index}",
                kind=kind,
                expr=expr,
            )
            for index, (kind, expr) in enumerate(zip(kinds, exprs, strict=True), start=1)
        ),
        **overrides,
    )


#: W39's equities read held against what Phase 3 rests on: breadth not broadly
#: confirming, VIX and HY calm. On the fixture tape the first three hold and
#: MOVE above 90 does not.
HOLDS = [
    {"op": "lt", "series": "breadth_spx_above_200dma", "value": 60},
    {"op": "lt", "series": "vix", "value": 22},
    {"op": "lt", "series": "hy_oas_bps", "value": 300},
    {"op": "gt", "series": "move", "value": 90},  # 80.64, so it does not hold
]


def test_an_unevaluable_condition_never_scores_as_met_or_as_failed(registry):
    """The contract's rule, asserted three ways at once.

    Three conditions hold and the fourth cannot be read. The unevaluable one is
    counted in `unevaluable` and in neither `met`, `failed` nor
    `machine_checked`: it did not hold, and it did not fail either.
    """
    score, states = phase(
        four(
            ["expr", "expr", "expr", "expr"],
            [*HOLDS[:3], {"op": "gt", "series": "cnn_fear_greed", "value": 50}],  # no reading
            classification="fresh",
        ),
        ctx=ctx(registry),
        asserted={},
        activated_on=None,
    )
    assert score.met == 3
    assert score.of == 4
    assert score.unevaluable == 1
    assert score.failed == 0
    assert score.machine_checked == 3
    assert score.read_stale is False

    unreadable = states[3]
    assert unreadable.status is Status.UNEVALUABLE
    assert unreadable.status not in (Status.MET, Status.FAILED, Status.NOT_YET)


def test_a_fresh_condition_that_no_longer_holds_has_failed_not_not_yet(registry):
    """There is no "yet" on something that must stay true.

    The expression answers `not yet` for a false comparison. On a fresh call
    that is recorded as `failed`, because the thing the read rested on is not
    true any more.
    """
    score, states = phase(
        four(["expr"] * 4, HOLDS, classification="fresh"),
        ctx=ctx(registry),
        asserted={},
        activated_on=None,
    )
    assert [one.status for one in states] == [
        Status.MET, Status.MET, Status.MET, Status.FAILED
    ]
    assert (score.met, score.failed) == (3, 1)
    assert score.read_stale is False, "one failed condition is a crack, not a stale read"


def test_two_failed_conditions_mean_the_read_may_be_out_of_date(registry):
    from macro.evaluate import stale_read_finding

    fresh = four(
        ["expr"] * 4,
        [*HOLDS[:2], {"op": "gt", "series": "hy_oas_bps", "value": 300}, HOLDS[3]],
        classification="fresh",
    )
    score, _ = phase(fresh, ctx=ctx(registry), asserted={}, activated_on=None)
    assert (score.met, score.failed) == (2, 2)
    assert score.read_stale is True
    # Flagged, never acted on: the call is still the one that was read.
    assert score.phase == 3

    finding = stale_read_finding(fresh, score)
    assert finding is not None
    assert finding.check_key == "phase_read_stale"
    assert "may be out of date" in finding.sentence
    assert "read fresh again on Monday" in finding.sentence


def test_a_carried_call_is_scored_as_written_and_never_goes_stale(registry):
    """The fourteen migrated calls counted toward the next phase.

    Their unmet conditions are `not yet`, as the journals meant them, and two
    of them unmet says nothing about the current read.
    """
    from macro.evaluate import stale_read_finding

    carried = four(
        ["expr"] * 4,
        [*HOLDS[:2], {"op": "gt", "series": "hy_oas_bps", "value": 300}, HOLDS[3]],
    )
    assert carried.classification == "carried"
    score, states = phase(carried, ctx=ctx(registry), asserted={}, activated_on=None)
    assert states[2].status is Status.NOT_YET
    assert (score.met, score.failed) == (2, 0)
    assert score.read_stale is False
    assert stale_read_finding(carried, score) is None


def test_an_asserted_manual_condition_holds_on_its_assertion(registry):
    """A manual condition the author judged `met` holds; one with no assertion is unread."""
    judged = four(
        ["expr", "expr", "manual", "expr"],
        [HOLDS[0], HOLDS[1], None, HOLDS[3]],
        classification="fresh",
    )
    asserted = {
        "c3": AssertedCondition(
            condition_id="c3",
            owner_kind="phase",
            status=Status.MET,
            current_text="leadership compressed to a single trade",
            asserted_by="authoring",
        )
    }
    score, _ = phase(judged, ctx=ctx(registry), asserted=asserted, activated_on=None)
    assert score.met == 3
    assert score.manual == 1
    assert score.machine_checked == 3
    assert score.unevaluable == 0

    unjudged, _ = phase(judged, ctx=ctx(registry), asserted={}, activated_on=None)
    assert unjudged.met == 2
    assert unjudged.unevaluable == 1


# ---------------------------------------------------------------------------
# The `field` accessor
# ---------------------------------------------------------------------------
#
# It reads one named key from `extra`, one level deep, on a figure the worker
# already computed. No arithmetic, no derivation, no traversal.


def w39_extras():
    """The real W39 tape, with the `extra` the fetchers actually store.

    `spx_cash` closed 7,650.50 on 2026-09-18 at 6.51% over a rising 200-day
    average, and the 10Y's 2s10s spread was 25 bps. Both figures are in
    `2026-W39.points.json`.
    """
    spx = reading("spx_cash", "2026-09-18", "7650.50", high="7657.17", low="7610.52")
    spx.extra.update({"dma200_pct": "6.51", "dma200_slope": "rising"})
    ten_year = reading("ust_10y", "2026-09-18", "5.01")
    ten_year.extra["spread_2s10s_bps"] = "25.00"
    managers = reading("cftc_asset_managers_es", "2026-09-15", "36.98")
    managers.extra["band"] = "neutral"
    funds = reading("cftc_leveraged_funds_es", "2026-09-15", "-11.98")
    funds.extra["band"] = "neutral"
    return history(spx, ten_year, managers, funds)


def extras_ctx(registry, asof: str = "2026-09-18") -> Context:
    return context(registry, w39_extras(), asof)


def test_below_a_declining_200dma_is_false_on_the_w39_tape_and_not_unevaluable(registry):
    """The tracker row the accessor exists for, against the real numbers.

    W39's equities tracker condition 1 reads "Lower lows below a declining
    200dma". On 2026-09-18 `spx_cash` sat 6.51% *over* a *rising* 200-day
    average, so both halves are false and the condition is `not yet`.

    Before the accessor this row could only be written as a `failed_retest` on
    the price, which captured "rallies fail at prior support" and left the
    200dma half unreachable: the tracker scored against a subset of its own
    recipe and reported `unevaluable` for the part it could not see.
    """
    below = evaluate(
        {"op": "lt", "series": "spx_cash", "field": "dma200_pct", "value": 0},
        extras_ctx(registry),
    )
    assert below.status is Status.NOT_YET
    assert below.status is not Status.UNEVALUABLE
    assert below.evidence["current"] == "6.51"
    assert below.evidence["field"] == "dma200_pct"
    assert below.evidence["unit"] == "percent"

    declining = evaluate(
        {"op": "eq", "series": "spx_cash", "field": "dma200_slope", "value": "falling"},
        extras_ctx(registry),
    )
    assert declining.status is Status.NOT_YET
    assert declining.evidence["current"] == "rising"

    both = evaluate(
        {
            "all": [
                {"op": "lt", "series": "spx_cash", "field": "dma200_pct", "value": 0},
                {"op": "eq", "series": "spx_cash", "field": "dma200_slope", "value": "falling"},
            ]
        },
        extras_ctx(registry),
    )
    assert both.status is Status.NOT_YET


def test_over_a_rising_200dma_is_met_on_the_same_tape(registry):
    """The same two fields, asserted the way they are actually true.

    6.51 is above 0 and the slope reads `rising`, so the conjunction is `met`.
    Asserting the true reading as well as the false one is what proves the
    accessor is reading the figure rather than failing consistently.
    """
    outcome = evaluate(
        {
            "all": [
                {"op": "gt", "series": "spx_cash", "field": "dma200_pct", "value": 0},
                {"op": "eq", "series": "spx_cash", "field": "dma200_slope", "value": "rising"},
            ]
        },
        extras_ctx(registry),
    )
    assert outcome.status is Status.MET


def test_a_field_absent_from_extra_is_unevaluable_and_never_false(registry):
    """Exactly a missing observation, and for the same reason.

    The figure the condition names was not computed for this day, and a
    condition over a figure nobody measured is not false.
    """
    bare = history(reading("spx_cash", "2026-09-18", "7650.50"))
    outcome = evaluate(
        {"op": "lt", "series": "spx_cash", "field": "dma200_pct", "value": 0},
        context(registry, bare, "2026-09-18"),
    )
    assert outcome.status is Status.UNEVALUABLE
    assert outcome.status is not Status.NOT_YET
    assert outcome.status is not Status.MET
    assert "carries no 'dma200_pct'" in outcome.evidence["why"]
    assert outcome.evidence["available"] == []


def test_a_null_field_is_unevaluable(registry):
    """`dma200_pct` is null until 200 closes exist. Null, never zero."""
    early = reading("spx_cash", "2026-09-18", "7650.50")
    early.extra["dma200_pct"] = None
    outcome = evaluate(
        {"op": "lt", "series": "spx_cash", "field": "dma200_pct", "value": 0},
        context(registry, history(early), "2026-09-18"),
    )
    assert outcome.status is Status.UNEVALUABLE
    assert "is null" in outcome.evidence["why"]


def test_a_missing_observation_is_still_unevaluable_with_a_field(registry):
    outcome = evaluate(
        {"op": "lt", "series": "spx_cash", "field": "dma200_pct", "value": 0},
        context(registry, history(), "2026-09-18"),
    )
    assert outcome.status is Status.UNEVALUABLE


def test_the_cftc_band_is_reachable_and_compares_as_text(registry):
    """Four words the fetcher already assigns, unreachable until now."""
    neutral = evaluate(
        {"op": "eq", "series": "cftc_asset_managers_es", "field": "band", "value": "neutral"},
        extras_ctx(registry, "2026-09-15"),
    )
    assert neutral.status is Status.MET

    heavy = evaluate(
        {"op": "eq", "series": "cftc_asset_managers_es", "field": "band", "value": "heavy"},
        extras_ctx(registry, "2026-09-15"),
    )
    assert heavy.status is Status.NOT_YET
    assert heavy.evidence["current"] == "neutral"


def test_the_2s10s_spread_is_reachable_in_basis_points(registry):
    """25 bps on 2026-09-18, which `2026-W39.points.json` also carries."""
    outcome = evaluate(
        {"op": "lt", "series": "ust_10y", "field": "spread_2s10s_bps", "value": 0},
        extras_ctx(registry),
    )
    assert outcome.status is Status.NOT_YET
    assert outcome.evidence["current"] == "25.00"
    assert outcome.evidence["unit"] == "bps"


def test_the_accessor_cannot_traverse(registry):
    """One key, one level deep. A dotted path is not a field."""
    with pytest.raises(ExpressionRefused, match="not a field this worker stores"):
        evaluate(
            {"op": "lt", "series": "spx_cash", "field": "extra.dma200_pct", "value": 0},
            extras_ctx(registry),
        )


# ---------------------------------------------------------------------------
# Validation, at write time
# ---------------------------------------------------------------------------


def test_only_eq_compares_against_a_text_field(registry):
    """Refused at validation, so it fails when written and not when it matters."""
    from macro.expr import validate

    validate(
        {"op": "eq", "series": "cftc_asset_managers_es", "field": "band", "value": "heavy"},
        registry,
    )
    for op in ("lt", "gt", "gte", "lte", "between"):
        with pytest.raises(ExpressionRefused, match="only `eq` compares"):
            validate(
                {
                    "op": op,
                    "series": "cftc_asset_managers_es",
                    "field": "band",
                    "value": "heavy",
                },
                registry,
            )


def test_a_typo_in_a_band_name_is_refused_rather_than_false_for_ever(registry):
    from macro.expr import validate

    with pytest.raises(ExpressionRefused, match="false for ever"):
        validate(
            {"op": "eq", "series": "cftc_asset_managers_es", "field": "band", "value": "hevy"},
            registry,
        )


def test_a_field_the_named_series_does_not_carry_is_refused(registry):
    """Known key, wrong series: it would be unevaluable for ever while looking
    perfectly well formed."""
    from macro.expr import validate

    with pytest.raises(ExpressionRefused, match="does not carry"):
        validate({"op": "lt", "series": "vix", "field": "dma200_pct", "value": 0}, registry)


def test_an_unknown_field_is_refused_and_says_what_is_available(registry):
    from macro.expr import validate

    with pytest.raises(ExpressionRefused, match="not a field this worker stores"):
        validate({"op": "lt", "series": "spx_cash", "field": "rsi", "value": 30}, registry)


def test_validation_accepts_every_well_formed_shape(registry):
    from macro.expr import validate

    for expression in (
        {"op": "lt", "series": "breadth_spx_above_200dma", "value": 45},
        {"op": "between", "series": "brent", "lo": 92, "hi": 112},
        {"op": "lt", "series": "spx_cash", "field": "dma200_pct", "value": 0},
        {"op": "eq", "series": "spx_cash", "field": "dma200_slope", "value": "rising"},
        {"all": [{"op": "gt", "series": "vix", "value": 22}]},
        {"n_of": 1, "of": [{"op": "gt", "series": "vix", "value": 22}]},
        {"manual": "Rallies fail at prior support"},
        {
            "predicate": "gate_at_entry",
            "args": {
                "series": "spx_cash",
                "entry_lo": 7500,
                "entry_hi": 7560,
                "within_sessions": 5,
                "gate": {"op": "lt", "series": "hy_oas_bps", "value": 292},
            },
        },
    ):
        validate(expression, registry)


def test_validation_refuses_what_evaluation_would_refuse(registry):
    from macro.expr import validate

    for expression, message in (
        ({"op": "lt", "series": "no_such_series", "value": 1}, "does not declare"),
        ({"op": "lt", "series": "fed_funds", "value": 1}, "prose row"),
        ({"op": "nope", "series": "vix", "value": 1}, "is not one of"),
        ({"op": "between", "series": "vix", "lo": 30, "hi": 20}, "above hi"),
        ({"n_of": 5, "of": [{"op": "gt", "series": "vix", "value": 1}]}, r"outside 1\.\.1"),
        ({"predicate": "not_a_predicate", "args": {}}, "is not one of"),
        ({"manual": "  "}, "states nothing"),
        ({"nonsense": True}, "has no op"),
    ):
        with pytest.raises(ExpressionRefused, match=message):
            validate(expression, registry)


def test_validation_reaches_inside_a_nested_gate(registry):
    """`gate_at_entry` takes an expression, so a typo in it must fail here too."""
    from macro.expr import validate

    with pytest.raises(ExpressionRefused, match="does not carry"):
        validate(
            {
                "predicate": "gate_at_entry",
                "args": {
                    "series": "spx_cash",
                    "entry_lo": 7500,
                    "entry_hi": 7560,
                    "within_sessions": 5,
                    "gate": {"op": "lt", "series": "vix", "field": "band", "value": "heavy"},
                },
            },
            registry,
        )


def test_the_declared_dma200_series_are_the_ones_yahoo_computes_it_for():
    """The field table is declared in `expr`, not imported from a fetcher.

    The expression form must not depend on a fetcher, so the two are kept apart
    and this is what keeps them true.
    """
    from macro.expr import EXTRA_FIELDS
    from macro.fetchers import yahoo

    computed = {spec.series_key for spec in yahoo.SPECS if spec.dma200}
    assert EXTRA_FIELDS["dma200_pct"].series == computed
    assert EXTRA_FIELDS["dma200_slope"].series == computed


# ---------------------------------------------------------------------------
# The stage
# ---------------------------------------------------------------------------


def called(**overrides) -> PhaseCall:
    """A W39 equities call with four machine-checkable conditions."""
    return four(["expr"] * 4, HOLDS, **overrides)


def scored(registry, call: PhaseCall):
    score, _ = phase(call, ctx=ctx(registry), asserted={}, activated_on=None)
    return score


def test_stage_is_carried_and_never_derived_from_the_conditions(registry):
    """Depth inside the phase is authored, whatever the four conditions read."""
    for authored in ("early", "mid", "late", None):
        score = scored(registry, called(stage=authored, classification="fresh"))
        assert score.stage == authored
        assert score.met == 3  # the conditions never move with it


def test_no_stage_is_null_rather_than_guessed(registry):
    score = scored(registry, called())
    assert score.stage is None


# ---------------------------------------------------------------------------
# Series published after the day they describe
# ---------------------------------------------------------------------------

PCE_IN_BAND = {"op": "between", "series": "pce_core_mom", "lo": 0.2, "hi": 0.3}
HY_IN_BAND = {"op": "between", "series": "hy_oas_bps", "lo": 270, "hi": 350}


def clause_ctx(registry, hist, asof: str) -> Context:
    """A W40 trigger clause: activated 2026-09-29, scored on a later session."""
    base = context(registry, hist, asof, not_before="2026-09-29")
    return Context(
        asof=base.asof,
        history=base.history,
        registry=base.registry,
        not_before=base.not_before,
        forward=True,
    )


def test_a_clause_on_a_monthly_print_is_scored_once_the_print_lands(registry):
    """W40 in production: August core PCE printed 0.25% and the clause stayed unevaluable.

    A monthly print is dated its month's end, so it never has a reading on the
    session a clause is scored on.
    """
    hist = history(
        reading("pce_core_mom", "2026-07-31", "0.2455"),
        reading("pce_core_mom", "2026-08-31", "0.2474"),
    )
    outcome = evaluate(PCE_IN_BAND, clause_ctx(registry, hist, "2026-10-02"))
    assert outcome.status is Status.MET
    assert outcome.evidence["current"] == "0.2474"


def test_a_clause_is_never_settled_by_the_print_before_it_went_live(registry):
    """July's 0.25% sits inside the band and was public a month before W40 began."""
    hist = history(reading("pce_core_mom", "2026-07-31", "0.2455"))
    outcome = evaluate(PCE_IN_BAND, clause_ctx(registry, hist, "2026-09-29"))
    assert outcome.status is Status.UNEVALUABLE
    assert "before this clause went live" in outcome.evidence["why"]


def test_a_standing_condition_reads_the_current_monthly_print(registry):
    """A phase condition is about the state, so the print in force is the reading."""
    hist = history(reading("pce_core_mom", "2026-07-31", "0.2455"))
    outcome = evaluate(PCE_IN_BAND, context(registry, hist, "2026-09-29"))
    assert outcome.status is Status.MET


def test_a_series_published_a_business_day_late_is_read_at_its_newest_print(registry):
    """W40's HY band was blank on most runs: FRED posts Friday's spread on Monday."""
    hist = history(reading("hy_oas_bps", "2026-09-25", "293"))
    assert evaluate(HY_IN_BAND, context(registry, hist, "2026-09-28")).status is Status.MET

    # By Tuesday the Monday print was due, so Friday's is no longer the state.
    late = evaluate(HY_IN_BAND, context(registry, hist, "2026-09-29"))
    assert late.status is Status.UNEVALUABLE
    assert "was due by" in late.evidence["why"]


def test_a_price_is_still_read_on_the_evaluation_date_exactly(registry):
    """A Friday close is never read as a Monday one."""
    hist = history(reading("vix", "2026-09-25", "14.87"))
    vix_calm = {"op": "lt", "series": "vix", "value": 20}
    outcome = evaluate(vix_calm, context(registry, hist, "2026-09-28"))
    assert outcome.status is Status.UNEVALUABLE
