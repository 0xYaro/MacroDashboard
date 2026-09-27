/**
 * The `macro_mart` and `macro_quality` rows this app reads, and the validator
 * for each.
 *
 * One module per `docs/CONTRACT.md`, which is the binding seam between the SQL,
 * the worker and this app. Column names and nullability here mirror that file;
 * a mismatch is a contract break and must be raised against `docs/CONTRACT.md`
 * rather than papered over with a coercion.
 *
 * Every `numeric` becomes `number | null` at this boundary and nowhere later.
 * The app then holds numbers, so no component has to remember that a
 * probability arrived as the string `"45"`, and no component can accidentally
 * add two of them as strings.
 *
 * ## Relations the contract does not yet define
 *
 * Seven of the relations below are marked **not in CONTRACT.md**. Each one is
 * required by a route the contract mandates and is unreachable from the tables
 * it lists, because the source data sits in `macro_core`, which is not on the
 * Data API. They are named here so the database has a precise list to build
 * against, and every one of them is read through `optionalTable` in
 * `lib/data.ts`: a project without them degrades that one section to a named
 * empty state rather than failing the route. They are enumerated in full in
 * `web/README.md`.
 */

import {
  asRecord,
  bad,
  boolean,
  booleanOrNull,
  calendarDate,
  calendarDateOrNull,
  fields,
  instant,
  instantOrNull,
  integer,
  integerArray,
  integerOrNull,
  jsonBlob,
  jsonObject,
  jsonRows,
  numeric,
  ok,
  oneOf,
  oneOfOrNull,
  text,
  textArray,
  textOrNull,
  weekLabel,
  weekLabelOrNull,
  type Checked,
  type RowValidator,
} from "./validate";

/* ================================================================ enums == */

/**
 * The seven freshness states a snapshot row may carry.
 *
 * `due` is the one that earns its place. A weekly series sitting between its
 * Tuesday as-of and its Friday release is *current*: it is not late, nothing
 * failed, and the reading on screen is the correct one. Rendering it as
 * `overdue` or as Accounting's `stale` would put 47 series with six natural
 * cadences permanently in an amber that means nothing, and an amber that always
 * shows is an amber nobody reads.
 *
 * `overdue` is the genuine version of that complaint: `next_expected` has
 * passed and no observation arrived.
 */
export const FRESHNESS = [
  "fresh",
  "due",
  "overdue",
  "gap",
  "asserted",
  "not_connected",
  "failed",
] as const;
export type Freshness = (typeof FRESHNESS)[number];

/** The five states a stated condition may be in. */
export const CONDITION_STATUS = [
  "met",
  "not yet",
  "failed",
  "unevaluable",
  "void",
] as const;
export type ConditionStatus = (typeof CONDITION_STATUS)[number];

/** How a condition is evaluated: by expression, by predicate, or by judgment. */
export const CONDITION_KIND = ["expr", "predicate", "manual"] as const;

/** A series' declared cadence, from `config/series.yaml`. */
export const CADENCE = [
  "session",
  "daily",
  "weekly",
  "monthly",
  "event",
] as const;
export type Cadence = (typeof CADENCE)[number];

/** Where inside a phase the reading sits. Not the transition tracker. */
export const STAGES = ["early", "mid", "late"] as const;
export type Stage = (typeof STAGES)[number];

export const ASSETS = ["equities", "crypto"] as const;
export type Asset = (typeof ASSETS)[number];

export const GRADES = ["Materialized", "Partial", "Not"] as const;
export type Grade = (typeof GRADES)[number];

export const SETUP_OUTCOMES = [
  "not triggered",
  "triggered → stopped",
  "triggered → target",
  "open",
] as const;

export const GATE_STATES = ["open", "blocked", "unevaluable"] as const;
export type GateState = (typeof GATE_STATES)[number];

export const ENTRY_STATES = [
  "not triggered",
  "open",
  "stopped",
  "target",
] as const;

export const LIFECYCLE = ["proposed", "active", "superseded"] as const;

/* ================================================ macro_mart.snapshot_row */

export interface SnapshotRow {
  seriesId: string;
  key: string;
  label: string;
  tableGroup: string;
  ordinal: number;
  /** Null is a reading that could not be measured. Never zero, never NaN. */
  value: number | null;
  /** The worker's own formatting of the value, with its unit. */
  display: string | null;
  unit: string | null;
  valueKind: "numeric" | "text";
  /** The change over this series' own cadence, never a one-day move on a weekly. */
  deltaNative: number | null;
  /** What that change is a change over: `1d`, `WoW`, `MoM`. */
  deltaNativeLabel: string | null;
  /** Measured against `anchorAsof`, one anchor for the whole snapshot. */
  deltaWeekOpen: number | null;
  anchorAsof: string | null;
  tier: number | null;
  source: string | null;
  asof: string | null;
  released: string | null;
  nextExpected: string | null;
  cadence: Cadence | null;
  /**
   * Which of the two daily runs this reading came from.
   *
   * Added in migration 0011. For a 24-hour series it is the run time and for a
   * session series it is the close, so it is what tells a 09:30 reading from
   * the 21:00 rescore of the same `asof`.
   */
  observedAt: string | null;
  freshness: Freshness;
  note: string | null;
  extra: Readonly<Record<string, unknown>>;
  updatedAt: string;
}

export const snapshotRow: RowValidator<SnapshotRow> = (row) => {
  const record = asRecord(row);
  if (!record.ok) return record;
  const r = record.value;
  return fields<SnapshotRow>({
    seriesId: text(r, "series_id"),
    key: text(r, "key"),
    label: text(r, "label"),
    tableGroup: text(r, "table_group"),
    ordinal: integer(r, "ordinal"),
    value: numeric(r, "value"),
    display: textOrNull(r, "display"),
    unit: textOrNull(r, "unit"),
    valueKind: oneOf(r, "value_kind", ["numeric", "text"]),
    deltaNative: numeric(r, "delta_native"),
    deltaNativeLabel: textOrNull(r, "delta_native_label"),
    deltaWeekOpen: numeric(r, "delta_week_open"),
    anchorAsof: calendarDateOrNull(r, "anchor_asof"),
    tier: integerOrNull(r, "tier"),
    source: textOrNull(r, "source"),
    asof: calendarDateOrNull(r, "asof"),
    released: calendarDateOrNull(r, "released"),
    nextExpected: calendarDateOrNull(r, "next_expected"),
    // Both added in migration 0011, both nullable there, so both are read
    // as optional: a project on the 0006 shape reads them null and the row
    // prints its delta label alone rather than the snapshot failing over a
    // subtitle.
    cadence: oneOfOrNull(r, "cadence", CADENCE),
    observedAt: instantOrNull(r, "observed_at"),
    freshness: oneOf(r, "freshness", FRESHNESS),
    note: textOrNull(r, "note"),
    extra: jsonObject(r, "extra"),
    updatedAt: instant(r, "updated_at"),
  });
};

/**
 * A text series is a different kind of claim from a failed fetch.
 *
 * Both are a null `value` with a string beside them. `value_kind` is what tells
 * them apart, per `docs/CONTRACT.md`, and this is the predicate every renderer
 * asks rather than testing `value === null` itself. Without it the five Macro
 * and Fiscal State rows and a Tier 4 failure draw identically, and a failed
 * fetch would render as a macro state.
 */
export function isProse(row: SnapshotRow): boolean {
  return row.valueKind === "text";
}

/* ============================================== macro_mart.condition_state */

export interface ConditionState {
  conditionId: string;
  ownerKind: "phase" | "trigger";
  ownerId: string;
  ordinal: number;
  text: string;
  kind: (typeof CONDITION_KIND)[number];
  status: ConditionStatus;
  /** What the condition is currently reading, as the worker formatted it. */
  currentDisplay: string | null;
  evidence: Readonly<Record<string, unknown>>;
  evaluatedAt: string;
  /**
   * Why the clause was voided, and when.
   *
   * `macro_core.trigger_clause` carries `void_at` and `void_reason`, and the
   * contract says a void clause is kept and counted in the denominator. A void
   * clause with no reason on screen is a struck-through line the reader cannot
   * act on, so the reason is projected onto the state row. Optional, because
   * a phase condition is never void.
   */
  voidReason: string | null;
  voidAt: string | null;
  /**
   * `computed` for an `expr` or `predicate` condition, `asserted` for a manual
   * one whose status was read from `macro_core.condition_state_asserted`.
   *
   * Added in migration 0011. Without it an asserted judgment and a machine
   * evaluation are the same row shape, and the coverage line cannot tell the
   * reader which of the four conditions a human decided.
   */
  source: "computed" | "asserted" | null;
}

export const conditionState: RowValidator<ConditionState> = (row) => {
  const record = asRecord(row);
  if (!record.ok) return record;
  const r = record.value;
  return fields<ConditionState>({
    conditionId: text(r, "condition_id"),
    ownerKind: oneOf(r, "owner_kind", ["phase", "trigger"]),
    ownerId: text(r, "owner_id"),
    ordinal: integer(r, "ordinal"),
    text: text(r, "text"),
    kind: oneOf(r, "kind", CONDITION_KIND),
    status: oneOf(r, "status", CONDITION_STATUS),
    currentDisplay: textOrNull(r, "current_display"),
    evidence: jsonObject(r, "evidence"),
    evaluatedAt: instant(r, "evaluated_at"),
    // Added in migration 0013, which the contract changed to match: a clause
    // that cannot say which catalyst moved, and when, is a claim the operator
    // has to take on trust, and the whole reason the threshold is not
    // renormalised is that the void clause stays visible and checkable.
    voidReason: textOrNull(r, "void_reason"),
    voidAt: instantOrNull(r, "void_at"),
    source: oneOfOrNull(r, "source", ["computed", "asserted"]),
  });
};

/* ================================================= macro_mart.phase_score */

/**
 * The arithmetic over one phase call's four conditions, as the worker scored it.
 *
 * A fresh call (every call classified since 2026-09-28) is read from the data
 * each Monday with last week's call out of view, and its four conditions are
 * what must stay true for the phase to hold. There is no upgrade and no
 * 3-of-4 rule: any phase may follow any phase, and the next Monday's read is a
 * new read. A condition that no longer holds has `failed`; there is no `not
 * yet` on a fresh call.
 *
 * A carried call is one of the fourteen migrated journal calls. Its four
 * conditions were written as signs of the next phase, and they are shown as
 * that rather than relabelled.
 */
export interface PhaseScore {
  asset: Asset;
  phaseCallId: string;
  phase: number;
  phaseName: string;
  /** Where inside the phase the reading sits: `early`, `mid` or `late`. */
  stage: Stage | null;
  met: number;
  of: number;
  machineChecked: number;
  manual: number;
  unevaluable: number;
  /** How many of the four have failed. */
  failed: number;
  /**
   * Two or more of a fresh call's four conditions have failed, so this week's
   * read may be out of date. The worker flags it and never acts on it: the
   * phase is read again on Monday.
   */
  readStale: boolean;
  evaluatedAt: string;
}

export const phaseScore: RowValidator<PhaseScore> = (row) => {
  const record = asRecord(row);
  if (!record.ok) return record;
  const r = record.value;
  return fields<PhaseScore>({
    asset: oneOf(r, "asset", ASSETS),
    phaseCallId: text(r, "phase_call_id"),
    phase: integer(r, "phase"),
    phaseName: text(r, "phase_name"),
    stage: oneOfOrNull(r, "stage", STAGES),
    met: integer(r, "met"),
    of: integer(r, "of"),
    machineChecked: integer(r, "machine_checked"),
    manual: integer(r, "manual"),
    unevaluable: integer(r, "unevaluable"),
    failed: integer(r, "failed"),
    readStale: boolean(r, "read_stale"),
    evaluatedAt: instant(r, "evaluated_at"),
  });
};

/**
 * Where the two mart relations disagree about which call is standing.
 *
 * `macro_mart.phase_score` is rebuilt by the worker and
 * `macro_mart.phase_call_detail` is projected from the authored row. Between
 * an accept on the Desk and the rescore that follows it, the score can still
 * belong to the call that was just replaced. Said plainly rather than hidden,
 * because the four chips would otherwise be read as the new call's.
 */
export function martDisagreement(
  score: PhaseScore,
  call: PhaseCallDetail | undefined,
): string | null {
  if (call === undefined) return null;
  if (score.phaseCallId === call.phaseCallId && score.phase === call.phase) {
    return null;
  }
  return `The scores below belong to the previous call (Phase ${score.phase}). They refresh on the next worker run.`;
}

/* ======================================== macro_mart.phase_call_detail (+) */

export const PHASE_CLASSIFICATIONS = ["carried", "fresh"] as const;
export type PhaseClassification = (typeof PHASE_CLASSIFICATIONS)[number];

/**
 * The authored phase call behind a score. **Not in CONTRACT.md.**
 *
 * `macro_mart.phase_score` carries the arithmetic and none of the judgment.
 * The reason, the evidence, the falsifier, last week's phase and how long the
 * current phase has run are all read here, from `macro_core.phase_call`
 * through a view, because a falsifier the operator cannot read cannot be
 * checked.
 */
export interface PhaseCallDetail {
  phaseCallId: string;
  asset: Asset;
  week: string;
  phase: number;
  phaseName: string;
  /** Last week's phase, filled in by the command, never by the read itself. */
  priorPhase: number | null;
  direction: "up" | "unchanged" | "down";
  reason: string;
  evidence: readonly string[];
  falsifier: string;
  /** Where inside the phase the reading sits. */
  stage: Stage | null;
  stageReason: string | null;
  /**
   * When this call came into force. Null while it is still only proposed.
   *
   * The view filters to `status = 'active'`, so every row that reaches here is
   * the standing call for its asset.
   */
  activatedAt: string | null;
  /** `fresh` is read weekly from the data; `carried` is a migrated journal call. */
  classification: PhaseClassification;
  /** When the fresh read was written. Null on a carried call. */
  classifiedAt: string | null;
  /** One sentence written after the reveal: what changed since last week. */
  changeNote: string | null;
  /** The week of the call before this one, e.g. `2026-W38`. */
  priorWeek: string | null;
  /** The first week of the current unbroken run of this phase. */
  sinceWeek: string | null;
  /** Calendar weeks from `sinceWeek` to this call's week, inclusive. */
  weeksRunning: number | null;
}

export const phaseCallDetail: RowValidator<PhaseCallDetail> = (row) => {
  const record = asRecord(row);
  if (!record.ok) return record;
  const r = record.value;
  return fields<PhaseCallDetail>({
    phaseCallId: text(r, "phase_call_id"),
    asset: oneOf(r, "asset", ASSETS),
    week: weekLabel(r, "week"),
    phase: integer(r, "phase"),
    phaseName: text(r, "phase_name"),
    priorPhase: integerOrNull(r, "prior_phase"),
    direction: oneOf(r, "direction", ["up", "unchanged", "down"]),
    reason: text(r, "reason"),
    evidence: textArray(r, "evidence"),
    falsifier: text(r, "falsifier"),
    stage: oneOfOrNull(r, "stage", STAGES),
    stageReason: textOrNull(r, "stage_reason"),
    activatedAt: instantOrNull(r, "activated_at"),
    classification: oneOf(r, "classification", PHASE_CLASSIFICATIONS),
    classifiedAt: instantOrNull(r, "classified_at"),
    changeNote: textOrNull(r, "change_note"),
    priorWeek: weekLabelOrNull(r, "prior_week"),
    sinceWeek: weekLabelOrNull(r, "since_week"),
    weeksRunning: integerOrNull(r, "weeks_running"),
  });
};

/**
 * The Monday of an ISO week, as a UTC midnight. Null for anything that is not
 * `YYYY-Www`.
 */
export function isoWeekMonday(week: string): Date | null {
  const match = /^(\d{4})-W(\d{2})$/.exec(week.trim());
  if (match === null) return null;
  const year = Number(match[1]);
  const number = Number(match[2]);
  // 4 January is always in ISO week 1.
  const jan4 = Date.UTC(year, 0, 4);
  const jan4Day = (new Date(jan4).getUTCDay() + 6) % 7; // Monday = 0
  return new Date(jan4 - jan4Day * 86_400_000 + (number - 1) * 7 * 86_400_000);
}

/**
 * When the phase is next read: the Monday of the ISO week after the call's.
 *
 * The weekly run classifies on Monday and falls back to Tuesday if Monday's
 * run fails, so the read is only overdue once that Tuesday has passed.
 */
export function nextPhaseRead(
  call: PhaseCallDetail,
  now: Date,
): { date: Date; overdue: boolean } | null {
  const monday = isoWeekMonday(call.week);
  if (monday === null) return null;
  const next = new Date(monday.getTime() + 7 * 86_400_000);
  const fallbackEnds = next.getTime() + 2 * 86_400_000;
  return { date: next, overdue: now.getTime() >= fallbackEnds };
}

/**
 * How this week's phase stands to last week's, in plain words.
 *
 * `Same as last week, 8 weeks running` or `Was P3 last week`. Null when there
 * is no prior call on record.
 */
export function phaseMove(call: PhaseCallDetail): string | null {
  if (call.priorPhase === null) return null;
  if (call.priorPhase === call.phase) {
    const weeks = call.weeksRunning;
    return weeks !== null && weeks > 1
      ? `Same as last week, ${weeks} weeks running`
      : "Same as last week";
  }
  return `Was P${call.priorPhase} last week, now P${call.phase}`;
}

/* ====================================== macro_mart.scenario_set_state (+) */

/**
 * The active scenario set's own metadata. **Not in CONTRACT.md.**
 *
 * The synthesis, the probability check, the swing variables and the waived
 * rules all live on `macro_core.scenario_set`. The probability check is the
 * paragraph that walks R1 to R12 against the table, and a scenario set rendered
 * without it is three numbers with no argument attached.
 */
export interface ScenarioSetState {
  setId: string;
  week: string;
  /**
   * Nullable on `macro_core.scenario_set`, so nullable here.
   *
   * A set is written whole in one transaction, but the column permits a null
   * and a proposal that has not been narrated yet would carry one. Requiring
   * it would drop the whole set over a missing paragraph, and the three
   * scenarios are the forecast; the synthesis is the case for it.
   */
  synthesis: string | null;
  probabilityCheck: string | null;
  /**
   * Each swing variable, with its cool and hot branch, as the worker stores it.
   *
   * Opaque here and read by `readSwingVariables`, because the column is a bare
   * `jsonb` whose top level is an array in every stored set.
   */
  swingVariables: unknown;
  /** Rule numbers consciously waived, per `scenario-rules.md`. */
  rulesWaived: readonly number[];
  /**
   * When this set came into force. The view filters to `status = 'active'` and
   * projects no `status`, so every row that reaches here is the standing set.
   */
  activatedAt: string | null;
}

export const scenarioSetState: RowValidator<ScenarioSetState> = (row) => {
  const record = asRecord(row);
  if (!record.ok) return record;
  const r = record.value;
  return fields<ScenarioSetState>({
    setId: text(r, "set_id"),
    week: weekLabel(r, "week"),
    synthesis: textOrNull(r, "synthesis"),
    probabilityCheck: textOrNull(r, "probability_check"),
    swingVariables: jsonBlob(r, "swing_variables"),
    rulesWaived: integerArray(r, "rules_waived"),
    activatedAt: instantOrNull(r, "activated_at"),
  });
};

/** Each swing variable as the set states it: a name, a cool leg and a hot leg. */
export interface SwingVariable {
  name: string;
  cool: string;
  hot: string;
}

/**
 * The swing variables, read out of the jsonb the worker owns.
 *
 * **The stored shape is a bare array.** `macro_core.scenario_set.swing_variables`
 * is declared `jsonb not null default '[]'::jsonb` and migration 0003's own
 * smoke test writes `[{"name": …, "cool": …, "hot": …}]`. This function once
 * looked only under a `variables` key, and an object wrapper was asserted in the
 * validator above it, so every stored set was rejected and `/history` showed
 * nothing behind a banner blaming the data. Both forms are read now: the array
 * is what the database holds, and the wrapper is kept because the fixtures and
 * an early worker draft wrote one, and widening a reader is cheaper than a
 * migration that rewrites judgment.
 *
 * Read defensively rather than validated as a row shape: a set that adds a
 * fourth field to each variable should widen this block, never reject the whole
 * scenario set. Anything that is not the shape below is skipped silently here
 * and counted by the caller.
 */
export function readSwingVariables(blob: unknown): readonly SwingVariable[] {
  const list = Array.isArray(blob)
    ? blob
    : typeof blob === "object" && blob !== null
      ? (blob as Record<string, unknown>)["variables"]
      : null;
  if (!Array.isArray(list)) return [];
  const out: SwingVariable[] = [];
  for (const item of list) {
    if (typeof item !== "object" || item === null) continue;
    const entry = item as Record<string, unknown>;
    if (
      typeof entry["name"] === "string" &&
      typeof entry["cool"] === "string" &&
      typeof entry["hot"] === "string"
    ) {
      out.push({
        name: entry["name"],
        cool: entry["cool"],
        hot: entry["hot"],
      });
    }
  }
  return out;
}

/* ============================================== macro_mart.scenario_state */

export interface ScenarioState {
  scenarioId: string;
  setId: string;
  ordinal: number;
  title: string;
  probability: number;
  positioning: string | null;
  clausesFired: number;
  clausesTotal: number;
  /** Kept in the denominator. The threshold is never renormalised. */
  clausesVoid: number;
  requiredUnmet: number;
  /** Counts only `kind = 'band'` targets: the automatically scorable ones. */
  bandsHolding: number;
  bandsTotal: number;
  /**
   * Every target, band and manual alike, and the `n` that `targets hit x of n`
   * uses at grading. Added in migration 0011, and never smaller than
   * `bandsTotal`.
   *
   * Two denominators because they answer different questions. The app renders
   * both and labels both rather than counting the rows itself: the web app
   * computes no metric, and conflating these two is what produced the W36
   * defect.
   */
  targetsHolding: number | null;
  targetsTotal: number | null;
  premiseMoved: boolean;
  evaluatedAt: string;
}

export const scenarioState: RowValidator<ScenarioState> = (row) => {
  const record = asRecord(row);
  if (!record.ok) return record;
  const r = record.value;
  return fields<ScenarioState>({
    scenarioId: text(r, "scenario_id"),
    setId: text(r, "set_id"),
    ordinal: integer(r, "ordinal"),
    title: text(r, "title"),
    probability: integer(r, "probability"),
    // Added in migration 0011. Optional rather than required, so a project on
    // the 0006 shape shows the scenario without its positioning line instead
    // of dropping the scenario.
    positioning: textOrNull(r, "positioning"),
    clausesFired: integer(r, "clauses_fired"),
    clausesTotal: integer(r, "clauses_total"),
    clausesVoid: integer(r, "clauses_void"),
    requiredUnmet: integer(r, "required_unmet"),
    bandsHolding: integer(r, "bands_holding"),
    bandsTotal: integer(r, "bands_total"),
    targetsHolding: integerOrNull(r, "targets_holding"),
    targetsTotal: integerOrNull(r, "targets_total"),
    premiseMoved: boolean(r, "premise_moved"),
    evaluatedAt: instant(r, "evaluated_at"),
  });
};

/* ======================================= macro_mart.scenario_target_state */

export interface ScenarioTargetState {
  targetId: string;
  scenarioId: string;
  seriesId: string | null;
  label: string;
  /**
   * `band` is auto-scored; `manual` is presented for judgment at grading.
   *
   * Nullable in migration 0011, which added it, so a project on the 0006 shape
   * reads null. The renderer falls back to inferring a manual target from a
   * null `series_id`, which is how `macro_core.scenario_target`'s own CHECK
   * defines one. Inferring it from a null `holding` would be wrong: that is the
   * unevaluable case and means something else entirely.
   */
  kind: "band" | "manual" | null;
  /**
   * Which edges the forecast actually stated. Added in migration 0014.
   *
   * A `lo` of null with `hi` set means "below X" under `lt` and "we never
   * wrote the floor" under `between`. The two render identically without this
   * column and only one of them is a forecast.
   */
  comparator: "between" | "gt" | "lt" | null;
  lo: number | null;
  hi: number | null;
  current: number | null;
  /** Null means unevaluable: nothing read, so nothing is claimed. */
  holding: boolean | null;
  /**
   * How far the current value is from breaching, in the series' own declared
   * unit.
   *
   * The unit is not stated in `docs/CONTRACT.md`. It is read as the series'
   * unit here, which is the only reading consistent with "one canonical unit
   * per series, declared once, and every condition written in it". A sign
   * convention is not stated either, so the magnitude is used and the sign
   * ignored.
   */
  distanceToBreach: number | null;
  /**
   * A manual target's stated sentence. Added in migration 0014.
   *
   * These carry no numbers at all and still count toward `targets_n`, so a
   * scenario that showed only its bands would understate what it will be
   * graded against.
   */
  text: string | null;
  evaluatedAt: string;
}

export const scenarioTargetState: RowValidator<ScenarioTargetState> = (row) => {
  const record = asRecord(row);
  if (!record.ok) return record;
  const r = record.value;
  return fields<ScenarioTargetState>({
    targetId: text(r, "target_id"),
    scenarioId: text(r, "scenario_id"),
    seriesId: textOrNull(r, "series_id"),
    label: text(r, "label"),
    kind: oneOfOrNull(r, "kind", ["band", "manual"]),
    comparator: oneOfOrNull(r, "comparator", ["between", "gt", "lt"]),
    lo: numeric(r, "lo"),
    hi: numeric(r, "hi"),
    current: numeric(r, "current"),
    holding: booleanOrNull(r, "holding"),
    distanceToBreach: numeric(r, "distance_to_breach"),
    text: textOrNull(r, "text"),
    evaluatedAt: instant(r, "evaluated_at"),
  });
};

/**
 * Whether a target is presented for judgment rather than scored.
 *
 * `kind` when the mart carries it, and a null `series_id` otherwise, which is
 * exactly how `macro_core.scenario_target`'s CHECK defines a manual target. A
 * null `holding` is deliberately not consulted: that is the unevaluable case,
 * which means a band whose series had no observation, and collapsing the two
 * would report a missing reading as a judgment call.
 */
export function isManualTarget(target: ScenarioTargetState): boolean {
  if (target.kind !== null) return target.kind === "manual";
  return target.seriesId === null;
}

/* ================================================== macro_mart.setup_state */

export interface SetupState {
  setupId: string;
  week: string;
  asset: Asset;
  name: string;
  entryLo: number | null;
  entryHi: number | null;
  invalidation: number | null;
  /**
   * Which side of the invalidation level ends the trade.
   *
   * On `macro_core.setup` and not in CONTRACT.md's `setup_state` column list.
   * Without it a level of 7,450 is printed with no statement of whether a close
   * above or below it is the stop, which is half a trade instruction.
   */
  invalidationDir: "above" | "below" | null;
  target: number | null;
  rrPlanned: number | null;
  gateState: GateState;
  gateReason: string | null;
  /** The gate clause as written, which is what the operator checks against. */
  gateText: string | null;
  /** Derived from the session high and low, not from the close. */
  bandTouchedToday: boolean;
  bandTouchedClose: boolean;
  entryState: (typeof ENTRY_STATES)[number];
  sessionsCarried: number;
  rRunning: number | null;
  /**
   * The text of the sector-focus row this setup sits under.
   *
   * `sector_focus_label`, resolved through `macro_core.setup.sector_focus_id`
   * by migration 0011. The setup carries the row by key rather than by string
   * and `macro_core` is not on the Data API, so the label has to be carried
   * here or the Setups view cannot show what the row it references says.
   */
  sectorFocusLabel: string | null;
  /**
   * The series this setup's entry, invalidation and target are levels on,
   * resolved from `macro_core.setup.series_id` by migration 0014.
   *
   * **Null means the worker deliberately did not score this setup.** Nothing
   * else in the row says which series its levels belong to, and equity setups
   * are quoted against a cash index while crypto setups are quoted against BTC
   * or ETH, so the mapping cannot be inferred from `asset`. A setup sitting
   * ungraded with no stated reason looks like a bug; this is what lets the
   * surface say it is a refusal instead.
   */
  seriesKey: string | null;
  seriesLabel: string | null;
  evaluatedAt: string;
}

export const setupState: RowValidator<SetupState> = (row) => {
  const record = asRecord(row);
  if (!record.ok) return record;
  const r = record.value;
  return fields<SetupState>({
    setupId: text(r, "setup_id"),
    week: weekLabel(r, "week"),
    asset: oneOf(r, "asset", ASSETS),
    name: text(r, "name"),
    entryLo: numeric(r, "entry_lo"),
    entryHi: numeric(r, "entry_hi"),
    invalidation: numeric(r, "invalidation"),
    invalidationDir: oneOfOrNull(r, "invalidation_dir", ["above", "below"]),
    target: numeric(r, "target"),
    rrPlanned: numeric(r, "rr_planned"),
    gateState: oneOf(r, "gate_state", GATE_STATES),
    gateReason: textOrNull(r, "gate_reason"),
    gateText: textOrNull(r, "gate_text"),
    bandTouchedToday: boolean(r, "band_touched_today"),
    bandTouchedClose: boolean(r, "band_touched_close"),
    entryState: oneOf(r, "entry_state", ENTRY_STATES),
    sessionsCarried: integer(r, "sessions_carried"),
    rRunning: numeric(r, "r_running"),
    sectorFocusLabel: textOrNull(r, "sector_focus_label"),
    seriesKey: textOrNull(r, "series_key"),
    seriesLabel: textOrNull(r, "series_label"),
    evaluatedAt: instant(r, "evaluated_at"),
  });
};

/**
 * Whether the worker is refusing to score this setup at all.
 *
 * `series_key` is what its entry, invalidation and target are levels on, and
 * the worker will not evaluate a setup that names no series. Without this the
 * row sits with a gate state and an entry state that were never computed and
 * looks like a bug rather than a refusal.
 */
export function isUnscored(setup: SetupState): boolean {
  return setup.seriesKey === null;
}

/**
 * The failure this whole system was built to catch, as a predicate.
 *
 * The entry band was reached and the gate refused, so the trade was forgone.
 * That happened in W37, W38 and W39, and each time it was found the following
 * Monday. `band_touched_today` rather than `band_touched_close`, because the
 * band being reached intraday and refused is exactly the case a close-only
 * reading cannot see.
 */
export function gateRefusedATouch(setup: SetupState): boolean {
  // A setup nobody scored has no band-touch flag worth believing.
  if (isUnscored(setup)) return false;
  return (
    setup.gateState !== "open" &&
    (setup.bandTouchedToday || setup.bandTouchedClose) &&
    setup.entryState === "not triggered"
  );
}

/* ======================================== macro_mart.strategy_state ====== */

export interface SectorFocus {
  /** The id `holdings_check.sector_focus_id` and a setup's own row point at. */
  id: string;
  band: "CRITICAL" | "HOLD" | "WATCH" | "AVOID";
  text: string;
}

const sectorFocus: RowValidator<SectorFocus> = (row) => {
  const record = asRecord(row);
  if (!record.ok) return record;
  const r = record.value;
  return fields<SectorFocus>({
    id: text(r, "id"),
    band: oneOf(r, "band", ["CRITICAL", "HOLD", "WATCH", "AVOID"]),
    text: text(r, "text"),
  });
};

export interface HoldingsCheck {
  id: string;
  ticker: string;
  action: "keep" | "trim" | "add" | "exit";
  text: string;
  /**
   * The sector-focus row this holding sits under, **by id**.
   *
   * Deliberately not the band itself. The `sectorFocus` array on the same
   * `strategy_state` row already resolves the id, and denormalising the band
   * into the holding would give a reader two places to disagree about one
   * fact. The UI resolves it through `resolveFocusBand` below.
   *
   * Null is the finding rather than a gap in the data: an active holding that
   * no sector-focus row supports. `macro_quality` raises it as a finding rather
   * than the schema refusing it, because holdings are re-parsed from the
   * Watchlist on every run while the strategy is authored weekly, so a ticker
   * added on a Wednesday would otherwise fail the parse that discovered it.
   */
  sectorFocusId: string | null;
}

const holdingsCheck: RowValidator<HoldingsCheck> = (row) => {
  const record = asRecord(row);
  if (!record.ok) return record;
  const r = record.value;
  return fields<HoldingsCheck>({
    id: text(r, "id"),
    ticker: text(r, "ticker"),
    action: oneOf(r, "action", ["keep", "trim", "add", "exit"]),
    text: text(r, "text"),
    sectorFocusId: textOrNull(r, "sector_focus_id"),
  });
};

/**
 * The band a holding sits under, resolved against the sibling sector-focus
 * rows, or null when no row supports it.
 *
 * One resolution, in one place, so no component is tempted to read a band off
 * the holding itself. An id that names a row this strategy does not carry
 * resolves to null and is shown as unsupported, which is the honest answer: the
 * app is not going to invent a band for an id it cannot find.
 */
export function resolveFocusBand(
  holding: HoldingsCheck,
  rows: readonly SectorFocus[],
): SectorFocus | null {
  if (holding.sectorFocusId === null) return null;
  return rows.find((row) => row.id === holding.sectorFocusId) ?? null;
}

/**
 * The standing strategy per book.
 *
 * `exposureOperated*` is separate from `exposure*` because the crypto book runs
 * two bands: the mandate band the framework allows, and the narrower band it is
 * actually operated at. Collapsing them would report a book at 60% as though
 * 110% were its position.
 *
 * The view filters to `status = 'active'` and projects no `status`, so every
 * row that reaches here is the standing strategy. Superseded ones are read
 * through `macro_mart.strategy_history`.
 */
export interface StrategyState {
  strategyId: string;
  week: string;
  asset: Asset;
  stance: string;
  posture: string;
  exposureLo: number | null;
  exposureHi: number | null;
  exposureOperatedLo: number | null;
  exposureOperatedHi: number | null;
  /** Ordered CRITICAL, HOLD, WATCH, AVOID by the view. Not alphabetical. */
  sectorFocus: readonly SectorFocus[];
  holdingsCheck: readonly HoldingsCheck[];
  activatedAt: string | null;
}

export const strategyState: RowValidator<StrategyState> = (row) => {
  const record = asRecord(row);
  if (!record.ok) return record;
  const r = record.value;
  const focus = readNested(r, "sector_focus", sectorFocus);
  if (!focus.ok) return focus;
  const holdings = readNested(r, "holdings_check", holdingsCheck);
  if (!holdings.ok) return holdings;
  return fields<StrategyState>({
    strategyId: text(r, "strategy_id"),
    week: weekLabel(r, "week"),
    asset: oneOf(r, "asset", ASSETS),
    stance: text(r, "stance"),
    posture: text(r, "posture"),
    exposureLo: numeric(r, "exposure_lo"),
    exposureHi: numeric(r, "exposure_hi"),
    exposureOperatedLo: numeric(r, "exposure_operated_lo"),
    exposureOperatedHi: numeric(r, "exposure_operated_hi"),
    sectorFocus: focus,
    holdingsCheck: holdings,
    activatedAt: instantOrNull(r, "activated_at"),
  });
};

function readNested<T>(
  r: Record<string, unknown>,
  key: string,
  validator: RowValidator<T>,
): Checked<readonly T[]> {
  const value = r[key];
  if (value === null || value === undefined) return ok([]);
  if (!Array.isArray(value)) return bad(`${key} must be a json array or null`);
  const out: T[] = [];
  for (let index = 0; index < value.length; index += 1) {
    const checked = validator(value[index]);
    if (!checked.ok) return bad(`${key}[${index}]: ${checked.reason}`);
    out.push(checked.value);
  }
  return ok(out);
}

/* ================================================== macro_mart.calibration */

/**
 * The single calibration row.
 *
 * Every measured column is nullable, because migration 0010 seeds `id = 1`
 * with all of them null and the worker fills them in on the Monday run after
 * grading. Reading any of them as required would reject the seed row and make
 * a correctly migrated project report that its migrations had not run, which
 * is the opposite of the truth and sends the operator to the wrong place.
 *
 * Null here is the same claim it is everywhere else: nothing was measured. A
 * `graded_weeks` of null and a `graded_weeks` of 0 mean different things, and
 * the tile prints the absent dash for the first.
 */
export interface Calibration {
  id: number;
  gradedWeeks: number | null;
  /** Materialized counts 1, Partial 0.5, Not 0, over up to the last 8 rows. */
  baseHitScore: number | null;
  baseMaterialized: number | null;
  meanBrier: number | null;
  brierWeeks: number | null;
  baseWasS2: number | null;
  window: number | null;
  computedAt: string | null;
}

export const calibration: RowValidator<Calibration> = (row) => {
  const record = asRecord(row);
  if (!record.ok) return record;
  const r = record.value;
  return fields<Calibration>({
    id: integer(r, "id"),
    gradedWeeks: integerOrNull(r, "graded_weeks"),
    baseHitScore: numeric(r, "base_hit_score"),
    baseMaterialized: integerOrNull(r, "base_materialized"),
    meanBrier: numeric(r, "mean_brier"),
    brierWeeks: integerOrNull(r, "brier_weeks"),
    baseWasS2: integerOrNull(r, "base_was_s2"),
    window: integerOrNull(r, "window"),
    computedAt: instantOrNull(r, "computed_at"),
  });
};

/**
 * The single calibration row, or a named reason there is none.
 *
 * The migration seeds `id = 1`, so an empty result means the migrations have
 * not run rather than that the worker is late. Different problems, different
 * fixes, different sentences.
 */
export function readSingleCalibration(payload: unknown): Checked<Calibration> {
  if (!Array.isArray(payload)) {
    return bad("macro_mart.calibration did not answer with a row set");
  }
  if (payload.length === 0) {
    return bad(
      "macro_mart.calibration holds no row. Migration 0010 seeds id = 1, so the migrations have not been applied to this project yet. A row whose measured columns are all null is a different thing: that is a migrated project whose worker has not graded a Monday yet.",
    );
  }
  return calibration(payload[0]);
}

/* ================================== macro_mart.week_grade (+) ============ */

/** One graded week of the Scorecard Ledger. **Not in CONTRACT.md's mart.** */
export interface WeekGrade {
  week: string;
  baseScenarioId: string | null;
  /** Always carried, so migrated and native rows read alike. */
  baseScenarioLabel: string;
  baseGrade: Grade;
  /**
   * The other two scenarios of the week, as `macro_core.week_grade` stores
   * them: a jsonb **array** of `{id, prob, grade, note}`, not an object.
   *
   * Read leniently, into whatever entries have an id and a grade. A ledger row
   * whose note gains a field should widen this line, never delete the graded
   * week, because the hit rate and the Brier score are computed from the
   * columns beside it and not from this blob.
   */
  otherGrades: readonly OtherGrade[];
  eqCall: string | null;
  cryptoCall: string | null;
  keyMiss: string | null;
  targetsHit: number | null;
  targetsN: number | null;
  brier: number | null;
  premiseMoved: boolean;
  /**
   * Null for the nine migrated weeks, which were graded by archive journals
   * this system deliberately does not read.
   *
   * Nullable on purpose: a derived Monday would be a manufactured fact, and
   * null, never zero applies to a timestamp as much as to a figure.
   */
  gradedAt: string | null;
}

/** One of the two non-base scenarios on a graded week. */
export interface OtherGrade {
  id: string;
  probability: number | null;
  grade: Grade;
  note: string | null;
}

export const weekGrade: RowValidator<WeekGrade> = (row) => {
  const record = asRecord(row);
  if (!record.ok) return record;
  const r = record.value;
  return fields<WeekGrade>({
    week: weekLabel(r, "week"),
    baseScenarioId: textOrNull(r, "base_scenario_id"),
    baseScenarioLabel: text(r, "base_scenario_label"),
    baseGrade: oneOf(r, "base_grade", GRADES),
    otherGrades: otherGrades(r, "other_grades"),
    eqCall: textOrNull(r, "eq_call"),
    cryptoCall: textOrNull(r, "crypto_call"),
    keyMiss: textOrNull(r, "key_miss"),
    targetsHit: integerOrNull(r, "targets_hit"),
    targetsN: integerOrNull(r, "targets_n"),
    brier: numeric(r, "brier"),
    premiseMoved: boolean(r, "premise_moved"),
    gradedAt: instantOrNull(r, "graded_at"),
  });
};

/**
 * The `other_grades` array, read for the entries that are the shape it states.
 *
 * Lenient rather than strict: this blob is a rendering detail beside the hit
 * rate and the Brier score, both of which are real columns. An entry that is
 * not the stated shape is skipped rather than failing the graded week, because
 * losing a week from the calibration record over a malformed note would change
 * a number the whole product is calibrated against.
 */
function otherGrades(
  row: Record<string, unknown>,
  key: string,
): Checked<readonly OtherGrade[]> {
  const value = row[key];
  if (value === null || value === undefined) return ok([]);
  if (!Array.isArray(value)) {
    return bad(`${key} must be a json array or null, got ${typeof value}`);
  }
  const out: OtherGrade[] = [];
  for (const item of value) {
    if (typeof item !== "object" || item === null || Array.isArray(item)) continue;
    const entry = item as Record<string, unknown>;
    const id = entry["id"];
    const grade = entry["grade"];
    if (typeof id !== "string") continue;
    if (typeof grade !== "string" || !GRADES.includes(grade as Grade)) continue;
    const prob = entry["prob"];
    out.push({
      id,
      probability: typeof prob === "number" && Number.isFinite(prob) ? prob : null,
      grade: grade as Grade,
      note: typeof entry["note"] === "string" ? entry["note"] : null,
    });
  }
  return ok(out);
}

/* ================================= macro_mart.setup_grade (+) ============ */

/** One graded setup of the Setup Ledger. **Not in CONTRACT.md's mart.** */
export interface SetupGrade {
  setupId: string;
  gradedWeek: string;
  /** The week it was proposed, which is what the ledger sorts by. */
  proposedWeek: string | null;
  asset: Asset | null;
  name: string | null;
  outcome: (typeof SETUP_OUTCOMES)[number];
  rRealised: number | null;
  note: string | null;
}

export const setupGrade: RowValidator<SetupGrade> = (row) => {
  const record = asRecord(row);
  if (!record.ok) return record;
  const r = record.value;
  return fields<SetupGrade>({
    setupId: text(r, "setup_id"),
    gradedWeek: weekLabel(r, "graded_week"),
    proposedWeek: weekLabelOrNull(r, "proposed_week"),
    asset: oneOfOrNull(r, "asset", ASSETS),
    name: textOrNull(r, "name"),
    outcome: oneOf(r, "outcome", SETUP_OUTCOMES),
    rRealised: numeric(r, "r_realised"),
    note: textOrNull(r, "note"),
  });
};

/* ================================ macro_mart.scenario_rule (+) =========== */

/** R1 to R12, with their teaching weeks and fire counts. **Not in the mart.** */
export interface ScenarioRule {
  number: number;
  text: string;
  /** Kept with its retiring week set. Numbers are never reused. */
  retiredWeek: string | null;
  /** A real foreign key onto graded weeks, so the count can be stated. */
  teachingWeeks: readonly string[];
  /** How many graded weeks have cited this rule since it was written. */
  fireCount: number;
}

export const scenarioRule: RowValidator<ScenarioRule> = (row) => {
  const record = asRecord(row);
  if (!record.ok) return record;
  const r = record.value;
  return fields<ScenarioRule>({
    number: integer(r, "number"),
    text: text(r, "text"),
    retiredWeek: weekLabelOrNull(r, "retired_week"),
    teachingWeeks: textArray(r, "teaching_weeks"),
    fireCount: integer(r, "fire_count"),
  });
};

/* ========================== macro_mart.scenario_set_history ============== */

/**
 * What was once believed, including the rows that were superseded.
 *
 * `macro_core` is not on the Data API and `/history` has to read what was
 * believed on a past date, so this projection is the only route to it. It is a
 * view rather than a recomputation and must never be dropped and rebuilt the
 * way the evaluated tables are.
 *
 * **It projects the scenario *set*, not the three scenarios inside it.**
 * Migration 0007 selects from `macro_core.scenario_set` alone, so the week's
 * synthesis, probability check, waived rules and amendment are all here and the
 * individual titles and probabilities are not. Nothing in `macro_mart` exposes
 * `macro_core.scenario` with a validity interval, so the probability a
 * scenario carried on a past date is currently unreadable, and `/history` says
 * so rather than showing the current three under a past date. That is listed
 * as a contract issue in `web/README.md`.
 *
 * A row is in force on a date when `valid_from <= date` and `valid_to` is
 * either null or after it. `valid_from` is `activated_at` and is null while the
 * row is still only proposed, which is correct: a proposal was never believed,
 * so it has no validity interval yet.
 */
export interface ScenarioSetHistory {
  setId: string;
  week: string;
  synthesis: string | null;
  probabilityCheck: string | null;
  /** Opaque, and read by `readSwingVariables`. An array in every stored set. */
  swingVariables: unknown;
  rulesWaived: readonly number[];
  status: (typeof LIFECYCLE)[number];
  authoredAt: string;
  /** Null while the set is still proposed. It was never believed. */
  validFrom: string | null;
  validTo: string | null;
  amends: string | null;
  /**
   * The event that triggered a re-price, from `macro_core.scenario_set`.
   *
   * Never dropped: a probability that moved with no stated reason is the
   * over-anchoring R2, R4, R5, R6, R10, R11 and R12 were all written to stop.
   */
  amendReason: string | null;
}

export const scenarioSetHistory: RowValidator<ScenarioSetHistory> = (row) => {
  const record = asRecord(row);
  if (!record.ok) return record;
  const r = record.value;
  return fields<ScenarioSetHistory>({
    // The view names the primary key `id`, not `set_id`.
    setId: text(r, "id"),
    week: weekLabel(r, "week"),
    synthesis: textOrNull(r, "synthesis"),
    probabilityCheck: textOrNull(r, "probability_check"),
    swingVariables: jsonBlob(r, "swing_variables"),
    rulesWaived: integerArray(r, "rules_waived"),
    status: oneOf(r, "status", LIFECYCLE),
    authoredAt: instant(r, "authored_at"),
    validFrom: instantOrNull(r, "valid_from"),
    validTo: instantOrNull(r, "valid_to"),
    amends: textOrNull(r, "amends"),
    amendReason: textOrNull(r, "amend_reason"),
  });
};

/* ============================= macro_mart.phase_call_history ============= */

export interface PhaseCallHistory {
  phaseCallId: string;
  asset: Asset;
  week: string;
  phase: number;
  phaseName: string;
  priorPhase: number | null;
  direction: "up" | "unchanged" | "down";
  reason: string | null;
  evidence: readonly string[];
  falsifier: string | null;
  status: (typeof LIFECYCLE)[number];
  validFrom: string | null;
  validTo: string | null;
  amends: string | null;
  amendReason: string | null;
}

export const phaseCallHistory: RowValidator<PhaseCallHistory> = (row) => {
  const record = asRecord(row);
  if (!record.ok) return record;
  const r = record.value;
  return fields<PhaseCallHistory>({
    // The view names the primary key `id`, not `phase_call_id`.
    phaseCallId: text(r, "id"),
    asset: oneOf(r, "asset", ASSETS),
    week: weekLabel(r, "week"),
    phase: integer(r, "phase"),
    phaseName: text(r, "phase_name"),
    priorPhase: integerOrNull(r, "prior_phase"),
    direction: oneOf(r, "direction", ["up", "unchanged", "down"]),
    reason: textOrNull(r, "reason"),
    evidence: textArray(r, "evidence"),
    falsifier: textOrNull(r, "falsifier"),
    status: oneOf(r, "status", LIFECYCLE),
    validFrom: instantOrNull(r, "valid_from"),
    validTo: instantOrNull(r, "valid_to"),
    amends: textOrNull(r, "amends"),
    amendReason: textOrNull(r, "amend_reason"),
  });
};

/* ============================ macro_mart.observation_history ============= */

/**
 * Every observation ever recorded, keyed `(series_id, asof)`.
 *
 * Read one series at a time, when a snapshot row's disclosure is opened.
 * Forty-seven series of full history on page load would be several thousand
 * rows to render one sparkline nobody asked for.
 *
 * `note` is the read as written at the time, added in migration 0016. It is
 * null on every observation taken before reads began to be stored, and that
 * blank is rendered as a blank.
 *
 * **A missing read is never filled in.** Not by re-deriving one from the value,
 * which would be this app manufacturing a judgment nobody made, and not by
 * falling back to the current read, which is worse still: a read shown beside a
 * value it was not written about is a sentence attributed to a day that did not
 * produce it.
 */
export interface ObservationHistory {
  seriesId: string;
  seriesKey: string;
  asof: string;
  value: number | null;
  display: string | null;
  unit: string | null;
  tier: number | null;
  source: string | null;
  released: string | null;
  /** Which of the two daily runs this reading came from. */
  observedAt: string | null;
  /**
   * The read this observation was written with: the journal's Read cell.
   *
   * Null where none was written, which is every observation predating
   * migration 0016. Never substituted for.
   */
  note: string | null;
}

export const observationHistory: RowValidator<ObservationHistory> = (row) => {
  const record = asRecord(row);
  if (!record.ok) return record;
  const r = record.value;
  return fields<ObservationHistory>({
    seriesId: text(r, "series_id"),
    seriesKey: text(r, "series_key"),
    asof: calendarDate(r, "asof"),
    value: numeric(r, "value"),
    display: textOrNull(r, "display"),
    unit: textOrNull(r, "unit"),
    tier: integerOrNull(r, "tier"),
    source: textOrNull(r, "source"),
    released: calendarDateOrNull(r, "released"),
    observedAt: instantOrNull(r, "observed_at"),
    note: textOrNull(r, "note"),
  });
};

/* ============================== macro_mart.scenario_history ============== */

/**
 * Every scenario of every set, including superseded ones.
 *
 * Added in migration 0014, and it closes the last thing `/history` could only
 * state rather than show: a past week's three titles and their probabilities.
 *
 * `macro_mart.scenario_set_history` projects the set and not the scenarios
 * inside it, so before this view the only options were to say so or to render
 * today's three under a past date. The second would have been misattribution
 * rather than staleness: a probability the operator never wrote, printed under
 * a day they did not write it, against which a Brier score would later be read.
 *
 * `synthesis`, `probabilityCheck` and the lifecycle columns come from the set
 * and repeat on each of its three rows, because the view joins them.
 */
export interface ScenarioHistory {
  scenarioId: string;
  setId: string;
  week: string;
  ordinal: number;
  title: string;
  probability: number;
  positioning: string | null;
  status: (typeof LIFECYCLE)[number];
  synthesis: string | null;
  probabilityCheck: string | null;
  /** Null while the set is still proposed. It was never believed. */
  validFrom: string | null;
  validTo: string | null;
  amends: string | null;
  amendReason: string | null;
}

export const scenarioHistory: RowValidator<ScenarioHistory> = (row) => {
  const record = asRecord(row);
  if (!record.ok) return record;
  const r = record.value;
  return fields<ScenarioHistory>({
    scenarioId: text(r, "scenario_id"),
    setId: text(r, "set_id"),
    week: weekLabel(r, "week"),
    ordinal: integer(r, "ordinal"),
    title: text(r, "title"),
    probability: integer(r, "probability"),
    positioning: textOrNull(r, "positioning"),
    status: oneOf(r, "status", LIFECYCLE),
    synthesis: textOrNull(r, "synthesis"),
    probabilityCheck: textOrNull(r, "probability_check"),
    validFrom: instantOrNull(r, "valid_from"),
    validTo: instantOrNull(r, "valid_to"),
    amends: textOrNull(r, "amends"),
    amendReason: textOrNull(r, "amend_reason"),
  });
};

/* ================================ macro_mart.setup_history =============== */

/**
 * The authored shape of a setup, including superseded rows.
 *
 * Added by migration 0012, and it closes half of what `/history` could not do:
 * the week's setups can now be rendered as they stood on a past date.
 *
 * The evaluated columns of `setup_state` are deliberately absent here.
 * `gate_state`, `entry_state`, the band-touch flags, `sessions_carried` and
 * `r_running` are recomputed from the tape on every run and were never stored
 * per version, so there is no past value of them to project. The view shows
 * what was *written*, not what it was doing that day, and this app must not
 * imply otherwise.
 */
export interface SetupHistory {
  setupId: string;
  week: string;
  asset: Asset;
  name: string;
  style: string | null;
  entryLo: number | null;
  entryHi: number | null;
  invalidation: number | null;
  invalidationDir: "above" | "below" | null;
  target: number | null;
  rrPlanned: number | null;
  gateText: string | null;
  sectorFocusLabel: string | null;
  rationale: string | null;
  status: (typeof LIFECYCLE)[number];
  validFrom: string | null;
  validTo: string | null;
  amends: string | null;
  amendReason: string | null;
}

export const setupHistory: RowValidator<SetupHistory> = (row) => {
  const record = asRecord(row);
  if (!record.ok) return record;
  const r = record.value;
  return fields<SetupHistory>({
    setupId: text(r, "setup_id"),
    week: weekLabel(r, "week"),
    asset: oneOf(r, "asset", ASSETS),
    name: text(r, "name"),
    style: textOrNull(r, "style"),
    entryLo: numeric(r, "entry_lo"),
    entryHi: numeric(r, "entry_hi"),
    invalidation: numeric(r, "invalidation"),
    invalidationDir: oneOfOrNull(r, "invalidation_dir", ["above", "below"]),
    target: numeric(r, "target"),
    rrPlanned: numeric(r, "rr_planned"),
    gateText: textOrNull(r, "gate_text"),
    sectorFocusLabel: textOrNull(r, "sector_focus_label"),
    rationale: textOrNull(r, "rationale"),
    status: oneOf(r, "status", LIFECYCLE),
    validFrom: instantOrNull(r, "valid_from"),
    validTo: instantOrNull(r, "valid_to"),
    amends: textOrNull(r, "amends"),
    amendReason: textOrNull(r, "amend_reason"),
  });
};

/**
 * Whether a validity interval covers a calendar day, as a half-open range.
 *
 * A null `valid_from` is never in force: the row is still only proposed, and a
 * proposal was never believed. Treating null as "since the beginning of time"
 * would date a belief to before it was held, which is the one thing a history
 * screen must not do.
 */
export function inForceOn(
  row: { validFrom: string | null; validTo: string | null },
  day: string,
): boolean {
  if (row.validFrom === null) return false;
  const at = Date.parse(`${day}T23:59:59.999Z`);
  const from = Date.parse(row.validFrom);
  if (!Number.isFinite(at) || !Number.isFinite(from)) return false;
  if (from > at) return false;
  if (row.validTo === null) return true;
  const to = Date.parse(row.validTo);
  return Number.isFinite(to) ? to > at : true;
}

/* ============================================ macro_mart.catalyst_window */

export interface CatalystWindow {
  catalystId: string;
  name: string;
  startsAt: string | null;
  /** The last day of a window, or of the part of a month an estimate names. */
  endsAt: string | null;
  datePrecision: string | null;
  /** The monitor's own "Date & Time" words, e.g. "late October 2026 expected". */
  dateText: string | null;
  /** The date is the monitor's guess, not a published schedule. */
  dateEstimated: boolean;
  /**
   * One of the sixteen worker categories (`families.py`). Null only on a
   * project that has not run migration 20260923001000; the page maps it, and
   * any category it does not know, to "Other".
   */
  category: string | null;
  affected: readonly string[];
  daysOut: number | null;
  /** Which trigger clauses depend on this catalyst. */
  referencedBy: readonly string[];
  sourceUrl: string | null;
}

/**
 * A flag column added by a later migration, read as false where it is absent.
 * `date_estimated` is `not null default false`, so false is also what the
 * column itself would say about a row written before it existed.
 */
function orFalse(checked: Checked<boolean | null>): Checked<boolean> {
  return checked.ok ? ok(checked.value ?? false) : checked;
}

export const catalystWindow: RowValidator<CatalystWindow> = (row) => {
  const record = asRecord(row);
  if (!record.ok) return record;
  const r = record.value;
  return fields<CatalystWindow>({
    catalystId: text(r, "catalyst_id"),
    name: text(r, "name"),
    startsAt: instantOrNull(r, "starts_at"),
    endsAt: instantOrNull(r, "ends_at"),
    datePrecision: textOrNull(r, "date_precision"),
    dateText: textOrNull(r, "date_text"),
    dateEstimated: orFalse(booleanOrNull(r, "date_estimated")),
    category: textOrNull(r, "category"),
    affected: textArray(r, "affected"),
    daysOut: integerOrNull(r, "days_out"),
    referencedBy: textArray(r, "referenced_by"),
    sourceUrl: textOrNull(r, "source_url"),
  });
};

/* ============================================= macro_quality.ingest_run == */

export interface IngestRun {
  id: number;
  source: string;
  /** `desk` is a `macro-agent desk` submission (docs/DESK.md), since 20260924000300. */
  runKind: "full" | "rescore" | "desk";
  startedAt: string;
  finishedAt: string | null;
  rows: number | null;
  status: string;
  error: string | null;
  /**
   * The feed a run wrote (`catalysts`, `tail_risks`, `holdings`), since
   * 20260924000300. Null on every source run the worker makes for a series,
   * and on a project that has not run the migration.
   */
  feed: string | null;
  /** Who sent a feed run: `openclaw:macro-desk` or `vault`. */
  origin: string | null;
}

export const ingestRun: RowValidator<IngestRun> = (row) => {
  const record = asRecord(row);
  if (!record.ok) return record;
  const r = record.value;
  return fields<IngestRun>({
    id: integer(r, "id"),
    source: text(r, "source"),
    runKind: oneOf(r, "run_kind", ["full", "rescore", "desk"] as const),
    startedAt: instant(r, "started_at"),
    finishedAt: instantOrNull(r, "finished_at"),
    rows: integerOrNull(r, "rows"),
    status: text(r, "status"),
    error: textOrNull(r, "error"),
    feed: textOrNull(r, "feed"),
    origin: textOrNull(r, "origin"),
  });
};

/* ================================================ macro_quality.finding == */

export interface Finding {
  id: number;
  checkKey: string;
  subject: string;
  /** The worker's sentence, rendered verbatim and never reworded. */
  sentence: string;
  severity: number;
  openedAt: string;
  closedAt: string | null;
}

export const finding: RowValidator<Finding> = (row) => {
  const record = asRecord(row);
  if (!record.ok) return record;
  const r = record.value;
  return fields<Finding>({
    id: integer(r, "id"),
    checkKey: text(r, "check_key"),
    subject: text(r, "subject"),
    sentence: text(r, "sentence"),
    severity: integer(r, "severity"),
    openedAt: instant(r, "opened_at"),
    closedAt: instantOrNull(r, "closed_at"),
  });
};

/* ==================================================== macro_quality.gap == */

export interface Gap {
  id: number;
  seriesId: string;
  /** The series key, so a gap can be named without a second lookup. */
  seriesKey: string | null;
  asof: string;
  reason: string;
  /** A point-in-time source that was missed and can never be recovered. */
  permanent: boolean;
  openedAt: string;
}

export const gap: RowValidator<Gap> = (row) => {
  const record = asRecord(row);
  if (!record.ok) return record;
  const r = record.value;
  return fields<Gap>({
    id: integer(r, "id"),
    seriesId: text(r, "series_id"),
    seriesKey: textOrNull(r, "series_key"),
    asof: calendarDate(r, "asof"),
    reason: text(r, "reason"),
    permanent: boolean(r, "permanent"),
    openedAt: instant(r, "opened_at"),
  });
};

/* =================================================== macro_mart.drift === */

export const DRIFT_KINDS = [
  "trigger_fired",
  "phase_condition",
  "phase_stale",
  "premise_moved",
  "tail_risk",
  "setup_gate",
  "setup_entry",
  "clause",
  "band",
] as const;

export interface DriftItem {
  kind: (typeof DRIFT_KINDS)[number];
  subject: string;
  before: string | null;
  after: string | null;
  /** One of the four changes `docs/DECISIONS.md` says may permit a re-price. */
  material: boolean;
  weight: number;
  sentence: string;
}

const driftItem: RowValidator<DriftItem> = (row) => {
  const record = asRecord(row);
  if (!record.ok) return record;
  const r = record.value;
  return fields<DriftItem>({
    kind: oneOf(r, "kind", DRIFT_KINDS),
    subject: text(r, "subject"),
    before: textOrNull(r, "before"),
    after: textOrNull(r, "after"),
    material: boolean(r, "material"),
    weight: integer(r, "weight"),
    sentence: text(r, "sentence"),
  });
};

/**
 * What has moved since the active call was made, scored by the worker.
 *
 * The app computes none of it. The worker re-evaluates every condition, band
 * and gate at the set's authoring as-of and diffs that against now; the Desk
 * prints the score, the reason and the items in the order the worker ranked
 * them.
 */
export interface Drift {
  setId: string;
  week: string;
  anchorAsof: string | null;
  asof: string | null;
  score: number;
  material: number;
  refreshSuggested: boolean;
  reason: string;
  items: readonly DriftItem[];
  evaluatedAt: string;
}

export const drift: RowValidator<Drift> = (row) => {
  const record = asRecord(row);
  if (!record.ok) return record;
  const r = record.value;
  return fields<Drift>({
    setId: text(r, "set_id"),
    week: weekLabel(r, "week"),
    anchorAsof: calendarDateOrNull(r, "anchor_asof"),
    asof: calendarDateOrNull(r, "asof"),
    score: integer(r, "score"),
    material: integer(r, "material"),
    refreshSuggested: boolean(r, "refresh_suggested"),
    reason: text(r, "reason"),
    items: jsonRows(r, "items", driftItem),
    evaluatedAt: instant(r, "evaluated_at"),
  });
};

/* ======================================== macro_mart.run_request_state == */

export const RUN_KINDS = ["narrative", "author", "amend"] as const;
export const RUN_STATUSES = [
  "queued",
  "running",
  "proposed",
  "done",
  "failed",
  "rejected",
  "accepted",
] as const;

export interface ProposedRef {
  table: string;
  id: string;
  week: string | null;
  asset: string | null;
  name: string | null;
}

const proposedRef: RowValidator<ProposedRef> = (row) => {
  const record = asRecord(row);
  if (!record.ok) return record;
  const r = record.value;
  return fields<ProposedRef>({
    table: text(r, "table"),
    id: text(r, "id"),
    week: textOrNull(r, "week"),
    asset: textOrNull(r, "asset"),
    name: textOrNull(r, "name"),
  });
};

/**
 * One Claude run, from the queue every run goes through.
 *
 * `proposed` with a null `decision` is the one state the operator acts on: the
 * run wrote judgment as proposed and is waiting for Accept or Reject. A
 * decision is recorded by `macro_mart.decide_proposal` and applied by the
 * runner on the operator's Mac, which is why a decided row stays `proposed`
 * for up to two minutes before it reads `accepted`.
 */
export interface RunRequest {
  id: number;
  kind: (typeof RUN_KINDS)[number];
  reason: string | null;
  origin: "schedule" | "operator";
  requestedAt: string;
  status: (typeof RUN_STATUSES)[number];
  claimedAt: string | null;
  finishedAt: string | null;
  decidedAt: string | null;
  decision: "accept" | "reject" | null;
  proposed: readonly ProposedRef[];
  summary: string | null;
  error: string | null;
}

export const runRequest: RowValidator<RunRequest> = (row) => {
  const record = asRecord(row);
  if (!record.ok) return record;
  const r = record.value;
  return fields<RunRequest>({
    id: integer(r, "id"),
    kind: oneOf(r, "kind", RUN_KINDS),
    reason: textOrNull(r, "reason"),
    origin: oneOf(r, "origin", ["schedule", "operator"] as const),
    requestedAt: instant(r, "requested_at"),
    status: oneOf(r, "status", RUN_STATUSES),
    claimedAt: instantOrNull(r, "claimed_at"),
    finishedAt: instantOrNull(r, "finished_at"),
    decidedAt: instantOrNull(r, "decided_at"),
    decision: oneOfOrNull(r, "decision", ["accept", "reject"] as const),
    proposed: jsonRows(r, "proposed", proposedRef),
    summary: textOrNull(r, "summary"),
    error: textOrNull(r, "error"),
  });
};

/* ========================================== macro_mart.tail_risk_state == */

export const TAIL_RISK_SEVERITIES = ["acute", "elevated", "watch"] as const;
export type TailRiskSeverity = (typeof TAIL_RISK_SEVERITIES)[number];

export interface TailRisk {
  key: string;
  name: string;
  severity: TailRiskSeverity;
  summary: string | null;
  pricedIn: string | null;
  assets: readonly string[];
  sourceUrl: string | null;
  firstSeen: string;
  lastSeen: string;
  /**
   * The risk's fixed identity since 20260924000300: rewording `name` keeps it,
   * where `key` (a hash of the name) would fork. Null only on a project that has
   * not run the migration, where the page falls back to `key`.
   */
  riskId: string | null;
  /** `vault` (the Monday note) or `openclaw:macro-desk`. */
  feedSource: string | null;
  lastCheckedAt: string | null;
  lastCheckKind: "weekly" | "daily" | null;
  /** The last different severity a check recorded, so an escalation can be shown. */
  previousSeverity: TailRiskSeverity | null;
}

export const tailRisk: RowValidator<TailRisk> = (row) => {
  const record = asRecord(row);
  if (!record.ok) return record;
  const r = record.value;
  return fields<TailRisk>({
    key: text(r, "key"),
    name: text(r, "name"),
    severity: oneOf(r, "severity", TAIL_RISK_SEVERITIES),
    summary: textOrNull(r, "summary"),
    pricedIn: textOrNull(r, "priced_in"),
    assets: textArray(r, "assets"),
    sourceUrl: textOrNull(r, "source_url"),
    firstSeen: calendarDate(r, "first_seen"),
    lastSeen: calendarDate(r, "last_seen"),
    riskId: textOrNull(r, "risk_id"),
    feedSource: textOrNull(r, "feed_source"),
    lastCheckedAt: instantOrNull(r, "last_checked_at"),
    lastCheckKind: oneOfOrNull(r, "last_check_kind", ["weekly", "daily"] as const),
    previousSeverity: oneOfOrNull(r, "previous_severity", TAIL_RISK_SEVERITIES),
  });
};

/* ======================================== macro_mart.tail_risk_history == */

/**
 * One check of one tail risk: the Monday scan or the daily recheck. The
 * Catalysts page draws a risk's escalation trail from these.
 */
export interface TailRiskCheck {
  riskId: string;
  name: string;
  checkedAt: string;
  kind: "weekly" | "daily";
  severity: TailRiskSeverity;
  summary: string | null;
  sourceUrl: string | null;
  retired: boolean;
}

export const tailRiskCheck: RowValidator<TailRiskCheck> = (row) => {
  const record = asRecord(row);
  if (!record.ok) return record;
  const r = record.value;
  return fields<TailRiskCheck>({
    riskId: text(r, "risk_id"),
    name: text(r, "name"),
    checkedAt: instant(r, "checked_at"),
    kind: oneOf(r, "kind", ["weekly", "daily"] as const),
    severity: oneOf(r, "severity", TAIL_RISK_SEVERITIES),
    summary: textOrNull(r, "summary"),
    sourceUrl: textOrNull(r, "source_url"),
    retired: boolean(r, "retired"),
  });
};

/* ==================================== macro_mart.watchlist_change_state == */

export interface WatchlistChange {
  ticker: string;
  observedAt: string;
  changePct: number | null;
  summary: string | null;
  sourceUrl: string | null;
  /**
   * The New York session the move belongs to, as `YYYY-MM-DD`. The 09:15 cron
   * re-reports Friday's session on the weekend, and the view keeps one row per
   * session. Null before migration 20260923001000.
   */
  sessionOn: string | null;
}

export const watchlistChange: RowValidator<WatchlistChange> = (row) => {
  const record = asRecord(row);
  if (!record.ok) return record;
  const r = record.value;
  return fields<WatchlistChange>({
    ticker: text(r, "ticker"),
    observedAt: instant(r, "observed_at"),
    changePct: numeric(r, "change_pct"),
    summary: textOrNull(r, "summary"),
    sourceUrl: textOrNull(r, "source_url"),
    sessionOn: calendarDateOrNull(r, "session_on"),
  });
};

/* ====================================== macro_mart.holding_watch_state == */

export const HOLDING_SOURCES = ["accounting", "manual", "watch"] as const;
export type HoldingSource = (typeof HOLDING_SOURCES)[number];

/**
 * One watched name on the latest day the holdings watch ran.
 *
 * `price` and `change24hPct` are the desk command's, computed from market
 * data, never a model's number. A change it could not compute is null with
 * `unavailableReason`; the page says the move is unavailable rather than
 * printing a zero.
 */
export interface HoldingWatch {
  asOf: string;
  symbol: string;
  source: HoldingSource;
  name: string | null;
  assetClass: string | null;
  marketSymbol: string | null;
  price: number | null;
  priceAt: string | null;
  currency: string | null;
  change24hPct: number | null;
  /** `close_to_close` for stocks and ETFs, `rolling_24h` for crypto. */
  changeBasis: "close_to_close" | "rolling_24h" | null;
  /** For a close-to-close change, the session whose close it ends on. */
  changeSession: string | null;
  priceSource: string | null;
  unavailableReason: string | null;
  /** An option carries its underlying's move, and says so. */
  underlyingOnly: boolean;
  material: boolean;
  driverSummary: string | null;
  sourceUrl: string | null;
  /**
   * The desk looked and found no story behind a material move. It may still
   * carry a driver with no source. A column added after 20260924000400, read
   * as false where it is absent.
   */
  noClearReason: boolean;
  recordedAt: string;
}

export const holdingWatch: RowValidator<HoldingWatch> = (row) => {
  const record = asRecord(row);
  if (!record.ok) return record;
  const r = record.value;
  return fields<HoldingWatch>({
    asOf: calendarDate(r, "as_of"),
    symbol: text(r, "symbol"),
    source: oneOf(r, "source", HOLDING_SOURCES),
    name: textOrNull(r, "name"),
    assetClass: textOrNull(r, "asset_class"),
    marketSymbol: textOrNull(r, "market_symbol"),
    price: numeric(r, "price"),
    priceAt: instantOrNull(r, "price_at"),
    currency: textOrNull(r, "currency"),
    change24hPct: numeric(r, "change_24h_pct"),
    changeBasis: oneOfOrNull(r, "change_basis", [
      "close_to_close",
      "rolling_24h",
    ] as const),
    changeSession: calendarDateOrNull(r, "change_session"),
    priceSource: textOrNull(r, "price_source"),
    unavailableReason: textOrNull(r, "unavailable_reason"),
    underlyingOnly: boolean(r, "underlying_only"),
    material: boolean(r, "material"),
    driverSummary: textOrNull(r, "driver_summary"),
    sourceUrl: textOrNull(r, "source_url"),
    noClearReason: orFalse(booleanOrNull(r, "no_clear_reason")),
    recordedAt: instant(r, "recorded_at"),
  });
};

/* ====================================== macro_mart.holding_event_state == */

export const HOLDING_EVENT_KINDS = [
  "earnings",
  "product",
  "regulatory",
  "unlock",
  "other",
] as const;

/** What is coming up for a watched name in the next fourteen days. */
export interface HoldingEvent {
  id: string;
  symbol: string;
  kind: (typeof HOLDING_EVENT_KINDS)[number];
  name: string;
  startsAt: string | null;
  endsAt: string | null;
  datePrecision: string;
  /** The date is the source's guess, not a published schedule. */
  dateEstimated: boolean;
  dateText: string | null;
  sourceUrl: string;
  /** The calendar row an earnings event was placed on or linked to. */
  catalystId: string | null;
}

export const holdingEvent: RowValidator<HoldingEvent> = (row) => {
  const record = asRecord(row);
  if (!record.ok) return record;
  const r = record.value;
  return fields<HoldingEvent>({
    id: text(r, "id"),
    symbol: text(r, "symbol"),
    kind: oneOf(r, "kind", HOLDING_EVENT_KINDS),
    name: text(r, "name"),
    startsAt: instantOrNull(r, "starts_at"),
    endsAt: instantOrNull(r, "ends_at"),
    datePrecision: text(r, "date_precision"),
    dateEstimated: boolean(r, "date_estimated"),
    dateText: textOrNull(r, "date_text"),
    sourceUrl: text(r, "source_url"),
    catalystId: textOrNull(r, "catalyst_id"),
  });
};

/* ================ macro_mart.manual_position_state, watch_ticker_state == */

export const ASSET_CLASSES = [
  "equity",
  "etf",
  "crypto",
  "option",
  "commodity",
  "fixed_income",
  "other",
] as const;
export type AssetClass = (typeof ASSET_CLASSES)[number];

/** A position at a broker Accounting does not connect to, edited on the page. */
export interface ManualPosition {
  id: string;
  symbol: string;
  name: string | null;
  venue: string;
  assetClass: AssetClass;
  quantity: number | null;
  note: string | null;
  createdAt: string;
  updatedAt: string;
}

export const manualPosition: RowValidator<ManualPosition> = (row) => {
  const record = asRecord(row);
  if (!record.ok) return record;
  const r = record.value;
  return fields<ManualPosition>({
    id: text(r, "id"),
    symbol: text(r, "symbol"),
    name: textOrNull(r, "name"),
    venue: text(r, "venue"),
    assetClass: oneOf(r, "asset_class", ASSET_CLASSES),
    quantity: numeric(r, "quantity"),
    note: textOrNull(r, "note"),
    createdAt: instant(r, "created_at"),
    updatedAt: instant(r, "updated_at"),
  });
};

/** A name followed without being held, edited on the page. */
export interface WatchTicker {
  id: string;
  symbol: string;
  name: string | null;
  assetClass: AssetClass;
  note: string | null;
  createdAt: string;
  updatedAt: string;
}

export const watchTicker: RowValidator<WatchTicker> = (row) => {
  const record = asRecord(row);
  if (!record.ok) return record;
  const r = record.value;
  return fields<WatchTicker>({
    id: text(r, "id"),
    symbol: text(r, "symbol"),
    name: textOrNull(r, "name"),
    assetClass: oneOf(r, "asset_class", ASSET_CLASSES),
    note: textOrNull(r, "note"),
    createdAt: instant(r, "created_at"),
    updatedAt: instant(r, "updated_at"),
  });
};

/* =========================================== macro_mart.feed_freshness == */

/**
 * Where one of the three feeds last came from and when.
 *
 * Read the same whether the worker still parses the Obsidian note (`vault`)
 * or the desk command writes the tables (`openclaw:macro-desk`). A failed
 * feed keeps its `lastOkAt`, which is what lets the page show it stale.
 */
export interface FeedFreshness {
  feed: string;
  origin: string | null;
  lastRunAt: string;
  lastFinishedAt: string | null;
  lastStatus: string;
  lastError: string | null;
  lastOkAt: string | null;
  lastOkOrigin: string | null;
  rows: number | null;
}

export const feedFreshness: RowValidator<FeedFreshness> = (row) => {
  const record = asRecord(row);
  if (!record.ok) return record;
  const r = record.value;
  return fields<FeedFreshness>({
    feed: text(r, "feed"),
    origin: textOrNull(r, "origin"),
    lastRunAt: instant(r, "last_run_at"),
    lastFinishedAt: instantOrNull(r, "last_finished_at"),
    lastStatus: text(r, "last_status"),
    lastError: textOrNull(r, "last_error"),
    lastOkAt: instantOrNull(r, "last_ok_at"),
    lastOkOrigin: textOrNull(r, "last_ok_origin"),
    rows: integerOrNull(r, "rows"),
  });
};
