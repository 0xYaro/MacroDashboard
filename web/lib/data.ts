/**
 * The one module that reads the database.
 *
 * Everything the app knows comes through here: `macro_mart.*` and
 * `macro_quality.*` by select through `supabase.schema(...)`, and nothing else.
 * The app computes no metric; it selects what the worker already computed.
 *
 * Two things are enforced at this seam rather than left to callers.
 *
 * **Validation is not optional.** Every row goes through `lib/contract.ts`, and
 * the reading that comes back carries both the rows that validated and a named
 * reason for each that did not. A caller cannot accidentally read the raw
 * payload, because it is never returned.
 *
 * **Envelope and row failures stay separate.** A query that failed is a
 * `transportError` on the reading, which means the whole reading is untrusted
 * and nothing is shown. A trustworthy response holding one malformed row keeps
 * its valid neighbours and counts the bad one. Collapsing the two would either
 * discard a healthy snapshot over one bad row, or show a stale one as though
 * the query had succeeded.
 */

import type { SupabaseClient } from "@supabase/supabase-js";

import {
  catalystWindow,
  conditionState,
  drift,
  feedFreshness,
  finding,
  observationHistory,
  gap,
  holdingEvent,
  holdingWatch,
  ingestRun,
  manualPosition,
  phaseCallDetail,
  phaseCallHistory,
  phaseScore,
  readSingleCalibration,
  runRequest,
  scenarioRule,
  scenarioHistory,
  scenarioSetHistory,
  scenarioSetState,
  scenarioState,
  scenarioTargetState,
  setupGrade,
  setupHistory,
  setupState,
  snapshotRow,
  strategyState,
  tailRisk,
  tailRiskCheck,
  watchlistChange,
  watchTicker,
  weekGrade,
  type AssetClass,
  type Calibration,
  type CatalystWindow,
  type ConditionState,
  type Drift,
  type FeedFreshness,
  type Finding,
  type Gap,
  type HoldingEvent,
  type HoldingWatch,
  type IngestRun,
  type ManualPosition,
  type ObservationHistory,
  type PhaseCallDetail,
  type PhaseCallHistory,
  type PhaseScore,
  type RunRequest,
  type ScenarioHistory,
  type ScenarioRule,
  type ScenarioSetHistory,
  type ScenarioSetState,
  type ScenarioState,
  type ScenarioTargetState,
  type SetupGrade,
  type SetupHistory,
  type SetupState,
  type SnapshotRow,
  type StrategyState,
  type TailRisk,
  type TailRiskCheck,
  type WatchlistChange,
  type WatchTicker,
  type WeekGrade,
} from "./contract";
import { fixtureSnapshot, type FixtureDataset } from "./fixtures";
import { readTable, type RowValidator, type TableReading } from "./validate";

/* -------------------------------------------------------------- query keys */

/**
 * Query keys, in one place, so nothing can disagree about which cache entry a
 * relation lives in. In fixture mode the dataset is part of the scope:
 * switching dataset is a different question, not a stale answer to the same
 * one.
 *
 * Every key is namespaced by relation, and **the Desk, Data and Catalysts pages share
 * these keys**. That is what makes `[` and `]` free: the views are four reads
 * of one response, so walking between them touches no query at all.
 */
export const keys = {
  snapshot: (scope: string) => ["macro_mart", "snapshot_row", scope] as const,
  conditions: (scope: string) =>
    ["macro_mart", "condition_state", scope] as const,
  phaseScores: (scope: string) => ["macro_mart", "phase_score", scope] as const,
  phaseCalls: (scope: string) =>
    ["macro_mart", "phase_call_detail", scope] as const,
  scenarioSet: (scope: string) =>
    ["macro_mart", "scenario_set_state", scope] as const,
  scenarios: (scope: string) =>
    ["macro_mart", "scenario_state", scope] as const,
  targets: (scope: string) =>
    ["macro_mart", "scenario_target_state", scope] as const,
  setups: (scope: string) => ["macro_mart", "setup_state", scope] as const,
  strategy: (scope: string) => ["macro_mart", "strategy_state", scope] as const,
  catalysts: (scope: string) =>
    ["macro_mart", "catalyst_window", scope] as const,
  calibration: (scope: string) => ["macro_mart", "calibration", scope] as const,
  weekGrades: (scope: string) => ["macro_mart", "week_grade", scope] as const,
  setupGrades: (scope: string) => ["macro_mart", "setup_grade", scope] as const,
  rules: (scope: string) => ["macro_mart", "scenario_rule", scope] as const,
  scenarioSetHistory: (scope: string) =>
    ["macro_mart", "scenario_set_history", scope] as const,
  scenarioHistory: (scope: string) =>
    ["macro_mart", "scenario_history", scope] as const,
  phaseHistory: (scope: string) =>
    ["macro_mart", "phase_call_history", scope] as const,
  setupHistory: (scope: string) =>
    ["macro_mart", "setup_history", scope] as const,
  drift: (scope: string) => ["macro_mart", "drift", scope] as const,
  runRequests: (scope: string) =>
    ["macro_mart", "run_request_state", scope] as const,
  tailRisks: (scope: string) =>
    ["macro_mart", "tail_risk_state", scope] as const,
  watchlist: (scope: string) =>
    ["macro_mart", "watchlist_change_state", scope] as const,
  tailRiskHistory: (scope: string) =>
    ["macro_mart", "tail_risk_history", scope] as const,
  holdings: (scope: string) =>
    ["macro_mart", "holding_watch_state", scope] as const,
  holdingEvents: (scope: string) =>
    ["macro_mart", "holding_event_state", scope] as const,
  manualPositions: (scope: string) =>
    ["macro_mart", "manual_position_state", scope] as const,
  watchTickers: (scope: string) =>
    ["macro_mart", "watch_ticker_state", scope] as const,
  feeds: (scope: string) => ["macro_mart", "feed_freshness", scope] as const,
  findings: (scope: string) => ["macro_quality", "finding", scope] as const,
  gaps: (scope: string) => ["macro_quality", "gap", scope] as const,
  runs: (scope: string) => ["macro_quality", "ingest_run", scope] as const,
  /** Per series, so a row opened twice is read once. */
  observations: (scope: string, seriesKey: string) =>
    ["macro_mart", "observation_history", scope, seriesKey] as const,
  /**
   * Every series' last thirty days, as one cache entry.
   *
   * Keyed on the window's first day rather than on nothing, so the window
   * moving is a different question and not a stale answer to the same one.
   */
  recentObservations: (scope: string, since: string) =>
    ["macro_mart", "observation_history", scope, "since", since] as const,
};

/**
 * `days` before an ISO calendar day, as an ISO calendar day.
 *
 * UTC throughout. An `asof` is a calendar date with no time in it, and
 * shifting one through a local zone is how a window silently loses or gains a
 * day for half the world.
 */
export function daysBefore(day: string, days: number): string {
  const at = Date.parse(`${day}T00:00:00Z`);
  if (!Number.isFinite(at)) return day;
  return new Date(at - days * 86_400_000).toISOString().slice(0, 10);
}

/* ----------------------------------------------------------------- readings */

/** A table reading: the rows that validated, and why the others did not. */
export interface Reading<T> extends TableReading<T> {
  /**
   * A query, transport or permission failure. Non-null means the rows in this
   * reading, if any, are whatever the cache was already holding.
   */
  transportError: string | null;
}

export interface SingleReading<T> {
  row: T | null;
  transportError: string | null;
  rejected: readonly string[];
}

function empty<T>(transportError: string | null): Reading<T> {
  return { rows: [], rejected: [], transportError };
}

/** Wrap already-fetched rows in a reading. */
export function readingOf<T>(
  payload: unknown,
  validator: RowValidator<T>,
): Reading<T> {
  const checked = readTable(payload, validator);
  if (!checked.ok) return empty(checked.reason);
  return { ...checked.value, transportError: null };
}

/**
 * A PostgREST failure, as a sentence the panel can print.
 *
 * The code is kept because `PGRST205` (relation not found) and `42501`
 * (insufficient privilege) send the operator to two completely different
 * places: a migration that has not run, and a schema missing from the Data
 * API's exposed list. `macro_mart` and `macro_quality` both have to be added to
 * that list by hand, so `PGRST205` is the likeliest first-deploy failure here.
 */
function transportSentence(
  table: string,
  error: { message: string; code?: string; hint?: string | null },
): string {
  const code = error.code ? ` [${error.code}]` : "";
  const hint = error.hint ? ` ${error.hint}` : "";
  return `Reading ${table} failed${code}: ${error.message}.${hint}`;
}

/**
 * A thrown fetch, as a sentence.
 *
 * supabase-js resolves most failures into `{ error }`, but a DNS failure, an
 * aborted request or an offline browser can reject instead. A queryFn that lets
 * the rejection escape leaves TanStack with no `data` at all, and the panel
 * then shows its "the worker has not written yet" empty state, which is a claim
 * about the worker when the truth is about the network.
 */
function thrownSentence(table: string, cause: unknown): string {
  const detail =
    cause instanceof Error
      ? cause.message
      : `an unexpected value (${typeof cause})`;
  return `Reading ${table} threw before it answered: ${detail}. This is usually the browser being unable to reach the Supabase project at all, rather than the project refusing the request.`;
}

/**
 * Whether a refusal means the session is dead rather than the schema is wrong.
 *
 * The distinction matters because the responses look alike and the fixes are
 * opposite. `PGRST301` and a JWT complaint mean the token expired while the
 * page sat open: the operator has to sign in again. `42501` and `PGRST205` mean
 * a grant or an exposed-schema setting is missing; signing out would send the
 * operator round the login form forever and leave the real cause unread.
 */
export function isExpiredSession(error: {
  message: string;
  code?: string;
}): boolean {
  if (error.code === "PGRST301") return true;
  return /\bjwt\b.*(expired|invalid)|invalid claim|token is expired/i.test(
    error.message,
  );
}

/**
 * Whether a refusal means the relation simply is not there.
 *
 * Seven relations this app reads are not in `docs/CONTRACT.md` yet. Each is
 * read through `optionalTable`, which swallows exactly this refusal and nothing
 * else: a missing relation degrades one section to a named empty state, while a
 * permission error or a network failure still reaches the banner as a fault.
 */
export function isMissingRelation(error: {
  message: string;
  code?: string;
}): boolean {
  if (error.code === "PGRST205" || error.code === "42P01") return true;
  return /could not find the table|relation .* does not exist/i.test(
    error.message,
  );
}

/* --------------------------------------------------------------- the source */

/**
 * Fixture mode is a source, not a mock layer.
 *
 * It answers the same interface the live path does, with rows in the same wire
 * shape, so every validator, every null and every named reason is exercised on
 * the way to the screen.
 */
export interface Source {
  /** A stable string that makes one source's cache entries its own. */
  scope: string;
  martTable<T>(table: string, validator: RowValidator<T>): Promise<Reading<T>>;
  qualityTable<T>(
    table: string,
    validator: RowValidator<T>,
  ): Promise<Reading<T>>;
  /**
   * A relation the contract does not define yet. A missing relation answers
   * with an empty reading and no transport error; every other failure is
   * reported as a fault.
   */
  optionalTable<T>(
    table: string,
    validator: RowValidator<T>,
  ): Promise<Reading<T>>;
  /**
   * One relation, narrowed to the rows whose `column` equals `value`.
   *
   * The only filtered read in the app, and it exists for one reason:
   * `macro_mart.observation_history` holds every observation of every series,
   * and a snapshot row's disclosure needs one series of it. Selecting the
   * whole relation to draw one sparkline would pull several thousand rows to
   * render forty of them.
   */
  filteredTable<T>(
    table: string,
    column: string,
    value: string,
    validator: RowValidator<T>,
  ): Promise<Reading<T>>;
  /**
   * One relation, narrowed to the rows at or after a calendar day.
   *
   * The snapshot draws a thirty-day sparkline on every numeric row, and one
   * read per visible series would be forty-odd requests on every page load.
   * A single window read is one request and, at thirty days across the whole
   * registry, a few hundred rows rather than the several thousand the whole
   * relation holds. The window is the filter, so the cost does not grow with
   * the history.
   */
  sinceTable<T>(
    table: string,
    column: string,
    since: string,
    validator: RowValidator<T>,
  ): Promise<Reading<T>>;
  calibration(): Promise<SingleReading<Calibration>>;
  /**
   * The writes this app makes, and the only ones.
   *
   * Everything else is a select. The first two call the SECURITY DEFINER
   * functions in migration 20260923000300, which insert or update one row of
   * the run queue and nothing else: the Refresh button asks for an amendment,
   * and Accept or Reject records a decision the runner on the operator's Mac
   * then applies. Neither moves any judgment row itself.
   *
   * The other four (migration 20260924000400) edit the operator's two short
   * holdings lists: positions at brokers Accounting does not connect to, and
   * names watched without being held. A row is deactivated, never deleted.
   *
   * Each resolves to a sentence on failure, never throws.
   */
  requestRefresh(reason: string): Promise<WriteResult>;
  decideProposal(
    id: number,
    decision: "accept" | "reject",
  ): Promise<WriteResult>;
  saveManualPosition(input: ManualPositionInput): Promise<WriteResult>;
  retireManualPosition(id: string): Promise<WriteResult>;
  saveWatchTicker(input: WatchTickerInput): Promise<WriteResult>;
  retireWatchTicker(id: string): Promise<WriteResult>;
}

export type WriteResult = { ok: true } | { ok: false; reason: string };

/** A manual position to add (`id` null) or to change. */
export interface ManualPositionInput {
  id: string | null;
  symbol: string;
  name: string | null;
  venue: string;
  assetClass: AssetClass;
  quantity: number | null;
  note: string | null;
}

/** A watched ticker to add (`id` null) or to change. */
export interface WatchTickerInput {
  id: string | null;
  symbol: string;
  name: string | null;
  assetClass: AssetClass;
  note: string | null;
}

/** The symbol rule `save_manual_position` and `save_watch_ticker` enforce. */
export const SYMBOL_PATTERN = /^[A-Z0-9][A-Z0-9.^=/:-]{0,23}$/;

/** A symbol as the two functions store it: trimmed and upper-cased. */
export function normaliseSymbol(symbol: string): string {
  return symbol.trim().toUpperCase();
}

/** Blank is null, as the functions' `nullif(btrim(...), '')` has it. */
function blankToNull(value: string | null): string | null {
  if (value === null) return null;
  const trimmed = value.trim();
  return trimmed === "" ? null : trimmed;
}

export function fixtureSource(dataset: FixtureDataset): Source {
  const snapshot = fixtureSnapshot(dataset);
  const tables: Readonly<Record<string, unknown[]>> = {
    snapshot_row: snapshot.snapshotRows,
    condition_state: snapshot.conditions,
    phase_score: snapshot.phaseScores,
    phase_call_detail: snapshot.phaseCalls,
    scenario_set_state: snapshot.scenarioSet,
    scenario_state: snapshot.scenarios,
    scenario_target_state: snapshot.targets,
    setup_state: snapshot.setups,
    strategy_state: snapshot.strategies,
    catalyst_window: snapshot.catalysts,
    drift: snapshot.drift,
    run_request_state: snapshot.runRequests,
    tail_risk_state: snapshot.tailRisks,
    tail_risk_history: snapshot.tailRiskHistory,
    watchlist_change_state: snapshot.watchlist,
    holding_watch_state: snapshot.holdings,
    holding_event_state: snapshot.holdingEvents,
    manual_position_state: snapshot.manualPositions,
    watch_ticker_state: snapshot.watchTickers,
    feed_freshness: snapshot.feeds,
    calibration: snapshot.calibration,
    week_grade: snapshot.weekGrades,
    setup_grade: snapshot.setupGrades,
    scenario_rule: snapshot.rules,
    scenario_set_history: snapshot.scenarioSetHistory,
    scenario_history: snapshot.scenarioHistory,
    observation_history: snapshot.observations,
    phase_call_history: snapshot.phaseHistory,
    setup_history: snapshot.setupHistory,
    finding: snapshot.findings,
    gap: snapshot.gaps,
    ingest_run: snapshot.runs,
  };
  const read = async <T>(
    name: string,
    validator: RowValidator<T>,
  ): Promise<Reading<T>> => {
    const payload = tables[name];
    if (payload === undefined) {
      return empty(`The fixtures hold no ${name}.`);
    }
    return readingOf(payload, validator);
  };
  return {
    scope: `fixture:${dataset}`,
    martTable: read,
    qualityTable: read,
    optionalTable: read,
    async filteredTable(name, column, value, validator) {
      const payload = tables[name];
      if (payload === undefined) return empty(`The fixtures hold no ${name}.`);
      // Narrowed on the wire shape, by the snake_case column name, exactly
      // where PostgREST narrows it. Filtering after validation would be
      // filtering on the camelCase field the validator produces, which is a
      // different name and would silently match nothing.
      const narrowed = payload.filter(
        (row) =>
          typeof row === "object" &&
          row !== null &&
          (row as Record<string, unknown>)[column] === value,
      );
      return readingOf(narrowed, validator);
    },
    async sinceTable(name, column, since, validator) {
      const payload = tables[name];
      if (payload === undefined) return empty(`The fixtures hold no ${name}.`);
      // Narrowed on the wire shape by the snake_case column, exactly where
      // PostgREST narrows it, and by string comparison, which is what an ISO
      // calendar day is ordered by both here and in Postgres.
      const narrowed = payload.filter((row) => {
        if (typeof row !== "object" || row === null) return false;
        const at = (row as Record<string, unknown>)[column];
        return typeof at === "string" && at >= since;
      });
      return readingOf(narrowed, validator);
    },
    async calibration() {
      const checked = readSingleCalibration(snapshot.calibration);
      return checked.ok
        ? { row: checked.value, transportError: null, rejected: [] }
        : { row: null, transportError: null, rejected: [checked.reason] };
    },
    // The fixture queue behaves as the two SQL functions do, in memory, so the
    // Refresh control and the proposal review can be exercised with no project.
    async requestRefresh(reason) {
      const text = reason.trim();
      if (text.length < 3) {
        return {
          ok: false,
          reason: "a refresh names what changed, in at least a few words",
        };
      }
      const queue = snapshot.runRequests as Record<string, unknown>[];
      if (
        queue.some(
          (row) =>
            row.kind === "amend" &&
            (row.status === "queued" || row.status === "running"),
        )
      ) {
        return { ok: true };
      }
      const id = Math.max(0, ...queue.map((row) => Number(row.id))) + 1;
      queue.unshift({
        id,
        kind: "amend",
        reason: text,
        origin: "operator",
        requested_at: new Date().toISOString(),
        status: "queued",
        claimed_at: null,
        finished_at: null,
        decided_at: null,
        decision: null,
        proposed: [],
        summary: null,
        error: null,
      });
      return { ok: true };
    },
    async decideProposal(id, decision) {
      const queue = snapshot.runRequests as Record<string, unknown>[];
      const row = queue.find((one) => Number(one.id) === id);
      if (
        row === undefined ||
        row.status !== "proposed" ||
        row.decision !== null
      ) {
        return {
          ok: false,
          reason: `run ${id} is not a proposal awaiting a decision`,
        };
      }
      row.decision = decision;
      row.decided_at = new Date().toISOString();
      return { ok: true };
    },
    // The two holdings lists behave as the four SQL functions do, in memory,
    // including their refusals, so the editor can be exercised with no project.
    async saveManualPosition(input) {
      const symbol = normaliseSymbol(input.symbol);
      if (!SYMBOL_PATTERN.test(symbol)) {
        return { ok: false, reason: symbolRefusal(input.symbol) };
      }
      const venue = input.venue.trim();
      if (venue === "") {
        return {
          ok: false,
          reason: "a manual position names the broker or venue it is held at",
        };
      }
      const rows = snapshot.manualPositions as Record<string, unknown>[];
      const clash = rows.some(
        (row) =>
          row.id !== input.id &&
          row.symbol === symbol &&
          String(row.venue).toLowerCase() === venue.toLowerCase(),
      );
      if (clash) {
        return {
          ok: false,
          reason: `${symbol} at ${venue} is already on the list`,
        };
      }
      const now = new Date().toISOString();
      const fields = {
        symbol,
        name: blankToNull(input.name),
        venue,
        asset_class: input.assetClass,
        quantity: input.quantity === null ? null : String(input.quantity),
        note: blankToNull(input.note),
        updated_at: now,
      };
      if (input.id === null) {
        rows.push({ id: fixtureId(), created_at: now, ...fields });
        return { ok: true };
      }
      const row = rows.find((one) => one.id === input.id);
      if (row === undefined) {
        return {
          ok: false,
          reason: `manual position ${input.id} is not an active row`,
        };
      }
      Object.assign(row, fields);
      return { ok: true };
    },
    async retireManualPosition(id) {
      const rows = snapshot.manualPositions as Record<string, unknown>[];
      const at = rows.findIndex((one) => one.id === id);
      if (at < 0) {
        return { ok: false, reason: `manual position ${id} is not an active row` };
      }
      rows.splice(at, 1);
      return { ok: true };
    },
    async saveWatchTicker(input) {
      const symbol = normaliseSymbol(input.symbol);
      if (!SYMBOL_PATTERN.test(symbol)) {
        return { ok: false, reason: symbolRefusal(input.symbol) };
      }
      const rows = snapshot.watchTickers as Record<string, unknown>[];
      if (rows.some((row) => row.id !== input.id && row.symbol === symbol)) {
        return { ok: false, reason: `${symbol} is already on the watch list` };
      }
      const now = new Date().toISOString();
      const fields = {
        symbol,
        name: blankToNull(input.name),
        asset_class: input.assetClass,
        note: blankToNull(input.note),
        updated_at: now,
      };
      if (input.id === null) {
        rows.push({ id: fixtureId(), created_at: now, ...fields });
        return { ok: true };
      }
      const row = rows.find((one) => one.id === input.id);
      if (row === undefined) {
        return {
          ok: false,
          reason: `watched ticker ${input.id} is not an active row`,
        };
      }
      Object.assign(row, fields);
      return { ok: true };
    },
    async retireWatchTicker(id) {
      const rows = snapshot.watchTickers as Record<string, unknown>[];
      const at = rows.findIndex((one) => one.id === id);
      if (at < 0) {
        return { ok: false, reason: `watched ticker ${id} is not an active row` };
      }
      rows.splice(at, 1);
      return { ok: true };
    },
  };
}

/** The functions' own refusal for a symbol they will not store. */
function symbolRefusal(symbol: string): string {
  return `a symbol is letters, digits and . ^ = / : - only, up to 24 characters, not ${symbol}`;
}

/** A fresh id for a fixture row, as `gen_random_uuid()` would give one. */
function fixtureId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `fixture-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function supabaseSource(
  client: SupabaseClient,
  /** Called once when a refusal means the session itself has ended. */
  onExpiredSession: () => void = () => {},
): Source {
  async function fromSchema<T>(
    schema: "macro_mart" | "macro_quality",
    table: string,
    validator: RowValidator<T>,
    optional: boolean,
  ): Promise<Reading<T>> {
    const name = `${schema}.${table}`;
    try {
      // `select("*")` rather than a column list on purpose: a project whose
      // migration predates an optional column then reads it as null through the
      // validator, instead of the whole query failing with "column does not
      // exist" and blanking a panel over a subtitle.
      const { data, error } = await client
        .schema(schema)
        .from(table)
        .select("*");
      if (error) {
        if (isExpiredSession(error)) onExpiredSession();
        if (optional && isMissingRelation(error)) return empty<T>(null);
        return empty<T>(transportSentence(name, error));
      }
      const reading = readingOf(data, validator);
      return reading.transportError === null
        ? reading
        : empty(`${name} answered oddly: ${reading.transportError}`);
    } catch (cause) {
      return empty(thrownSentence(name, cause));
    }
  }

  return {
    scope: "supabase",
    martTable: (table, validator) =>
      fromSchema("macro_mart", table, validator, false),
    qualityTable: (table, validator) =>
      fromSchema("macro_quality", table, validator, false),
    optionalTable: (table, validator) =>
      fromSchema("macro_mart", table, validator, true),
    async filteredTable<T2>(
      table: string,
      column: string,
      value: string,
      validator: RowValidator<T2>,
    ): Promise<Reading<T2>> {
      const name = `macro_mart.${table}`;
      try {
        const { data, error } = await client
          .schema("macro_mart")
          .from(table)
          .select("*")
          .eq(column, value);
        if (error) {
          if (isExpiredSession(error)) onExpiredSession();
          // Added by migration 0012, so an older project has no such view.
          // One row's disclosure then says so; the page is unaffected.
          if (isMissingRelation(error)) return empty<T2>(null);
          return empty(transportSentence(name, error));
        }
        return readingOf(data, validator);
      } catch (cause) {
        return empty(thrownSentence(name, cause));
      }
    },
    async sinceTable<T2>(
      table: string,
      column: string,
      since: string,
      validator: RowValidator<T2>,
    ): Promise<Reading<T2>> {
      const name = `macro_mart.${table}`;
      try {
        const { data, error } = await client
          .schema("macro_mart")
          .from(table)
          .select("*")
          .gte(column, since);
        if (error) {
          if (isExpiredSession(error)) onExpiredSession();
          // Added by migration 0012, so an older project has no such view.
          // The sparklines are then absent; nothing else on the page is.
          if (isMissingRelation(error)) return empty<T2>(null);
          return empty(transportSentence(name, error));
        }
        return readingOf(data, validator);
      } catch (cause) {
        return empty(thrownSentence(name, cause));
      }
    },
    async calibration() {
      const name = "macro_mart.calibration";
      try {
        const { data, error } = await client
          .schema("macro_mart")
          .from("calibration")
          .select("*")
          .eq("id", 1);
        if (error) {
          if (isExpiredSession(error)) onExpiredSession();
          return {
            row: null,
            transportError: transportSentence(name, error),
            rejected: [],
          };
        }
        const checked = readSingleCalibration(data);
        return checked.ok
          ? { row: checked.value, transportError: null, rejected: [] }
          : { row: null, transportError: null, rejected: [checked.reason] };
      } catch (cause) {
        return {
          row: null,
          transportError: thrownSentence(name, cause),
          rejected: [],
        };
      }
    },
    async requestRefresh(reason) {
      return call("macro_mart.request_refresh", () =>
        client
          .schema("macro_mart")
          .rpc("request_refresh", { p_reason: reason }),
      );
    },
    async decideProposal(id, decision) {
      return call("macro_mart.decide_proposal", () =>
        client
          .schema("macro_mart")
          .rpc("decide_proposal", { p_id: id, p_decision: decision }),
      );
    },
    async saveManualPosition(input) {
      return call("macro_mart.save_manual_position", () =>
        client.schema("macro_mart").rpc("save_manual_position", {
          p_id: input.id,
          p_symbol: normaliseSymbol(input.symbol),
          p_name: blankToNull(input.name),
          p_venue: input.venue.trim(),
          p_asset_class: input.assetClass,
          p_quantity: input.quantity,
          p_note: blankToNull(input.note),
        }),
      );
    },
    async retireManualPosition(id) {
      return call("macro_mart.retire_manual_position", () =>
        client.schema("macro_mart").rpc("retire_manual_position", { p_id: id }),
      );
    },
    async saveWatchTicker(input) {
      return call("macro_mart.save_watch_ticker", () =>
        client.schema("macro_mart").rpc("save_watch_ticker", {
          p_id: input.id,
          p_symbol: normaliseSymbol(input.symbol),
          p_name: blankToNull(input.name),
          p_asset_class: input.assetClass,
          p_note: blankToNull(input.note),
        }),
      );
    },
    async retireWatchTicker(id) {
      return call("macro_mart.retire_watch_ticker", () =>
        client.schema("macro_mart").rpc("retire_watch_ticker", { p_id: id }),
      );
    },
  };

  async function call(
    name: string,
    send: () => PromiseLike<{
      error: { message: string; code?: string; hint?: string | null } | null;
    }>,
  ): Promise<WriteResult> {
    try {
      const { error } = await send();
      if (error) {
        if (isExpiredSession(error)) onExpiredSession();
        return { ok: false, reason: refusalSentence(error, name) };
      }
      return { ok: true };
    } catch (cause) {
      return { ok: false, reason: thrownSentence(name, cause) };
    }
  }
}

/**
 * A write's refusal as a sentence the operator can act on.
 *
 * The functions raise their own plain sentences (SQLSTATE P0001), and those
 * pass through, with a capital and a full stop. Anything else is Postgres
 * speaking for itself, which nobody at the desk should have to read: a unique
 * index firing is "already on the list", a check constraint is "not allowed".
 * This is the fallback under the functions' own checks, not a replacement for
 * them, so it says what kind of refusal it was rather than guessing which
 * field caused it.
 */
export function refusalSentence(
  error: { message: string; code?: string },
  /** The function that refused, so a constraint names only its own fields. */
  fn = "",
): string {
  switch (error.code) {
    case undefined:
    case "":
    case "P0001":
      return asSentence(error.message);
    case "23505":
      return "That one is already on the list.";
    case "23514":
      return fn.endsWith("manual_position")
        ? "One of the values is not allowed. A symbol is letters, digits and . ^ = / : - only, up to 24 characters, and a broker name is 1 to 40 characters."
        : "One of the values is not allowed. A symbol is letters, digits and . ^ = / : - only, up to 24 characters.";
    case "23502":
      return "A required field is empty.";
    case "22001":
      return "One of the entries is too long. Shorten it and try again.";
    case "42501":
      return "You are not allowed to change this list. Try signing in again.";
    default:
      if (error.code.startsWith("PGRST")) {
        return `The change was not saved: the database could not be reached as expected (${error.code}). Try again in a minute.`;
      }
      return `The change was not saved: the database refused it (code ${error.code}).`;
  }
}

function asSentence(text: string): string {
  const trimmed = text.trim();
  if (trimmed === "") return "The change was not saved.";
  const capped = trimmed.charAt(0).toUpperCase() + trimmed.slice(1);
  return /[.!?]$/.test(capped) ? capped : `${capped}.`;
}

/* ------------------------------------------------------------- the fetchers */

export const fetchSnapshot = (source: Source): Promise<Reading<SnapshotRow>> =>
  source.martTable("snapshot_row", snapshotRow);

export const fetchConditions = (
  source: Source,
): Promise<Reading<ConditionState>> =>
  source.martTable("condition_state", conditionState);

export const fetchPhaseScores = (
  source: Source,
): Promise<Reading<PhaseScore>> => source.martTable("phase_score", phaseScore);

export const fetchScenarios = (
  source: Source,
): Promise<Reading<ScenarioState>> =>
  source.martTable("scenario_state", scenarioState);

export const fetchTargets = (
  source: Source,
): Promise<Reading<ScenarioTargetState>> =>
  source.martTable("scenario_target_state", scenarioTargetState);

export const fetchSetups = (source: Source): Promise<Reading<SetupState>> =>
  source.martTable("setup_state", setupState);

export const fetchCatalysts = (
  source: Source,
): Promise<Reading<CatalystWindow>> =>
  source.martTable("catalyst_window", catalystWindow);

export const fetchDrift = (source: Source): Promise<Reading<Drift>> =>
  source.optionalTable("drift", drift);

export const fetchRunRequests = (
  source: Source,
): Promise<Reading<RunRequest>> =>
  source.optionalTable("run_request_state", runRequest);

export const fetchTailRisks = (source: Source): Promise<Reading<TailRisk>> =>
  source.optionalTable("tail_risk_state", tailRisk);

export const fetchWatchlist = (
  source: Source,
): Promise<Reading<WatchlistChange>> =>
  source.optionalTable("watchlist_change_state", watchlistChange);

/*
 * The desk feeds, since migrations 20260924000300 to 20260924000500. Read
 * through the optional path, so a project that has not run them keeps the old
 * Catalysts page (tail risks without a trail, the watchlist moves, no
 * freshness line) instead of failing the route.
 */

export const fetchTailRiskHistory = (
  source: Source,
): Promise<Reading<TailRiskCheck>> =>
  source.optionalTable("tail_risk_history", tailRiskCheck);

export const fetchHoldings = (
  source: Source,
): Promise<Reading<HoldingWatch>> =>
  source.optionalTable("holding_watch_state", holdingWatch);

export const fetchHoldingEvents = (
  source: Source,
): Promise<Reading<HoldingEvent>> =>
  source.optionalTable("holding_event_state", holdingEvent);

export const fetchManualPositions = (
  source: Source,
): Promise<Reading<ManualPosition>> =>
  source.optionalTable("manual_position_state", manualPosition);

export const fetchWatchTickers = (
  source: Source,
): Promise<Reading<WatchTicker>> =>
  source.optionalTable("watch_ticker_state", watchTicker);

export const fetchFeeds = (source: Source): Promise<Reading<FeedFreshness>> =>
  source.optionalTable("feed_freshness", feedFreshness);

export const fetchScenarioSetHistory = (
  source: Source,
): Promise<Reading<ScenarioSetHistory>> =>
  source.martTable("scenario_set_history", scenarioSetHistory);

/**
 * Added by migration 0014, so a project on an older schema has no such view.
 * Read through the optional path: `/history` then falls back to saying the
 * three scenarios are unreadable, which is what it did before the view existed.
 */
export const fetchScenarioHistory = (
  source: Source,
): Promise<Reading<ScenarioHistory>> =>
  source.optionalTable("scenario_history", scenarioHistory);

export const fetchPhaseHistory = (
  source: Source,
): Promise<Reading<PhaseCallHistory>> =>
  source.martTable("phase_call_history", phaseCallHistory);

/**
 * Added by migration 0012, so a project on an older schema has no such view.
 * Read through the optional path: `/history` then shows the set and the phase
 * calls and says the setups are unavailable, rather than failing the route.
 */
/**
 * One series' observations, newest first.
 *
 * Called only when a snapshot row's disclosure is opened, and cached per
 * series, so a row opened twice is read once.
 */
export const fetchObservationHistory = (
  source: Source,
  seriesKey: string,
): Promise<Reading<ObservationHistory>> =>
  source.filteredTable(
    "observation_history",
    "series_key",
    seriesKey,
    observationHistory,
  );

/**
 * Every series' observations from `since` onward, in one read.
 *
 * The snapshot's trend column, and nothing else: one request for the whole
 * page rather than one per row on open. `since` is the evaluation date minus
 * thirty days, so the window is anchored to the reading on screen rather than
 * to the wall clock, and a page held open overnight does not quietly redraw
 * against a different span.
 */
export const fetchRecentObservations = (
  source: Source,
  since: string,
): Promise<Reading<ObservationHistory>> =>
  source.sinceTable("observation_history", "asof", since, observationHistory);

export const fetchSetupHistory = (
  source: Source,
): Promise<Reading<SetupHistory>> =>
  source.optionalTable("setup_history", setupHistory);

export const fetchFindings = (source: Source): Promise<Reading<Finding>> =>
  source.qualityTable("finding", finding);

export const fetchGaps = (source: Source): Promise<Reading<Gap>> =>
  source.qualityTable("gap", gap);

export const fetchRuns = (source: Source): Promise<Reading<IngestRun>> =>
  source.qualityTable("ingest_run", ingestRun);

/* -- the seven relations `docs/CONTRACT.md` does not define yet ------------ */

export const fetchPhaseCalls = (
  source: Source,
): Promise<Reading<PhaseCallDetail>> =>
  source.optionalTable("phase_call_detail", phaseCallDetail);

export const fetchScenarioSet = (
  source: Source,
): Promise<Reading<ScenarioSetState>> =>
  source.optionalTable("scenario_set_state", scenarioSetState);

export const fetchStrategies = (
  source: Source,
): Promise<Reading<StrategyState>> =>
  source.optionalTable("strategy_state", strategyState);

export const fetchWeekGrades = (source: Source): Promise<Reading<WeekGrade>> =>
  source.optionalTable("week_grade", weekGrade);

export const fetchSetupGrades = (
  source: Source,
): Promise<Reading<SetupGrade>> =>
  source.optionalTable("setup_grade", setupGrade);

export const fetchRules = (source: Source): Promise<Reading<ScenarioRule>> =>
  source.optionalTable("scenario_rule", scenarioRule);

export const fetchCalibration = (
  source: Source,
): Promise<SingleReading<Calibration>> => source.calibration();

/* --------------------------------------------------------------- the clock */

/**
 * The latest evaluation stamp across a set of rows.
 *
 * Latest rather than earliest: every `macro_mart` table is recomputed by one
 * evaluate step, so they should all carry the same stamp, and the one that
 * disagrees is the one that failed to recompute. Taking the latest makes that
 * visible as a spread rather than hiding it behind the oldest.
 */
export function latestStamp(
  stamps: readonly (string | null | undefined)[],
): string | null {
  let best: string | null = null;
  let bestMs = Number.NEGATIVE_INFINITY;
  for (const stamp of stamps) {
    if (typeof stamp !== "string") continue;
    const ms = Date.parse(stamp);
    if (!Number.isFinite(ms) || ms <= bestMs) continue;
    best = stamp;
    bestMs = ms;
  }
  return best;
}
