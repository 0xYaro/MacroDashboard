"""The evaluate step: `macro_core` and the run's observations into `macro_mart`.

Pure. Everything here takes the instant, the history and the judgment rows as
arguments and returns records. Nothing opens a socket or reads a clock, which is
what makes "re-running the evaluate step reproduces `macro_mart`" a testable
claim rather than a hope.

Three rules run through all of it.

**A condition that cannot be evaluated is `unevaluable`, never `not yet`.** It
propagates: a phase call with one unevaluable condition is not upgrade eligible,
a target band with no reading has `holding` null rather than false, and a setup
gate whose breadth is missing is `unevaluable` rather than `blocked`.

**A manual condition is never computed.** It has no expression. Its status is
read from `macro_core.condition_state_asserted` and carried through with
`source = 'asserted'`. A manual condition with no assertion is `unevaluable`.

**A void clause is kept and counted.** It stays in `clauses_total`, it is never
removed from its `n_of`, and no threshold is renormalised anywhere.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from . import release
from .expr import Context, ExpressionRefused, Outcome
from .expr import evaluate as evaluate_expression
from .families import classify
from .model import (
    AssertedCondition,
    Catalyst,
    CatalystWindowRow,
    ConditionState,
    Finding,
    PhaseCall,
    PhaseScore,
    Scenario,
    ScenarioSet,
    ScenarioState,
    ScenarioTarget,
    ScenarioTargetState,
    Setup,
    SetupState,
    SnapshotRow,
    Status,
    TriggerClause,
)
from .observations import History, Reading
from .series import Registry, Series

#: How close to a bound counts as "within ten percent of a breach", which the
#: web app renders in `--urgency-2`. Ten percent of the band's own width.
BREACH_FRACTION = Decimal("0.10")

#: How far ahead `macro_mart.catalyst_window` looks.
CATALYST_HORIZON_DAYS = 30

GateState = Literal["open", "blocked", "unevaluable"]
EntryState = Literal["not triggered", "open", "stopped", "target"]

#: The checks this module raises about the active judgment, on both runs.
CHECK_KEYS: tuple[str, ...] = ("phase_read_stale", "condition_malformed", "condition_coverage")


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


def snapshot(
    registry: Registry,
    history: History,
    *,
    now: datetime,
    anchor_asof: date | None,
    source_status: Mapping[str, str] | None = None,
) -> list[SnapshotRow]:
    """One row per declared series, in journal order.

    `delta_native` is the change over the series' own cadence: the difference
    against the immediately preceding observation of that same series, which for
    a session series is a day, for a weekly one a week, and for a monthly one a
    month. That is why it is never a one-day change on a weekly series: there is
    no prior day to take one against.

    `delta_week_open` is measured against `anchor_asof`, one date shared by every
    row, which is the active scenario set's authoring as-of. Not a calendar
    Monday, and not each series' own prior week: one anchor is what makes the
    weekly rendering's week-over-week column mean one thing. What each series
    is measured from is set by `_week_open`.
    """
    statuses = dict(source_status or {})
    rows: list[SnapshotRow] = []
    for series in registry.ordered():
        latest = history.latest(series.key)
        sessions = history.sessions(series.key)
        previous = history.at(series.key, sessions[-2]) if len(sessions) >= 2 else None
        anchor = _week_open(history, series.key, anchor_asof, latest)

        asof = None if latest is None else latest.asof
        next_due = _next_expected(series, latest, asof)
        rows.append(
            SnapshotRow(
                series_key=series.key,
                label=series.label,
                table_group=series.table_group,
                ordinal=series.ordinal,
                value=None if latest is None else latest.value,
                display="" if latest is None else latest.display,
                unit=series.unit or "",
                value_kind=series.value_kind,
                cadence=series.cadence,
                delta_native=_delta(
                    series.value_kind,
                    None if latest is None else latest.value,
                    None if previous is None else previous.value,
                ),
                delta_native_label=series.delta_native_label,
                delta_week_open=_delta(
                    series.value_kind,
                    None if latest is None else latest.value,
                    None if anchor is None else anchor.value,
                ),
                anchor_asof=anchor_asof,
                tier=None if latest is None else latest.tier,
                source=series.source_name,
                asof=asof,
                released=None if latest is None else latest.released,
                next_expected=next_due,
                observed_at=None if latest is None else latest.observed_at,
                freshness=_freshness(
                    series, history, asof, now, statuses.get(series.key), latest
                ),
                note=None if latest is None else (latest.extra.get("note") or None),
                extra={} if latest is None else dict(latest.extra),
            )
        )
    return rows


def _week_open(
    history: History, series_key: str, anchor_asof: date | None, latest: Reading | None
) -> Reading | None:
    """The reading a series' week-open delta is measured from, or `None`.

    The newest reading dated strictly before the anchor day, which is the tape
    the set was authored on: a set activated on Monday was written against
    Friday's close. Reading the anchor day itself compared every series that had
    printed Monday with itself, and HY, IG, breadth and the dollar showed 0.00
    all week.

    `None` when the series has printed nothing on or after the anchor day. A
    monthly print from before the week opened has not moved "since week open";
    it has not printed since, and a zero would say the former.
    """
    if anchor_asof is None or latest is None or latest.asof < anchor_asof:
        return None
    for asof in reversed(history.sessions(series_key)):
        if asof < anchor_asof:
            return history.at(series_key, asof)
    return None


def _next_expected(series: Series, latest: Reading | None, asof: date | None) -> date | None:
    """When the next observation is due.

    Derived from the series' declared release rule, except where the source
    stated it. An auction has no cadence, so its rule is `event` and the rule
    can say nothing; TreasuryDirect publishes the schedule, so the fetcher
    carries the next auction date on the observation and it is preferred here.

    A stated date is a fact from the producer. A derived one is a rule's
    inference. Where both exist the fact wins, and where only the inference
    exists nothing is invented to replace it.
    """
    stated = None if latest is None else _stated_next(latest)
    if stated is not None:
        return stated
    if asof is None:
        return None
    return release.next_expected(series.release_rule, asof)


def _stated_next(latest: Reading) -> date | None:
    """The next-observation date the producer published, if it did."""
    raw = latest.extra.get("next_expected")
    if isinstance(raw, date):
        return raw
    if isinstance(raw, str):
        try:
            return date.fromisoformat(raw)
        except ValueError:
            return None
    return None


def _delta(value_kind: str, current: Decimal | None, prior: Decimal | None) -> Decimal | None:
    """The difference, in the series' own unit. Null when either side is missing.

    A prose row has no delta at all. Subtracting one sentence from another is
    not a smaller sentence.
    """
    if value_kind == "text" or current is None or prior is None:
        return None
    return current - prior


def _freshness(
    series: Series,
    history: History,
    asof: date | None,
    now: datetime,
    source_state: str | None,
    latest: Reading | None = None,
    *,
    grace_days: int = 1,
) -> str:
    """One of the seven freshness words, in the order they take precedence.

    `failed` and `not_connected` are about the source, `gap` is about a specific
    reading, and only then does the release rule get a say. A source that is
    down is reported as down rather than as a series that happens to be
    overdue, and a source nothing has ever written to is reported as not
    connected rather than as one that fell behind.

    `asserted` is for a row a human writes. It has no release to be late for, so
    rating it against one would put a permanent amber beside a figure that is
    exactly as current as it was ever going to be.
    """
    if source_state in ("failed", "not_connected"):
        return source_state
    if "asserted" in series.source_name.lower():
        return "asserted"
    stated = None if latest is None else _stated_next(latest)
    if stated is not None:
        # The producer published a date, so this series can be late after all.
        # `event` alone could only ever say `fresh`, which is right for a series
        # with no schedule and wrong for one whose schedule is simply published
        # somewhere a release rule cannot reach.
        #
        # Compared at the instant, not the date, for the same reason every other
        # freshness comparison is: an auction is struck in the New York
        # afternoon, so at 21:00 Singapore on its own day it has not happened
        # and the row is not late.
        stated_readable = release.published_by(stated)
        if now < stated_readable:
            return "fresh"
        return "due" if now <= stated_readable + timedelta(days=grace_days) else "overdue"
    if asof is None and series.fetch_path == "agent_0930":
        # Nothing has ever written to this series. The 09:30 agents are outside
        # this worker, so until one runs the row is not connected, which is a
        # different fault from a source that missed a release it was owed.
        return "not_connected"
    if asof is not None:
        following = release.next_asof(series.release_rule, asof)
        readable = (
            None if following is None else release.readable_at(series.release_rule, following)
        )
        if (
            following is not None
            and readable is not None
            and now >= readable
            and history.has_gap(series.key, following)
        ):
            return "gap"
    return release.freshness(series.release_rule, asof, now, fetch_path=series.fetch_path)


# ---------------------------------------------------------------------------
# Conditions
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Scored:
    """One condition's state plus the outcome it came from."""

    state: ConditionState
    outcome: Outcome


def score_condition(
    *,
    condition_id: str,
    owner_kind: str,
    owner_id: str,
    ordinal: int,
    text: str,
    kind: str,
    expression: dict[str, Any] | None,
    ctx: Context,
    asserted: AssertedCondition | None = None,
    void_at: datetime | None = None,
    void_reason: str | None = None,
) -> Scored:
    """Score one condition, whatever kind it is.

    A void clause is `void` and is not evaluated: its premise moved, and running
    its expression anyway would put a number beside a clause that no longer
    means anything.

    A `manual` condition is never computed. Its status comes from the assertion,
    and with no assertion it is `unevaluable` rather than `not yet`.
    """
    if void_reason is not None:
        return Scored(
            state=ConditionState(
                condition_id=condition_id,
                owner_kind=owner_kind,  # type: ignore[arg-type]
                owner_id=owner_id,
                ordinal=ordinal,
                text=text,
                kind=kind,  # type: ignore[arg-type]
                status=Status.VOID,
                current_display=None,
                evidence={"why": void_reason, "kind": "void"},
                source="computed",
                void_at=void_at,
                void_reason=void_reason,
            ),
            outcome=Outcome(status=Status.VOID, evidence={"why": void_reason}),
        )

    if kind == "manual":
        if asserted is None:
            status, display, evidence = (
                Status.UNEVALUABLE,
                None,
                {
                    "kind": "manual",
                    "why": (
                        "a manual condition has no expression and carries no asserted status, "
                        "so it is awaiting judgment rather than false"
                    ),
                },
            )
        else:
            status, display, evidence = (
                asserted.status,
                asserted.current_text,
                {
                    "kind": "manual",
                    "asserted_by": asserted.asserted_by,
                    "why": f"asserted by {asserted.asserted_by}",
                },
            )
        return Scored(
            state=ConditionState(
                condition_id=condition_id,
                owner_kind=owner_kind,  # type: ignore[arg-type]
                owner_id=owner_id,
                ordinal=ordinal,
                text=text,
                kind=kind,  # type: ignore[arg-type]
                status=status,
                current_display=display,
                evidence=evidence,
                source="asserted",
            ),
            outcome=Outcome(status=status, display=display, evidence=evidence),
        )

    if expression is None:
        outcome = Outcome(
            status=Status.UNEVALUABLE,
            evidence={"why": f"a {kind} condition carries no expression"},
        )
    else:
        try:
            outcome = evaluate_expression(expression, ctx)
        except ExpressionRefused as exc:
            # A malformed expression is a configuration fault, and a fault is
            # never a false. It reports unevaluable and says why.
            outcome = Outcome(
                status=Status.UNEVALUABLE,
                evidence={"why": str(exc), "kind": "refused"},
            )
    return Scored(
        state=ConditionState(
            condition_id=condition_id,
            owner_kind=owner_kind,  # type: ignore[arg-type]
            owner_id=owner_id,
            ordinal=ordinal,
            text=text,
            kind=kind,  # type: ignore[arg-type]
            status=outcome.status,
            current_display=outcome.display,
            evidence=outcome.evidence,
            source="computed",
        ),
        outcome=outcome,
    )


# ---------------------------------------------------------------------------
# Phase
# ---------------------------------------------------------------------------


#: How many of a fresh call's four conditions must fail before the Desk says
#: this week's read may be out of date. One failing condition is a crack; two
#: is half of what the read was resting on.
STALE_AT = 2


def phase(
    call: PhaseCall,
    *,
    ctx: Context,
    asserted: Mapping[str, AssertedCondition],
    activated_on: date | None,
) -> tuple[PhaseScore, list[ConditionState]]:
    """Score one phase call's four conditions.

    **A fresh call's conditions are what must stay true for its phase to hold**,
    so each one either holds or has failed. The expression language answers
    `not yet` for a comparison that is false, and on a holds-while condition
    there is no "yet": a condition the read rested on that is no longer true has
    failed, and it is recorded as `failed`. `unevaluable` stays `unevaluable`
    and is never counted as either: a condition whose series has no reading
    for the day was not checked, which is not the same as failing.

    Two failed conditions raise `read_stale`: this week's read may be out of
    date. Nothing reclassifies on it. The phase is read fresh on Monday, and
    the flag is there so the operator knows how much weight the current read
    still carries until then.

    A **carried** call is one of the fourteen migrated journal rows, whose
    conditions counted toward the next phase. They are scored exactly as they
    were written, `not yet` included, and never raise the flag, because a
    "sign of the next phase" not being met is not the current read failing.
    """
    clipped = Context(
        asof=ctx.asof, history=ctx.history, registry=ctx.registry, not_before=activated_on
    )
    states: list[ConditionState] = []
    for condition in call.conditions:
        state = score_condition(
            condition_id=condition.condition_id,
            owner_kind="phase",
            owner_id=call.phase_call_id,
            ordinal=condition.ordinal,
            text=condition.text,
            kind=condition.kind,
            expression=condition.expr,
            ctx=clipped,
            asserted=asserted.get(condition.condition_id),
        ).state
        if call.fresh and state.status is Status.NOT_YET:
            state = replace(state, status=Status.FAILED)
        states.append(state)

    met = sum(1 for one in states if one.status is Status.MET)
    failed = sum(1 for one in states if one.status is Status.FAILED)
    unevaluable = sum(1 for one in states if one.status is Status.UNEVALUABLE)
    manual = sum(1 for one in states if one.kind == "manual")
    machine_checked = sum(
        1 for one in states if one.kind != "manual" and one.status is not Status.UNEVALUABLE
    )
    score = PhaseScore(
        asset=call.asset,
        phase_call_id=call.phase_call_id,
        phase=call.phase,
        phase_name=call.phase_name,
        # Authored, never derived from the conditions: depth inside the phase
        # is a judgment about the whole reading, not a count.
        stage=call.stage,
        met=met,
        of=len(states),
        machine_checked=machine_checked,
        manual=manual,
        unevaluable=unevaluable,
        failed=failed,
        read_stale=call.fresh and failed >= STALE_AT,
    )
    return score, states


def stale_read_finding(call: PhaseCall, score: PhaseScore) -> Finding | None:
    """The finding a stale read opens. It never reclassifies the call.

    The operator's rule is one fresh read a week. Between Mondays the read is
    held up against its own four conditions, and when half of them have failed
    the honest thing to say is that it may be out of date, not to produce a new
    one: a mid-week reclassification would be the weekly read's anchor coming
    back in by another door.
    """
    if not score.read_stale:
        return None
    return Finding(
        check_key="phase_read_stale",
        subject=call.asset,
        sentence=(
            f"This week's {call.asset} phase read may be out of date: {score.failed} of the "
            f"{score.of} things Phase {call.phase} was read on have failed since "
            f"{call.week}'s call. Nothing reclassifies on this; the phase is read fresh "
            "again on Monday."
        ),
        severity=2,
    )


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


def scenarios(
    scenario_set: ScenarioSet,
    *,
    ctx: Context,
    asserted: Mapping[str, AssertedCondition],
    activated_on: date | None,
) -> tuple[list[ScenarioState], list[ScenarioTargetState], list[ConditionState]]:
    """Score every clause and every target band of one active scenario set."""
    clipped = Context(
        asof=ctx.asof, history=ctx.history, registry=ctx.registry, not_before=activated_on
    )
    states: list[ScenarioState] = []
    target_states: list[ScenarioTargetState] = []
    condition_states: list[ConditionState] = []

    for scenario in scenario_set.scenarios:
        clause_states = [
            score_condition(
                condition_id=clause.condition_id,
                owner_kind="trigger",
                owner_id=scenario.scenario_id,
                ordinal=clause.ordinal,
                text=clause.text,
                kind=clause.kind,
                expression=clause.expr,
                ctx=clipped,
                asserted=asserted.get(clause.condition_id),
                # The moment, not just the reason. A clause voided last Tuesday
                # stays voided last Tuesday however many runs read it since.
                void_at=clause.void_at if clause.is_void else None,
                void_reason=clause.void_reason if clause.is_void else None,
            ).state
            for clause in scenario.clauses
        ]
        condition_states.extend(clause_states)
        targets = [target_state(target, ctx) for target in scenario.targets]
        target_states.extend(targets)
        states.append(_scenario_state(scenario, clause_states, targets))
    return states, target_states, condition_states


def _scenario_state(
    scenario: Scenario,
    clause_states: Sequence[ConditionState],
    targets: Sequence[ScenarioTargetState],
) -> ScenarioState:
    by_id = {clause.condition_id: clause for clause in scenario.clauses}
    fired = sum(1 for one in clause_states if one.status is Status.MET)
    void = sum(1 for one in clause_states if one.status is Status.VOID)
    # A required clause that is not `met` is unmet, whether it is false, void or
    # unevaluable. The state row reports how many, and the unevaluable ones are
    # visible in `condition_state`; collapsing them into "fired" would be the
    # exact silent pass the contract forbids.
    required_unmet = sum(
        1
        for one in clause_states
        if by_id[one.condition_id].required and one.status is not Status.MET
    )
    bands = [one for one in targets if one.kind == "band"]
    return ScenarioState(
        scenario_id=scenario.scenario_id,
        set_id=scenario.set_id,
        ordinal=scenario.ordinal,
        title=scenario.title,
        probability=scenario.probability,
        positioning=scenario.positioning,
        clauses_fired=fired,
        # Every clause, including every void one. It is never removed from the
        # denominator and the threshold is never renormalised, because doing so
        # would quietly make the scenario easier to satisfy.
        clauses_total=len(clause_states),
        clauses_void=void,
        required_unmet=required_unmet,
        # Only the targets a machine can score.
        bands_holding=sum(1 for one in bands if one.holding is True),
        bands_total=len(bands),
        # Every target row, band and manual alike. `targets_total` is the `n`
        # that "targets hit x of n" uses at grading: W39's S1 carries a
        # leadership statement alongside ten numeric bands and it counts toward
        # the eleven. The two pairs are kept apart because counting one kind in
        # one scenario and both in another is the W37 grading defect, and one
        # pair of columns would let it back in.
        targets_holding=sum(1 for one in targets if one.holding is True),
        targets_total=len(targets),
        premise_moved=void > 0,
    )


def target_state(target: ScenarioTarget, ctx: Context) -> ScenarioTargetState:
    """One target band against today's reading.

    `holding` null means unevaluable: a manual target is never auto-scored, and
    a band whose series has no observation for the evaluation date has no answer
    rather than a false one.

    `distance_to_breach` is how far the current value is from the nearest bound
    it would cross, in the series' own unit, and null whenever `holding` is.
    """
    if target.kind == "manual" or target.series_key is None:
        return ScenarioTargetState(
            target_id=target.target_id,
            scenario_id=target.scenario_id,
            series_key=None,
            label=target.label,
            kind=target.kind,
            comparator=target.comparator,
            text=target.text,
            lo=None,
            hi=None,
            current=None,
            holding=None,
            distance_to_breach=None,
        )

    reading = ctx.history.at(target.series_key, ctx.asof)
    current = None if reading is None else reading.value
    if current is None:
        return ScenarioTargetState(
            target_id=target.target_id,
            scenario_id=target.scenario_id,
            series_key=target.series_key,
            label=target.label,
            kind=target.kind,
            comparator=target.comparator,
            text=target.text,
            lo=target.lo,
            hi=target.hi,
            current=None,
            holding=None,
            distance_to_breach=None,
        )

    holding, distance = _band(target, current)
    return ScenarioTargetState(
        target_id=target.target_id,
        scenario_id=target.scenario_id,
        series_key=target.series_key,
        label=target.label,
        kind=target.kind,
        comparator=target.comparator,
        text=target.text,
        lo=target.lo,
        hi=target.hi,
        current=current,
        holding=holding,
        distance_to_breach=distance,
    )


def _band(target: ScenarioTarget, current: Decimal) -> tuple[bool | None, Decimal | None]:
    """Whether a band holds, and how far the value is from breaching it.

    Bounds are inclusive. A band is a region a price occupies, so a value
    exactly on a bound is inside it, which is what "S&P 7,520-7,800" means in
    every scenario table written to date.

    The distance is an unsigned magnitude in the series' own canonical unit,
    never a fraction and never a percentage: a band in basis points and a band
    in index points would otherwise read the same and mean different things. It
    is null whenever `holding` is, because the distance to breaching a band the
    value is already outside is not a distance to a breach.
    """
    if target.comparator == "between":
        if target.lo is None or target.hi is None:
            return None, None
        holding = target.lo <= current <= target.hi
        distance = min(current - target.lo, target.hi - current) if holding else None
        return holding, distance
    if target.comparator == "gt":
        if target.lo is None:
            return None, None
        holding = current > target.lo
        return holding, (current - target.lo) if holding else None
    if target.comparator == "lt":
        if target.hi is None:
            return None, None
        holding = current < target.hi
        return holding, (target.hi - current) if holding else None
    return None, None


# ---------------------------------------------------------------------------
# Setups
# ---------------------------------------------------------------------------


def setup_state(
    setup: Setup,
    ctx: Context,
    *,
    sector_focus_label: str | None = None,
) -> SetupState:
    """One setup's gate, its band touch and its entry state.

    Evaluated on the close, because every entry, invalidation, gate and planned
    R:R in the Setup Ledger is written against closes and eighteen graded rows
    depend on that meaning holding still.

    The band-touch flags are recorded alongside and are two facts, not one:
    `band_touched_close` is an entry, and `band_touched_today` is the session's
    high and low reaching into the band without one. "Entry band reached, gate
    refused, trade forgone" is the exact failure that has occurred three times,
    and it surfaces the same day.
    """
    if setup.series_key is None:
        # The setup does not say what its levels are levels of, so nothing here
        # can be measured. It is still shown: a setup that vanishes from the
        # board is indistinguishable from one nobody proposed, and the operator
        # needs to see it sitting there with the reason.
        return _unscored(setup)

    clipped = Context(
        asof=ctx.asof,
        history=ctx.history,
        registry=ctx.registry,
        not_before=setup.activated_on,
    )
    sessions = ctx.history.window(
        setup.series_key, ending=ctx.asof, count=400, not_before=setup.activated_on
    )
    today = ctx.history.at(setup.series_key, ctx.asof)

    gate, gate_reason = _gate(setup, clipped)
    band_touched_close = bool(
        today is not None
        and today.value is not None
        and setup.entry_lo <= today.value <= setup.entry_hi
    )
    band_touched_today = band_touched_close or _touched_intraday(setup, today)

    entry_state, _entry_session, r_running = _entry(setup, setup.series_key, sessions, ctx)
    series = ctx.registry.get(setup.series_key) if setup.series_key else None
    return SetupState(
        setup_id=setup.setup_id,
        week=setup.week,
        asset=setup.asset,
        # Null means the setup names no series and is not scored. It is never
        # defaulted: a guessed series produces an entry state and an R-multiple
        # that are wrong and not visibly so.
        series_key=setup.series_key,
        series_label=series.label if series is not None else None,
        name=setup.name,
        entry_lo=setup.entry_lo,
        entry_hi=setup.entry_hi,
        invalidation=setup.invalidation,
        invalidation_dir=setup.invalidation_dir,
        target=setup.target,
        rr_planned=setup.rr_planned,
        gate_state=gate,
        gate_reason=gate_reason,
        gate_text=setup.gate_text,
        sector_focus_label=sector_focus_label,
        band_touched_today=band_touched_today,
        band_touched_close=band_touched_close,
        entry_state=entry_state,
        sessions_carried=len(sessions),
        r_running=r_running,
    )


def _unscored(setup: Setup) -> SetupState:
    """A setup that names no series: present, and deliberately not scored.

    Every measured field is null or false rather than defaulted. A guessed
    series would produce an entry state, an R-multiple and a Setup Ledger row
    that are all wrong and none of them visibly so.
    """
    return SetupState(
        setup_id=setup.setup_id,
        week=setup.week,
        asset=setup.asset,
        series_key=None,
        series_label=None,
        name=setup.name,
        entry_lo=setup.entry_lo,
        entry_hi=setup.entry_hi,
        invalidation=setup.invalidation,
        invalidation_dir=setup.invalidation_dir,
        target=setup.target,
        rr_planned=setup.rr_planned,
        gate_state="unevaluable",
        gate_reason=(
            "the setup names no series, so its entry, invalidation and target are levels of "
            "nothing this worker can read, and it is not scored"
        ),
        gate_text=setup.gate_text,
        sector_focus_label=None,
        band_touched_today=False,
        band_touched_close=False,
        entry_state="not triggered",
        sessions_carried=0,
        r_running=None,
    )


def _gate(setup: Setup, ctx: Context) -> tuple[GateState, str]:
    if setup.gate_expr is None:
        return "open", (setup.gate_text or "no gate: the setup is taken on its levels alone")
    try:
        outcome = evaluate_expression(setup.gate_expr, ctx)
    except ExpressionRefused as exc:
        return "unevaluable", f"the gate expression could not be read: {exc}"
    if outcome.is_unevaluable:
        return "unevaluable", _gate_reason(outcome, Status.UNEVALUABLE)
    if outcome.is_met:
        return "open", _gate_reason(outcome, Status.MET)
    return "blocked", _gate_reason(outcome, Status.NOT_YET)


def _gate_reason(outcome: Outcome, verdict: Status) -> str:
    """One sentence naming the clause the gate turned on.

    A gate reported as `blocked` with no clause text is a refusal nobody can
    check, which is the failure that cost three weeks in a row. The evidence
    tree already carries every term that was read, so the sentence is built from
    it rather than from the gate's prose, and it names the reading as well as
    the threshold.
    """
    stated = str(outcome.evidence.get("why") or "")
    if stated:
        return stated
    decisive = _decisive_term(outcome.evidence, verdict)
    if decisive is None:
        return {
            Status.MET: "the gate held",
            Status.NOT_YET: "the gate did not hold",
        }.get(verdict, "the gate could not be evaluated")
    series, op, current, threshold = decisive
    verb = {
        Status.MET: "held",
        Status.NOT_YET: "refused",
    }.get(verdict, "could not be read")
    return f"{series} {op} {threshold} {verb} at {current}"


def _decisive_term(
    evidence: dict[str, Any], verdict: Status
) -> tuple[str, str, str, str] | None:
    """The first leaf comparison in the tree whose status settled the answer."""
    children = evidence.get("children")
    if isinstance(children, list):
        for child in children:
            child_evidence = child.get("evidence") or {}
            if str(child.get("status")) == str(verdict):
                found = _decisive_term(child_evidence, verdict)
                if found is not None:
                    return found
        return None
    if evidence.get("kind") != "op":
        return None
    series = str(evidence.get("series") or "")
    op = str(evidence.get("op") or "")
    current = str(evidence.get("current") or "")
    threshold = str(
        evidence.get("value")
        if evidence.get("value") is not None
        else f"[{evidence.get('lo')}, {evidence.get('hi')}]"
    )
    return series, op, current, threshold


def _touched_intraday(setup: Setup, today: Reading | None) -> bool:
    if today is None:
        return False
    high, low = today.session_high, today.session_low
    if high is None or low is None:
        return False
    return bool(low <= setup.entry_hi and high >= setup.entry_lo)


def _entry(
    setup: Setup, series_key: str, sessions: Sequence[date], ctx: Context
) -> tuple[EntryState, date | None, Decimal | None]:
    """Walk the sessions since activation and decide what the trade did.

    `series_key` is passed separately because `Setup.series_key` is nullable and
    a setup that names no series never reaches here: it is refused upstream
    rather than measured against a guess.

    Closes decide. A session's close inside the band opens the trade; a later
    close beyond the invalidation stops it; a later close at or beyond the
    target completes it. The window starts at `activated_on`, so a setup can
    never grade as entered on a session that preceded it.
    """
    entered_on: date | None = None
    entry_price: Decimal | None = None
    for session in sessions:
        reading = ctx.history.at(series_key, session)
        close = None if reading is None else reading.value
        if close is None:
            continue
        if entered_on is None:
            if setup.entry_lo <= close <= setup.entry_hi:
                entered_on, entry_price = session, close
            continue
        if _stopped(setup, close):
            return "stopped", entered_on, _r_multiple(setup, entry_price, close)
        if _target_hit(setup, close):
            return "target", entered_on, _r_multiple(setup, entry_price, close)

    if entered_on is None:
        return "not triggered", None, None
    last = ctx.history.at(series_key, sessions[-1]) if sessions else None
    running = None if last is None else _r_multiple(setup, entry_price, last.value)
    return "open", entered_on, running


def _stopped(setup: Setup, close: Decimal) -> bool:
    if setup.invalidation_dir == "below":
        return close < setup.invalidation
    return close > setup.invalidation


def _target_hit(setup: Setup, close: Decimal) -> bool:
    return close >= setup.target if setup.is_long else close <= setup.target


def _r_multiple(
    setup: Setup, entry_price: Decimal | None, close: Decimal | None
) -> Decimal | None:
    """The realised or running multiple of the initial risk.

    Risk is measured from the close that actually opened the trade, not from the
    middle of the band. The Setup Ledger's `R realised` column means the
    multiple of the risk the trade was taken at, and a mid-band entry price
    would state an R the operator never had.
    """
    if entry_price is None or close is None:
        return None
    risk = entry_price - setup.invalidation if setup.is_long else setup.invalidation - entry_price
    if risk <= 0:
        return None
    move = close - entry_price if setup.is_long else entry_price - close
    return move / risk


# ---------------------------------------------------------------------------
# Catalysts
# ---------------------------------------------------------------------------


def catalyst_window(
    catalysts: Iterable[Catalyst],
    clauses: Iterable[TriggerClause],
    *,
    now: datetime,
    horizon_days: int = CATALYST_HORIZON_DAYS,
    ids: Mapping[str, str] | None = None,
) -> list[CatalystWindowRow]:
    """The next thirty days, with which trigger clauses depend on each catalyst.

    A catalyst stays in the window until it is more than a day past its end,
    not its start: a three-day conference that opened yesterday is still on,
    and last night's 02:00 FOMC is still worth seeing, dimmed, at 09:30. One
    older than that is gone even if the worker missed a morning, because the
    page reads this table as it was last written.

    A day-dated event has no time, so it lasts its whole day. The category is
    carried from the parse, and classified here from the name when a row
    predates the column, so no row reaches the page without one.
    """
    key_to_id = dict(ids or {})
    grace = now - timedelta(days=1)
    referenced: dict[str, list[str]] = {}
    for clause in clauses:
        if clause.catalyst_id:
            referenced.setdefault(clause.catalyst_id, []).append(clause.condition_id)

    rows: list[CatalystWindowRow] = []
    for catalyst in catalysts:
        if catalyst.status != "tracked" or catalyst.starts_at is None:
            continue
        days_out = (catalyst.starts_at.date() - now.date()).days
        if days_out > horizon_days:
            continue
        last = catalyst.ends_at or catalyst.starts_at
        if catalyst.date_precision != "exact":
            last += timedelta(days=1)
        if last < grace:
            continue
        family: str | None
        category: str
        if catalyst.category is None:
            family, category = classify(catalyst.name)
        else:
            family, category = catalyst.family, catalyst.category
        identifier = key_to_id.get(catalyst.external_key, catalyst.external_key)
        rows.append(
            CatalystWindowRow(
                catalyst_id=identifier,
                name=catalyst.name,
                starts_at=catalyst.starts_at,
                date_precision=catalyst.date_precision,
                affected=catalyst.affected,
                days_out=days_out,
                referenced_by=tuple(referenced.get(identifier, ())),
                source_url=catalyst.source_url,
                ends_at=catalyst.ends_at,
                date_text=catalyst.raw_date_text or None,
                date_estimated=catalyst.date_estimated,
                family=family,
                category=category,
            )
        )
    rows.sort(key=lambda row: (row.days_out, row.name))
    return rows


# ---------------------------------------------------------------------------
# Coverage findings
# ---------------------------------------------------------------------------


def malformed_findings(states: Sequence[ConditionState]) -> list[Finding]:
    """Every stored condition the validator would refuse.

    `score_condition` already turns a refusal into `unevaluable` with the
    diagnosis in its evidence, so the run is never wrong. This turns it into a
    finding as well, because a condition that can never be evaluated is a
    different fault from one whose reading has not arrived, and only one of them
    is fixed by waiting.
    """
    return [
        Finding(
            check_key="condition_malformed",
            subject=f"{one.owner_kind} {one.ordinal}: {one.text[:70]}",
            sentence=(
                f"A {one.kind} condition cannot be evaluated as written: "
                f"{one.evidence.get('why')}. It reports unevaluable on every run until it is "
                "rewritten, which no amount of waiting will change."
            ),
            severity=1,
        )
        for one in states
        if one.evidence.get("kind") == "refused"
    ]


def coverage_findings(states: Sequence[ConditionState]) -> list[Finding]:
    """The condition-coverage check that exists only because the clock is daily.

    A set whose conditions are mostly unevaluable is not a tracker that says
    "not yet"; it is a tracker that cannot see, and the difference has to be on
    the screen.
    """
    if not states:
        return []
    unevaluable = [one for one in states if one.status is Status.UNEVALUABLE]
    if not unevaluable:
        return []
    return [
        Finding(
            check_key="condition_coverage",
            subject=f"{len(unevaluable)} of {len(states)} conditions",
            sentence=(
                f"{len(unevaluable)} of {len(states)} conditions are unevaluable on this run, so "
                "the trackers and triggers over them are reporting what they can see rather than "
                "what is true; none of them is scored as not yet."
            ),
            severity=2 if len(unevaluable) * 2 < len(states) else 1,
        )
    ]
