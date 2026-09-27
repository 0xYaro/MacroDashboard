"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import type { Gap, ObservationHistory, SnapshotRow } from "@/lib/contract";
import { isProse } from "@/lib/contract";
import { GROUP_ORDER } from "@/lib/fixtures";
import { dimsRow, needsChip } from "@/lib/freshness";
import { glossaryFor } from "@/lib/glossary";
import {
  ABSENT,
  directionClass,
  formatLevel,
  formatMoney,
  formatSeriesDelta,
} from "@/lib/numbers";
import { dayLabel, fullDayLabel } from "@/lib/time";
import { GlossaryTerm } from "./GlossaryTerm";
import { SeriesDetail } from "./SeriesDetail";
import { StatusChip } from "./StatusChip";

/**
 * Forty-seven series, six release rules, one table, and nothing lying.
 *
 * The hard problem this view exists to solve: a weekly series dated Tuesday
 * sitting beside a session close dated Friday, beside a monthly survey dated
 * 31 August, beside a 24-hour crypto series dated today. All four are current.
 * None of them is stale. A table that painted the older as-ofs amber would put
 * a permanent warning on a terminal that is working perfectly, and the operator
 * would stop reading the colour by Wednesday.
 *
 * Four decisions follow from that.
 *
 * 1. **The freshness column is empty when nothing is wrong.** `fresh` and `due`
 *    are the two normal states and neither gets a chip. Only `overdue`, `gap`,
 *    `asserted`, `not_connected` and `failed` light up, so an empty column is
 *    itself the reading.
 * 2. **As-of and next-expected are columns, not a chip.** A reader who wants to
 *    know why a row is dated Tuesday looks one column right and sees that the
 *    next print is Friday. The explanation is beside the fact rather than
 *    behind a hover.
 * 3. **The delta names its own cadence.** `delta_native_label` rides under the
 *    number as `WoW` or `1d`, because a bare `-7.06` beside a weekly series
 *    reads as a one-day move, and that is a fabricated figure.
 * 4. **`display` is preferred over any formatting this app could do.** Net
 *    liquidity is stored in USD with zero decimals, so its value is
 *    5754716000000 and the worker's display is `$5.755T`. Formatting the number
 *    here would need a per-series unit table, and the first series it got wrong
 *    would be wrong silently.
 * 5. **The read is behind a disclosure, not beside the number.** Forty-seven
 *    reads printed inline is a page of prose with figures in it. The table is
 *    the figures; opening a row gives the read, the trend and the provenance
 *    of the series under it, and the history for that one series is fetched on
 *    the open rather than on load.
 */

/**
 * The columns, and the width each one is pinned to.
 *
 * Every column but one is sized to its content: the figures to the widest
 * legal figure in `ch`, the series name to the longest name it holds. The
 * trend column takes whatever is left.
 *
 * That is a reversal. The series column used to be the elastic one, which put
 * the names at the far left of a 1,200px table and the first figure six
 * hundred pixels away, with nothing in between: the eye crossed a hand's width
 * of nothing on every row to reach the number the row is about. Slack has to
 * go somewhere, and a thirty-day sparkline is the one thing on this table that
 * is better for having more of it.
 */
function Columns() {
  return (
    <colgroup>
      <col className="c-series" />
      <col className="c-value" />
      <col className="c-delta" />
      <col className="c-delta c-since" />
      <col className="c-asof" />
      <col className="c-next" />
      <col className="c-trend" />
      <col className="c-open" />
    </colgroup>
  );
}

/** How many columns a numeric table carries, for the spans below. */
const COLUMNS = 8;

/* ------------------------------------------------------------ the trend -- */

const SPARK_HEIGHT = 16;
const SPARK_PAD = 2;

/**
 * Thirty days of one series, at the size of a word.
 *
 * Drawn in the muted token and in no direction colour, for the same reason the
 * opened chart carries none: a series going up is not a position moving in
 * your favour, and the delta column two cells left already states the
 * direction as a signed figure.
 *
 * **Nothing is drawn across fewer than two points.** The doctrine the opened
 * chart was built on holds at this size too, and more so: a single dot in a
 * trend column reads as a flat line to anyone not looking closely, and a flat
 * line is a claim about thirty days that one observation cannot support. An
 * empty cell is the honest rendering of a series we hold one print of.
 *
 * Horizontal positions are percentages of the SVG's own width so the plot
 * widens with the column; vertical positions are pixels so the row height
 * cannot move. `preserveAspectRatio="none"` is what lets the two coexist.
 */
function Sparkline({
  points,
  label,
}: {
  points: readonly ObservationHistory[];
  label: string;
}) {
  const plot = useMemo(() => {
    const measured = points
      .filter((point) => point.value !== null)
      .sort((a, b) => a.asof.localeCompare(b.asof));
    if (measured.length < 2) return null;
    const values = measured.map((point) => point.value!);
    const lo = Math.min(...values);
    const hi = Math.max(...values);
    // A flat series still needs a band to sit in, or every point lands on the
    // same pixel and the line disappears.
    const span = hi - lo || Math.abs(hi) * 0.02 || 1;
    const first = Date.parse(`${measured[0]!.asof}T00:00:00Z`);
    const width =
      Date.parse(`${measured[measured.length - 1]!.asof}T00:00:00Z`) - first ||
      1;
    const path = measured
      .map((point) => {
        const x =
          ((Date.parse(`${point.asof}T00:00:00Z`) - first) / width) * 100;
        const y =
          SPARK_PAD +
          (1 - (point.value! - lo) / span) * (SPARK_HEIGHT - SPARK_PAD * 2);
        return `${x.toFixed(2)},${y.toFixed(2)}`;
      })
      .join(" ");
    return { path, measured, lo, hi };
  }, [points]);

  if (plot === null) return null;
  const from = plot.measured[0]!;
  const to = plot.measured[plot.measured.length - 1]!;
  return (
    <svg
      aria-label={`${label}: ${plot.measured.length} observations, ${formatLevel(from.value)} on ${dayLabel(from.asof)} to ${formatLevel(to.value)} on ${dayLabel(to.asof)}`}
      className="spark"
      height={SPARK_HEIGHT}
      preserveAspectRatio="none"
      role="img"
      viewBox={`0 0 100 ${SPARK_HEIGHT}`}
    >
      <polyline
        className="spark-line"
        points={plot.path}
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}

/* ------------------------------------------------- the unit a delta reads in -- */

/**
 * A `percent` series is either a yield, whose moves are read in basis points,
 * or a share of something (breadth, an allocation, a fear index), whose moves
 * are percentage points. The registry names the unit but not which of the two
 * it is, so the key decides: the yields and auction stops carry `yield` or
 * `ust_` in their names, and nothing else does.
 */
const YIELD_KEY = /(^|_)(ust|yield|high_yield|fed_funds)(_|$)/;

export function deltaUnit(row: {
  key: string;
  unit: string | null;
}): string | null {
  if (row.unit === "percent" && YIELD_KEY.test(row.key)) return "yield_percent";
  return row.unit;
}

/* ------------------------------------------------- one cell, one figure -- */

/**
 * The leading figure of a value the worker wrote as several.
 *
 * A handful of rows carry two readings in one `display`, separated by a
 * semicolon: `$156.79B proxy; 1.835M BTC` on the open-interest proxy,
 * `~$514M; $56M longs / $458M shorts` on liquidations, a ranked list on the
 * funding extremes. In a 13ch column each of those wraps to four lines and
 * takes the whole table's row height with it, and the second figure is not
 * the one any condition reads.
 *
 * So the cell keeps the first and the disclosure carries the rest. **This is
 * a rendering decision and nothing else**: the worker still writes the whole
 * string, `macro_mart.snapshot_row.display` is unchanged, and every figure it
 * held is still on screen one click away. Splitting in the worker would throw
 * the remainder away.
 */
export function leadFigure(display: string | null): string | null {
  if (display === null) return null;
  const at = display.indexOf(";");
  return at === -1 ? display : display.slice(0, at).trim();
}

/** Longer than this, a `display` is a sentence about the figure, not the figure. */
const CELL_FIGURE_MAX = 22;

/** A figure at the start of a sentence: `+$1.71B`, `~$461M`, `57.4%`, `-5 bp`. */
const LEADING_FIGURE =
  /^[~≈<>+\-−]?\$?\d[\d,.]*[KMBT]?(?:%|\s?(?:bps?|pp|pts|x))?/;

/**
 * What a numeric row's Value cell prints.
 *
 * The narrative run writes a sentence into `display` for the series it
 * searches: `+$1.71B week-to-date (Mon Sep 21 +$998.95M, …), up from +$6.2M`,
 * `FedWatch puts a 25 bp hike at the Oct 28 FOMC at 59.7% (quoted Sep 22)`.
 * Printed whole, the first live run of it on 2026-09-23 stretched the Value
 * column to 900px and pushed every other column off a phone. The cell keeps a
 * short display as written, the leading figure of a long one, and otherwise
 * the series' own number in its own unit. The sentence is on the cell's title
 * and in the row's disclosure, so nothing the run wrote is lost.
 */
export function cellFigure(row: {
  display: string | null;
  value: number | null;
  unit: string | null;
}): string {
  const lead = leadFigure(row.display);
  if (lead !== null && lead.length <= CELL_FIGURE_MAX) return lead;
  const figure =
    lead === null ? null : (LEADING_FIGURE.exec(lead)?.[0] ?? null);
  if (figure !== null) return figure.trim();
  if (row.value === null) return lead ?? ABSENT;
  switch (row.unit) {
    case "usd":
      return formatMoney(row.value).replace(/^\+/, "");
    case "percent":
      return `${formatLevel(row.value)}%`;
    case "sessions":
      return `${row.value > 0 ? "+" : ""}${row.value} session${Math.abs(row.value) === 1 ? "" : "s"}`;
    default:
      return formatLevel(row.value);
  }
}

/**
 * An index pair, folded into one row the way the journal writes it.
 *
 * `7,657.35 (cash 7,650.50, -0.08%)`: the front-month level first, the cash
 * level and the week-over-week beside it. Three pairs only, S&P, Dow and
 * Russell; the Nasdaq is cash-only and has no future.
 *
 * **The data model does not change.** `spx_front` and `spx_cash` remain two
 * separate rows in `macro_mart.snapshot_row`, and every condition, scenario
 * band and setup level still resolves against the cash series. This folds the
 * presentation and nothing else.
 *
 * Both numbers stay visible, which is the whole condition on folding them. R1
 * exists because the two were once conflated in the week-over-week arithmetic,
 * and burying the value a condition reads inside another row's formatting
 * would be that mistake again. Showing both in one cell answers it; showing
 * only the future would not.
 *
 * **The percentage is the cash index's, never the future's**, per R1. Contract
 * rolls contaminate a front-month week-over-week.
 */
interface Pair {
  future: SnapshotRow;
  cash: SnapshotRow;
}

const PAIRED = new Set(["spx", "dow", "rut"]);

function stem(key: string): string | null {
  if (key.endsWith("_front")) return key.slice(0, -"_front".length);
  if (key.endsWith("_cash")) return key.slice(0, -"_cash".length);
  return null;
}

/**
 * Fold each `*_front` row together with the `*_cash` row that follows it.
 *
 * Keyed on the series key rather than on adjacency alone, so a group whose
 * ordinals were reordered folds the right two rows or folds nothing, rather
 * than folding whichever row happens to sit underneath.
 */
function fold(rows: readonly SnapshotRow[]): {
  row: SnapshotRow;
  pair: Pair | null;
}[] {
  const out: { row: SnapshotRow; pair: Pair | null }[] = [];
  const consumed = new Set<string>();
  for (const row of rows) {
    if (consumed.has(row.key)) continue;
    const base = stem(row.key);
    if (base !== null && PAIRED.has(base) && row.key.endsWith("_front")) {
      const cash = rows.find((other) => other.key === `${base}_cash`);
      if (cash !== undefined) {
        consumed.add(cash.key);
        // The cash row carries the identity: it is the one every condition
        // names, so it owns the label, the as-of and the freshness.
        out.push({ row: cash, pair: { future: row, cash } });
        continue;
      }
    }
    out.push({ row, pair: null });
  }
  return out;
}

/** `S&P 500 (cash)` and `S&P 500 (ES=F)` share a name once folded. */
function pairLabel(pair: Pair): string {
  return pair.cash.label.replace(/\s*\((cash|ES=F|YM=F|RTY=F)\)\s*$/i, "");
}

/** The series key a `#series-<key>` fragment names, read after mount. */
export function seriesFromHash(hash: string): string | null {
  const match = /^#series-([a-z0-9_]+)$/.exec(hash);
  return match === null ? null : match[1]!;
}

function useTargeted(keys: readonly string[]): boolean {
  const [hit, setHit] = useState(false);
  const joined = keys.join(",");
  useEffect(() => {
    const read = () => {
      const named = seriesFromHash(window.location.hash);
      setHit(named !== null && joined.split(",").includes(named));
    };
    read();
    window.addEventListener("hashchange", read);
    return () => window.removeEventListener("hashchange", read);
  }, [joined]);
  return hit;
}

function Row({
  row,
  pair,
  gap,
  trend,
  moved,
}: {
  row: SnapshotRow;
  /** Set when this row is an index pair folded into one. */
  pair: Pair | null;
  gap: Gap | undefined;
  /** This series' observations inside the trend window, unsorted. */
  trend: readonly ObservationHistory[];
  /** This series printed on the evaluation date: one of the day's movers. */
  moved: boolean;
}) {
  // A Desk link lands here as `/data#series-<key>`: the row it names opens
  // with its detail drawn and scrolls into view, so a band chip on the Desk
  // is one click from the history behind it. A folded pair answers to either
  // of its two keys, because a condition names the cash index and a reader
  // may have clicked the future.
  const keys = pair === null ? [row.key] : [pair.cash.key, pair.future.key];
  const targeted = useTargeted(keys);
  const [open, setOpen] = useState(false);
  const self = useRef<HTMLTableRowElement>(null);
  useEffect(() => {
    if (!targeted) return;
    setOpen(true);
    self.current?.scrollIntoView({ block: "center" });
  }, [targeted]);
  const dim = dimsRow(row.freshness);
  const prose = isProse(row);
  const classes = [
    prose ? "row-text" : "",
    moved ? "row-moved" : "",
    targeted ? "row-targeted" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <>
      <tr
        className={classes === "" ? undefined : classes}
        id={`series-${row.key}`}
        ref={self}
      >
        <td
          title={
            moved
              ? "New figures for this series came out on the evaluation date: it is one of the day's movers."
              : undefined
          }
        >
          <GlossaryTerm entry={glossaryFor({ key: row.key, label: row.label })}>
            {pair === null ? row.label : pairLabel(pair)}
          </GlossaryTerm>
          {/* An abnormal state rides on the row rather than in a column of its
            own. `fresh` and `due` are the two normal states and neither gets a
            chip, so a column for this would be blank on most rows most of the
            time, and a header over permanent blankness reads as missing data
            rather than as nothing being wrong. */}
          {needsChip(row.freshness) ? (
            <span
              className="row-state"
              title={
                gap === undefined
                  ? undefined
                  : `${gap.permanent ? "Not recoverable" : "Recoverable"}: ${gap.reason}`
              }
            >
              <StatusChip state={row.freshness} />
            </span>
          ) : null}
        </td>

        {prose ? (
          // A prose row never prints the numeric columns. Three dashes is the
          // shape of a Tier 4 failure, and a macro state must never be readable
          // as a fetch that came back empty. A group that is entirely prose does
          // not draw a table at all; this is the mixed group, where the sentence
          // spans the columns it has no figure for.
          <td className="state" colSpan={3}>
            {row.display ?? ABSENT}
          </td>
        ) : (
          <>
            <td className={`num${dim ? " cell-stale" : ""}`}>
              {pair === null ? (
                <span title={row.display ?? undefined}>{cellFigure(row)}</span>
              ) : (
                <>
                  {leadFigure(pair.future.display) ??
                    formatLevel(pair.future.value)}
                  <span className="paired-cash">
                    {/* The cash level and its week-over-week, as the journal
                      writes the cell. The percentage is the CASH index's, per
                      R1: contract rolls contaminate a front-month
                      week-over-week, which is the arithmetic the rule was
                      written about. It is the same figure the wk-open column
                      carries, inline here because that is the form the journal
                      reads in. */}
                    cash{" "}
                    {leadFigure(pair.cash.display) ??
                      formatLevel(pair.cash.value)}
                    {/* In the cash series' own unit, as every delta on this
                      table is. It printed a `%` after an index-point figure
                      (`-185.13%` on the Dow) once the worker, rather than the
                      migrated journal, wrote the delta. */}
                    {pair.cash.deltaWeekOpen === null
                      ? ""
                      : `, ${formatSeriesDelta(
                          pair.cash.deltaWeekOpen,
                          deltaUnit(pair.cash),
                          pair.cash.deltaNativeLabel,
                          pair.cash.value,
                        )}`}
                  </span>
                </>
              )}
            </td>
            {/* Both deltas are read in the series' own unit and at its own
              magnitude, because the worker computes each as a plain difference
              of two observations of this one series. A raw
              `-27,855,000,000` beside `$5.864T` is arithmetic the reader has
              to do, and the column exists to spare them it. */}
            <td className={`num${dim ? " cell-stale" : ""}`}>
              <span className={directionClass(row.deltaNative)}>
                {formatSeriesDelta(
                  row.deltaNative,
                  deltaUnit(row),
                  row.deltaNativeLabel,
                  row.value,
                )}
              </span>
              <span className="cadence">{row.deltaNativeLabel ?? ""}</span>
            </td>
            <td className={`num c-since${dim ? " cell-stale" : ""}`}>
              <span className={directionClass(row.deltaWeekOpen)}>
                {formatSeriesDelta(
                  row.deltaWeekOpen,
                  deltaUnit(row),
                  row.deltaNativeLabel,
                  row.value,
                )}
              </span>
              {/* No sub-label. The anchor is one date for the whole snapshot,
                stated once in the footnote rather than repeated on forty
                rows, and the header already names what the column measures. */}
            </td>
          </>
        )}

        <td className="num c-asof" title={fullDayLabel(row.asof)}>
          {dayLabel(row.asof)}
          <span className="cadence">{row.cadence ?? ""}</span>
        </td>
        <td
          className="num c-next"
          title={
            row.nextExpected === null
              ? "An event series has no scheduled next observation, so it is never overdue."
              : `Next observation expected ${fullDayLabel(row.nextExpected)}`
          }
        >
          {row.nextExpected === null ? "—" : dayLabel(row.nextExpected)}
          <span className="cadence">
            {row.released === null ? "" : `released ${dayLabel(row.released)}`}
          </span>
        </td>
        {/* Last of the figures, so the columns a reader compares sit together
          and the month of shape fills whatever is left to the right. */}
        <td className="c-trend">
          {prose ? null : (
            <Sparkline
              label={pair === null ? row.label : pairLabel(pair)}
              points={trend}
            />
          )}
        </td>
        <td className="c-open">
          <button
            aria-expanded={open}
            className="row-open"
            onClick={() => setOpen((was) => !was)}
            title={
              open
                ? "Close"
                : "This series' chart, the read each observation carried, and every observation on record"
            }
            type="button"
          >
            <svg aria-hidden="true" viewBox="0 0 10 6">
              <path d="M1 1 L5 5 L9 1" />
            </svg>
          </button>
        </td>
      </tr>
      {open ? (
        <tr className="row-detail">
          {/* One cell spanning the row, so the detail is not forced into the
            fixed column widths the figures above it are pinned to. */}
          <td colSpan={COLUMNS}>
            <SeriesDetail row={pair === null ? row : pair.cash} />
          </td>
        </tr>
      ) : null}
    </>
  );
}

/**
 * One standing state: a label, a sentence, and the day it was last restated.
 *
 * Macro and Fiscal State holds five text series, and they were rendered in the
 * numeric grid: a paragraph on the active tariff regime squeezed into a 13ch
 * Value column beside two empty Δ cells and an empty trend cell, at a hundred
 * and sixty pixels of row height. Four columns of nothing are not an absence
 * of data, they are a table shape that does not fit what it holds. A standing
 * state is a sentence with a date on it, so it is laid out as one.
 *
 * The disclosure stays. A text series has a history like any other, and it is
 * the one place a reader can see what the state said last week.
 */
function StandingRow({ row, gap }: { row: SnapshotRow; gap: Gap | undefined }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="standing-row">
      <p className="standing-label">
        <GlossaryTerm entry={glossaryFor({ key: row.key, label: row.label })}>
          {row.label}
        </GlossaryTerm>
        {needsChip(row.freshness) ? (
          <span
            className="row-state"
            title={
              gap === undefined
                ? undefined
                : `${gap.permanent ? "Not recoverable" : "Recoverable"}: ${gap.reason}`
            }
          >
            <StatusChip state={row.freshness} />
          </span>
        ) : null}
      </p>
      <div className="standing-body">
        <p className="standing-text">{row.display ?? ABSENT}</p>
        <p className="standing-asof">
          <span title={fullDayLabel(row.asof)}>
            stated {dayLabel(row.asof)}
          </span>
          <button
            aria-expanded={open}
            className="row-open"
            onClick={() => setOpen((was) => !was)}
            title={
              open
                ? "Close"
                : "Every restatement of this series on record, and the read each one carried"
            }
            type="button"
          >
            <svg aria-hidden="true" viewBox="0 0 10 6">
              <path d="M1 1 L5 5 L9 1" />
            </svg>
          </button>
        </p>
        {open ? <SeriesDetail row={row} /> : null}
      </div>
    </div>
  );
}

export function SnapshotView({
  rows,
  gaps,
  trends = [],
  title,
}: {
  rows: readonly SnapshotRow[];
  gaps: readonly Gap[];
  /**
   * A heading to print in place of each group's own name, for a page that
   * shows a slice of one group under a name that says what the slice is.
   */
  title?: string;
  /**
   * Thirty days of observations for every series, read once for the page.
   *
   * Optional, and empty is a legal reading: the trend column is then blank and
   * every figure on the table is unaffected. A sparkline never gates a number.
   */
  trends?: readonly ObservationHistory[];
}) {
  // `ordinal` is the only declaration of row order, and `GROUP_ORDER` is the
  // only declaration of group order. Sorting either alphabetically would make
  // the snapshot's shape depend on a spelling.
  const group_ = (name: string) => {
    const inGroup = rows
      .filter((row) => row.tableGroup === name)
      .sort((a, b) => a.ordinal - b.ordinal);
    return {
      name,
      rows: inGroup,
      // Every row prose, not merely some. A group holding one prose row among
      // numbers keeps the numeric table; the prose row spans the columns it has
      // no figure for.
      prose: inGroup.length > 0 && inGroup.every(isProse),
    };
  };

  const groups = GROUP_ORDER.map(group_).filter(
    (group) => group.rows.length > 0,
  );

  // Any group the contract did not name is still shown, at the end, rather than
  // silently dropped. A series in an unknown group is a contract drift worth
  // seeing, not a row to hide.
  const known = new Set<string>(GROUP_ORDER);
  const extra = Array.from(
    new Set(rows.map((row) => row.tableGroup).filter((g) => !known.has(g))),
  ).map(group_);

  const gapByKey = new Map(gaps.map((g) => [g.seriesKey ?? g.seriesId, g]));
  const anchor =
    rows.find((row) => row.anchorAsof !== null)?.anchorAsof ?? null;

  // The trend window, indexed by series key once for the whole page rather
  // than filtered per row, which would be forty passes over the same array.
  const trendByKey = new Map<string, ObservationHistory[]>();
  for (const point of trends) {
    const held = trendByKey.get(point.seriesKey);
    if (held === undefined) trendByKey.set(point.seriesKey, [point]);
    else held.push(point);
  }

  // The evaluation date: the newest as-of anywhere in the snapshot. A row
  // dated to it printed today, and those are the rows a reader opening the
  // terminal in the morning is looking for. Read from the rows rather than
  // from the clock, so the marker means "moved in this reading" rather than
  // "moved since you last looked".
  const evaluation = rows
    .map((row) => row.asof)
    .filter((asof): asof is string => asof !== null)
    .reduce<string | null>((a, b) => (a === null || b > a ? b : a), null);

  return (
    <>
      {[...groups, ...extra].map((group) => (
        <section className="snapshot-group" key={group.name}>
          <div className="snapshot-group-head">
            <h3>{title ?? group.name}</h3>
          </div>
          {group.prose ? (
            <div className="tbl">
              <div className="standing-state">
                {group.rows.map((row) => (
                  <StandingRow
                    gap={gapByKey.get(row.key)}
                    key={row.seriesId}
                    row={row}
                  />
                ))}
              </div>
              <p className="snapshot-anchor">
                {group.rows.length === 1
                  ? "This row is a written statement, not a number that failed to arrive."
                  : `These ${group.rows.length} rows are written statements, not numbers that failed to arrive.`}
              </p>
            </div>
          ) : (
            <div className="tbl">
              <table className="snapshot-table">
                <Columns />
                <thead>
                  <tr>
                    <th>Series</th>
                    <th className="num">Value</th>
                    <th className="num">Δ</th>
                    <th className="num c-since">Since read</th>
                    <th className="num c-asof">As-of</th>
                    <th className="num c-next">Next print</th>
                    <th className="c-trend">30d</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {fold(group.rows).map((entry) => (
                    <Row
                      gap={gapByKey.get(entry.row.key)}
                      key={entry.row.seriesId}
                      moved={
                        evaluation !== null && entry.row.asof === evaluation
                      }
                      pair={entry.pair}
                      row={entry.row}
                      trend={trendByKey.get(entry.row.key) ?? []}
                    />
                  ))}
                </tbody>
              </table>
              <p className="snapshot-anchor">
                {`Δ is the change since the last reading. Since read is the change since ${
                  anchor === null ? "" : `${fullDayLabel(anchor)}, `
                }when this week's scenarios were written. 30d is the last 30 days.`}
              </p>
            </div>
          )}
        </section>
      ))}
    </>
  );
}
