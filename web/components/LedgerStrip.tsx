"use client";

import type { ReactNode } from "react";

import { ABSENT } from "@/lib/numbers";

/**
 * One figure the reading is sized against, or a named reason there is none.
 *
 * A withheld reading prints a dash in the faint token with a short label
 * beneath it, never a zero and never a spinner. The blank keeps the tile's
 * height, so a series going quiet does not reflow the strip and move the figure
 * the operator was reading.
 *
 * The label under a withheld tile is a *short* one, not the worker's sentence.
 * The sentence belongs to the banner, which owns it whole; a tile carrying its
 * own copy would claim a second fault and would clip it in a different place.
 */
export function LedgerTile({
  label,
  value,
  sub,
  tone,
  withheld,
  withheldNote,
}: {
  label: string;
  /** Null renders the withheld dash. */
  value: string | null;
  sub?: ReactNode;
  /**
   * A token class for the figure. Deliberately not a direction: nothing in this
   * strip is a position moving in your favour.
   */
  tone?: string;
  /**
   * Why there is no figure, in the operator's words. Short, and never a
   * relation name: "macro_mart.scenario_state has no row" is true and tells a
   * reader who does not hold the schema nothing at all about whether to wait.
   */
  withheld?: string;
  /** The relation behind it, on the tooltip, for the reader who does. */
  withheldNote?: string;
}) {
  const blank = value === null || value === ABSENT;
  return (
    <div className="ledger-cell">
      <span>{label}</span>
      <strong className={blank ? "withheld" : (tone ?? "")}>
        {blank ? ABSENT : value}
      </strong>
      {blank && withheld ? (
        <small className="withheld" title={withheldNote}>
          {withheld}
        </small>
      ) : sub ? (
        <small>{sub}</small>
      ) : (
        // An empty slot rather than no slot: the tiles keep one common height
        // whether or not each of them has a subtitle.
        <small>&nbsp;</small>
      )}
    </div>
  );
}

export function LedgerStrip({
  children,
  label,
}: {
  children: ReactNode;
  label: string;
}) {
  return (
    <div className="ledger" aria-label={label}>
      {children}
    </div>
  );
}
