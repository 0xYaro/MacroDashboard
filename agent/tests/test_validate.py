"""What a model produced, and what this package refuses to write.

Every refusal in here is a mistake that has actually been made, either by the
pipeline this replaces or by a week in the ledger.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from macro_agent.model import Refused
from macro_agent.validate import check, events, narrative_points, refusals

from .conftest import full

ASOF = date(2026, 9, 28)
NOW = datetime(2026, 9, 28, 1, 30, tzinfo=UTC)  # Monday 09:30 in Singapore

KINDS = {
    "cnn_fear_greed": ("numeric", "index_0_100", 1),
    "btc_etf_weekly_flow": ("numeric", "usd", 1),
    "btc_etf_inflow_streak": ("numeric", "sessions", 2),
    "liquidations_24h": ("numeric", "usd", 1),
    "fed_funds": ("text", None, 1),
    "next_fomc": ("text", None, 2),
    "fedwatch_next_fomc": ("text", None, 2),
}
ALLOWED = frozenset(KINDS)


def _named(checks, key):
    return [one for one in checks if one.key == key]


# ---------------------------------------------------------------------------
# The scenario set
# ---------------------------------------------------------------------------


def test_probabilities_must_sum_to_100(w40, settings, reads):
    found = full(w40, reads)
    passed = _named(check(found, settings, asof=ASOF), "probabilities_sum_to_100")
    assert passed and passed[0].ok

    w40["scenario_set"]["scenarios"][1]["probability"] = 30
    failed = _named(check(full(w40, reads), settings, asof=ASOF), "probabilities_sum_to_100")
    assert failed and not failed[0].ok
    assert "= 98" in failed[0].sentence
    assert "S1 45" in failed[0].sentence, "the sentence names the three numbers, not a trigger"


def test_a_set_must_carry_exactly_three_scenarios(w40, settings, reads):
    w40["scenario_set"]["scenarios"] = w40["scenario_set"]["scenarios"][:2]
    w40["scenario_set"]["scenarios"][0]["probability"] = 60
    w40["scenario_set"]["scenarios"][1]["probability"] = 40
    found = _named(check(full(w40, reads), settings, asof=ASOF), "scenario_count")
    assert found and not found[0].ok


def test_a_reflexive_40_30_30_is_warned_about_and_not_refused(w40, settings, reads):
    for scenario, probability in zip(w40["scenario_set"]["scenarios"], (40, 30, 30), strict=True):
        scenario["probability"] = probability
    found = _named(check(full(w40, reads), settings, asof=ASOF), "not_a_reflexive_403030")
    assert found and not found[0].ok and found[0].severity == "warn"
    assert not refusals(check(full(w40, reads), settings, asof=ASOF))


def test_a_scenario_with_only_manual_targets_is_refused(w40, settings, reads):
    scenario = w40["scenario_set"]["scenarios"][2]
    scenario["targets"] = [
        {"label": one["label"], "kind": "manual", "text": "judgment"} for one in scenario["targets"]
    ]
    found = _named(check(full(w40, reads), settings, asof=ASOF), "s3_has_a_checkable_target")
    assert found and not found[0].ok


def test_a_band_that_names_no_series_is_refused_at_decode(w40, reads):
    w40["scenario_set"]["scenarios"][0]["targets"][0]["series_key"] = None
    with pytest.raises(Refused, match="must name the series"):
        full(w40, reads)


def test_a_gt_band_carries_its_bound_in_lo_and_an_lt_band_in_hi(w40, reads):
    """The schema's asymmetry, which is easy to get backwards.

    `20260921000300_core_judgment.sql` allows `gt` only with `lo` set and `hi` null, and
    `lt` only with `hi` set and `lo` null. The fixture uses a `gt` band for
    S3's `btc_etf_weekly_flow`, so this is the live shape and not a contrivance.
    """
    full(w40, reads)  # the fixture decodes
    target = next(
        one
        for one in w40["scenario_set"]["scenarios"][2]["targets"]
        if one.get("comparator") == "gt"
    )
    assert target["lo"] and "hi" not in target

    target["hi"] = target.pop("lo")
    with pytest.raises(Refused, match="carries its bound in lo"):
        full(w40, reads)


def test_clause_ordinals_run_without_a_gap(w40, settings, reads):
    """Joining the clauses in ordinal order has to reproduce the trigger as written."""
    w40["scenario_set"]["scenarios"][0]["clauses"][2]["ordinal"] = 9
    found = _named(check(full(w40, reads), settings, asof=ASOF), "s1_clause_ordinals")
    assert found and not found[0].ok


# ---------------------------------------------------------------------------
# Phase calls
# ---------------------------------------------------------------------------


def test_a_phase_call_carries_exactly_four_conditions(w40, settings, reads):
    reads["phase_calls"][0]["conditions"] = reads["phase_calls"][0]["conditions"][:3]
    found = _named(check(full(w40, reads), settings, asof=ASOF), "equities_four_conditions")
    assert found and not found[0].ok


def test_a_manual_condition_with_no_expression_is_the_only_legal_manual(w40, reads):
    """`kind` and `expr` have to agree, whichever way they disagree.

    Built by flipping a typed condition's kind rather than by picking whichever
    condition happens to be manual in the fixture: every phase condition in the
    reference week is typed now, and a test that depended on one not being would
    go quiet exactly when the typing got better.
    """
    reads["phase_calls"][1]["conditions"][0]["kind"] = "manual"
    with pytest.raises(Refused, match="manual condition carries no expression"):
        full(w40, reads)


def test_a_typed_condition_without_an_expression_is_refused(w40, reads):
    reads["phase_calls"][0]["conditions"][1]["expr"] = None
    with pytest.raises(Refused, match="needs an expression"):
        full(w40, reads)


def test_a_manual_condition_holds_or_has_failed_and_says_which(w40, settings, reads):
    """There is no `not yet` on something that must stay true, and no silence either."""
    condition = reads["phase_calls"][1]["conditions"][0]
    condition["kind"] = "manual"
    condition["expr"] = None
    condition.pop("asserted_status", None)
    found = _named(
        check(full(w40, reads), settings, asof=ASOF), "crypto_manual_condition_1_asserted"
    )
    assert found and not found[0].ok and found[0].severity == "refuse"

    condition["asserted_status"] = "not yet"
    found = _named(
        check(full(w40, reads), settings, asof=ASOF), "crypto_manual_condition_1_asserted"
    )
    assert found and not found[0].ok

    condition["asserted_status"] = "met"
    checks = check(full(w40, reads), settings, asof=ASOF)
    assert _named(checks, "crypto_manual_condition_1_asserted")[0].ok
    # Still surfaced: a manual condition never counts toward a stale read.
    warned = _named(checks, "crypto_manual_condition_1")
    assert warned and warned[0].severity == "warn"


# ---------------------------------------------------------------------------
# Setups
# ---------------------------------------------------------------------------


def test_a_setup_must_name_the_series_it_is_measured_against(w40, reads):
    w40["setups"][0]["series_key"] = ""
    with pytest.raises(Refused, match="series_key is required"):
        full(w40, reads)


def test_an_equity_setup_quoted_on_a_future_is_refused(w40, settings, reads):
    """R1: every level in the Setup Ledger is a cash level."""
    w40["setups"][0]["series_key"] = "spx_front"
    found = _named(
        check(full(w40, reads), settings, asof=ASOF), "setup_quoted_on_cash_S&P FOMC-shelf FTR long"
    )
    assert found and not found[0].ok


def test_an_invalidation_on_the_target_side_is_refused(w40, settings, reads):
    """A trade with no risk leg has no planned R:R, only a number that looks like one."""
    w40["setups"][0]["invalidation"] = "7900"  # above a 7800 target on a long
    checks = check(full(w40, reads), settings, asof=ASOF)
    found = _named(checks, "setup_invalidation_side_S&P FOMC-shelf FTR long")
    assert found and not found[0].ok


def test_a_setup_whose_series_is_not_in_the_registry_is_refused(w40, settings, reads):
    w40["setups"][2]["series_key"] = "btc_usd"
    found = _named(
        check(full(w40, reads), settings, asof=ASOF), "setup_series_known_BTC $82K acceptance retest"
    )
    assert found and not found[0].ok


# ---------------------------------------------------------------------------
# Amendments
# ---------------------------------------------------------------------------


def _all_manual(scenario):
    for clause in scenario["clauses"]:
        clause["kind"] = "manual"
        clause.pop("expr", None)


def test_a_new_week_with_an_all_manual_trigger_is_refused(w40, settings, reads):
    """W39: S2's one manual clause 'fired in full' while the Desk showed it unfired."""
    assert not _named(
        refusals(check(full(w40, reads), settings, asof=ASOF)), "s3_trigger_is_typed"
    )
    _all_manual(w40["scenario_set"]["scenarios"][2])
    refused = _named(refusals(check(full(w40, reads), settings, asof=ASOF)), "s3_trigger_is_typed")
    assert refused and "all 4 are manual" in refused[0].sentence


def test_a_risk_off_branch_must_type_its_confirming_leg(w40, settings, reads):
    """R4 as a refusal: the confirming leg is an expression, not a sentence."""
    scenario = w40["scenario_set"]["scenarios"][1]
    scenario["clauses"] = [
        one for one in scenario["clauses"] if "n_of" not in (one.get("expr") or {})
    ]
    for index, one in enumerate(scenario["clauses"], 1):
        one["ordinal"] = index
    refused = _named(
        refusals(check(full(w40, reads), settings, asof=ASOF)), "risk_off_confirming_leg_typed"
    )
    assert refused and refused[0].sentence.startswith("S2 is the risk-off branch")


def test_an_amendment_keeps_the_accepted_manual_trigger(w40, settings, reads):
    """An amendment is graded against the accepted statement, so it is not re-typed mid-week."""
    for scenario in w40["scenario_set"]["scenarios"]:
        _all_manual(scenario)
    w40["scenario_set"]["amends"] = "11111111-1111-1111-1111-111111111111"
    w40["scenario_set"]["amend_reason"] = "S2's trigger fired in full on a weak 5Y auction."
    checks = refusals(check(full(w40, reads), settings, asof=ASOF))
    assert not [one for one in checks if "trigger_is_typed" in one.key or "confirming_leg" in one.key]


def test_an_amendment_must_carry_both_halves(w40, settings, reads):
    w40["scenario_set"]["amends"] = "11111111-1111-1111-1111-111111111111"
    checks = check(full(w40, reads), settings, asof=ASOF)
    found = [one for one in checks if one.key.startswith("amendment_carries_its_reason")]
    assert found and not found[0].ok

    w40["scenario_set"]["amend_reason"] = "S2's confirming leg fired in full: HY cleared 300 bps."
    checks = check(full(w40, reads), settings, asof=ASOF)
    found = [one for one in checks if one.key.startswith("amendment_carries_its_reason")]
    assert found and found[0].ok


# ---------------------------------------------------------------------------
# Mode A
# ---------------------------------------------------------------------------


def test_a_tier_4_point_is_an_empty_value_and_a_note():
    """Never a number the model felt was about right, and never a zero."""
    found, checks = narrative_points(
        [
            {
                "series": "cnn_fear_greed",
                "value": "",
                "display": "",
                "tier": 4,
                "asof": "2026-09-28",
                "released": "",
                "note": "check MacroMicro for the CNN reading",
            }
        ],
        kinds=KINDS,
        allowed=ALLOWED,
        observed_at=NOW,
        source_prefix="search agent (equities)",
    )
    assert not checks
    assert len(found) == 1
    assert found[0].value is None
    assert found[0].tier == 4
    assert found[0].note


def test_a_tier_4_point_that_carries_a_number_has_the_number_dropped():
    found, _ = narrative_points(
        [
            {
                "series": "liquidations_24h",
                "value": "250000000",
                "display": "about $250M",
                "tier": 4,
                "asof": "2026-09-28",
                "released": "",
                "note": "no source found; this is an estimate",
            }
        ],
        kinds=KINDS,
        allowed=ALLOWED,
        observed_at=NOW,
        source_prefix="search agent (crypto-flows)",
    )
    assert found[0].value is None, "a Tier 4 estimate is not a reading"


def test_a_tier_cannot_beat_the_series_tier_floor():
    """An aggregator quote does not become a primary source because a model said so."""
    found, _ = narrative_points(
        [
            {
                "series": "btc_etf_inflow_streak",
                "value": "3",
                "display": "a third straight inflow session",
                "tier": 1,
                "asof": "2026-09-28",
                "released": "",
                "source": "SoSoValue",
            }
        ],
        kinds=KINDS,
        allowed=ALLOWED,
        observed_at=NOW,
        source_prefix="search agent (crypto-flows)",
    )
    assert found[0].tier == 2


def test_a_streak_is_a_signed_integer_and_the_sentence_goes_in_display():
    found, _ = narrative_points(
        [
            {
                "series": "btc_etf_inflow_streak",
                "value": "-4",
                "display": "a fourth consecutive outflow session; the streak broke on Tuesday",
                "tier": 2,
                "asof": "2026-09-28",
                "released": "",
                "source": "SoSoValue",
            }
        ],
        kinds=KINDS,
        allowed=ALLOWED,
        observed_at=NOW,
        source_prefix="search agent (crypto-flows)",
    )
    assert found[0].value == Decimal(-4)
    assert "outflow" in found[0].display


def test_a_text_series_keeps_the_sentence_and_drops_any_number():
    found, checks = narrative_points(
        [
            {
                "series": "fedwatch_next_fomc",
                "value": "50.9",
                "display": "hold ~49.1% / +25 bp ~50.9% for Oct 28, quoted Sep 17",
                "tier": 2,
                "asof": "2026-09-28",
                "released": "",
                "source": "aggregator quoting CME FedWatch",
            }
        ],
        kinds=KINDS,
        allowed=ALLOWED,
        observed_at=NOW,
        source_prefix="search agent (macro)",
    )
    assert found[0].value is None
    assert found[0].display.startswith("hold")
    assert any(one.key == "text_series_carried_a_number" for one in checks)


def test_a_text_series_with_no_sentence_is_refused():
    found, checks = narrative_points(
        [
            {
                "series": "fed_funds",
                "value": "",
                "display": "",
                "tier": 1,
                "asof": "2026-09-28",
                "released": "",
            }
        ],
        kinds=KINDS,
        allowed=ALLOWED,
        observed_at=NOW,
        source_prefix="search agent (macro)",
    )
    assert not found
    assert any("text series" in one.sentence for one in checks)


def test_the_same_key_twice_is_the_defect_that_produced_two_next_fomc_rows():
    points = [
        {
            "series": "next_fomc",
            "value": "",
            "display": "October 27-28, 2026",
            "tier": 2,
            "asof": "2026-09-28",
            "released": "",
            "source": "federalreserve.gov",
        },
        {
            "series": "next_fomc",
            "value": "",
            "display": "hold ~49% / hike ~51%",
            "tier": 2,
            "asof": "2026-09-28",
            "released": "",
            "source": "aggregator",
        },
    ]
    found, checks = narrative_points(
        points,
        kinds=KINDS,
        allowed=ALLOWED,
        observed_at=NOW,
        source_prefix="search agent (macro)",
    )
    assert len(found) == 1
    assert found[0].display.startswith("October")
    assert any(one.key == "series_returned_twice" for one in checks)


def test_a_series_this_run_does_not_own_is_dropped():
    """The worker fetches breadth deterministically; a search agent must not overwrite it."""
    found, checks = narrative_points(
        [
            {
                "series": "breadth_spx_above_200dma",
                "value": "49.5",
                "display": "49.50%",
                "tier": 2,
                "asof": "2026-09-28",
                "released": "",
                "source": "MacroMicro",
            }
        ],
        kinds={**KINDS, "breadth_spx_above_200dma": ("numeric", "percent", 2)},
        allowed=ALLOWED,
        observed_at=NOW,
        source_prefix="search agent (equities)",
    )
    assert not found
    assert any(one.key == "series_not_on_this_path" for one in checks)


def test_an_event_without_a_kind_is_refused_rather_than_guessed():
    decoded, checks = events(
        [
            {
                "date": "2026-09-30",
                "kind": "",
                "whatHappened": "Micron reported",
                "whyItMatters": "the complex's print",
            }
        ]
    )
    assert not decoded
    assert checks


# ---------------------------------------------------------------------------
# A fresh phase read
# ---------------------------------------------------------------------------


def test_the_reference_reads_pass_every_phase_check(w40, settings, reads):
    checks = check(full(w40, reads), settings, asof=ASOF)
    phase = [one for one in checks if one.key.startswith(("equities_", "crypto_"))]
    assert phase and all(one.ok for one in phase), [one.sentence for one in phase if not one.ok]


def test_a_read_that_states_last_weeks_phase_is_refused_at_decode(reads):
    """The read is taken with the prior out of view, so it cannot carry one."""
    from macro_agent.validate import phase_reads

    for key, value in (("prior_phase", 3), ("direction", "unchanged"), ("blind_phase", 3)):
        broken = {**reads, "phase_calls": [dict(reads["phase_calls"][0], **{key: value})]}
        with pytest.raises(Refused, match="out of view"):
            phase_reads(broken)


def test_a_proposal_cannot_restate_the_phase_after_the_reveal(w40, reads):
    w40["phase_calls"][0]["phase"] = 4
    with pytest.raises(Refused, match="fixed there"):
        full(w40, reads)


def test_a_read_that_talks_about_the_prior_call_is_warned_about(w40, settings, reads):
    reads["phase_calls"][1]["reason"] = "Still in Phase 1 because the flow has not come."
    found = _named(check(full(w40, reads), settings, asof=ASOF), "crypto_read_is_fresh")
    assert found and not found[0].ok and found[0].severity == "warn"
    assert "still in phase 1" in found[0].sentence


def test_a_newly_authored_read_must_carry_a_stage_and_its_reason(w40, settings, reads):
    reads["phase_calls"][0].pop("stage_reason")
    found = _named(check(full(w40, reads), settings, asof=ASOF), "equities_stage_recorded")
    assert found and not found[0].ok and found[0].severity == "refuse"


def test_a_stage_outside_early_mid_late_is_refused_at_decode(w40, reads):
    reads["phase_calls"][0]["stage"] = "mature"
    with pytest.raises(Refused, match="early, mid or late"):
        full(w40, reads)


def test_a_phase_outside_one_to_five_is_refused(w40, settings, reads):
    reads["phase_calls"][0]["phase"] = 7
    found = _named(check(full(w40, reads), settings, asof=ASOF), "equities_phase_is_a_phase")
    assert found and not found[0].ok


def test_a_name_that_disagrees_with_the_number_is_warned_about(w40, settings, reads):
    reads["phase_calls"][0]["phase"] = 2
    found = _named(check(full(w40, reads), settings, asof=ASOF), "equities_phase_name_matches")
    assert found and not found[0].ok and "Expansion / Momentum" in found[0].sentence


def test_four_manual_conditions_are_refused(w40, settings, reads):
    """A read nothing can check can never be flagged as out of date."""
    for condition in reads["phase_calls"][0]["conditions"]:
        condition.update(kind="manual", expr=None, asserted_status="met")
    found = _named(
        check(full(w40, reads), settings, asof=ASOF), "equities_conditions_are_checkable"
    )
    assert found and not found[0].ok and found[0].severity == "refuse"


def test_any_phase_may_follow_any_phase(w40, settings, reads):
    """No move is refused for its size. A large one has to be argued."""
    reads["phase_calls"][0]["phase"] = 1
    reads["phase_calls"][0]["phase_name"] = "Recovery / Early Bull"
    w40["phase_calls"][0]["change_note"] = "Moved."
    found = _named(check(full(w40, reads), settings, asof=ASOF), "equities_large_jump_is_argued")
    assert found and not found[0].ok and found[0].severity == "refuse"
    assert "Phase 3 to Phase 1" in found[0].sentence

    w40["phase_calls"][0]["change_note"] = (
        "Phase 3 to Phase 1 in a week: the index broke its 200dma and washed out, then breadth "
        "thrust off the low with HY tightening, so the Contraction step was passed through."
    )
    argued = _named(check(full(w40, reads), settings, asof=ASOF), "equities_large_jump_is_argued")
    assert argued and argued[0].ok

    # One step either way, and 5 to 1 around the cycle, need no argument.
    for phase in (2, 4):
        reads["phase_calls"][0]["phase"] = phase
        assert not _named(
            check(full(w40, reads), settings, asof=ASOF), "equities_large_jump_is_argued"
        )


def test_the_cycle_wraps_from_lull_to_early_bull():
    from macro_agent.model import phase_distance

    assert phase_distance(5, 1) == 1
    assert phase_distance(1, 5) == 1
    assert phase_distance(3, 1) == 2
    assert phase_distance(1, 4) == 2
    assert phase_distance(2, 2) == 0


def test_every_read_needs_a_change_note_after_the_reveal(w40, settings, reads):
    w40["phase_calls"] = w40["phase_calls"][:1]
    found = _named(check(full(w40, reads), settings, asof=ASOF), "crypto_change_note")
    assert found and not found[0].ok and found[0].severity == "refuse"


def test_a_jump_note_may_name_the_phases_by_their_framework_names(w40, settings, reads):
    """SKILL.md says the note "names both phases"; the framework's own names are names too."""
    reads["phase_calls"][0]["phase"] = 1
    reads["phase_calls"][0]["phase_name"] = "Recovery / Early Bull"
    w40["phase_calls"][0]["change_note"] = (
        "From Late Cycle / Distribution straight to Recovery in a week: the index washed out "
        "under its 200-day average and breadth thrust off the low, so Contraction was passed "
        "through."
    )
    argued = _named(check(full(w40, reads), settings, asof=ASOF), "equities_large_jump_is_argued")
    assert argued and argued[0].ok, argued[0].sentence

    # A name that belongs to neither phase is still not an argument.
    w40["phase_calls"][0]["change_note"] = (
        "Straight from Expansion to Base-Building in a week, because breadth thrust off the low."
    )
    refused = _named(check(full(w40, reads), settings, asof=ASOF), "equities_large_jump_is_argued")
    assert refused and not refused[0].ok
    assert "'Recovery / Early Bull'" in refused[0].sentence


def test_two_reads_for_one_book_are_refused(settings, reads):
    """Two proposed equities reads could never both be accepted."""
    from macro_agent.validate import phase_reads, read_checks

    reads["phase_calls"].append(dict(reads["phase_calls"][0]))
    found = _named(read_checks(phase_reads(reads), settings, asof=ASOF), "one_read_per_book")
    assert found and not found[0].ok and found[0].severity == "refuse"
    assert "equities" in found[0].sentence
