"use client";

import { useQuery } from "@tanstack/react-query";

import { useSource } from "@/hooks/useSource";
import {
  isProse,
  type ObservationHistory,
  type SnapshotRow,
} from "@/lib/contract";
import { fetchObservationHistory, keys, type Reading } from "@/lib/data";
import { safeHref } from "@/lib/links";
import { formatLevel } from "@/lib/numbers";
import { dayLabel, fullDayLabel } from "@/lib/time";
import { NO_READ, SeriesChart } from "./SeriesChart";

/**
 * One series, opened out: its trend, and what each observation carried.
 *
 * Fetched only when the disclosure is opened, and cached per series, so a row
 * opened twice is read once and a page load reads none of them. Forty-seven
 * series of full history to draw one sparkline nobody asked for is several
 * thousand rows.
 *
 * **The interpretation over time is the read each observation actually
 * carried**, from `macro_mart.observation_history.note` since migration 0016.
 *
 * Where there is one, it is shown. Where there is not, the cell says that no
 * read was written for that observation and nothing further: not a
 * re-derivation from the value,
 * which would be this app manufacturing a judgment nobody made, and not the
 * current read borrowed down the column, which is worse, because a sentence
 * shown beside a value it was not written about is attributed to a day that
 * did not produce it. The rule is not explained on screen; it is enforced in
 * the code and pinned by four probes in `test/failure-modes.test.tsx`.
 *
 * A text series is the exception to the chart and the table: it has nothing to
 * draw and a paragraph per observation, so it gets a dated list of what it said
 * instead. See `ProseHistory` below; the read rule is the same there.
 */
export function SeriesDetail({ row }: { row: SnapshotRow }) {
  const { source } = useSource();
  const scope = source?.scope ?? "none";

  const history = useQuery({
    // The disclosure is only mounted when open, so mounting is the trigger.
    enabled: source !== null,
    queryKey: keys.observations(scope, row.key),
    queryFn: () => fetchObservationHistory(source!, row.key),
    retry: 1,
  });

  return (
    <SeriesHistory
      pending={history.isPending}
      reading={history.data ?? null}
      row={row}
    />
  );
}

/**
 * The same thing with the rows handed to it.
 *
 * Split from the fetch so the rule this file exists to hold can be tested at
 * the level it would be broken at. A component that only renders behind a live
 * query is a component whose rendering rules are not covered, and the rule here
 * is one a single `??` would quietly undo.
 */
export function SeriesHistory({
  row,
  reading,
  pending,
}: {
  row: SnapshotRow;
  reading: Reading<ObservationHistory> | null;
  pending: boolean;
}) {
  const rows = reading?.rows ?? [];
  if (isProse(row)) {
    return <ProseHistory pending={pending} reading={reading} row={row} />;
  }
  const ordered = [...rows].sort((a, b) => b.asof.localeCompare(a.asof));
  const written = ordered.filter((observation) => observation.note !== null)
    .length;
  // The figures the worker wrote after the first, which the Value cell in the
  // table above keeps out so a 13ch column does not become four lines. They
  // are readings, not decoration, so they are here in full.
  const alsoRead = (row.display ?? "")
    .split(";")
    .slice(1)
    .map((part) => part.trim())
    .filter((part) => part.length > 0);

  return (
    <div className="series-detail">
      <div className="series-detail-chart">
        {pending ? (
          <p className="chart-absent">
            Reading macro_mart.observation_history.
          </p>
        ) : reading !== null && reading.transportError !== null ? (
          <p className="error">{reading.transportError}</p>
        ) : (
          <SeriesChart label={`${row.label} · ${row.key}`} rows={rows} />
        )}
      </div>

      <div className="series-detail-reads">
        <p className="series-detail-head">
          <span>The read</span>
          <span className="faint">
            {row.asof === null ? "no as-of" : fullDayLabel(row.asof)}
          </span>
        </p>
        {row.note === null ? (
          <p className="series-detail-none">{NO_READ}</p>
        ) : (
          <p className="series-detail-note">{row.note}</p>
        )}
        {alsoRead.length === 0 ? null : (
          <>
            <p className="series-detail-head">
              <span>Also in this reading</span>
            </p>
            <ul className="series-detail-also">
              {alsoRead.map((part) => (
                <li key={part}>{part}</li>
              ))}
            </ul>
          </>
        )}
      </div>

      {ordered.length === 0 ? null : (
        <div className="series-detail-rows">
          <p className="series-detail-head">
            <span>Observations</span>
            <span className="faint">
              {ordered.length} on record · {written} with a read
            </span>
          </p>
          <div className="tbl tbl-bounded">
            <table>
              <colgroup>
                <col style={{ width: "10ch" }} />
                <col style={{ width: "13ch" }} />
                <col />
                <col style={{ width: "10ch" }} />
                <col style={{ width: "5ch" }} />
                <col style={{ width: "24ch" }} />
              </colgroup>
              <thead>
                <tr>
                  <th className="num">As-of</th>
                  <th className="num">Value</th>
                  <th>The read, as written</th>
                  <th className="num">Released</th>
                  <th className="num">Tier</th>
                  <th>Source</th>
                </tr>
              </thead>
              <tbody>
                {ordered.map((observation) => (
                  <tr key={observation.asof}>
                    <td className="num" title={fullDayLabel(observation.asof)}>
                      {dayLabel(observation.asof)}
                    </td>
                    <td className="num">
                      {observation.display ?? formatLevel(observation.value)}
                    </td>
                    {/* The read this observation carried, or nothing. Never
                        the current read in its place, and never a neighbour's:
                        that would attribute a sentence to a day that did not
                        produce it. */}
                    <td className="obs-read">
                      {observation.note ?? (
                        <span className="obs-read-none">{NO_READ}</span>
                      )}
                    </td>
                    <td className="num">
                      {observation.released === null
                        ? "same day"
                        : dayLabel(observation.released)}
                    </td>
                    <td className="num">{observation.tier ?? "—"}</td>
                    <td>{observation.source ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * A text series' history: what it said, week by week, newest first.
 *
 * Not the chart and table a figure gets. A statement has no value to draw, so
 * the chart could only report an absence that was not one, and a paragraph in
 * a nowrap Value column pushed the table past seven thousand pixels wide with
 * the read squeezed to a sliver beside it. A statement is a date and a
 * sentence, so it is laid out as one: the day on the left, the sentence
 * wrapping on the right, and where it came from underneath.
 *
 * The read rule holds here unchanged. Each entry shows the read its own
 * observation carried and nothing otherwise: no "no read" line down every
 * entry, which in a history of restatements is most of them and says nothing,
 * and never a neighbour's read or the current one borrowed onto a day that did
 * not produce it. Where identical statements run together they are one entry
 * over a range of days, and any read inside that range keeps its own date.
 */
function ProseHistory({
  row,
  reading,
  pending,
}: {
  row: SnapshotRow;
  reading: Reading<ObservationHistory> | null;
  pending: boolean;
}) {
  const rows = reading?.rows ?? [];
  const entries = restatements(rows);
  const written = rows.filter(
    (observation) => observation.note !== null,
  ).length;
  // The current read, only when no entry below already carries it under its
  // own date. Shown twice it reads as two judgments; shown under the newest
  // entry without having been stored there it is a borrowed one.
  const currentRead =
    row.note !== null &&
    !rows.some((observation) => observation.note === row.note)
      ? row.note
      : null;

  return (
    <div className="series-detail series-detail-prose">
      {currentRead === null ? null : (
        <div className="series-detail-reads">
          <p className="series-detail-head">
            <span>The read</span>
            <span className="faint">
              {row.asof === null ? "no as-of" : fullDayLabel(row.asof)}
            </span>
          </p>
          <p className="series-detail-note">{currentRead}</p>
        </div>
      )}

      <div className="series-detail-rows">
        <p className="series-detail-head">
          <span>What it said</span>
          {rows.length === 0 ? null : (
            <span className="faint">
              {rows.length} on record · {written} with a read
            </span>
          )}
        </p>
        {pending ? (
          <p className="series-detail-none">
            Reading macro_mart.observation_history.
          </p>
        ) : reading !== null && reading.transportError !== null ? (
          <p className="error">{reading.transportError}</p>
        ) : entries.length === 0 ? (
          <p className="series-detail-none">
            No earlier statement in macro_mart.observation_history.
          </p>
        ) : (
          <ol className="series-detail-timeline">
            {entries.map((entry) => (
              <li className="series-detail-entry" key={entry.newest}>
                <p
                  className="series-detail-entry-day"
                  title={
                    entry.oldest === entry.newest
                      ? fullDayLabel(entry.newest)
                      : `${fullDayLabel(entry.oldest)} to ${fullDayLabel(entry.newest)}, restated unchanged`
                  }
                >
                  {spanLabel(entry.oldest, entry.newest)}
                </p>
                <div className="series-detail-entry-body">
                  {entry.text === null ? (
                    <p className="series-detail-none">Nothing stated.</p>
                  ) : (
                    <p className="series-detail-entry-text">{entry.text}</p>
                  )}
                  {/* Each read under the entry its observation belongs to, and
                      dated when the entry spans more than one day, so no read
                      is attributed to a day that did not produce it. */}
                  {entry.reads.map((read) => (
                    <p className="series-detail-entry-read" key={read.asof}>
                      {entry.oldest === entry.newest ? null : (
                        <span className="series-detail-entry-read-day">
                          {dayLabel(read.asof)}
                        </span>
                      )}
                      {read.note}
                    </p>
                  ))}
                  {entry.sources.length === 0 ? null : (
                    <p className="series-detail-entry-source">
                      {entry.sources.map((source, index) => (
                        <span key={source.text}>
                          {index === 0 ? null : "; "}
                          {source.href === null ? (
                            source.text
                          ) : (
                            <>
                              {source.before}
                              <a
                                href={source.href}
                                rel="noreferrer"
                                target="_blank"
                              >
                                {source.link}
                              </a>
                              {source.after}
                            </>
                          )}
                        </span>
                      ))}
                    </p>
                  )}
                </div>
              </li>
            ))}
          </ol>
        )}
      </div>
    </div>
  );
}

interface Restatement {
  newest: string;
  oldest: string;
  text: string | null;
  reads: { asof: string; note: string }[];
  sources: SourcePart[];
}

/** Observations newest first, with identical consecutive statements merged. */
export function restatements(
  rows: readonly ObservationHistory[],
): Restatement[] {
  const ordered = [...rows].sort((a, b) => b.asof.localeCompare(a.asof));
  const out: Restatement[] = [];
  for (const observation of ordered) {
    const text =
      observation.display?.trim() ||
      (observation.value === null ? null : formatLevel(observation.value));
    const last = out.at(-1);
    const entry: Restatement =
      last !== undefined && last.text !== null && last.text === text
        ? last
        : {
            newest: observation.asof,
            oldest: observation.asof,
            text,
            reads: [],
            sources: [],
          };
    if (entry !== last) out.push(entry);
    entry.oldest = observation.asof;
    if (observation.note !== null) {
      entry.reads.push({ asof: observation.asof, note: observation.note });
    }
    for (const part of readableSource(observation.source)) {
      if (!entry.sources.some((seen) => seen.text === part.text)) {
        entry.sources.push(part);
      }
    }
  }
  return out;
}

/** "18 Sept", "11–18 Sept", or "28 Aug–4 Sept" across a month. */
function spanLabel(oldest: string, newest: string): string {
  const to = dayLabel(newest);
  if (oldest === newest) return to;
  const from = dayLabel(oldest);
  const [fromDay, ...fromMonth] = from.split(" ");
  const [, ...toMonth] = to.split(" ");
  return fromMonth.join(" ") === toMonth.join(" ")
    ? `${fromDay}–${to}`
    : `${from}–${to}`;
}

interface SourcePart {
  /** The whole part as read, for de-duplication and the unlinked case. */
  text: string;
  before: string;
  link: string;
  after: string;
  href: string | null;
}

const URL_PATTERN =
  /(https?:\/\/[^\s;,)]+)|\b((?:[a-z0-9-]+\.)+(?:gov|com|org|net|int|edu|io)(?:\/[^\s;,)]*)?)(?![a-z0-9-])/i;

/** ISO dates in running text, read as the rest of the page's dates. */
function readableDates(text: string): string {
  return text.replace(/\b(\d{4}-\d{2}-\d{2})\b/g, (iso) => dayLabel(iso));
}

/**
 * A stored source string as a reader should see it.
 *
 * The worker and the migrator record provenance inside the string: a
 * "search agent (macro): " prefix, a "(search agent)" tag, and a "(migrated
 * from Weekly Market Journal 2026-W38)" suffix. Those say how the row got into
 * storage, not where the statement came from, so they are dropped here and
 * nowhere else; the stored value is untouched. A URL or a bare domain becomes
 * a link, but only when it parses as http or https. The URL is found first and
 * left exactly as written: ISO dates read as the rest of the page's dates only
 * in the text around it, since a date inside an address is part of the path.
 */
export function readableSource(source: string | null): SourcePart[] {
  if (source === null) return [];
  const cleaned = source
    .replace(/\s*\(migrated from [^)]*\)/gi, "")
    .replace(/^\s*search agent(?:\s*\([^)]*\))?\s*:\s*/i, "")
    .replace(/\s*\((?:[^()]*,\s*)?search agent\)/gi, "");
  const parts: SourcePart[] = [];
  for (const raw of cleaned.split(";")) {
    const trimmed = raw.trim();
    if (trimmed.length === 0 || /^search agent$/i.test(trimmed)) continue;
    const match = URL_PATTERN.exec(trimmed);
    const href =
      match === null ? null : safeHref(match[1] ?? `https://${match[2]}`);
    if (match === null || href === null) {
      const text = readableDates(trimmed);
      parts.push({ text, before: "", link: "", after: "", href: null });
      continue;
    }
    // A full URL reads as its site. The address is in the link itself.
    const link =
      match[1] === undefined
        ? match[0]
        : new URL(href).hostname.replace(/^www\./, "");
    const before = readableDates(trimmed.slice(0, match.index));
    const after = readableDates(trimmed.slice(match.index + match[0].length));
    parts.push({
      text: `${before}${match[0]}${after}`,
      before,
      link,
      after,
      href,
    });
  }
  return parts;
}
