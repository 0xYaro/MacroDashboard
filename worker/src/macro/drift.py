"""What has moved since the active call was made, and whether it is enough to ask again.

`docs/DECISIONS.md` fixes the clock: evidence moves daily and judgment moves
weekly, and a probability is re-priced only on a material change. The operator
can now ask for a refresh from the Desk at any time, so the system owes them an
honest answer to the question they are really asking when they reach for the
button: has enough changed?

**The baseline is re-evaluated, not remembered.** Every condition, band and
setup gate is a pure function of the observation history at a date, so the
state the call was made against is recomputed by evaluating the same judgment
at the last US session that closed before the set was activated, over today's
history. Nothing is stored between runs to drift away from the rows it
describes, and a replay of any past run produces the same drift it produced
then. A reading that arrived late for an
anchor-day session is counted at the anchor, which is the honest direction: the
call should have seen it.

**Only a change of state counts, never a change of data availability.** A
condition that was unevaluable at the anchor and is `met` now did not flip; a
series arrived. Transitions to or from unevaluable are left out, so a vendor
outage cannot manufacture a refresh prompt.

**Material is the decision's own list, and nothing else.** A trigger firing in
full, a phase condition flipping, a tail risk going acute, a scenario's
premise moving. Each weighs 3. A setup gate or entry state changing weighs 2,
because it changes what the operator may do today without changing the call. A
single clause or band weighs 1. The score is capped at 10 so it reads as a
gauge, and a refresh is suggested on any material change or at 6.

**A stale phase read is flagged, not weighed.** A fresh phase call is held
against the four things it was read on. When two of them have failed since the
call, the Desk says this week's read may be out of date. The item weighs
nothing, because the condition flips under it are already counted, and nothing
reclassifies on it: the operator's rule is one fresh read a week, on Monday.

Pure: no clock, no socket. `now` and every state are arguments.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from . import clock, evaluate
from .expr import Context
from .model import (
    AssertedCondition,
    ConditionState,
    PhaseCall,
    ScenarioSet,
    ScenarioState,
    ScenarioTargetState,
    Setup,
    SetupState,
    Status,
)

DriftKind = Literal[
    "trigger_fired",
    "phase_condition",
    "phase_stale",
    "premise_moved",
    "tail_risk",
    "setup_gate",
    "setup_entry",
    "clause",
    "band",
]

MATERIAL_WEIGHT = 3
SETUP_WEIGHT = 2
MINOR_WEIGHT = 1
SCORE_CAP = 10
SUGGEST_AT = 6
#: How far before the anchor the baseline may look for an item's last
#: measured state. Ten days covers a weekly series and a long weekend.
LOOKBACK_DAYS = 10


@dataclass(frozen=True, slots=True)
class DriftItem:
    """One change since the call, in the words the Desk prints."""

    kind: DriftKind
    subject: str
    before: str | None
    after: str | None
    material: bool
    weight: int
    sentence: str

    def as_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "subject": self.subject,
            "before": self.before,
            "after": self.after,
            "material": self.material,
            "weight": self.weight,
            "sentence": self.sentence,
        }


@dataclass(frozen=True, slots=True)
class Drift:
    """One `macro_mart.drift` row."""

    set_id: str
    week: str
    anchor_asof: date | None
    asof: date | None
    score: int
    material: int
    refresh_suggested: bool
    reason: str
    items: tuple[DriftItem, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class TailRiskSeen:
    """What drift needs of a tail risk: how bad, and since when it has been listed."""

    name: str
    severity: str
    first_seen: date
    #: The day the weekly note dropped it, or `None` while it is still listed.
    #: A retired risk is kept only so the first ingest's date stays known.
    retired_on: date | None = None


def compute(
    *,
    scenario_set: ScenarioSet,
    phase_calls: Sequence[PhaseCall],
    setups: Sequence[Setup],
    asserted: Mapping[str, AssertedCondition],
    ctx: Context,
    now_conditions: Sequence[ConditionState],
    now_scenarios: Sequence[ScenarioState],
    now_targets: Sequence[ScenarioTargetState],
    now_setups: Sequence[SetupState],
    tail_risks: Sequence[TailRiskSeen] = (),
) -> Drift:
    """Diff the judgment's state on the tape the set was written on against its state now."""
    anchor = baseline_session(scenario_set.activated_at)
    if anchor >= ctx.asof:
        return Drift(
            set_id=scenario_set.set_id,
            week=scenario_set.week,
            anchor_asof=anchor,
            asof=ctx.asof,
            score=0,
            material=0,
            refresh_suggested=False,
            reason=(
                "No session has closed since the call was made, so nothing has moved against it."
            ),
        )

    # The baseline is each item's last measurable state on or before the
    # anchor, looking back at most LOOKBACK_DAYS. The evaluator scores a band
    # only against an observation dated the evaluation day, and never carries
    # a value forward; a weekly flow series has no reading on most anchors, so
    # an exact-day baseline would read unevaluable and drift would miss the
    # very move it exists for (the W39 ETF band, 23 September). Walking back to
    # the last day it *was* measured states nothing about a day it did not
    # describe: it is the state the author was looking at.
    days = [anchor - timedelta(days=back) for back in range(LOOKBACK_DAYS + 1)]
    before_conditions = _first_measured(
        [
            _conditions_at(ctx.with_asof(day), phase_calls, scenario_set, asserted)
            for day in days
        ],
        key=lambda one: one.condition_id,
        measured=lambda one: _comparable(one.status),
    )
    passes = [
        evaluate.scenarios(
            scenario_set,
            ctx=ctx.with_asof(day),
            asserted=asserted,
            activated_on=scenario_set.activated_at.date(),
        )
        for day in days
    ]
    before_scenarios = passes[0][0]
    before_targets = _first_measured(
        [one[1] for one in passes],
        key=lambda one: one.target_id,
        measured=lambda one: one.holding is not None,
    )
    before_setups = _first_measured(
        [[evaluate.setup_state(one, ctx.with_asof(day)) for one in setups] for day in days],
        key=lambda one: one.setup_id,
        measured=lambda one: one.gate_state != "unevaluable",
    )

    items: list[DriftItem] = []
    items += _conditions(before_conditions, now_conditions, phase_calls, scenario_set)
    items += _stale_reads(before_conditions, now_conditions, phase_calls)
    items += _fired(before_scenarios, now_scenarios, before_conditions, now_conditions)
    items += _premise(before_scenarios, now_scenarios)
    items += _bands(before_targets, now_targets, now_scenarios)
    items += _setups(before_setups, now_setups)
    # A tail risk is news against the day the call was made, not the tape it
    # was written on: one listed over the weekend before a Monday call was in
    # front of the author.
    items += _tail_risks(tail_risks, scenario_set.authored_asof)

    # A stale phase read leads, because it is the sentence that tells the
    # operator how much the rest of the list should worry them.
    items.sort(key=lambda one: (one.kind != "phase_stale", -one.weight, one.kind, one.subject))
    score = min(SCORE_CAP, sum(one.weight for one in items))
    material = sum(1 for one in items if one.material)
    suggested = material > 0 or score >= SUGGEST_AT
    return Drift(
        set_id=scenario_set.set_id,
        week=scenario_set.week,
        anchor_asof=anchor,
        asof=ctx.asof,
        score=score,
        material=material,
        refresh_suggested=suggested,
        reason=_reason(items, material, score, suggested) + _stale_sentence(items),
        items=tuple(items),
    )


def baseline_session(activated_at: datetime) -> date:
    """The last US session that closed strictly before the call was activated.

    The tape the call was written on. A set activated at 09:30 Singapore on a
    Monday was written against Friday's close, and anchoring on the UTC date
    of activation (Monday) compared Monday's close with itself on Tuesday and
    reported that no session had closed since. The same rule as
    `evaluate._week_open`, which measures the week-open delta from the newest
    reading dated before the activation day.
    """
    return clock.last_completed_us_session(activated_at - timedelta(microseconds=1))


def _conditions_at(
    ctx: Context,
    phase_calls: Sequence[PhaseCall],
    scenario_set: ScenarioSet,
    asserted: Mapping[str, AssertedCondition],
) -> list[ConditionState]:
    out: list[ConditionState] = []
    for call in phase_calls:
        _, states = evaluate.phase(
            call, ctx=ctx, asserted=asserted, activated_on=call.activated_at.date()
        )
        out.extend(states)
    _, _, clause_states = evaluate.scenarios(
        scenario_set, ctx=ctx, asserted=asserted, activated_on=scenario_set.activated_at.date()
    )
    out.extend(clause_states)
    return out


def _first_measured[T](
    passes: Sequence[Sequence[T]],
    *,
    key: Callable[[T], str],
    measured: Callable[[T], bool],
) -> list[T]:
    """Per item, the newest pass in which it was measured, else the newest pass."""
    chosen: dict[str, T] = {}
    for one in passes[0] if passes else ():
        chosen[key(one)] = one
    for items in passes:
        for one in items:
            held = chosen.get(key(one))
            if held is not None and not measured(held) and measured(one):
                chosen[key(one)] = one
    # A pass further back only ever replaces an unmeasured item, so the first
    # measured state found walking back from the anchor is the one kept.
    return list(chosen.values())


def _comparable(status: Status) -> bool:
    return status not in (Status.UNEVALUABLE, Status.VOID)


def _conditions(
    before: Sequence[ConditionState],
    now: Sequence[ConditionState],
    phase_calls: Sequence[PhaseCall],
    scenario_set: ScenarioSet,
) -> list[DriftItem]:
    assets = {call.phase_call_id: call.asset for call in phase_calls}
    fresh = {call.phase_call_id for call in phase_calls if call.fresh}
    ordinals = {one.scenario_id: one.ordinal for one in scenario_set.scenarios}
    then = {one.condition_id: one for one in before}
    out: list[DriftItem] = []
    for current in now:
        prior = then.get(current.condition_id)
        if prior is None or prior.status is current.status:
            continue
        if not (_comparable(prior.status) and _comparable(current.status)):
            continue
        if current.owner_kind == "phase":
            asset = assets.get(current.owner_id, "the")
            if current.owner_id in fresh:
                verb = (
                    "no longer holds" if current.status is Status.FAILED else "holds again"
                )
                sentence = f"The {asset} phase condition \"{current.text}\" {verb}."
            else:
                sentence = (
                    f"The {asset} tracker condition \"{current.text}\" went from "
                    f"{prior.status} to {current.status}."
                )
            out.append(
                DriftItem(
                    kind="phase_condition",
                    subject=f"{asset} phase condition {current.ordinal}",
                    before=str(prior.status),
                    after=str(current.status),
                    material=True,
                    weight=MATERIAL_WEIGHT,
                    sentence=sentence,
                )
            )
        else:
            label = f"S{ordinals.get(current.owner_id, '?')}"
            out.append(
                DriftItem(
                    kind="clause",
                    subject=f"{label} clause {current.ordinal}",
                    before=str(prior.status),
                    after=str(current.status),
                    material=False,
                    weight=MINOR_WEIGHT,
                    sentence=f"{label}'s clause \"{current.text}\" went from {prior.status} "
                    f"to {current.status}.",
                )
            )
    return out


def _stale_reads(
    before: Sequence[ConditionState],
    now: Sequence[ConditionState],
    phase_calls: Sequence[PhaseCall],
) -> list[DriftItem]:
    """A fresh phase read whose failed conditions reached two since the call."""
    out: list[DriftItem] = []
    for call in phase_calls:
        if not call.fresh:
            continue
        ids = {one.condition_id for one in call.conditions}
        then = sum(1 for one in before if one.condition_id in ids and one.status is Status.FAILED)
        failed = sum(1 for one in now if one.condition_id in ids and one.status is Status.FAILED)
        if failed < evaluate.STALE_AT or then >= evaluate.STALE_AT:
            continue
        out.append(
            DriftItem(
                kind="phase_stale",
                subject=f"{call.asset} phase",
                before=f"{then} of {len(ids)} failed",
                after=f"{failed} of {len(ids)} failed",
                material=False,
                weight=0,
                sentence=f"This week's {call.asset} phase read may be out of date: {failed} of "
                f"the {len(ids)} things it holds on have failed since the call. The phase is "
                "read fresh again on Monday.",
            )
        )
    return out


def _stale_sentence(items: Sequence[DriftItem]) -> str:
    stale = [one.subject.removesuffix(" phase") for one in items if one.kind == "phase_stale"]
    if not stale:
        return ""
    books = " and ".join(stale)
    return f" This week's {books} phase read may be out of date."


def _fired_in_full(state: ScenarioState) -> bool:
    live = state.clauses_total - state.clauses_void
    return live > 0 and state.clauses_fired >= live


def _fired(
    before: Sequence[ScenarioState],
    now: Sequence[ScenarioState],
    before_conditions: Sequence[ConditionState],
    now_conditions: Sequence[ConditionState],
) -> list[DriftItem]:
    then = {one.scenario_id: one for one in before}
    # A clause that was unevaluable at the anchor and is met now did not fire; its
    # series arrived. A trigger completed only by such a clause has not fired in
    # full against the call, it has become measurable.
    prior_status = {one.condition_id: one.status for one in before_conditions}
    arrived = {
        one.owner_id
        for one in now_conditions
        if one.owner_kind == "trigger"
        and one.status is Status.MET
        and prior_status.get(one.condition_id) is Status.UNEVALUABLE
    }
    out: list[DriftItem] = []
    for current in now:
        prior = then.get(current.scenario_id)
        if prior is None or _fired_in_full(prior) or not _fired_in_full(current):
            continue
        if current.scenario_id in arrived:
            continue
        out.append(
            DriftItem(
                kind="trigger_fired",
                subject=f"S{current.ordinal}",
                before=f"{prior.clauses_fired} of {prior.clauses_total}",
                after=f"{current.clauses_fired} of {current.clauses_total}",
                material=True,
                weight=MATERIAL_WEIGHT,
                sentence=f"S{current.ordinal}'s trigger has fired in full: "
                f"{current.clauses_fired} of {current.clauses_total} clauses met.",
            )
        )
    return out


def _premise(before: Sequence[ScenarioState], now: Sequence[ScenarioState]) -> list[DriftItem]:
    then = {one.scenario_id: one for one in before}
    out: list[DriftItem] = []
    for current in now:
        prior = then.get(current.scenario_id)
        if not current.premise_moved or (prior is not None and prior.premise_moved):
            continue
        out.append(
            DriftItem(
                kind="premise_moved",
                subject=f"S{current.ordinal}",
                before=None,
                after=f"{current.clauses_void} void",
                material=True,
                weight=MATERIAL_WEIGHT,
                sentence=f"S{current.ordinal}'s premise moved: a catalyst it named was retired "
                "or rescheduled out of the horizon.",
            )
        )
    return out


def _holding_word(holding: bool | None) -> str:
    return "in band" if holding else "out of band"


def _bands(
    before: Sequence[ScenarioTargetState],
    now: Sequence[ScenarioTargetState],
    scenarios: Sequence[ScenarioState],
) -> list[DriftItem]:
    ordinals = {one.scenario_id: one.ordinal for one in scenarios}
    then = {one.target_id: one for one in before}
    out: list[DriftItem] = []
    for current in now:
        prior = then.get(current.target_id)
        if prior is None or prior.holding is None or current.holding is None:
            continue
        if prior.holding == current.holding:
            continue
        label = f"S{ordinals.get(current.scenario_id, '?')}"
        verb = "entered" if current.holding else "left"
        out.append(
            DriftItem(
                kind="band",
                subject=f"{label} {current.label}",
                before=_holding_word(prior.holding),
                after=_holding_word(current.holding),
                material=False,
                weight=MINOR_WEIGHT,
                sentence=f"{current.label} {verb} {label}'s band"
                + (f" at {_level(current.current)}" if current.current is not None else "")
                + ".",
            )
        )
    return out


def _setups(before: Sequence[SetupState], now: Sequence[SetupState]) -> list[DriftItem]:
    then = {one.setup_id: one for one in before}
    out: list[DriftItem] = []
    for current in now:
        prior = then.get(current.setup_id)
        if prior is None:
            continue
        if (
            prior.gate_state != current.gate_state
            and "unevaluable" not in (prior.gate_state, current.gate_state)
        ):
            out.append(
                DriftItem(
                    kind="setup_gate",
                    subject=current.name,
                    before=prior.gate_state,
                    after=current.gate_state,
                    material=False,
                    weight=SETUP_WEIGHT,
                    sentence=f"The gate on {current.name} went from {prior.gate_state} "
                    f"to {current.gate_state}.",
                )
            )
        if prior.entry_state != current.entry_state:
            out.append(
                DriftItem(
                    kind="setup_entry",
                    subject=current.name,
                    before=prior.entry_state,
                    after=current.entry_state,
                    material=False,
                    weight=SETUP_WEIGHT,
                    sentence=f"{current.name} went from {prior.entry_state} "
                    f"to {current.entry_state}.",
                )
            )
    return out


def _tail_risks(risks: Sequence[TailRiskSeen], anchor: date) -> list[DriftItem]:
    # Severity has no history in macro_core, so "went acute" is read as "is acute
    # and was first listed after the call". A risk that was already acute at the
    # anchor was in front of the author and is not news.
    #
    # The first day any risk was ever parsed is the day the feed was connected,
    # not the day those risks appeared: every one of them was already in
    # Blackswan Events.md, some for weeks. Only a risk first listed after both
    # the call and that first ingest is new, and only while it is still listed:
    # a risk the weekly note dropped is retired, not acute. Retired rows still
    # count toward the first ingest, which is a fact about the feed.
    if not risks:
        return []
    connected = min(risk.first_seen for risk in risks)
    since = max(anchor, connected)
    return [
        DriftItem(
            kind="tail_risk",
            subject=risk.name,
            before=None,
            after=risk.severity,
            material=True,
            weight=MATERIAL_WEIGHT,
            sentence=f"A new acute tail risk was listed on {risk.first_seen.isoformat()}: "
            f"{risk.name}.",
        )
        for risk in risks
        if risk.severity == "acute" and risk.first_seen > since and risk.retired_on is None
    ]


def _level(value: Decimal) -> str:
    """A level as the Desk prints it: whole numbers above 1,000, two places below."""
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    return f"{value:,.2f}"


def _reason(items: Sequence[DriftItem], material: int, score: int, suggested: bool) -> str:
    if not items:
        return "Nothing the call depends on has changed state since it was made."
    head = f"{len(items)} change{'s' if len(items) != 1 else ''} since the call"
    if material:
        return (
            f"{head}, {material} of them material under the re-price rule. "
            "A refresh is warranted."
        )
    if suggested:
        return f"{head}, none material on its own, but together they score {score} of 10."
    return f"{head}, none material. The call stands as written."
