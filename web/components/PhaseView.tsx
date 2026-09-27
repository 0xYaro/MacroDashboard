"use client";

import type {
  ConditionState,
  PhaseCallDetail,
  PhaseScore,
} from "@/lib/contract";
import { martDisagreement, nextPhaseRead, phaseMove } from "@/lib/contract";
import { coverageSentence } from "@/lib/conditions";
import { DESK_ZONE, parseInstant, weekdayDay } from "@/lib/time";
import { ConditionChip } from "./StatusChip";
import { ConditionRow } from "./ConditionRow";

/**
 * The two phase calls, each read fresh from the data once a week.
 *
 * `docs/DECISIONS.md` 2026-09-23: every Monday the phase is classified from the
 * data and the frameworks with last week's call out of view. Any phase may
 * follow any phase, so there is no upgrade, no 3-of-4 rule and no "toward the
 * next phase". Last week's call is shown only to say what moved.
 *
 * The card reads top to bottom as the operator asks it:
 *
 * - **What is it**, in the header, with how many of its four conditions hold.
 * - **When was it read, and when is it read next.** A read is a snapshot, so
 *   the date it was taken is part of the call.
 * - **How it stands to last week**: the same for eight weeks, or `was P3`.
 * - **Holds while**: the four things that must stay true for this read to
 *   hold, each met or failed. Two failed means the read may be out of date,
 *   which is said plainly and never acted on: the next read is Monday's.
 *
 * A carried call, one of the fourteen migrated journal calls, is labelled for
 * what it is. Its four conditions were written as signs of the next phase, and
 * they are shown that way rather than relabelled as something they never were.
 */

function readWhen(
  call: PhaseCallDetail,
  now: Date,
): {
  read: string | null;
  next: string | null;
  overdue: boolean;
} {
  const at = parseInstant(call.classifiedAt ?? call.activatedAt);
  const next = nextPhaseRead(call, now);
  return {
    read: at === null ? null : weekdayDay(at, DESK_ZONE),
    // The next read is a calendar Monday, a UTC midnight by construction.
    next: next === null ? null : weekdayDay(next.date, "UTC"),
    overdue: next?.overdue ?? false,
  };
}

/**
 * How the four stand, in plain words: `4 of 4 hold`, `2 hold, 2 failed`, and
 * only then what was not checked by machine today. The machine-checked count
 * is left out when it is simply all of them, because `4 of 4 hold · 4 of 4
 * machine-checked` says the same number twice.
 */
export function holdsSentence(score: PhaseScore): string {
  const head =
    score.failed > 0
      ? `${score.met} hold, ${score.failed} failed`
      : `${score.met} of ${score.of} hold`;
  const parts = [head];
  if (score.manual > 0) parts.push(`${score.manual} judged by hand`);
  if (score.unevaluable > 0) {
    parts.push(`${score.unevaluable} not checked today, no reading`);
  }
  return parts.join(" · ");
}

function PhaseCard({
  score,
  call,
  conditions,
  now,
}: {
  score: PhaseScore;
  call: PhaseCallDetail | undefined;
  conditions: readonly ConditionState[];
  now: Date;
}) {
  // The two projections name the same call except between an accept and the
  // rescore that follows it; then the chips below are the previous call's.
  const inconsistent = martDisagreement(score, call);
  const ordered = [...conditions].sort((a, b) => a.ordinal - b.ordinal);
  const fresh = call?.classification !== "carried";

  // The per-condition JUDGMENT tag earns its place only when the card is mixed.
  const mixed =
    ordered.some((c) => c.kind === "manual") &&
    ordered.some((c) => c.kind !== "manual");

  const when = call === undefined ? null : readWhen(call, now);
  const move = call === undefined ? null : phaseMove(call);
  const moved =
    call !== undefined &&
    call.priorPhase !== null &&
    call.priorPhase !== call.phase;

  const chip = !fresh
    ? {
        className: "cond-not-yet",
        text: "carried call",
        title:
          "Carried from the weekly journal before the phase was read fresh each Monday.",
      }
    : score.readStale
      ? {
          className: "state-stale",
          text: `${score.failed} of ${score.of} failed`,
          title:
            "Two or more of the four things this read holds on have failed.",
        }
      : {
          className: score.met === score.of ? "cond-met" : "cond-not-yet",
          text: `Holds ${score.met} of ${score.of}`,
          title:
            "How many of the four things this read holds on are true today.",
        };

  return (
    <article className="phase-card" aria-label={`${score.asset} phase call`}>
      <header className="phase-card-head">
        <span className="phase-asset">{score.asset}</span>
        <span className="phase-name">
          Phase {score.phase}: {score.phaseName}
        </span>
        <span className={`chip ${chip.className}`} title={chip.title}>
          {chip.text}
        </span>
      </header>

      <div className="phase-body">
        {when === null ? null : (
          <p className="phase-meta">
            {when.read === null ? null : <span>Read {when.read}</span>}
            {when.next === null ? null : (
              <span className={when.overdue ? "is-overdue" : undefined}>
                {when.read === null ? null : <b aria-hidden="true">·</b>}
                {when.overdue
                  ? `next read was due ${when.next}`
                  : `next read ${when.next}`}
              </span>
            )}
          </p>
        )}

        {move === null ? null : (
          <p className={`phase-move${moved ? " is-moved" : ""}`}>
            {move}
            {call?.changeNote ? <span>{call.changeNote}</span> : null}
          </p>
        )}

        {fresh && score.readStale ? (
          <p className="phase-divergence">
            This read may be out of date: {score.failed} of the {score.of}{" "}
            things it holds on have failed since it was made. The phase is read
            fresh again {when?.next ? `on ${when.next}` : "next Monday"}.
          </p>
        ) : null}

        {!fresh && when?.next ? (
          <p className="phase-reading-why">
            {`Carried from the weekly journal. The first fresh read is ${when.next}.`}
          </p>
        ) : null}

        {inconsistent === null ? null : (
          <p className="phase-divergence">{inconsistent}</p>
        )}

        <p className="phase-reason">
          {call === undefined ? (
            <span className="muted">
              The reason, the evidence and what would change this call have not
              been written.
            </span>
          ) : (
            call.reason
          )}
        </p>

        {/* The stage, where inside the phase the reading sits, named once
            and in front of the reason it was authored with. */}
        {score.stage === null ? null : (
          <p className="phase-reading-why">
            <b className="phase-stage-label">
              {score.stage} in Phase {score.phase}
            </b>
            {call?.stageReason ?? null}
          </p>
        )}

        <p className="phase-conditions-head">
          <b>
            {fresh
              ? "Holds while"
              : `Signs of Phase ${(score.phase % 5) + 1} (old journal)`}
          </b>
          <span>
            {fresh
              ? holdsSentence(score)
              : `${score.met} of ${score.of} met · ${coverageSentence({
                  machineChecked: score.machineChecked,
                  manual: score.manual,
                  unevaluable: score.unevaluable,
                  of: score.of,
                })}`}
          </span>
        </p>

        <div className="cond-list">
          {ordered.length === 0 ? (
            <p className="muted">
              No conditions are on record for this call. A call carries exactly
              four.
            </p>
          ) : (
            ordered.map((condition) => (
              <ConditionRow
                condition={condition}
                flagManual={mixed}
                key={condition.conditionId}
              />
            ))
          )}
        </div>

        {call === undefined || call.evidence.length === 0 ? null : (
          <ul className="phase-evidence">
            {call.evidence.map((line, index) => (
              <li key={index}>{line}</li>
            ))}
          </ul>
        )}

        {call === undefined ? null : (
          <p className="phase-falsifier">
            <b>What would flip this</b>
            <br />
            {call.falsifier}
          </p>
        )}
      </div>
    </article>
  );
}

export function PhaseView({
  scores,
  calls,
  conditions,
  now = new Date(),
}: {
  scores: readonly PhaseScore[];
  calls: readonly PhaseCallDetail[];
  conditions: readonly ConditionState[];
  now?: Date;
}) {
  // Equities first, then crypto, as the journal orders them.
  const order = ["equities", "crypto"];
  const ordered = [...scores].sort(
    (a, b) => order.indexOf(a.asset) - order.indexOf(b.asset),
  );

  if (ordered.length === 0) {
    return (
      <p className="muted">
        No phase call yet. Only stocks and crypto get one. Metals, oil, rates
        and the dollar show up in the Data table and as scenario targets.
      </p>
    );
  }

  return (
    <div className="phase-grid">
      {ordered.map((score) => (
        <PhaseCard
          call={calls.find((c) => c.asset === score.asset)}
          conditions={conditions.filter(
            (c) => c.ownerKind === "phase" && c.ownerId === score.phaseCallId,
          )}
          key={score.asset}
          now={now}
          score={score}
        />
      ))}
      <p
        className="phase-legend"
        title="A condition with no reading for the day never counts as failed. Counting it would make the read look weaker than the evidence shows."
      >
        <ConditionChip status="unevaluable" />
        <span>is never counted as</span>
        <ConditionChip status="failed" />
      </p>
    </div>
  );
}
