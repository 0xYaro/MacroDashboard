"use client";

import { useState } from "react";

import type { IngestRun } from "@/lib/contract";
import { isFeedRun } from "@/lib/feeds";
import { dedupeSentence } from "@/lib/freshness";
import {
  DESK_ZONE,
  ageLabel,
  clockMinute,
  fullDayLabel,
  parseInstant,
  stampInZone,
  zoneAbbreviation,
} from "@/lib/time";

/**
 * The three clocks, never collapsed, and one line per source that ran.
 *
 * 1. **Evaluated** is when the worker last recomputed `macro_mart`. It is what
 *    every number above is as-of.
 * 2. **As-of** is the day a reading describes, in the source's own market
 *    calendar. It is a calendar day, not an instant, so it is never converted
 *    into the desk's zone: a Friday US close observed at 05:00 Saturday
 *    Singapore time has a Friday as-of, and shifting it would rename the
 *    observation.
 * 3. **Released** is when the source published it. CFTC positions are as-of
 *    Tuesday and released Friday, and those are different facts about one row.
 *
 * `next expected` sits beside them as the fourth column. It is not a clock but
 * a forecast, and it is the only thing `due` and `overdue` are decided against.
 * A single "as of" would hide which of the four the operator is reading, and
 * the gap between the first three is the whole reason a lagged feed is
 * dangerous.
 *
 * One small line, not four. The paragraph explaining that observation dates are
 * calendar days is a fact about the whole screen that has to be read once and
 * then never again, so it sits on the evaluation stamp's tooltip: the place a
 * reader who doubts the date will put their cursor anyway.
 */
export function ClocksLine({
  evaluatedAt,
  now,
  asofSpread,
}: {
  evaluatedAt: string | null;
  now: Date;
  /** The oldest and newest `asof` across the reading, when there are any. */
  asofSpread: { oldest: string; newest: string } | null;
}) {
  const at = parseInstant(evaluatedAt);
  return (
    <p className="provenance-venue">
      <span
        title={`${
          at === null
            ? ""
            : `${stampInZone(at, DESK_ZONE)} ${zoneAbbreviation(DESK_ZONE)}. `
        }Observation dates are calendar days in each source's own market calendar and are never converted into the desk's zone. Only the evaluation stamp is an instant.`}
      >
        <b>Evaluated</b>{" "}
        {at === null
          ? "no stamp"
          : `${clockMinute(at, DESK_ZONE)} ${zoneAbbreviation(DESK_ZONE)} · ${ageLabel(at, now)}`}
      </span>
      {asofSpread === null ? (
        <span>
          <b>As-of</b> no observation dates
        </span>
      ) : asofSpread.oldest === asofSpread.newest ? (
        <span>
          <b>As-of</b> {fullDayLabel(asofSpread.oldest)}
        </span>
      ) : (
        <span
          title="Several release rules sit in this reading, so the as-of dates legitimately differ. A weekly series dated Tuesday beside a session close dated Friday is two honest observations, not one stale one."
        >
          <b>As-of</b> {fullDayLabel(asofSpread.oldest)} to{" "}
          {fullDayLabel(asofSpread.newest)}
        </span>
      )}
    </p>
  );
}

const FAILED = new Set(["failed", "error"]);

/**
 * The run kind this reading was mostly built from.
 *
 * Printed per source only when that source differs from it. Every row saying
 * `Run rescore` under a rescore reading is eleven repetitions of one fact; the
 * one source still sitting on the morning's full run is the fact worth having,
 * and it is invisible when the other ten shout with it.
 */
function defaultRunKind(sources: readonly SourceRuns[]): string {
  const tally = new Map<string, number>();
  for (const entry of sources) {
    tally.set(entry.latest.runKind, (tally.get(entry.latest.runKind) ?? 0) + 1);
  }
  return [...tally.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))[0]![0];
}

interface SourceRuns {
  source: string;
  /** The most recent run of any kind, which decides the summary count. */
  latest: IngestRun;
  lastOk: IngestRun | null;
  lastFail: IngestRun | null;
}

/**
 * The runs folded to one entry per source, newest first inside each.
 *
 * Two runs a day against eleven sources is twenty-two rows, and nineteen of
 * them say the same thing twice. What a reader wants from this footer is one
 * question answered per source: did it answer, and when did it last not.
 */
function bySource(runs: readonly IngestRun[]): SourceRuns[] {
  const grouped = new Map<string, IngestRun[]>();
  for (const run of runs) {
    const held = grouped.get(run.source) ?? [];
    held.push(run);
    grouped.set(run.source, held);
  }
  return [...grouped.entries()]
    .map(([source, held]) => {
      const ordered = [...held].sort((a, b) =>
        b.startedAt.localeCompare(a.startedAt),
      );
      return {
        source,
        latest: ordered[0]!,
        lastOk: ordered.find((run) => !FAILED.has(run.status)) ?? null,
        lastFail: ordered.find((run) => FAILED.has(run.status)) ?? null,
      };
    })
    .sort((a, b) => a.source.localeCompare(b.source));
}

/** A vendor error on one line, with the worker's repetition collapsed out. */
function oneLine(error: string): string {
  return dedupeSentence(error.replace(/\s+/g, " ").trim());
}

function timeAndAge(at: string | null, now: Date): string {
  const parsed = parseInstant(at);
  if (parsed === null) return "no stamp";
  return `${clockMinute(parsed, DESK_ZONE)} ${zoneAbbreviation(DESK_ZONE)} · ${ageLabel(parsed, now)}`;
}

/**
 * One summary line per reading, and one row per source when it is opened.
 *
 * Kept per source rather than summed into a run count once it is open, because
 * "FRED answered and the CNN agent did not" is the fact that matters and a
 * total of three successes out of four does not carry it. A source that
 * reported zero rows without failing is printed as zero rather than omitted:
 * nothing arriving is a reading about the source.
 *
 * Closed, it is two numbers and a clock, because on the good days, which are
 * most of them, twenty-two rows of provenance are twenty-two rows saying
 * nothing happened.
 *
 * The three desk feeds (`vault:*` and `desk:*` runs) are left out: they are
 * not data sources for this page, and the Catalysts page's freshness line is
 * where they are read.
 */
export function ProvenanceFooter({
  runs,
  now,
}: {
  runs: readonly IngestRun[];
  now: Date;
}) {
  const [open, setOpen] = useState(false);
  const sourceRuns = runs.filter((run) => !isFeedRun(run));
  if (sourceRuns.length === 0) return null;
  const sources = bySource(sourceRuns);
  const usual = defaultRunKind(sources);
  const answered = sources.filter(
    (entry) => !FAILED.has(entry.latest.status),
  ).length;
  const newest = sources
    .map((entry) => entry.latest.startedAt)
    .sort()
    .at(-1)!;
  const at = parseInstant(newest);

  return (
    <div className="provenance" aria-label="Source provenance">
      <button
        aria-expanded={open}
        className="provenance-summary"
        onClick={() => setOpen(!open)}
        type="button"
      >
        <span className="provenance-name">Sources</span>
        <span className={answered === sources.length ? undefined : "provenance-excluded"}>
          {answered} of {sources.length} answered
          {at === null
            ? ""
            : ` at ${clockMinute(at, DESK_ZONE)} ${zoneAbbreviation(DESK_ZONE)}`}
        </span>
        <span className="provenance-more">{open ? "▴" : "▾"}</span>
      </button>
      {open
        ? sources.map((entry) => (
            <p className="provenance-venue" key={entry.source}>
              <span className="provenance-name">{entry.source}</span>
              {entry.latest.runKind === usual ? null : (
                <span>
                  <b>Run</b> {entry.latest.runKind}
                </span>
              )}
              <span
                title={
                  entry.lastOk === null
                    ? undefined
                    : `${stampInZone(parseInstant(entry.lastOk.startedAt)!, DESK_ZONE)} ${zoneAbbreviation(DESK_ZONE)}`
                }
              >
                <b>Last ok</b>{" "}
                {entry.lastOk === null
                  ? "never"
                  : `${timeAndAge(entry.lastOk.startedAt, now)} · ${entry.lastOk.rows === null ? "no row count" : `${entry.lastOk.rows} rows`}`}
              </span>
              {entry.lastFail === null ? null : (
                <span className="provenance-excluded">
                  <b>Last fail</b> {timeAndAge(entry.lastFail.startedAt, now)}
                  {entry.lastFail.error === null
                    ? ""
                    : ` · ${oneLine(entry.lastFail.error)}`}
                </span>
              )}
            </p>
          ))
        : null}
    </div>
  );
}
