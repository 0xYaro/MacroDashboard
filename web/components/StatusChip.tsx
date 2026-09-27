import type { ReactNode } from "react";

import type { ConditionStatus, Freshness } from "@/lib/contract";
import { CONDITION_COPY } from "@/lib/conditions";
import { STATE_COPY, isFault } from "@/lib/freshness";

/**
 * The one place a freshness state becomes a colour.
 *
 * Semantic colour lives in `app/globals.css`; this picks the class and nothing
 * else. A component that reached for `var(--down)` because a state "felt bad"
 * is how two surfaces end up describing the same series differently.
 *
 * The chip carries the state's meaning on its own title, because `DUE` is a
 * word that reads like a complaint and is not one: it means the next print is
 * due, not that this one is late.
 */
export function StatusChip({
  state,
  children,
}: {
  state: Freshness | "pending";
  children?: ReactNode;
}) {
  if (state === "pending") {
    return (
      <span
        className="chip state-pending"
        title="No relation behind this panel has answered yet."
      >
        {children ?? "WAITING"}
      </span>
    );
  }
  const copy = STATE_COPY[state];
  return (
    <span className={`chip ${copy.tokenClass}`} title={copy.meaning}>
      {children ?? copy.chipLabel}
    </span>
  );
}

/**
 * The badge beside the reading's own title, which is about the reading and not
 * about the worst vendor under it.
 *
 * It used to be `StatusChip` on `worstFault`, which meant one silent vendor
 * printed FAILED in red beside "The reading · 2026-W39" while forty-nine series
 * were current and every figure on screen was true. The headline answered a
 * question nobody asked. What the reader needs to know here is whether the mart
 * answered at all, and when.
 *
 * Green with the evaluation stamp when it did and nothing is in fault. Amber
 * STALE when it did and something is: the reading is still usable and the strip
 * below says what is held back. Red only when nothing rendered, which is the
 * one case where the screen is not showing a reading.
 */
export function ReadingBadge({
  state,
  hasData,
  stamp,
  missed = 0,
}: {
  state: Freshness | "pending";
  hasData: boolean;
  /** The evaluation clock, already in the desk's zone. Null before one arrives. */
  stamp: string | null;
  /** Scheduled runs that have passed since the stamp. See `missedRuns`. */
  missed?: number;
}) {
  if (state === "pending") return <StatusChip state="pending" />;
  if (!hasData) {
    return (
      <span
        className="chip state-failed"
        title="No row rendered. The relations behind this reading answered and held nothing, or the query itself failed."
      >
        NOTHING SHOWN
      </span>
    );
  }
  if (missed > 0) {
    return (
      <span
        className="chip state-stale"
        title={`The worker has not run since ${stamp ?? "its last stamp"}. ${missed} scheduled run${missed === 1 ? " has" : "s have"} passed, so every rating on this page is as of then, not now.`}
      >
        STALE
      </span>
    );
  }
  if (isFault(state)) {
    return (
      <span
        className="chip state-stale"
        title={`The reading rendered. Its worst series is ${STATE_COPY[state].chipLabel.toLowerCase()}, and the health line above says how many and why.`}
      >
        STALE
      </span>
    );
  }
  return (
    <span
      className="chip state-fresh"
      title="The mart answered and every series is current for its own cadence."
    >
      {stamp === null ? "EVALUATED" : `EVALUATED ${stamp}`}
    </span>
  );
}

/**
 * The same idea for a stated condition.
 *
 * Five states rather than seven, and a different vocabulary, because "is this
 * series current" and "does this clause hold" are different questions. A
 * `void` clause and a `failed` condition mean opposite things: one is a premise
 * that disappeared, the other is evidence against the scenario.
 */
export function ConditionChip({ status }: { status: ConditionStatus }) {
  const copy = CONDITION_COPY[status];
  return (
    <span className={`chip ${copy.tokenClass}`} title={copy.meaning}>
      {copy.label}
    </span>
  );
}
