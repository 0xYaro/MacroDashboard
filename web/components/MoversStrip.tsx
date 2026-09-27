"use client";

import Link from "next/link";

import type { ObservationHistory, SnapshotRow } from "@/lib/contract";
import { unusualMoves } from "@/lib/desk";

/**
 * The day's moves, ranked by how unusual each is for its own series.
 *
 * Seventy-odd rows in journal order put a 2-sigma VIX move at the same visual
 * weight as a funding rate that did nothing, and finding the one was the
 * reader's job. This strip does that job and nothing else: the table below is
 * unchanged, and each chip is a link to its row there.
 */
export function MoversStrip({
  rows,
  trends,
}: {
  rows: readonly SnapshotRow[];
  trends: readonly ObservationHistory[];
}) {
  const movers = unusualMoves(rows, trends);
  // Said, not hidden: an empty strip on a quiet day would read as a strip that
  // failed to load.
  if (movers.length === 0) {
    return (
      <div className="movers" aria-label="Unusual moves">
        <span className="desk-k">Unusual today</span>
        <span className="muted">
          Nothing moved more than one standard deviation of its own daily range.
        </span>
      </div>
    );
  }
  return (
    <div className="movers" aria-label="Unusual moves">
      <span className="desk-k">Unusual today</span>
      <ul className="movers-list">
        {movers.map((one) => (
          <li key={one.key}>
            <Link
              className={one.sigma >= 2 ? "mover mover-loud" : "mover"}
              href={`#series-${one.key}`}
              title="The last day's change, in standard deviations of this series' own daily changes over thirty days."
            >
              <b>{one.label}</b>
              <span>{one.sigma.toFixed(1)}σ</span>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
