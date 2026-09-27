"use client";

import { useMemo, useState } from "react";

import type { ObservationHistory } from "@/lib/contract";
import { formatLevel } from "@/lib/numbers";
import { dayLabel, fullDayLabel } from "@/lib/time";

/**
 * One series, drawn properly and walkable across timeframes.
 *
 * Hand-rolled inline SVG. No chart library: the stack is pinned and
 * Accounting's NAV chart is the precedent, so its geometry doctrine is
 * followed. Horizontal positions are percentages of the SVG's own width, so
 * the plot scales with the disclosure; vertical positions are pixels, so the
 * axis type never stretches.
 *
 * Three rules carried over from the sparkline, all of which were right.
 *
 * **A gap breaks the line rather than being bridged.** Bridging draws a trend
 * through a stretch nothing stated, which reads as a series that moved
 * smoothly when the truth is that nobody looked.
 *
 * **The break threshold is three times the series' own median step**, not a
 * number of days. A day-based rule would break every weekly line at every
 * point, which is exactly the series where a real gap matters most.
 *
 * **Nothing is drawn across a single observation.** A one-point line is not a
 * trend and a two-point line drawn confidently is a claim.
 *
 * The timeframe buttons narrow what is *drawn*, not what was fetched: the
 * whole series is already in hand, so a window is a question about the plot
 * and never another read. A window holding fewer than two observations is
 * offered but marked, so the reader can see that the series does not have the
 * history rather than wondering why the chart is empty.
 *
 * No direction colour. A series going up is not a position moving in your
 * favour, and the row above already carries its stated delta.
 */

/**
 * What a cell says when the observation carried no read.
 *
 * One sentence, stated once, and shared by every surface that renders the
 * absence, so the three cannot drift into three different phrasings of the
 * same fact. `No insights.` was the old wording and it read as a verdict on
 * the series rather than as a note about the record: the reader asked whether
 * the analyst had nothing to say, when the truth is that nothing was written
 * down on that day.
 */
export const NO_READ = "No read was written for this observation.";

const HEIGHT = 150;
const PAD_TOP = 10;
const PAD_BOTTOM = 20;

const WINDOWS: readonly { id: string; name: string; days: number | null }[] = [
  { id: "7d", name: "7D", days: 7 },
  { id: "30d", name: "30D", days: 30 },
  { id: "90d", name: "90D", days: 90 },
  { id: "all", name: "All", days: null },
];

function breaks(days: readonly number[]): number {
  if (days.length < 2) return Number.POSITIVE_INFINITY;
  const steps = days
    .slice(1)
    .map((day, index) => day - days[index]!)
    .filter((step) => step > 0)
    .sort((a, b) => a - b);
  const median = steps[Math.floor(steps.length / 2)] ?? 1;
  return median * 3;
}

const asDay = (asof: string) => Date.parse(`${asof}T00:00:00Z`) / 86_400_000;

export function SeriesChart({
  rows,
  label,
}: {
  rows: readonly ObservationHistory[];
  label: string;
}) {
  const [window, setWindow] = useState("all");
  const [cursor, setCursor] = useState<number | null>(null);

  const measured = useMemo(
    () =>
      rows
        .filter((row) => row.value !== null)
        .sort((a, b) => a.asof.localeCompare(b.asof)),
    [rows],
  );

  const windowed = useMemo(() => {
    const days = WINDOWS.find((w) => w.id === window)?.days ?? null;
    if (days === null || measured.length === 0) return measured;
    const last = asDay(measured[measured.length - 1]!.asof);
    return measured.filter((row) => last - asDay(row.asof) <= days);
  }, [measured, window]);

  const plot = useMemo(() => {
    if (windowed.length < 2) return null;
    const values = windowed.map((row) => row.value!);
    const lo = Math.min(...values);
    const hi = Math.max(...values);
    // A flat series still needs a band to sit in, or every point lands on one
    // pixel and the line vanishes.
    const span = hi - lo || Math.abs(hi) * 0.02 || 1;
    const days = windowed.map((row) => asDay(row.asof));
    const first = days[0]!;
    const width = days[days.length - 1]! - first || 1;
    const maxStep = breaks(days);

    const points = windowed.map((row, index) => ({
      x: ((days[index]! - first) / width) * 100,
      y:
        PAD_TOP +
        (1 - (row.value! - lo) / span) * (HEIGHT - PAD_TOP - PAD_BOTTOM),
      row,
    }));

    const segments: (typeof points)[] = [];
    let run: typeof points = [];
    points.forEach((point, index) => {
      if (index > 0 && days[index]! - days[index - 1]! > maxStep) {
        if (run.length > 0) segments.push(run);
        run = [];
      }
      run.push(point);
    });
    if (run.length > 0) segments.push(run);

    return { points, segments, lo, hi };
  }, [windowed]);

  const at = cursor === null ? null : (plot?.points[cursor] ?? null);

  return (
    <div className="chart">
      <div className="chart-head">
        <span className="chart-title">{label}</span>
        <div className="chart-windows">
          {WINDOWS.map((option) => {
            const days = option.days;
            const inWindow =
              days === null || measured.length === 0
                ? measured.length
                : measured.filter(
                    (row) =>
                      asDay(measured[measured.length - 1]!.asof) -
                        asDay(row.asof) <=
                      days,
                  ).length;
            return (
              <button
                aria-pressed={option.id === window}
                className="chart-window"
                key={option.id}
                onClick={() => {
                  setWindow(option.id);
                  setCursor(null);
                }}
                title={
                  inWindow < 2
                    ? `${inWindow} observation${inWindow === 1 ? "" : "s"} in this window. Nothing is drawn across fewer than two: a one-point line is not a trend.`
                    : `${inWindow} observations`
                }
                type="button"
              >
                {option.name}
                {inWindow < 2 ? <i className="chart-window-thin">·</i> : null}
              </button>
            );
          })}
        </div>
      </div>

      {plot === null ? (
        <p className="chart-absent">
          {measured.length === 0
            ? "No observation in macro_mart.observation_history for this series."
            : windowed.length === 1
              ? "One observation in this window. Nothing is drawn across a single point: a line there would be a trend nobody measured."
              : "No observation in this window."}
        </p>
      ) : (
        <>
          <svg
            aria-label={`${label}: ${windowed.length} observations from ${fullDayLabel(windowed[0]!.asof)} to ${fullDayLabel(windowed[windowed.length - 1]!.asof)}`}
            className="chart-svg"
            height={HEIGHT}
            onMouseLeave={() => setCursor(null)}
            onMouseMove={(event) => {
              const box = event.currentTarget.getBoundingClientRect();
              if (box.width === 0) return;
              const pct = ((event.clientX - box.left) / box.width) * 100;
              // Nearest point, not interpolated: the cursor reads an
              // observation that exists, never a value between two.
              let best = 0;
              let bestGap = Number.POSITIVE_INFINITY;
              plot.points.forEach((point, index) => {
                const gap = Math.abs(point.x - pct);
                if (gap < bestGap) {
                  bestGap = gap;
                  best = index;
                }
              });
              setCursor(best);
            }}
            preserveAspectRatio="none"
            role="img"
            viewBox={`0 0 100 ${HEIGHT}`}
          >
            <line
              className="chart-grid"
              vectorEffect="non-scaling-stroke"
              x1="0"
              x2="100"
              y1={PAD_TOP}
              y2={PAD_TOP}
            />
            <line
              className="chart-grid"
              vectorEffect="non-scaling-stroke"
              x1="0"
              x2="100"
              y1={HEIGHT - PAD_BOTTOM}
              y2={HEIGHT - PAD_BOTTOM}
            />
            {plot.segments.map((segment, index) => (
              <polyline
                className="chart-line"
                key={index}
                points={segment.map((p) => `${p.x},${p.y}`).join(" ")}
                vectorEffect="non-scaling-stroke"
              />
            ))}
            {/* A measured point with a break either side is drawn rather than
                dropped: it is a reading we hold, and a line cannot be struck
                through one point. */}
            {plot.segments
              .filter((segment) => segment.length === 1)
              .map((segment, index) => (
                <circle
                  className="chart-point"
                  cx={segment[0]!.x}
                  cy={segment[0]!.y}
                  key={index}
                  r={1.6}
                />
              ))}
            {at === null ? null : (
              <>
                <line
                  className="chart-cursor"
                  vectorEffect="non-scaling-stroke"
                  x1={at.x}
                  x2={at.x}
                  y1={PAD_TOP}
                  y2={HEIGHT - PAD_BOTTOM}
                />
                <circle
                  className="chart-cursor-dot"
                  cx={at.x}
                  cy={at.y}
                  r={2.2}
                />
              </>
            )}
          </svg>

          {/* Two lines reserved, so moving the cursor onto the plot cannot
              shift the table beneath it. */}
          <p className="chart-readout">
            {at === null ? (
              <>
                <span>
                  {dayLabel(windowed[0]!.asof)} –{" "}
                  {dayLabel(windowed[windowed.length - 1]!.asof)}
                </span>
                <span>
                  {windowed.length} obs
                  {plot.segments.length > 1
                    ? ` · ${plot.segments.length - 1} break${plot.segments.length === 2 ? "" : "s"}`
                    : ""}
                </span>
                <span>
                  {formatLevel(plot.lo)} – {formatLevel(plot.hi)}
                </span>
              </>
            ) : (
              <>
                <span>{fullDayLabel(at.row.asof)}</span>
                <span className="chart-readout-value">
                  {at.row.display ?? formatLevel(at.row.value)}
                </span>
                <span className="chart-readout-note">
                  {at.row.note ?? NO_READ}
                </span>
              </>
            )}
          </p>
        </>
      )}
    </div>
  );
}
