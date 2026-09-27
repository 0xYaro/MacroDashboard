"""Every typed expression this package authors, run through the worker's evaluator.

This is the test the package exists for. A condition stored with a typed
expression that the 21:00 rescore cannot resolve is worse than a `manual` one:
a manual condition reports "awaiting judgment" and is visibly unscored, while a
refused expression reports `unevaluable` and looks exactly like a series that
simply has no print today.

The evaluator here is the worker's own `macro.expr`, reached through the one
seam in `worker_bridge`. Checking against a lookalike written in this package
would prove that two of our own modules agree, which is not the thing worth
knowing.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from macro_agent.validate import check
from macro_agent.worker_bridge import evaluator, registry

from .conftest import full

ASOF = date(2026, 9, 28)


def _every_expression(found) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    for call in found.phase_calls:
        for condition in call.conditions:
            if condition.expr is not None:
                out.append((f"{call.asset} condition {condition.ordinal}", condition.expr))
    for scenario in found.scenario_set.scenarios:
        for clause in scenario.clauses:
            if clause.expr is not None:
                out.append((f"S{scenario.ordinal} clause {clause.ordinal}", clause.expr))
    for setup in found.setups:
        if setup.gate_expr is not None:
            out.append((f"setup {setup.name}", setup.gate_expr))
    return out


def test_every_typed_expression_resolves_under_the_workers_evaluator(w40, settings, reads):
    """The whole point: what is written on Monday is what runs on Tuesday night.

    Resolved against an empty history, so the answer is `unevaluable` for each
    one. That is the correct answer and not a failure: a series with no reading
    for the evaluation date is exactly what `unevaluable` is for. What is being
    asserted is that the evaluator *accepts* the expression, which it refuses to
    do for an unknown series key, an unlisted predicate, a malformed shape or a
    numeric comparison against a text series.
    """
    expr, _predicates, observations = evaluator(settings)
    reg = registry(settings)
    ctx = expr.Context(asof=ASOF, history=observations.History(), registry=reg)

    found = full(w40, reads)
    expressions = _every_expression(found)
    assert len(expressions) >= 12, "the fixture stopped carrying typed expressions"

    for label, expression in expressions:
        outcome = expr.evaluate(expression, ctx)
        assert outcome.status is expr.UNEVALUABLE, (
            f"{label} resolved to {outcome.status} against an empty history; it should be "
            "unevaluable, because nothing has a reading"
        )


def test_a_typed_expression_actually_scores_when_the_tape_is_there(w40, settings, reads):
    """The same expressions, with readings, produce met and not yet.

    Unevaluable everywhere would pass the test above for a broken evaluator, so
    this one puts the W39 tape under the W40 conditions and asserts the answers
    by hand.
    """
    expr, _predicates, observations = evaluator(settings)
    reg = registry(settings)

    def reading(key: str, day: str, value: str, **extra):
        return observations.Reading(
            series_key=key,
            asof=date.fromisoformat(day),
            value=Decimal(value),
            display=value,
            tier=1,
            extra=extra,
        )

    history = observations.History(
        [
            # The W39 tape, from config/predicates.md's worked-example table.
            reading("breadth_spx_above_200dma", "2026-09-18", "49.50"),
            reading("hy_oas_bps", "2026-09-18", "270"),
            reading("vix", "2026-09-18", "14.81"),
            reading("move", "2026-09-18", "88.0"),
            reading("crypto_fear_greed", "2026-09-18", "70"),
            reading("btc_etf_weekly_flow", "2026-09-18", "6200000"),
            reading("btc_etf_inflow_streak", "2026-09-18", "2"),
        ]
    )
    ctx = expr.Context(asof=date(2026, 9, 18), history=history, registry=reg)
    found = full(w40, reads)
    by_label = dict(_every_expression(found))

    # Each W40 phase condition is something the read holds on, so on the W39
    # tape every one that has a reading holds.
    # Breadth 49.50 is under 60: the index is not broadly confirmed.
    assert expr.evaluate(by_label["equities condition 2"], ctx).status is expr.MET
    # HY 270 is under 300.
    assert expr.evaluate(by_label["equities condition 3"], ctx).status is expr.MET
    # VIX 14.81 under 22 and MOVE 88 under 90.
    assert expr.evaluate(by_label["equities condition 4"], ctx).status is expr.MET
    # Crypto F&G 70 is at or above 40.
    assert expr.evaluate(by_label["crypto condition 3"], ctx).status is expr.MET
    # The ETF week finished at +$6.2M, nowhere near a -$500M outflow.
    assert expr.evaluate(by_label["crypto condition 4"], ctx).status is expr.MET
    # BTC has no reading on this tape, so its two levels are unevaluable, never
    # false: the third value is what keeps a missing print from failing a read.
    assert expr.evaluate(by_label["crypto condition 1"], ctx).status is expr.UNEVALUABLE
    # One confirming leg of three: none of VIX>20, HY>300, breadth<45 holds.
    # Clause 5, not 4: the auction conjunction was split when the survey half
    # turned out to have no series behind it, which pushed the later ordinals on.
    assert expr.evaluate(by_label["S2 clause 5"], ctx).status is expr.NOT_YET
    # Clause 4 names `ust_10y`, which this tape does not carry, so it is
    # unevaluable rather than false. That is the whole point of the third value.
    assert expr.evaluate(by_label["S2 clause 4"], ctx).status is expr.UNEVALUABLE


def test_the_n_of_combinator_is_unknown_while_it_could_still_be_reached(w40, settings, reads):
    """A threshold is never renormalised because a clause could not be read.

    S2's confirming leg is `n_of 1 of 3`. With VIX and HY known false and breadth
    missing, one unknown still could reach the threshold of one, so the answer is
    unevaluable rather than not yet.
    """
    expr, _predicates, observations = evaluator(settings)
    reg = registry(settings)
    history = observations.History(
        [
            observations.Reading("vix", date(2026, 9, 18), Decimal("14.81"), "", 1, {}),
            observations.Reading("hy_oas_bps", date(2026, 9, 18), Decimal("270"), "", 1, {}),
        ]
    )
    ctx = expr.Context(asof=date(2026, 9, 18), history=history, registry=reg)
    found = full(w40, reads)
    clause = dict(_every_expression(found))["S2 clause 5"]
    assert expr.evaluate(clause, ctx).status is expr.UNEVALUABLE


def test_an_unknown_series_key_is_refused_before_it_is_written(w40, settings, reads):
    """The fault this check exists to catch, planted deliberately."""
    reads["phase_calls"][0]["conditions"][1]["expr"] = {
        "op": "lt",
        "series": "breadth_spx_above_200_dma",  # one underscore too many
        "value": 45,
    }
    checks = check(full(w40, reads), settings, asof=ASOF)
    refused = [one for one in checks if not one.ok and one.severity == "refuse"]
    assert any("breadth_spx_above_200_dma" in one.sentence for one in refused)


def test_a_numeric_comparison_against_a_text_series_is_refused(w40, settings, reads):
    """The five Macro & Fiscal State rows may only appear in a manual condition.

    `fedwatch_next_fomc` is the one an author reaches for: the implied odds for
    the next meeting are the most-quoted number in any trigger. It is a text
    series, so it cannot be compared, and this is where that is discovered.
    """
    w40["scenario_set"]["scenarios"][0]["clauses"][2] = {
        "ordinal": 3,
        "required": False,
        "kind": "expr",
        "text": "Oct 28 stays near a coin flip",
        "expr": {"op": "lt", "series": "fedwatch_next_fomc", "value": 60},
    }
    checks = check(full(w40, reads), settings, asof=ASOF)
    refused = [one for one in checks if not one.ok and one.severity == "refuse"]
    assert any("fedwatch_next_fomc" in one.sentence for one in refused)


def test_an_unlisted_predicate_is_refused(w40, settings, reads):
    """A fifth predicate is not added by writing one."""
    reads["phase_calls"][0]["conditions"][0]["expr"] = {
        "predicate": "accepted_above",
        "args": {"series": "spx_cash", "level": 7450},
    }
    checks = check(full(w40, reads), settings, asof=ASOF)
    assert any(
        not one.ok and "accepted_above" in one.sentence for one in checks
    )


@pytest.mark.parametrize(
    "expression",
    [
        {"op": "lt", "series": "vix"},  # no operand
        {"all": [{"op": "gt", "series": "vix", "value": 22}, "not an expression"]},
        {"nonsense": 1},
    ],
)
def test_a_malformed_expression_is_refused_rather_than_stored(expression, w40, settings, reads):
    reads["phase_calls"][0]["conditions"][1]["expr"] = expression
    checks = check(full(w40, reads), settings, asof=ASOF)
    assert any(not one.ok and one.severity == "refuse" for one in checks)
