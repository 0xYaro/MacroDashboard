/**
 * How the Catalysts page groups, dates and trims the calendar.
 *
 * The worker tags every catalyst with one of sixteen categories
 * (`worker/src/macro/families.py`). Sixteen is the right grain for a year's
 * archive and too fine for a filter, so the page folds them into eight groups a
 * reader recognises. This file is the only place that folding is written down.
 *
 * Pure functions only, so the rules are tested without rendering the page.
 */

import type { CatalystWindow } from "./contract";
import { MONTH_SHORT } from "./time";

export type CatalystGroup =
  | "macro"
  | "central_banks"
  | "earnings"
  | "market_events"
  | "tech"
  | "policy"
  | "investor"
  | "crypto"
  | "other";

/** The groups in the order the filter shows them, with their plain names. */
export const CATALYST_GROUPS: readonly {
  id: CatalystGroup;
  label: string;
}[] = [
  { id: "macro", label: "Macro data" },
  { id: "central_banks", label: "Central banks" },
  { id: "earnings", label: "Earnings" },
  { id: "market_events", label: "Market events" },
  { id: "tech", label: "Tech conferences" },
  { id: "policy", label: "Policy & geopolitics" },
  { id: "investor", label: "Investor conferences" },
  { id: "crypto", label: "Crypto" },
  { id: "other", label: "Other" },
];

const GROUP_OF_CATEGORY: Readonly<Record<string, CatalystGroup>> = {
  us_macro: "macro",
  global_macro: "macro",
  fed: "central_banks",
  global_central_bank: "central_banks",
  earnings: "earnings",
  index_options: "market_events",
  market_closure: "market_events",
  market_structure: "market_events",
  treasury_liquidity: "market_events",
  ai_tech: "tech",
  policy_geopolitics: "policy",
  trade_tariffs: "policy",
  commodities_energy: "policy",
  investor_forum: "investor",
  crypto: "crypto",
};

/**
 * The group a worker category belongs to. `other`, a null from a row written
 * before the column existed, and a category this file has not heard of all
 * land in "Other", so a new worker category shows up rather than vanishing.
 */
export function groupOf(category: string | null): CatalystGroup {
  if (category === null) return "other";
  return GROUP_OF_CATEGORY[category] ?? "other";
}

export function groupLabel(group: CatalystGroup): string {
  return CATALYST_GROUPS.find((g) => g.id === group)?.label ?? "Other";
}

/** How many window rows fall in each group. */
export function groupCounts(
  rows: readonly CatalystWindow[],
): Map<CatalystGroup, number> {
  const out = new Map<CatalystGroup, number>();
  for (const row of rows) {
    const group = groupOf(row.category);
    out.set(group, (out.get(group) ?? 0) + 1);
  }
  return out;
}

const DAY_MS = 86_400_000;

/**
 * The moment a catalyst is over. A timed event ends at its time; a dated one
 * lasts its whole day, and a window or an estimated part of a month lasts to
 * the end of its last day.
 */
export function endOf(row: CatalystWindow): number | null {
  const start = row.startsAt === null ? NaN : Date.parse(row.startsAt);
  const end = row.endsAt === null ? NaN : Date.parse(row.endsAt);
  const last = Number.isFinite(end) ? end : start;
  if (!Number.isFinite(last)) return null;
  return row.datePrecision === "exact" ? last : last + DAY_MS;
}

/**
 * The page's own guard against a stale window. The worker already drops a
 * catalyst more than a day past its end, but only when it runs: a missed
 * morning would otherwise leave last week's events on the calendar.
 */
export function withinWindow(row: CatalystWindow, now: Date): boolean {
  const end = endOf(row);
  return end === null || end >= now.getTime() - DAY_MS;
}

/** Whether a catalyst is already over, for dimming. */
export function isPast(row: CatalystWindow, now: Date): boolean {
  const end = endOf(row);
  return end !== null && end <= now.getTime();
}

const FULL_MONTHS = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
] as const;

function partOf(day: number): "Early" | "Mid" | "Late" {
  if (day <= 10) return "Early";
  if (day <= 20) return "Mid";
  return "Late";
}

/**
 * What the day column says for a catalyst dated only to (part of) a month.
 *
 * The worker stores "late October 2026 expected" as 21 to 31 October, so the
 * part is read back from the span: `Late Oct`, `Mid-late Oct`, `October`. The
 * dates are UTC midnights with no zone of their own, so they are read in UTC.
 */
export function monthLabel(row: CatalystWindow): string | null {
  if (row.startsAt === null) return null;
  const start = new Date(row.startsAt);
  if (!Number.isFinite(start.getTime())) return null;
  const month = start.getUTCMonth();
  const end = row.endsAt === null ? null : new Date(row.endsAt);
  const lastDay = new Date(
    Date.UTC(start.getUTCFullYear(), month + 1, 0),
  ).getUTCDate();
  const from = start.getUTCDate();
  const to =
    end !== null && Number.isFinite(end.getTime())
      ? end.getUTCDate()
      : lastDay;
  if (from === 1 && to >= lastDay) return FULL_MONTHS[month]!;
  const first = partOf(from);
  const last = partOf(to);
  const part =
    first === last ? first : `${first}-${last.toLowerCase()}`;
  return `${part} ${MONTH_SHORT[month]!}`;
}

/**
 * Where a row sorts. A dated row sorts by its instant. A month-only estimate
 * sorts after every dated row of its month, so eight "late October" earnings
 * no longer sit on 1 October pretending to be scheduled.
 */
export function sortKey(row: CatalystWindow): string {
  if (row.startsAt === null) return "9999";
  if (row.datePrecision !== "month") return row.startsAt;
  const start = new Date(row.startsAt);
  const month = String(start.getUTCMonth() + 1).padStart(2, "0");
  return `${start.getUTCFullYear()}-${month}-99 ${row.startsAt}`;
}
