"use client";

import type { MacroData } from "@/hooks/useMacro";
import { readSwingVariables } from "@/lib/contract";
import { shortWeek } from "@/lib/conditions";
import { fullDayLabel } from "@/lib/time";
import { PhaseView } from "./PhaseView";
import { ScenarioTable } from "./ScenarioTable";
import { SetupsView } from "./SetupsView";

/**
 * The weekly half of the reading, on one page: scenarios, then the phase
 * calls, then the setups.
 *
 * Named for the system's own split. `docs/DECISIONS.md` divides the clock in
 * two: evidence moves daily and judgment moves weekly. Snapshot is the
 * evidence. Everything here was authored once, on Monday, and between
 * authorings is only re-scored and never rewritten, which is exactly what
 * makes it one page rather than three: a reader checking whether the week's
 * call still holds wants the scenario set, the phase it was written under and
 * the trades it implies in one pass, not three tab presses apart.
 *
 * Scenarios first because the phase call and the setups are both read against
 * them, and setups last because they are what the other two produce.
 *
 * This changes nothing about the one-reading rule. It is the same response
 * under the same clocks, and walking to it from Snapshot still issues no query.
 *
 * **The three section titles name the surface and the week and stop there.**
 * `THE PHASE CALL · equities and crypto · re-scored, never rewritten, between
 * authorings` spent a title line on doctrine the reader had already accepted
 * by opening the page, and `THE HOUSE VIEW · no set · 3 scenarios` reported a
 * missing relation as though it were the week's name. The doctrine is in this
 * file's own prose and in `docs/CONTRACT.md`, where it belongs.
 */
export function JudgmentView({ data }: { data: MacroData }) {
  const set = data.scenarioSet;
  const swings = set === null ? [] : readSwingVariables(set.swingVariables);
  // The week, from whichever row carries it. A scenario set names it directly;
  // when the synthesis has not been written the setups and the phase calls
  // still know which week is on screen, and a title reading `no set` told the
  // reader about a relation rather than about the week.
  const week =
    shortWeek(set?.week) ??
    shortWeek(data.setups[0]?.week) ??
    shortWeek(data.phaseCalls[0]?.week);
  const synthesisState =
    set === null
      ? "summary not written yet"
      : set.synthesis === null
        ? "no summary written"
        : `${data.scenarios.length} scenarios`;

  return (
    <>
      <section className="judgment-section">
        <div className="snapshot-group-head">
          <h3>This week&apos;s view</h3>
          <span className="snapshot-group-count">
            {week ?? "week not stated"}
          </span>
          <span className="snapshot-group-count">{synthesisState}</span>
        </div>

        {set === null ? (
          <p className="muted">
            The summary{week === null ? "" : ` for ${week}`} is not written yet.
            The scenarios below are complete.
          </p>
        ) : (
          <div className="scenario-set-head">
            <div
              className={
                swings.length === 0
                  ? "scenario-set-split is-single"
                  : "scenario-set-split"
              }
            >
              {set.synthesis === null ? (
                <p className="faint">
                  No summary was written for this set. The scenarios below are
                  the forecast.
                </p>
              ) : (
                <p>{set.synthesis}</p>
              )}
              {swings.length === 0 ? null : (
                <ol className="swing-list">
                  {swings.map((swing) => (
                    <li key={swing.name}>
                      <b>{swing.name}</b>
                      <span className="swing-leg">
                        <i>if calmer</i> {swing.cool}
                      </span>
                      <span className="swing-leg">
                        <i>if hotter</i> {swing.hot}
                      </span>
                    </li>
                  ))}
                </ol>
              )}
            </div>
            {set.probabilityCheck === null ? null : (
              <details className="probability-check">
                <summary>
                  How the odds were checked against rules R1 to R12
                </summary>
                <p>{set.probabilityCheck}</p>
              </details>
            )}
            <dl className="scenario-set-facts">
              <div>
                <dt>In force since</dt>
                <dd>
                  {set.activatedAt === null
                    ? "proposed, not yet active"
                    : fullDayLabel(set.activatedAt.slice(0, 10))}
                </dd>
              </div>
              <div>
                <dt>Rules waived</dt>
                <dd>
                  {set.rulesWaived.length === 0
                    ? "none"
                    : set.rulesWaived.map((n) => `R${n}`).join(", ")}
                </dd>
              </div>
            </dl>
          </div>
        )}

        <ScenarioTable
          conditions={data.conditions}
          scenarios={data.scenarios}
          targets={data.targets}
        />
      </section>

      <section className="judgment-section">
        <div className="snapshot-group-head">
          <h3>Phase call</h3>
          <span className="snapshot-group-count">
            {week ?? "week not stated"}
          </span>
        </div>
        <PhaseView
          calls={data.phaseCalls}
          conditions={data.conditions}
          scores={data.phaseScores}
        />
      </section>

      <section className="judgment-section">
        <div className="snapshot-group-head">
          <h3>Setups</h3>
          <span className="snapshot-group-count">
            {data.setups.length} live
          </span>
        </div>
        <SetupsView setups={data.setups} strategies={data.strategies} />
      </section>
    </>
  );
}
