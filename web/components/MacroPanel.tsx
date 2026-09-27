"use client";

import { useMacro } from "@/hooks/useMacro";
import { useRenderTick } from "@/hooks/useRenderTick";
import { countsSentence, currentSentence, isFault } from "@/lib/freshness";
import { belongsOnCatalysts } from "@/lib/desk";
import { formatProbability } from "@/lib/numbers";
import {
  DESK_ZONE,
  ageLabel,
  clockMinute,
  missedRuns,
  parseInstant,
  stampInZone,
} from "@/lib/time";
import { HealthStrip } from "./FindingsList";
import { LedgerStrip, LedgerTile } from "./LedgerStrip";
import { Panel, PanelEmpty } from "./Panel";
import { PhaseTile } from "./PhaseTile";
import { DeskView } from "./DeskView";
import { JudgmentView } from "./JudgmentView";
import { MoversStrip } from "./MoversStrip";
import { ClocksLine, ProvenanceFooter } from "./ProvenanceFooter";
import { SnapshotView } from "./SnapshotView";
import { ReadingBadge } from "./StatusChip";

/**
 * One reading, two pages.
 *
 * The Desk is the call held against today's tape: what has moved since it was
 * made, what is closest to changing, and the call itself below. Data is the
 * evidence those conditions read. They were two tabs of one panel; they are
 * two routes now, because the Desk is what a trader opens first and a tab
 * behind it was a click between the operator and the only question they came
 * with. Both read the same queries under the same keys, so walking between
 * them issues no request and cannot give one terminal two readings.
 *
 * The ledger strip sits above both, because the five figures on it are the
 * same five whichever page is open.
 *
 * The panel banner is suppressed here and the health strip owns the reason
 * instead. One fault, one owner: the banner printed the worst per-series
 * sentence in red above a strip that already counts the same fault.
 */
export const MACRO_PAGES = [
  { id: "desk", href: "/", name: "Desk" },
  { id: "data", href: "/data", name: "Data" },
] as const;

export type MacroPage = (typeof MACRO_PAGES)[number]["id"];

const SCHEDULE =
  "the worker, at 09:30 and 21:00 Asia/Singapore. 09:30 is the full run; 21:00 is a deterministic rescore with no LLM on the path.";

export function MacroPanel({ page }: { page: MacroPage }) {
  const data = useMacro();
  // Once a second, so the age beside the evaluation stamp stays true on a page
  // that will not receive a new row for another eleven hours.
  useRenderTick();
  // Sampled at render, never carried on the tick.
  const now = new Date();

  const equities = data.phaseScores.find((row) => row.asset === "equities");
  const crypto = data.phaseScores.find((row) => row.asset === "crypto");
  const base = data.scenarios.reduce<(typeof data.scenarios)[number] | null>(
    (best, s) => (best === null || s.probability > best.probability ? s : best),
    null,
  );
  const evaluatedAt = parseInstant(data.reading.evaluatedAt);
  const missed = missedRuns(evaluatedAt, now);
  const missedSentence =
    missed === 0 || evaluatedAt === null
      ? null
      : `The worker has missed ${missed} scheduled run${missed === 1 ? "" : "s"} since ${clockMinute(evaluatedAt, DESK_ZONE)} SGT (${ageLabel(evaluatedAt, now)}), so every rating below is as of then, not now`;
  const week =
    data.scenarioSet?.week ??
    data.phaseCalls[0]?.week ??
    data.setups[0]?.week ??
    null;

  return (
    <Panel
      empty={
        <PanelEmpty
          reading={data.reading}
          relations={[
            "macro_mart.snapshot_row",
            "macro_mart.condition_state",
            "macro_mart.phase_score",
            "macro_mart.scenario_state",
            "macro_mart.setup_state",
          ]}
          schedule={SCHEDULE}
        />
      }
      footer={
        <>
          <ClocksLine
            asofSpread={data.asofSpread}
            evaluatedAt={data.reading.evaluatedAt}
            now={now}
          />
          <ProvenanceFooter now={now} runs={data.runs} />
        </>
      }
      head={
        <>
          <ReadingBadge
            hasData={data.reading.hasData}
            missed={missed}
            stamp={
              evaluatedAt === null ? null : clockMinute(evaluatedAt, DESK_ZONE)
            }
            state={data.reading.state}
          />
          <span
            className="muted"
            title={`${countsSentence(data.reading.counts)}. ${data.reading.reason}`}
            style={{ fontFamily: "var(--mono)", fontSize: 10 }}
          >
            {missed > 0 ? "At last run: " : null}
            {currentSentence(data.reading.counts)}
          </span>
        </>
      }
      reading={{ ...data.reading, banner: null }}
      title={
        page === "desk"
          ? week === null
            ? "The desk"
            : `The desk · ${week}`
          : week === null
            ? "The data"
            : `The data · ${week}`
      }
      titleNote={
        evaluatedAt === null
          ? null
          : `${stampInZone(evaluatedAt, DESK_ZONE)} SGT · ${ageLabel(evaluatedAt, now)}`
      }
    >
      <LedgerStrip label="The week's reading">
        <PhaseTile
          call={data.phaseCalls.find((row) => row.asset === "equities")}
          score={equities}
        />
        <PhaseTile
          call={data.phaseCalls.find((row) => row.asset === "crypto")}
          score={crypto}
        />
        <LedgerTile
          label="Base case"
          sub={
            base === null
              ? null
              : `S${base.ordinal} · ${base.bandsHolding} of ${base.bandsTotal} bands holding`
          }
          value={base === null ? null : formatProbability(base.probability)}
          withheld="not yet computed"
          withheldNote="macro_mart.scenario_state has no row."
        />
        <LedgerTile
          label="Setups open"
          sub={`${data.setups.length} live this week`}
          value={
            data.setups.length === 0
              ? null
              : String(data.setups.filter((s) => s.gateState === "open").length)
          }
          withheld="not yet computed"
          withheldNote="macro_mart.setup_state has no row."
        />
        <LedgerTile
          label="Last evaluated"
          sub={
            evaluatedAt === null ? null : (
              <span title="The moment the worker last recomputed macro_mart. Every figure above is as-of this instant; the observation dates behind them are older by design.">
                {ageLabel(evaluatedAt, now)}
              </span>
            )
          }
          value={
            evaluatedAt === null
              ? null
              : `${clockMinute(evaluatedAt, DESK_ZONE)} SGT`
          }
          withheld="not yet computed"
          withheldNote="No evaluated_at on any macro_mart row."
        />
      </LedgerStrip>

      <HealthStrip
        counts={data.reading.counts}
        findings={data.findings}
        lead={
          missedSentence ??
          (data.reading.banner === null ||
          data.reading.state === "pending" ||
          isFault(data.reading.state)
            ? null
            : data.reading.banner.text)
        }
        now={now}
        rows={data.snapshot}
      />

      {page === "desk" ? (
        <>
          <DeskView data={data} now={now} />
          <JudgmentView data={data} />
        </>
      ) : (
        <>
          <MoversStrip rows={data.snapshot} trends={data.trends} />
          <SnapshotView
            gaps={data.gaps}
            rows={data.snapshot.filter((row) => !belongsOnCatalysts(row))}
            trends={data.trends}
          />
        </>
      )}
    </Panel>
  );
}
