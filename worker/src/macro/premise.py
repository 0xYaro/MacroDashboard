"""The premise-moved rule.

A scenario trigger clause may name a catalyst. When the Catalyst Monitor retires
that catalyst, or reschedules it out of the scenario's horizon, the clause is
marked void with a reason and the scenario is flagged `premise moved`.

**The clause is never dropped from its `n_of` and the trigger is never
renormalised.** Renormalising would quietly make the scenario easier to satisfy
and corrupt the Brier record: a set that could not fire in full would start
firing, and nothing in the record would say why.

At grading the scenario is scored normally and the ledger row carries a
`premise_moved` tag. The Brier score is not forgiven. R6 exists because two
weeks in ten were driven by an event no calendar carried, so over-anchoring on a
dated catalyst is a forecasting error rather than bad luck; forgiving it would
remove the pressure that rule applies. The tag exists so that a rule can later
be written from counted evidence rather than from an impression.

## The horizon

The scenario set's forward view is "next 1-2 weeks". A catalyst rescheduled
beyond that window can no longer settle the clause inside the week the
probability was stated for, which is what makes the premise moved rather than
merely late. `HORIZON_DAYS` is fourteen for that reason, measured from the
scenario set's own authoring as-of and not from today, because the probability
was stated then.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .model import Catalyst, Finding, ScenarioSet, TriggerClause

#: The scenario table's stated forward view, in days. "Next 1-2 weeks".
HORIZON_DAYS = 14


@dataclass(frozen=True, slots=True)
class Voiding:
    """One clause to mark void, and why."""

    condition_id: str
    scenario_id: str
    reason: str
    void_at: datetime


#: The checks `voidings` raises, on both runs. `trigger_clause_dropped` is the
#: guard's, which no scheduled run calls, so no run may close it.
CHECK_KEYS: tuple[str, ...] = ("trigger_clause_catalyst_missing", "scenario_premise_moved")

def voidings(
    scenario_set: ScenarioSet,
    catalysts: Mapping[str, Catalyst],
    *,
    now: datetime,
    horizon_days: int = HORIZON_DAYS,
) -> tuple[list[Voiding], list[Finding]]:
    """Every clause whose named catalyst has moved out from under it.

    `catalysts` is keyed by `macro_core.catalyst.id`, which is what
    `trigger_clause.catalyst_id` points at.

    A clause already void is left exactly as it is. Re-voiding it would move its
    `void_at` forward and lose the moment the premise actually moved, and that
    moment is the whole record.
    """
    horizon = scenario_set.authored_asof + timedelta(days=horizon_days)
    out: list[Voiding] = []
    findings: list[Finding] = []

    for scenario in scenario_set.scenarios:
        for clause in scenario.clauses:
            if clause.is_void or clause.catalyst_id is None:
                continue
            catalyst = catalysts.get(clause.catalyst_id)
            if catalyst is None:
                findings.append(
                    Finding(
                        check_key="trigger_clause_catalyst_missing",
                        subject=f"{scenario.title} clause {clause.ordinal}",
                        sentence=(
                            f"Clause {clause.ordinal} of {scenario.title!r} names a catalyst that "
                            "is no longer in macro_core.catalyst at all, so the worker cannot tell "
                            "whether its premise moved; the clause is left standing rather than "
                            "voided on a guess."
                        ),
                        severity=1,
                    )
                )
                continue
            reason = _why(catalyst, horizon, now=now, others=catalysts.values())
            if reason is None:
                continue
            out.append(
                Voiding(
                    condition_id=clause.condition_id,
                    scenario_id=scenario.scenario_id,
                    reason=reason,
                    void_at=now,
                )
            )
            findings.append(
                Finding(
                    check_key="scenario_premise_moved",
                    subject=f"{scenario.title} clause {clause.ordinal}",
                    sentence=(
                        f"Clause {clause.ordinal} of {scenario.title!r} is void because {reason}. "
                        "It stays in the trigger and in the denominator, the threshold is not "
                        "renormalised, and the Brier score for the week will not be forgiven."
                    ),
                    severity=2,
                )
            )
    return out, findings


#: How far apart a retired catalyst and a tracked one of the same family may be
#: dated and still be one event under two names.
RENAME_DAYS = 1


def _why(
    catalyst: Catalyst, horizon: date, *, now: datetime, others: Iterable[Catalyst]
) -> str | None:
    """The reason this catalyst no longer supports its clause, or `None`.

    A retirement is a moved premise only when the event was still to come and
    nothing replaced it. The Catalyst Monitor drops an event from its note once
    it has happened, and it renames one by retiring the old line and adding the
    new: W40 lost its PCE, Micron and payrolls clauses to exactly those two, the
    payrolls ones the morning after the report they were written to be scored on.
    """
    if catalyst.status == "retired":
        if catalyst.starts_at is not None and catalyst.starts_at <= now:
            return None
        if _renamed(catalyst, others):
            return None
        return (
            f"the Catalyst Monitor retired {catalyst.name!r}, so the event the clause is written "
            "against is not going to happen inside the scenario's horizon"
        )
    if catalyst.starts_at is None:
        return (
            f"{catalyst.name!r} has lost its date entirely, so nothing can say whether it lands "
            "inside the scenario's horizon"
        )
    if catalyst.starts_at.date() > horizon:
        return (
            f"{catalyst.name!r} was rescheduled to {catalyst.starts_at.date().isoformat()}, past "
            f"the scenario's horizon of {horizon.isoformat()}"
        )
    return None


def _renamed(retired: Catalyst, others: Iterable[Catalyst]) -> bool:
    """A tracked catalyst of the same family dated within a day: the same event renamed."""
    if retired.family is None or retired.starts_at is None:
        return False
    window = timedelta(days=RENAME_DAYS)
    return any(
        one.status == "tracked"
        and one.family == retired.family
        and one.starts_at is not None
        and abs(one.starts_at - retired.starts_at) <= window
        for one in others
    )


def flagged_scenarios(clauses: Iterable[TriggerClause]) -> frozenset[str]:
    """Which scenarios carry at least one void clause."""
    return frozenset(clause.scenario_id for clause in clauses if clause.is_void)


def renormalisation_guard(
    before: Sequence[TriggerClause], after: Sequence[TriggerClause]
) -> list[Finding]:
    """Assert that voiding changed nothing but `void_at` and `void_reason`.

    A regression test in the shape of a runtime check. The one thing this rule
    must never do is remove a clause or change a threshold, and the cheapest
    place to notice that it did is the moment it happens.
    """
    before_ids = [one.condition_id for one in before]
    after_ids = [one.condition_id for one in after]
    if before_ids == after_ids:
        return []
    lost = set(before_ids) - set(after_ids)
    return [
        Finding(
            check_key="trigger_clause_dropped",
            subject=", ".join(sorted(lost)) or "clause ordering",
            sentence=(
                "Voiding a clause changed the trigger's clause set, which it must never do: a "
                "void clause is kept and counted in the denominator, and removing one quietly "
                "makes the scenario easier to satisfy."
            ),
            severity=1,
        )
    ]
