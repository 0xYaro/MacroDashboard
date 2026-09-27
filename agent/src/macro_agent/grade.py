"""Closing the week: the countable half, computed by the worker's own code.

Nothing in this module decides a grade. `Materialized`, `Partial` and `Not` are
judgment and arrive from the authoring run as `ScenarioGrade` records; the
worker refuses to machine-assign them because a machine grade entering the Brier
series beside eleven hand-assigned ones would make the whole record
incomparable, and that refusal is the reason this package exists.

What is computed here, and by whom:

- **`targets_hit` and `targets_n`** by `macro.grading.count_targets`, over
  target states produced by `macro.evaluate.target_state`. `targets_n` is the
  row count and counts band and manual rows alike. `targets_hit` counts only
  the bands that were measured and held.
- **The Brier score** by `macro.calibration.brier`, unchanged since the vault
  script. It takes one argument and there is nowhere to pass it an excuse: a
  scenario whose premise moved is graded normally and the row carries the tag.
- **The base case** by `macro.grading.week_grade`, which takes the scenario
  stated at the highest probability with a tie going to the lower ordinal, and
  formats `base_scenario_label` the way the eleven migrated rows are formatted,
  so the calibration read's S2-habit check keeps working across both.

This module's own job is translation: `macro_core` rows read by this package's
store, into the records the worker's functions take.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from .config import Settings
from .model import ProposedGrade, Refused, ScenarioGrade
from .store import LedgerRow
from .worker_bridge import calibration as load_calibration
from .worker_bridge import evaluator, require
from .worker_bridge import grading as load_grading
from .worker_bridge import registry as load_registry


def _scenario_set_record(payload: dict[str, Any], model: Any) -> Any:
    """One stored scenario set, as the worker's `ScenarioSet`."""
    activated = payload.get("activated_at") or payload["authored_at"]
    return model.ScenarioSet(
        set_id=payload["set_id"],
        week=payload["week"],
        authored_asof=activated.date(),
        activated_at=activated,
        scenarios=tuple(
            model.Scenario(
                scenario_id=one["scenario_id"],
                set_id=payload["set_id"],
                ordinal=one["ordinal"],
                title=one["title"],
                probability=one["probability"],
                positioning=one["positioning"],
                clauses=tuple(
                    model.TriggerClause(
                        condition_id=clause["condition_id"],
                        scenario_id=one["scenario_id"],
                        ordinal=clause["ordinal"],
                        text=clause["text"],
                        kind=clause["kind"],
                        expr=clause["expr"],
                        catalyst_id=clause["catalyst_id"],
                        required=clause["required"],
                        void_at=clause["void_at"],
                        void_reason=clause["void_reason"],
                    )
                    for clause in one["clauses"]
                ),
                targets=tuple(
                    model.ScenarioTarget(
                        target_id=target["target_id"],
                        scenario_id=one["scenario_id"],
                        series_key=target["series_key"],
                        label=target["label"],
                        kind=target["kind"],
                        comparator=target["comparator"],
                        lo=target["lo"],
                        hi=target["hi"],
                        text=target["text"],
                    )
                    for target in one["targets"]
                ),
            )
            for one in payload["scenarios"]
        ),
    )


def close_the_week(
    *,
    settings: Settings,
    week: str,
    stored_set: dict[str, Any],
    grades: list[ScenarioGrade],
    history_rows: list[dict[str, Any]],
    open_gaps: list[tuple[str, date]],
    asof: date,
    eq_call: str,
    crypto_call: str,
    key_miss: str,
    graded_at: datetime,
) -> tuple[ProposedGrade, list[str]]:
    """One `macro_core.week_grade` row, and the worker's findings about it.

    `asof` is the last session of the graded week: the target bands are scored
    where the week ended, not where today happens to sit. A band that held on
    Friday and broke on Monday held for the week it described.
    """
    model = require("macro.model", settings)
    expr_module, _predicates, observations = evaluator(settings)
    grading = load_grading(settings)
    reg = load_registry(settings)
    evaluate = require("macro.evaluate", settings)

    scenario_set = _scenario_set_record(stored_set, model)
    known = {one.scenario_id for one in scenario_set.scenarios}
    unknown = [one.scenario_id for one in grades if one.scenario_id not in known]
    if unknown:
        raise Refused(
            f"{len(unknown)} grade(s) name a scenario that is not in {week}'s set: "
            f"{', '.join(unknown)}. A grade against a scenario nobody stated is not a grade."
        )

    history = observations.History(observations.readings_from(history_rows), gaps=open_gaps)
    ctx = expr_module.Context(asof=asof, history=history, registry=reg)

    by_scenario = {
        one.scenario_id: [evaluate.target_state(target, ctx) for target in one.targets]
        for one in scenario_set.scenarios
    }

    # The note rides on the judgment. `macro_core.week_grade.other_grades` is an
    # array of `{"id", "prob", "grade", "note"}` and the worker builds it, so
    # nothing is merged, reshaped or re-keyed here: a second hand on that column
    # is how it came to hold two shapes in the first place.
    row, findings = grading.week_grade(
        week=week,
        scenario_set=scenario_set,
        judgments=[
            grading.ScenarioJudgment(
                scenario_id=one.scenario_id, grade=one.grade, note=one.note
            )
            for one in grades
        ],
        target_states_by_scenario=by_scenario,
        eq_call=eq_call,
        crypto_call=crypto_call,
        key_miss=key_miss,
        graded_at=graded_at,
    )

    sentences = [one.sentence for one in findings]
    sentences.extend(_note_findings(scenario_set, grades, row.base_scenario_id, key_miss))

    return (
        ProposedGrade(
            week=row.week,
            base_scenario_id=row.base_scenario_id,
            base_scenario_label=row.base_scenario_label,
            base_grade=row.base_grade,
            other_grades=row.other_grades,
            eq_call=row.eq_call,
            crypto_call=row.crypto_call,
            key_miss=row.key_miss,
            targets_hit=row.targets_hit,
            targets_n=row.targets_n,
            brier=row.brier,
            premise_moved=row.premise_moved,
            graded_at=row.graded_at,
        ),
        sentences,
    )


def _note_findings(
    scenario_set: Any, grades: list[ScenarioGrade], base_scenario_id: str | None, key_miss: str
) -> list[str]:
    """Two things about the grading notes that are worth saying out loud.

    Neither is a refusal. A note is prose and this package will not write one,
    so the most it can do is tell the operator what is missing and where a
    supplied one went.
    """
    out: list[str] = []
    by_ordinal = {one.scenario_id: one.ordinal for one in scenario_set.scenarios}

    blank = [
        f"S{by_ordinal[one.scenario_id]}"
        for one in grades
        if one.scenario_id != base_scenario_id and not one.note.strip()
    ]
    if blank:
        out.append(
            f"{', '.join(blank)} graded without a reason, so the ledger's Other scenarios column "
            "carries a blank for them. That is the correct outcome when nobody wrote one: a "
            "generated sentence that reads like a human one is worse than a blank, because the "
            "ledger's value is that its sentences were written by someone who thought about the "
            "week."
        )

    base_note = next(
        (one.note for one in grades if one.scenario_id == base_scenario_id and one.note.strip()),
        None,
    )
    if base_note and base_note.strip() not in key_miss:
        out.append(
            "The base case was graded with a reason that is not in key_miss. The base case is "
            "excluded from other_grades on purpose and its reasoning is what key_miss is, so "
            "that sentence is not stored anywhere: fold it into key_miss and re-run."
        )
    return out


def calibration_read(settings: Settings, ledger: list[LedgerRow]) -> dict[str, Any]:
    """The rolling read, by the worker's `calibration.read`. The section 0 line.

    Rows go in oldest first, which is how the ledger accrues them and how the
    window of eight is taken.
    """
    calibration = load_calibration(settings)
    found = calibration.read(
        [
            calibration.GradedWeek(
                week=one.week,
                base_scenario_label=one.base_scenario_label,
                base_grade=one.base_grade,
                brier=one.brier,
            )
            for one in ledger
        ]
    )
    return {
        "graded_weeks": found.graded_weeks,
        "base_hit_score": _num(found.base_hit_score),
        "base_materialized": found.base_materialized,
        "mean_brier": _num(found.mean_brier),
        "brier_weeks": found.brier_weeks,
        "base_was_s2": found.base_was_s2,
        "window": found.window,
        "sentence": found.sentence,
    }


def _num(value: Decimal | None) -> str | None:
    return None if value is None else str(value)
