/**
 * Clocks. Every timestamp crosses the wire as `timestamptz` in UTC, every
 * observation date crosses it as a calendar `date`, and every conversion to a
 * readable time happens here.
 *
 * The distinction between the two is the point. A `timestamptz` is an instant
 * and has a zone; `asof` is a day in the source's own market calendar and does
 * not. A Friday US close observed at 05:00 Saturday Singapore time has a Friday
 * `asof`, per `docs/CONTRACT.md`, and converting that date into the operator's
 * zone would move it to Saturday and silently rename the observation.
 */

/** The operator's clock. One desk, one zone, and the zone the worker runs on. */
export const DESK_ZONE = "Asia/Singapore";

/**
 * The two runs, in the desk's own zone.
 *
 * 09:30 is the full run and 21:00 is the deterministic rescore, per
 * `docs/DECISIONS.md`. They are stated here rather than inferred from the last
 * `ingest_run`, because "the next run is at 21:00" is a fact about the schedule
 * and inferring it from history would make a missed run look like a changed
 * schedule.
 */
export const RUN_HOURS_SGT: readonly number[] = [9.5, 21];

/**
 * The polling interval, and why it is this number.
 *
 * Twelve hours separate the two runs, so nothing this app reads can change more
 * often than that. `staleTime` is set to the run interval per
 * `docs/CONTRACT.md`: a shorter one would poll a database that cannot have
 * moved, and a longer one would let a page sit through a run.
 *
 * There is no socket and no `refetchInterval` shorter than this anywhere, by
 * decision. Macro has no tick.
 */
export const RUN_INTERVAL_MS = 11.5 * 60 * 60 * 1000;

/**
 * How long past a scheduled run the reading waits before calling it missed.
 *
 * A full run takes about three minutes and a rescore about two. Forty-five
 * minutes covers a slow vendor and a machine waking from sleep, and is still
 * short enough that a run that never happened is said the same morning.
 */
export const RUN_GRACE_MS = 45 * 60 * 1000;

/** Singapore keeps no daylight saving, so the offset is a constant. */
const SGT_OFFSET_MS = 8 * 60 * 60 * 1000;

/**
 * How many scheduled runs have come and gone since `evaluatedAt`.
 *
 * Every rating on the page is the worker's, computed at `evaluatedAt`, and the
 * page never re-rates a series itself. That is only honest while the worker is
 * running: a row rated `fresh` on Monday evening is still stored as `fresh` on
 * Wednesday if nothing ran in between, and the header went on saying
 * "43 of 47 current" under a two-day-old stamp. A missed run is a fact about
 * the schedule, not a threshold this app invents, so it is counted here against
 * `RUN_HOURS_SGT` rather than guessed from the age.
 */
export function missedRuns(
  evaluatedAt: Date | null,
  now: Date,
  graceMs: number = RUN_GRACE_MS,
): number {
  if (evaluatedAt === null) return 0;
  const cutoff = now.getTime() - graceMs;
  if (cutoff <= evaluatedAt.getTime()) return 0;
  const day = 24 * 60 * 60 * 1000;
  // Midnight Singapore on the day of the evaluation, as a UTC instant.
  const localMidnight =
    Math.floor((evaluatedAt.getTime() + SGT_OFFSET_MS) / day) * day -
    SGT_OFFSET_MS;
  let missed = 0;
  for (let start = localMidnight; start <= cutoff; start += day) {
    for (const hour of RUN_HOURS_SGT) {
      const run = start + hour * 60 * 60 * 1000;
      if (run > evaluatedAt.getTime() && run <= cutoff) missed += 1;
    }
  }
  return missed;
}

function formatter(zone: string, options: Intl.DateTimeFormatOptions) {
  return new Intl.DateTimeFormat("en-GB", { ...options, timeZone: zone });
}

/** The short label a zone is announced by, beside its time. */
export function zoneAbbreviation(zone: string): string {
  if (zone === "Asia/Singapore") return "SGT";
  if (zone === "UTC") return "UTC";
  if (zone === "America/New_York") return "ET";
  return zone;
}

/** A wall clock to the minute, in a named zone. `21:04`. */
export function clockMinute(at: Date, zone: string): string {
  return formatter(zone, {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(at);
}

/** An instant with its date, in a named zone. `2026-09-21 21:04:02`. */
export function stampInZone(at: Date, zone: string): string {
  const parts = formatter(zone, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).formatToParts(at);
  const get = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((part) => part.type === type)?.value ?? "";
  return `${get("year")}-${get("month")}-${get("day")} ${get("hour")}:${get("minute")}:${get("second")}`;
}

/**
 * An instant, parsed, or null.
 *
 * A stamp the database could not have produced is not a date with a problem; it
 * is not a stamp, and the caller has to say so rather than render an
 * `Invalid Date`.
 */
export function parseInstant(value: string | null | undefined): Date | null {
  if (value === null || value === undefined) return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? new Date(parsed) : null;
}

/**
 * A calendar day, parsed as UTC midnight, or null.
 *
 * UTC rather than local on purpose. `asof` is a label, not an instant, and
 * parsing `2026-09-19` in a browser west of Greenwich would make it the
 * eighteenth. Every comparison this app makes between two `asof` values is
 * therefore between two UTC midnights, which is the same comparison the
 * database makes between two `date`s.
 */
export function parseDay(value: string | null | undefined): Date | null {
  if (value === null || value === undefined) return null;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
  const parsed = Date.parse(`${value}T00:00:00.000Z`);
  return Number.isFinite(parsed) ? new Date(parsed) : null;
}

/** Today, as the desk's own calendar day, in the `YYYY-MM-DD` the mart uses. */
export function deskToday(now: Date): string {
  const parts = formatter(DESK_ZONE, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(now);
  const get = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((part) => part.type === type)?.value ?? "";
  return `${get("year")}-${get("month")}-${get("day")}`;
}

/**
 * How long ago, in the grain the operator actually reads.
 *
 * Minutes, then hours, then days. Seconds are dropped, unlike Accounting's
 * ladder: a book whose marks move every two seconds earns "4s ago", and a
 * dashboard that recomputes twice a day does not. Printing seconds here would
 * spend a column on a digit that is never the information.
 */
export function ageLabel(at: Date | null, now: Date): string {
  if (at === null) return "never";
  const seconds = Math.round((now.getTime() - at.getTime()) / 1000);
  // A stamp ahead of the browser's clock is a skew, not a prediction. It reads
  // as "just now" rather than as a negative age, because a terminal that prints
  // "-3m ago" has told the operator about their laptop and not about the data.
  if (seconds <= 0) return "just now";
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

/**
 * Whole days between two calendar days, or null when either is missing.
 *
 * Positive means `to` is after `from`. Both are UTC midnights by construction,
 * so this is exact integer arithmetic and never drifts by an hour across a
 * daylight-saving boundary.
 */
export function daysBetween(from: Date | null, to: Date | null): number | null {
  if (from === null || to === null) return null;
  return Math.round((to.getTime() - from.getTime()) / 86_400_000);
}

/**
 * The app's one way to write a month: British short names, with September as
 * `Sept`. Spelled here rather than left to `Intl`, because engines disagree
 * (older ICU says `Sep`, newer says `Sept`) and a page rendered on the server
 * and again in the browser must print the same word both times.
 */
export const MONTH_SHORT = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sept",
  "Oct",
  "Nov",
  "Dec",
] as const;

const WEEKDAY_SHORT = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"] as const;

/** The calendar day an instant falls on in a zone, as numbers. */
function zonedDay(
  at: Date,
  zone: string,
): { year: number; month: number; day: number; weekday: number } {
  const parts = formatter(zone, {
    year: "numeric",
    month: "numeric",
    day: "numeric",
  }).formatToParts(at);
  const get = (type: Intl.DateTimeFormatPartTypes) =>
    Number(parts.find((part) => part.type === type)?.value ?? NaN);
  const year = get("year");
  const month = get("month") - 1;
  const day = get("day");
  return {
    year,
    month,
    day,
    weekday: new Date(Date.UTC(year, month, day)).getUTCDay(),
  };
}

/** An instant's day in a zone, as the page writes it: `9 Sept`. */
export function shortDay(at: Date, zone: string): string {
  const { month, day } = zonedDay(at, zone);
  return `${day} ${MONTH_SHORT[month]}`;
}

/** The same day with its weekday: `Mon 21 Sept`. */
export function weekdayDay(at: Date, zone: string): string {
  const { weekday } = zonedDay(at, zone);
  return `${WEEKDAY_SHORT[weekday]} ${shortDay(at, zone)}`;
}

/**
 * A calendar day, written the way the snapshot table prints it. `19 Sept`.
 *
 * The year is dropped because every row on the screen is inside the last month
 * and a repeated `2026` on 43 rows is 43 columns of nothing. The full date is
 * on the cell's own tooltip.
 */
export function dayLabel(value: string | null): string {
  const day = parseDay(value);
  if (day === null) return "—";
  return shortDay(day, "UTC");
}

/** The same day with its year, for a tooltip and for the history controls. */
export function fullDayLabel(value: string | null): string {
  const day = parseDay(value);
  if (day === null) return "no date";
  return `${shortDay(day, "UTC")} ${day.getUTCFullYear()}`;
}

/**
 * How long a tab may be hidden before what it is showing stops being credible.
 *
 * Longer than Accounting's thirty seconds, because the thing behind this app
 * moves twice a day rather than twice a second, and re-reading the database
 * every time the operator flicks back from a browser tab would be a poll
 * wearing a different name. Ten minutes is short enough that a tab left open
 * overnight re-reads on return and long enough that switching windows does not.
 */
export const STALE_AFTER_HIDDEN_MS = 10 * 60 * 1000;

/** Seconds between two instants, or null when there is no stamp to measure. */
export function ageSeconds(at: Date | null, now: Date): number | null {
  if (at === null) return null;
  return Math.max(0, (now.getTime() - at.getTime()) / 1000);
}

/**
 * An instant as the desk reads it: `08:51 today`, `21:00 yesterday`, or
 * `Mon 21 Sept 08:51` for anything older, in Singapore time.
 *
 * "Today" is the desk's own calendar day, not the browser's, so a 23:30 SGT
 * run reads as today on a laptop still set to London.
 */
export function whenWord(at: Date, now: Date): string {
  const clock = clockMinute(at, DESK_ZONE);
  const day = deskToday(at);
  if (day === deskToday(now)) return `${clock} today`;
  if (day === deskToday(new Date(now.getTime() - 86_400_000))) {
    return `${clock} yesterday`;
  }
  return `${weekdayDay(at, DESK_ZONE)} ${clock}`;
}
