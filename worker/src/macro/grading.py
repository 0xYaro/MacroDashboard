"""Weekly grading: `macro_core.week_grade` and `macro_core.setup_grade`.

Monday's 09:30 run closes the week. It grades the prior week's scenario set,
appends the ledger rows, computes the Brier score and recomputes the calibration
read.

## What this module decides, and what it refuses to

A setup's outcome is a fact about the tape and is computed here in full: the
entry, the stop, the target, the realised multiple.

A scenario's grade is a judgment and is not. `Materialized`, `Partial` and `Not`
are read off how the week actually went against a prose trigger and a table of
target bands, and the journal's own grading notes are three paragraphs of
reasoning apiece. This module computes the two things that are countable, the
target hit count and the Brier score once the grades exist, and leaves the grade
itself to the authoring run. A machine-assigned `Partial` would enter the Brier
series beside eleven hand-assigned ones and make the whole record incomparable.

## Two counting rules that are not negotiable

**`targets_n` is the row count.** Every `macro_core.scenario_target` for the
scenario counts, band and manual alike. W39's S1 carries a leadership statement
alongside ten numeric bands and it counts toward the eleven. In W37's grading
block, W36's S1 was counted as nine targets *including* three non-numeric legs
while W36's S2 in the same block was counted as six *excluding* its two
equivalent legs, and the two conventions contradict each other. Here the
denominator is `len(targets)` and there is nowhere for a second convention to
live.

**The Brier score is never forgiven for a premise that moved.** A scenario whose
trigger clause was voided is graded normally and the ledger row carries
`premise_moved`. R6 exists because two weeks in ten were driven by an event no
calendar carried, so over-anchoring on a dated catalyst is a forecasting error
rather than bad luck, and forgiving it would remove the pressure that rule
applies. The tag exists so a rule can later be written from counted evidence.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from .calibration import ScenarioOutcome, brier
from .evaluate import setup_state
from .expr import Context
from .model import (
    Finding,
    Scenario,
    ScenarioSet,
    ScenarioTargetState,
    Setup,
    SetupGrade,
    SetupState,
    WeekGrade,
)

Grade = Literal["Materialized", "Partial", "Not"]

#: The Setup Ledger's outcome vocabulary, verbatim, including the U+2192 arrow.
OUTCOMES: dict[str, str] = {
    "not triggered": "not triggered",
    "open": "open",
    "stopped": "triggered → stopped",
    "target": "triggered → target",
}


@dataclass(frozen=True, slots=True)
class ScenarioJudgment:
    """The grade the authoring run assigned to one scenario of the graded week.

    `note` is the one-line grading reason, written at grading time looking
    backward at what happened. In the journal it is the sentence trailing the
    grade:

        S2 (30%) "Hike and done: the Fed buys credibility and the tape exhales"
        — Partial — targets hit 5 of 11. Not one trigger clause fired, yet the
        four bands S1 missed — VIX 13–16, BTC $79K–$85K, ETH $2,550–$2,850,
        gold $4,380–$4,600 — all landed here, plus HY at the top of its range.

    Everything after `targets hit 5 of 11` is the note.

    It is supplied by the authoring run and never derived here. It is not the
    scenario's `positioning`, which is forward-looking posture written a week
    earlier: substituting one for the other would put last week's intentions in
    the column that is supposed to explain last week's outcome, and it would
    read plausibly enough that nobody would catch it. Nor is it synthesised
    from the band counts. A generated reason that reads like a human one is
    worse than a blank, because the ledger's value is that its sentences were
    written by someone who actually thought about it, and twelve weeks of them
    are what R1 to R12 were distilled from.
    """

    scenario_id: str
    grade: Grade
    #: The grading reason, or empty when the authoring run supplied none.
    note: str = ""


def grade_setups(
    setups: Sequence[Setup],
    *,
    ctx: Context,
    graded_week: str,
) -> list[SetupGrade]:
    """One `macro_core.setup_grade` row per setup proposed in the graded week.

    Evaluated on the close, over the sessions since the setup's own
    `activated_at`, so a setup can never grade as entered on a session that
    preceded it. An `open` row is rewritten the following week rather than
    duplicated, which the primary key on `setup_id` enforces.
    """
    out: list[SetupGrade] = []
    for setup in setups:
        state = setup_state(setup, ctx)
        outcome = OUTCOMES[state.entry_state]
        realised = state.r_running if state.entry_state in ("stopped", "target") else None
        out.append(
            SetupGrade(
                setup_id=setup.setup_id,
                graded_week=graded_week,
                outcome=outcome,  # type: ignore[arg-type]
                r_realised=realised,
                note=_setup_note(state, setup),
            )
        )
    return out


def _setup_note(state: SetupState, setup: Setup) -> str:
    """One sentence naming what the tape did and what the gate did.

    The Setup Ledger's Note column is where "price traded through the band but
    breadth was 52.88% against the above-55% gate" has to be readable, because
    that sentence is the whole record of a forgone trade.
    """
    gate_state = state.gate_state
    gate_reason = state.gate_reason
    entry_state = state.entry_state
    touched = state.band_touched_today
    closed_in = state.band_touched_close

    if entry_state == "not triggered" and touched and not closed_in:
        return (
            f"The session reached into {setup.entry_lo}–{setup.entry_hi} intraday without "
            f"closing inside it, and the gate was {gate_state}: {gate_reason}"
        )
    if entry_state == "not triggered" and gate_state == "blocked":
        return f"The gate kept it shut: {gate_reason}"
    if entry_state == "not triggered":
        return (
            f"No close landed inside {setup.entry_lo}–{setup.entry_hi} while the setup was "
            f"active; the gate was {gate_state}."
        )
    if entry_state == "open":
        return f"Entered and still open; neither {setup.invalidation} nor {setup.target} closed."
    if entry_state == "stopped":
        return f"Entered, then closed through the {setup.invalidation} invalidation."
    return f"Entered and closed at or beyond the {setup.target} target."


def count_targets(target_states: Sequence[ScenarioTargetState]) -> tuple[int, int]:
    """`targets_hit` and `targets_n` for one scenario.

    `targets_n` is the row count, full stop. A manual target counts toward the
    denominator because it is a forecast the week either met or did not, and it
    is presented for judgment at grading rather than dropped from the count.

    `targets_hit` counts only the bands that were measured and held. A target
    whose `holding` is null was not measured and is not a hit, which is why the
    two numbers are returned together and never one without the other.
    """
    return sum(1 for one in target_states if one.holding is True), len(target_states)


def week_grade(
    *,
    week: str,
    scenario_set: ScenarioSet,
    judgments: Sequence[ScenarioJudgment],
    target_states_by_scenario: Mapping[str, Sequence[ScenarioTargetState]],
    eq_call: str,
    crypto_call: str,
    key_miss: str,
    graded_at: datetime,
) -> tuple[WeekGrade, list[Finding]]:
    """One `macro_core.week_grade` row from the authored grades and the tape.

    The base case is the scenario the set stated at the highest probability, and
    ties go to the lower ordinal, which is how every row already in the ledger
    was read.

    `targets_hit`, `targets_n` and `brier` are computed. The grades and their
    one-line reasons arrive from the authoring run, and `key_miss` with them:
    assigning Materialized, Partial or Not is judgment, and the reason belongs
    beside the judgment that made it.
    """
    findings: list[Finding] = []
    by_scenario = {one.scenario_id: one for one in judgments}
    graded = {scenario_id: one.grade for scenario_id, one in by_scenario.items()}

    missing = [one.scenario_id for one in scenario_set.scenarios if one.scenario_id not in graded]
    if missing:
        findings.append(
            Finding(
                check_key="week_grade_incomplete",
                subject=week,
                sentence=(
                    f"{len(missing)} of {len(scenario_set.scenarios)} scenarios in {week} carry no "
                    "grade, so the week cannot be scored: a Brier over a partial set would not be "
                    "comparable with the eleven already in the ledger."
                ),
                severity=1,
            )
        )

    base = _base_scenario(scenario_set.scenarios)
    base_grade = graded.get(base.scenario_id)
    hit, total = count_targets(list(target_states_by_scenario.get(base.scenario_id, ())))

    score: Decimal | None = None
    if not missing:
        score = brier(
            [
                ScenarioOutcome(
                    label=f"S{one.ordinal}",
                    probability=one.probability,
                    grade=graded[one.scenario_id],
                )
                for one in scenario_set.scenarios
            ]
        )

    premise_moved = any(clause.is_void for one in scenario_set.scenarios for clause in one.clauses)

    others = other_grades(scenario_set, by_scenario, base_scenario_id=base.scenario_id)

    return (
        WeekGrade(
            week=week,
            base_scenario_id=base.scenario_id,
            base_scenario_label=f'S{base.ordinal} "{base.title}" ({base.probability}%)',
            base_grade=base_grade or "Not",
            other_grades=others,
            eq_call=eq_call,
            crypto_call=crypto_call,
            key_miss=key_miss,
            targets_hit=hit,
            targets_n=total,
            brier=score,
            # The Brier score is not forgiven. The tag exists so a rule can
            # later be written from counted evidence rather than an impression.
            premise_moved=premise_moved,
            graded_at=graded_at,
        ),
        findings,
    )


def other_grades(
    scenario_set: ScenarioSet,
    judgments: Mapping[str, ScenarioJudgment],
    *,
    base_scenario_id: str | None,
) -> list[dict[str, Any]]:
    """`macro_core.week_grade.other_grades`: an array, never an object.

    `[{"id": "S2", "prob": 30, "grade": "Partial", "note": "..."}]`, in scenario
    order, for every scenario of the set except the base case.

    The base case is excluded because the ledger's own "Other scenarios" column
    is, and because the base case's reasoning is absorbed into `key_miss`, which
    is the paragraph that was written about it.

    The shape is load-bearing and it is the one thing here three parts each got
    right on their own terms and wrong together. The column defaults to
    `'[]'::jsonb` and the web app reads an array, so an object keyed by scenario
    id parses as a malformed row, rejects every ledger row it appears in, and
    empties the calibration route entirely. The four named keys are the
    contract's; `title` rides along beside them because the ledger prints it.

    `note` is carried through from the judgment and is never derived. A scenario
    the authoring run graded without one gets an empty string, so the key is
    always present and a blank is visibly a blank.
    """
    return [
        {
            "id": f"S{one.ordinal}",
            "prob": one.probability,
            "grade": judgments[one.scenario_id].grade,
            "note": judgments[one.scenario_id].note,
            "title": one.title,
        }
        for one in sorted(scenario_set.scenarios, key=lambda s: s.ordinal)
        if one.scenario_id != base_scenario_id and one.scenario_id in judgments
    ]


def _base_scenario(scenarios: Sequence[Scenario]) -> Scenario:
    """The scenario stated at the highest probability; a tie goes to the lower ordinal."""
    return min(scenarios, key=lambda one: (-one.probability, one.ordinal))


#: The checks the worker's Monday grading raises. `week_grade_incomplete` is
#: raised by the authoring agent's grading call and is the agent's to close.
WEEKLY_CHECK_KEYS: tuple[str, ...] = (
    "setup_ledger_open_too_long",
    "setup_check_incomplete",
    "scorecard_row_missing",
)

#: The checks every evaluation of the active judgment raises, on both runs.
EVALUATION_CHECK_KEYS: tuple[str, ...] = (
    "scenario_set_unscoreable",
    "scenario_count",
    "scenario_probabilities_sum",
    "phase_tracker_rows",
)


def ledger_findings(
    open_grades: Sequence[tuple[str, str, str]],
    *,
    current_week: str,
    previous_week: str,
) -> list[Finding]:
    """Ledger integrity: no setup left `open` for more than one week ungraded.

    `open_grades` is `(setup_id, graded_week, name)`. A row still `open` from a
    week before last is a setup nobody closed, and an ungraded setup is a trade
    idea that quietly left the hit rate.
    """
    stale = [
        (setup_id, graded_week, name)
        for setup_id, graded_week, name in open_grades
        if graded_week not in (current_week, previous_week)
    ]
    return [
        Finding(
            check_key="setup_ledger_open_too_long",
            subject=f"{name} ({graded_week})",
            sentence=(
                f"The setup {name!r} has been open in the Setup Ledger since {graded_week} and "
                f"has not been regraded; an open row is rewritten the following week, never "
                "carried, so its outcome is currently missing from the hit rate."
            ),
            severity=2,
        )
        for _, graded_week, name in stale
    ]


def setup_check_findings(
    proposed: Sequence[Setup],
    graded: Sequence[SetupGrade],
    *,
    graded_week: str,
) -> list[Finding]:
    """The setup check covers every prior-week setup, or says which it missed."""
    have = {one.setup_id for one in graded}
    missing = [one for one in proposed if one.setup_id not in have]
    return [
        Finding(
            check_key="setup_check_incomplete",
            subject=f"{one.name} ({one.week})",
            sentence=(
                f"{graded_week}'s setup check has no row for {one.name!r}, proposed in "
                f"{one.week}; every setup proposed in a week is graded the following week, and "
                "one that is not simply disappears from the record."
            ),
            severity=1,
        )
        for one in missing
    ]


def unscored_set_finding(
    target_states: Sequence[ScenarioTargetState], *, week: str
) -> list[Finding]:
    """A set whose targets are all manual is a finding: it is not a forecast.

    A scenario with nothing checkable cannot be graded against the tape, only
    against an opinion formed after it.
    """
    if not target_states:
        return []
    if any(one.series_key is not None for one in target_states):
        return []
    return [
        Finding(
            check_key="scenario_set_unscoreable",
            subject=week,
            sentence=(
                f"Every target in {week}'s scenario set is manual, so nothing in it can be "
                "checked against the tape; a scenario with nothing checkable is not a forecast."
            ),
            severity=1,
        )
    ]


def probability_findings(scenario_set: ScenarioSet) -> list[Finding]:
    """The two structural checks `lint_journal.py` performed on a scenario set."""
    out: list[Finding] = []
    count = len(scenario_set.scenarios)
    if count != 3:
        out.append(
            Finding(
                check_key="scenario_count",
                subject=scenario_set.week,
                sentence=(
                    f"{scenario_set.week}'s scenario set carries {count} scenarios and the set is "
                    "exactly three; the probabilities cannot sum to 100 over any other number."
                ),
                severity=1,
            )
        )
    total = sum(one.probability for one in scenario_set.scenarios)
    if count and total != 100:
        out.append(
            Finding(
                check_key="scenario_probabilities_sum",
                subject=scenario_set.week,
                sentence=(
                    f"{scenario_set.week}'s scenario probabilities sum to {total}, not 100, so the "
                    "Brier score over them would not be a proper score."
                ),
                severity=1,
            )
        )
    return out


def phase_score_findings(asset: str, of: int) -> list[Finding]:
    """A phase call rests on exactly four conditions, as `lint_journal.py` checked.

    The upgrade arithmetic that used to sit here is gone with the upgrade: the
    phase is read fresh every Monday, and nothing moves it between reads.
    """
    if of == 4:
        return []
    return [
        Finding(
            check_key="phase_tracker_rows",
            subject=asset,
            sentence=(
                f"The {asset} phase call carries {of} conditions and it is exactly four; the "
                "read is held against four things and a stale flag at two failed means "
                "nothing over any other number."
            ),
            severity=1,
        )
    ]


def holdings_findings(
    active_holdings: Sequence[tuple[str, str]],
    checked: Mapping[str, set[str]],
) -> list[Finding]:
    """Every active holding carries a holdings-check line for its book's strategy."""
    return [
        Finding(
            check_key="holdings_check_missing",
            subject=ticker,
            sentence=(
                f"{ticker} is an active {book} holding with no holdings-check line on the active "
                f"{book} strategy, so the week's strategy does not say what to do with a position "
                "the book actually holds."
            ),
            severity=2,
        )
        for ticker, book in active_holdings
        if ticker not in checked.get(book, set())
    ]


def scorecard_row_findings(graded_weeks: Sequence[str], expected: Sequence[str]) -> list[Finding]:
    """A Scorecard Ledger row exists for each graded week."""
    have = set(graded_weeks)
    return [
        Finding(
            check_key="scorecard_row_missing",
            subject=week,
            sentence=(
                f"There is no Scorecard Ledger row for {week}, so that week's base case is absent "
                "from the rolling calibration read and the hit rate is computed over a shorter "
                "record than it claims."
            ),
            severity=1,
        )
        for week in expected
        if week not in have
    ]
