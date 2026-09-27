/**
 * What the terminal is allowed to claim right now.
 *
 * Three clocks, never collapsed:
 *
 * 1. **Evaluated** — `evaluated_at` / `updated_at`, the moment the worker last
 *    recomputed `macro_mart`. It is what the arithmetic on screen is as-of.
 * 2. **As-of** — `asof`, the day the reading describes, in the *source's own*
 *    market calendar. A Friday US close observed at 05:00 Saturday Singapore
 *    time has a Friday as-of.
 * 3. **Released** — `released`, the day the source published it. CFTC positions
 *    are as-of Tuesday and released Friday, and those are different facts.
 *
 * `next_expected` sits beside the three as the fourth column. It is not a clock
 * but a forecast, and it is the only thing `due` and `overdue` are decided
 * against.
 *
 * **This module never rates a series itself.** `macro_mart.snapshot_row.freshness`
 * is the worker's rating, computed against each series' declared `release_rule`,
 * and inventing a threshold here to turn a chip green is precisely the failure
 * the design guards against. What this module does is decide what the *reading*
 * may claim given the ratings it was handed.
 */

import { FRESHNESS, type Freshness } from "./contract";
import { dayLabel } from "./time";

/**
 * Which states mean something has gone wrong, and which are a series behaving
 * exactly as its cadence says it should.
 *
 * This table is the single most load-bearing thing in the file. Forty-seven
 * series carry six release rules, so at any moment a large minority of
 * them are between prints. If `due` counted as a fault the panel would sit
 * permanently amber, the operator would learn to ignore the colour, and the one
 * week a source actually failed would look like every other week.
 *
 * `asserted` is the same argument from the other end. State Street's monthly
 * allocation and the tariff state are hand-asserted by design, not fetched;
 * they are a different kind of claim, marked as one, and never a complaint.
 */
const IS_FAULT: Readonly<Record<Freshness, boolean>> = {
  fresh: false,
  due: false,
  asserted: false,
  overdue: true,
  gap: true,
  not_connected: true,
  failed: true,
};

export function isFault(state: Freshness): boolean {
  return IS_FAULT[state];
}

/**
 * Severity order over the fault states, worst last.
 *
 * `not_connected` outranks `gap` because a permanent gap is one missing day on
 * a series that is otherwise wired, and an unwired series is every day of it.
 * `failed` outranks both: a source that was called and did not answer is the
 * only one of the four where something is actively broken right now.
 */
const FAULT_ORDER: readonly Freshness[] = [
  "overdue",
  "gap",
  "not_connected",
  "failed",
];

export interface StateCopy {
  /** What a chip carrying this state says. */
  chipLabel: string;
  /** The token class. The only place a state becomes a colour. */
  tokenClass: string;
  /** One line explaining what the state claims, for a title attribute. */
  meaning: string;
}

/**
 * The display vocabulary, which is not the contract vocabulary.
 *
 * The seven keys (`fresh`, `due`, `overdue`, `gap`, `asserted`,
 * `not_connected`, `failed`) are shared with the worker and never renamed. What
 * changes here is only the word the operator reads, and the words were chosen
 * against the one question a chip has to answer: is the figure beside it usable?
 *
 * `FAILED` was the worst of them. A source that did not answer on the last run
 * leaves the previous observation on screen with its own older as-of, so
 * nothing failed in the sense the word carries; the figure is simply stale, and
 * printing it in red taught the reader that the whole reading was broken when
 * one vendor was quiet. `ASSERTED` read as an accusation rather than as a
 * provenance note, and `NOT CONNECTED` read as a network fault rather than as a
 * series nobody has wired yet.
 */
export const STATE_COPY: Readonly<Record<Freshness, StateCopy>> = {
  fresh: {
    chipLabel: "FRESH",
    tokenClass: "state-fresh",
    meaning:
      "The latest observation is current for this series' cadence and its next print is not yet due.",
  },
  /**
   * The calm token, deliberately, and not the amber `--state-stale`.
   *
   * A weekly series four days from its next print is current, not stale. It is
   * rendered in the dated grey with its `next expected` date beside it, so the
   * reader sees a series waiting rather than a series failing.
   */
  due: {
    chipLabel: "DUE",
    tokenClass: "state-dated",
    meaning:
      "Between observations. The reading shown is the current one for this series' cadence; the next print is due on the date beside it. This is not late and nothing has failed.",
  },
  overdue: {
    chipLabel: "OVERDUE",
    tokenClass: "state-stale",
    meaning:
      "The next observation was expected and has not arrived. The reading shown is the last one and is no longer current for this series' cadence.",
  },
  gap: {
    chipLabel: "MISSING DAY",
    tokenClass: "state-empty",
    meaning:
      "A point-in-time source was missed and cannot be recovered. Nothing is interpolated and no prior value is carried forward, so the day has no reading and any condition depending on it is unevaluable.",
  },
  asserted: {
    chipLabel: "MANUAL",
    tokenClass: "state-unrated",
    meaning:
      "Recorded by hand from a source that cannot be polled. A different kind of claim from a measured reading, marked as one, and never a complaint.",
  },
  not_connected: {
    chipLabel: "NOT WIRED",
    tokenClass: "state-not-connected",
    meaning:
      "This series is declared in config/series.yaml but no source has ever written an observation for it.",
  },
  /**
   * Amber, and the chip is followed by the as-of the figure is held at.
   *
   * Red said the reading had failed. It had not: the last observation is still
   * on screen, still dated, and still true of the day it describes. What has
   * failed is the fetch, which makes the figure stale rather than wrong.
   */
  failed: {
    chipLabel: "STALE",
    tokenClass: "state-stale",
    meaning:
      "The source did not answer on the last run. The figure is the last one received, dated beside it.",
  },
};

/**
 * The display word for a state, in prose.
 *
 * Derived from `STATE_COPY` rather than kept as a second table, so a chip and a
 * sentence can never describe one state with two different words. It is never
 * the contract key: `not_connected` reads "not wired" here and stays
 * `not_connected` on the wire.
 */
export function stateWord(state: Freshness): string {
  return STATE_COPY[state].chipLabel.toLowerCase();
}

/**
 * The worst fault among a set of series states, or `fresh` when there is none.
 *
 * Worst rather than an average, exactly as Accounting takes the worst venue: a
 * headline is a claim about everything under it, so one silent source outranks
 * forty live ones. But `due` and `asserted` are filtered out before the
 * comparison, because a headline that read `DUE` every day of the week would be
 * describing the calendar and not the data.
 */
export function worstFault(states: readonly Freshness[]): Freshness {
  let worst: Freshness = "fresh";
  for (const state of states) {
    if (!IS_FAULT[state]) continue;
    if (FAULT_ORDER.indexOf(state) > FAULT_ORDER.indexOf(worst)) worst = state;
    else if (worst === "fresh") worst = state;
  }
  return worst;
}

/** How many series sit in each state, for the line under the header. */
export type FreshnessCounts = Readonly<Record<Freshness, number>>;

export function countStates(states: readonly Freshness[]): FreshnessCounts {
  const counts: Record<Freshness, number> = {
    fresh: 0,
    due: 0,
    overdue: 0,
    gap: 0,
    asserted: 0,
    not_connected: 0,
    failed: 0,
  };
  for (const state of states) counts[state] += 1;
  return counts;
}

/**
 * The counts, as a sentence, in the order the reader cares about.
 *
 * States with a count of zero are dropped. A line reading
 * "38 fresh · 0 overdue · 0 gap · 0 failed" spends four slots asserting that
 * nothing is wrong, which trains the eye to skip the line that would carry the
 * one week something is.
 */
export function countsSentence(counts: FreshnessCounts): string {
  const order: readonly Freshness[] = [
    "fresh",
    "due",
    "asserted",
    "overdue",
    "gap",
    "not_connected",
    "failed",
  ];
  const parts = order
    .filter((state) => counts[state] > 0)
    .map((state) => `${counts[state]} ${stateWord(state)}`);
  return parts.join(" · ");
}

export function totalSeries(counts: FreshnessCounts): number {
  return Object.values(counts).reduce((sum, n) => sum + n, 0);
}

/**
 * The header line: two numbers, not seven.
 *
 * The seven-way breakdown is the truth and it stays one hover away, but it is
 * not a headline. A line reading "49 fresh · 2 due · 4 asserted · 1 overdue ·
 * 1 gap · 3 not connected · 14 failed" makes the reader do the arithmetic that
 * decides whether to trust the screen, every time they look at it. This does
 * the arithmetic once and says the answer.
 *
 * Only `fresh` counts as current. A `due` series is inside its cadence and is
 * not a fault, which is why it never colours anything amber, but it is also not
 * a reading taken today, and a headline that counted it as current would let a
 * week of missed prints hide behind the word.
 */
export function currentSentence(counts: FreshnessCounts): string {
  const total = totalSeries(counts);
  return `${counts.fresh} of ${total} current · ${total - counts.fresh} not`;
}

export type BannerSeverity = "fault" | "caution";

export interface Banner {
  severity: BannerSeverity;
  /** The producer's sentence, rendered verbatim. Never clipped, never reworded. */
  text: string;
}

/**
 * The verdict for one reading: what it may claim, and whether it may render.
 */
export interface Reading {
  state: Freshness | "pending";
  statusLabel: string;
  tokenClass: string;
  /**
   * Whether the panel may render its children at all.
   *
   * Structural, not advisory: `Panel` refuses to render children when this is
   * false, so no view can fall through to a plausible zero or to an empty table
   * that reads as "nothing happened".
   */
  hasData: boolean;
  /** Why nothing is showing, or what rates what is. Always names the input. */
  reason: string;
  /** One fault, one owner: the only place the reason is printed whole. */
  banner: Banner | null;
  /** Dim the body and keep it readable. Never a spinner over data we hold. */
  dimmed: boolean;
  counts: FreshnessCounts;
  /** The evaluation stamp this reading is as-of, or null before one arrives. */
  evaluatedAt: string | null;
}

export interface ReadingInput {
  /** Per-series ratings from the worker. Empty means it has never written. */
  states: readonly Freshness[];
  /** Rows the validators excluded, each with the reason it was excluded. */
  rejected: readonly string[];
  /** A query, permission or network failure. An envelope fault, not a row one. */
  transportError: string | null;
  /** The latest `evaluated_at` across the tables this reading covers. */
  evaluatedAt: string | null;
  /** True once every query behind this reading has answered at least once. */
  answered: boolean;
  /** What this reading is of, for the sentences. `the snapshot`, `the ledgers`. */
  subject: string;
  /** The mart relations this reading reads, named in the empty state. */
  relations: readonly string[];
}

const PENDING_COPY = {
  chipLabel: "WAITING",
  tokenClass: "state-pending",
};

export function readReading(input: ReadingInput): Reading {
  const counts = countStates(input.states);

  // An envelope fault outranks everything a row could say. If the query itself
  // failed, the rows being held may be arbitrarily old and nothing is shown.
  if (input.transportError !== null && input.states.length === 0) {
    return {
      state: "failed",
      statusLabel: STATE_COPY.failed.chipLabel,
      tokenClass: STATE_COPY.failed.tokenClass,
      hasData: false,
      reason: input.transportError,
      banner: { severity: "fault", text: input.transportError },
      dimmed: false,
      counts,
      evaluatedAt: input.evaluatedAt,
    };
  }

  if (!input.answered || input.states.length === 0) {
    const reason = !input.answered
      ? `${input.relations.join(" and ")} have not answered yet.`
      : `${input.relations.join(" and ")} answered and held nothing. The worker has not completed a pass against this project, so there is no ${input.subject} to show, and a zero would be a claim rather than a blank.`;
    return {
      state: "pending",
      statusLabel: PENDING_COPY.chipLabel,
      tokenClass: PENDING_COPY.tokenClass,
      hasData: false,
      reason,
      banner: null,
      dimmed: false,
      counts,
      evaluatedAt: input.evaluatedAt,
    };
  }

  const state = worstFault(input.states);

  // Amber for a series that is late or has a hole in it, red only for a source
  // that was called and did not answer, or for a series nobody wired. The two
  // must not borrow each other's colour: one is a schedule slipping, the other
  // is something broken.
  const severity: BannerSeverity =
    state === "failed" || state === "not_connected" ? "fault" : "caution";

  const banner: Banner | null =
    input.transportError !== null
      ? {
          severity: "caution",
          text: `${input.transportError} The figures below are the last ones this session received.`,
        }
      : isFault(state)
        ? { severity, text: faultSentence(state, counts) }
        : input.rejected.length > 0
          ? {
              severity: "caution",
              text: `${input.rejected.length} row${input.rejected.length === 1 ? "" : "s"} did not validate and ${input.rejected.length === 1 ? "is" : "are"} not shown: ${input.rejected.join("; ")}`,
            }
          : null;

  return {
    state,
    statusLabel: STATE_COPY[state].chipLabel,
    tokenClass: STATE_COPY[state].tokenClass,
    hasData: true,
    reason: reasonSentence(state, counts, input.subject),
    banner,
    // Only an envelope fault dims the whole body.
    //
    // A per-series fault is dimmed on that series' own cells, and nowhere else.
    // One unwired series out of forty-seven does not make the other forty-six
    // less true, and dimming the panel over it puts every good figure behind a
    // veil while the one bad row is the only one the operator cannot read
    // anyway. A `due` reading is not dimmed at all: it is the current reading
    // for its cadence, and dimming it would say the opposite.
    //
    // When the query itself failed, every row is suspect at once, because they
    // are all whatever the cache happened to be holding. That is the one case
    // where the veil is the truth.
    dimmed: input.transportError !== null,
    counts,
    evaluatedAt: input.evaluatedAt,
  };
}

function faultSentence(state: Freshness, counts: FreshnessCounts): string {
  const n = counts[state];
  const plural = n === 1 ? "series is" : "series are";
  if (state === "overdue") {
    return `${n} ${plural} past the date their next observation was expected. The readings shown are the last ones received and are no longer current for those series' cadence.`;
  }
  if (state === "gap") {
    return `${n} ${plural} carrying a permanent gap: a point-in-time source was missed and cannot be recovered. Nothing is interpolated and no prior value is carried forward, so any condition depending on those days reports unevaluable.`;
  }
  if (state === "not_connected") {
    return `${n} ${plural} declared in config/series.yaml with no observation ever written. Nothing is being shown for them, and a condition referencing one resolves to unevaluable rather than to false.`;
  }
  return `${n} ${plural} on a source that was called on the last run and did not answer.`;
}

function reasonSentence(
  state: Freshness,
  counts: FreshnessCounts,
  subject: string,
): string {
  const total = totalSeries(counts);
  const head = `${total} series in ${subject}: ${countsSentence(counts)}.`;
  const cadence =
    counts.due > 0
      ? ` A series between observations reads due, not overdue: ${counts.due} of them are inside their own cadence with the next print not yet expected.`
      : "";
  const rating = isFault(state)
    ? ` The worst rating across them is ${stateWord(state)}, and the terminal takes the worst rather than an average.`
    : " No series is overdue, missing a day, unwired, or held on a source that did not answer.";
  return `${head}${cadence}${rating}`;
}

/**
 * Whether a single row should be dimmed.
 *
 * Only a fault dims. A `due` row is the current reading of a weekly series and
 * dimming it would say the opposite. `asserted` is not dimmed either: an
 * asserted value is exactly as true as the person who asserted it, which is a
 * claim about provenance and is carried by the chip, not by opacity.
 */
export function dimsRow(state: Freshness): boolean {
  return isFault(state);
}

/**
 * Whether a row's state is worth a chip of its own in a dense table.
 *
 * `fresh` and `due` are the two normal states, and a column that prints a chip
 * on all forty-seven rows carries no information. The abnormal five get one, so
 * an empty freshness column means nothing is wrong, which is a reading the eye
 * can take at a glance.
 */
export function needsChip(state: Freshness): boolean {
  return state !== "fresh" && state !== "due";
}

/* ===================================================== the health line === */

/**
 * One line at the top of the reading, instead of a banner and a stack of chips.
 *
 * What was there before: a red banner carrying one sentence about one vendor,
 * then six chips, one of which repeated the same sentence fourteen times
 * because the worker had written it once per series, and four of which said the
 * same setup defect four times. Six shapes on screen, three actual problems,
 * and the reader had to count to find that out.
 *
 * What is here instead is one clause per problem with a count in front of it.
 * A clause is either a source that did not answer, with how many series are
 * held at which observation date, or a group of findings sharing a `check_key`.
 * The full sentences are one click away and are never reworded, because the
 * producer owns them; what the strip owns is the arithmetic that says how many
 * of each there are.
 */

/** The fields the strip reads off a snapshot row. */
export interface HealthRow {
  freshness: Freshness;
  source: string | null;
  asof: string | null;
}

/** The fields the strip reads off a finding. */
export interface HealthFinding {
  checkKey: string;
  subject: string;
  closedAt: string | null;
}

/**
 * What each check is called, singular and plural, from its `check_key`.
 *
 * The keys are the producers' and are never renamed: every key here is one
 * the worker (`worker/src/macro`) or the agent package (`agent/src`) writes to
 * `macro_quality.finding`, and a key nobody writes does not belong here. What
 * the reader sees is a clause they can act on: `condition_coverage` is a true
 * name for the check and tells an operator nothing about what to do next.
 */
const CHECK_CLAUSE: Readonly<Record<string, readonly [string, string]>> = {
  // worker: sources and series
  source_incomplete: ["source answered in part", "sources answered in part"],
  series_key_unknown: ["reading for an unknown series", "readings for unknown series"],
  breadth_divergence: ["breadth source disagrees", "breadth sources disagree"],
  breadth_fallback: ["breadth from the backup source", "breadth from the backup source"],
  etf_flow_divergence: ["ETF flow sources disagree", "ETF flow sources disagree"],
  etf_flow_fallback: ["ETF flow from the backup source", "ETF flows from the backup source"],
  database_write_refused: ["database write refused", "database writes refused"],
  vault_file_unreadable: ["Obsidian note unreadable", "Obsidian notes unreadable"],
  catalyst_name_collision: ["catalyst name used twice", "catalyst names used twice"],
  // worker: the week's judgment
  setup_series_missing: ["setup unscored", "setups unscored"],
  condition_coverage: ["condition needs judgment", "conditions need judgment"],
  condition_malformed: ["condition cannot be checked", "conditions cannot be checked"],
  phase_read_stale: [
    "phase read may be out of date",
    "phase reads may be out of date",
  ],
  phase_tracker_rows: [
    "phase call without four conditions",
    "phase calls without four conditions",
  ],
  scenario_premise_moved: ["scenario premise moved", "scenario premises moved"],
  trigger_clause_catalyst_missing: [
    "trigger clause names a missing event",
    "trigger clauses name a missing event",
  ],
  trigger_clause_dropped: ["trigger clause dropped", "trigger clauses dropped"],
  scenario_count: ["scenario set not three scenarios", "scenario sets not three scenarios"],
  scenario_probabilities_sum: [
    "scenario set not summing to 100",
    "scenario sets not summing to 100",
  ],
  scenario_set_unscoreable: [
    "scenario set that cannot be scored",
    "scenario sets that cannot be scored",
  ],
  holdings_check_missing: [
    "holding without a strategy row",
    "holdings without a strategy row",
  ],
  week_grade_incomplete: ["week not fully graded", "weeks not fully graded"],
  scorecard_row_missing: ["week missing from the scorecard", "weeks missing from the scorecard"],
  setup_check_incomplete: ["setup not graded", "setups not graded"],
  setup_ledger_open_too_long: [
    "setup open too long in the ledger",
    "setups open too long in the ledger",
  ],
  // worker: the Obsidian feeds, until the desk replaces them
  catalyst_monitor_empty: ["calendar note empty", "calendar notes empty"],
  catalyst_monitor_section_missing: [
    "calendar note missing its section",
    "calendar notes missing their section",
  ],
  catalyst_line_unparsed: ["calendar line not read", "calendar lines not read"],
  blackswan_empty: ["tail-risk note empty", "tail-risk notes empty"],
  blackswan_section_missing: [
    "tail-risk note missing its section",
    "tail-risk notes missing their section",
  ],
  tail_risk_line_unparsed: ["tail-risk line not read", "tail-risk lines not read"],
  watchlist_empty: ["watchlist note empty", "watchlist notes empty"],
  watchlist_section_missing: [
    "watchlist note missing its section",
    "watchlist notes missing their section",
  ],
  change_line_unparsed: ["watchlist line not read", "watchlist lines not read"],
  holding_line_unparsed: ["holding line not read", "holding lines not read"],
  // desk command
  desk_catalyst_refused: ["calendar item refused", "calendar items refused"],
  desk_tail_risk_refused: ["tail risk refused", "tail risks refused"],
  desk_holding_refused: ["holding refused", "holdings refused"],
  // authoring agent: the narrative run and the proposal's pre-flight (agent/src)
  narrative_series_missing: ["narrative series missing", "narrative series missing"],
  typed_condition_coverage: ["call with no typed conditions", "calls with no typed conditions"],
  r1_levels_are_cash_levels: ["R1 gap: a weekly change not read off cash", "R1 gaps: weekly changes not read off cash"],
  r2_two_sided: ["R2 gap: bands all on one side", "R2 gaps: bands all on one side"],
  r3_oil_bands_sized_from_the_range: ["R3 gap: oil bands too narrow", "R3 gaps: oil bands too narrow"],
  r4_risk_off_has_a_confirming_leg: ["R4 gap: risk-off trigger has no typed confirming leg", "R4 gaps: risk-off triggers have no typed confirming leg"],
  r5_leadership_has_its_own_axis: ["R5 gap: a scenario has no leadership target", "R5 gaps: scenarios have no leadership target"],
  r6_dateless_shock_is_priced: ["R6 gap: no dateless shock priced", "R6 gaps: no dateless shock priced"],
  r7_the_two_books_get_their_own_rows: ["R7 gap: crypto shares the equity rows", "R7 gaps: crypto shares the equity rows"],
  r8_conjunction_falsifier_is_priced: ["R8 gap: joint falsifier not priced", "R8 gaps: joint falsifiers not priced"],
  r9_leadership_is_not_in_the_trigger: ["R9 gap: leadership inside a trigger", "R9 gaps: leadership inside triggers"],
  r10_no_streak_extrapolated: ["R10 gap: a flow streak extrapolated", "R10 gaps: flow streaks extrapolated"],
  r11_oil_has_its_own_axis: ["R11 gap: oil has no axis of its own", "R11 gaps: oil has no axis of its own"],
  r12_driver_named_per_asset: ["R12 gap: driver not named per asset", "R12 gaps: drivers not named per asset"],
  // runner (agent/src/macro_agent/runner.py)
  run_changed_repo: ["Claude run changed repo files", "Claude runs changed repo files"],
};

/** The chip a finding carries: its clause in the singular. */
export function checkChipLabel(checkKey: string, subject: string): string {
  if (checkKey === "source_unreadable") {
    return `${subject.toUpperCase()} not answering`;
  }
  const pair = CHECK_CLAUSE[checkKey];
  // The de-underscored key, so a check this table has never heard of still
  // renders as itself rather than as a blank chip.
  return pair === undefined ? checkKey.replace(/_/g, " ") : pair[0];
}

/** The counted form of the same clause, for the strip. */
function clauseFor(checkKey: string, count: number, subjects: string[]): string {
  if (checkKey === "source_unreadable") {
    return count === 1
      ? `${subjects[0]!.toUpperCase()} not answering`
      : `${count} sources not answering`;
  }
  const pair = CHECK_CLAUSE[checkKey];
  // A key with no clause is counted as findings, named by its key, so the
  // count never has to agree in number with words it did not choose.
  if (pair === undefined) {
    return `${count} ${count === 1 ? "finding" : "findings"}: ${checkKey.replace(/_/g, " ")}`;
  }
  return `${count} ${count === 1 ? pair[0] : pair[1]}`;
}

/**
 * Collapse a sentence that repeats itself.
 *
 * The worker writes one clause per affected series and joins them with `"; "`,
 * which for a vendor owning fourteen series produces the same sentence fourteen
 * times inside one finding. That is being fixed in the worker. The UI does not
 * depend on the fix: a producer's sentence is rendered verbatim, and rendering
 * the same verbatim sentence fourteen times is not extra honesty.
 */
export function dedupeSentence(sentence: string): string {
  const seen = new Set<string>();
  const kept: string[] = [];
  for (const part of sentence.split("; ")) {
    const key = part.trim();
    if (key.length === 0 || seen.has(key)) continue;
    seen.add(key);
    kept.push(part);
  }
  return kept.join("; ");
}

export interface HealthLine {
  /** Amber when anything is not current, green when everything is. */
  stale: boolean;
  /** One clause per source or defect, already counted. */
  clauses: readonly string[];
}

function sourceName(source: string): string {
  return source.charAt(0).toUpperCase() + source.slice(1);
}

/**
 * The vendor behind a series' source string. `Yahoo chart API (^DJI)` and
 * `Yahoo chart API (^GSPC)` are one vendor that failed once, not fourteen
 * clauses; the symbol in brackets and the "chart API" suffix name the request,
 * not the source that owes it.
 */
export function sourceFamily(source: string): string {
  return source
    .replace(/\s*\([^)]*\)\s*$/, "")
    .replace(/\s+(chart|quote|data)?\s*api$/i, "")
    .replace(/\s+via\s+.*$/i, "")
    .trim();
}

export function healthLine(input: {
  rows: readonly HealthRow[];
  findings: readonly HealthFinding[];
  counts: FreshnessCounts;
  /**
   * An envelope fault or a rejected-row count, which is about the query rather
   * than about any series and so cannot be grouped with the rest.
   */
  lead: string | null;
}): HealthLine {
  const clauses: string[] = [];
  if (input.lead !== null) clauses.push(input.lead);

  // Failed series, by the source that owes them, with the observation date the
  // figures are held at. One clause per source, never one per series.
  const held = new Map<string, { n: number; asof: string | null }>();
  for (const row of input.rows) {
    if (row.freshness !== "failed") continue;
    const key = row.source === null ? "an unnamed source" : sourceFamily(row.source);
    const seen = held.get(key) ?? { n: 0, asof: null };
    seen.n += 1;
    if (row.asof !== null && (seen.asof === null || row.asof > seen.asof)) {
      seen.asof = row.asof;
    }
    held.set(key, seen);
  }
  const heldSources = new Set([...held.keys()].map((key) => key.toLowerCase()));
  const heldClauses = [...held.entries()]
    .sort((a, b) => b[1].n - a[1].n || a[0].localeCompare(b[0]))
    .map(([source, seen]) =>
      seen.asof === null
        ? `${seen.n} series on ${sourceName(source)}, which has not answered`
        : `${seen.n} held at ${dayLabel(seen.asof)}: ${sourceName(source)} has not answered`,
    );
  clauses.push(...heldClauses);

  // Findings, grouped by check. A source that is already named by a held clause
  // is not named twice: one fault, one clause.
  const groups = new Map<string, string[]>();
  for (const finding of input.findings) {
    if (finding.closedAt !== null) continue;
    if (
      finding.checkKey === "source_unreadable" &&
      heldSources.has(finding.subject.toLowerCase())
    ) {
      continue;
    }
    const subjects = groups.get(finding.checkKey) ?? [];
    subjects.push(finding.subject);
    groups.set(finding.checkKey, subjects);
  }
  clauses.push(
    ...[...groups.entries()]
      .sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0]))
      .map(([key, subjects]) => clauseFor(key, subjects.length, subjects)),
  );

  const stale = FRESHNESS.some(
    (state) => isFault(state) && input.counts[state] > 0,
  );
  if (clauses.length === 0) {
    clauses.push(
      stale
        ? `${totalSeries(input.counts) - input.counts.fresh} of ${totalSeries(input.counts)} series are not current`
        : `All ${totalSeries(input.counts)} series current`,
    );
  }
  return { stale: stale || input.lead !== null, clauses };
}
