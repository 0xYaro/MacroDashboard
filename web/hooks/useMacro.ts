"use client";

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";

import type {
  CatalystWindow,
  ConditionState,
  Drift,
  FeedFreshness,
  Finding,
  Gap,
  HoldingEvent,
  HoldingWatch,
  IngestRun,
  ObservationHistory,
  PhaseCallDetail,
  PhaseScore,
  RunRequest,
  ScenarioSetState,
  ScenarioState,
  ScenarioTargetState,
  SetupState,
  SnapshotRow,
  StrategyState,
  TailRisk,
  TailRiskCheck,
  WatchlistChange,
} from "@/lib/contract";
import {
  daysBefore,
  fetchCatalysts,
  fetchConditions,
  fetchDrift,
  fetchFeeds,
  fetchFindings,
  fetchGaps,
  fetchHoldingEvents,
  fetchHoldings,
  fetchPhaseCalls,
  fetchPhaseScores,
  fetchRecentObservations,
  fetchRunRequests,
  fetchRuns,
  fetchScenarioSet,
  fetchScenarios,
  fetchSetups,
  fetchSnapshot,
  fetchStrategies,
  fetchTailRiskHistory,
  fetchTailRisks,
  fetchTargets,
  fetchWatchlist,
  keys,
  latestStamp,
  type Reading as TableReading,
} from "@/lib/data";
import { readReading, type Reading } from "@/lib/freshness";
import { useSource } from "./useSource";

/**
 * The whole reading, assembled once, for the Desk, Data and Catalysts pages.
 *
 * Every relation is its own query so a slow scenario recompute cannot hold up
 * the snapshot, and one failing relation cannot blank the others. They are
 * assembled here rather than in the views so the freshness verdict is computed
 * once, from the same rows the views render, and no view can reach a figure the
 * verdict says it may not show.
 *
 * **Every query is keyed on the source scope alone.** That is what makes `[`
 * and `]` free: the four views share these cache entries, so walking between
 * them touches no query, issues no request and cannot move a clock.
 */

export interface MacroData {
  reading: Reading;
  snapshot: readonly SnapshotRow[];
  conditions: readonly ConditionState[];
  phaseScores: readonly PhaseScore[];
  phaseCalls: readonly PhaseCallDetail[];
  scenarioSet: ScenarioSetState | null;
  scenarios: readonly ScenarioState[];
  targets: readonly ScenarioTargetState[];
  setups: readonly SetupState[];
  strategies: readonly StrategyState[];
  catalysts: readonly CatalystWindow[];
  findings: readonly Finding[];
  gaps: readonly Gap[];
  /**
   * Thirty days of observations for every series, for the snapshot's trend
   * column. Empty until the snapshot has answered, because the window is
   * anchored to the newest as-of in it.
   */
  trends: readonly ObservationHistory[];
  runs: readonly IngestRun[];
  /** What has moved since the active call, as the worker scored it. Null before it has. */
  drift: Drift | null;
  /** The run queue, newest first: the Refresh control and the proposal review read it. */
  runRequests: readonly RunRequest[];
  tailRisks: readonly TailRisk[];
  /** Every check of every tail risk over 180 days, for the escalation trail. */
  tailRiskHistory: readonly TailRiskCheck[];
  /** The old watchlist moves, read only while the holdings watch is empty. */
  watchlist: readonly WatchlistChange[];
  /** The latest holdings watch, one row per watched name. */
  holdings: readonly HoldingWatch[];
  holdingEvents: readonly HoldingEvent[];
  /** Where each of the three desk feeds last came from, and when. */
  feeds: readonly FeedFreshness[];
  /** The oldest and newest observation date in the snapshot. */
  asofSpread: { oldest: string; newest: string } | null;
  loading: boolean;
}

/** A stable reference, so an unanswered query does not defeat the memo. */
const NONE = { rows: [], rejected: [], transportError: null } as const;

/** The sparkline's span, in days. Thirty is the journal's own trend window. */
const TREND_DAYS = 30;

const RELATIONS = [
  "macro_mart.snapshot_row",
  "macro_mart.condition_state",
  "macro_mart.phase_score",
  "macro_mart.scenario_state",
  "macro_mart.setup_state",
];

export function useMacro(): MacroData {
  const { source, unavailable } = useSource();
  const scope = source?.scope ?? "none";
  const enabled = source !== null;
  const shared = { enabled, retry: 1 } as const;

  const snapshot = useQuery({
    ...shared,
    queryKey: keys.snapshot(scope),
    queryFn: () => fetchSnapshot(source!),
  });
  const conditions = useQuery({
    ...shared,
    queryKey: keys.conditions(scope),
    queryFn: () => fetchConditions(source!),
  });
  const phaseScores = useQuery({
    ...shared,
    queryKey: keys.phaseScores(scope),
    queryFn: () => fetchPhaseScores(source!),
  });
  const phaseCalls = useQuery({
    ...shared,
    queryKey: keys.phaseCalls(scope),
    queryFn: () => fetchPhaseCalls(source!),
  });
  const scenarioSet = useQuery({
    ...shared,
    queryKey: keys.scenarioSet(scope),
    queryFn: () => fetchScenarioSet(source!),
  });
  const scenarios = useQuery({
    ...shared,
    queryKey: keys.scenarios(scope),
    queryFn: () => fetchScenarios(source!),
  });
  const targets = useQuery({
    ...shared,
    queryKey: keys.targets(scope),
    queryFn: () => fetchTargets(source!),
  });
  const setups = useQuery({
    ...shared,
    queryKey: keys.setups(scope),
    queryFn: () => fetchSetups(source!),
  });
  const strategies = useQuery({
    ...shared,
    queryKey: keys.strategy(scope),
    queryFn: () => fetchStrategies(source!),
  });
  const catalysts = useQuery({
    ...shared,
    queryKey: keys.catalysts(scope),
    queryFn: () => fetchCatalysts(source!),
  });
  const findings = useQuery({
    ...shared,
    queryKey: keys.findings(scope),
    queryFn: () => fetchFindings(source!),
  });
  const gaps = useQuery({
    ...shared,
    queryKey: keys.gaps(scope),
    queryFn: () => fetchGaps(source!),
  });
  const runs = useQuery({
    ...shared,
    queryKey: keys.runs(scope),
    queryFn: () => fetchRuns(source!),
  });
  const drift = useQuery({
    ...shared,
    queryKey: keys.drift(scope),
    queryFn: () => fetchDrift(source!),
  });
  const runRequests = useQuery({
    ...shared,
    queryKey: keys.runRequests(scope),
    queryFn: () => fetchRunRequests(source!),
    // The one relation that moves between runs. While a Refresh is queued or
    // running, or a decision is waiting for the runner to apply it, the Desk
    // re-reads the queue every twenty seconds; otherwise it keeps the page's
    // own cadence and adds no traffic at all.
    refetchInterval: (query) =>
      (query.state.data?.rows ?? []).some(
        (row) =>
          row.status === "queued" ||
          row.status === "running" ||
          (row.status === "proposed" && row.decision !== null),
      )
        ? 20_000
        : false,
  });
  const tailRisks = useQuery({
    ...shared,
    queryKey: keys.tailRisks(scope),
    queryFn: () => fetchTailRisks(source!),
  });
  const watchlist = useQuery({
    ...shared,
    queryKey: keys.watchlist(scope),
    queryFn: () => fetchWatchlist(source!),
  });
  const tailRiskHistory = useQuery({
    ...shared,
    queryKey: keys.tailRiskHistory(scope),
    queryFn: () => fetchTailRiskHistory(source!),
  });
  const holdings = useQuery({
    ...shared,
    queryKey: keys.holdings(scope),
    queryFn: () => fetchHoldings(source!),
  });
  const holdingEvents = useQuery({
    ...shared,
    queryKey: keys.holdingEvents(scope),
    queryFn: () => fetchHoldingEvents(source!),
  });
  const feeds = useQuery({
    ...shared,
    queryKey: keys.feeds(scope),
    queryFn: () => fetchFeeds(source!),
  });

  // The window the snapshot's sparklines are drawn over: thirty days back from
  // the newest as-of the snapshot carries, which is the evaluation date. Read
  // from the answered snapshot rather than from the clock, so the span a
  // sparkline covers is the span the table above it is dated to, and a page
  // left open overnight does not silently redraw against a different one.
  const newestAsof = useMemo(() => {
    const asofs = (snapshot.data?.rows ?? [])
      .map((row) => row.asof)
      .filter((value): value is string => value !== null);
    return asofs.length === 0 ? null : asofs.reduce((a, b) => (a > b ? a : b));
  }, [snapshot.data]);
  const since = newestAsof === null ? null : daysBefore(newestAsof, TREND_DAYS);
  const trends = useQuery({
    // One read for the whole page, and only once there is a date to anchor it
    // to. A sparkline is an ornament on a figure; it never delays the figure.
    enabled: enabled && since !== null,
    retry: 1,
    queryKey: keys.recentObservations(scope, since ?? "none"),
    queryFn: () => fetchRecentObservations(source!, since!),
  });

  return useMemo<MacroData>(() => {
    const snapshotReading: TableReading<SnapshotRow> = snapshot.data ?? NONE;
    const conditionsReading: TableReading<ConditionState> =
      conditions.data ?? NONE;
    const scoresReading: TableReading<PhaseScore> = phaseScores.data ?? NONE;
    const scenariosReading: TableReading<ScenarioState> =
      scenarios.data ?? NONE;
    const setupsReading: TableReading<SetupState> = setups.data ?? NONE;

    // Every named reason the app is holding, in one list, so the banner can say
    // how many rows are missing and which. Only the five relations the reading
    // is rated over contribute; an optional relation that is not there yet is
    // a missing section, not a malformed row.
    const rejected = [
      ...snapshotReading.rejected,
      ...conditionsReading.rejected,
      ...scoresReading.rejected,
      ...scenariosReading.rejected,
      ...setupsReading.rejected,
    ];

    // The first envelope fault wins. Order matters only in that the snapshot is
    // the relation the operator will check first.
    const transportError =
      unavailable ??
      snapshotReading.transportError ??
      conditionsReading.transportError ??
      scoresReading.transportError ??
      scenariosReading.transportError ??
      setupsReading.transportError;

    const answered =
      !enabled ||
      (!snapshot.isPending &&
        !conditions.isPending &&
        !phaseScores.isPending &&
        !scenarios.isPending &&
        !setups.isPending);

    const evaluatedAt = latestStamp([
      ...snapshotReading.rows.map((row) => row.updatedAt),
      ...conditionsReading.rows.map((row) => row.evaluatedAt),
      ...scoresReading.rows.map((row) => row.evaluatedAt),
    ]);

    const asofs = snapshotReading.rows
      .map((row) => row.asof)
      .filter((value): value is string => value !== null)
      .sort();
    const asofSpread =
      asofs.length === 0
        ? null
        : { oldest: asofs[0]!, newest: asofs[asofs.length - 1]! };

    const reading = readReading({
      states: snapshotReading.rows.map((row) => row.freshness),
      rejected,
      transportError,
      evaluatedAt,
      answered,
      subject: "the snapshot",
      relations: RELATIONS,
    });

    return {
      reading,
      snapshot: snapshotReading.rows,
      conditions: conditionsReading.rows,
      phaseScores: scoresReading.rows,
      phaseCalls: phaseCalls.data?.rows ?? [],
      scenarioSet: scenarioSet.data?.rows[0] ?? null,
      scenarios: scenariosReading.rows,
      targets: targets.data?.rows ?? [],
      setups: setupsReading.rows,
      strategies: strategies.data?.rows ?? [],
      catalysts: catalysts.data?.rows ?? [],
      findings: findings.data?.rows ?? [],
      gaps: gaps.data?.rows ?? [],
      trends: trends.data?.rows ?? [],
      runs: runs.data?.rows ?? [],
      drift: drift.data?.rows[0] ?? null,
      runRequests: [...(runRequests.data?.rows ?? [])].sort(
        (a, b) => b.id - a.id,
      ),
      tailRisks: tailRisks.data?.rows ?? [],
      tailRiskHistory: tailRiskHistory.data?.rows ?? [],
      watchlist: watchlist.data?.rows ?? [],
      holdings: holdings.data?.rows ?? [],
      holdingEvents: holdingEvents.data?.rows ?? [],
      feeds: feeds.data?.rows ?? [],
      asofSpread,
      loading: enabled && !answered,
    };
  }, [
    catalysts.data,
    conditions.data,
    conditions.isPending,
    drift.data,
    enabled,
    feeds.data,
    findings.data,
    gaps.data,
    holdingEvents.data,
    holdings.data,
    phaseCalls.data,
    phaseScores.data,
    phaseScores.isPending,
    runRequests.data,
    runs.data,
    scenarioSet.data,
    scenarios.data,
    scenarios.isPending,
    setups.data,
    setups.isPending,
    snapshot.data,
    snapshot.isPending,
    strategies.data,
    tailRiskHistory.data,
    tailRisks.data,
    targets.data,
    trends.data,
    watchlist.data,
    unavailable,
  ]);
}
