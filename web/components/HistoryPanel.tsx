"use client";

import { useMemo, useState } from "react";

import { useHistory, type HistoryAt, type SetupGroup } from "@/hooks/useHistory";
import { useRenderTick } from "@/hooks/useRenderTick";
import { readSwingVariables } from "@/lib/contract";
import {
  formatBand,
  formatLevel,
  formatProbability,
  formatRR,
} from "@/lib/numbers";
import { deskToday, fullDayLabel } from "@/lib/time";
import { Panel, PanelEmpty } from "./Panel";
import { StatusChip } from "./StatusChip";

/**
 * What was believed on a day, and what changed between two days.
 *
 * `docs/DECISIONS.md` calls this the most valuable screen the system can offer:
 * "what did we believe on 16 September, and what changed since" is a query, not
 * a note, and it is unreachable from markdown. Judgment is never updated in
 * place, so every set, phase call and setup is a row with a validity interval
 * and the superseded row is kept.
 *
 * Three things this view refuses to do.
 *
 * **It does not interpolate a day nothing was stated on.** If no judgment was
 * in force on the chosen date it says so and names the nearest day that has
 * one. Showing the next set forward would date a belief to before it was held.
 *
 * **It does not show what it cannot read.** Before migration 0014 there was no
 * projection of `macro_core.scenario` with a validity interval, and this panel
 * said so rather than rendering today's three under a past date. That would
 * have been misattribution and not staleness: a probability the operator never
 * wrote, printed under a day they did not write it, against which a Brier
 * score would later be read. `macro_mart.scenario_history` now answers it, and
 * the fallback sentence remains for a project that predates the view.
 *
 * **It does not imply a past evaluation.** `macro_mart.setup_history` carries
 * what a setup was *written* as, not what it was doing that day: the gate
 * state, the entry state and the band-touch flags are recomputed from the tape
 * on every run and were never stored per version. The setups table here shows
 * levels and gate text only, and says so.
 *
 * A diff is marked in the accent, never in a direction colour. A base case that
 * went from 45% to 30% is a change of mind, not a loss, and painting it red
 * would make a forecasting record read as a P&L.
 */

function changed(a: unknown, b: unknown): boolean {
  return b !== undefined && !Object.is(a, b);
}

/** `2026-W39` as `W39`, for a strip of weeks that is read left to right. */
function shortWeek(week: string): string {
  return week.slice(week.indexOf("W"));
}

/**
 * The rule numbers a sentence names, each linked to where the rule is written.
 *
 * `R2, R4, R5, R6, R10, R11 and R12` is the footnote's whole explanation of
 * itself, and nothing on this page says what any of them is. The numbers are
 * the only handle the operator has, so each one is a link to the rules block on
 * the Calibration page rather than a number to go and look up elsewhere.
 */
function RuleRef({ number }: { number: number }) {
  return (
    <a
      className="rule-ref"
      href="/calibration#rules"
      title="scenario rules, on the Calibration page"
    >
      R{number}
    </a>
  );
}

/** What a set's three stated probabilities add up to. A constraint says 100. */
function sum(rows: readonly { probability: number }[]): number {
  return rows.reduce((total, row) => total + row.probability, 0);
}

function Amendment({ reason }: { reason: string | null }) {
  if (reason === null) return null;
  return (
    <span className="amendment">
      <b>Amended</b>
      {reason}
    </span>
  );
}

/** The invalidation level and what closes through it, as the setup states it. */
function Invalidation({
  level,
  direction,
}: {
  level: number | null;
  direction: "above" | "below" | null;
}) {
  return (
    <>
      {formatLevel(level)}
      <span className="cadence">
        {direction === null ? "direction not stated" : `close ${direction}`}
      </span>
    </>
  );
}

/**
 * One setup idea, with the weeks it was carried and its latest levels.
 *
 * The same idea re-proposed three times used to be three rows with identical
 * numbers, which reads as three trades. One row, a strip of the weeks it was
 * written in, and the older levels behind a disclosure: the strip says it was
 * carried, and the disclosure says at what.
 */
function SetupGroupRow({
  group,
  other,
}: {
  group: SetupGroup;
  other: HistoryAt | null;
}) {
  const row = group.current;
  const twin = other?.setupGroups.find((g) => g.key === group.key)?.current;
  const moved =
    twin !== undefined &&
    (twin.entryLo !== row.entryLo ||
      twin.entryHi !== row.entryHi ||
      twin.invalidation !== row.invalidation ||
      twin.gateText !== row.gateText);
  const earlier = group.versions.filter((v) => v.setupId !== row.setupId);

  return (
    <tr className={moved ? "diff-changed" : undefined}>
      <td>
        <span className="setup-asset">{group.asset}</span> {group.name}
        {group.versions.length < 2 ? null : (
          <span
            className="history-weeks"
            title={`Re-proposed rather than edited. Carried across ${group.versions.length} weeks; the levels beside this row are ${row.week}'s.`}
          >
            {group.versions.map((v) => shortWeek(v.week)).join(" → ")}
          </span>
        )}
        {row.gateText === null ? null : (
          <small className="setup-detail">gate: {row.gateText}</small>
        )}
        {earlier.length === 0 ? null : (
          <details className="history-versions">
            <summary>
              {earlier.length} earlier version
              {earlier.length === 1 ? "" : "s"}
            </summary>
            <ul>
              {earlier.map((version) => (
                <li key={version.setupId}>
                  <b>{version.week}</b>
                  <span>entry {formatBand(version.entryLo, version.entryHi)}</span>
                  <span>
                    invalidation {formatLevel(version.invalidation)}
                    {version.invalidationDir === null
                      ? ""
                      : ` (close ${version.invalidationDir})`}
                  </span>
                  <span>R:R {formatRR(version.rrPlanned)}</span>
                </li>
              ))}
            </ul>
          </details>
        )}
        <Amendment reason={row.amendReason} />
      </td>
      <td className="num">{formatBand(row.entryLo, row.entryHi)}</td>
      <td className="num">
        <Invalidation direction={row.invalidationDir} level={row.invalidation} />
      </td>
      <td className="num">{formatRR(row.rrPlanned)}</td>
    </tr>
  );
}

function AtColumn({
  at,
  other,
  label,
}: {
  at: HistoryAt;
  /** The other side of the diff, when there is one, for marking changes. */
  other: HistoryAt | null;
  label: string;
}) {
  const empty =
    at.set === null &&
    at.scenarios.length === 0 &&
    at.phaseCalls.length === 0 &&
    at.setups.length === 0;
  const swings = at.set === null ? [] : readSwingVariables(at.set.swingVariables);

  return (
    <div>
      <p className="diff-column-head">
        <span>{label}</span>
        <span>{fullDayLabel(at.day)}</span>
      </p>

      {empty ? (
        <p className="muted">
          No scenario set, phase call or setup was in force on{" "}
          {fullDayLabel(at.day)}. Nothing is shown rather than the nearest set
          forward, because dating a belief to before it was held is the one
          thing a history screen must never do.
        </p>
      ) : (
        <>
          {at.set === null ? null : (
            <div className="history-block">
              <p className="diff-column-head">
                <span>Scenario set</span>
                <span>{at.set.week}</span>
                {changed(at.set.week, other?.set?.week) ? (
                  <span className="chip k">CHANGED</span>
                ) : null}
              </p>
              {at.set.synthesis === null ? null : (
                <p className="muted">{at.set.synthesis}</p>
              )}
              <Amendment reason={at.set.amendReason} />
              <dl className="scenario-set-facts">
                <div>
                  <dt>Rules waived</dt>
                  <dd>
                    {at.set.rulesWaived.length === 0
                      ? "none"
                      : at.set.rulesWaived.map((n, i) => (
                          <span key={n}>
                            {i === 0 ? "" : ", "}
                            <RuleRef number={n} />
                          </span>
                        ))}
                  </dd>
                </div>
                <div>
                  <dt>Swing variables</dt>
                  <dd>{swings.length}</dd>
                </div>
                <div>
                  <dt>Status</dt>
                  <dd>{at.set.status}</dd>
                </div>
              </dl>
              {swings.length === 0 ? null : (
                <details className="history-swings">
                  <summary>
                    The {swings.length} swing variable
                    {swings.length === 1 ? "" : "s"}, and each one&apos;s two
                    branches
                  </summary>
                  <ul>
                    {swings.map((swing) => (
                      <li key={swing.name}>
                        <b>{swing.name}</b>
                        <span className="history-swing-leg">
                          <i>if calmer</i>
                          {swing.cool}
                        </span>
                        <span className="history-swing-leg">
                          <i>if hotter</i>
                          {swing.hot}
                        </span>
                      </li>
                    ))}
                  </ul>
                </details>
              )}
              {at.set.probabilityCheck === null ? null : (
                <details className="probability-check">
                  <summary>The probability check as it was written</summary>
                  <p>{at.set.probabilityCheck}</p>
                </details>
              )}
              {at.scenarios.length === 0 ? (
                <p className="history-unreadable">
                  The three scenarios&apos; titles and probabilities for this
                  week are not shown. This project has no{" "}
                  <code>macro_mart.scenario_history</code>, and rendering
                  today&apos;s three under a past date would attribute a
                  probability to a day nobody wrote it on.
                </p>
              ) : (
                <div className="tbl tbl-narrow" style={{ marginTop: 8 }}>
                  <table>
                    <colgroup>
                      <col style={{ width: "5ch" }} />
                      <col />
                      <col style={{ width: "8ch" }} />
                    </colgroup>
                    <thead>
                      <tr>
                        <th />
                        <th>Scenario</th>
                        <th className="num">Prob</th>
                      </tr>
                    </thead>
                    <tbody>
                      {at.scenarios.map((row) => {
                        const twin = other?.scenarios.find(
                          (s) => s.ordinal === row.ordinal,
                        );
                        const moved =
                          twin !== undefined &&
                          (twin.probability !== row.probability ||
                            twin.title !== row.title);
                        return (
                          <tr
                            className={moved ? "diff-changed" : undefined}
                            key={row.scenarioId}
                          >
                            <td className="num">S{row.ordinal}</td>
                            <td>
                              {row.title}
                              {row.positioning === null ? null : (
                                <small>{row.positioning}</small>
                              )}
                              <Amendment reason={row.amendReason} />
                            </td>
                            <td className="num">
                              {formatProbability(row.probability)}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                    {sum(at.scenarios) === 100 ? null : (
                      <tfoot>
                        <tr>
                          <td colSpan={3} className="tbl-note">
                            These three sum to {sum(at.scenarios)}, not 100. The
                            Brier score read against them is not comparable with
                            the rest of the ledger.
                          </td>
                        </tr>
                      </tfoot>
                    )}
                  </table>
                </div>
              )}
            </div>
          )}

          {at.phaseCalls.length === 0 ? null : (
            <div className="tbl tbl-narrow" style={{ marginTop: 10 }}>
              <table>
                <colgroup>
                  <col style={{ width: "10ch" }} />
                  <col />
                  <col style={{ width: "12ch" }} />
                </colgroup>
                <thead>
                  <tr>
                    <th>Asset</th>
                    <th>Phase · what flips it</th>
                    <th className="num">Direction</th>
                  </tr>
                </thead>
                <tbody>
                  {at.phaseCalls.map((row) => {
                    const twin = other?.phaseCalls.find(
                      (p) => p.asset === row.asset,
                    );
                    const moved =
                      twin !== undefined &&
                      (twin.phase !== row.phase || twin.week !== row.week);
                    return (
                      <tr
                        className={moved ? "diff-changed" : undefined}
                        key={row.phaseCallId}
                      >
                        <td>{row.asset}</td>
                        <td>
                          Phase {row.phase} — {row.phaseName}
                          {row.falsifier === null ? null : (
                            <small>{row.falsifier}</small>
                          )}
                          <Amendment reason={row.amendReason} />
                        </td>
                        <td className="num">{row.direction}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          {at.setupGroups.length === 0 ? null : (
            <div className="tbl tbl-wide" style={{ marginTop: 10 }}>
              <table className="history-setups-table">
                <colgroup>
                  <col />
                  <col style={{ width: "15ch" }} />
                  <col style={{ width: "15ch" }} />
                  <col style={{ width: "8ch" }} />
                </colgroup>
                <thead>
                  <tr>
                    <th>Setup</th>
                    <th className="num">Entry</th>
                    <th className="num">Invalidation</th>
                    <th className="num">R:R</th>
                  </tr>
                </thead>
                <tbody>
                  {at.setupGroups.map((group) => (
                    <SetupGroupRow group={group} key={group.key} other={other} />
                  ))}
                </tbody>
              </table>
              <p className="tbl-note">
                What each setup was <b>written</b> as. The gate state, the entry
                state and the band-touch flags are recomputed from the tape on
                every run and were never stored per version, so there is no past
                value of them to show.
              </p>
            </div>
          )}
        </>
      )}
    </div>
  );
}

export function HistoryPanel() {
  const data = useHistory();
  useRenderTick();
  const today = deskToday(new Date());

  const [asAt, setAsAt] = useState(today);
  const [compare, setCompare] = useState("");

  const left = useMemo(() => data.at(asAt), [data, asAt]);
  const right = useMemo(
    () => (compare === "" ? null : data.at(compare)),
    [data, compare],
  );

  const nearest = useMemo(() => {
    const before = data.statedDays.filter((day) => day <= asAt);
    return before.length > 0 ? before[before.length - 1]! : null;
  }, [data.statedDays, asAt]);

  return (
    <Panel
      empty={
        <PanelEmpty
          reading={data.reading}
          relations={[
            "macro_mart.scenario_set_history",
            "macro_mart.scenario_history",
            "macro_mart.phase_call_history",
            "macro_mart.setup_history",
          ]}
          schedule="the weekly authoring run, which supersedes rather than updates. Nothing that was once believed is overwritten."
        />
      }
      head={<StatusChip state={data.reading.state} />}
      reading={data.reading}
      title="What was believed"
    >
      {data.hidden.length === 0 ? null : (
        <details className="history-hidden">
          <summary>
            Which {data.hidden.length === 1 ? "week" : "weeks"}, and why
          </summary>
          <ul>
            {data.hidden.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        </details>
      )}
      <div className="history-controls">
        <label className="field">
          <span>As at</span>
          <input
            max={today}
            onChange={(event) => setAsAt(event.target.value)}
            type="date"
            value={asAt}
          />
        </label>
        <label className="field">
          <span>Compare to</span>
          <input
            max={today}
            onChange={(event) => setCompare(event.target.value)}
            type="date"
            value={compare}
          />
        </label>
        {data.statedDays.length === 0 ? null : (
          <button
            className="button-plain"
            onClick={() => {
              const days = data.statedDays;
              setAsAt(days[days.length - 1] ?? today);
              setCompare(days.length > 1 ? (days[days.length - 2] ?? "") : "");
            }}
            type="button"
          >
            Last two sets
          </button>
        )}
        <p className="history-hint">
          {data.statedDays.length} day
          {data.statedDays.length === 1 ? "" : "s"} carry a judgment that came
          into force:{" "}
          {data.statedDays.map((day) => fullDayLabel(day)).join(" · ")}.
          {left.set === null && nearest !== null
            ? ` Nothing was in force on ${fullDayLabel(asAt)}; the nearest earlier day with a stated set is ${fullDayLabel(nearest)}.`
            : ""}
        </p>
      </div>

      <div className={`diff-grid${right === null ? "" : " is-comparing"}`}>
        <AtColumn at={left} label="As at" other={right} />
        {right === null ? null : (
          <AtColumn at={right} label="Compare to" other={left} />
        )}
      </div>

      {right === null ? null : (
        <p className="tbl-note" style={{ border: 0, paddingLeft: 0 }}>
          A marked row differs between the two dates. It is marked in the accent
          rather than in a direction colour: a probability that moved from 45%
          to 30% is a change of mind, not a loss. Where a row was re-priced, the
          amendment carries the event that triggered it, because a probability
          that moved with no stated reason is the over-anchoring{" "}
          <RuleRef number={2} />, <RuleRef number={4} />, <RuleRef number={5} />,{" "}
          <RuleRef number={6} />, <RuleRef number={10} />, <RuleRef number={11} />{" "}
          and <RuleRef number={12} /> were all written to stop.
        </p>
      )}
    </Panel>
  );
}
