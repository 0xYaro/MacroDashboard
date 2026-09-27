"use client";

import { useState } from "react";

import { useCalibration } from "@/hooks/useCalibration";
import { useRenderTick } from "@/hooks/useRenderTick";
import type { Grade, OtherGrade, WeekGrade } from "@/lib/contract";
import { ABSENT, formatOutOf, formatR, formatScore } from "@/lib/numbers";
import { LedgerStrip, LedgerTile } from "./LedgerStrip";
import { Panel, PanelEmpty } from "./Panel";
import { StatusChip } from "./StatusChip";

/**
 * Both ledgers, the rolling calibration read, and R1 to R12.
 *
 * The calibration line is the sentence §0 of the journal currently pastes from
 * `calibration.py`, and it is the reason the weekly commit point exists at all:
 * twelve graded weeks and twelve standing rules were distilled from a fixed
 * one-to-two-week prediction horizon, and re-authoring scenarios daily would
 * destroy the Brier series.
 *
 * **A grade is not a direction.** Materialized is not "up". The three grades
 * take green, amber and the dated grey, but they are deliberately not the
 * market's up and down tokens: a scorecard painted in the colours a live book
 * wears would read as a set of positions rather than as a forecasting record.
 *
 * The ledger keeps every row it ever had, including the twelve weeks whose
 * scenario sets predate the import and whose `targets_hit` and `brier` columns
 * are therefore null. Those print the absent dash, never a zero: a Brier of
 * zero is a perfect week and an unscored week is not.
 */

const GRADE_TOKEN: Readonly<Record<Grade, string>> = {
  Materialized: "grade-materialized",
  Partial: "grade-partial",
  Not: "grade-not",
};

/**
 * How a grade is printed, which is not what it is called in the contract.
 *
 * `Not` is the stored value and stays the stored value: it is the third arm of
 * a CHECK constraint and the Brier arithmetic keys on it. On a badge beside
 * `Materialized` and `Partial` it reads as a truncation rather than a verdict,
 * so the badge says `Missed`. Display only; nothing writes this back.
 */
const GRADE_LABEL: Readonly<Record<Grade, string>> = {
  Materialized: "Materialized",
  Partial: "Partial",
  Not: "Missed",
};

/**
 * The two-word verdict a call was written with, and the note behind it.
 *
 * `eq_call` and `crypto_call` are written as a verdict, a hyphen and up to
 * forty words of reasoning. Rendered whole into a twelve-character column they
 * wrapped to nine lines and made every ledger row 140px tall, so the column
 * carries the verdict as a chip and the reasoning moves into the row's
 * disclosure, where it has the width to be read.
 *
 * The leading word is the verdict, and `valid but …` is its own verdict: a call
 * that was right and is turning is not the same claim as one that held.
 */
export function verdictOf(call: string): { label: string; token: string } {
  const head = (call.split(" - ")[0] ?? call).trim().toLowerCase();
  if (head.startsWith("valid but")) return { label: "TURNING", token: "grade-partial" };
  if (head.startsWith("valid")) return { label: "VALID", token: "grade-materialized" };
  if (head.startsWith("wrong")) return { label: "WRONG", token: "grade-not" };
  if (head.startsWith("advanced")) return { label: "ADVANCED", token: "k" };
  // An unrecognised verdict is shown as written rather than dropped: a word
  // this reader has not met is a new verdict, not a bad row.
  return { label: (head.split(/\s+/)[0] ?? call).toUpperCase(), token: "k" };
}

function Verdict({ call }: { call: string | null }) {
  if (call === null) return <>{ABSENT}</>;
  const verdict = verdictOf(call);
  return (
    <span className={`chip ${verdict.token}`} title={call}>
      {verdict.label}
    </span>
  );
}

/**
 * One graded week, one line, with the week's prose behind the disclosure.
 *
 * The table is the record; opening a row gives the argument. Nothing in the
 * closed row depends on how long a note is, so no row is taller than another
 * and nothing reflows when the ledger grows.
 */
function WeekGradeRow({ row }: { row: WeekGrade }) {
  const [open, setOpen] = useState(false);
  const notes: readonly [string, string][] = [
    ...(row.eqCall === null
      ? []
      : ([["Equities call", row.eqCall]] as [string, string][])),
    ...(row.cryptoCall === null
      ? []
      : ([["Crypto call", row.cryptoCall]] as [string, string][])),
    ...(row.keyMiss === null
      ? []
      : ([["What was missed", row.keyMiss]] as [string, string][])),
  ];

  return (
    <>
      <tr>
        <td className="num">{row.week}</td>
        <td>
          {row.baseScenarioLabel}
          <small>others: {otherGradesLine(row.otherGrades)}</small>
        </td>
        <td>
          <span className={`chip ${GRADE_TOKEN[row.baseGrade]}`}>
            {GRADE_LABEL[row.baseGrade]}
          </span>
          {row.premiseMoved ? (
            <span
              className="refused-mark"
              title="A catalyst this scenario's trigger named moved out of its horizon. The tag is recorded and the Brier score is not forgiven: R6 exists because over-anchoring on a dated catalyst is a forecasting error rather than bad luck."
            >
              PREMISE MOVED
            </span>
          ) : null}
        </td>
        <td data-label="Equities">
          <Verdict call={row.eqCall} />
        </td>
        <td data-label="Crypto">
          <Verdict call={row.cryptoCall} />
        </td>
        <td className="num" data-label="Targets">
          {formatOutOf(row.targetsHit, row.targetsN)}
        </td>
        <td className="num" data-label="Brier">
          {formatScore(row.brier)}
        </td>
        <td className="c-open">
          {notes.length === 0 ? null : (
            <button
              aria-expanded={open}
              className="row-open"
              onClick={() => setOpen((was) => !was)}
              title={
                open ? "Close" : "The two calls as written, and the key miss"
              }
              type="button"
            >
              <svg aria-hidden="true" viewBox="0 0 10 6">
                <path d="M1 1 L5 5 L9 1" />
              </svg>
            </button>
          )}
        </td>
      </tr>
      {open && notes.length > 0 ? (
        <tr className="row-detail">
          {/* One cell spanning the row, so a forty-word note is not forced
              into the twelve characters a verdict chip needs. */}
          <td colSpan={8}>
            <dl className="calibration-notes">
              {notes.map(([label, note]) => (
                <div key={label}>
                  <dt>{label}</dt>
                  <dd>{note}</dd>
                </div>
              ))}
            </dl>
          </td>
        </tr>
      ) : null}
    </>
  );
}

/**
 * The other two scenarios of a graded week, as one line.
 *
 * `other_grades` is a jsonb array of `{id, prob, grade, note}`, so the
 * probability each one carried is stated rather than implied. `S2 30% Not`
 * says both what was forecast and how it landed, which is the pair the Brier
 * score is computed from.
 */
function otherGradesLine(rows: readonly OtherGrade[]): string {
  if (rows.length === 0) return ABSENT;
  return rows
    .map((row) =>
      row.probability === null
        ? `${row.id} ${GRADE_LABEL[row.grade]}`
        : `${row.id} ${row.probability}% ${GRADE_LABEL[row.grade]}`,
    )
    .join(" · ");
}

export function CalibrationPanel() {
  const data = useCalibration();
  useRenderTick();

  const read = data.calibration;
  const hits = data.hitRate;
  // The relation name belongs in a tooltip, not in the sentence: the operator
  // reading a tile is being told a figure is missing, not being handed a query
  // to run.
  // The closing insight, moved into the heading it is about.
  const untriggered =
    data.setupGrades.length === 0
      ? null
      : data.setupGrades.filter((row) => row.outcome === "not triggered").length;
  const withheld =
    read === null ? "no calibration row yet" : "seeded, never graded";

  return (
    <Panel
      empty={
        <PanelEmpty
          reading={data.reading}
          relations={[
            "macro_mart.calibration",
            "macro_mart.week_grade",
            "macro_mart.setup_grade",
            "macro_mart.scenario_rule",
          ]}
          schedule="the Monday full run, which closes the week: it grades the prior set, appends both ledger rows, recomputes the Brier score and opens the new set."
        />
      }
      head={<StatusChip state={data.reading.state} />}
      reading={data.reading}
      title="Calibration"
    >
      {data.lead === null ? null : (
        <p className="calibration-lead">{data.lead}</p>
      )}
      <LedgerStrip label="The rolling calibration read">
        <LedgerTile
          label="Hit rate"
          sub={
            hits === null
              ? read === null || read.window === null
                ? null
                : `over the last ${read.window} graded weeks`
              : `full / partial / missed, of ${hits.window} graded weeks`
          }
          // Three figures, named once underneath. Written out in full the
          // headline wrapped to two lines at tile size and outweighed the
          // three single figures beside it.
          value={
            hits === null
              ? null
              : `${hits.full} / ${hits.partial} / ${hits.missed}`
          }
          withheld={withheld}
        />
        <LedgerTile
          label="Mean Brier"
          sub={
            read === null || read.brierWeeks === null
              ? null
              : `over ${read.brierWeeks} scored week${read.brierWeeks === 1 ? "" : "s"} · 0 perfect, 2 worst`
          }
          value={read === null ? null : formatScore(read.meanBrier)}
          withheld={withheld}
        />
        <LedgerTile
          label="Base case was S2"
          sub={
            read === null || read.window === null ? null : (
              <span title="A bias check, not a score. The base case is whichever scenario carried the highest probability; that it keeps being the middle one says the set is written around a central case rather than a directional one.">
                of {read.window}
              </span>
            )
          }
          value={
            read === null || read.baseWasS2 === null
              ? null
              : String(read.baseWasS2)
          }
          withheld={withheld}
        />
        <LedgerTile
          label="Graded weeks"
          sub="in the ledger, all time"
          value={
            read === null || read.gradedWeeks === null
              ? null
              : String(read.gradedWeeks)
          }
          withheld={withheld}
        />
      </LedgerStrip>

      <section className="snapshot-group">
        <div className="snapshot-group-head">
          <h3>Scorecard Ledger</h3>
          <span className="snapshot-group-count">
            {data.weekGrades.length} graded week
            {data.weekGrades.length === 1 ? "" : "s"}
          </span>
        </div>
        {data.weekGrades.length === 0 ? (
          <p className="muted" title="macro_mart.week_grade">
            No graded weeks yet. The calibration read is defined over the last
            eight graded weeks, so an empty ledger silences the probability
            check entirely rather than reporting a perfect record.
          </p>
        ) : (
          <div className="tbl tbl-bounded">
            <table className="ledger-table">
              <colgroup>
                <col className="c-week" />
                <col className="c-scenario" />
                <col className="c-grade" />
                <col className="c-call" />
                <col className="c-call" />
                <col className="c-targets" />
                <col className="c-brier" />
                <col className="c-open" />
              </colgroup>
              <thead>
                <tr>
                  <th className="num">Week</th>
                  <th>Base case · what was missed</th>
                  <th>Grade</th>
                  <th>Equities call</th>
                  <th>Crypto call</th>
                  <th className="num">Targets</th>
                  <th className="num">Brier</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {data.weekGrades.map((row) => (
                  <WeekGradeRow key={row.week} row={row} />
                ))}
              </tbody>
            </table>
            <p className="tbl-note">
              A dash in Targets or Brier is a week whose scenario set predates
              the import, not a week that scored zero. A Brier of 0.000 is a
              perfect forecast and an unscored week is not, so the two are never
              drawn alike.
            </p>
          </div>
        )}
      </section>

      <section className="snapshot-group">
        <div className="snapshot-group-head">
          <h3>
            Setup Ledger
            {untriggered === null ? null : (
              <span className="calibration-title-note">
                {" · "}
                {untriggered} of {data.setupGrades.length} never triggered
              </span>
            )}
          </h3>
          <span className="snapshot-group-count">
            {data.setupGrades.length} graded setup
            {data.setupGrades.length === 1 ? "" : "s"}
          </span>
        </div>
        {data.setupGrades.length === 0 ? (
          <p className="muted" title="macro_mart.setup_grade">
            No graded setups yet. Every setup proposed in a week is graded the
            following week, so the journal&apos;s trade ideas get a hit rate
            rather than disappearing.
          </p>
        ) : (
          <div className="tbl tbl-bounded">
            <table className="setup-ledger-table">
              <colgroup>
                <col className="c-week" />
                <col className="c-name" />
                <col className="c-outcome" />
                <col className="c-r" />
              </colgroup>
              <thead>
                <tr>
                  <th className="num">Proposed</th>
                  <th>Setup · outcome</th>
                  <th>Outcome</th>
                  <th className="num">R realised</th>
                </tr>
              </thead>
              <tbody>
                {data.setupGrades.map((row) => (
                  <tr key={row.setupId}>
                    <td className="num">{row.proposedWeek ?? row.gradedWeek}</td>
                    <td>
                      <span className="setup-asset">{row.asset ?? "—"}</span>{" "}
                      {row.name ?? row.setupId}
                      {row.note === null ? null : <small>{row.note}</small>}
                    </td>
                    <td>{row.outcome}</td>
                    <td className="num" data-label="R">
                      {row.rRealised === null ? ABSENT : formatR(row.rRealised)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="tbl-note">
              A gate that refuses a structurally correct entry is the recurring
              failure this terminal exists to surface on the day, and this
              column is the record of how often it has happened. The count is in
              the heading above, where the finding belongs rather than under the
              rows it is the finding about.
            </p>
          </div>
        )}
      </section>

      <section className="snapshot-group" id="rules">
        <div className="snapshot-group-head">
          <h3>Scenario rules</h3>
          <span className="snapshot-group-count">
            {data.rules.length} standing
          </span>
        </div>
        {data.rules.length === 0 ? (
          <p className="muted" title="macro_mart.scenario_rule">
            No scenario rules on record yet. Each rule exists because the same
            miss showed up in more than one graded week, and each one&apos;s
            teaching weeks point at those graded weeks, so the system can state
            how many times it has fired rather than imply it.
          </p>
        ) : (
          <ul className="rules">
            {data.rules.map((rule) => (
              <li
                className={`rule${rule.retiredWeek === null ? "" : " is-retired"}`}
                key={rule.number}
              >
                <span className="rule-number">R{rule.number}</span>
                <span className="rule-text">{rule.text}</span>
                <span className="rule-weeks">
                  <span className="rule-fires">
                    fired {rule.fireCount}×
                  </span>
                  <br />
                  {rule.teachingWeeks.length === 0
                    ? "no teaching week recorded"
                    : rule.teachingWeeks.join(" · ")}
                  {rule.retiredWeek === null
                    ? null
                    : ` · retired ${rule.retiredWeek}`}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </Panel>
  );
}
