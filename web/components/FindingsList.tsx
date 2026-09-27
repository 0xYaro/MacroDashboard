"use client";

import { useState } from "react";

import type { Finding, SnapshotRow } from "@/lib/contract";
import {
  checkChipLabel,
  countsSentence,
  dedupeSentence,
  healthLine,
  type FreshnessCounts,
} from "@/lib/freshness";
import { ageLabel, parseInstant } from "@/lib/time";

/**
 * Open findings from `macro_quality.finding`, rendered verbatim.
 *
 * Each sentence is the worker's, unedited and never clipped. Severity is the
 * worker's too, and it only ever picks a token here. Every check
 * `lint_journal.py` used to perform becomes one of these rows, so this list is
 * how a structural problem in the week's judgment reaches the operator on the
 * day it happens rather than the following Monday.
 *
 * A finding the panel banner is already carrying is dropped. One fault, one
 * owner: the same silence printed as a banner and again as a finding reads as
 * two problems, and the reader has to work out that it is one.
 */

/** Severity to token. 3 is a fault; 2 a caution; anything lower is a note. */
function tokenFor(severity: number): string {
  if (severity >= 3) return "state-failed";
  if (severity === 2) return "state-stale";
  return "state-dated";
}

export function FindingsList({
  findings,
  bannerText,
  now,
}: {
  findings: readonly Finding[];
  bannerText: string | null;
  now: Date;
}) {
  const open = findings
    .filter((row) => row.closedAt === null)
    .filter(
      (row) => bannerText === null || !bannerText.includes(row.sentence),
    )
    .sort((a, b) => b.severity - a.severity || a.id - b.id);

  if (open.length === 0) return null;

  return (
    <ul className="findings" aria-label="Open findings">
      {open.map((row) => (
        <li key={row.id}>
          <span className={`chip ${tokenFor(row.severity)}`} title={row.checkKey}>
            {checkChipLabel(row.checkKey, row.subject)}
          </span>
          <span className="findings-subject">{row.subject}</span>
          <span className="findings-sentence">
            {dedupeSentence(row.sentence)}
          </span>
          <span className="findings-age">
            open {ageLabel(parseInstant(row.openedAt), now)}
          </span>
        </li>
      ))}
    </ul>
  );
}

/**
 * The reading's health, as one line that expands.
 *
 * It replaces a red banner and a stack of six reason chips. The banner carried
 * one sentence about one vendor and painted the whole panel as failed; the
 * chips carried the same sentence fourteen times, because the worker had
 * written it once per affected series, and the same setup defect four times.
 * Three problems, six shapes, and the reader had to count to find that out.
 *
 * One clause per problem, each with its count in front of it, and the producer's
 * own sentences one click below. Amber when anything is not current, green when
 * everything is. The dot is the only colour: a strip that is red whenever a
 * vendor is quiet teaches the operator to stop reading it.
 */
export function HealthStrip({
  counts,
  findings,
  rows,
  lead,
  now,
}: {
  counts: FreshnessCounts;
  findings: readonly Finding[];
  rows: readonly SnapshotRow[];
  /** An envelope fault or a rejected-row count, which is not about any series. */
  lead: string | null;
  now: Date;
}) {
  const [open, setOpen] = useState(false);
  const line = healthLine({ counts, findings, lead, rows });
  return (
    <div className="health" aria-label="Reading health">
      <button
        aria-expanded={open}
        className="health-line"
        onClick={() => setOpen(!open)}
        type="button"
      >
        <span
          aria-hidden="true"
          className={`health-dot ${line.stale ? "state-stale" : "state-fresh"}`}
        />
        <span className="health-text">{line.clauses.join(" · ")}</span>
        <span className="health-more">details {open ? "▴" : "▾"}</span>
      </button>
      {open ? (
        <div className="health-detail">
          <p className="health-breakdown">{countsSentence(counts)}</p>
          <FindingsList bannerText={null} findings={findings} now={now} />
        </div>
      ) : null}
    </div>
  );
}
