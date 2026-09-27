/**
 * The Desk's arithmetic, over rows the worker already computed.
 *
 * Nothing here scores anything. Drift is the worker's (`macro_mart.drift`),
 * every band's `holding` and `current` are the worker's, every catalyst's
 * clause references are the worker's. What this module does is *select and
 * join*: which bands sit nearest their edge, which dated events decide which
 * scenario, which run is waiting on the operator. Those are orderings of
 * computed facts, not new facts, which is the line `lib/data.ts` draws for
 * the whole app.
 */

import type {
  CatalystWindow,
  ConditionState,
  ObservationHistory,
  RunRequest,
  ScenarioState,
  ScenarioTargetState,
  SnapshotRow,
} from "./contract";

/* ------------------------------------------------------------- band edges */

export interface Edge {
  targetId: string;
  scenarioOrdinal: number;
  label: string;
  seriesKey: string | null;
  current: number;
  /** The edge the reading is nearest to, which is the one that decides it. */
  edge: number;
  side: "floor" | "ceiling";
  holding: boolean;
  /** Distance to that edge as a share of the current level, in percent. */
  pctAway: number;
}

/**
 * Every scored band, ordered by how close its reading sits to the edge that
 * would change it.
 *
 * A band that holds is measured to the edge it would leave by; one that does
 * not is measured to the edge it would enter by. Both are a percentage of the
 * current level so an S&P 36 points from its ceiling and a VIX 0.8 from its
 * floor can be ranked on one list. An unevaluable band (holding null) has no
 * distance and is left out rather than ranked as far.
 */
export function nearestEdges(
  targets: readonly ScenarioTargetState[],
  scenarios: readonly ScenarioState[],
  snapshot: readonly SnapshotRow[],
  limit = 4,
): Edge[] {
  const ordinal = new Map(scenarios.map((s) => [s.scenarioId, s.ordinal]));
  const keyOf = new Map(snapshot.map((row) => [row.seriesId, row.key]));
  const out: Edge[] = [];
  for (const target of targets) {
    if (target.kind === "manual" || target.holding === null) continue;
    const current = target.current;
    if (current === null || current === 0) continue;
    const candidates: { edge: number; side: "floor" | "ceiling" }[] = [];
    if (target.lo !== null) candidates.push({ edge: target.lo, side: "floor" });
    if (target.hi !== null)
      candidates.push({ edge: target.hi, side: "ceiling" });
    if (candidates.length === 0) continue;
    const nearest = candidates.reduce((best, one) =>
      Math.abs(current - one.edge) < Math.abs(current - best.edge) ? one : best,
    );
    out.push({
      targetId: target.targetId,
      scenarioOrdinal: ordinal.get(target.scenarioId) ?? 0,
      label: target.label,
      seriesKey:
        target.seriesId === null ? null : (keyOf.get(target.seriesId) ?? null),
      current,
      edge: nearest.edge,
      side: nearest.side,
      holding: target.holding,
      pctAway: (Math.abs(current - nearest.edge) / Math.abs(current)) * 100,
    });
  }
  // At most two per scenario, so the list says what is close to changing
  // across the call rather than listing four edges of the least likely leg.
  const perScenario = new Map<number, number>();
  return out
    .sort((a, b) => a.pctAway - b.pctAway)
    .filter((edge) => {
      const seen = perScenario.get(edge.scenarioOrdinal) ?? 0;
      perScenario.set(edge.scenarioOrdinal, seen + 1);
      return seen < 2;
    })
    .slice(0, limit);
}

/* ------------------------------------------------------ the next settlers */

export interface Settler {
  catalystId: string;
  name: string;
  startsAt: string | null;
  daysOut: number | null;
  /** Whether `startsAt` is a time, or only a day stored at midnight. */
  exact: boolean;
  /** Which scenarios carry a clause bound to this catalyst, as S-ordinals. */
  scenarios: number[];
}

/**
 * The dated events inside `days` that decide a scenario clause, soonest first.
 *
 * `referenced_by` holds trigger-clause ids; a clause's owner is its scenario,
 * and the scenario's ordinal is what the Desk prints. A catalyst no clause
 * names is left to the Catalysts page: it is on the calendar, but it does not
 * settle anything in this call.
 */
export function nextSettlers(
  catalysts: readonly CatalystWindow[],
  conditions: readonly ConditionState[],
  scenarios: readonly ScenarioState[],
  days = 10,
  limit = 5,
): Settler[] {
  const ordinal = new Map(scenarios.map((s) => [s.scenarioId, s.ordinal]));
  const owner = new Map(
    conditions
      .filter((c) => c.ownerKind === "trigger")
      .map((c) => [c.conditionId, c.ownerId]),
  );
  return catalysts
    .filter((c) => c.daysOut !== null && c.daysOut >= 0 && c.daysOut <= days)
    .map((c) => ({
      catalystId: c.catalystId,
      name: c.name,
      startsAt: c.startsAt,
      daysOut: c.daysOut,
      exact: c.datePrecision === "exact",
      scenarios: [
        ...new Set(
          c.referencedBy
            .map((id) => ordinal.get(owner.get(id) ?? ""))
            .filter((n): n is number => n !== undefined),
        ),
      ].sort(),
    }))
    .filter((c) => c.scenarios.length > 0)
    .sort((a, b) => (a.startsAt ?? "").localeCompare(b.startsAt ?? ""))
    .slice(0, limit);
}

/* ------------------------------------------------------------- the queue */

/** The run the operator must decide, if any: proposed and not yet decided. */
export function awaitingDecision(
  runs: readonly RunRequest[],
): RunRequest | null {
  return (
    runs.find((run) => run.status === "proposed" && run.decision === null) ??
    null
  );
}

/** The refresh in flight, if any: queued, running, or decided and not yet applied. */
export function refreshInFlight(
  runs: readonly RunRequest[],
): RunRequest | null {
  return (
    runs.find(
      (run) =>
        run.kind === "amend" &&
        (run.status === "queued" ||
          run.status === "running" ||
          (run.status === "proposed" && run.decision !== null)),
    ) ?? null
  );
}

/** The newest finished run of each kind, for the queue's one-line history. */
export function lastOf(
  runs: readonly RunRequest[],
  kind: RunRequest["kind"],
): RunRequest | null {
  return runs.find((run) => run.kind === kind) ?? null;
}

/* --------------------------------------------------------- where rows go */

/**
 * Whether a snapshot row lives on the Catalysts page rather than on Data.
 *
 * The auction results are dated events with a figure attached, read against a
 * calendar, and the prose Macro & Fiscal rows are policy state; both are what
 * the Catalysts page is for. The numeric macro prints (CPI, payrolls, retail
 * sales) stay on Data, where every other figure a condition can read is.
 */
export function belongsOnCatalysts(row: SnapshotRow): boolean {
  if (row.key.startsWith("auction_")) return true;
  return row.tableGroup === "Macro & Fiscal State" && row.valueKind === "text";
}

/* -------------------------------------------------------- unusual moves */

export interface Mover {
  key: string;
  label: string;
  display: string | null;
  delta: number;
  deltaLabel: string | null;
  /** The move in standard deviations of this series' own daily changes. */
  sigma: number;
}

/**
 * Today's moves, ranked by how unusual each is for its own series.
 *
 * The move is the worker's `delta_native`. The yardstick is the standard
 * deviation of the series' day-to-day changes over the thirty-day trend
 * window the page already holds, so a 1.2-point VIX move and a 90-point S&P
 * move are compared on one scale. Only daily-cadence rows qualify: a monthly
 * print's change against a thirty-day window of one or two points has no
 * yardstick, and ranking it would be inventing one. Fewer than eight changes
 * is too few to call anything unusual.
 */
export function unusualMoves(
  rows: readonly SnapshotRow[],
  trends: readonly ObservationHistory[],
  limit = 6,
  /** Below one standard deviation a move is the series' ordinary day. */
  floor = 1,
): Mover[] {
  const byKey = new Map<string, ObservationHistory[]>();
  for (const point of trends) {
    const held = byKey.get(point.seriesKey);
    if (held === undefined) byKey.set(point.seriesKey, [point]);
    else held.push(point);
  }
  const out: Mover[] = [];
  const keys = new Set(rows.map((row) => row.key));
  for (const row of rows) {
    if (row.valueKind !== "numeric" || row.deltaNative === null) continue;
    // A future whose cash index is on the board is the same move twice.
    if (row.key.endsWith("_front") && keys.has(row.key.replace(/_front$/, "_cash"))) {
      continue;
    }
    if (row.deltaNativeLabel !== "1d") continue;
    const points = (byKey.get(row.key) ?? [])
      .filter((p) => p.value !== null)
      .sort((a, b) => a.asof.localeCompare(b.asof));
    const changes: number[] = [];
    for (let i = 1; i < points.length; i += 1) {
      changes.push(points[i]!.value! - points[i - 1]!.value!);
    }
    if (changes.length < 8) continue;
    const move =
      row.value !== null && points.length > 0 ? lastChange(points) : null;
    if (move === null) continue;
    const sd = stdev(changes);
    if (sd === 0) continue;
    out.push({
      key: row.key,
      label: row.label,
      display: row.display,
      delta: row.deltaNative,
      deltaLabel: row.deltaNativeLabel,
      sigma: Math.abs(move) / sd,
    });
  }
  return out
    .filter((one) => one.sigma >= floor)
    .sort((a, b) => b.sigma - a.sigma)
    .slice(0, limit);
}

function lastChange(points: readonly ObservationHistory[]): number | null {
  if (points.length < 2) return null;
  return points[points.length - 1]!.value! - points[points.length - 2]!.value!;
}

function stdev(values: readonly number[]): number {
  const mean = values.reduce((a, b) => a + b, 0) / values.length;
  const variance =
    values.reduce((sum, v) => sum + (v - mean) ** 2, 0) / (values.length - 1);
  return Math.sqrt(variance);
}
