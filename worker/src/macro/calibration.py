"""Brier and the rolling calibration read, ported from `calibration.py`.

The arithmetic is unchanged from the vault skill's script, deliberately and
exactly. Twelve graded weeks were scored with it and the Brier series is only
comparable if the formula does not move, so this module reproduces it rather
than improving it.

## Brier

Outcome weights are `Materialized 1`, `Partial 0.5`, `Not 0`, normalised to sum
to 1 across the set. Brier is the sum over the three scenarios of `(p - o)²`,
where `p` is the stated probability as a fraction and `o` is the normalised
outcome weight. It runs 0 for perfect to 2 for the worst case.

If nothing materialized at all, the truth lay outside the set, and the score is
`sum(p²) + 1`, which is the worst case by construction.

## The rolling read

Base-case hit rate over up to the last eight graded weeks, with Materialized
counting 1 and Partial counting a half. Beside it: how often the base fully
materialized, the mean Brier where one was recorded, and how often the base was
S2, which is the habit check.

`calibration.py` computes the hit rate as a *rate* in its JSON and prints the
raw score in its sentence. `macro_mart.calibration.base_hit_score` is the score,
matching the sentence, and `graded_weeks` is the denominator beside it.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from .model import CalibrationRead

Grade = Literal["Materialized", "Partial", "Not"]

#: Outcome weights. Not configurable: the Brier series is only comparable across
#: twelve graded weeks because these three numbers have never moved.
WEIGHT: dict[str, Decimal] = {
    "materialized": Decimal(1),
    "partial": Decimal("0.5"),
    "not": Decimal(0),
}

#: The calibration read is defined over the last eight graded weeks.
DEFAULT_WINDOW = 8


@dataclass(frozen=True, slots=True)
class ScenarioOutcome:
    """One scenario's stated probability and how it graded."""

    label: str
    probability: int
    grade: Grade

    @property
    def weight(self) -> Decimal:
        return WEIGHT[self.grade.strip().lower()]


def brier(outcomes: Sequence[ScenarioOutcome]) -> Decimal:
    """The Brier score for one week's scenario set.

    Ported from `calibration.py`'s `brier`, including its out-of-set case.
    """
    if not outcomes:
        raise ValueError("a Brier score needs a scenario set")
    probabilities = [Decimal(one.probability) / Decimal(100) for one in outcomes]
    total_weight = sum((one.weight for one in outcomes), Decimal(0))
    if total_weight == 0:
        # Nothing materialized at all, so the truth lay outside the set. The
        # worst case by construction, and it is scored rather than excused.
        return sum((p * p for p in probabilities), Decimal(0)) + Decimal(1)
    return sum(
        (
            (p - one.weight / total_weight) ** 2
            for p, one in zip(probabilities, outcomes, strict=True)
        ),
        Decimal(0),
    )


@dataclass(frozen=True, slots=True)
class GradedWeek:
    """One `macro_core.week_grade` row, as the calibration read needs it."""

    week: str
    base_scenario_label: str
    base_grade: Grade
    brier: Decimal | None

    @property
    def base_was_s2(self) -> bool:
        """Whether the base case was the middle scenario. The habit check.

        Matched the same way `calibration.py` matched it: the label as written
        begins with `S2`. Rules R2, R4, R5, R6, R10, R11 and R12 all exist
        because probabilities were set badly once a week with deliberation, and
        a base that is always S2 is the shape that habit takes.
        """
        return bool(re.match(r"\s*S2\b", self.base_scenario_label or ""))


def read(weeks: Sequence[GradedWeek], window: int = DEFAULT_WINDOW) -> CalibrationRead:
    """The rolling calibration read over the last `window` graded weeks.

    Ported from `calibration.py`'s `read`, over rows instead of over a markdown
    table. Rows arrive oldest first, the way the ledger accrues them, and the
    last `window` of them are the ones scored.
    """
    last = list(weeks[-window:])
    count = len(last)
    score = sum((WEIGHT[one.base_grade.strip().lower()] for one in last), Decimal(0))
    materialized = sum(1 for one in last if one.base_grade.strip().lower() == "materialized")
    briers = [one.brier for one in last if one.brier is not None]
    was_s2 = sum(1 for one in last if one.base_was_s2)
    mean = (sum(briers, Decimal(0)) / Decimal(len(briers))) if briers else None

    return CalibrationRead(
        graded_weeks=count,
        base_hit_score=score if count else None,
        base_materialized=materialized,
        mean_brier=mean,
        brier_weeks=len(briers),
        base_was_s2=was_s2,
        window=window,
        sentence=sentence(count, score, materialized, mean, len(briers), was_s2),
    )


def sentence(
    graded_weeks: int,
    score: Decimal,
    materialized: int,
    mean_brier: Decimal | None,
    brier_weeks: int,
    was_s2: int,
) -> str:
    """The section 0 line, worded exactly as `calibration.py` words it."""
    if graded_weeks == 0:
        return (
            "**Calibration:** no graded weeks yet, so there is no base-case hit rate and no "
            "Brier mean to report."
        )
    percent = Decimal(100) * Decimal(materialized) / Decimal(graded_weeks)
    line = (
        f"**Calibration:** base case scored {score:.1f} of the last {graded_weeks} graded weeks "
        f"(Partial = half credit), fully materialized in {materialized} of {graded_weeks} "
        f"({percent:.0f}%)"
    )
    if mean_brier is not None:
        line += (
            f"; mean Brier {mean_brier:.2f} over {brier_weeks} scored weeks "
            "(0 = perfect, 2 = worst)"
        )
    line += f"; base was S2 in {was_s2} of {graded_weeks}."
    return line
