"""R1 to R12, walked against the W40 fixture and against a break of each one.

A preflight that only ever passes is a preflight nobody would notice failing, so
each rule is tested twice: once on a set written to satisfy it, and once on the
same set with the thing that rule exists to catch put back in.

The W40 fixture is built to pass all twelve, which is what a week authored under
the rules looks like. The breaks below are drawn from the ledger's own key
misses.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from macro_agent.preflight import Holding, Tape, phase_read, report, walk

from .conftest import full

#: The W39 close and the trailing eight-week Brent range the W39 probability
#: check quotes ("sized off the last eight weeks' $83-$108 Brent range per R3").
TAPE = Tape(
    index_close=Decimal("7650.50"),
    brent_8w_lo=Decimal("83"),
    brent_8w_hi=Decimal("108"),
    btc_streak=Decimal("2"),
    eth_streak=Decimal("1"),
)


def rule(checks, number):
    return next(one for one in checks if one.key.startswith(f"r{number}_"))


def test_a_week_authored_under_the_rules_passes_all_twelve(w40, reads):
    checks = walk(full(w40, reads), TAPE)
    assert len(checks) == 12
    failing = [one for one in checks if not one.ok]
    assert not failing, report(checks)


def test_r1_catches_a_level_quoted_on_a_front_month_future(w40, reads):
    w40["setups"][0]["series_key"] = "spx_front"
    assert not rule(walk(full(w40, reads), TAPE), 1).ok


def test_r2_catches_a_one_sided_set(w40, reads):
    """Every branch banded below the close: a set with no up-case in it."""
    for scenario in w40["scenario_set"]["scenarios"]:
        for target in scenario["targets"]:
            if target.get("series_key") == "spx_cash":
                target["lo"], target["hi"] = "7000", "7400"
    assert not rule(walk(full(w40, reads), TAPE), 2).ok


def test_r2_says_so_when_it_could_not_be_checked(w40, reads):
    """A rule that was not tested says it was not tested, rather than passing quietly."""
    check = rule(walk(full(w40, reads), Tape()), 2)
    assert check.ok and check.severity == "note" and "not checked" in check.sentence


def test_r3_catches_oil_bands_narrower_than_the_range_they_were_sized_from(w40, reads):
    for scenario in w40["scenario_set"]["scenarios"]:
        for target in scenario["targets"]:
            if target.get("series_key") == "brent":
                target["lo"], target["hi"] = "95", "105"
    check = rule(walk(full(w40, reads), TAPE), 3)
    assert not check.ok
    assert "83-83" not in check.sentence
    assert "95-105" in check.sentence.replace("–", "-")


def test_r4_catches_a_risk_off_branch_with_no_confirming_leg(w40, reads):
    """W30: 7,450 fired at VIX 18.6 and breadth 65.8%, on a bare price level."""
    scenario = w40["scenario_set"]["scenarios"][1]
    # By content, not by ordinal: the confirming leg is the `n_of`, wherever the
    # clause split happens to have left it.
    scenario["clauses"] = [
        one for one in scenario["clauses"] if "n_of" not in (one.get("expr") or {})
    ]
    for index, one in enumerate(scenario["clauses"], 1):
        one["ordinal"] = index
    assert not rule(walk(full(w40, reads), TAPE), 4).ok


def test_r5_catches_a_branch_with_no_leadership_row(w40, reads):
    scenario = w40["scenario_set"]["scenarios"][0]
    scenario["targets"] = [one for one in scenario["targets"] if one["label"] != "Leadership"]
    assert not rule(walk(full(w40, reads), TAPE), 5).ok


def test_r6_catches_a_set_where_every_clause_is_bound_to_a_dated_catalyst(w40, reads):
    """Two of the last ten weeks were driven by an event no calendar carried."""
    for scenario in w40["scenario_set"]["scenarios"]:
        for clause in scenario["clauses"]:
            clause["catalyst_id"] = "CATALYST:Micron Q4 FY2026"
    assert not rule(walk(full(w40, reads), TAPE), 6).ok


def test_r7_catches_a_branch_that_does_not_band_both_books(w40, reads):
    scenario = w40["scenario_set"]["scenarios"][1]
    scenario["targets"] = [
        one for one in scenario["targets"] if one.get("series_key") not in ("btc", "eth")
    ]
    assert not rule(walk(full(w40, reads), TAPE), 7).ok


def test_r8_catches_a_conjunction_falsifier_with_no_flow_band(w40, reads):
    """A Lull call with no mechanism for a +20% week is not a call."""
    for scenario in w40["scenario_set"]["scenarios"]:
        scenario["targets"] = [
            one
            for one in scenario["targets"]
            if one.get("series_key") != "btc_etf_weekly_flow"
        ]
    assert not rule(walk(full(w40, reads), TAPE), 8).ok


def test_r9_catches_a_trigger_gated_on_leadership(w40, reads):
    w40["scenario_set"]["scenarios"][0]["clauses"][0]["text"] = (
        "memory leadership continues to carry the Nasdaq"
    )
    assert not rule(walk(full(w40, reads), TAPE), 9).ok


def test_r10_catches_a_running_streak_priced_straight_through(w40, reads):
    """W27 and W37: a three-week flow streak extrapolated into a regime."""
    tape = replace(TAPE, btc_streak=Decimal("4"))
    assert not rule(walk(full(w40, reads), tape), 10).ok


def test_r10_passes_when_no_streak_is_running(w40, reads):
    assert rule(walk(full(w40, reads), TAPE), 10).ok


def test_r11_catches_oil_priced_only_inside_the_tail(w40, reads):
    for scenario in w40["scenario_set"]["scenarios"][:2]:
        scenario["targets"] = [
            one for one in scenario["targets"] if one.get("series_key") != "brent"
        ]
    assert not rule(walk(full(w40, reads), TAPE), 11).ok


def test_r12_catches_a_set_whose_swing_variables_name_no_crypto_driver(w40, reads):
    """W38: BTC capped at $79K off the rates channel while the SEC drove it."""
    w40["scenario_set"]["swing_variables"] = [
        {"name": "The auction cluster", "cool": "clears", "hot": "tails"}
    ]
    check = rule(walk(full(w40, reads), TAPE), 12)
    assert not check.ok


@pytest.mark.parametrize("number", [3, 6, 8, 10, 12])
def test_the_partly_checkable_rules_warn_rather_than_refuse(number, w40, reads):
    """A rule this file cannot fully mechanise never blocks a write on its own."""
    checks = walk(full(w40, reads), TAPE)
    assert rule(checks, number).severity in ("warn", "note")


def test_a_waived_rule_is_recorded_rather_than_refused(w40, reads):
    """`scenario-rules.md`: name the rule you waived, do not hide it."""
    w40["scenario_set"]["rules_waived"] = [10]
    tape = replace(TAPE, btc_streak=Decimal("4"))
    check = rule(walk(full(w40, reads), tape), 10)
    assert not check.ok
    assert check.severity == "note"
    assert "rules_waived" in check.sentence


def test_the_report_prints_one_line_per_rule(w40, reads):
    lines = report(walk(full(w40, reads), TAPE)).splitlines()
    assert len(lines) == 12
    assert all(line.startswith("pass") for line in lines)


# ---------------------------------------------------------------------------
# The phase read, which is not one of the twelve
# ---------------------------------------------------------------------------


def phase(checks, key):
    return next(one for one in checks if one.key == key)


def test_the_phase_read_is_not_part_of_the_numbered_walk(w40, reads):
    """R1 to R12 are a checklist against the scenario table, and the read is not on it."""
    rules = walk(full(w40, reads), TAPE)
    assert len(rules) == 12
    assert not any(one.key.startswith(("equities_", "crypto_")) for one in rules)

    read = phase_read(full(w40, reads), TAPE)
    assert [one.key for one in read] == [
        "crypto_transition", "crypto_holds_today", "equities_transition", "equities_holds_today"
    ]


def test_the_same_phase_as_last_week_is_a_note_with_its_change_note(w40, reads):
    check = phase(phase_read(full(w40, reads), TAPE), "equities_transition")
    assert check.ok and check.severity == "note"
    assert "read fresh as Phase 3" in check.sentence
    assert "Same as last week in 2026-W39" in check.sentence
    assert "slipped from 50% to 49.5%" in check.sentence


def test_a_one_step_move_is_reported_and_not_judged(w40, reads):
    reads["phase_calls"][1].update(phase=2, phase_name="Wealth Creation")
    check = phase(phase_read(full(w40, reads), TAPE), "crypto_transition")
    assert check.ok and check.severity == "note"
    assert "P1 to P2, 1 step around the cycle" in check.sentence


def test_a_large_jump_is_surfaced_for_the_operator_to_read(w40, reads):
    """Any phase may follow any phase; a jump of two is a warning, never a refusal."""
    reads["phase_calls"][0].update(phase=1, phase_name="Recovery / Early Bull")
    check = phase(phase_read(full(w40, reads), TAPE), "equities_transition")
    assert not check.ok and check.severity == "warn"
    assert "P3 to P1, 2 steps" in check.sentence


def test_a_read_whose_own_conditions_fail_today_is_warned_about(w40, reads):
    day = date(2026, 9, 25)
    tape = replace(
        TAPE,
        holding={
            "equities": Holding(met=3, failed=1, unevaluable=0, asof=day),
            "crypto": Holding(met=4, failed=0, unevaluable=0, asof=day),
        },
    )
    read = phase_read(full(w40, reads), tape)
    assert phase(read, "crypto_holds_today").ok
    failing = phase(read, "equities_holds_today")
    assert not failing.ok and failing.severity == "warn"
    assert "3 of the 4 things Phase 3 is held on hold on 2026-09-25. 1 failed." in (
        failing.sentence
    )


def test_an_unevaluable_condition_is_not_a_failure(w40, reads):
    """A series with no reading on the day says nothing against the read.

    Counting it as not holding is how a Monday-morning run, scored against a US
    session that had not happened yet, filed four false warnings as findings.
    """
    tape = replace(
        TAPE,
        holding={"equities": Holding(met=3, failed=0, unevaluable=1, asof=date(2026, 9, 25))},
    )
    check = phase(phase_read(full(w40, reads), tape), "equities_holds_today")
    assert check.ok and check.severity == "note"
    assert "1 could not be evaluated" in check.sentence
    assert "failed" not in check.sentence.split("could not")[0]


def test_holds_today_says_so_when_there_was_no_tape(w40, reads):
    check = phase(phase_read(full(w40, reads), TAPE), "equities_holds_today")
    assert check.ok and check.severity == "note"
    assert "not checked against the tape" in check.sentence
