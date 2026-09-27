/**
 * How the Catalysts page orders and words the holdings watch.
 *
 * The desk command computes every price and change (`docs/DESK.md`); the
 * model writes only the reason. This file decides nothing about a number: it
 * sorts, names the basis a change was measured on, and picks out the events a
 * name has coming up.
 *
 * Pure functions only, so the rules are tested without rendering the page.
 */

import type {
  CatalystWindow,
  HoldingEvent,
  HoldingSource,
  HoldingWatch,
} from "./contract";
import { endOf } from "./catalysts";
import { parseInstant } from "./time";

/**
 * Material moves first, then by the size of the move either way. A name with
 * no change sorts last, because there is nothing to rank it by; ties fall to
 * the symbol so the order never shuffles between renders.
 */
export function sortHoldings(rows: readonly HoldingWatch[]): HoldingWatch[] {
  const size = (row: HoldingWatch) =>
    row.change24hPct === null ? -1 : Math.abs(row.change24hPct);
  return [...rows].sort(
    (a, b) =>
      Number(b.material) - Number(a.material) ||
      size(b) - size(a) ||
      a.symbol.localeCompare(b.symbol),
  );
}

/** What the source chip says. */
export const SOURCE_LABEL: Readonly<Record<HoldingSource, string>> = {
  accounting: "Accounting",
  manual: "manual",
  watch: "watching",
};

/**
 * What a change was measured over, in words: `since last close` for a stock
 * (the last completed session against the one before), `24h` for crypto (a
 * rolling day). An option carries its underlying's move, and says whose.
 */
export function basisWord(row: HoldingWatch): string | null {
  if (row.changeBasis === null) return null;
  const base = row.changeBasis === "close_to_close" ? "since last close" : "24h";
  if (row.underlyingOnly) {
    return `${row.marketSymbol ?? "underlying"} ${base}`;
  }
  return base;
}

/**
 * A holding with nothing to say: a change and no reason behind it. The page
 * says so plainly rather than leaving a blank line that reads like a fault.
 */
export function isQuiet(row: HoldingWatch): boolean {
  return (
    row.driverSummary === null &&
    !row.material &&
    !row.noClearReason &&
    row.unavailableReason === null
  );
}

/**
 * A name's events, soonest first, dropping any that ended more than a day ago.
 *
 * The view already keeps only the next fourteen days, but only as of the last
 * query; a page left open overnight would otherwise keep yesterday's event.
 */
export function eventsFor(
  events: readonly HoldingEvent[],
  symbol: string,
  now: Date,
): HoldingEvent[] {
  return events
    .filter((one) => one.symbol === symbol)
    .filter((one) => {
      const end = endOf(asWindow(one));
      return end === null || end >= now.getTime() - 86_400_000;
    })
    .sort((a, b) => (a.startsAt ?? "9999").localeCompare(b.startsAt ?? "9999"));
}

/**
 * An event in the calendar's shape, so the calendar's own date rules (when a
 * day ends, how a part of a month is named) apply to it unchanged.
 */
export function asWindow(event: HoldingEvent): CatalystWindow {
  return {
    catalystId: event.id,
    name: event.name,
    startsAt: event.startsAt,
    endsAt: event.endsAt,
    datePrecision: event.datePrecision,
    dateText: event.dateText,
    dateEstimated: event.dateEstimated,
    category: event.kind === "earnings" ? "earnings" : null,
    affected: [],
    daysOut: null,
    referencedBy: [],
    sourceUrl: event.sourceUrl,
  };
}

/** When the watch last recorded a row: the newest `recorded_at`. */
export function lastWatchRun(rows: readonly HoldingWatch[]): Date | null {
  let best: Date | null = null;
  for (const row of rows) {
    const at = parseInstant(row.recordedAt);
    if (at !== null && (best === null || at > best)) best = at;
  }
  return best;
}

/** What an event's kind is called on the page. */
export const EVENT_KIND_LABEL: Readonly<Record<HoldingEvent["kind"], string>> = {
  earnings: "Earnings",
  product: "Product",
  regulatory: "Regulatory",
  unlock: "Unlock",
  other: "Event",
};
