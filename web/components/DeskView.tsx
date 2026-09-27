"use client";

import Link from "next/link";
import { useState } from "react";

import type { MacroData } from "@/hooks/useMacro";
import { useRunQueue } from "@/hooks/useRunQueue";
import type { CatalystWindow, Drift, RunRequest } from "@/lib/contract";
import {
  awaitingDecision,
  lastOf,
  nearestEdges,
  nextSettlers,
  refreshInFlight,
  type Edge,
  type Settler,
} from "@/lib/desk";
import { formatLevel } from "@/lib/numbers";
import {
  DESK_ZONE,
  ageLabel,
  clockMinute,
  dayLabel,
  parseInstant,
  shortDay,
} from "@/lib/time";

/**
 * The top of the Desk: what has moved against the call, what is closest to
 * moving, what settles next, and the one control that asks for a new read.
 *
 * Everything below it on the page is the call as authored. Everything here is
 * the call held up against today's tape, which is the join a reader used to
 * make in their head between two tabs.
 */
export function DeskView({ data, now }: { data: MacroData; now: Date }) {
  const edges = nearestEdges(data.targets, data.scenarios, data.snapshot);
  const upcoming = [...data.catalysts]
    .filter((c) => c.daysOut !== null && c.daysOut >= 0 && c.daysOut <= 7)
    .sort((a, b) => (a.startsAt ?? "").localeCompare(b.startsAt ?? ""))
    .slice(0, 5);
  const settlers = nextSettlers(
    data.catalysts,
    data.conditions,
    data.scenarios,
  );
  const pending = awaitingDecision(data.runRequests);
  return (
    <section className="desk" aria-label="Since the call">
      <DriftBar drift={data.drift} now={now} runs={data.runRequests} />
      {pending === null ? null : <ProposalReview now={now} run={pending} />}
      <div className="desk-grid">
        <EdgeList edges={edges} />
        <SettlerList now={now} settlers={settlers} upcoming={upcoming} />
      </div>
    </section>
  );
}

/* ------------------------------------------------------------- drift bar */

function DriftBar({
  drift,
  runs,
  now,
}: {
  drift: Drift | null;
  runs: readonly RunRequest[];
  now: Date;
}) {
  const [open, setOpen] = useState(false);
  const tone =
    drift === null
      ? ""
      : drift.refreshSuggested
        ? " desk-drift-warn"
        : " desk-drift-calm";
  return (
    <div className={`desk-drift${tone}`}>
      <div className="desk-drift-head">
        <div className="desk-drift-score" aria-label="Drift score">
          <span className="desk-k">Drift</span>
          <strong>{drift === null ? "–" : `${drift.score}/10`}</strong>
        </div>
        <div className="desk-drift-text">
          {drift === null ? (
            <p className="muted">
              Not yet scored. The worker writes drift on its next run once a
              scenario set is active.
            </p>
          ) : (
            <>
              <p>
                <b>
                  Call {drift.week}, measured from{" "}
                  {drift.anchorAsof === null
                    ? "its authoring"
                    : dayLabel(drift.anchorAsof)}{" "}
                  to {drift.asof === null ? "today" : dayLabel(drift.asof)}.
                </b>{" "}
                {drift.reason}
              </p>
              {drift.items.length === 0 ? null : (
                <button
                  aria-expanded={open}
                  className="button-plain"
                  onClick={() => setOpen(!open)}
                  type="button"
                >
                  {open ? "Hide" : "Show"} what changed ({drift.items.length})
                </button>
              )}
            </>
          )}
        </div>
        <RefreshControl drift={drift} now={now} runs={runs} />
      </div>
      {open && drift !== null ? (
        <ul className="desk-drift-items">
          {drift.items.map((item) => (
            <li key={`${item.kind}-${item.subject}`}>
              <span
                className={`chip ${item.material ? "state-failed" : "state-dated"}`}
              >
                {item.material ? "MATERIAL" : `+${item.weight}`}
              </span>
              <span>{item.sentence}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

/* ------------------------------------------------------ refresh control */

function RefreshControl({
  drift,
  runs,
  now,
}: {
  drift: Drift | null;
  runs: readonly RunRequest[];
  now: Date;
}) {
  const { refresh } = useRunQueue();
  const [asking, setAsking] = useState(false);
  const [reason, setReason] = useState("");
  const inFlight = refreshInFlight(runs);
  const last = lastOf(runs, "amend");
  const refused =
    refresh.data !== undefined && !refresh.data.ok ? refresh.data.reason : null;

  if (inFlight !== null) {
    return (
      <div className="desk-refresh">
        <span className="chip state-stale">{flightLabel(inFlight)}</span>
        <small className="muted">{flightNote(inFlight, now)}</small>
      </div>
    );
  }

  if (!asking) {
    return (
      <div className="desk-refresh">
        <button
          className={drift?.refreshSuggested ? "button-accent" : "button-plain"}
          onClick={() => setAsking(true)}
          type="button"
        >
          Refresh the call
        </button>
        {last === null || last.status === "proposed" ? null : (
          <small
            className="muted"
            title={last.error ?? last.summary ?? undefined}
          >
            Last refresh {lastWord(last)}
          </small>
        )}
      </div>
    );
  }

  return (
    <form
      className="desk-refresh-form"
      onSubmit={(event) => {
        event.preventDefault();
        refresh.mutate(reason, {
          onSuccess: (result) => {
            if (result.ok) {
              setAsking(false);
              setReason("");
            }
          },
        });
      }}
    >
      <label htmlFor="refresh-reason">What changed?</label>
      <textarea
        id="refresh-reason"
        onChange={(event) => setReason(event.target.value)}
        placeholder={
          drift !== null && drift.items[0] !== undefined
            ? drift.items[0].sentence
            : "e.g. oil broke $95 and HY is widening"
        }
        rows={2}
        value={reason}
      />
      <div className="desk-refresh-actions">
        <button
          className="button-accent"
          disabled={refresh.isPending || reason.trim().length < 3}
          type="submit"
        >
          {refresh.isPending ? "Queueing" : "Queue the refresh"}
        </button>
        <button
          className="button-plain"
          onClick={() => setAsking(false)}
          type="button"
        >
          Cancel
        </button>
      </div>
      <small className="muted">
        A Claude run on the desk Mac re-reads the call against the tape and
        proposes an amendment, or says why none is due. Probabilities move only
        on a material change. Nothing goes live until you accept it here.
      </small>
      {refused === null ? null : (
        <small className="desk-refused">{refused}</small>
      )}
    </form>
  );
}

function flightLabel(run: RunRequest): string {
  if (run.status === "queued") return "REFRESH QUEUED";
  if (run.status === "running") return "REFRESHING";
  return run.decision === "accept" ? "ACCEPTING" : "REJECTING";
}

function flightNote(run: RunRequest, now: Date): string {
  if (run.status === "queued")
    return "The runner picks it up within two minutes.";
  if (run.status === "running") {
    const since = parseInstant(run.claimedAt);
    return `Running ${since === null ? "" : ageLabel(since, now).replace(" ago", "")}; usually 10 to 30 minutes.`;
  }
  return "The runner applies your decision within two minutes, then rescores.";
}

function lastWord(run: RunRequest): string {
  if (run.status === "failed") return "failed";
  if (run.status === "rejected") return "was rejected";
  if (run.status === "accepted") return "was accepted";
  return run.summary === null ? "found no change" : `: ${run.summary}`;
}

/* ------------------------------------------------------ proposal review */

function ProposalReview({ run, now }: { run: RunRequest; now: Date }) {
  const { decide } = useRunQueue();
  const refused =
    decide.data !== undefined && !decide.data.ok ? decide.data.reason : null;
  const finished = parseInstant(run.finishedAt);
  return (
    <div
      className="desk-proposal"
      role="region"
      aria-label="Proposal awaiting your decision"
    >
      <div className="desk-proposal-head">
        <span className="chip state-unrated">AWAITING YOU</span>
        <strong>
          {run.kind === "author"
            ? "The week's new call"
            : "An amendment to the call"}
        </strong>
        <small className="muted">
          {finished === null
            ? ""
            : `proposed ${clockMinute(finished, DESK_ZONE)} SGT · ${ageLabel(finished, now)}`}
        </small>
      </div>
      {run.reason === null ? null : (
        <p className="muted">
          You asked: <i>{run.reason}</i>
        </p>
      )}
      <p>{run.summary ?? "The run wrote a proposal and no summary."}</p>
      <ul className="desk-proposal-rows">
        {run.proposed.map((row) => (
          <li key={row.id}>
            <code>{row.table.replace("_", " ")}</code>{" "}
            {[row.asset, row.name, row.week].filter(Boolean).join(" · ")}
          </li>
        ))}
      </ul>
      <div className="desk-refresh-actions">
        <button
          className="button-accent"
          disabled={decide.isPending}
          onClick={() => decide.mutate({ id: run.id, decision: "accept" })}
          type="button"
        >
          Accept
        </button>
        <button
          className="button-plain"
          disabled={decide.isPending}
          onClick={() => decide.mutate({ id: run.id, decision: "reject" })}
          type="button"
        >
          Reject
        </button>
        <small className="muted">
          Accepting supersedes the rows it replaces and the page rescores within
          a few minutes; rejecting leaves the call exactly as it stands.
        </small>
      </div>
      {refused === null ? null : (
        <small className="desk-refused">{refused}</small>
      )}
    </div>
  );
}

/* --------------------------------------------------------- edge list */

function EdgeList({ edges }: { edges: readonly Edge[] }) {
  return (
    <div className="desk-card">
      <div className="desk-k">Closest to changing</div>
      {edges.length === 0 ? (
        <p className="muted">No scored band has a reading yet.</p>
      ) : (
        <ul className="desk-list">
          {edges.map((edge) => (
            <li key={edge.targetId}>
              <div>
                <b>
                  S{edge.scenarioOrdinal} {edge.label}
                </b>
                <span className="desk-why">
                  {formatLevel(edge.current, edge.edge)} is{" "}
                  {edge.pctAway.toFixed(1)}% from the {edge.side} at{" "}
                  {formatLevel(edge.edge, edge.current)};{" "}
                  {edge.holding
                    ? "holding, would leave"
                    : "outside, would enter"}
                </span>
              </div>
              {edge.seriesKey === null ? null : (
                <Link
                  className="desk-link"
                  href={`/data#series-${edge.seriesKey}`}
                >
                  Data →
                </Link>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/* ------------------------------------------------------- settler list */

function SettlerList({
  settlers,
  upcoming,
  now,
}: {
  settlers: readonly Settler[];
  /** The next dated catalysts, for a call whose clauses name none. */
  upcoming: readonly CatalystWindow[];
  now: Date;
}) {
  // A call whose clauses are bound to no catalyst (every migrated week is)
  // still has a calendar in front of it. Showing the next dated events, and
  // saying they are not bound, beats a card that is empty but for a link.
  const bound = settlers.length > 0;
  const rows = bound
    ? settlers.map((one) => ({
        id: one.catalystId,
        name: one.name,
        startsAt: one.startsAt,
        exact: one.exact,
        scenarios: one.scenarios,
      }))
    : upcoming.map((one) => ({
        id: one.catalystId,
        name: one.name,
        startsAt: one.startsAt,
        exact: one.datePrecision === "exact",
        scenarios: [] as number[],
      }));
  return (
    <div className="desk-card">
      <div className="desk-k">
        {bound ? "Next events that settle a clause" : "Next dated events"}
      </div>
      {rows.length === 0 ? (
        <p className="muted">Nothing dated in the next seven days.</p>
      ) : (
        <ul className="desk-list">
          {rows.map((one) => {
            const at = parseInstant(one.startsAt);
            return (
              <li key={one.id}>
                <div>
                  <span className="desk-when">
                    {at === null ? "date tbc" : whenLabel(at, now, one.exact)}
                  </span>
                  <b>{one.name}</b>
                </div>
                {one.scenarios.length === 0 ? null : (
                  <span className="chip state-unrated">
                    {one.scenarios.map((n) => `S${n}`).join(" · ")}
                  </span>
                )}
              </li>
            );
          })}
        </ul>
      )}
      <p className="desk-card-foot">
        {bound
          ? null
          : "No clause in this call is bound to a dated catalyst, so none of these settles one by itself. "}
        <Link className="desk-link" href="/catalysts">
          Full calendar →
        </Link>
      </p>
    </div>
  );
}

function whenLabel(at: Date, now: Date, exact: boolean): string {
  const day = shortDay(at, DESK_ZONE);
  if (!exact) {
    // Dated to a day with no time: it is stored at midnight, so an hour
    // count would call a Wednesday event "passed" at 00:01 on Wednesday.
    const today = shortDay(now, DESK_ZONE);
    return day === today ? `${day} · today` : day;
  }
  const hours = (at.getTime() - now.getTime()) / 3_600_000;
  if (hours < 0) return `${day} ${clockMinute(at, DESK_ZONE)} · passed`;
  if (hours < 24) return `${day} ${clockMinute(at, DESK_ZONE)} · ${Math.round(hours)}h`;
  return `${day} ${clockMinute(at, DESK_ZONE)}`;
}
