"use client";

import type { ConditionState } from "@/lib/contract";
import { isStruck } from "@/lib/conditions";
import { ConditionChip } from "./StatusChip";

/**
 * One stated condition: its status, its text, and what it is currently reading.
 *
 * A `void` clause is struck through and dimmed, never removed. The contract is
 * emphatic about why: the clause stays in its `n_of` denominator and the
 * threshold is never renormalised, because renormalising would quietly make the
 * scenario easier to satisfy and corrupt the Brier record. A struck line the
 * reader can still see is the only rendering consistent with that.
 *
 * The reason a clause was voided sits under it and never instead of it. A
 * struck-through line with no reason is something the operator cannot act on.
 *
 * A `manual` condition is marked, because "awaiting judgment" and "evaluated
 * and false" are different claims and only one of them is the machine's.
 *
 * **It is marked only when the card it sits on mixes the two.** On a card
 * whose four conditions are all manual, the coverage line above them already
 * says so in one sentence, and a JUDGMENT tag on each of the four is that one
 * fact repeated four times at chip weight. The tag is a distinction, so it is
 * drawn only where there is something to distinguish it from.
 */
export function ConditionRow({
  condition,
  flagManual = true,
}: {
  condition: ConditionState;
  /**
   * Whether a manual condition wears its tag. False on a card where every
   * condition is manual, because the coverage line has already said it.
   */
  flagManual?: boolean;
}) {
  const struck = isStruck(condition.status);
  return (
    <div className={`cond-row${struck ? " is-void" : ""}`}>
      <ConditionChip status={condition.status} />
      <span className="cond-text">
        {condition.text}
        {flagManual && condition.kind === "manual" ? (
          <span
            className="row-flag"
            title="A condition that is read and called by hand. It is never scored by the worker, and the tracker counts it separately from the machine-checked ones."
          >
            JUDGMENT
          </span>
        ) : null}
      </span>
      <span
        className={`cond-current${condition.currentDisplay === null ? " absent" : ""}`}
        title={condition.currentDisplay ?? undefined}
      >
        {condition.currentDisplay ?? "no reading"}
      </span>
      {condition.voidReason === null ? null : (
        <p className="cond-reason">{condition.voidReason}</p>
      )}
    </div>
  );
}
