"use client";

import type { PhaseCallDetail, PhaseScore } from "@/lib/contract";
import { STAGES } from "@/lib/contract";

/**
 * A phase call, in the space a ledger tile has.
 *
 * - **The phase**, large, with the stage beside it: `P3 late`.
 * - **A stage strip**, three segments with the authored one filled, because
 *   early, mid and late are an ordered scale inside one phase.
 * - **One small line** saying how the read stands: the same as last week and
 *   for how long, or what it was last week, and how many of the four things
 *   it holds on are true today. When two have failed the line says the read
 *   may be out of date, and nothing else, because that is the one thing the
 *   operator needs from the tile then.
 *
 * A carried call (one of the migrated journal calls) says so rather than
 * printing a hold count its conditions were never written to give.
 *
 * **A part that was never authored is not drawn.** No stage means no strip.
 */

/** How this week's phase stands against last week's, in words. */
export function standing(
  call: PhaseCallDetail | undefined,
  phase: number,
): string | null {
  if (call === undefined || call.priorPhase === null) return null;
  if (call.priorPhase !== phase) return `was P${call.priorPhase}`;
  const weeks = call.weeksRunning;
  return weeks !== null && weeks > 1
    ? `same for ${weeks} weeks`
    : "same as last week";
}

export function PhaseTile({
  score,
  call,
}: {
  score: PhaseScore | undefined;
  call?: PhaseCallDetail | undefined;
}) {
  if (score === undefined) {
    return (
      <div className="ledger-cell">
        <span>Phase</span>
        <strong className="withheld">—</strong>
        <small className="withheld">no phase call on record</small>
      </div>
    );
  }

  const fresh = call?.classification !== "carried";
  const hold = score.met;
  const move = standing(call, score.phase);
  const tail = !fresh
    ? "carried call"
    : score.readStale
      ? "may be out of date"
      : `${hold}/${score.of} hold`;

  return (
    <div className="ledger-cell phase-tile">
      <span>{score.asset}</span>
      <strong>
        P{score.phase}
        {score.stage === null ? null : (
          <i className="phase-stage-word">{score.stage}</i>
        )}
      </strong>

      {score.stage === null ? null : (
        <span
          aria-label={`Stage ${score.stage} of Phase ${score.phase}`}
          className="phase-stage"
          role="img"
          title={`${score.stage} in Phase ${score.phase}`}
        >
          {STAGES.map((stage) => (
            <i className={stage === score.stage ? "is-here" : ""} key={stage} />
          ))}
        </span>
      )}

      <small
        className={`phase-standing${fresh && score.readStale ? " is-stale" : ""}`}
      >
        {move === null ? null : <span>{move}</span>}
        <b
          title={
            !fresh
              ? "Carried from the weekly journal before the phase was read fresh each Monday."
              : score.readStale
                ? `${score.failed} of the ${score.of} things this read holds on have failed. It is read fresh again on Monday.`
                : `${hold} of the ${score.of} things this read holds on are true today.`
          }
        >
          {tail}
        </b>
      </small>
    </div>
  );
}
