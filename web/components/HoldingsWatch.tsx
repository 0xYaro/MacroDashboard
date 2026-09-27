"use client";

import { monthLabel } from "@/lib/catalysts";
import type {
  HoldingEvent,
  HoldingWatch,
  WatchlistChange,
} from "@/lib/contract";
import {
  EVENT_KIND_LABEL,
  SOURCE_LABEL,
  asWindow,
  basisWord,
  eventsFor,
  isQuiet,
  lastWatchRun,
  sortHoldings,
} from "@/lib/holdings";
import { safeHref } from "@/lib/links";
import { formatDelta } from "@/lib/numbers";
import {
  DESK_ZONE,
  clockMinute,
  dayLabel,
  parseInstant,
  shortDay,
  stampInZone,
  weekdayDay,
  whenWord,
} from "@/lib/time";
import { HoldingsEditor } from "./HoldingsEditor";
import { SourceLink } from "./SourceLink";

/**
 * "Your holdings": every name the morning holdings watch covered, with its
 * move, the reason for it and what is coming up in the next fourteen days.
 *
 * The names are Accounting's open positions, the operator's manual list
 * (brokers Accounting cannot see) and the watch list. Until the watch has run
 * once, the section shows the old watchlist moves from the Obsidian note
 * instead, and says so, so nothing goes blank before the cutover.
 */
export function HoldingsWatch({
  holdings,
  events,
  watchlist,
  now,
}: {
  holdings: readonly HoldingWatch[];
  events: readonly HoldingEvent[];
  watchlist: readonly WatchlistChange[];
  now: Date;
}) {
  const ran = lastWatchRun(holdings);
  const fallback = holdings.length === 0;
  return (
    <section className="snapshot-group">
      <div className="snapshot-group-head">
        <h3>Your holdings</h3>
        <span className="snapshot-group-count">
          {fallback
            ? "watchlist, last fourteen days"
            : `${holdings.length} ${holdings.length === 1 ? "name" : "names"}${ran === null ? "" : ` · watch ran ${whenWord(ran, now)}`}`}
        </span>
      </div>
      {fallback ? (
        <>
          <p className="faint hold-note">
            The holdings watch has not run yet, so these are the watchlist
            moves from the Obsidian note until it does.
          </p>
          <Watchlist changes={watchlist} />
        </>
      ) : (
        <ul className="hold-list">
          {sortHoldings(holdings).map((row) => (
            <HoldingRow
              events={eventsFor(events, row.symbol, now)}
              key={`${row.source}:${row.symbol}`}
              row={row}
            />
          ))}
        </ul>
      )}
      <HoldingsEditor />
    </section>
  );
}

function HoldingRow({
  row,
  events,
}: {
  row: HoldingWatch;
  events: readonly HoldingEvent[];
}) {
  const basis = basisWord(row);
  return (
    <li className="hold-row">
      <div className="hold-head">
        <b className="hold-symbol" title={row.symbol}>
          {row.symbol.replace(/\s+/g, " ")}
        </b>
        {row.name === null ? null : (
          <span className="hold-name">{row.name}</span>
        )}
        <span className={`hold-src hold-src-${row.source}`}>
          {SOURCE_LABEL[row.source]}
        </span>
        {row.material ? (
          <span
            className="chip k"
            title="The watch judged this move worth reading about today."
          >
            MATERIAL
          </span>
        ) : null}
        <span className="hold-move" title={priceTitle(row)}>
          {row.change24hPct === null ? (
            <span className="faint">no change</span>
          ) : (
            <span className={row.change24hPct >= 0 ? "up" : "down"}>
              {formatDelta(row.change24hPct)}%
            </span>
          )}
          {basis === null ? null : <small className="faint">{basis}</small>}
        </span>
      </div>
      {row.noClearReason ? (
        <p className="hold-driver">
          <span className="hold-no-reason">No clear reason found.</span>
          {row.driverSummary === null ? null : <> {row.driverSummary}</>}{" "}
          <SourceLink url={row.sourceUrl} />
        </p>
      ) : row.driverSummary !== null ? (
        <p className="hold-driver">
          {row.driverSummary} <SourceLink url={row.sourceUrl} />
        </p>
      ) : row.unavailableReason !== null ? (
        <p className="hold-quiet">{sentence(row.unavailableReason)}</p>
      ) : isQuiet(row) ? (
        <p className="hold-quiet">Quiet. No news found behind this move.</p>
      ) : null}
      {events.length === 0 ? null : (
        <ul className="hold-events">
          {events.map((event) => (
            <EventLine event={event} key={event.id} />
          ))}
        </ul>
      )}
    </li>
  );
}

function EventLine({ event }: { event: HoldingEvent }) {
  return (
    <li>
      <span className="hold-event-day">{eventDay(event)}</span>
      {event.dateEstimated ? (
        <span className="cal-est hold-event-est" title={event.dateText ?? undefined}>
          est.
        </span>
      ) : null}
      {safeHref(event.sourceUrl) === null ? (
        <span className="hold-event-name" title={event.dateText ?? undefined}>
          {event.name}
        </span>
      ) : (
        <a
          className="hold-event-name"
          href={safeHref(event.sourceUrl)!}
          rel="noreferrer"
          target="_blank"
          title={event.dateText ?? undefined}
        >
          {event.name}
        </a>
      )}
      {event.kind === "other" ? null : (
        <span className="faint hold-event-kind">
          {EVENT_KIND_LABEL[event.kind]}
        </span>
      )}
    </li>
  );
}

/**
 * When an event is, in the calendar's own words: a timed event with its
 * Singapore time, a window with its two ends, part of a month as `Early Oct`.
 */
function eventDay(event: HoldingEvent): string {
  if (event.datePrecision === "month") {
    return monthLabel(asWindow(event)) ?? "date not set";
  }
  const at = parseInstant(event.startsAt);
  if (at === null) return "date not set";
  if (event.datePrecision === "window") {
    const end = parseInstant(event.endsAt);
    return end === null
      ? weekdayDay(at, DESK_ZONE)
      : `${shortDay(at, DESK_ZONE)} to ${shortDay(end, DESK_ZONE)}`;
  }
  if (event.datePrecision === "exact" && !event.dateEstimated) {
    return `${weekdayDay(at, DESK_ZONE)} ${clockMinute(at, DESK_ZONE)}`;
  }
  return weekdayDay(at, DESK_ZONE);
}

/** Where a price came from and when, for the hover on the move. */
function priceTitle(row: HoldingWatch): string {
  const parts: string[] = [];
  if (row.price !== null) {
    const at = parseInstant(row.priceAt);
    parts.push(
      `Price ${row.price}${row.currency === null ? "" : ` ${row.currency}`}${
        at === null ? "" : ` at ${stampInZone(at, DESK_ZONE).slice(0, 16)} SGT`
      }.`,
    );
  }
  if (row.changeBasis === "close_to_close") {
    parts.push(
      `Change from the close before ${
        row.changeSession === null ? "the last session" : dayLabel(row.changeSession)
      } to that day's close.`,
    );
  } else if (row.changeBasis === "rolling_24h") {
    parts.push("Change over the last 24 hours.");
  }
  if (row.underlyingOnly) {
    parts.push("An option: this is its underlying's move, not the option's own.");
  }
  if (row.priceSource !== null) parts.push(`Source: ${row.priceSource}.`);
  if (row.unavailableReason !== null) parts.push(sentence(row.unavailableReason));
  return parts.join(" ");
}

/** The desk's reasons arrive without a capital or a full stop. */
function sentence(text: string): string {
  const trimmed = text.trim();
  const capped = trimmed.charAt(0).toUpperCase() + trimmed.slice(1);
  return /[.!?]$/.test(capped) ? capped : `${capped}.`;
}

/* ----------------------------------------------- the pre-cutover fallback */

function Watchlist({ changes }: { changes: readonly WatchlistChange[] }) {
  if (changes.length === 0) {
    return <p className="muted">No watchlist change in fourteen days.</p>;
  }
  // Newest session first; two reports of one session, newest report first.
  const sorted = [...changes].sort(
    (a, b) =>
      (b.sessionOn ?? b.observedAt).localeCompare(a.sessionOn ?? a.observedAt) ||
      b.observedAt.localeCompare(a.observedAt) ||
      a.ticker.localeCompare(b.ticker),
  );
  return (
    <ul className="risk-list">
      {sorted.map((one) => (
        <li key={`${one.ticker}-${one.observedAt}`}>
          <div className="risk-head">
            <b>{one.ticker}</b>
            {one.changePct === null ? null : (
              <span className={one.changePct >= 0 ? "up" : "down"}>
                {formatDelta(one.changePct)}%
              </span>
            )}
            <small className="faint">{sessionWord(one)}</small>
            <SourceLink url={one.sourceUrl} />
          </div>
          {one.summary === null ? null : <p>{one.summary}</p>}
        </li>
      ))}
    </ul>
  );
}

/**
 * Which trading day a move belongs to. The view dates each report to the New
 * York session that had closed when the 09:15 cron wrote it, so Friday's move
 * reads as Friday's even when it was reported on Monday morning.
 */
function sessionWord(one: WatchlistChange): string {
  if (one.sessionOn !== null) {
    const day = new Date(`${one.sessionOn}T00:00:00Z`);
    return `${weekdayDay(day, "UTC")} session`;
  }
  const at = parseInstant(one.observedAt);
  return at === null ? "" : `reported ${shortDay(at, DESK_ZONE)}`;
}
