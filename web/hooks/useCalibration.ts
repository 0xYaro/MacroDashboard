"use client";

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";

import type { Calibration, ScenarioRule, SetupGrade, WeekGrade } from "@/lib/contract";
import {
  fetchCalibration,
  fetchRules,
  fetchSetupGrades,
  fetchWeekGrades,
  keys,
  type Reading as TableReading,
  type SingleReading,
} from "@/lib/data";
import { readReading, type Reading } from "@/lib/freshness";
import { useSource } from "./useSource";

/**
 * Both ledgers, the rolling calibration read and the twelve standing rules.
 *
 * All four relations are projections of `macro_core`, which is not on the Data
 * API, and none of them is in `docs/CONTRACT.md` yet. They are read through the
 * optional path, so a project without them shows a named empty state per
 * section rather than failing the route.
 */

const NONE = { rows: [], rejected: [], transportError: null } as const;
const NO_ROW: SingleReading<Calibration> = {
  row: null,
  transportError: null,
  rejected: [],
};

const RELATIONS = [
  "macro_mart.calibration",
  "macro_mart.week_grade",
  "macro_mart.setup_grade",
  "macro_mart.scenario_rule",
];

/**
 * The base case's record over the calibration window, as three counts.
 *
 * `base_hit_score` is the worker's own arithmetic: Materialized counts 1,
 * Partial 0.5, Not 0. A tile that printed `4.5` made the operator invert that
 * to find out what happened, so the three counts are recovered from it rather
 * than recounted off the ledger: the score and the materialized count are the
 * figures the rest of the product is calibrated against, and a count taken here
 * could disagree with them over a window boundary.
 */
export interface HitRate {
  full: number;
  partial: number;
  missed: number;
  window: number;
}

export function readHitRate(row: Calibration | null): HitRate | null {
  if (row === null) return null;
  const { baseHitScore, baseMaterialized, window } = row;
  if (baseHitScore === null || baseMaterialized === null || window === null) {
    return null;
  }
  const partial = Math.round((baseHitScore - baseMaterialized) * 2);
  const missed = window - baseMaterialized - partial;
  // Only when the three actually reconstruct the score. A window the worker
  // sized differently from the counts is a finding, not a tile to guess at.
  if (partial < 0 || missed < 0) return null;
  return { full: baseMaterialized, partial, missed, window };
}

/**
 * The one sentence the tiles are a breakdown of.
 *
 * Five figures in five tiles is a reading exercise. The finding is that the
 * base case keeps being the middle scenario and keeps landing half-right, and
 * it is stated in words above the tiles so the page leads with it.
 */
export function calibrationLead(row: Calibration | null): string | null {
  if (row === null) return null;
  const parts: string[] = [];
  if (row.baseWasS2 !== null && row.window !== null) {
    const full =
      row.baseMaterialized === null
        ? null
        : row.baseMaterialized === 1
          ? "once"
          : `${row.baseMaterialized} times`;
    parts.push(
      `The base case has been S2 in ${row.baseWasS2} of the last ${row.window} weeks` +
        (full === null ? "." : ` and fully materialised ${full}.`),
    );
  }
  if (row.meanBrier !== null && row.brierWeeks !== null) {
    parts.push(
      `Mean Brier ${row.meanBrier.toFixed(3)} over ${row.brierWeeks} scored week${row.brierWeeks === 1 ? "" : "s"}.`,
    );
  }
  return parts.length === 0 ? null : parts.join(" ");
}

export interface CalibrationData {
  reading: Reading;
  calibration: Calibration | null;
  /** The base case's record over the window, as full, partial and missed. */
  hitRate: HitRate | null;
  /** The finding the tiles break down, or null when nothing was measured. */
  lead: string | null;
  weekGrades: readonly WeekGrade[];
  setupGrades: readonly SetupGrade[];
  rules: readonly ScenarioRule[];
  loading: boolean;
}

export function useCalibration(): CalibrationData {
  const { source, unavailable } = useSource();
  const scope = source?.scope ?? "none";
  const enabled = source !== null;
  const shared = { enabled, retry: 1 } as const;

  const calibration = useQuery({
    ...shared,
    queryKey: keys.calibration(scope),
    queryFn: () => fetchCalibration(source!),
  });
  const weekGrades = useQuery({
    ...shared,
    queryKey: keys.weekGrades(scope),
    queryFn: () => fetchWeekGrades(source!),
  });
  const setupGrades = useQuery({
    ...shared,
    queryKey: keys.setupGrades(scope),
    queryFn: () => fetchSetupGrades(source!),
  });
  const rules = useQuery({
    ...shared,
    queryKey: keys.rules(scope),
    queryFn: () => fetchRules(source!),
  });

  return useMemo<CalibrationData>(() => {
    const single: SingleReading<Calibration> = calibration.data ?? NO_ROW;
    const weeks: TableReading<WeekGrade> = weekGrades.data ?? NONE;
    const setups: TableReading<SetupGrade> = setupGrades.data ?? NONE;
    const ruleRows: TableReading<ScenarioRule> = rules.data ?? NONE;

    const rejected = [
      ...single.rejected,
      ...weeks.rejected,
      ...setups.rejected,
      ...ruleRows.rejected,
    ];
    const transportError =
      unavailable ??
      single.transportError ??
      weeks.transportError ??
      setups.transportError ??
      ruleRows.transportError;
    const answered =
      !enabled || (!calibration.isPending && !weekGrades.isPending);

    // One state per graded week, all `asserted`: a ledger row is a judgment
    // that was recorded, not a series with a release rule. Rating it `fresh`
    // would claim a cadence the Scorecard Ledger does not have.
    const reading = readReading({
      states: weeks.rows.map(() => "asserted" as const),
      rejected,
      transportError,
      evaluatedAt: single.row?.computedAt ?? null,
      answered,
      subject: "the calibration record",
      relations: RELATIONS,
    });

    return {
      reading,
      calibration: single.row,
      hitRate: readHitRate(single.row),
      lead: calibrationLead(single.row),
      // Newest last, as the ledger accrues, so the most recent week is the one
      // nearest the calibration line beneath the table.
      weekGrades: [...weeks.rows].sort((a, b) => a.week.localeCompare(b.week)),
      setupGrades: [...setups.rows].sort(
        (a, b) =>
          (a.proposedWeek ?? a.gradedWeek).localeCompare(
            b.proposedWeek ?? b.gradedWeek,
          ) || (a.name ?? "").localeCompare(b.name ?? ""),
      ),
      rules: [...ruleRows.rows].sort((a, b) => a.number - b.number),
      loading: enabled && !answered,
    };
  }, [
    calibration.data,
    calibration.isPending,
    enabled,
    rules.data,
    setupGrades.data,
    unavailable,
    weekGrades.data,
    weekGrades.isPending,
  ]);
}
