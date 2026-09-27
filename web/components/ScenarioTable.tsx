"use client";

import type {
  ConditionState,
  ScenarioState,
  ScenarioTargetState,
} from "@/lib/contract";
import { isManualTarget } from "@/lib/contract";
import { CONDITION_COPY, isStruck } from "@/lib/conditions";
import { formatOutOf, formatProbability } from "@/lib/numbers";
import { BandRow } from "./BandExtent";

/**
 * The three scenarios as one comparison table, which is the journal's own shape.
 *
 * Section 4 writes one row per scenario with columns for the trigger, the asset
 * targets and the positioning, and it is written that way because the useful
 * question is the difference between the three, not the contents of any one.
 * Three cards make the reader hold S1's breadth band in their head while they
 * scroll to S2's; a table puts them one above the other.
 *
 * **What a card had room for and a row does not is the live state**, so it is
 * compressed into the scenario cell rather than spread across the row: the
 * clauses that have fired, and one line for the targets.
 *
 * **The targets are one line with one denominator.** It used to be two
 * counters, `Bands holding 11 of 13` above `Targets holding 11 of 14`, with a
 * footnote under the table explaining that reading one as the other was the
 * W36 defect. A reader who has to be warned off a confusion in a footnote has
 * already been handed the confusion. The line now says `11 of 14 in band · 1
 * needs judgment · 2 unevaluable` against the single denominator the week is
 * graded on, and the reason the other figure is not shown is that the split
 * the reader actually needs is the one already on the line: what was scored,
 * what is waiting for a call, and what nobody could read. The old footnote's
 * explanation is kept as the line's own title.
 */

function Counter({
  label,
  hit,
  of,
  note,
  title,
}: {
  label: string;
  hit: number | null;
  of: number | null;
  note?: string;
  title: string;
}) {
  return (
    <span className="sc-counter" title={title}>
      <b>{label}</b>
      <span className="sc-counter-value">
        {hit === null || of === null ? "—" : `${hit} of ${of}`}
      </span>
      {note === undefined ? null : <i>{note}</i>}
    </span>
  );
}

/**
 * A scenario's trigger, one clause per line.
 *
 * **A chip appears only on a clause that was evaluated.** Four clauses each
 * wearing an `UNEVALUABLE` chip is four chips that distinguish nothing: the
 * chips were the loudest thing in the column and every one of them said the
 * same word. An unevaluated clause is now plain text, and the fact that it was
 * not evaluated is stated once for the scenario, under the clauses, in the
 * faint token. `MET`, `NOT YET`, `FAILED` and `VOID` keep their chips, because
 * each of those is a verdict and the reader is scanning for the ones that
 * moved. `NOT YET` in particular keeps its chip: evaluated and false is a
 * claim, and it is exactly the claim an unevaluated clause must never be
 * confused with.
 */
function TriggerCell({ clauses }: { clauses: readonly ConditionState[] }) {
  if (clauses.length === 0) {
    return <span className="muted">No trigger clauses written.</span>;
  }
  const awaiting = clauses.filter((c) => c.status === "unevaluable").length;
  return (
    <>
      <ul
        className={
          awaiting === clauses.length ? "sc-clauses is-plain" : "sc-clauses"
        }
      >
        {[...clauses]
          .sort((a, b) => a.ordinal - b.ordinal)
          .map((clause) => (
            <li
              className={isStruck(clause.status) ? "is-void" : undefined}
              key={clause.conditionId}
            >
              {clause.status === "unevaluable" ? null : (
                <span
                  className={`sc-dot ${CONDITION_COPY[clause.status].tokenClass}`}
                  title={CONDITION_COPY[clause.status].meaning}
                >
                  {CONDITION_COPY[clause.status].label}
                </span>
              )}
              <span className="sc-clause-text">
                {clause.text}
                {clause.currentDisplay === null ? null : (
                  <i className="sc-clause-now">{clause.currentDisplay}</i>
                )}
                {clause.voidReason === null ? null : (
                  <i className="sc-clause-void">{clause.voidReason}</i>
                )}
              </span>
            </li>
          ))}
      </ul>
      {awaiting === 0 ? null : (
        <p
          className="sc-clauses-note"
          title="A clause with no chip was not checked this run: its data had no reading for the date, or a person has to judge it. It never counts as not yet."
        >
          {awaiting} of {clauses.length} need judgment
        </p>
      )}
    </>
  );
}

export function ScenarioTable({
  scenarios,
  targets,
  conditions,
}: {
  scenarios: readonly ScenarioState[];
  targets: readonly ScenarioTargetState[];
  conditions: readonly ConditionState[];
}) {
  if (scenarios.length === 0) {
    return (
      <p className="muted">
        No scenarios yet for this week. Each week gets three, with odds that add
        up to 100.
      </p>
    );
  }

  const ordered = [...scenarios].sort((a, b) => a.ordinal - b.ordinal);
  // The base case is the highest probability, not the first ordinal: the
  // Scorecard Ledger's base has been S1, S2 and S3 across the record.
  const baseId = ordered.reduce((best, s) =>
    s.probability > best.probability ? s : best,
  ).scenarioId;
  const total = ordered.reduce((sum, s) => sum + s.probability, 0);

  return (
    <>
      {total === 100 ? null : (
        <p className="panel-banner">
          The three odds add up to {total}, not 100. They must add up to 100, so
          this week&apos;s accuracy score will not compare fairly with other
          weeks.
        </p>
      )}
      <div className="tbl tbl-wide">
        <table className="scenario-table">
          <colgroup>
            <col className="c-scenario" />
            <col className="c-trigger" />
            <col className="c-targets" />
          </colgroup>
          <thead>
            <tr>
              <th>Scenario</th>
              <th>Trigger</th>
              <th>Asset targets</th>
            </tr>
          </thead>
          <tbody>
            {ordered.map((scenario) => {
              const mine = targets.filter(
                (t) => t.scenarioId === scenario.scenarioId,
              );
              const manual = mine.filter(isManualTarget).length;
              const unevaluable = mine.filter(
                (t) => !isManualTarget(t) && t.holding === null,
              ).length;
              const isBase = scenario.scenarioId === baseId;
              return (
                <tr
                  className={isBase ? "row-base" : undefined}
                  key={scenario.scenarioId}
                >
                  <td>
                    <p className="sc-head">
                      <span className="sc-ordinal">S{scenario.ordinal}</span>
                      <span className="sc-prob">
                        {formatProbability(scenario.probability)}
                      </span>
                    </p>
                    <p className="sc-title">{scenario.title}</p>
                    <p className="sc-chips">
                      {isBase ? (
                        <span
                          className="chip k"
                          title="The base case: the most likely scenario. The ledger's hit rate and accuracy score are measured against it."
                        >
                          BASE
                        </span>
                      ) : null}
                      {scenario.premiseMoved ? (
                        <span
                          className="chip premise-moved"
                          title="An event this scenario's trigger named was cancelled or moved past its time frame. That clause is void but still counts, the bar is not lowered, and the accuracy score is not excused."
                        >
                          PREMISE MOVED
                        </span>
                      ) : null}
                    </p>
                    <div className="sc-counters">
                      <Counter
                        hit={scenario.clausesFired}
                        label="Clauses fired"
                        note={
                          [
                            scenario.clausesVoid > 0
                              ? `${scenario.clausesVoid} void, kept in`
                              : null,
                            scenario.requiredUnmet > 0
                              ? `${scenario.requiredUnmet} required unmet`
                              : null,
                          ]
                            .filter(Boolean)
                            .join(" · ") || undefined
                        }
                        of={scenario.clausesTotal}
                        title="A void clause still counts. The bar is never lowered to match, because that would quietly make the scenario easier to pass."
                      />
                      <p
                        className="sc-targets-line"
                        title="Out of every target this scenario states, including the ones a person judges, which is what the week is graded on. A target waiting for judgment or with no reading never counts as a miss."
                      >
                        {[
                          `${formatOutOf(
                            scenario.targetsHolding,
                            scenario.targetsTotal,
                          )} in band`,
                          manual > 0
                            ? `${manual} need${manual === 1 ? "s" : ""} judgment`
                            : null,
                          unevaluable > 0 ? `${unevaluable} no reading` : null,
                        ]
                          .filter(Boolean)
                          .join(" · ")}
                      </p>
                    </div>
                    {/* Under the scenario it belongs to rather than in a
                        fourth column: at 1200px that column ran off the panel
                        and the one instruction a scenario gives the book was
                        the part a reader had to scroll sideways to find. */}
                    <p className="sc-positioning">
                      <span className="desk-k">Positioning</span>{" "}
                      {scenario.positioning ?? (
                        <span className="muted">none stated</span>
                      )}
                    </p>
                  </td>

                  <td>
                    <TriggerCell
                      clauses={conditions.filter(
                        (c) =>
                          c.ownerKind === "trigger" &&
                          c.ownerId === scenario.scenarioId,
                      )}
                    />
                  </td>

                  <td>
                    {mine.length === 0 ? (
                      <span className="muted">
                        No targets stated, so there is nothing to check this
                        scenario against.
                      </span>
                    ) : (
                      mine.map((target) => (
                        <BandRow
                          compact
                          key={target.targetId}
                          target={target}
                        />
                      ))
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}
