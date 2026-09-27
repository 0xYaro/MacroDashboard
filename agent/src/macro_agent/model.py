"""The vocabulary this package speaks.

Deliberately its own, not the worker's. The worker's `model.py` describes rows
that have been *accepted*: a `PhaseCall` carries an `activated_at`, a `Setup`
carries an `activated_on`, and every predicate window is clipped at it. Nothing
this package authors has been activated, by construction, because the operator
has not seen it yet. Reusing those records would mean carrying a required field
that is meaningless until the review gate is passed, and filling it with
something plausible is exactly the class of mistake the review gate exists for.

Two invariants are enforced in `__post_init__` rather than left to the database,
because a refusal that names the series and the day is worth more than a
constraint violation naming a column:

- A null value needs a note. `docs/CONTRACT.md`'s "null, never zero" applies to
  a search agent's Tier 4 return more sharply than anywhere else: an LLM that
  cannot find a figure can always produce one that looks about right.
- A `text` series carries a null value and its sentence in `display`. Without
  that split a prose row and a Tier 4 failure are the same shape, and a failed
  fetch would render as a macro state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

ConditionKind = Literal["expr", "predicate", "manual"]
Stage = Literal["early", "mid", "late"]
Grade = Literal["Materialized", "Partial", "Not"]
Band = Literal["CRITICAL", "HOLD", "WATCH", "AVOID"]


class Refused(Exception):
    """Something a model produced that this package will not write. The message is why."""


# ---------------------------------------------------------------------------
# Mode A
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NarrativeObservation:
    """One `macro_core.observation` row written by a search agent.

    The same columns as the worker's `Observation`, and the same refusals, so
    that a row cannot be distinguished by which of the two wrote it.
    """

    series_key: str
    asof: date
    value: Decimal | None
    display: str
    tier: int
    source: str
    observed_at: datetime
    released: date | None = None
    note: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    #: `numeric` or `text`, from `config/series.yaml`. It is carried because the
    #: null-needs-a-note rule does not apply to a prose row: a text series
    #: stores `value` null and its content in `display` by contract, so the
    #: worker's own `Observation`, whose invariant has no exception for it,
    #: could not express one of the five Macro & Fiscal State rows.
    value_kind: str = "numeric"

    def __post_init__(self) -> None:
        if self.value_kind == "text":
            if self.value is not None:
                raise Refused(
                    f"{self.series_key} {self.asof}: a text series carries no number. A value "
                    "stored here could never be read, because a text series may only appear in "
                    "a manual condition."
                )
        elif self.value is None and not self.note:
            raise Refused(f"{self.series_key} {self.asof}: a null value needs a note")
        if not 1 <= self.tier <= 4:
            raise Refused(f"{self.series_key} {self.asof}: tier {self.tier} is outside 1..4")
        if self.observed_at.tzinfo is None:
            raise Refused(f"{self.series_key} {self.asof}: observed_at must be timezone aware")
        if not self.display:
            raise Refused(f"{self.series_key} {self.asof}: display is what the page renders")


@dataclass(frozen=True, slots=True)
class Event:
    """One `macro_core.event` row: the dated event log behind section 2."""

    occurred_on: date
    kind: Literal["macro", "structure"]
    text: str
    assets: tuple[str, ...] = ()
    surprise: bool = False
    source: str | None = None


# ---------------------------------------------------------------------------
# Mode B: grading
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScenarioGrade:
    """The grade the authoring run assigns to one scenario of the closing week.

    This is the judgment the worker deliberately refuses to make. It is carried
    as its own record so that the thing crossing the seam is a grade and a
    reason, and never a probability the run could have quietly adjusted after
    the tape was known.

    `note` is the one-line grading reason, written at grading time looking
    backward at what happened: in the journal it is the sentence trailing
    `targets hit 5 of 11`. It is carried straight through to
    `macro_core.week_grade.other_grades` and nothing derives it.

    It is **not** the scenario's `positioning`, which is forward-looking posture
    authored a week earlier. Substituting one for the other puts last week's
    intentions in the column that is supposed to explain last week's outcome,
    and it reads plausibly enough that nobody would catch it.

    Where the authoring run has no note, the note is empty. A generated sentence
    that reads like a human one is worse than a blank, because the ledger's
    whole value is that its sentences were written by someone who thought about
    the week, and twelve weeks of them are what R1 to R12 were distilled from.
    """

    scenario_id: str
    grade: Grade
    #: The grading reason, or empty when the authoring run supplied none.
    note: str = ""


@dataclass(frozen=True, slots=True)
class ProposedGrade:
    """One `macro_core.week_grade` row, ready to write.

    `targets_hit`, `targets_n` and `brier` are not authored. They are computed:
    the first two by counting `macro_core.scenario_target` rows of both kinds,
    the third by the worker's own `calibration.brier`.
    """

    week: str
    base_scenario_id: str | None
    base_scenario_label: str
    base_grade: Grade
    #: An array, never an object keyed by scenario id:
    #: `[{"id": "S2", "prob": 30, "grade": "Partial", "note": "..."}]`, in
    #: scenario order, for every scenario except the base case. The column
    #: defaults to `'[]'::jsonb` and the web app reads an array, so an object
    #: parses as a malformed row and empties the calibration route entirely.
    #: The base case is excluded because its grading reasoning is `key_miss`.
    other_grades: list[dict[str, Any]]
    eq_call: str
    crypto_call: str
    key_miss: str
    targets_hit: int | None
    targets_n: int | None
    brier: Decimal | None
    premise_moved: bool
    graded_at: datetime | None


# ---------------------------------------------------------------------------
# Mode B: authoring
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Condition:
    """One phase condition or trigger-style condition, as authored.

    `kind` is `expr` or `predicate` whenever the sentence permits, and `manual`
    only when it genuinely needs judgment. Every migrated week's conditions are
    `manual` with a null expression because the journals were prose; a week
    authored here that carried the same proportion would mean the daily
    evaluation had nothing to do.
    """

    ordinal: int
    text: str
    kind: ConditionKind
    expr: dict[str, Any] | None
    #: The as-written status, for a `manual` condition. Written to
    #: `macro_core.condition_state_asserted`, because a manual condition can
    #: never be computed and `macro_mart` is rebuilt on every run.
    asserted_status: str | None = None
    asserted_text: str | None = None

    def __post_init__(self) -> None:
        if self.kind == "manual" and self.expr is not None:
            raise Refused(f"condition {self.ordinal}: a manual condition carries no expression")
        if self.kind != "manual" and self.expr is None:
            raise Refused(
                f"condition {self.ordinal}: kind {self.kind!r} needs an expression. "
                "If the sentence cannot be typed, the kind is manual and it says so."
            )


#: The five phases, numbered one to five in both frameworks on purpose, so a
#: phase outside that range is a typo.
PHASES = range(1, 6)


def phase_distance(a: int, b: int) -> int:
    """Steps between two phases around the cycle: 5 to 1 is one step, 3 to 1 two.

    The frameworks draw the five phases as a cycle, and a Lull turning into an
    Early Bull is the cycle's next step, not a four-phase jump.
    """
    gap = abs(a - b) % len(PHASES)
    return min(gap, len(PHASES) - gap)


#: A move of this many steps or more is a large jump, and the change note has to
#: argue it.
LARGE_JUMP = 2

#: Each framework's own name for each phase, as `13 - Crypto Market Cycle
#: Framework` and `14 - Equities Market Cycle Framework` write them. A read
#: whose name and number disagree is a read where one of the two is a typo.
PHASE_NAMES: dict[str, dict[int, str]] = {
    "equities": {
        1: "Recovery / Early Bull",
        2: "Expansion / Momentum",
        3: "Late Cycle / Distribution",
        4: "Contraction / Bear",
        5: "Base-Building / Lull",
    },
    "crypto": {
        1: "Early Bull",
        2: "Wealth Creation",
        3: "Wealth Distribution",
        4: "Wealth Destruction / Down Chop",
        5: "Lull",
    },
}


@dataclass(frozen=True, slots=True)
class PhaseCallProposal:
    """One fresh phase read and its exactly four conditions.

    **The read is taken fresh every week, from the data alone.** The Monday run
    classifies each asset from the frameworks' definitions and this week's data
    with last week's call out of its context, and writes the read with
    `author classify` before that call is shown to it. Any phase may follow any
    phase: the market can go from 3 to 4 and back to 2, and a read that started
    from last week's and asked whether to move it one step would anchor on
    itself, which is what 2026-W32's key miss recorded ("the phase call capped
    the scenario").

    **The four conditions are what must stay true for this phase to hold.**
    They are the read's own falsifiable evidence, typed wherever a series
    exists, and the worker scores them twice a day. Two failed means this
    week's read may be out of date; nothing reclassifies on it.

    `stage` is where inside the phase the reading sits: early, mid or late.

    `prior_phase` and `prior_week` are filled in by `author classify` from the
    call that was active when the read was written, never by the read. The
    `change_note` is the one sentence written after the reveal: what changed.
    """

    asset: Literal["equities", "crypto"]
    week: str
    phase: int
    phase_name: str
    reason: str
    evidence: tuple[str, ...]
    falsifier: str
    conditions: tuple[Condition, ...]
    authored_at: datetime
    stage: Stage | None = None
    stage_reason: str | None = None
    #: Filled by the command from the active call, never by the read.
    prior_phase: int | None = None
    prior_week: str | None = None
    #: Written after the reveal.
    change_note: str | None = None
    #: When the read was written. Set by `author classify`.
    classified_at: datetime | None = None
    #: The stored row, once the read has been written.
    call_id: str | None = None

    @property
    def direction(self) -> Literal["up", "unchanged", "down"]:
        """The phase number's move from last week. Any phase may follow any phase."""
        if self.prior_phase is None or self.prior_phase == self.phase:
            return "unchanged"
        return "up" if self.phase > self.prior_phase else "down"

    @property
    def steps(self) -> int | None:
        """Steps moved around the cycle since last week, or None with no prior call."""
        if self.prior_phase is None:
            return None
        return phase_distance(self.prior_phase, self.phase)

    @property
    def move(self) -> str:
        """The transition as the Desk prints it: `P3 to P2`, or `same as last week`."""
        if self.prior_phase is None:
            return "no prior call"
        if self.prior_phase == self.phase:
            return "same as last week"
        return f"P{self.prior_phase} to P{self.phase}"


@dataclass(frozen=True, slots=True)
class PhaseNote:
    """What the proposal says about a phase after the reveal: one sentence.

    The phase itself was fixed at `author classify`, before last week's call
    was shown, so the only thing the proposal may add is what changed.
    """

    asset: Literal["equities", "crypto"]
    change_note: str


@dataclass(frozen=True, slots=True)
class TriggerClauseProposal:
    """One clause of a scenario's trigger.

    A scenario's trigger is stored only as its clauses, so the split has to be
    lossless: joining a scenario's clauses in ordinal order with `"; "` must
    reproduce the trigger as written.

    `catalyst_id` is what makes premise-moved detection work. A clause that
    names a dated catalyst and does not bind it is a clause the Catalyst
    Monitor can never void, so the scenario keeps standing on an event that has
    been retired and nothing says so.
    """

    ordinal: int
    text: str
    kind: ConditionKind
    expr: dict[str, Any] | None
    required: bool
    catalyst_id: str | None = None
    asserted_status: str | None = None
    asserted_text: str | None = None

    def __post_init__(self) -> None:
        if self.kind == "manual" and self.expr is not None:
            raise Refused(f"clause {self.ordinal}: a manual clause carries no expression")
        if self.kind != "manual" and self.expr is None:
            raise Refused(f"clause {self.ordinal}: kind {self.kind!r} needs an expression")


@dataclass(frozen=True, slots=True)
class TargetProposal:
    """One `macro_core.scenario_target`.

    `kind` and `series_key` are not optional decoration. `targets_n` counts
    every row, band and manual alike, and `targets_hit` counts only the bands
    that were measured and held, so a band that names no series is a target
    nothing can ever score and a set of only manual targets is a forecast with
    nothing checkable in it.

    Note the schema's asymmetry, which is deliberate and worth reading twice:
    a `gt` band carries its bound in `lo` with `hi` null, and an `lt` band
    carries its bound in `hi` with `lo` null.
    """

    label: str
    kind: Literal["band", "manual"]
    series_key: str | None = None
    comparator: Literal["between", "gt", "lt"] | None = None
    lo: Decimal | None = None
    hi: Decimal | None = None
    text: str | None = None

    def __post_init__(self) -> None:
        if self.kind == "manual":
            if self.series_key or self.comparator or self.lo is not None or self.hi is not None:
                raise Refused(f"target {self.label!r}: a manual target carries no band")
            return
        if not self.series_key:
            raise Refused(
                f"target {self.label!r}: a band must name the series it is measured against. "
                "A band with no series is a number nothing can ever score."
            )
        if self.comparator == "between" and (self.lo is None or self.hi is None):
            raise Refused(f"target {self.label!r}: a between band needs both bounds")
        if self.comparator == "gt" and (self.lo is None or self.hi is not None):
            raise Refused(f"target {self.label!r}: a gt band carries its bound in lo, hi null")
        if self.comparator == "lt" and (self.hi is None or self.lo is not None):
            raise Refused(f"target {self.label!r}: an lt band carries its bound in hi, lo null")


@dataclass(frozen=True, slots=True)
class ScenarioProposal:
    ordinal: int
    title: str
    probability: int
    positioning: str | None
    clauses: tuple[TriggerClauseProposal, ...]
    targets: tuple[TargetProposal, ...]


@dataclass(frozen=True, slots=True)
class ScenarioSetProposal:
    """One `macro_core.scenario_set` and everything under it, written whole."""

    week: str
    synthesis: str
    probability_check: str
    swing_variables: list[Any]
    rules_waived: tuple[int, ...]
    scenarios: tuple[ScenarioProposal, ...]
    authored_at: datetime
    amends: str | None = None
    amend_reason: str | None = None


@dataclass(frozen=True, slots=True)
class SectorFocusProposal:
    band: Band
    text: str


@dataclass(frozen=True, slots=True)
class HoldingsCheckProposal:
    ticker: str
    action: Literal["keep", "trim", "add", "exit"]
    text: str | None
    #: The sector-focus band this holding sits under, resolved to an id at write
    #: time. Null when no row supports it, which is itself a statement.
    band: Band | None = None


@dataclass(frozen=True, slots=True)
class StrategyProposal:
    week: str
    asset: Literal["equities", "crypto"]
    stance: str
    posture: str
    exposure_lo: Decimal | None
    exposure_hi: Decimal | None
    exposure_operated_lo: Decimal | None
    exposure_operated_hi: Decimal | None
    sector_focus: tuple[SectorFocusProposal, ...]
    holdings_check: tuple[HoldingsCheckProposal, ...]
    authored_at: datetime
    amends: str | None = None
    amend_reason: str | None = None


@dataclass(frozen=True, slots=True)
class SetupProposal:
    """One `macro_core.setup`.

    `series_key` is required and has no default. A setup that does not say what
    it is measured against is refused by the worker: no `setup_state` row, no
    ledger row, and a `setup_series_missing` finding. Equity setups are quoted
    on the cash index and crypto setups on BTC or ETH, so `asset` does not
    determine it, and a Nasdaq setup scored against the S&P would produce an
    entry state, an R multiple and a ledger row that are all wrong and none of
    them visibly so.
    """

    week: str
    asset: Literal["equities", "crypto"]
    name: str
    style: str | None
    series_key: str
    entry_lo: Decimal
    entry_hi: Decimal
    invalidation: Decimal
    invalidation_dir: Literal["above", "below"]
    target: Decimal
    rr_planned: Decimal | None
    gate_expr: dict[str, Any] | None
    gate_text: str | None
    band: Band | None
    rationale: str | None
    authored_at: datetime
    amends: str | None = None
    amend_reason: str | None = None

    def __post_init__(self) -> None:
        if self.entry_lo > self.entry_hi:
            raise Refused(f"setup {self.name!r}: entry_lo is above entry_hi")
        if not self.series_key:
            raise Refused(
                f"setup {self.name!r}: a setup must name the series its levels are measured on"
            )


@dataclass(frozen=True, slots=True)
class Proposal:
    """One week's authored judgment, as one unit.

    It is validated as one unit and written as four transactions, one per
    subject, because the cardinality rules are per subject: a scenario set is
    three scenarios summing to 100 and a phase call is exactly four conditions,
    and neither says anything about the other.
    """

    week: str
    scenario_set: ScenarioSetProposal
    strategies: tuple[StrategyProposal, ...]
    setups: tuple[SetupProposal, ...]
    #: The week's fresh reads, loaded from what `author classify` wrote and
    #: merged with `phase_notes`. Empty on an amendment, which never touches a
    #: phase: the phase is read once a week.
    phase_calls: tuple[PhaseCallProposal, ...] = ()
    #: The change notes the proposal wrote, one per asset.
    phase_notes: tuple[PhaseNote, ...] = ()


@dataclass(frozen=True, slots=True)
class Check:
    """One line of the R1 to R12 preflight, or of proposal validation."""

    key: str
    ok: bool
    sentence: str
    #: `refuse` stops the write. `warn` is surfaced and the operator decides.
    severity: Literal["refuse", "warn", "note"] = "refuse"

    @property
    def marker(self) -> str:
        if self.ok:
            return "pass"
        return "REFUSE" if self.severity == "refuse" else self.severity.upper()
