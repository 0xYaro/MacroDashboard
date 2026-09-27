"use client";

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";

import type {
  PhaseCallHistory,
  ScenarioHistory,
  ScenarioSetHistory,
  SetupHistory,
} from "@/lib/contract";
import { inForceOn } from "@/lib/contract";
import {
  fetchPhaseHistory,
  fetchScenarioHistory,
  fetchScenarioSetHistory,
  fetchSetupHistory,
  keys,
  latestStamp,
  type Reading as TableReading,
} from "@/lib/data";
import {
  isFault,
  readReading,
  worstFault,
  type Reading,
} from "@/lib/freshness";
import { useSource } from "./useSource";

/**
 * What was believed on a given day.
 *
 * The history views are the only route to `macro_core`, which is not on the
 * Data API. They are read whole and filtered in the browser rather than queried
 * per date, because the whole judgment record is a few hundred rows and a date
 * picker that refetched per keystroke would issue a request per character typed
 * into a date field.
 *
 * Filtering is not computing a metric. Nothing here derives a number; it
 * selects the rows whose validity interval covers a day.
 *
 * Two relations rather than one, because they answer different questions.
 * `scenario_set_history` projects `macro_core.scenario_set` and is
 * authoritative for a set that exists; `scenario_history`, added in migration
 * 0014, projects each of its three scenarios with the probability it carried.
 * A set with no scenarios would vanish from the second and still appear in the
 * first, which is a finding rather than a set to hide.
 *
 * Before 0014 the second was unreadable and this screen said so. It never
 * rendered today's three under a past date: that would be misattribution
 * rather than staleness, a probability the operator never wrote printed under
 * a day they did not write it, against which a Brier score would later be read.
 */

const NONE = { rows: [], rejected: [], transportError: null } as const;

const RELATIONS = [
  "macro_mart.scenario_set_history",
  "macro_mart.scenario_history",
  "macro_mart.phase_call_history",
  "macro_mart.setup_history",
];

export interface HistoryAt {
  day: string;
  /** At most one set is in force on any day, because the interval is half-open. */
  set: ScenarioSetHistory | null;
  /** That set's three scenarios, in ordinal order, as they were priced. */
  scenarios: readonly ScenarioHistory[];
  phaseCalls: readonly PhaseCallHistory[];
  setups: readonly SetupHistory[];
  /** The same setups, one entry per idea rather than one per version. */
  setupGroups: readonly SetupGroup[];
}

/**
 * One setup idea and every version of it written up to a day.
 *
 * A setup that survives a week is re-proposed, not edited, so the same idea
 * appears once per week it was carried. Rendered flat, `BTC old-box retest`
 * printed three times with the same levels and nothing on screen said the three
 * rows were one trade. The idea is the row; the weeks are a strip beside it.
 */
export interface SetupGroup {
  /** `asset\u0000name`, which is what identifies one idea across re-proposals. */
  key: string;
  asset: SetupHistory["asset"];
  name: string;
  /** The version in force on the day, whose levels the row prints. */
  current: SetupHistory;
  /** Every version up to and including the current one, oldest week first. */
  versions: readonly SetupHistory[];
}

/**
 * The validator banner for a history read, as one line.
 *
 * `readReading` prints every rejection whole, which is right for a snapshot of
 * forty-seven series and wrong here: a contract mismatch on one column rejects
 * every week at once, and seven identical sentences joined with semicolons
 * become a paragraph of banner above an empty page. The count and the reason
 * are the line; the per-row detail goes behind a disclosure inside the panel.
 */
export function hiddenWeeksLine(rejected: readonly string[]): string {
  const reasons = rejected.map((entry) => entry.replace(/^row \d+: /, ""));
  const distinct = Array.from(new Set(reasons));
  const short =
    distinct.length === 1
      ? (distinct[0]!.split(", got ")[0] ?? distinct[0]!)
      : `${distinct.length} different reasons`;
  return `${rejected.length} week${rejected.length === 1 ? "" : "s"} hidden: ${short}`;
}

export interface HistoryData {
  reading: Reading;
  allSets: readonly ScenarioSetHistory[];
  allScenarios: readonly ScenarioHistory[];
  allPhaseCalls: readonly PhaseCallHistory[];
  allSetups: readonly SetupHistory[];
  /**
   * Each excluded row and the reason it was excluded, for the disclosure.
   *
   * The banner carries the count and one short reason; this is what the
   * operator opens when they want to know which week and which column.
   */
  hidden: readonly string[];
  /** The days on which any judgment came into force. */
  statedDays: readonly string[];
  at: (day: string) => HistoryAt;
  loading: boolean;
}

/**
 * One row per setup idea in force on a day, carrying its earlier versions.
 *
 * The lineage is matched on asset and name rather than on `amends`, because a
 * re-proposal is a fresh row in a new week and the chain is not always written.
 * Only versions that were *believed* on or before the day join it: a row whose
 * `valid_from` is null was never in force, and one from a later week would date
 * a level to before it was written.
 */
function groupSetups(
  rows: readonly SetupHistory[],
  day: string,
): readonly SetupGroup[] {
  const keyOf = (row: SetupHistory) => `${row.asset}\u0000${row.name}`;
  const upTo = rows.filter(
    (row) => row.validFrom !== null && row.validFrom.slice(0, 10) <= day,
  );
  const byWeek = (a: SetupHistory, b: SetupHistory) =>
    a.week.localeCompare(b.week);

  const groups = new Map<string, SetupGroup>();
  for (const row of rows.filter((candidate) => inForceOn(candidate, day))) {
    const key = keyOf(row);
    const versions = upTo.filter((other) => keyOf(other) === key).sort(byWeek);
    // The in-force version, not simply the newest: a row written this week but
    // not yet activated is a proposal, and its levels were never believed.
    const current =
      versions.filter((other) => inForceOn(other, day)).sort(byWeek).at(-1) ??
      row;
    groups.set(key, {
      key,
      asset: row.asset,
      name: row.name,
      current,
      versions,
    });
  }

  return [...groups.values()].sort(
    (a, b) => a.asset.localeCompare(b.asset) || a.name.localeCompare(b.name),
  );
}

export function useHistory(): HistoryData {
  const { source, unavailable } = useSource();
  const scope = source?.scope ?? "none";
  const enabled = source !== null;
  const shared = { enabled, retry: 1 } as const;

  const sets = useQuery({
    ...shared,
    queryKey: keys.scenarioSetHistory(scope),
    queryFn: () => fetchScenarioSetHistory(source!),
  });
  const scenarios = useQuery({
    ...shared,
    queryKey: keys.scenarioHistory(scope),
    queryFn: () => fetchScenarioHistory(source!),
  });
  const phases = useQuery({
    ...shared,
    queryKey: keys.phaseHistory(scope),
    queryFn: () => fetchPhaseHistory(source!),
  });
  const setups = useQuery({
    ...shared,
    queryKey: keys.setupHistory(scope),
    queryFn: () => fetchSetupHistory(source!),
  });

  return useMemo<HistoryData>(() => {
    const setReading: TableReading<ScenarioSetHistory> = sets.data ?? NONE;
    const scenarioReading: TableReading<ScenarioHistory> =
      scenarios.data ?? NONE;
    const phaseReading: TableReading<PhaseCallHistory> = phases.data ?? NONE;
    const setupReading: TableReading<SetupHistory> = setups.data ?? NONE;

    const rejected = [
      ...setReading.rejected,
      ...scenarioReading.rejected,
      ...phaseReading.rejected,
      ...setupReading.rejected,
    ];
    const transportError =
      unavailable ??
      setReading.transportError ??
      scenarioReading.transportError ??
      phaseReading.transportError ??
      setupReading.transportError;
    const answered = !enabled || (!sets.isPending && !phases.isPending);

    // A row still only proposed has a null `valid_from` and was never believed,
    // so it contributes no day.
    const statedDays = Array.from(
      new Set(
        [
          ...setReading.rows.map((row) => row.validFrom),
          ...scenarioReading.rows.map((row) => row.validFrom),
          ...phaseReading.rows.map((row) => row.validFrom),
          ...setupReading.rows.map((row) => row.validFrom),
        ]
          .filter((stamp): stamp is string => stamp !== null)
          .map((stamp) => stamp.slice(0, 10)),
      ),
    ).sort();

    // Every row counts as `asserted`: the history is a projection of judgment,
    // not of observation, so it has no cadence to be due against and rating it
    // `fresh` would claim a freshness nobody measured.
    const states = [
      ...setReading.rows,
      ...scenarioReading.rows,
      ...phaseReading.rows,
      ...setupReading.rows,
    ].map(() => "asserted" as const);

    const base = readReading({
      states,
      rejected,
      transportError,
      evaluatedAt: latestStamp(setReading.rows.map((row) => row.validFrom)),
      answered,
      subject: "the judgment record",
      relations: RELATIONS,
    });

    // One line, not a paragraph. A row-level rejection is the only banner this
    // panel rewrites: an envelope fault and a freshness fault are one sentence
    // each already, and both outrank anything a row could say.
    const rowsOnly =
      transportError === null && rejected.length > 0 && !isFault(worstFault(states));
    const reading: Reading = rowsOnly
      ? {
          ...base,
          banner: { severity: "caution", text: hiddenWeeksLine(rejected) },
        }
      : base;

    return {
      reading,
      allSets: setReading.rows,
      allScenarios: scenarioReading.rows,
      allPhaseCalls: phaseReading.rows,
      allSetups: setupReading.rows,
      hidden: rejected,
      statedDays,
      at: (day: string) => ({
        day,
        // At most one, by construction: the intervals are half-open and a
        // partial unique index allows one active set per week. Taking the first
        // rather than asserting it, because a database that violated that would
        // be a finding and not a reason to blank the screen.
        set: setReading.rows.find((row) => inForceOn(row, day)) ?? null,
        scenarios: scenarioReading.rows
          .filter((row) => inForceOn(row, day))
          .sort((a, b) => a.ordinal - b.ordinal),
        phaseCalls: phaseReading.rows
          .filter((row) => inForceOn(row, day))
          .sort((a, b) => a.asset.localeCompare(b.asset)),
        setups: setupReading.rows
          .filter((row) => inForceOn(row, day))
          .sort(
            (a, b) =>
              a.asset.localeCompare(b.asset) || a.name.localeCompare(b.name),
          ),
        setupGroups: groupSetups(setupReading.rows, day),
      }),
      loading: enabled && !answered,
    };
  }, [
    enabled,
    phases.data,
    phases.isPending,
    scenarios.data,
    sets.data,
    sets.isPending,
    setups.data,
    unavailable,
  ]);
}
