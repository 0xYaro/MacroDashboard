"""What a model produced, checked before any of it reaches the database.

Two kinds of check live here and they are not the same kind.

**Shape.** Decoding JSON into the records in `model.py`. Every refusal here is a
sentence naming the field and the row, because the caller is a model that will
be asked to fix it and "check constraint violated" tells it nothing.

**Meaning.** The checks a constraint cannot make, and one of them is the reason
this package exists:

- Every `expr` and `predicate` expression is handed to the worker's own
  evaluator against the real registry. Not a lookalike written here: the point
  is to know that the 21:00 rescore will be able to resolve it, and a second
  implementation agreeing with the first proves nothing about the third.
  A malformed expression, an unknown series key, an unlisted predicate or a
  numeric comparison against a `text` series all fail here rather than on a
  Tuesday night with the condition silently reporting `unevaluable`.
- A condition whose expression evaluates to `unevaluable` today is **not** a
  failure. A series with no reading for the evaluation date is exactly what
  `unevaluable` is for, and refusing it would mean a Monday with a missing print
  could not author a condition over that series at all.
- Probabilities sum to 100 and there are exactly three scenarios. The database
  asserts this at `COMMIT` through a deferred trigger, and it is checked here as
  well so that the sentence the operator reads names the three numbers rather
  than an internal trigger name.
- Every band names a series. Every setup names a series. A trigger clause that
  names a dated catalyst is bound to it.
- The clause split is lossless: joining a scenario's clauses in ordinal order
  with `"; "` reproduces the trigger as written.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from . import preflight as preflight_rules
from .config import Settings
from .model import (
    LARGE_JUMP,
    PHASE_NAMES,
    PHASES,
    Check,
    Condition,
    Event,
    HoldingsCheckProposal,
    NarrativeObservation,
    PhaseCallProposal,
    PhaseNote,
    Proposal,
    Refused,
    ScenarioGrade,
    ScenarioProposal,
    ScenarioSetProposal,
    SectorFocusProposal,
    SetupProposal,
    StrategyProposal,
    TargetProposal,
    TriggerClauseProposal,
)
from .worker_bridge import evaluator
from .worker_bridge import registry as load_registry

TYPED_KINDS = frozenset({"expr", "predicate"})

#: How many sessions the probe history carries. The longest window any predicate
#: in `config/predicates.md` takes is ten, and a window that cannot be filled
#: would make a well-formed predicate look unevaluable for the wrong reason.
PROBE_SESSIONS = 15


# ---------------------------------------------------------------------------
# Small decoders. Each one names the field it refused.
# ---------------------------------------------------------------------------


def _str(raw: Any, field: str, *, required: bool = True) -> str:
    value = "" if raw is None else str(raw).strip()
    if required and not value:
        raise Refused(f"{field} is required and was empty")
    return value


def _opt_str(raw: Any) -> str | None:
    value = None if raw is None else str(raw).strip()
    return value or None


def _int(raw: Any, field: str) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise Refused(f"{field} is not a whole number: {raw!r}") from exc


def _dec(raw: Any, field: str) -> Decimal:
    """`Decimal(str(v))`, never a float, at every edge. The contract's rule."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raise Refused(f"{field} is required and was empty")
    try:
        return Decimal(str(raw).strip().replace(",", ""))
    except InvalidOperation as exc:
        raise Refused(f"{field} is not a number: {raw!r}") from exc


def _opt_dec(raw: Any, field: str) -> Decimal | None:
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    return _dec(raw, field)


def _date(raw: Any, field: str) -> date:
    try:
        return date.fromisoformat(str(raw).strip()[:10])
    except ValueError as exc:
        raise Refused(
            f"{field} is not an ISO date: {raw!r}. An observation date is never blank and never "
            "inferred from the calendar."
        ) from exc


def _expr(raw: Any, field: str) -> dict[str, Any] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise Refused(f"{field} must be a JSON object, not {type(raw).__name__}")
    return raw


# ---------------------------------------------------------------------------
# Mode A
# ---------------------------------------------------------------------------


def narrative_points(
    payload: list[dict[str, Any]],
    *,
    kinds: dict[str, tuple[str, str | None, int]],
    allowed: frozenset[str],
    observed_at: datetime,
    source_prefix: str,
) -> tuple[list[NarrativeObservation], list[Check]]:
    """Decode one search category's `points` into observations.

    A point this package cannot place is dropped with a named check rather than
    stored under a key no condition can resolve. The old pipeline matched
    returns by display label and filed two different readings under one row;
    here the key is the identity and an unknown one is a refusal, not a guess.
    """
    out: list[NarrativeObservation] = []
    checks: list[Check] = []
    seen: set[str] = set()

    for index, raw in enumerate(payload):
        where = f"point {index}"
        try:
            key = _str(raw.get("series"), f"{where}.series")
        except Refused as exc:
            checks.append(Check(f"point_{index}_series", False, str(exc)))
            continue
        if key not in allowed:
            checks.append(
                Check(
                    "series_not_on_this_path",
                    False,
                    f"{key!r} is not one of the series whose fetch_path is agent_0930, "
                    "so this run does not own it. It was dropped rather than written over a "
                    "reading the worker fetched deterministically.",
                )
            )
            continue
        if key in seen:
            checks.append(
                Check(
                    "series_returned_twice",
                    False,
                    f"{key!r} came back twice in one category; the first was kept. This is the "
                    "shape of the defect that filed a FedWatch reading under the Next FOMC row.",
                )
            )
            continue
        seen.add(key)

        value_kind, _unit, tier_floor = kinds[key]
        try:
            tier = max(_int(raw.get("tier"), f"{where}.tier"), tier_floor)
            asof = _date(raw.get("asof"), f"{where}.asof")
            released_raw = _opt_str(raw.get("released"))
            note = _opt_str(raw.get("note"))
            display = _opt_str(raw.get("display")) or _opt_str(raw.get("value")) or ""
            value_raw = _opt_str(raw.get("value"))

            if value_kind == "text":
                # A prose row carries no number at all. A number here would be a
                # figure no condition can ever read, and a null with no sentence
                # would be indistinguishable from a Tier 4 failure.
                value = None
                if not display:
                    raise Refused(
                        f"{key} is a text series: its whole content is the sentence in display, "
                        "and it came back with none."
                    )
                note = note or None
                if value_raw:
                    checks.append(
                        Check(
                            "text_series_carried_a_number",
                            False,
                            f"{key} is a text series and came back with a value of {value_raw!r}. "
                            "The number was dropped and the sentence kept: a text series may only "
                            "appear in a manual condition, so a number stored here could never be "
                            "read by anything.",
                            severity="warn",
                        )
                    )
            elif tier >= 4 or not value_raw:
                # The tier ladder's rule, carried from gather.workflow.js: a
                # Tier 4 point is an empty value and a note, never a number the
                # model felt was about right and never a zero.
                value = None
                tier = 4
                note = note or f"could not retrieve {key}; check the source named above"
                display = display or "—"
            else:
                value = _dec(value_raw, f"{where}.value")

            out.append(
                NarrativeObservation(
                    series_key=key,
                    asof=asof,
                    value=value,
                    display=display or "—",
                    tier=tier,
                    # A Tier 4 point has no source by definition: it is the
                    # point that could not be sourced. The prefix still names
                    # which search agent looked, which is what makes a gap
                    # attributable.
                    source=f"{source_prefix}: {_opt_str(raw.get('source')) or 'no source found'}",
                    observed_at=observed_at,
                    released=None if not released_raw else _date(released_raw, f"{where}.released"),
                    note=note,
                    value_kind=value_kind,
                )
            )
        except Refused as exc:
            checks.append(Check(f"point_{key}", False, str(exc)))

    return out, checks


def events(payload: list[dict[str, Any]]) -> tuple[list[Event], list[Check]]:
    """Decode the events agent's return into `macro_core.event` rows."""
    out: list[Event] = []
    checks: list[Check] = []
    for index, raw in enumerate(payload):
        where = f"event {index}"
        try:
            kind = _str(raw.get("kind"), f"{where}.kind").lower()
            if kind not in ("macro", "structure"):
                raise Refused(
                    f"{where}.kind is {kind!r}; the log has two sections and a row belongs to one "
                    "of them: macro or structure."
                )
            what = _str(raw.get("whatHappened"), f"{where}.whatHappened")
            why = _str(raw.get("whyItMatters"), f"{where}.whyItMatters")
            assets = raw.get("assets") or []
            out.append(
                Event(
                    occurred_on=_date(raw.get("date"), f"{where}.date"),
                    kind=kind,  # type: ignore[arg-type]
                    text=f"{what} {why}".strip(),
                    assets=tuple(str(one).strip() for one in assets if str(one).strip()),
                    surprise=bool(raw.get("surprise")),
                    source=_opt_str(raw.get("source")),
                )
            )
        except Refused as exc:
            checks.append(Check(f"event_{index}", False, str(exc)))
    return out, checks


# ---------------------------------------------------------------------------
# Mode B: grading
# ---------------------------------------------------------------------------


def scenario_grades(payload: list[dict[str, Any]]) -> tuple[list[ScenarioGrade], list[Check]]:
    out: list[ScenarioGrade] = []
    checks: list[Check] = []
    for index, raw in enumerate(payload):
        try:
            grade = _str(raw.get("grade"), f"grade {index}.grade")
            if grade not in ("Materialized", "Partial", "Not"):
                raise Refused(
                    f"grade {index} is {grade!r}; the vocabulary is Materialized, Partial, Not, "
                    "and it has not moved across twelve graded weeks."
                )
            # The note is optional and stays optional. It is prose written
            # looking backward at what happened, nothing here derives it, and a
            # generated sentence that reads like a human one is worse than a
            # blank; `grade.close_the_week` reports the blank instead.
            out.append(
                ScenarioGrade(
                    scenario_id=_str(raw.get("scenario_id"), f"grade {index}.scenario_id"),
                    grade=grade,  # type: ignore[arg-type]
                    note=_opt_str(raw.get("note")) or "",
                )
            )
        except Refused as exc:
            checks.append(Check(f"grade_{index}", False, str(exc)))
    return out, checks


# ---------------------------------------------------------------------------
# Mode B: the proposal
# ---------------------------------------------------------------------------


def proposal(payload: dict[str, Any], *, authored_at: datetime | None = None) -> Proposal:
    """Decode one week's authored judgment. Raises `Refused` on the first fault.

    The phase calls are not in it. They were classified by `author classify`,
    before last week's call was revealed, and the proposal carries only the
    change note written afterwards. `attach_reads` merges the two.
    """
    at = authored_at or datetime.now(UTC)
    week = _str(payload.get("week"), "week")

    notes = tuple(
        _phase_note(one, index) for index, one in enumerate(payload.get("phase_calls") or ())
    )
    strategies = tuple(
        _strategy(one, week=week, at=at) for one in payload.get("strategies") or ()
    )
    setups = tuple(_setup(one, week=week, at=at) for one in payload.get("setups") or ())
    return Proposal(
        week=week,
        scenario_set=_scenario_set(payload.get("scenario_set") or {}, week=week, at=at),
        strategies=strategies,
        setups=setups,
        phase_notes=notes,
    )


#: What a fresh read may not say about itself, because it is taken before last
#: week's call is known. `author classify` fills in the prior itself.
_NOT_THE_READS = ("prior_phase", "prior_week", "direction", "change_note", "blind_phase",
                  "blind_reason", "amends", "amend_reason")


def phase_reads(
    payload: dict[str, Any], *, classified_at: datetime | None = None
) -> tuple[PhaseCallProposal, ...]:
    """Decode the week's fresh reads, the input to `author classify`.

    Refuses a read that names last week's phase or a transition. The read is
    taken with the prior call out of view, so a read that states one either was
    not taken that way or is guessing; either way it is not what the column
    will claim it is.
    """
    at = classified_at or datetime.now(UTC)
    week = _str(payload.get("week"), "week")
    return tuple(
        _phase_call(one, week=week, at=at) for one in payload.get("phase_calls") or ()
    )


def _phase_note(raw: dict[str, Any], index: int) -> PhaseNote:
    asset = _str(raw.get("asset"), f"phase_calls[{index}].asset")
    extra = sorted(set(raw) - {"asset", "change_note"})
    if extra:
        raise Refused(
            f"phase_calls[{index}] ({asset}) carries {', '.join(extra)}. The phase was classified "
            "at `author classify`, before last week's call was shown, and it is fixed there. "
            "After the reveal the proposal writes only the one-sentence `change_note`."
        )
    return PhaseNote(
        asset=asset,  # type: ignore[arg-type]
        change_note=_str(raw.get("change_note"), f"phase_calls[{index}].change_note"),
    )


def attach_reads(found: Proposal, reads: tuple[PhaseCallProposal, ...]) -> Proposal:
    """The week's fresh reads, with the change notes the proposal wrote after the reveal.

    A note for an asset with no read is refused: it would be a sentence about a
    transition nobody classified.
    """
    by_asset = {one.asset: one for one in found.phase_notes}
    stray = sorted(set(by_asset) - {one.asset for one in reads})
    if stray:
        raise Refused(
            f"the proposal writes a change note for {', '.join(stray)}, and no fresh read for "
            f"{found.week} exists for it. Run `author phase-context` and `author classify` first."
        )
    merged = tuple(
        replace(read, change_note=by_asset[read.asset].change_note)
        if read.asset in by_asset
        else read
        for read in reads
    )
    return replace(found, phase_calls=merged)


def _condition(raw: dict[str, Any]) -> Condition:
    return Condition(
        ordinal=_int(raw.get("ordinal"), "condition.ordinal"),
        text=_str(raw.get("text"), "condition.text"),
        kind=_kind(raw.get("kind"), "condition.kind"),
        expr=_expr(raw.get("expr"), "condition.expr"),
        asserted_status=_opt_str(raw.get("asserted_status")),
        asserted_text=_opt_str(raw.get("asserted_text")),
    )


def _kind(raw: Any, field: str) -> Any:
    value = _str(raw, field)
    if value not in ("expr", "predicate", "manual"):
        raise Refused(f"{field} is {value!r}; it is one of expr, predicate, manual")
    return value


def _phase_call(raw: dict[str, Any], *, week: str, at: datetime) -> PhaseCallProposal:
    asset = _str(raw.get("asset"), "phase_call.asset")
    if asset not in ("equities", "crypto"):
        raise Refused(f"phase_call.asset is {asset!r}; only equities and crypto carry phase calls")
    named = [key for key in _NOT_THE_READS if raw.get(key) is not None]
    if named:
        raise Refused(
            f"the {asset} read carries {', '.join(named)}. A fresh read is taken with last "
            "week's call out of view, so it cannot state one: `author classify` fills in last "
            "week's phase itself, and the change note is written after the reveal."
        )
    stage = _opt_str(raw.get("stage"))
    if stage is not None and stage not in ("early", "mid", "late"):
        raise Refused(
            f"phase_call.stage is {stage!r}; it is early, mid or late, the depth of this "
            "reading inside its phase."
        )
    return PhaseCallProposal(
        asset=asset,  # type: ignore[arg-type]
        week=week,
        phase=_int(raw.get("phase"), "phase_call.phase"),
        phase_name=_str(raw.get("phase_name"), "phase_call.phase_name"),
        reason=_str(raw.get("reason"), "phase_call.reason"),
        evidence=tuple(str(one).strip() for one in raw.get("evidence") or () if str(one).strip()),
        falsifier=_str(raw.get("falsifier"), "phase_call.falsifier"),
        conditions=tuple(_condition(one) for one in raw.get("conditions") or ()),
        authored_at=at,
        stage=stage,  # type: ignore[arg-type]
        stage_reason=_opt_str(raw.get("stage_reason")),
        classified_at=at,
    )


def _clause(raw: dict[str, Any]) -> TriggerClauseProposal:
    return TriggerClauseProposal(
        ordinal=_int(raw.get("ordinal"), "clause.ordinal"),
        text=_str(raw.get("text"), "clause.text"),
        kind=_kind(raw.get("kind"), "clause.kind"),
        expr=_expr(raw.get("expr"), "clause.expr"),
        required=bool(raw.get("required", False)),
        catalyst_id=_opt_str(raw.get("catalyst_id")),
        asserted_status=_opt_str(raw.get("asserted_status")),
        asserted_text=_opt_str(raw.get("asserted_text")),
    )


def _target(raw: dict[str, Any]) -> TargetProposal:
    kind = _str(raw.get("kind"), "target.kind")
    if kind not in ("band", "manual"):
        raise Refused(f"target.kind is {kind!r}; it is band or manual")
    comparator = _opt_str(raw.get("comparator"))
    if comparator and comparator not in ("between", "gt", "lt"):
        raise Refused(f"target.comparator is {comparator!r}; it is between, gt or lt")
    return TargetProposal(
        label=_str(raw.get("label"), "target.label"),
        kind=kind,  # type: ignore[arg-type]
        series_key=_opt_str(raw.get("series_key")),
        comparator=comparator,  # type: ignore[arg-type]
        lo=_opt_dec(raw.get("lo"), "target.lo"),
        hi=_opt_dec(raw.get("hi"), "target.hi"),
        text=_opt_str(raw.get("text")),
    )


def _scenario_set(raw: dict[str, Any], *, week: str, at: datetime) -> ScenarioSetProposal:
    return ScenarioSetProposal(
        week=week,
        synthesis=_str(raw.get("synthesis"), "scenario_set.synthesis"),
        probability_check=_str(raw.get("probability_check"), "scenario_set.probability_check"),
        swing_variables=list(raw.get("swing_variables") or ()),
        rules_waived=tuple(_int(one, "rules_waived") for one in raw.get("rules_waived") or ()),
        scenarios=tuple(
            ScenarioProposal(
                ordinal=_int(one.get("ordinal"), "scenario.ordinal"),
                title=_str(one.get("title"), "scenario.title"),
                probability=_int(one.get("probability"), "scenario.probability"),
                positioning=_opt_str(one.get("positioning")),
                clauses=tuple(_clause(c) for c in one.get("clauses") or ()),
                targets=tuple(_target(t) for t in one.get("targets") or ()),
            )
            for one in raw.get("scenarios") or ()
        ),
        authored_at=at,
        amends=_opt_str(raw.get("amends")),
        amend_reason=_opt_str(raw.get("amend_reason")),
    )


def _strategy(raw: dict[str, Any], *, week: str, at: datetime) -> StrategyProposal:
    asset = _str(raw.get("asset"), "strategy.asset")
    if asset not in ("equities", "crypto"):
        raise Refused(f"strategy.asset is {asset!r}")
    return StrategyProposal(
        week=week,
        asset=asset,  # type: ignore[arg-type]
        stance=_str(raw.get("stance"), "strategy.stance"),
        posture=_str(raw.get("posture"), "strategy.posture"),
        exposure_lo=_opt_dec(raw.get("exposure_lo"), "strategy.exposure_lo"),
        exposure_hi=_opt_dec(raw.get("exposure_hi"), "strategy.exposure_hi"),
        exposure_operated_lo=_opt_dec(raw.get("exposure_operated_lo"), "operated_lo"),
        exposure_operated_hi=_opt_dec(raw.get("exposure_operated_hi"), "operated_hi"),
        sector_focus=tuple(
            SectorFocusProposal(
                band=_band(one.get("band"), "sector_focus.band"),
                text=_str(one.get("text"), "sector_focus.text"),
            )
            for one in raw.get("sector_focus") or ()
        ),
        holdings_check=tuple(
            HoldingsCheckProposal(
                ticker=_str(one.get("ticker"), "holdings_check.ticker"),
                action=_action(one.get("action")),
                text=_opt_str(one.get("text")),
                band=None if one.get("band") is None else _band(one["band"], "holdings.band"),
            )
            for one in raw.get("holdings_check") or ()
        ),
        authored_at=at,
        amends=_opt_str(raw.get("amends")),
        amend_reason=_opt_str(raw.get("amend_reason")),
    )


def _band(raw: Any, field: str) -> Any:
    value = _str(raw, field).upper()
    if value not in ("CRITICAL", "HOLD", "WATCH", "AVOID"):
        raise Refused(f"{field} is {value!r}; the four bands are CRITICAL, HOLD, WATCH, AVOID")
    return value


def _action(raw: Any) -> Any:
    value = _str(raw, "holdings_check.action").lower()
    if value not in ("keep", "trim", "add", "exit"):
        raise Refused(f"holdings_check.action is {value!r}; it is keep, trim, add or exit")
    return value


def _setup(raw: dict[str, Any], *, week: str, at: datetime) -> SetupProposal:
    asset = _str(raw.get("asset"), "setup.asset")
    if asset not in ("equities", "crypto"):
        raise Refused(f"setup.asset is {asset!r}")
    direction = _str(raw.get("invalidation_dir"), "setup.invalidation_dir")
    if direction not in ("above", "below"):
        raise Refused(f"setup.invalidation_dir is {direction!r}; it is above or below")
    return SetupProposal(
        week=week,
        asset=asset,  # type: ignore[arg-type]
        name=_str(raw.get("name"), "setup.name"),
        style=_opt_str(raw.get("style")),
        series_key=_str(raw.get("series_key"), "setup.series_key"),
        entry_lo=_dec(raw.get("entry_lo"), "setup.entry_lo"),
        entry_hi=_dec(raw.get("entry_hi"), "setup.entry_hi"),
        invalidation=_dec(raw.get("invalidation"), "setup.invalidation"),
        invalidation_dir=direction,  # type: ignore[arg-type]
        target=_dec(raw.get("target"), "setup.target"),
        rr_planned=_opt_dec(raw.get("rr_planned"), "setup.rr_planned"),
        gate_expr=_expr(raw.get("gate_expr"), "setup.gate_expr"),
        gate_text=_opt_str(raw.get("gate_text")),
        band=None if raw.get("band") is None else _band(raw["band"], "setup.band"),
        rationale=_opt_str(raw.get("rationale")),
        authored_at=at,
        amends=_opt_str(raw.get("amends")),
        amend_reason=_opt_str(raw.get("amend_reason")),
    )


# ---------------------------------------------------------------------------
# Meaning
# ---------------------------------------------------------------------------


def check(found: Proposal, settings: Settings, *, asof: date) -> list[Check]:
    """Every check that has to pass before a proposal is written.

    `asof` is the evaluation date the expressions are tried against. An
    expression that comes back `unevaluable` there is not a fault: it means the
    series has no reading for that day, which is what `unevaluable` is for.
    A *refused* expression is a fault, and that is the difference this function
    turns on.
    """
    reg = load_registry(settings)
    out: list[Check] = []

    out.extend(_check_probabilities(found.scenario_set))
    out.extend(_check_expressions(_expression_items(found), settings, asof=asof))
    out.extend(_check_targets(found.scenario_set))
    out.extend(_check_setups(found, reg))
    if found.phase_calls:
        out.extend(_check_reads(found.phase_calls))
        out.extend(_check_transitions(found.phase_calls))
    out.extend(_check_clause_split(found.scenario_set))
    out.extend(_check_typed_triggers(found.scenario_set))
    out.extend(_check_amendments(found))
    return out


def read_checks(
    reads: tuple[PhaseCallProposal, ...], settings: Settings, *, asof: date
) -> list[Check]:
    """Every check a fresh read has to pass before `author classify` writes it.

    The same checks `check` runs on the whole proposal later, less the
    transition: at this step last week's call is not known, by design.
    """
    items = [
        (f"{call.asset} phase condition {condition.ordinal}", condition.kind, condition.expr)
        for call in reads
        for condition in call.conditions
    ]
    return _check_expressions(items, settings, asof=asof) + _check_reads(reads)


def _expression_items(found: Proposal) -> list[tuple[str, str, dict[str, Any] | None]]:
    """Every typed-or-manual condition in a proposal, labelled for its sentence."""
    items: list[tuple[str, str, dict[str, Any] | None]] = [
        (f"{call.asset} phase condition {condition.ordinal}", condition.kind, condition.expr)
        for call in found.phase_calls
        for condition in call.conditions
    ]
    items += [
        (f"S{scenario.ordinal} clause {clause.ordinal}", clause.kind, clause.expr)
        for scenario in found.scenario_set.scenarios
        for clause in scenario.clauses
    ]
    items += [
        (f"setup {setup.name!r} gate", "expr", setup.gate_expr)
        for setup in found.setups
        if setup.gate_expr is not None
    ]
    return items


def _check_probabilities(scenario_set: ScenarioSetProposal) -> list[Check]:
    scenarios = scenario_set.scenarios
    total = sum(one.probability for one in scenarios)
    out = [
        Check(
            "scenario_count",
            len(scenarios) == 3,
            f"{scenario_set.week} carries {len(scenarios)} scenarios; exactly three are required, "
            "and the deferred trigger refuses anything else at COMMIT.",
        ),
        Check(
            "probabilities_sum_to_100",
            total == 100,
            f"{scenario_set.week} probabilities are "
            + " + ".join(f"S{one.ordinal} {one.probability}" for one in scenarios)
            + f" = {total}, and they must sum to 100.",
        ),
    ]
    reflexive = sorted(one.probability for one in scenarios) == [30, 30, 40]
    out.append(
        Check(
            "not_a_reflexive_403030",
            not reflexive,
            "The set is 40/30/30, which the house style names as the shape a set takes when no "
            "probability was actually chosen. An asymmetric set is fine; a reflexive one is not.",
            severity="warn",
        )
    )
    return out


def _check_expressions(
    items: Sequence[tuple[str, str, dict[str, Any] | None]], settings: Settings, *, asof: date
) -> list[Check]:
    """Every typed expression, tried against the worker's own evaluator.

    The history is a **probe**, not the live tape. It carries fifteen sessions
    of a placeholder reading for every series the expression names, so the
    expression runs all the way to the bottom: the comparison executes, the
    predicate reads its arguments, and the session window is populated.

    The live tape would not do this job. An expression over a series with no
    print for the evaluation date resolves to `unevaluable` *before* its operand
    is read, so a comparison with no `value` at all would pass validation on a
    Monday when the print was late and fail silently for the rest of the week.
    The probe makes the answer depend on the expression and on nothing else.

    What comes back is therefore `met`, `not yet` or `failed`, and the verdict
    is not consulted: the placeholder values are arbitrary. What is asserted is
    only that the evaluator accepted the expression, which it refuses to do for
    an unknown series key, an unlisted predicate, a malformed shape, a missing
    operand and a numeric comparison against a `text` series.
    """
    expr_module, _predicates, observations = evaluator(settings)
    reg = load_registry(settings)

    def probe(expression: dict[str, Any] | None) -> Any:
        keys = expr_module.series_referenced(expression)
        readings = [
            observations.Reading(
                series_key=key,
                asof=date.fromordinal(asof.toordinal() - offset),
                value=Decimal(1),
                display="probe",
                tier=1,
                extra={"session_high": "1", "session_low": "1"},
            )
            for key in keys
            for offset in range(PROBE_SESSIONS)
        ]
        return expr_module.Context(
            asof=asof, history=observations.History(readings), registry=reg
        )

    out: list[Check] = []
    typed = 0
    manual = 0

    def try_one(label: str, kind: str, expression: dict[str, Any] | None) -> None:
        nonlocal typed, manual
        if kind == "manual":
            manual += 1
            return
        typed += 1
        try:
            expr_module.evaluate(expression, probe(expression))
        except expr_module.ExpressionRefused as exc:
            out.append(
                Check(
                    "expression_refused",
                    False,
                    f"{label}: the worker's evaluator refused this expression, so the 21:00 "
                    f"rescore would report it unevaluable for ever. {exc}",
                )
            )
        except Exception as exc:
            out.append(
                Check(
                    "expression_faulted",
                    False,
                    f"{label}: evaluating this expression raised "
                    f"{type(exc).__name__}: {exc}. A condition that cannot be evaluated by the "
                    "worker is a condition the daily run will never score.",
                )
            )

    for label, kind, expression in items:
        try_one(label, kind, expression)

    total = typed + manual
    out.append(
        Check(
            "typed_condition_coverage",
            typed > 0,
            f"{typed} of {total} conditions and clauses carry a typed expression and {manual} are "
            "manual. Every migrated week is 0 typed, which is why the daily evaluation currently "
            "does nothing for them.",
            severity="note",
        )
    )
    return out


def _check_targets(scenario_set: ScenarioSetProposal) -> list[Check]:
    out: list[Check] = []
    for scenario in scenario_set.scenarios:
        bands = [one for one in scenario.targets if one.kind == "band"]
        if not scenario.targets:
            out.append(
                Check(
                    f"s{scenario.ordinal}_has_targets",
                    False,
                    f"S{scenario.ordinal} carries no targets at all, so next week's "
                    "`targets hit x of n` has no denominator.",
                )
            )
            continue
        out.append(
            Check(
                f"s{scenario.ordinal}_has_a_checkable_target",
                bool(bands),
                f"S{scenario.ordinal}'s targets are all manual, and a scenario with nothing "
                "checkable is not a forecast.",
            )
        )
    return out


def _check_setups(found: Proposal, reg: Any) -> list[Check]:
    out: list[Check] = []
    for setup in found.setups:
        out.append(
            Check(
                f"setup_series_known_{setup.name}",
                reg.get(setup.series_key) is not None,
                f"setup {setup.name!r} names series {setup.series_key!r}, which is not in "
                "config/series.yaml. A setup measured against a series that does not exist is "
                "refused by the worker and never scored.",
            )
        )
        cash = {"spx_cash", "dow_cash", "rut_cash", "nasdaq_cash"}
        futures = {"spx_front", "dow_front", "rut_front"}
        if setup.asset == "equities" and setup.series_key in futures:
            out.append(
                Check(
                    f"setup_quoted_on_cash_{setup.name}",
                    False,
                    f"setup {setup.name!r} is quoted on {setup.series_key}, a front-month future. "
                    "Every entry, invalidation and target in the Setup Ledger is a cash level, "
                    "and R1 exists because the two were once conflated.",
                )
            )
        elif setup.asset == "equities" and setup.series_key not in cash:
            out.append(
                Check(
                    f"setup_quoted_on_an_index_{setup.name}",
                    False,
                    f"setup {setup.name!r} is an equity setup quoted on {setup.series_key}.",
                    severity="warn",
                )
            )
        direction_ok = (
            setup.invalidation < setup.entry_lo
            if setup.target > setup.entry_hi
            else setup.invalidation > setup.entry_hi
        )
        out.append(
            Check(
                f"setup_invalidation_side_{setup.name}",
                direction_ok,
                f"setup {setup.name!r} puts its invalidation on the same side as its target, so "
                "the trade has no risk leg and its planned R:R is not a number.",
            )
        )
    return out


#: Phrases a fresh read has no business using, because each one is about last
#: week's call and the read is taken with that call out of view. A match is a
#: warning, not a refusal: "held" can describe a level as well as a phase.
_PRIOR_WORDS = re.compile(
    r"\blast week(?:'s)? (?:call|read|phase)\b|\bprior (?:call|read|phase)\b"
    r"|\bcarried (?:call|read|forward)\b"
    r"|\b(?:still|remains?|stays?) (?:in )?(?:phase|stage|p)\s*[1-5]\b|\bwas (?:phase|p)\s*[1-5]\b"
    r"|\bupgrade[sd]?\b|\bdowngrade[sd]?\b",
    re.IGNORECASE,
)

#: What a manual holds-while condition may be asserted as when the read is
#: written. There is no "not yet" on something that must stay true.
_HOLDS_STATUSES = frozenset({"met", "failed"})


def _check_reads(reads: tuple[PhaseCallProposal, ...]) -> list[Check]:
    """The rules for a fresh read, which hold before and after the reveal alike."""
    out: list[Check] = []
    for call in reads:
        out.append(
            Check(
                f"{call.asset}_phase_is_a_phase",
                call.phase in PHASES,
                f"the {call.asset} read is phase {call.phase}; both frameworks number the phases "
                "one to five.",
            )
        )
        expected = PHASE_NAMES.get(call.asset, {}).get(call.phase)
        if expected is not None:
            out.append(
                Check(
                    f"{call.asset}_phase_name_matches",
                    call.phase_name.strip().lower() == expected.lower(),
                    f"the {call.asset} read is phase {call.phase} named {call.phase_name!r}, and "
                    f"the framework names phase {call.phase} {expected!r}. One of the two is a "
                    "typo.",
                    severity="warn",
                )
            )
        out.append(
            Check(
                f"{call.asset}_stage_recorded",
                call.stage is not None and bool(call.stage_reason),
                f"the {call.asset} read carries no stage. Where inside Phase {call.phase} this "
                "reading sits, early, mid or late, with a one-line reason, is part of the read.",
            )
        )
        out.append(
            Check(
                f"{call.asset}_read_has_evidence",
                bool(call.evidence),
                f"the {call.asset} read carries no evidence. Three bullets, each a fact from "
                "this week's data that put the read where it is.",
            )
        )
        wording = " ".join((call.reason, call.stage_reason or "", *call.evidence))
        leaked = sorted({match.group(0).lower() for match in _PRIOR_WORDS.finditer(wording)})
        out.append(
            Check(
                f"{call.asset}_read_is_fresh",
                not leaked,
                f"the {call.asset} read talks about a prior call ({', '.join(leaked)}). It is "
                "taken from this week's data and the frameworks alone, with last week's call out "
                "of view, so it has no prior to speak of: say what the data shows, not how it "
                "compares with a call the read is not supposed to know.",
                severity="warn",
            )
        )
        out.extend(_check_conditions(call))
    counted: dict[str, int] = {}
    for one in reads:
        counted[one.asset] = counted.get(one.asset, 0) + 1
    doubled = sorted(asset for asset, count in counted.items() if count > 1)
    out.append(
        Check(
            "one_read_per_book",
            not doubled,
            f"{', '.join(doubled)} carries more than one phase read. Each book is read once a "
            "week, and two proposed reads for one book could never both be accepted.",
        )
    )
    assets = {one.asset for one in reads}
    out.append(
        Check(
            "both_books_carry_a_phase_call",
            assets == {"equities", "crypto"},
            f"phase reads were written for {sorted(assets) or 'nothing'}; equities and crypto "
            "are both read every week, and metals, oil, rates and the dollar carry none.",
            severity="warn",
        )
    )
    return out


def _check_conditions(call: PhaseCallProposal) -> list[Check]:
    """Exactly four things this phase holds on, typed wherever a series exists."""
    out: list[Check] = []
    out.append(
        Check(
            f"{call.asset}_four_conditions",
            len(call.conditions) == 4,
            f"the {call.asset} read carries {len(call.conditions)} conditions; exactly four "
            "are required: the four things that must stay true for this phase to hold.",
        )
    )
    ordinals = sorted(one.ordinal for one in call.conditions)
    out.append(
        Check(
            f"{call.asset}_condition_ordinals",
            ordinals == [1, 2, 3, 4],
            f"the {call.asset} read's condition ordinals are {ordinals}; they run 1 to 4.",
        )
    )
    manual = [one for one in call.conditions if one.kind == "manual"]
    if call.conditions and len(manual) == len(call.conditions):
        out.append(
            Check(
                f"{call.asset}_conditions_are_checkable",
                False,
                f"all four of the {call.asset} read's conditions are manual. A read with nothing "
                "the worker can check can never be flagged as out of date, which is the whole "
                "point of holding it against four conditions between Mondays. Type the ones a "
                "series covers: the 200dma, breadth, HY and IG spreads, VIX and MOVE, the BTC "
                "50-week MA, funding, ETF flows and F&G are all series.",
            )
        )
    for condition in manual:
        out.append(
            Check(
                f"{call.asset}_manual_condition_{condition.ordinal}",
                False,
                f"the {call.asset} read's condition {condition.ordinal} is manual: "
                f"{condition.text!r}. The worker cannot check it, so it never counts toward a "
                "stale read. Keep it manual only if no series in the catalogue measures it.",
                severity="warn",
            )
        )
        status = condition.asserted_status
        out.append(
            Check(
                f"{call.asset}_manual_condition_{condition.ordinal}_asserted",
                status in _HOLDS_STATUSES,
                f"the {call.asset} read's manual condition {condition.ordinal} is asserted as "
                f"{status!r}. A condition the phase holds on either holds (`met`) or has failed "
                "(`failed`); there is no `not yet`, and with no assertion it reads unevaluable.",
            )
        )
    return out


def _check_transitions(calls: tuple[PhaseCallProposal, ...]) -> list[Check]:
    """After the reveal: the one sentence on what changed, and a large jump argued.

    Any phase may follow any phase, so no move is refused for its size. What a
    move of two or more steps around the cycle must carry is its argument, in
    the change note, naming both phases: a jump the note does not explain is
    one a later reader cannot check.
    """
    out: list[Check] = []
    for call in calls:
        note = (call.change_note or "").strip()
        out.append(
            Check(
                f"{call.asset}_change_note",
                bool(note),
                f"the {call.asset} read has no change note. After the reveal, write one "
                f"sentence on what changed since last week ({call.move}).",
            )
        )
        if call.steps is None or call.steps < LARGE_JUMP or call.prior_phase is None:
            continue
        lowered = note.lower()
        names_both = all(
            _names_phase(lowered, call.asset, number)
            for number in (call.prior_phase, call.phase)
        )
        out.append(
            Check(
                f"{call.asset}_large_jump_is_argued",
                names_both and len(note) >= 40,
                f"the {call.asset} read moved {call.steps} steps, Phase {call.prior_phase} to "
                f"Phase {call.phase}. Any phase may follow any phase, and a jump this size is "
                "allowed; the change note has to say why, naming both phases (by number, as "
                "`Phase 1`, or by the framework's name, as "
                f"{PHASE_NAMES.get(call.asset, {}).get(call.phase, 'its name')!r}) and the "
                f"evidence that skipped the step between. It reads: {note or '(empty)'}",
            )
        )
    return out


def _names_phase(lowered: str, asset: str, number: int) -> bool:
    """Whether a change note names one phase, by number or by the framework's name.

    `Phase 1`, `P1` and `stage 1` count, and so does the name the framework
    gives the phase for this book, whole or either half of a two-part name:
    `Early Bull`, `Recovery`, `Wealth Creation`.
    """
    if re.search(rf"\b(?:phase|p|stage)\s*{number}\b", lowered):
        return True
    name = PHASE_NAMES.get(asset, {}).get(number)
    if not name:
        return False
    forms = {name.lower(), *(part.strip().lower() for part in name.split("/"))}
    return any(
        re.search(rf"(?<![\w-]){re.escape(form)}(?![\w-])", lowered) for form in forms if form
    )


def _check_clause_split(scenario_set: ScenarioSetProposal) -> list[Check]:
    out: list[Check] = []
    for scenario in scenario_set.scenarios:
        ordinals = [one.ordinal for one in scenario.clauses]
        out.append(
            Check(
                f"s{scenario.ordinal}_clause_ordinals",
                ordinals == sorted(set(ordinals)) and ordinals == list(range(1, len(ordinals) + 1)),
                f"S{scenario.ordinal}'s clause ordinals are {ordinals}; they run 1..n without a "
                "gap, because joining them in ordinal order with '; ' has to reproduce the "
                "trigger as it was written.",
            )
        )
    return out


def _check_typed_triggers(scenario_set: ScenarioSetProposal) -> list[Check]:
    """Every trigger the daily run can see fire, and a risk-off branch it can see confirm.

    A trigger written only as `manual` clauses is invisible to the 09:30 run:
    `macro_mart.scenario_state` reports it unfired for ever and drift can never
    call it material, so whether it fired is left to whichever model reads it
    next. On 2026-09-27 an amendment re-priced W39 on a manual S2 trigger the
    Desk still showed as unfired. `docs/DECISIONS.md` 2026-09-27 makes this a
    refusal: each scenario carries at least one typed clause, and the risk-off
    branch types its confirming leg (R4) rather than naming it in prose. Manual
    clauses stay allowed beside typed ones.

    An amendment is exempt. It is read against the statement the operator
    accepted, and re-typing that statement mid-week would change what the week
    is graded on; `typed_condition_coverage` still notes the gap.
    """
    if scenario_set.amends is not None:
        return []
    out: list[Check] = []
    for scenario in scenario_set.scenarios:
        typed = [one for one in scenario.clauses if one.kind != "manual"]
        out.append(
            Check(
                f"s{scenario.ordinal}_trigger_is_typed",
                bool(typed),
                f"S{scenario.ordinal}'s trigger has no typed clause: all "
                f"{len(scenario.clauses)} are manual, so the daily run can never see it fire and "
                "drift can never call it material. Type at least one clause as an `expr` or "
                "`predicate`; a manual clause may stay beside it.",
            )
        )
    branch = preflight_rules.risk_off(scenario_set)
    if branch is None:
        out.append(
            Check(
                "risk_off_confirming_leg_typed",
                False,
                f"No scenario bands {preflight_rules.INDEX}, so the risk-off branch cannot be "
                "identified and its confirming leg cannot be checked. Give every scenario an "
                f"{preflight_rules.INDEX} band.",
            )
        )
        return out
    named = preflight_rules.clause_series(branch) & preflight_rules.CONFIRMING
    out.append(
        Check(
            "risk_off_confirming_leg_typed",
            bool(named),
            f"S{branch.ordinal} is the risk-off branch and none of its typed clauses names "
            f"{_or(sorted(preflight_rules.CONFIRMING))}. Its confirming leg has to be an "
            "expression the daily run evaluates, not a sentence.",
        )
    )
    return out


def _or(parts: list[str]) -> str:
    return ", ".join(parts[:-1]) + " or " + parts[-1] if len(parts) > 1 else "".join(parts)


def _check_amendments(found: Proposal) -> list[Check]:
    """An amendment carries the event that permitted it, in one sentence."""
    out: list[Check] = []
    pairs: list[tuple[str, str | None, str | None]] = [
        ("scenario_set", found.scenario_set.amends, found.scenario_set.amend_reason)
    ]
    pairs += [(f"setup {one.name}", one.amends, one.amend_reason) for one in found.setups]
    pairs += [(f"strategy {one.asset}", one.amends, one.amend_reason) for one in found.strategies]
    for label, amends, reason in pairs:
        if amends is None and reason is None:
            continue
        out.append(
            Check(
                f"amendment_carries_its_reason_{label}",
                bool(amends and reason),
                f"{label} sets one of amends and amend_reason without the other. A re-price is "
                "permitted by a material change and the row has to carry which one: a trigger "
                "firing in full, a phase condition flipping, a tail risk going acute, or a "
                "premise moving. Ordinary daily noise is not one of them.",
            )
        )
    return out


def refusals(checks: list[Check]) -> list[Check]:
    return [one for one in checks if not one.ok and one.severity == "refuse"]
