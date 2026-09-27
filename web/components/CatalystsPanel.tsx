"use client";

import { useEffect, useState } from "react";

import { useMacro } from "@/hooks/useMacro";
import { useRenderTick } from "@/hooks/useRenderTick";
import {
  CATALYST_GROUPS,
  type CatalystGroup,
  groupCounts,
  groupLabel,
  groupOf,
  isPast,
  monthLabel,
  sortKey,
  withinWindow,
} from "@/lib/catalysts";
import type {
  CatalystWindow,
  RunRequest,
  TailRisk,
  TailRiskCheck,
} from "@/lib/contract";
import { belongsOnCatalysts, lastOf } from "@/lib/desk";
import { safeHref } from "@/lib/links";
import {
  direction,
  feedStatuses,
  freshnessRows,
  originLabel,
  severityTrail,
  type FeedStatus,
} from "@/lib/feeds";
import {
  DESK_ZONE,
  ageLabel,
  clockMinute,
  dayLabel,
  parseInstant,
  shortDay,
  weekdayDay,
  whenWord,
} from "@/lib/time";
import { HoldingsWatch } from "./HoldingsWatch";
import { Panel, PanelEmpty } from "./Panel";
import { SnapshotView } from "./SnapshotView";
import { SourceLink } from "./SourceLink";

const SCHEDULE =
  "OpenClaw's morning calendar (08:40 SGT) and holdings watch (09:05 SGT), its Monday 06:00 SGT tail-risk scan with a daily recheck at 09:20, the 09:30 worker run while a feed is still read from its Obsidian note, and the 09:40 narrative run for the policy state.";

/** Where the calendar's group filter is remembered, per browser. */
const GROUP_KEY = "macro.catalysts.group";

/**
 * What is coming, what could break, and where policy stands.
 *
 * The calendar is `Catalyst Monitor.md` as the worker parsed it this morning;
 * tail risks and watchlist changes are the other two cron files. The policy
 * state and the auction results used to sit at the bottom of the snapshot,
 * forty rows below the prices, which is where nobody reads a Fed decision.
 * They are dated facts read against a calendar, so they live beside it.
 *
 * A catalyst a scenario clause is bound to carries that scenario's number, so
 * the page says which dated event decides which part of the call.
 */
export function CatalystsPanel() {
  const data = useMacro();
  useRenderTick();
  const now = new Date();
  const narrative = lastOf(data.runRequests, "narrative");
  const policy = data.snapshot.filter(
    (row) => belongsOnCatalysts(row) && !row.key.startsWith("auction_"),
  );
  const auctions = data.snapshot.filter((row) =>
    row.key.startsWith("auction_"),
  );
  const scenarioOf = clauseScenarios(data);
  const catalysts = data.catalysts.filter((row) => withinWindow(row, now));
  // The list keeps yesterday's rows, dimmed; the count is only what is left.
  const upcoming = catalysts.filter((row) => !isPast(row, now)).length;
  const feeds = feedStatuses(
    freshnessRows(data.feeds, data.runs),
    now,
    data.tailRisks.some((risk) => risk.severity !== "watch"),
  );

  return (
    <Panel
      empty={
        <PanelEmpty
          reading={data.reading}
          relations={[
            "macro_mart.catalyst_window",
            "macro_mart.tail_risk_state",
            "macro_mart.holding_watch_state",
            "macro_mart.feed_freshness",
          ]}
          schedule={SCHEDULE}
        />
      }
      head={
        <span
          className="muted"
          style={{ fontFamily: "var(--mono)", fontSize: 10 }}
        >
          {narrativeNote(narrative, now)}
        </span>
      }
      reading={{ ...data.reading, banner: null }}
      title="Catalysts"
      titleNote={`${upcoming} still to come in the next thirty days`}
    >
      <FeedLine feeds={feeds} />
      <p className="muted cal-provenance">
        Found by an AI web search (OpenClaw): the calendar and your holdings
        each morning, tail risks each Monday. Each row links to its source, so
        you can check it.
      </p>
      <div className="catalysts-grid">
        <section className="snapshot-group">
          <div className="snapshot-group-head">
            <h3>Calendar</h3>
            <span className="snapshot-group-count">next thirty days, SGT</span>
          </div>
          <Calendar catalysts={catalysts} now={now} scenarioOf={scenarioOf} />
        </section>
        <div>
          <section className="snapshot-group">
            <div className="snapshot-group-head">
              <h3>Tail risks</h3>
              <span className="snapshot-group-count">
                {data.tailRisks.length} listed
              </span>
            </div>
            <TailRisks
              history={data.tailRiskHistory}
              now={now}
              risks={data.tailRisks}
            />
          </section>
        </div>
      </div>
      {/* Full width below the calendar: a dozen holdings in the side column
          ran far past the calendar and left the left column empty. */}
      <HoldingsWatch
        events={data.holdingEvents}
        holdings={data.holdings}
        now={now}
        watchlist={data.watchlist}
      />
      <SnapshotView gaps={data.gaps} rows={policy} title="Policy state" />
      <SnapshotView
        gaps={data.gaps}
        rows={auctions}
        title="Treasury auctions"
        trends={data.trends}
      />
    </Panel>
  );
}

/**
 * One line, one entry per feed: where it last came from and when. A feed that
 * is late for its cadence, failed, or never ran says so in words ("late",
 * "failed") and is drawn in the stale colour. The detail is on hover for a
 * mouse, and read out after the entry for a screen reader, since a `title`
 * reaches neither a keyboard nor most readers.
 */
function FeedLine({ feeds }: { feeds: readonly FeedStatus[] }) {
  return (
    <p className="feed-line">
      <span className="visually-hidden">Where each feed last came from: </span>
      {feeds.map((feed) => (
        <span
          className={feed.stale ? "feed-item feed-stale" : "feed-item"}
          key={feed.feed}
          title={feed.title}
        >
          <b>{feed.label}:</b> {feed.text}
          {feed.stale ? (
            <span className="visually-hidden">. {feed.title}</span>
          ) : null}
        </span>
      ))}
    </p>
  );
}

/** When the policy state was last re-read by the narrative run, in one clause. */
function narrativeNote(run: RunRequest | null, now: Date): string {
  if (run === null) return "no narrative run on record";
  const at = parseInstant(run.finishedAt ?? run.claimedAt ?? run.requestedAt);
  const when = at === null ? "" : ` ${ageLabel(at, now)}`;
  if (run.status === "done") return `policy state re-read${when}`;
  if (run.status === "running") return `narrative running, started${when}`;
  if (run.status === "queued") return "narrative queued";
  return `last narrative ${run.status}${when}`;
}

/** Clause id to the ordinal of the scenario that owns it. */
function clauseScenarios(
  data: ReturnType<typeof useMacro>,
): Map<string, number> {
  const ordinal = new Map(data.scenarios.map((s) => [s.scenarioId, s.ordinal]));
  const out = new Map<string, number>();
  for (const condition of data.conditions) {
    if (condition.ownerKind !== "trigger") continue;
    const n = ordinal.get(condition.ownerId);
    if (n !== undefined) out.set(condition.conditionId, n);
  }
  return out;
}

/**
 * The group filter, remembered per browser. Read after mount, not during
 * render, so the server and the first client render agree; storage that
 * throws (a private window, blocked site data) leaves the filter on All.
 */
function useGroupFilter(): [
  CatalystGroup | "all",
  (next: CatalystGroup | "all") => void,
] {
  const [group, setGroup] = useState<CatalystGroup | "all">("all");
  useEffect(() => {
    try {
      const saved = window.localStorage.getItem(GROUP_KEY);
      if (saved !== null && CATALYST_GROUPS.some((g) => g.id === saved)) {
        setGroup(saved as CatalystGroup);
      }
    } catch {
      // No storage: the filter starts on All, which is also the default.
    }
  }, []);
  const choose = (next: CatalystGroup | "all") => {
    setGroup(next);
    try {
      if (next === "all") window.localStorage.removeItem(GROUP_KEY);
      else window.localStorage.setItem(GROUP_KEY, next);
    } catch {
      // Remembering is a convenience; the choice still applies to this visit.
    }
  };
  return [group, choose];
}

function Calendar({
  catalysts,
  scenarioOf,
  now,
}: {
  catalysts: readonly CatalystWindow[];
  scenarioOf: Map<string, number>;
  now: Date;
}) {
  const [group, setGroup] = useGroupFilter();
  if (catalysts.length === 0) {
    return (
      <p className="muted">
        No dated catalyst in the window. The calendar fills on the 09:30 run
        from Catalyst Monitor.md.
      </p>
    );
  }
  const counts = groupCounts(catalysts);
  // A remembered group with nothing in it this month falls back to All,
  // rather than showing an empty calendar with no chip to explain it.
  const active = group !== "all" && counts.has(group) ? group : "all";
  const shown = catalysts.filter(
    (one) => active === "all" || groupOf(one.category) === active,
  );
  const sorted = [...shown].sort((a, b) =>
    sortKey(a).localeCompare(sortKey(b)),
  );
  let lastDay = "";
  return (
    <>
      <div className="cal-filter" role="group" aria-label="Show catalysts by kind">
        <FilterChip
          count={catalysts.length}
          label="All"
          onPick={() => setGroup("all")}
          pressed={active === "all"}
        />
        {CATALYST_GROUPS.filter((g) => counts.has(g.id)).map((g) => (
          <FilterChip
            count={counts.get(g.id) ?? 0}
            group={g.id}
            key={g.id}
            label={g.label}
            onPick={() => setGroup(g.id)}
            pressed={active === g.id}
          />
        ))}
      </div>
      <ul className="cal-list">
        {sorted.map((one) => {
          const at = parseInstant(one.startsAt);
          const month = one.datePrecision === "month" ? monthLabel(one) : null;
          const day =
            month ??
            (at === null
              ? "date not set"
              : weekdayDay(at, DESK_ZONE));
          const showDay = day !== lastDay;
          lastDay = day;
          const scenarios = [
            ...new Set(
              one.referencedBy
                .map((id) => scenarioOf.get(id))
                .filter((n): n is number => n !== undefined),
            ),
          ].sort((a, b) => a - b);
          const kind = groupOf(one.category);
          return (
            <li
              className={isPast(one, now) ? "cal-row cal-past" : "cal-row"}
              key={one.catalystId}
            >
              <span className="cal-day">{showDay ? day : ""}</span>
              <span
                className={one.dateEstimated ? "cal-time cal-est" : "cal-time"}
                title={one.dateText ?? undefined}
              >
                {timeWord(one, at)}
              </span>
              <span className="cal-name">
                {safeHref(one.sourceUrl) === null ? (
                  one.name
                ) : (
                  <a
                    href={safeHref(one.sourceUrl)!}
                    rel="noreferrer"
                    target="_blank"
                  >
                    {one.name}
                  </a>
                )}
                <small className="cal-meta">
                  <span className={`cal-kind ${kindClass(kind)}`}>
                    {groupLabel(kind)}
                  </span>
                  {one.affected.length === 0 ? null : (
                    <span className="muted">
                      {one.affected.slice(0, 5).join(" · ")}
                    </span>
                  )}
                </small>
              </span>
              <span>
                {scenarios.length === 0 ? null : (
                  <span
                    className="chip state-unrated"
                    title="A trigger clause in these scenarios is bound to this event."
                  >
                    {scenarios.map((n) => `S${n}`).join(" · ")}
                  </span>
                )}
              </span>
            </li>
          );
        })}
      </ul>
    </>
  );
}

function FilterChip({
  label,
  count,
  group,
  pressed,
  onPick,
}: {
  label: string;
  count: number;
  group?: CatalystGroup;
  pressed: boolean;
  onPick: () => void;
}) {
  return (
    <button
      aria-pressed={pressed}
      className="cal-chip"
      onClick={onPick}
      type="button"
    >
      {group === undefined ? null : (
        <i aria-hidden="true" className={`cal-dot ${kindClass(group)}`} />
      )}
      {label}
      <b>{count}</b>
    </button>
  );
}

/** The hue class for a group: `market_events` is `cal-kind-market-events`. */
function kindClass(group: CatalystGroup): string {
  return `cal-kind-${group.replaceAll("_", "-")}`;
}

/**
 * What the time column says. A timed event gives its Singapore time; a firm
 * day says nothing, because the day column already holds all it knows; a
 * window says when it ends; an estimate says so, in words, whatever its
 * precision. The monitor's own wording is on hover.
 */
function timeWord(one: CatalystWindow, at: Date | null): string {
  if (one.dateEstimated) return "est.";
  if (at !== null && one.datePrecision === "exact") {
    return clockMinute(at, DESK_ZONE);
  }
  if (one.datePrecision === "window") {
    const end = parseInstant(one.endsAt);
    if (end === null) return "window";
    return `to ${shortDay(end, DESK_ZONE)}`;
  }
  return "";
}

const SEVERITY_CLASS: Record<TailRisk["severity"], string> = {
  acute: "state-failed",
  elevated: "state-stale",
  watch: "state-dated",
};

function TailRisks({
  risks,
  history,
  now,
}: {
  risks: readonly TailRisk[];
  history: readonly TailRiskCheck[];
  now: Date;
}) {
  if (risks.length === 0) {
    return (
      <p className="muted">
        No tail risk listed. OpenClaw scans for them each Monday.
      </p>
    );
  }
  const order = { acute: 0, elevated: 1, watch: 2 } as const;
  return (
    <ul className="risk-list">
      {[...risks]
        .sort((a, b) => order[a.severity] - order[b.severity])
        .map((risk) => (
          <li key={risk.riskId ?? risk.key}>
            <div className="risk-head">
              <span className={`chip ${SEVERITY_CLASS[risk.severity]}`}>
                {risk.severity.toUpperCase()}
              </span>
              <b>{risk.name}</b>
            </div>
            <Trail history={history} risk={risk} />
            {risk.summary === null ? null : <p>{risk.summary}</p>}
            {risk.pricedIn === null ? null : (
              <p className="muted">
                <i>Priced in:</i> {risk.pricedIn}
              </p>
            )}
            <small className="faint risk-foot">
              <span>{riskFoot(risk, now)}</span>
              <SourceLink url={risk.sourceUrl} />
            </small>
          </li>
        ))}
    </ul>
  );
}

/**
 * How a risk got to where it is: `watch 9 Sept → elevated 14 Sept → acute
 * 21 Sept`, from its check history. With no history but a recorded previous
 * severity, it says which way it last moved. A risk that has only ever been at
 * one level draws nothing.
 */
function Trail({
  risk,
  history,
}: {
  risk: TailRisk;
  history: readonly TailRiskCheck[];
}) {
  const steps =
    risk.riskId === null ? [] : severityTrail(history, risk.riskId);
  if (steps.length >= 2) {
    return (
      <p className="risk-trail">
        <span className="visually-hidden">How the level has changed: </span>
        {steps.map((step, index) => (
          <span key={step.checkedAt}>
            {index === 0 ? null : (
              <span aria-hidden="true" className="risk-trail-arrow">
                {" → "}
              </span>
            )}
            <span className={`risk-sev-${step.severity}`}>{step.severity}</span>{" "}
            {step.day}
          </span>
        ))}
      </p>
    );
  }
  if (risk.previousSeverity !== null) {
    const way = direction(risk.previousSeverity, risk.severity);
    if (way === "same") return null;
    return (
      <p className="risk-trail">
        {way === "up" ? "Up" : "Down"} from{" "}
        <span className={`risk-sev-${risk.previousSeverity}`}>
          {risk.previousSeverity}
        </span>
      </p>
    );
  }
  return null;
}

/**
 * The risk's dates, in the words its feed supports. The note only ever had a
 * first and a last listing; a desk check has a time and a kind.
 */
function riskFoot(risk: TailRisk, now: Date): string {
  const checked = parseInstant(risk.lastCheckedAt);
  const since = `first seen ${dayLabel(risk.firstSeen)}`;
  if (risk.feedSource !== null && risk.feedSource !== "vault" && checked !== null) {
    const daily = risk.lastCheckKind === "daily" ? " (daily check)" : "";
    return `${since} · checked ${whenWord(checked, now)} by ${originLabel(risk.feedSource)}${daily}`;
  }
  return `in the weekly note since ${dayLabel(risk.firstSeen)} · last listed ${dayLabel(risk.lastSeen)}`;
}
