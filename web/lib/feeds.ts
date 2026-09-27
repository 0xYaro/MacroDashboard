/**
 * The three desk feeds (the catalyst calendar, the tail risks and the holdings
 * watch): where each last came from, whether it is late, and how a tail risk
 * has moved between checks.
 *
 * The same page has to read correctly before and after the cutover in
 * `docs/DECISIONS.md` 2026-09-24. Before it, the worker parses each feed's
 * Obsidian note and writes a `vault:<feed>` run each morning; after it, the
 * desk command writes a `desk:<feed>` run once a morning (tail risks: each
 * Monday, plus a daily recheck while any risk is elevated or acute). Nothing
 * here assumes which side of that line a project is on.
 *
 * Pure functions only, so the rules are tested without rendering the page.
 */

import type {
  FeedFreshness,
  IngestRun,
  TailRiskCheck,
  TailRiskSeverity,
} from "./contract";
import { DESK_ZONE, parseInstant, shortDay, whenWord } from "./time";

export const FEEDS = ["catalysts", "tail_risks", "holdings"] as const;
export type FeedName = (typeof FEEDS)[number];

/** What each feed is called on the page. */
export const FEED_LABEL: Readonly<Record<FeedName, string>> = {
  catalysts: "Calendar",
  tail_risks: "Tail risks",
  holdings: "Holdings",
};

const HOUR_MS = 3_600_000;

/** Slack on top of a cadence before a feed reads late, as the worker's runs get. */
const GRACE_MS = 45 * 60 * 1000;

/**
 * How long a feed may go without a good run before it reads late.
 *
 * - The worker reads a note on its 09:30 full run only (the 21:00 rescore
 *   never opens the vault), so the longest gap is a day.
 * - The desk sends the calendar and the holdings watch once a morning.
 * - The desk scans the tail risks each Monday and rechecks them daily only
 *   while one is elevated or acute. With every risk at watch there is no daily
 *   run, and a week without one is not late.
 */
export function expectedGapMs(
  feed: FeedName,
  origin: string | null,
  dailyTailRecheck: boolean,
): number {
  if (feed === "tail_risks" && !dailyTailRecheck) {
    return 7 * 24 * HOUR_MS + GRACE_MS;
  }
  return 24 * HOUR_MS + GRACE_MS;
}

/** Who sent a feed, in words. */
export function originLabel(origin: string | null): string | null {
  if (origin === null) return null;
  if (origin === "vault") return "Obsidian note";
  if (origin.startsWith("openclaw")) return "OpenClaw";
  if (origin === "desk") return "OpenClaw";
  return origin;
}

/** A feed run's status that means it did not land. */
const FAILED = new Set(["failed", "error"]);

/**
 * Whether an ingest run is one of the three feeds rather than a data source.
 *
 * The Data page's source footer leaves these out, because they are shown on
 * the Catalysts page. By column where the migration has run, and by the
 * `vault:` and `desk:` source prefixes where it has not.
 */
export function isFeedRun(run: IngestRun): boolean {
  if (run.feed !== null) return true;
  return /^(vault|desk):/.test(run.source);
}

/** The feed a run belongs to, or null. */
function feedOf(run: IngestRun): FeedName | null {
  const name = run.feed ?? /^(?:vault|desk):(\w+)$/.exec(run.source)?.[1] ?? null;
  return FEEDS.find((feed) => feed === name) ?? null;
}

/**
 * `feed_freshness`, or the same three rows rebuilt from the ingest runs when
 * the view is not there yet. The view is a projection of those runs, so the
 * two agree wherever both exist.
 */
export function freshnessRows(
  feeds: readonly FeedFreshness[],
  runs: readonly IngestRun[],
): FeedFreshness[] {
  if (feeds.length > 0) return [...feeds];
  const out: FeedFreshness[] = [];
  for (const feed of FEEDS) {
    const mine = runs
      .filter((run) => feedOf(run) === feed)
      .sort((a, b) => b.startedAt.localeCompare(a.startedAt));
    const last = mine[0];
    if (last === undefined) continue;
    const ok =
      mine.find((run) => !FAILED.has(run.status) && run.finishedAt !== null) ??
      null;
    const originOf = (run: IngestRun) =>
      run.origin ?? (run.source.startsWith("vault:") ? "vault" : "desk");
    out.push({
      feed,
      origin: originOf(last),
      lastRunAt: last.startedAt,
      lastFinishedAt: last.finishedAt,
      lastStatus: last.status,
      lastError: last.error,
      lastOkAt: ok?.finishedAt ?? null,
      lastOkOrigin: ok === null ? null : originOf(ok),
      rows: ok?.rows ?? null,
    });
  }
  return out;
}

export interface FeedStatus {
  feed: FeedName;
  label: string;
  /** The words after the label: `OpenClaw, 08:51 today`. */
  text: string;
  /** Late, failed, or never run: drawn in the stale colour. */
  stale: boolean;
  /** The detail a reader who doubts the line will hover for. */
  title: string;
}

/**
 * One status per feed, in the page's order, from the freshness rows.
 *
 * A feed whose last run failed says so and gives its last good run, because
 * a failed morning must not look like a quiet one. A feed that has never run
 * says that, rather than vanishing from the line.
 */
export function feedStatuses(
  rows: readonly FeedFreshness[],
  now: Date,
  dailyTailRecheck: boolean,
): FeedStatus[] {
  return FEEDS.map((feed) => {
    const label = FEED_LABEL[feed];
    const row = rows.find((one) => one.feed === feed);
    if (row === undefined) {
      return {
        feed,
        label,
        text: "no run yet",
        stale: true,
        title: `No ${label.toLowerCase()} run is on record.`,
      };
    }
    const okAt = parseInstant(row.lastOkAt);
    const okOrigin = row.lastOkOrigin ?? row.origin;
    const gap = expectedGapMs(feed, okOrigin, dailyTailRecheck);
    const late = okAt === null || now.getTime() - okAt.getTime() > gap;
    const failed = FAILED.has(row.lastStatus);
    const who = originLabel(okOrigin);
    const detail: string[] = [];
    if (okAt !== null) {
      detail.push(
        `Last good run ${whenWord(okAt, now)} SGT${who === null ? "" : ` from ${fromWho(who)}`}${
          row.rows === null ? "" : `, ${row.rows} ${row.rows === 1 ? "row" : "rows"}`
        }.`,
      );
    }
    detail.push(
      `Expected at least ${cadenceWords(gap)} from ${fromWho(
        originLabel(okOrigin) ?? "source",
      )}.`,
    );
    if (failed) {
      const at = parseInstant(row.lastRunAt);
      detail.unshift(
        `The last run${at === null ? "" : ` (${whenWord(at, now)})`} failed${
          row.lastError === null ? "." : `: ${row.lastError}`
        }`,
      );
      return {
        feed,
        label,
        text:
          okAt === null
            ? "failed, never a good run"
            : `failed, last good ${whenWord(okAt, now)}`,
        stale: true,
        title: detail.join(" "),
      };
    }
    const last = okAt === null ? "" : `${who === null ? "" : `${who}, `}${whenWord(okAt, now)}`;
    return {
      feed,
      label,
      text:
        okAt === null
          ? "running, no good run yet"
          : late
            ? `late, last good ${last}`
            : last,
      stale: late,
      title: (late ? ["Late.", ...detail] : detail).join(" "),
    };
  });
}

/** A sender after "from": `the Obsidian note`, but `OpenClaw`, a name. */
function fromWho(label: string): string {
  return label === "OpenClaw" ? label : `the ${label}`;
}

/**
 * A feed's cadence in words, from its allowed gap less the grace: `weekly`,
 * `daily`, `twice a day`. Anything else falls back to hours.
 */
function cadenceWords(gapMs: number): string {
  const hours = Math.round((gapMs - GRACE_MS) / HOUR_MS);
  if (hours === 7 * 24) return "weekly";
  if (hours === 24) return "daily";
  if (hours <= 13 && hours >= 12) return "twice a day";
  return `every ${Math.round(gapMs / HOUR_MS)} hours`;
}

/* -------------------------------------------------------------- tail risks */

export interface TrailStep {
  severity: TailRiskSeverity;
  /** The day the risk reached this severity, as `22 Sept` in SGT. */
  day: string;
  checkedAt: string;
}

/**
 * A risk's escalation trail: its first severity, then each change, oldest
 * first. `watch 9 Sept → elevated 14 Sept → acute 21 Sept`.
 *
 * Checks that repeat a severity are folded into the step that reached it, so
 * a risk rechecked daily at the same level adds nothing. A risk that has only
 * ever been at one level has a one-step trail, which the page does not draw.
 */
export function severityTrail(
  checks: readonly TailRiskCheck[],
  riskId: string,
): TrailStep[] {
  const mine = checks
    .filter((one) => one.riskId === riskId && !one.retired)
    .sort((a, b) => a.checkedAt.localeCompare(b.checkedAt));
  const out: TrailStep[] = [];
  for (const check of mine) {
    if (out.at(-1)?.severity === check.severity) continue;
    const at = parseInstant(check.checkedAt);
    out.push({
      severity: check.severity,
      day: at === null ? "" : shortDay(at, DESK_ZONE),
      checkedAt: check.checkedAt,
    });
  }
  return out;
}

const RANK: Readonly<Record<TailRiskSeverity, number>> = {
  watch: 0,
  elevated: 1,
  acute: 2,
};

/** Whether a move between two severities is up (worse) or down. */
export function direction(
  from: TailRiskSeverity,
  to: TailRiskSeverity,
): "up" | "down" | "same" {
  if (RANK[to] > RANK[from]) return "up";
  if (RANK[to] < RANK[from]) return "down";
  return "same";
}
