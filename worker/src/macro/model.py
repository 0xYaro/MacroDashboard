"""The normalized vocabulary.

Every fetcher's `normalize` step produces these and nothing else. A vendor field
name must never appear on one of these types: that is the boundary the contract
calls "vendor and scraper field names stop at the worker's normalize step".

Every numeric is a `Decimal`, built with `Decimal(str(v))` at the vendor edge so
no value ever round-trips through a binary float.

Every timestamp is timezone-aware UTC. A bare `date` is a calendar date in the
source's own market calendar and carries no time at all, which is why it is a
`date` and not a midnight `datetime`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from .faults import group_faults

# --------------------------------------------------------------------------
# Observed facts
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Observation:
    """One `macro_core.observation` row.

    `value` null with a `note` is a Tier 4 reading, never a zero. `display` is
    what the web app renders when it has no better idea; it is never parsed back
    into a number by anything in this system.

    A `text` series is the one exception, and it is keyed on `value_kind` rather
    than on the null itself. The five Macro and Fiscal State rows are prose:
    they store `value` null and their content in `display`, and requiring a note
    beside that would make a prose reading look like a failed fetch. Keying the
    exception on the kind is what keeps the two apart: a numeric series with a
    null value still has to say why, because a Tier 4 failure and a macro state
    are otherwise the same shape.

    `asof` is the observation date in the source's own market calendar, never
    the operator's local date. A Friday US close observed at 05:00 Saturday
    Singapore time has a Friday `asof`.

    `observed_at` records the moment the reading described: the close for a
    session series, the run time for a 24-hour series.
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
    #: `numeric` or `text`, from `config/series.yaml`. The only thing it decides
    #: here is whether a null `value` needs a note.
    value_kind: Literal["numeric", "text"] = "numeric"
    #: `extra` keys this row cannot supply but a stored row for the same
    #: `(series, asof)` may carry, and which the upsert keeps rather than
    #: erases. A re-statement of an older session is the case: S5TH serves only
    #: one bar, so the cross-check exists only on the run that saw it.
    keep_extra: tuple[str, ...] = ()
    #: This row is a lower-confidence re-statement, and it does not replace a
    #: stored row of a better tier for the same `(series, asof)`. A cross-check
    #: that could not be read on this run is not evidence against a session a
    #: past run verified.
    defer_to_better_tier: bool = False

    def __post_init__(self) -> None:
        if self.value_kind == "text":
            if self.value is not None:
                raise ValueError(
                    f"{self.series_key} {self.asof}: a text series stores its content in "
                    "display and its value null"
                )
            if not self.display:
                raise ValueError(
                    f"{self.series_key} {self.asof}: a text series with an empty display says "
                    "nothing at all"
                )
        elif self.value is None and not self.note:
            raise ValueError(f"{self.series_key} {self.asof}: a null value needs a note")
        if not 1 <= self.tier <= 4:
            raise ValueError(f"{self.series_key} {self.asof}: tier {self.tier} is outside 1..4")
        if self.observed_at.tzinfo is None:
            raise ValueError(f"{self.series_key} {self.asof}: observed_at must be timezone aware")


@dataclass(frozen=True, slots=True)
class Gap:
    """One `macro_quality.gap` row.

    A `permanent` gap is a point-in-time source that was missed and can never be
    recovered. A gap on a replayable source is not permanent: the next catch-up
    fills it.
    """

    series_key: str
    asof: date
    reason: str
    permanent: bool


@dataclass(frozen=True, slots=True)
class Finding:
    """One `macro_quality.finding` row. One sentence, rendered verbatim."""

    check_key: str
    subject: str
    sentence: str
    severity: int


@dataclass(slots=True)
class FindingScope:
    """The checks one run evaluated, and so the only open findings it may close.

    `macro_quality.finding` has three writers: the 09:30 run (vault, archive,
    Monday grading), the 21:00 rescore (fetch and evaluate) and the authoring
    agent. A finding means "this is still wrong", and only a writer that looked
    again can say it no longer is. A rescore that closed every open finding it
    had not raised itself closed the morning's vault and grading findings, and
    the agent's, every evening.

    A check is covered either whole (`check_keys`), when the run evaluated it
    for every subject it could concern, or one subject at a time (`subjects`),
    when the run looked only at some: a refused write names the table it
    tried, and a setup refused for naming no series names the setup read.
    """

    check_keys: set[str] = field(default_factory=set)
    subjects: set[tuple[str, str]] = field(default_factory=set)

    def add(self, *check_keys: str) -> None:
        self.check_keys.update(check_keys)

    def add_subject(self, check_key: str, subject: str) -> None:
        self.subjects.add((check_key, subject))

    def covers(self, check_key: str, subject: str) -> bool:
        return check_key in self.check_keys or (check_key, subject) in self.subjects


@dataclass(frozen=True, slots=True)
class RawBatch:
    """One `macro_raw.batch` row: the payload exactly as the source served it."""

    source: str
    kind: str
    fetched_at: datetime
    payload: Any


@dataclass(slots=True)
class SourceResult:
    """Everything one source can say about one run.

    A source that raised still returns one of these, with `error` set and
    whatever it managed to normalise before the fault. Partial is better than
    nothing, and the `ingest_run` row records which it was.
    """

    source: str
    #: Every series this source owns, whether or not it delivered one. What
    #: lets the snapshot mark a failed source's rows `failed` rather than
    #: leaving them to look like series that quietly fell behind, and what
    #: `status` measures completeness against.
    series: tuple[str, ...] = ()
    observations: list[Observation] = field(default_factory=list)
    gaps: list[Gap] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    batches: list[RawBatch] = field(default_factory=list)
    #: One sentence per thing that went wrong inside the source while it still
    #: returned something: a symbol that answered 429, an id that 404s. Without
    #: these a partial run can say how much is missing but not why.
    faults: list[str] = field(default_factory=list)
    cursor: str | None = None
    error: str | None = None

    @property
    def declared(self) -> frozenset[str]:
        """Every series this source owes on a complete pass."""
        return frozenset(self.series)

    @property
    def delivered(self) -> frozenset[str]:
        """Every series it produced a row for, Tier 4 nulls included.

        A withheld figure with a note beside it *was* delivered: the source
        reported, and the reason is on the row. Missing means no row at all.
        """
        return frozenset(one.series_key for one in self.observations)

    @property
    def missing(self) -> tuple[str, ...]:
        return tuple(sorted(self.declared - self.delivered))

    @property
    def status(self) -> str:
        """`ok`, `partial` or `failed`, and `ok` means complete.

        `macro_quality.ingest_run` is how a quietly failing job is found, so a
        source that knows it owes N series and delivered fewer has not
        finished. Calling that `ok` is how a run that returned one row of
        nineteen reported success.

        A fault is not the same as a notable event. Findings do not enter here:
        a source that delivered everything it owes is `ok` even when it opened
        a finding about what it delivered.
        """
        if self.declared and not self.delivered:
            return "failed"
        if self.missing or self.error:
            return "partial"
        return "ok"

    @property
    def sentence(self) -> str | None:
        """Why the run was not complete, or `None` when it was.

        Names how many of how many, and what went wrong, because "partial" on
        its own tells the operator to go and look rather than what to look at.
        """
        if self.status == "ok":
            return None
        counted = f"{len(self.delivered)} of {len(self.declared)} series"
        # Grouped, not merely joined. Fourteen symbols refused by one host carry
        # one explanation between them, and repeating it once per symbol is how
        # a single fact filled a paragraph on the screen.
        why = group_faults([*([self.error] if self.error else []), *self.faults])
        if self.status == "failed":
            return f"{self.source} delivered nothing: {why or 'no reason was recorded'}"
        return (
            f"{self.source} delivered {counted} and has not finished"
            + (f": {why}" if why else "")
        )


# --------------------------------------------------------------------------
# The vault feed
# --------------------------------------------------------------------------

CatalystStatus = Literal["tracked", "retired", "rescheduled"]
DatePrecision = Literal["exact", "day", "window", "month", "unknown"]


@dataclass(frozen=True, slots=True)
class Catalyst:
    """One `macro_core.catalyst` row, parsed from `Catalyst Monitor.md`."""

    external_key: str
    name: str
    starts_at: datetime | None
    ends_at: datetime | None
    date_precision: DatePrecision
    source_url: str
    affected: tuple[str, ...]
    status: CatalystStatus
    seen_on: date
    raw_date_text: str
    #: The date is the cron's guess ("late October 2026 expected"), not a
    #: published schedule. A month-only date always is.
    date_estimated: bool = False
    #: From `families.classify`, by name alone: the recurring kind, or `None`
    #: for a one-off, and one of the sixteen categories.
    family: str | None = None
    category: str | None = None


OccurrenceProvenance = Literal["catalyst_monitor", "weekly_journal"]
OccurrenceConfidence = Literal["confirmed", "approximate"]


@dataclass(frozen=True, slots=True)
class CatalystOccurrence:
    """One `macro_core.catalyst_occurrence` row: a catalyst that took place."""

    name: str
    name_key: str
    occurred_on: date
    starts_at: datetime | None
    ends_at: datetime | None
    date_precision: DatePrecision
    family: str | None
    category: str
    source_url: str | None
    affected: tuple[str, ...]
    provenance: OccurrenceProvenance
    provenance_ref: str | None
    confidence: OccurrenceConfidence
    moved_markets: bool | None = None
    market_note: str | None = None
    note: str | None = None
    catalyst_id: str | None = None
    first_tracked: date | None = None
    last_tracked: date | None = None


@dataclass(frozen=True, slots=True)
class WatchlistChange:
    """One `macro_core.watchlist_change` row, parsed from `Market Watchlist.md`."""

    ticker: str
    observed_at: datetime
    change_pct: Decimal | None
    summary: str
    source_url: str


@dataclass(frozen=True, slots=True)
class Holding:
    """One `macro_core.holding` row, parsed from the Watchlist `## Holdings` list."""

    ticker: str
    label: str
    book: Literal["equities", "crypto"]
    active: bool


@dataclass(frozen=True, slots=True)
class TailRisk:
    """One `macro_core.tail_risk` row, parsed from `Blackswan Events.md`."""

    key: str
    name: str
    severity: Literal["acute", "elevated", "watch"]
    summary: str
    priced_in: str
    assets: tuple[str, ...]
    source_url: str
    seen_on: date


@dataclass(slots=True)
class VaultFeed:
    """What one pass over the three cron files produced.

    The three `*_whole` flags say whether each file was read, carried every
    section it feeds, and yielded at least one row. Only a whole file may retire
    or deactivate what it no longer lists: a file that was unreadable, empty or
    missing a section says nothing about the rows it did not deliver, and
    reading its silence as "dropped" is how one bad Catalyst Monitor once
    retired every tracked catalyst.
    """

    catalysts: list[Catalyst] = field(default_factory=list)
    changes: list[WatchlistChange] = field(default_factory=list)
    holdings: list[Holding] = field(default_factory=list)
    tail_risks: list[TailRisk] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    catalysts_whole: bool = False
    holdings_whole: bool = False
    tail_risks_whole: bool = False


# --------------------------------------------------------------------------
# Asserted judgment, as the evaluator reads it back
# --------------------------------------------------------------------------

ConditionKind = Literal["expr", "predicate", "manual"]


class Status(StrEnum):
    """`macro_mart.condition_state.status`.

    `NOT_YET` is a machine-evaluated false. `FAILED` is an operator's judgment
    that a condition has moved decisively away, which no expression can decide,
    so nothing in this worker ever produces it. `UNEVALUABLE` is the load-bearing
    one: a condition with no observation to stand on is never scored as false.
    """

    MET = "met"
    NOT_YET = "not yet"
    FAILED = "failed"
    UNEVALUABLE = "unevaluable"
    VOID = "void"


@dataclass(frozen=True, slots=True)
class PhaseCondition:
    """One `macro_core.phase_condition` row."""

    condition_id: str
    phase_call_id: str
    ordinal: int
    text: str
    kind: ConditionKind
    expr: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class PhaseCall:
    """One active `macro_core.phase_call` row and its exactly four conditions.

    **A fresh call is read from the data alone, every week.** The Monday run
    classifies each asset with the prior call out of its context, so any phase
    may follow any phase. Its four conditions are what must stay true for this
    phase to hold: a condition that no longer holds has failed, and two failed
    conditions mean this week's read may be out of date.

    A `carried` call is one of the fourteen migrated journal rows. Its four
    conditions counted toward the *next* phase, as the journal wrote them, and
    they are scored as written rather than reinterpreted.

    `stage` is where inside the phase the reading sits. It is authored, never
    derived from the conditions.
    """

    phase_call_id: str
    asset: Literal["equities", "crypto"]
    week: str
    phase: int
    phase_name: str
    #: When the operator accepted the call. Every predicate window inside its
    #: four conditions is clipped at this session.
    activated_at: datetime
    conditions: tuple[PhaseCondition, ...]
    classification: Literal["carried", "fresh"] = "carried"
    stage: Literal["early", "mid", "late"] | None = None
    stage_reason: str | None = None

    @property
    def fresh(self) -> bool:
        return self.classification == "fresh"


@dataclass(frozen=True, slots=True)
class TriggerClause:
    """One `macro_core.trigger_clause` row.

    A void clause is kept and counted in the denominator. It is never removed
    and the threshold is never renormalised.
    """

    condition_id: str
    scenario_id: str
    ordinal: int
    text: str
    kind: ConditionKind
    expr: dict[str, Any] | None
    catalyst_id: str | None
    required: bool
    void_at: datetime | None
    void_reason: str | None

    @property
    def is_void(self) -> bool:
        return self.void_at is not None


@dataclass(frozen=True, slots=True)
class ScenarioTarget:
    """One `macro_core.scenario_target` row."""

    target_id: str
    scenario_id: str
    series_key: str | None
    label: str
    kind: Literal["band", "manual"]
    comparator: Literal["between", "gt", "lt"] | None
    lo: Decimal | None
    hi: Decimal | None
    text: str | None


@dataclass(frozen=True, slots=True)
class Scenario:
    """One `macro_core.scenario` row with its clauses and targets."""

    scenario_id: str
    set_id: str
    ordinal: int
    title: str
    probability: int
    positioning: str | None
    clauses: tuple[TriggerClause, ...]
    targets: tuple[ScenarioTarget, ...]


@dataclass(frozen=True, slots=True)
class ScenarioSet:
    """One active `macro_core.scenario_set` row and its three scenarios."""

    set_id: str
    week: str
    #: The as-of every `delta_week_open` on the snapshot is measured against.
    authored_asof: date
    #: When the set was accepted. The clip for every clause's predicate window.
    activated_at: datetime
    scenarios: tuple[Scenario, ...]


@dataclass(frozen=True, slots=True)
class Setup:
    """One active `macro_core.setup` row."""

    setup_id: str
    week: str
    asset: str
    name: str
    #: Null when the setup names no series. Such a setup is not scored, and the
    #: page says so rather than leaving it looking ungraded for no reason.
    series_key: str | None
    entry_lo: Decimal
    entry_hi: Decimal
    invalidation: Decimal
    invalidation_dir: Literal["above", "below"]
    target: Decimal
    rr_planned: Decimal | None
    gate_expr: dict[str, Any] | None
    gate_text: str | None
    sector_focus_id: str | None
    #: The session the judgment became active. Every predicate window inside
    #: this setup is clipped at it, so a setup cannot grade as entered on a
    #: session that preceded it.
    activated_on: date

    @property
    def is_long(self) -> bool:
        """A setup is long when its target sits above its entry band.

        The Setup Ledger has never carried a setup whose target sits inside its
        band, and one would be a malformed row rather than a direction.
        """
        return self.target > self.entry_hi


# --------------------------------------------------------------------------
# Derived state
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SnapshotRow:
    """One `macro_mart.snapshot_row`."""

    series_key: str
    label: str
    table_group: str
    ordinal: int
    value: Decimal | None
    display: str
    unit: str
    value_kind: str
    #: Carried through from `macro_core.series` rather than looked up again, so
    #: the page can say what cadence a delta was taken over without a join.
    cadence: str
    delta_native: Decimal | None
    delta_native_label: str | None
    delta_week_open: Decimal | None
    anchor_asof: date | None
    tier: int | None
    source: str
    asof: date | None
    released: date | None
    next_expected: date | None
    #: The moment the reading described. What says which of the day's two runs
    #: produced it, which is the reason the column exists at all.
    observed_at: datetime | None
    freshness: str
    note: str | None
    extra: dict[str, Any]


@dataclass(frozen=True, slots=True)
class AssertedCondition:
    """One `macro_core.condition_state_asserted` row.

    A condition of kind `manual` has no expression, so its status can never be
    computed. That is true of every migrated week, whose trackers were written
    as prose, and it stays true for any condition the operator judges rather
    than measures. The assertion lives in `macro_core` because `macro_mart` is
    rebuilt, and a judgment that a rebuild destroys is not a record of anything.
    """

    condition_id: str
    owner_kind: Literal["phase", "trigger"]
    status: Status
    current_text: str | None
    asserted_by: Literal["migration", "authoring", "operator"]


@dataclass(frozen=True, slots=True)
class ConditionState:
    """One `macro_mart.condition_state`.

    `source` says where the status came from. An `expr` or `predicate`
    condition is `computed` on every run. A `manual` condition is `asserted`,
    read from `macro_core.condition_state_asserted`, and a manual condition with
    no assertion is `unevaluable` rather than `not yet`.

    `void_at` and `void_reason` are carried from `macro_core.trigger_clause` and
    are null unless the status is `void`. They travel together: a void clause
    with no reason is a clause that vanished without saying why, which is the
    whole thing the premise-moved rule exists to prevent.
    """

    condition_id: str
    owner_kind: Literal["phase", "trigger"]
    owner_id: str
    ordinal: int
    text: str
    kind: ConditionKind
    status: Status
    current_display: str | None
    evidence: dict[str, Any]
    source: Literal["computed", "asserted"]
    void_at: datetime | None = None
    void_reason: str | None = None

    def __post_init__(self) -> None:
        if (self.void_at is None) != (self.void_reason is None):
            raise ValueError(
                "void_at and void_reason are written together or not at all: a clause "
                "voided without a reason is a clause that disappeared silently"
            )


@dataclass(frozen=True, slots=True)
class PhaseScore:
    """One `macro_mart.phase_score`."""

    asset: str
    phase_call_id: str
    phase: int
    phase_name: str
    #: Authored depth within the current phase, or null when none was authored.
    stage: str | None
    met: int
    of: int
    machine_checked: int
    manual: int
    unevaluable: int
    #: Conditions that have failed. On a fresh call these are things the read
    #: holds on that no longer hold.
    failed: int = 0
    #: Two or more of a fresh call's four conditions have failed, so this
    #: week's read may be out of date. Flagged, never acted on: the phase is
    #: read again on Monday. Always false on a carried call, whose conditions
    #: mean something else.
    read_stale: bool = False


@dataclass(frozen=True, slots=True)
class ScenarioState:
    """One `macro_mart.scenario_state`."""

    scenario_id: str
    set_id: str
    ordinal: int
    title: str
    probability: int
    positioning: str | None
    clauses_fired: int
    clauses_total: int
    clauses_void: int
    required_unmet: int
    #: Only the targets of kind `band`: the ones a machine can score.
    bands_holding: int
    bands_total: int
    #: Every target, band and manual alike. `targets_total` is the `n` that
    #: "targets hit x of n" uses at grading, and it is kept apart from
    #: `bands_total` on purpose. In W37's grading block the same grader counted
    #: one scenario's targets including its manual legs and the next scenario's
    #: excluding them, producing two incompatible denominators in one paragraph.
    #: Carrying both makes that unrepresentable; collapsing them would bring it
    #: straight back.
    targets_holding: int
    targets_total: int
    premise_moved: bool


@dataclass(frozen=True, slots=True)
class ScenarioTargetState:
    """One `macro_mart.scenario_target_state`. `holding` null means unevaluable."""

    target_id: str
    scenario_id: str
    series_key: str | None
    label: str
    #: `band` or `manual`. A manual target carries null `lo`, `hi`, `current`
    #: and `holding`, and is presented for judgment rather than scored.
    kind: str
    #: `between`, `gt` or `lt`, as the target was written. Not optional detail:
    #: `lo` null with `hi` set means "below X" under `lt` and "we never wrote
    #: the floor" under `between`, and only one of those is a forecast. The web
    #: app resolves an incomplete `between` to unevaluable and prints it
    #: differently from a genuine `lt`, so a wrong comparator here produces a
    #: confidently wrong reading rather than a blank.
    comparator: str | None
    #: The manual target's own words. A manual target has no numbers at all and
    #: still counts toward `targets_total`.
    text: str | None
    lo: Decimal | None
    hi: Decimal | None
    current: Decimal | None
    holding: bool | None
    #: An unsigned magnitude in the series' own canonical unit: how far the
    #: current value is from the nearest bound it would cross. Never a fraction
    #: and never a percentage, because a band in basis points and a band in
    #: index points would then read the same and mean different things.
    distance_to_breach: Decimal | None


@dataclass(frozen=True, slots=True)
class SetupState:
    """One `macro_mart.setup_state`."""

    setup_id: str
    week: str
    asset: str
    #: The series the levels are quoted on. **Null means the setup names no
    #: series and is deliberately not scored**, which is a real state and not a
    #: missing value: the page says so and shows the gate unevaluable. Nothing
    #: defaults it, because a Nasdaq setup measured against the S&P produces an
    #: entry state, an R-multiple and a ledger row that are all wrong.
    series_key: str | None
    series_label: str | None
    name: str
    entry_lo: Decimal
    entry_hi: Decimal
    invalidation: Decimal
    #: Which side of the invalidation level is the stop. A level of 7,450 with
    #: no stated side is half a trade instruction.
    invalidation_dir: Literal["above", "below"]
    target: Decimal
    rr_planned: Decimal | None
    gate_state: Literal["open", "blocked", "unevaluable"]
    gate_reason: str
    #: The gate as the authoring agent wrote it. A gate reported as `blocked`
    #: with no clause text is a refusal nobody can check.
    gate_text: str | None
    sector_focus_label: str | None
    band_touched_today: bool
    band_touched_close: bool
    entry_state: Literal["not triggered", "open", "stopped", "target"]
    sessions_carried: int
    r_running: Decimal | None


@dataclass(frozen=True, slots=True)
class CalibrationRead:
    """The single `macro_mart.calibration` row, `id = 1`."""

    graded_weeks: int
    base_hit_score: Decimal | None
    base_materialized: int
    mean_brier: Decimal | None
    brier_weeks: int
    base_was_s2: int
    window: int
    sentence: str


@dataclass(frozen=True, slots=True)
class CatalystWindowRow:
    """One `macro_mart.catalyst_window`: the next thirty days."""

    catalyst_id: str
    name: str
    starts_at: datetime | None
    date_precision: str
    affected: tuple[str, ...]
    days_out: int
    referenced_by: tuple[str, ...]
    source_url: str
    ends_at: datetime | None = None
    date_text: str | None = None
    date_estimated: bool = False
    family: str | None = None
    category: str = "other"


@dataclass(frozen=True, slots=True)
class WeekGrade:
    """One `macro_core.week_grade` row."""

    week: str
    base_scenario_id: str | None
    base_scenario_label: str
    base_grade: Literal["Materialized", "Partial", "Not"]
    #: An array, never an object keyed by scenario id:
    #: `[{"id": "S1", "prob": 30, "grade": "Not", "note": "..."}]`. The column
    #: defaults to `'[]'::jsonb` and the web app reads an array, so an object
    #: parses as a malformed row and empties the calibration route.
    other_grades: list[dict[str, Any]]
    eq_call: str
    crypto_call: str
    key_miss: str
    targets_hit: int | None
    targets_n: int | None
    brier: Decimal | None
    premise_moved: bool
    #: Nullable. Nine migrated weeks were graded by archive journals this system
    #: deliberately does not read, and a derived grading date would be a
    #: manufactured fact.
    graded_at: datetime | None


@dataclass(frozen=True, slots=True)
class SetupGrade:
    """One `macro_core.setup_grade` row."""

    setup_id: str
    graded_week: str
    outcome: Literal["not triggered", "triggered → stopped", "triggered → target", "open"]
    r_realised: Decimal | None
    note: str
