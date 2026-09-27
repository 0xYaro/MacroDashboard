import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { BandRow } from "@/components/BandExtent";
import { CalibrationPanel, verdictOf } from "@/components/CalibrationPanel";
import { Providers } from "@/components/Providers";
import { SeriesChart } from "@/components/SeriesChart";
import { ConditionRow } from "@/components/ConditionRow";
import { Panel, PanelBoundary, PanelEmpty } from "@/components/Panel";
import { PhaseTile } from "@/components/PhaseTile";
import { PhaseView } from "@/components/PhaseView";
import { ScenarioTable } from "@/components/ScenarioTable";
import { MACRO_PAGES } from "@/components/MacroPanel";
import { SetupsView } from "@/components/SetupsView";
import { SeriesHistory, readableSource } from "@/components/SeriesDetail";
import { SnapshotView, deltaUnit, leadFigure } from "@/components/SnapshotView";
import { bandGeometry, isIncompleteBand } from "@/lib/conditions";
import {
  gateRefusedATouch,
  martDisagreement,
  phaseCallDetail,
  inForceOn,
  isUnscored,
  isoWeekMonday,
  nextPhaseRead,
  phaseMove,
  phaseScore,
  observationHistory,
  readSingleCalibration,
  readSwingVariables,
  scenarioSetHistory,
  resolveFocusBand,
  scenarioHistory,
  scenarioState,
  snapshotRow,
  weekGrade,
  type ConditionState,
  type PhaseScore,
  type ScenarioState,
  type ScenarioTargetState,
  type SetupState,
  type SnapshotRow,
} from "@/lib/contract";
import {
  daysBefore,
  fetchObservationHistory,
  fetchRecentObservations,
  fixtureSource,
  isExpiredSession,
  isMissingRelation,
  keys,
  latestStamp,
  readingOf,
  supabaseSource,
} from "@/lib/data";
import { NOT_CONFIGURED } from "@/lib/env";
import { fixtureSnapshot } from "@/lib/fixtures";
import {
  countsSentence,
  dimsRow,
  needsChip,
  readReading,
  worstFault,
  sourceFamily,
} from "@/lib/freshness";
import {
  ABSENT,
  formatBasisPoints,
  formatBounds,
  formatDelta,
  formatLevel,
  formatMoney,
  formatOutOf,
  formatSeriesDelta,
  labelNamesUnit,
} from "@/lib/numbers";
import { hiddenWeeksLine } from "@/hooks/useHistory";
import { calibrationLead, readHitRate } from "@/hooks/useCalibration";
import { ageLabel, missedRuns, parseDay } from "@/lib/time";
import { ReadingBadge } from "@/components/StatusChip";
import { numeric } from "@/lib/validate";

/**
 * Failure modes, not features.
 *
 * Every case here is a way this app can be wrong on screen while looking right,
 * which is the only class of bug this product cannot tolerate. Each test states
 * what the operator should see, because "does not crash" is not the bar: a
 * silent fallback to a plausible number is worse than a crash, and on a
 * terminal whose data moves twice a day a wrong figure can sit unnoticed for
 * eleven hours.
 */

/* ------------------------------------------------------------- scaffolding */

function readingOfStates(
  states: Parameters<typeof readReading>[0]["states"],
  overrides: Partial<Parameters<typeof readReading>[0]> = {},
) {
  return readReading({
    states,
    rejected: [],
    transportError: null,
    evaluatedAt: "2026-09-21T13:00:00.000Z",
    answered: true,
    subject: "the snapshot",
    relations: ["macro_mart.snapshot_row"],
    ...overrides,
  });
}

/** A `SupabaseClient` shaped just enough for the source to call it. */
function fakeClient(behaviour: {
  select?: () => Promise<{ data: unknown; error: unknown }>;
}) {
  const selectResult = () => {
    const promise =
      behaviour.select?.() ?? Promise.resolve({ data: [], error: null });
    return Object.assign(promise, { eq: () => promise });
  };
  return {
    schema: () => ({ from: () => ({ select: selectResult }) }),
    auth: { signOut: () => {} },
  } as never;
}

const SNAPSHOT_WIRE = {
  series_id: "ser-spx_cash",
  key: "spx_cash",
  label: "S&P 500 (cash)",
  table_group: "Price & Levels",
  ordinal: 2,
  value: "7650.50",
  display: "7,650.50",
  unit: "index_points",
  value_kind: "numeric",
  delta_native: "-0.09",
  delta_native_label: "1d %",
  delta_week_open: "-0.08",
  anchor_asof: "2026-09-11",
  tier: 1,
  source: "Yahoo chart API (^GSPC)",
  asof: "2026-09-18",
  released: null,
  next_expected: "2026-09-21",
  cadence: "session",
  freshness: "fresh",
  note: null,
  extra: {},
  updated_at: "2026-09-21T13:00:00.000Z",
};

function snapshot(overrides: Record<string, unknown> = {}): SnapshotRow {
  const checked = snapshotRow({ ...SNAPSHOT_WIRE, ...overrides });
  if (!checked.ok) throw new Error(checked.reason);
  return checked.value;
}

const SCORE: PhaseScore = {
  asset: "equities",
  phaseCallId: "pc-eq",
  phase: 3,
  phaseName: "Late Cycle / Distribution",
  stage: "late",
  met: 0,
  of: 4,
  machineChecked: 3,
  manual: 0,
  unevaluable: 0,
  failed: 0,
  readStale: false,
  evaluatedAt: "2026-09-21T13:00:00.000Z",
};

function condition(overrides: Partial<ConditionState> = {}): ConditionState {
  return {
    conditionId: "c1",
    ownerKind: "phase",
    ownerId: "pc-eq",
    ordinal: 1,
    text: "Breadth depressed and the decline broad-based",
    kind: "expr",
    status: "not yet",
    currentDisplay: "breadth 49.50% against 45%",
    evidence: {},
    evaluatedAt: "2026-09-21T13:00:00.000Z",
    voidReason: null,
    voidAt: null,
    source: "computed",
    ...overrides,
  };
}

function target(
  overrides: Partial<ScenarioTargetState> = {},
): ScenarioTargetState {
  return {
    targetId: "t1",
    scenarioId: "S1",
    seriesId: "ser-vix",
    label: "VIX",
    kind: "band",
    comparator: "between",
    lo: 13,
    hi: 19,
    current: 14.81,
    holding: true,
    distanceToBreach: 1.81,
    text: null,
    evaluatedAt: "2026-09-21T13:00:00.000Z",
    ...overrides,
  };
}

function setup(overrides: Partial<SetupState> = {}): SetupState {
  return {
    setupId: "su1",
    week: "2026-W39",
    asset: "equities",
    name: "S&P FOMC-shelf FTR long",
    entryLo: 7500,
    entryHi: 7560,
    invalidation: 7450,
    invalidationDir: "below",
    target: 7800,
    rrPlanned: 3.4,
    gateState: "open",
    gateReason: null,
    gateText: "HY under 292 bps and breadth not below 45%",
    bandTouchedToday: false,
    bandTouchedClose: false,
    entryState: "not triggered",
    sessionsCarried: 0,
    rRunning: null,
    sectorFocusLabel: "Deposit-funded Asian banks.",
    seriesKey: "spx_cash",
    seriesLabel: "S&P 500 (cash)",
    evaluatedAt: "2026-09-21T13:00:00.000Z",
    ...overrides,
  };
}

function scenario(overrides: Partial<ScenarioState> = {}): ScenarioState {
  return {
    scenarioId: "S1",
    setId: "set-w39",
    ordinal: 1,
    title: "Narrow and high-yielding",
    probability: 45,
    positioning: null,
    clausesFired: 1,
    clausesTotal: 4,
    clausesVoid: 0,
    requiredUnmet: 3,
    bandsHolding: 5,
    bandsTotal: 11,
    targetsHolding: 5,
    targetsTotal: 12,
    premiseMoved: false,
    evaluatedAt: "2026-09-21T13:00:00.000Z",
    ...overrides,
  };
}

/* ========================================================================= */

describe("a weekly series between prints is not stale", () => {
  it("does not treat `due` as a fault, so a snapshot of due series reads fresh", () => {
    const reading = readingOfStates(["fresh", "due", "due", "due", "asserted"]);
    expect(reading.state).toBe("fresh");
    expect(reading.hasData).toBe(true);
    expect(reading.banner).toBeNull();
  });

  it("dims the body only on an envelope fault, never over one bad series", () => {
    // One unwired series out of forty-seven does not make the other forty-six
    // less true. The bad row dims its own cells; the panel does not.
    expect(readingOfStates(["fresh", "due", "not_connected"]).dimmed).toBe(
      false,
    );
    expect(
      readingOfStates(["fresh"], {
        transportError: "The browser could not reach the project.",
      }).dimmed,
    ).toBe(true);
  });

  it("does not dim a due row, because it is the current reading for its cadence", () => {
    expect(dimsRow("due")).toBe(false);
    expect(dimsRow("asserted")).toBe(false);
    expect(dimsRow("overdue")).toBe(true);
    expect(dimsRow("gap")).toBe(true);
    expect(dimsRow("failed")).toBe(true);
  });

  it("ranks overdue as a fault while due is not, so the two never share a colour", () => {
    expect(worstFault(["fresh", "due", "due"])).toBe("fresh");
    expect(worstFault(["fresh", "due", "overdue"])).toBe("overdue");
    expect(worstFault(["overdue", "failed"])).toBe("failed");
    expect(worstFault(["gap", "overdue"])).toBe("gap");
  });

  it("gives no chip to a fresh or due row, so an empty column means nothing is wrong", () => {
    expect(needsChip("fresh")).toBe(false);
    expect(needsChip("due")).toBe(false);
    for (const state of ["overdue", "gap", "asserted", "not_connected", "failed"] as const) {
      expect(needsChip(state)).toBe(true);
    }
  });

  it("says in words that a due series is inside its cadence", () => {
    const reading = readingOfStates(["fresh", "due", "due"]);
    expect(reading.reason).toContain("due, not overdue");
  });

  it("drops zero counts from the header line rather than asserting four zeroes", () => {
    const line = countsSentence({
      fresh: 40,
      due: 3,
      overdue: 0,
      gap: 0,
      asserted: 2,
      not_connected: 0,
      failed: 0,
    });
    expect(line).toBe("40 fresh · 3 due · 2 manual");
  });

  it("renders a due row with its next-expected date and no warning chip", () => {
    render(
      <SnapshotView
        gaps={[]}
        rows={[
          snapshot({
            key: "cftc_asset_managers_es",
            series_id: "ser-cftc",
            label: "CFTC asset managers, ES net",
            table_group: "Sentiment & Positioning",
            ordinal: 5,
            freshness: "due",
            asof: "2026-09-15",
            released: "2026-09-18",
            next_expected: "2026-09-25",
            cadence: "weekly",
            delta_native_label: "WoW pp",
          }),
        ]}
      />,
    );
    const row = screen.getByText("CFTC asset managers, ES net").closest("tr")!;
    // The next-expected cell is what explains a Tuesday as-of on a Friday
    // screen, so it has to be on the row rather than behind a hover.
    expect(row.textContent).toContain("25 Sep");
    expect(within(row).queryByText("OVERDUE")).not.toBeInTheDocument();
    expect(within(row).queryByText("STALE")).not.toBeInTheDocument();
    // The legend explains the word, but the row itself carries no chip.
    expect(within(row).queryByText("DUE")).not.toBeInTheDocument();
  });
});

describe("a permanent gap never becomes a value", () => {
  it("keeps a blanked reading null rather than carrying the prior week forward", () => {
    const rows = fixtureSnapshot("honest").snapshotRows as Record<
      string,
      unknown
    >[];
    const cnn = rows.find((row) => row["key"] === "cnn_fear_greed")!;
    expect(cnn["value"]).toBeNull();
    expect(cnn["display"]).toBeNull();
    expect(cnn["freshness"]).toBe("gap");
    // The prior week read 33. No field that a renderer would print as a
    // figure carries it, and none carries a zero either. The note is allowed
    // to name it, because the note is the sentence explaining that it was not
    // carried forward.
    for (const field of [
      "value",
      "display",
      "delta_native",
      "delta_week_open",
    ]) {
      expect(cnn[field], field).toBeNull();
    }
    expect(cnn["asof"]).toBeNull();
    expect(cnn["tier"]).toBe(4);
  });

  it("prints the absent dash rather than a zero for a gapped row", () => {
    render(
      <SnapshotView
        gaps={[
          {
            id: 1,
            seriesId: "ser-cnn_fear_greed",
            seriesKey: "cnn_fear_greed",
            asof: "2026-09-18",
            reason: "CNN answered 451 and both aggregators answered 403.",
            permanent: true,
            openedAt: "2026-09-21T01:30:00.000Z",
          },
        ]}
        rows={[
          snapshot({
            key: "cnn_fear_greed",
            series_id: "ser-cnn",
            label: "Fear & Greed, equities",
            value: null,
            display: null,
            delta_native: null,
            delta_week_open: null,
            asof: null,
            freshness: "gap",
            tier: 4,
          }),
        ]}
      />,
    );
    const row = screen.getByText("Fear & Greed, equities").closest("tr")!;
    expect(within(row).getByText("MISSING DAY")).toBeInTheDocument();
    // The recoverability is a tooltip on the chip, not a second word on the
    // row: the reader needs the age of the figure, the cause one hover down.
    expect(within(row).queryByText("permanent")).not.toBeInTheDocument();
    expect(
      within(row).getByText("MISSING DAY").closest(".row-state")!,
    ).toHaveAttribute("title", expect.stringContaining("Not recoverable"));
    expect(within(row).queryByText("0")).not.toBeInTheDocument();
    expect(within(row).getAllByText(ABSENT).length).toBeGreaterThan(0);
  });
});

describe("an index pair is folded without hiding the value a condition reads", () => {
  function pair() {
    return [
      snapshot({
        key: "spx_front",
        series_id: "ser-spx_front",
        label: "S&P 500 (ES=F)",
        ordinal: 1,
        value: "7657.35",
        display: "7,657.35",
        delta_week_open: "-0.03",
      }),
      snapshot({
        key: "spx_cash",
        series_id: "ser-spx_cash",
        label: "S&P 500 (cash)",
        ordinal: 2,
        value: "7650.50",
        display: "7,650.50",
        delta_week_open: "-0.08",
      }),
    ];
  }

  it("renders one row carrying both levels", () => {
    render(<SnapshotView gaps={[]} rows={pair()} />);
    expect(screen.getAllByRole("row")).toHaveLength(2); // header plus one
    const row = screen.getByText("S&P 500").closest("tr")!;
    expect(row.textContent).toContain("7,657.35");
    // The cash level stays visible. Hiding it behind the future would be the
    // R1 mistake again: burying the value every condition reads.
    expect(row.textContent).toContain("cash 7,650.50");
  });

  it("shows the cash index's percentage, never the future's", () => {
    render(<SnapshotView gaps={[]} rows={pair()} />);
    const row = screen.getByText("S&P 500").closest("tr")!;
    // -0.08 is the cash week-over-week. -0.03 is the future's, and contract
    // rolls contaminate it, which is the arithmetic R1 is about.
    expect(row.textContent).toContain("-0.08");
    expect(row.textContent).not.toContain("-0.03");
  });

  it("does not fold a cash index that has no future", () => {
    render(
      <SnapshotView
        gaps={[]}
        rows={[
          snapshot({
            key: "nasdaq_cash",
            series_id: "ser-nasdaq",
            label: "Nasdaq Composite (cash)",
            ordinal: 5,
          }),
        ]}
      />,
    );
    expect(
      screen.getByText("Nasdaq Composite (cash)"),
    ).toBeInTheDocument();
  });

  it("does not fold a pair the contract does not name as one", () => {
    // Only the three index pairs fold. A future turning up beside an unrelated
    // cash series must not be merged into it.
    render(
      <SnapshotView
        gaps={[]}
        rows={[
          snapshot({
            key: "gold",
            series_id: "ser-gold",
            label: "Gold",
            ordinal: 11,
          }),
          snapshot({
            key: "silver",
            series_id: "ser-silver",
            label: "Silver",
            ordinal: 12,
          }),
        ]}
      />,
    );
    expect(screen.getAllByRole("row")).toHaveLength(3);
  });
});

describe("an interpretation is never truncated", () => {
  const LONG =
    "Roughly $6.2tn of options expired on this date, the second-largest triple witching on record, alongside an S&P rebalance whose largest mechanical trade was a $7.9bn sell in Information Technology.";

  it("keeps the table to one line per row and puts the read behind a disclosure", () => {
    const { container } = render(
      <SnapshotView gaps={[]} rows={[snapshot({ note: LONG })]} />,
    );
    // Forty-seven reads printed inline is a page of prose with figures in it.
    // The table is the figures.
    expect(container.querySelector(".snapshot-table td small")).toBeNull();
    const open = screen.getByRole("button", { expanded: false });
    expect(open).toBeInTheDocument();
  });

  it("renders the read in full when the row is opened, never clipped", async () => {
    const user = userEvent.setup();
    render(
      <Providers>
        <SnapshotView gaps={[]} rows={[snapshot({ note: LONG })]} />
      </Providers>,
    );
    await user.click(screen.getByRole("button", { expanded: false }));
    // Whole, in the document, not behind a hover and not ellipsised. A
    // truncated interpretation is worse than none: the reader cannot tell
    // whether the missing half changes the meaning.
    const read = await screen.findByText(LONG);
    expect(read.textContent).toBe(LONG);
  });

  it("names the absence of a read rather than leaving a blank, and explains nothing", async () => {
    const user = userEvent.setup();
    render(
      <Providers>
        <SnapshotView gaps={[]} rows={[snapshot({ note: null })]} />
      </Providers>,
    );
    await user.click(screen.getByRole("button", { expanded: false }));
    expect(
      await screen.findByText("No read was written for this observation."),
    ).toBeInTheDocument();
    // The policy is enforced in the code and pinned by the probes below. It is
    // not narrated on screen: a reader wants the insight or its absence, not a
    // paragraph about how absences are handled.
    expect(screen.queryByText(/re-derived/)).not.toBeInTheDocument();
    expect(screen.queryByText(/observations on record carry/)).not.toBeInTheDocument();
  });

  it("renders a clause's evidence in full", () => {
    const evidence =
      "breadth 49.50% against the 45% threshold, with the 18 September expiry distorting the print";
    render(
      <ConditionRow condition={condition({ currentDisplay: evidence })} />,
    );
    expect(screen.getByText(evidence).textContent).toBe(evidence);
  });
});

describe("a read is never shown beside a value it was not written about", () => {
  const CURRENT = "Through 50% for the first time in the arc.";

  it("shows each observation's own read, not the current one", () => {
    const rows = readingOf(
      fixtureSnapshot("clean").observations,
      observationHistory,
    ).rows.filter((row) => row.seriesKey === "breadth_spx_above_200dma");
    const own = rows.find((row) => row.asof === "2026-09-15");
    const blank = rows.find((row) => row.asof === "2026-09-17");
    expect(own?.note).toContain("shut the W38 setup");
    // The blank stays blank. Not re-derived from the value, and not borrowed
    // from the reading either side of it.
    expect(blank?.note).toBeNull();
  });

  it("leaves a missing read blank rather than filling it from the current one", () => {
    const rows = readingOf(
      fixtureSnapshot("clean").observations,
      observationHistory,
    ).rows.filter((row) => row.seriesKey === "breadth_spx_above_200dma");
    const withRead = rows.filter((row) => row.note !== null).length;
    expect(withRead).toBeGreaterThan(0);
    expect(rows.length - withRead).toBeGreaterThan(0);

    render(
      <SeriesHistory
        pending={false}
        reading={{ rows, rejected: [], transportError: null }}
        row={snapshot({ key: "breadth_spx_above_200dma", note: CURRENT })}
      />,
    );
    // The current read appears exactly once, as the current read. A `??` on
    // the row's own note would repeat it down the column and attribute a
    // sentence to every day that did not produce one.
    expect(screen.getAllByText(CURRENT)).toHaveLength(1);
    // And every observation without one says so.
    expect(
      screen.getAllByText("No read was written for this observation."),
    ).toHaveLength(rows.length - withRead);
  });

  it("never prints more reads than there are observations that carry one", () => {
    const rows = readingOf(
      fixtureSnapshot("clean").observations,
      observationHistory,
    ).rows.filter((row) => row.seriesKey === "btc");
    const { container } = render(
      <SeriesHistory
        pending={false}
        reading={{ rows, rejected: [], transportError: null }}
        row={snapshot({ key: "btc", note: CURRENT })}
      />,
    );
    const cells = container.querySelectorAll(".obs-read");
    const filled = [...cells].filter(
      (cell) => cell.querySelector(".obs-read-none") === null,
    );
    expect(filled).toHaveLength(rows.filter((r) => r.note !== null).length);
  });

  it("counts how many observations carry a read of their own", () => {
    const rows = readingOf(
      fixtureSnapshot("clean").observations,
      observationHistory,
    ).rows;
    const withRead = rows.filter((row) => row.note !== null);
    // Sparse on purpose: the reads only start being stored partway through
    // each arc, which is what the real migration looks like.
    expect(withRead.length).toBeGreaterThan(0);
    expect(withRead.length).toBeLessThan(rows.length);
  });

  it("does not repeat one series' read onto another's observation", () => {
    const rows = readingOf(
      fixtureSnapshot("clean").observations,
      observationHistory,
    ).rows;
    const byNote = new Map<string, Set<string>>();
    for (const row of rows) {
      if (row.note === null) continue;
      const seen = byNote.get(row.note) ?? new Set<string>();
      seen.add(row.seriesKey);
      byNote.set(row.note, seen);
    }
    for (const [note, series] of byNote) {
      expect(series.size, note.slice(0, 40)).toBe(1);
    }
  });
});

describe("a text series' history reads as what it said, not as a table", () => {
  const PROSE = {
    value_kind: "text",
    value: null,
    unit: null,
    delta_native: null,
    delta_native_label: null,
    delta_week_open: null,
    anchor_asof: null,
  };

  function proseRows(key: string) {
    return readingOf(
      fixtureSnapshot("clean").observations,
      observationHistory,
    ).rows.filter((row) => row.seriesKey === key);
  }

  it("lists every statement in full, newest first, with no chart and no table", () => {
    const rows = proseRows("fed_funds");
    expect(rows.length).toBeGreaterThan(2);
    const { container } = render(
      <SeriesHistory
        pending={false}
        reading={{ rows, rejected: [], transportError: null }}
        row={snapshot({
          ...PROSE,
          key: "fed_funds",
          display: rows[0]!.display,
        })}
      />,
    );
    // Nothing to draw, so no chart claiming there is nothing on record, and no
    // nowrap Value column for a paragraph to push seven thousand pixels wide.
    expect(container.querySelector("table")).toBeNull();
    expect(screen.queryByText(/No observation in/)).not.toBeInTheDocument();

    const entries = [...container.querySelectorAll(".series-detail-entry")];
    expect(entries).toHaveLength(rows.length);
    const newestFirst = [...rows].sort((a, b) => b.asof.localeCompare(a.asof));
    entries.forEach((entry, index) => {
      expect(
        entry.querySelector(".series-detail-entry-text")?.textContent,
      ).toBe(newestFirst[index]!.display);
    });
  });

  it("shows only the reads that were written, each under its own day", () => {
    const rows = proseRows("fed_funds");
    const withRead = rows.filter((row) => row.note !== null);
    expect(withRead.length).toBeGreaterThan(0);
    expect(withRead.length).toBeLessThan(rows.length);
    const { container } = render(
      <SeriesHistory
        pending={false}
        reading={{ rows, rejected: [], transportError: null }}
        row={snapshot({ ...PROSE, key: "fed_funds", note: null })}
      />,
    );
    // No "no read" line down every restatement: in prose most carry none.
    expect(
      screen.queryByText("No read was written for this observation."),
    ).not.toBeInTheDocument();
    const reads = container.querySelectorAll(".series-detail-entry-read");
    expect(reads).toHaveLength(withRead.length);
    // And each sits in the entry of the observation that carried it.
    for (const row of withRead) {
      const read = screen.getByText(row.note!);
      const entry = read.closest(".series-detail-entry");
      expect(
        entry?.querySelector(".series-detail-entry-text")?.textContent,
      ).toBe(row.display);
    }
  });

  it("never lends the current read to a restatement that did not carry it", () => {
    const CURRENT = "A read the stored history never carried.";
    const rows = proseRows("geopolitical_state");
    expect(rows.every((row) => row.note === null)).toBe(true);
    const { container } = render(
      <SeriesHistory
        pending={false}
        reading={{ rows, rejected: [], transportError: null }}
        row={snapshot({ ...PROSE, key: "geopolitical_state", note: CURRENT })}
      />,
    );
    // Once, as the current read, and never inside a dated entry.
    expect(screen.getAllByText(CURRENT)).toHaveLength(1);
    expect(
      container.querySelectorAll(".series-detail-entry-read"),
    ).toHaveLength(0);
  });

  it("merges a statement restated unchanged into one entry over its days", () => {
    const rows = proseRows("next_fomc");
    const { container } = render(
      <SeriesHistory
        pending={false}
        reading={{ rows, rejected: [], transportError: null }}
        row={snapshot({ ...PROSE, key: "next_fomc" })}
      />,
    );
    const days = [
      ...container.querySelectorAll(".series-detail-entry-day"),
    ].map((day) => day.textContent);
    expect(days).toHaveLength(2);
    expect(days[1]).toMatch(/^4–11 Sept$/);
  });

  it("reads a source the way a person would cite it", () => {
    expect(
      readableSource(
        "FOMC coverage (search agent) (migrated from Weekly Market Journal 2026-W38)",
      ).map((part) => part.text),
    ).toEqual(["FOMC coverage"]);
    expect(
      readableSource(
        "Wire and analysis coverage (asserted, search agent) (migrated from Weekly Market Journal 2026-W37)",
      ).map((part) => part.text),
    ).toEqual(["Wire and analysis coverage"]);
    const [calendar] = readableSource(
      "search agent (macro): federalreserve.gov FOMC calendar",
    );
    expect(calendar?.text).toBe("federalreserve.gov FOMC calendar");
    expect(calendar?.href).toBe("https://federalreserve.gov");
    const [, statement] = readableSource(
      "search agent (macro): CNBC 2026-09-16 Fed decision; https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm",
    );
    expect(statement?.link).toBe("federalreserve.gov");
    expect(statement?.href).toMatch(/^https:\/\/www\.federalreserve\.gov\//);
    expect(readableSource("CNBC 2026-09-16 Fed decision")[0]?.text).toBe(
      "CNBC 16 Sept Fed decision",
    );
  });
});

describe("the chart claims only what was measured", () => {
  function obs(asof: string, value: number | null) {
    return {
      seriesId: "ser-x",
      seriesKey: "x",
      asof,
      value,
      display: null,
      unit: null,
      tier: 1,
      source: "test",
      released: null,
      observedAt: null,
      note: null,
    };
  }

  it("draws nothing across a single observation", () => {
    render(<SeriesChart label="x" rows={[obs("2026-09-18", 10)]} />);
    // A flat line across one point is a claim that the series did not move.
    expect(
      screen.getByText(/Nothing is drawn across a single point/),
    ).toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });

  it("breaks the line at a gap rather than bridging it", () => {
    const { container } = render(
      <SeriesChart
        label="x"
        rows={[
          obs("2026-09-14", 10),
          obs("2026-09-15", 11),
          obs("2026-09-16", 12),
          // Three weeks nothing stated.
          obs("2026-10-08", 13),
        ]}
      />,
    );
    // Bridging would draw a trend through a stretch nobody looked at.
    expect(container.querySelectorAll("polyline").length).toBeGreaterThan(1);
    expect(screen.getByText(/1 break/)).toBeInTheDocument();
  });

  it("draws one continuous line when every step is the series' own", () => {
    const { container } = render(
      <SeriesChart
        label="x"
        rows={[
          obs("2026-09-14", 10),
          obs("2026-09-15", 11),
          obs("2026-09-16", 12),
        ]}
      />,
    );
    expect(container.querySelectorAll("polyline")).toHaveLength(1);
  });

  it("does not break a weekly series on its own seven-day step", () => {
    const { container } = render(
      <SeriesChart
        label="CFTC"
        rows={[
          obs("2026-09-01", 45.64),
          obs("2026-09-08", 44.04),
          obs("2026-09-15", 36.98),
        ]}
      />,
    );
    // A rule in days rather than in the series' own step would break every
    // weekly line at every point.
    expect(container.querySelectorAll("polyline")).toHaveLength(1);
  });

  it("carries no direction colour", () => {
    const { container } = render(
      <SeriesChart
        label="x"
        rows={[obs("2026-09-14", 10), obs("2026-09-18", 40)]}
      />,
    );
    expect(container.querySelector(".up")).toBeNull();
    expect(container.querySelector(".down")).toBeNull();
  });
});

describe("a view is not a subject", () => {
  it("opens on the Desk, with the evidence one page behind it", () => {
    // The call first, because it is the question a trader opens the terminal
    // with; the evidence it is read against second. They are two routes over
    // one reading, not two readings.
    expect(MACRO_PAGES.map((page) => [page.id, page.href])).toEqual([
      ["desk", "/"],
      ["data", "/data"],
    ]);
  });

  it("keys every query on the source alone, so a page change refetches nothing", () => {
    // Structural rather than careful: there is no key for a tab to vary, so a
    // view switch cannot issue a request or reset a clock.
    for (const [name, make] of Object.entries(keys)) {
      // Two exceptions, both narrowing reads of observation_history rather
      // than whole-relation ones: one series, and one window.
      if (name === "observations" || name === "recentObservations") continue;
      const key = (make as (scope: string) => readonly unknown[])("supabase");
      expect(key, name).toEqual([key[0], key[1], "supabase"]);
    }
  });

  it("keys a series' history per series, so a row opened twice is read once", () => {
    // The one exception, and the reason for it: observation_history holds
    // every observation of every series, and a disclosure needs one of them.
    expect(keys.observations("supabase", "spx_cash")).toEqual([
      "macro_mart",
      "observation_history",
      "supabase",
      "spx_cash",
    ]);
    expect(keys.observations("supabase", "vix")).not.toEqual(
      keys.observations("supabase", "spx_cash"),
    );
  });

  it("reads one series of history, not all forty-seven", async () => {
    const source = fixtureSource("clean");
    const one = await fetchObservationHistory(source, "spx_cash");
    const all = readingOf(
      fixtureSnapshot("clean").observations,
      observationHistory,
    );
    expect(one.rows.length).toBeGreaterThan(0);
    expect(one.rows.every((row) => row.seriesKey === "spx_cash")).toBe(true);
    // Loading every series to draw one sparkline is several thousand rows.
    expect(one.rows.length).toBeLessThan(all.rows.length);
  });

  it("has no history row on the day a point-in-time source was missed", async () => {
    const source = fixtureSource("honest");
    const rows = await fetchObservationHistory(source, "cnn_fear_greed");
    // A gap is an absence in the history as much as in the snapshot. Nothing
    // is interpolated and no neighbour is stretched across it.
    expect(rows.rows.some((row) => row.asof === "2026-09-18")).toBe(false);
    expect(rows.rows.length).toBeGreaterThan(0);
  });
});

describe("the targets are one line against one denominator", () => {
  it("states what the figure is made of rather than showing two of them", () => {
    render(
      <ScenarioTable
        conditions={[]}
        scenarios={[
          scenario({
            probability: 100,
            bandsHolding: 11,
            bandsTotal: 13,
            targetsHolding: 11,
            targetsTotal: 14,
          }),
        ]}
        targets={[
          target({ targetId: "b1" }),
          target({
            targetId: "m1",
            kind: "manual",
            seriesId: null,
            comparator: null,
            lo: null,
            hi: null,
            current: null,
            holding: null,
            distanceToBreach: null,
            label: "Leadership",
            text: "Nasdaq beats the Dow again",
          }),
          target({ targetId: "u1", current: null, holding: null }),
          target({ targetId: "u2", current: null, holding: null }),
        ]}
      />,
    );
    // Two labelled counters and a footnote warning the reader not to confuse
    // them handed over the confusion with the warning attached. One line, the
    // denominator the week is graded on, and the split it is made of.
    const row = screen.getByText("Narrow and high-yielding").closest("tr")!;
    expect(
      within(row).getByText(
        "11 of 14 in band · 1 needs judgment · 2 no reading",
      ),
    ).toBeInTheDocument();
    expect(within(row).queryByText("Bands holding")).not.toBeInTheDocument();
    expect(within(row).queryByText(/W36 defect/)).not.toBeInTheDocument();
  });

  it("chips only the clauses that were evaluated, and counts the rest once", () => {
    render(
      <ScenarioTable
        conditions={[
          condition({
            conditionId: "c1",
            ordinal: 1,
            ownerKind: "trigger",
            ownerId: "S1",
            status: "unevaluable",
            text: "The auction cluster clears without a second record tail",
          }),
          condition({
            conditionId: "c2",
            ordinal: 2,
            ownerKind: "trigger",
            ownerId: "S1",
            status: "unevaluable",
            text: "Micron confirms HBM pricing",
          }),
          condition({
            conditionId: "c3",
            ordinal: 3,
            ownerKind: "trigger",
            ownerId: "S1",
            status: "not yet",
            text: "Brent stays inside a wide band",
          }),
        ]}
        scenarios={[scenario({ probability: 100 })]}
        targets={[]}
      />,
    );
    // Four clauses each wearing the same UNEVALUABLE chip was the loudest
    // thing in the column and it distinguished nothing.
    expect(screen.queryByText("UNEVALUABLE")).not.toBeInTheDocument();
    expect(screen.getByText("2 of 3 need judgment")).toBeInTheDocument();
    // An evaluated clause keeps its chip, and NOT YET above all: evaluated and
    // false is exactly the claim an unevaluated clause must never be read as.
    expect(screen.getByText("NOT YET")).toBeInTheDocument();
    expect(screen.getByText("Micron confirms HBM pricing")).toBeInTheDocument();
  });

  it("prefers the worker's counts over counting the rows itself", () => {
    render(
      <ScenarioTable
        conditions={[]}
        scenarios={[
          scenario({ probability: 100, targetsHolding: 7, targetsTotal: 11 }),
        ]}
        // Two rows on screen, eleven in the worker's count. The app computes
        // no metric, so the worker's figure wins.
        targets={[target(), target({ targetId: "t2" })]}
      />,
    );
    expect(screen.getByText("7 of 11 in band")).toBeInTheDocument();
  });

  it("prints a dash rather than zero when the worker stated no count", () => {
    render(
      <ScenarioTable
        conditions={[]}
        scenarios={[
          scenario({
            probability: 100,
            targetsHolding: null,
            targetsTotal: null,
          }),
        ]}
        targets={[]}
      />,
    );
    const row = screen.getByText("Narrow and high-yielding").closest("tr")!;
    // A dash, never a zero: nobody counted is not the same as counted none.
    expect(within(row).getByText(/in band/).textContent).toContain("—");
  });
});

describe("a strategy with no holdings on record", () => {
  it("names the absence rather than rendering an empty table", () => {
    render(
      <SetupsView
        setups={[]}
        strategies={[
          {
            strategyId: "st",
            week: "2026-W39",
            asset: "equities",
            stance: "",
            posture: "Hold",
            exposureLo: 25,
            exposureHi: 50,
            exposureOperatedLo: null,
            exposureOperatedHi: null,
            sectorFocus: [],
            holdingsCheck: [],
            activatedAt: null,
          },
        ]}
      />,
    );
    // An empty table and an absent one are different claims, and only one of
    // them is a finding.
    expect(
      screen.getByText(/No holdings check is recorded against this strategy/),
    ).toBeInTheDocument();
    expect(screen.queryByText("Holding")).not.toBeInTheDocument();
  });

  it("renders the list when there is one", () => {
    render(
      <SetupsView
        setups={[]}
        strategies={[
          {
            strategyId: "st",
            week: "2026-W39",
            asset: "equities",
            stance: "",
            posture: "Hold",
            exposureLo: null,
            exposureHi: null,
            exposureOperatedLo: null,
            exposureOperatedHi: null,
            sectorFocus: [
              { id: "sf", band: "CRITICAL", text: "Memory and HBM." },
            ],
            holdingsCheck: [
              {
                id: "h",
                ticker: "MU",
                action: "keep",
                text: "No add before the print.",
                sectorFocusId: "sf",
              },
            ],
            activatedAt: null,
          },
        ]}
      />,
    );
    expect(screen.getByText("MU")).toBeInTheDocument();
    expect(screen.getAllByText("CRITICAL").length).toBeGreaterThan(0);
  });
});

describe("a prose row is not a failed fetch", () => {
  it("marks a text series so a macro state cannot read as an empty reading", () => {
    render(
      <SnapshotView
        gaps={[]}
        rows={[
          snapshot({
            key: "fed_funds",
            series_id: "ser-fed",
            label: "Fed funds rate",
            table_group: "Macro & Fiscal State",
            ordinal: 1,
            value: null,
            value_kind: "text",
            display: "3.75%–4.00% after a unanimous 12–0 hike.",
            delta_native: null,
            delta_native_label: null,
            delta_week_open: null,
            unit: null,
            next_expected: null,
          }),
          // A Tier 4 failure: also a null value with a string beside it.
          snapshot({
            key: "silver",
            series_id: "ser-silver",
            label: "Silver",
            table_group: "Macro & Fiscal State",
            ordinal: 2,
            value: null,
            value_kind: "numeric",
            display: null,
            delta_native: null,
            delta_week_open: null,
            tier: 4,
            freshness: "failed",
          }),
        ]}
      />,
    );
    const prose = screen.getByText("Fed funds rate").closest("tr")!;
    const failure = screen.getByText("Silver").closest("tr")!;
    // The two are the same shape in the data and must not draw alike.
    expect(prose.className).toContain("row-text");
    expect(failure.className).not.toContain("row-text");
    expect(
      within(prose).getByText(/3.75%–4.00% after a unanimous/),
    ).toBeInTheDocument();
    expect(within(failure).getByText("STALE")).toBeInTheDocument();
    expect(within(prose).queryByText("STALE")).not.toBeInTheDocument();
  });
});

describe("an unevaluable condition is never scored as not yet", () => {
  it("draws the two in different tokens", () => {
    const { container: a } = render(
      <ConditionRow condition={condition({ status: "unevaluable" })} />,
    );
    const { container: b } = render(
      <ConditionRow condition={condition({ status: "not yet" })} />,
    );
    expect(a.querySelector(".cond-unevaluable")).not.toBeNull();
    expect(b.querySelector(".cond-not-yet")).not.toBeNull();
    expect(a.querySelector(".cond-not-yet")).toBeNull();
  });

  it("prints the coverage beside the Holds while heading, not in a footnote", () => {
    render(
      <PhaseView
        calls={[]}
        conditions={[condition({ status: "unevaluable" })]}
        scores={[{ ...SCORE, met: 3, machineChecked: 2, unevaluable: 1 }]}
      />,
    );
    expect(screen.getByText("Holds while")).toBeInTheDocument();
    expect(
      screen.getByText("3 of 4 hold · 1 not checked today, no reading"),
    ).toBeInTheDocument();
    // No upgrade and no 3-of-4 rule: any phase may follow any phase.
    expect(screen.queryByText(/needs 3 of 4/)).not.toBeInTheDocument();
    expect(screen.queryByText(/upgrade/i)).not.toBeInTheDocument();
  });

  it("says how the four stand in plain words, without repeating a number", () => {
    const { rerender } = render(
      <PhaseView
        calls={[]}
        conditions={[]}
        scores={[{ ...SCORE, met: 4, machineChecked: 4, manual: 0, unevaluable: 0 }]}
      />,
    );
    expect(screen.getByText("4 of 4 hold")).toBeInTheDocument();
    expect(screen.queryByText(/machine-checked/)).not.toBeInTheDocument();
    rerender(
      <PhaseView
        calls={[]}
        conditions={[]}
        scores={[
          { ...SCORE, met: 2, failed: 2, machineChecked: 3, manual: 1, unevaluable: 0 },
        ]}
      />,
    );
    expect(
      screen.getByText("2 hold, 2 failed · 1 judged by hand"),
    ).toBeInTheDocument();
  });

  it("tags a manual condition only on a card that mixes the two kinds", () => {
    const { rerender } = render(
      <PhaseView
        calls={[]}
        conditions={[
          condition({ conditionId: "m1", ordinal: 1, kind: "manual" }),
          condition({ conditionId: "m2", ordinal: 2, kind: "manual" }),
        ]}
        scores={[{ ...SCORE, machineChecked: 0, manual: 2, of: 2 }]}
      />,
    );
    // All manual: the coverage line above has already said it once, and four
    // identical tags under it are that one fact repeated at chip weight.
    expect(screen.queryByText("JUDGMENT")).not.toBeInTheDocument();

    rerender(
      <PhaseView
        calls={[]}
        conditions={[
          condition({ conditionId: "m1", ordinal: 1, kind: "manual" }),
          condition({ conditionId: "e1", ordinal: 2, kind: "expr" }),
        ]}
        scores={[{ ...SCORE, machineChecked: 1, manual: 1, of: 2 }]}
      />,
    );
    expect(screen.getByText("JUDGMENT")).toBeInTheDocument();
  });
});

describe("the phase is read fresh every week", () => {
  const calls = (dataset: "clean" | "honest") =>
    readingOf(fixtureSnapshot(dataset).phaseCalls, phaseCallDetail).rows;
  const scores = (dataset: "clean" | "honest") =>
    readingOf(fixtureSnapshot(dataset).phaseScores, phaseScore).rows;
  const equities = () =>
    calls("clean").find((row) => row.asset === "equities")!;
  const NOW = new Date("2026-09-23T04:00:00.000Z");

  it("says when the phase was read and when it is read next", () => {
    render(
      <PhaseView
        calls={calls("clean")}
        conditions={[]}
        now={NOW}
        scores={scores("clean")}
      />,
    );
    expect(screen.getAllByText("Read Mon 21 Sept")).toHaveLength(2);
    expect(screen.getAllByText(/next read Mon 28 Sept/)).toHaveLength(2);
  });

  it("says the next read is overdue only once the Tuesday fallback has passed", () => {
    const call = equities();
    expect(
      nextPhaseRead(call, new Date("2026-09-29T15:00:00.000Z"))?.overdue,
    ).toBe(false);
    expect(
      nextPhaseRead(call, new Date("2026-09-30T00:00:00.000Z"))?.overdue,
    ).toBe(true);
    expect(isoWeekMonday("2026-W01")?.toISOString().slice(0, 10)).toBe(
      "2025-12-29",
    );
  });

  it("says how the read stands to last week's, in plain words", () => {
    const call = equities();
    expect(phaseMove(call)).toBe("Same as last week, 8 weeks running");
    expect(phaseMove({ ...call, priorPhase: 2, phase: 3 })).toBe(
      "Was P2 last week, now P3",
    );
    expect(phaseMove({ ...call, priorPhase: null })).toBeNull();
    // A jump of any size is legal: phases are regimes, not a fixed order.
    expect(phaseMove({ ...call, priorPhase: 4, phase: 2 })).toBe(
      "Was P4 last week, now P2",
    );
  });

  it("shows the change note beside the move", () => {
    render(
      <PhaseView
        calls={calls("clean")}
        conditions={[]}
        now={NOW}
        scores={scores("clean")}
      />,
    );
    expect(
      screen.getByText(/Breadth slipped under 50% for the first time/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/blind/i)).not.toBeInTheDocument();
  });

  it("says a read may be out of date when two of its four have failed", () => {
    render(
      <PhaseView
        calls={calls("honest")}
        conditions={[]}
        now={NOW}
        scores={scores("honest")}
      />,
    );
    expect(screen.getByText("2 of 4 failed")).toBeInTheDocument();
    expect(
      screen.getByText(/This read may be out of date: 2 of the 4 things/),
    ).toBeInTheDocument();
    // One stale read, not two: the equities read only has an unknown.
    expect(screen.getAllByText(/may be out of date/)).toHaveLength(1);
  });

  it("labels a carried journal call for what it is", () => {
    const call = { ...equities(), classification: "carried" as const };
    render(
      <PhaseView calls={[call]} conditions={[]} now={NOW} scores={[SCORE]} />,
    );
    expect(
      screen.getByText("Signs of Phase 4 (old journal)"),
    ).toBeInTheDocument();
    expect(screen.getByText("carried call")).toBeInTheDocument();
    expect(
      screen.getByText(/The first fresh read is Mon 28 Sept/),
    ).toBeInTheDocument();
    expect(screen.queryByText("Holds while")).not.toBeInTheDocument();
  });

  it("puts the phase, the stage and how it stands on the tile", () => {
    const { rerender, container } = render(
      <PhaseTile call={equities()} score={{ ...SCORE, met: 4 }} />,
    );
    expect(screen.getByText("late")).toBeInTheDocument();
    expect(container.querySelectorAll(".phase-stage i.is-here")).toHaveLength(1);
    expect(screen.getByText("same for 8 weeks")).toBeInTheDocument();
    expect(screen.getByText("4/4 hold")).toBeInTheDocument();
    expect(screen.queryByText(/to P4/)).not.toBeInTheDocument();

    rerender(
      <PhaseTile
        call={{ ...equities(), priorPhase: 2 }}
        score={{ ...SCORE, stage: null, failed: 2, readStale: true }}
      />,
    );
    expect(screen.getByText("was P2")).toBeInTheDocument();
    expect(screen.getByText("may be out of date")).toBeInTheDocument();
    // No strip at all when no stage was authored.
    expect(container.querySelector(".phase-stage")).toBeNull();

    rerender(
      <PhaseTile
        call={{ ...equities(), classification: "carried" }}
        score={SCORE}
      />,
    );
    expect(screen.getByText("carried call")).toBeInTheDocument();
  });

  it("names a score that still belongs to the previous call", () => {
    const call = equities();
    expect(
      martDisagreement({ ...SCORE, phaseCallId: call.phaseCallId }, call),
    ).toBeNull();
    expect(
      martDisagreement({ ...SCORE, phaseCallId: "pc-eq-w38" }, call),
    ).toContain("belong to the previous call");
    expect(martDisagreement(SCORE, undefined)).toBeNull();
  });

  it("shows no fault on either fixture dataset", () => {
    for (const dataset of ["clean", "honest"] as const) {
      for (const score of scores(dataset)) {
        const call = calls(dataset).find((row) => row.asset === score.asset);
        expect(martDisagreement(score, call)).toBeNull();
      }
    }
  });
});

describe("a target reads at a glance without being taught how", () => {
  it("answers in words and figures rather than drawing an extent", () => {
    const { container } = render(
      <ScenarioTable
        conditions={[]}
        scenarios={[scenario({ probability: 100 })]}
        targets={[target()]}
      />,
    );
    // A reader scanning three scenarios across one row is comparing bands to
    // each other, not studying one. IN, NEAR and OUT need no key.
    expect(screen.getByText("IN")).toBeInTheDocument();
    expect(screen.getByText("14.81")).toBeInTheDocument();
    expect(screen.getByText("13.00 – 19.00")).toBeInTheDocument();
    expect(container.querySelector(".band-mark")).toBeNull();
    expect(container.querySelector(".band-rail")).toBeNull();
  });

  it("uses the same three words for the three states, and no direction colour", () => {
    const { container } = render(
      <ScenarioTable
        conditions={[]}
        scenarios={[scenario({ probability: 100 })]}
        targets={[
          target({ targetId: "in", distanceToBreach: 1.81 }),
          target({ targetId: "near", distanceToBreach: 0.4 }),
          target({ targetId: "out", current: 40, holding: false }),
          target({
            targetId: "none",
            holding: null,
            current: null,
            distanceToBreach: null,
          }),
        ]}
      />,
    );
    expect(screen.getByText("IN")).toBeInTheDocument();
    expect(screen.getByText("NEAR")).toBeInTheDocument();
    expect(screen.getByText("OUT")).toBeInTheDocument();
    // A band is a statement about a forecast, not a position moving in your
    // favour.
    expect(container.querySelector(".up")).toBeNull();
    expect(container.querySelector(".down")).toBeNull();
  });

  it("marks a manual target as one to judge rather than scoring it", () => {
    render(
      <ScenarioTable
        conditions={[]}
        scenarios={[scenario({ probability: 100 })]}
        targets={[
          target({
            kind: "manual",
            seriesId: null,
            comparator: null,
            lo: null,
            hi: null,
            current: null,
            holding: null,
            distanceToBreach: null,
            label: "Leadership",
            text: "Nasdaq beats the Dow again",
          }),
        ]}
      />,
    );
    expect(screen.getByText("JUDGE")).toBeInTheDocument();
    // The asset leads the row and the authored sentence follows it, in full:
    // it is the whole of what the scenario will be graded against, so it is
    // never truncated and never replaced by a placeholder.
    expect(screen.getByText("Leadership")).toBeInTheDocument();
    expect(screen.getByText("Nasdaq beats the Dow again")).toBeInTheDocument();
  });
});

describe("a void clause stays in the denominator", () => {
  it("counts the void clause in the total and says so", () => {
    render(
      <ScenarioTable
        conditions={[
          condition({
            conditionId: "cl1",
            ownerKind: "trigger",
            ownerId: "S2",
            status: "void",
            text: "A tightening Senior Credit Officer survey on 24 September",
            voidReason: "Rescheduled to 5 November, outside the horizon.",
            currentDisplay: null,
          }),
        ]}
        scenarios={[
          scenario({
            scenarioId: "S2",
            ordinal: 2,
            probability: 100,
            clausesFired: 0,
            clausesTotal: 6,
            clausesVoid: 1,
            premiseMoved: true,
          }),
        ]}
        targets={[]}
      />,
    );
    // 6, not 5: renormalising would quietly make the scenario easier to satisfy.
    expect(screen.getByText("Clauses fired")).toBeInTheDocument();
    expect(screen.getByText("0 of 6")).toBeInTheDocument();
    expect(screen.getByText(/1 void, kept in/)).toBeInTheDocument();
    expect(screen.getByText("PREMISE MOVED")).toBeInTheDocument();
  });

  it("keeps the void clause visible and struck through, never removed", () => {
    const { container } = render(
      <ConditionRow
        condition={condition({
          status: "void",
          voidReason: "The catalyst was rescheduled out of the horizon.",
        })}
      />,
    );
    expect(container.querySelector(".cond-row.is-void")).not.toBeNull();
    expect(
      screen.getByText("Breadth depressed and the decline broad-based"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("The catalyst was rescheduled out of the horizon."),
    ).toBeInTheDocument();
  });

  it("flags a set whose probabilities do not sum to 100 instead of showing it plainly", () => {
    render(
      <ScenarioTable
        conditions={[]}
        scenarios={[
          scenario({ probability: 45 }),
          scenario({ scenarioId: "S2", ordinal: 2, probability: 30 }),
        ]}
        targets={[]}
      />,
    );
    expect(screen.getByText(/add up to 75, not 100/)).toBeInTheDocument();
  });
});

describe("a band is a forecast, not a position", () => {
  it("draws no marker at all when the series has no reading", () => {
    const geometry = bandGeometry(
      target({ holding: null, current: null, distanceToBreach: null }),
    );
    expect(geometry.state).toBe("unevaluable");
    expect(geometry.markPct).toBeNull();
  });

  it("uses the near token inside a tenth of the band's width, and flat otherwise", () => {
    // Band 13 to 19 is six wide, so the near threshold is 0.6.
    expect(bandGeometry(target({ distanceToBreach: 1.81 })).state).toBe(
      "holding",
    );
    expect(bandGeometry(target({ distanceToBreach: 0.4 })).state).toBe("near");
  });

  it("never uses a direction class on a band, whichever side it broke", () => {
    for (const current of [7.0, 40.0]) {
      const { container } = render(
        <BandRow target={target({ current, holding: false })} />,
      );
      expect(container.querySelector(".up")).toBeNull();
      expect(container.querySelector(".down")).toBeNull();
      expect(container.querySelector(".is-outside")).not.toBeNull();
    }
  });

  it("pins a value beyond the drawn extent to the edge rather than off it", () => {
    const geometry = bandGeometry(target({ current: 1000, holding: false }));
    expect(geometry.markPct).toBe(100);
    expect(geometry.clamped).toBe("high");
  });

  it("holds the manual target in the count without pretending to score it", () => {
    render(
      <BandRow
        target={target({
          kind: "manual",
          seriesId: null,
          lo: null,
          hi: null,
          comparator: null,
          current: null,
          holding: null,
          distanceToBreach: null,
          text: "Nasdaq beats the Dow again",
        })}
      />,
    );
    expect(screen.getByText("JUDGMENT")).toBeInTheDocument();
    expect(screen.getByText("not scored")).toBeInTheDocument();
  });

  it("does not invent a second edge on a one-sided band", () => {
    const geometry = bandGeometry(
      target({ comparator: "lt", lo: null, hi: 95, current: 103.87, holding: false }),
    );
    expect(geometry.spanLeftPct).toBe(0);
    expect(geometry.state).toBe("outside");
  });
});

describe("a one-sided band is not a band missing an edge", () => {
  it("draws `lt` as a forecast and `between` with a null floor as an omission", () => {
    const oneSided = target({
      comparator: "lt",
      lo: null,
      hi: 95,
      current: 103.87,
      holding: false,
    });
    const missingFloor = target({
      comparator: "between",
      lo: null,
      hi: 95,
      current: 103.87,
      holding: false,
    });

    // Identical rows before `comparator` existed, and only one is a forecast.
    expect(bandGeometry(oneSided).state).toBe("outside");
    expect(bandGeometry(missingFloor).state).toBe("unevaluable");
    expect(isIncompleteBand(oneSided)).toBe(false);
    expect(isIncompleteBand(missingFloor)).toBe(true);
  });

  it("prints the two differently", () => {
    expect(formatBounds("lt", null, 95)).toBe("< 95.00");
    // Never printed as `< 95`: a missing floor is an omission, not a ceiling.
    expect(formatBounds("between", null, 95)).toBe("… – 95.00");
    expect(formatBounds("gt", null, null)).toBe("no floor stated");
  });

  it("renders a manual target's stated sentence, not its label", () => {
    render(
      <BandRow
        target={target({
          kind: "manual",
          seriesId: null,
          comparator: null,
          lo: null,
          hi: null,
          current: null,
          holding: null,
          distanceToBreach: null,
          label: "Leadership",
          text: "Nasdaq beats the Dow again and memory leads the Nasdaq",
        })}
      />,
    );
    // It carries no numbers and still counts toward targets_n, so showing only
    // the label would understate what the scenario is graded on.
    expect(
      screen.getByText(
        "Nasdaq beats the Dow again and memory leads the Nasdaq",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText("Leadership")).not.toBeInTheDocument();
  });
});

describe("a setup that names no series is a refusal, not a result", () => {
  it("marks the row once, rather than saying the same thing three times", () => {
    render(
      <SetupsView
        setups={[
          setup({
            seriesKey: null,
            seriesLabel: null,
            gateState: "unevaluable",
            gateReason:
              "This setup names no series, so its levels cannot be resolved against a price and the worker does not score it.",
          }),
        ]}
        strategies={[]}
      />,
    );
    // The tag is the mark. The banner above the table and the amber sentence
    // under the row both carried the same fact, so the row shouted it three
    // times and the reader still learned it once.
    expect(screen.getByText("names no series · not scored")).toBeInTheDocument();
    expect(
      screen.queryByText(/One setup names no series, so the worker does not/),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText(/its levels cannot be resolved against a price/),
    ).not.toBeInTheDocument();
  });

  it("resolves the sector-focus row back to its band, never storing it twice", () => {
    render(
      <SetupsView
        setups={[setup({ sectorFocusLabel: "Deposit-funded Asian banks." })]}
        strategies={[
          {
            strategyId: "st",
            week: "2026-W39",
            asset: "equities",
            stance: "",
            posture: "Hold",
            exposureLo: 25,
            exposureHi: 50,
            exposureOperatedLo: null,
            exposureOperatedHi: null,
            sectorFocus: [
              { id: "sf", band: "HOLD", text: "Deposit-funded Asian banks." },
            ],
            holdingsCheck: [],
            activatedAt: null,
          },
        ]}
      />,
    );
    // `sector_focus_label` is the row's sentence, not its band. The band is
    // recovered by matching, so one fact lives in one place.
    expect(screen.getAllByText("HOLD").length).toBeGreaterThan(0);
  });

  it("shows the sentence as written when it matches no known row", () => {
    render(
      <SetupsView
        setups={[setup({ sectorFocusLabel: "A row this strategy does not carry." })]}
        strategies={[]}
      />,
    );
    expect(
      screen.getByText("A row this strategy does not carry."),
    ).toBeInTheDocument();
  });

  it("names the series a scored setup's levels are quoted on", () => {
    render(<SetupsView setups={[setup()]} strategies={[]} />);
    // Equity setups are quoted against a cash index and crypto setups against
    // BTC or ETH, so the mapping cannot be inferred from the book.
    expect(screen.getByText("S&P 500 (cash)")).toBeInTheDocument();
  });

  it("does not read a band touch off a setup nobody scored", () => {
    // The flag was never computed, so it is not evidence of anything.
    expect(
      gateRefusedATouch(
        setup({
          seriesKey: null,
          gateState: "unevaluable",
          bandTouchedToday: true,
        }),
      ),
    ).toBe(false);
    expect(isUnscored(setup({ seriesKey: null }))).toBe(true);
    expect(isUnscored(setup())).toBe(false);
  });
});

describe("a gate that refused a touched band surfaces the same day", () => {
  it("marks the row and banners the count", () => {
    render(
      <SetupsView
        setups={[
          setup({
            gateState: "blocked",
            bandTouchedToday: true,
            bandTouchedClose: true,
            gateReason: "Breadth printed 44.10% on the entry close.",
          }),
        ]}
        strategies={[]}
      />,
    );
    expect(screen.getByText("band touched · gate refused")).toBeInTheDocument();
    expect(
      screen.getByText(/One setup had the entry band reached this session/),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Breadth printed 44.10% on the entry close."),
    ).toBeInTheDocument();
  });

  it("does not mark a blocked setup whose band was never reached", () => {
    expect(gateRefusedATouch(setup({ gateState: "blocked" }))).toBe(false);
    expect(
      gateRefusedATouch(
        setup({ gateState: "blocked", bandTouchedToday: true }),
      ),
    ).toBe(true);
    // An entry that actually traded is not a refusal.
    expect(
      gateRefusedATouch(
        setup({
          gateState: "blocked",
          bandTouchedToday: true,
          entryState: "open",
        }),
      ),
    ).toBe(false);
  });

  it("says which side of the invalidation ends the trade, or that nobody said", () => {
    const { rerender } = render(
      <SetupsView setups={[setup()]} strategies={[]} />,
    );
    expect(screen.getByText("close below")).toBeInTheDocument();
    rerender(
      <SetupsView setups={[setup({ invalidationDir: null })]} strategies={[]} />,
    );
    expect(screen.getByText("direction not stated")).toBeInTheDocument();
  });

  it("shows the gate clause as written, so a refusal can be checked", () => {
    render(
      <SetupsView
        setups={[setup({ gateState: "blocked" })]}
        strategies={[]}
      />,
    );
    expect(
      screen.getByText(/gate: HY under 292 bps and breadth not below 45%/),
    ).toBeInTheDocument();
  });
});

describe("envelope and row failures stay separate", () => {
  it("shows nothing at all when the query itself failed", () => {
    const reading = readingOfStates([], {
      transportError: "Reading macro_mart.snapshot_row failed [42501].",
      answered: true,
    });
    expect(reading.hasData).toBe(false);
    expect(reading.state).toBe("failed");
    expect(reading.banner?.severity).toBe("fault");
  });

  it("keeps the valid neighbours and counts the bad row", () => {
    const reading = readingOf(
      [SNAPSHOT_WIRE, { ...SNAPSHOT_WIRE, freshness: "somewhat fresh" }],
      snapshotRow,
    );
    expect(reading.rows).toHaveLength(1);
    expect(reading.rejected).toHaveLength(1);
    expect(reading.rejected[0]).toContain("freshness must be one of");
    expect(reading.transportError).toBeNull();
  });

  it("keeps the figures it holds when the fault arrives mid-session", () => {
    const reading = readingOfStates(["fresh", "fresh"], {
      transportError: "The browser could not reach the project.",
    });
    expect(reading.hasData).toBe(true);
    expect(reading.dimmed).toBe(true);
    expect(reading.banner?.text).toContain("last ones this session received");
  });

  it("names the network rather than blaming the worker when a fetch throws", async () => {
    const source = supabaseSource(
      fakeClient({
        select: () => Promise.reject(new Error("Failed to fetch")),
      }),
    );
    const reading = await source.martTable("snapshot_row", snapshotRow);
    expect(reading.transportError).toContain("threw before it answered");
    expect(reading.transportError).toContain("unable to reach the Supabase");
    expect(reading.rows).toHaveLength(0);
  });

  it("renders a PostgREST refusal as a sentence carrying its code", async () => {
    const source = supabaseSource(
      fakeClient({
        select: () =>
          Promise.resolve({
            data: null,
            error: {
              message: "Could not find the table 'macro_mart.snapshot_row'",
              code: "PGRST205",
              hint: "Add macro_mart to the exposed schemas.",
            },
          }),
      }),
    );
    const reading = await source.martTable("snapshot_row", snapshotRow);
    expect(reading.transportError).toContain("[PGRST205]");
    expect(reading.transportError).toContain("exposed schemas");
  });
});

describe("a relation the contract does not define yet", () => {
  it("degrades one section instead of failing the route", async () => {
    const source = supabaseSource(
      fakeClient({
        select: () =>
          Promise.resolve({
            data: null,
            error: {
              message: "Could not find the table 'macro_mart.week_grade'",
              code: "PGRST205",
            },
          }),
      }),
    );
    const reading = await source.optionalTable("week_grade", snapshotRow);
    expect(reading.transportError).toBeNull();
    expect(reading.rows).toHaveLength(0);
  });

  it("does not swallow a permission error dressed as a missing relation", async () => {
    const source = supabaseSource(
      fakeClient({
        select: () =>
          Promise.resolve({
            data: null,
            error: {
              message: "permission denied for table week_grade",
              code: "42501",
            },
          }),
      }),
    );
    const reading = await source.optionalTable("week_grade", snapshotRow);
    expect(reading.transportError).toContain("[42501]");
  });

  it("tells a missing relation from an expired session", () => {
    expect(isMissingRelation({ message: "x", code: "PGRST205" })).toBe(true);
    expect(isMissingRelation({ message: "x", code: "42501" })).toBe(false);
    expect(isExpiredSession({ message: "x", code: "PGRST301" })).toBe(true);
    expect(isExpiredSession({ message: "permission denied", code: "42501" })).toBe(
      false,
    );
  });

  it("names both missing environment variables on a route that has no project", () => {
    // Unconfigured is not the same as empty. A calibration page that printed
    // an empty Scorecard Ledger here would read as a perfect record.
    render(
      <Providers>
        <CalibrationPanel />
      </Providers>,
    );
    // Exactly once. The empty state owns the sentence, above a facts row
    // naming the relations; the banner is suppressed rather than repeating it,
    // because one sentence on two surfaces reads as two faults.
    expect(screen.getAllByText(NOT_CONFIGURED)).toHaveLength(1);
    expect(screen.queryByText("Scorecard Ledger")).not.toBeInTheDocument();
  });
});

describe("the seeded calibration row is not a missing one", () => {
  it("accepts the row migration 0010 seeds, with every measured column null", () => {
    const checked = readSingleCalibration([
      {
        id: 1,
        graded_weeks: null,
        base_hit_score: null,
        base_materialized: null,
        mean_brier: null,
        brier_weeks: null,
        base_was_s2: null,
        window: 8,
        computed_at: null,
      },
    ]);
    // A migrated project whose worker has not graded a Monday yet. Rejecting
    // this row would make the panel report that the migrations had not run,
    // which is the opposite of the truth and sends the operator to the wrong
    // place entirely.
    expect(checked.ok).toBe(true);
    if (!checked.ok) return;
    expect(checked.value.gradedWeeks).toBeNull();
    expect(checked.value.window).toBe(8);
  });

  it("still tells an empty table from a seeded one", () => {
    const empty = readSingleCalibration([]);
    expect(empty.ok).toBe(false);
    expect(empty.ok === false && empty.reason).toContain(
      "migrations have not been applied",
    );
  });
});

describe("the two ledgers tolerate what the schema permits", () => {
  it("accepts a migrated week with no grading moment", () => {
    const checked = weekGrade({
      week: "2026-W31",
      base_scenario_id: null,
      base_scenario_label: "S2 · Hold with hawkish framing (40%)",
      base_grade: "Partial",
      other_grades: [{ id: "S1", prob: 30, grade: "Partial", note: null }],
      eq_call: "valid",
      crypto_call: "valid",
      key_miss: null,
      targets_hit: null,
      targets_n: null,
      brier: null,
      premise_moved: false,
      // Null for the nine migrated weeks. A derived Monday would be a
      // manufactured fact.
      graded_at: null,
    });
    expect(checked.ok).toBe(true);
    if (!checked.ok) return;
    expect(checked.value.gradedAt).toBeNull();
    expect(checked.value.otherGrades[0]?.probability).toBe(30);
  });

  it("reads other_grades as the array it is, not as an object", () => {
    const checked = weekGrade({
      week: "2026-W38",
      base_scenario_id: null,
      base_scenario_label: "S1 (45%)",
      base_grade: "Partial",
      other_grades: [
        { id: "S2", prob: 30, grade: "Partial", note: "four bands landed here" },
        { id: "S3", prob: 25, grade: "Not", note: null },
        // Skipped rather than fatal: losing a graded week from the calibration
        // record over a malformed note would change a number the whole product
        // is calibrated against.
        { id: "S4", grade: "Nonsense" },
      ],
      eq_call: null,
      crypto_call: null,
      key_miss: null,
      targets_hit: 7,
      targets_n: 11,
      brier: "0.105",
      premise_moved: false,
      graded_at: "2026-09-21T01:30:00.000Z",
    });
    expect(checked.ok).toBe(true);
    if (!checked.ok) return;
    expect(checked.value.otherGrades.map((g) => g.id)).toEqual(["S2", "S3"]);
    expect(checked.value.brier).toBe(0.105);
  });
});

describe("a holding's band is resolved, never copied", () => {
  const FOCUS = [
    { id: "sf-1", band: "CRITICAL" as const, text: "Memory and HBM." },
    { id: "sf-2", band: "HOLD" as const, text: "Deposit-funded Asian banks." },
  ];

  it("resolves through the sibling sector-focus rows", () => {
    const band = resolveFocusBand(
      { id: "h1", ticker: "MU", action: "keep", text: "", sectorFocusId: "sf-1" },
      FOCUS,
    );
    expect(band?.band).toBe("CRITICAL");
  });

  it("returns null for a holding no row supports, which is the finding", () => {
    expect(
      resolveFocusBand(
        { id: "h2", ticker: "012330", action: "trim", text: "", sectorFocusId: null },
        FOCUS,
      ),
    ).toBeNull();
  });

  it("does not invent a band for an id the strategy does not carry", () => {
    expect(
      resolveFocusBand(
        { id: "h3", ticker: "X", action: "keep", text: "", sectorFocusId: "sf-9" },
        FOCUS,
      ),
    ).toBeNull();
  });
});

describe("a proposal was never believed", () => {
  it("is not in force on any day while its valid_from is null", () => {
    // A row still only proposed has no validity interval. Treating null as
    // "since the beginning of time" would date a belief to before it was held.
    expect(inForceOn({ validFrom: null, validTo: null }, "2026-09-21")).toBe(
      false,
    );
    expect(
      inForceOn({ validFrom: "2026-09-21T01:30:00.000Z", validTo: null }, "2026-09-21"),
    ).toBe(true);
  });
});

describe("a past probability is read, never attributed", () => {
  it("carries each scenario's own probability on the day it was in force", () => {
    const rows = fixtureSnapshot("clean").scenarioHistory as Record<
      string,
      unknown
    >[];
    const w38 = rows.filter((row) => row["week"] === "2026-W38");
    const w39 = rows.filter((row) => row["week"] === "2026-W39");
    expect(w38).toHaveLength(3);
    expect(w39).toHaveLength(3);
    // S2 was 36% in W38 and 32% in W39. Rendering today's under a past date
    // would be misattribution rather than staleness: a probability the
    // operator never wrote, printed under a day they did not write it.
    const at = (rs: Record<string, unknown>[], n: number) =>
      rs.find((r) => r["ordinal"] === n)?.["probability"];
    expect(at(w38, 2)).toBe(36);
    expect(at(w39, 2)).toBe(32);
  });

  it("closes each superseded set's interval at the next one's activation", () => {
    const reading = readingOf(
      fixtureSnapshot("clean").scenarioHistory,
      scenarioHistory,
    );
    expect(reading.rejected).toEqual([]);
    const w38 = reading.rows.filter((row) => row.week === "2026-W38");
    const w39 = reading.rows.filter((row) => row.week === "2026-W39");
    for (const row of w38) {
      expect(inForceOn(row, "2026-09-18")).toBe(true);
      expect(inForceOn(row, "2026-09-21")).toBe(false);
    }
    for (const row of w39) {
      expect(inForceOn(row, "2026-09-18")).toBe(false);
      expect(inForceOn(row, "2026-09-21")).toBe(true);
    }
  });

  it("keeps the amendment that re-priced one of them", () => {
    const reading = readingOf(
      fixtureSnapshot("clean").scenarioHistory,
      scenarioHistory,
    );
    const amended = reading.rows.filter((row) => row.amendReason !== null);
    expect(amended).toHaveLength(1);
    expect(amended[0]?.amendReason).toContain("record 5.42%");
    // A probability that moved with no stated reason is the over-anchoring
    // R2, R4, R5, R6, R10, R11 and R12 were all written to stop.
    expect(amended[0]?.probability).toBe(36);
  });
});

describe("null, never zero", () => {
  it("refuses a magnitude that overflows a double instead of storing Infinity", () => {
    const checked = numeric({ v: "1".repeat(400) }, "v");
    expect(checked.ok).toBe(false);
  });

  it("accepts exponent notation, which is how Postgres may serialise a numeric", () => {
    const checked = numeric({ v: "1e5" }, "v");
    expect(checked.ok && checked.value).toBe(100000);
  });

  it("refuses the literal strings that look like numbers and are not", () => {
    for (const value of ["NaN", "Infinity", "-Infinity", "seven"]) {
      expect(numeric({ v: value }, "v").ok).toBe(false);
    }
  });

  it("prints the dash rather than zero for every absent figure", () => {
    expect(formatLevel(null)).toBe(ABSENT);
    expect(formatDelta(null)).toBe(ABSENT);
    expect(formatOutOf(null, 11)).toBe(ABSENT);
    // And a real zero prints as a zero, unsigned: a flat week has no direction.
    expect(formatDelta(0)).toBe("0.00");
    expect(formatLevel(0)).toBe("0.00");
  });

  it("says the migrations have not run when the calibration table is empty", () => {
    const checked = readSingleCalibration([]);
    expect(checked.ok).toBe(false);
    expect(checked.ok === false && checked.reason).toContain(
      "migrations have not been applied",
    );
  });
});

describe("clock discipline", () => {
  it("reads a stamp from the future as just now, never as a negative age", () => {
    const now = new Date("2026-09-21T13:00:00.000Z");
    const ahead = new Date("2026-09-21T13:00:30.000Z");
    expect(ageLabel(ahead, now)).toBe("just now");
    expect(ageLabel(null, now)).toBe("never");
  });

  it("reads a calendar day as UTC midnight, so it cannot slip a day west of Greenwich", () => {
    const day = parseDay("2026-09-18");
    expect(day?.toISOString()).toBe("2026-09-18T00:00:00.000Z");
    expect(parseDay("2026-09-18T00:00:00Z")).toBeNull();
  });

  it("takes the latest evaluation stamp, so a relation that failed to recompute shows", () => {
    expect(
      latestStamp([
        "2026-09-21T01:30:00.000Z",
        "2026-09-21T13:00:00.000Z",
        null,
      ]),
    ).toBe("2026-09-21T13:00:00.000Z");
    expect(latestStamp([null, "not a stamp"])).toBeNull();
  });

  it("refuses an unparseable stamp at the validator rather than rendering Invalid Date", () => {
    const checked = snapshotRow({ ...SNAPSHOT_WIRE, updated_at: "yesterday" });
    expect(checked.ok).toBe(false);
  });

  it("treats a validity interval as half-open, so one day has exactly one set", () => {
    const row = {
      validFrom: "2026-09-14T01:30:00.000Z",
      validTo: "2026-09-21T01:30:00.000Z",
    };
    expect(inForceOn(row, "2026-09-14")).toBe(true);
    expect(inForceOn(row, "2026-09-20")).toBe(true);
    // The handover day belongs to the incoming set, not to both.
    expect(inForceOn(row, "2026-09-21")).toBe(false);
    expect(inForceOn(row, "2026-09-13")).toBe(false);
    expect(inForceOn({ ...row, validTo: null }, "2030-01-01")).toBe(true);
  });
});

describe("a reading the worker stopped refreshing says so", () => {
  // The last live evaluation before this was written: 22 September, 00:31 SGT.
  const evaluated = new Date("2026-09-22T16:31:00.000Z");

  it("counts no missed run before the next one is due", () => {
    // 09:30 SGT on the 23rd is 01:30 UTC; within its grace nothing is missed.
    expect(missedRuns(evaluated, new Date("2026-09-23T01:45:00.000Z"))).toBe(0);
  });

  it("counts a run as missed once its grace has passed", () => {
    expect(missedRuns(evaluated, new Date("2026-09-23T03:09:00.000Z"))).toBe(1);
  });

  it("counts every run across days, both the 09:30 and the 21:00", () => {
    // 23rd 09:30 and 21:00, 24th 09:30 and 21:00, 25th 09:30.
    expect(missedRuns(evaluated, new Date("2026-09-25T03:00:00.000Z"))).toBe(5);
  });

  it("counts nothing for a reading evaluated just after its run", () => {
    const onTime = new Date("2026-09-23T01:33:00.000Z");
    expect(missedRuns(onTime, new Date("2026-09-23T12:00:00.000Z"))).toBe(0);
    expect(missedRuns(null, new Date("2026-09-23T12:00:00.000Z"))).toBe(0);
  });

  it("will not show a green evaluated chip over a missed run", () => {
    render(<ReadingBadge hasData missed={2} stamp="00:31" state="fresh" />);
    expect(screen.getByText("STALE")).toBeInTheDocument();
    expect(screen.queryByText(/EVALUATED/)).toBeNull();
  });

  it("keeps the evaluated chip while the schedule is being kept", () => {
    render(<ReadingBadge hasData missed={0} stamp="09:30" state="fresh" />);
    expect(screen.getByText("EVALUATED 09:30")).toBeInTheDocument();
  });
});

describe("an empty database", () => {
  it("renders no children and names the missing input", () => {
    const reading = readingOfStates([], { answered: true });
    expect(reading.hasData).toBe(false);
    render(
      <Panel
        empty={
          <PanelEmpty
            reading={reading}
            relations={["macro_mart.snapshot_row"]}
            schedule="the worker, at 09:30 and 21:00 Asia/Singapore."
          />
        }
        reading={reading}
        title="The reading"
      >
        <table>
          <tbody>
            <tr>
              <td>0</td>
            </tr>
          </tbody>
        </table>
      </Panel>,
    );
    // Structural: the children are never rendered, so there is no zero to read.
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(
      screen.getByText(/has not completed a pass against this project/),
    ).toBeInTheDocument();
    expect(screen.getByText("macro_mart.snapshot_row")).toBeInTheDocument();
    expect(
      screen.getByText(/09:30 and 21:00 Asia\/Singapore/),
    ).toBeInTheDocument();
  });

  it("says so plainly when no phase call is on record", () => {
    render(<PhaseView calls={[]} conditions={[]} scores={[]} />);
    // Named in the operator's own terms, not the database's. A reader who is
    // told a relation is empty has been handed the engineer's half of the
    // sentence and left to work out the half that concerns them.
    expect(screen.getByText(/No phase call yet/)).toBeInTheDocument();
  });

  it("names the absence when a phase call has a score but no conditions", () => {
    render(<PhaseView calls={[]} conditions={[]} scores={[SCORE]} />);
    expect(
      screen.getByText(/No conditions are on record for this call/),
    ).toBeInTheDocument();
  });

  it("names the missing env vars rather than failing to connect", () => {
    expect(NOT_CONFIGURED).toContain("NEXT_PUBLIC_SUPABASE_URL");
    expect(NOT_CONFIGURED).toContain("NEXT_PUBLIC_SUPABASE_ANON_KEY");
    expect(NOT_CONFIGURED).toContain("NEXT_PUBLIC_FIXTURES=1");
  });
});

describe("a panel that throws while rendering", () => {
  function Boom(): never {
    throw new Error("render exploded");
  }

  it("degrades one panel and states that nothing from it is shown", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <>
        <PanelBoundary title="Snapshot">
          <Boom />
        </PanelBoundary>
        <p>The other panel</p>
      </>,
    );
    expect(screen.getByText(/Snapshot failed to render/)).toBeInTheDocument();
    expect(screen.getByText(/nothing from this one is being shown/)).toBeInTheDocument();
    expect(screen.getByText("The other panel")).toBeInTheDocument();
    spy.mockRestore();
  });
});

describe("the fixtures travel the same road a live row does", () => {
  it("validates every row of both datasets through the real validators", () => {
    for (const dataset of ["clean", "honest"] as const) {
      const snap = fixtureSnapshot(dataset);
      const rows = readingOf(snap.snapshotRows, snapshotRow);
      expect(rows.rejected).toEqual([]);
      expect(rows.rows.length).toBe(47);
      const scenarios = readingOf(snap.scenarios, scenarioState);
      expect(scenarios.rejected).toEqual([]);
      expect(
        scenarios.rows.reduce((sum, s) => sum + s.probability, 0),
      ).toBe(100);
    }
  });

  it("holds every ordinal exactly once inside each group", () => {
    const rows = readingOf(
      fixtureSnapshot("clean").snapshotRows,
      snapshotRow,
    ).rows;
    const byGroup = new Map<string, number[]>();
    for (const row of rows) {
      const list = byGroup.get(row.tableGroup) ?? [];
      list.push(row.ordinal);
      byGroup.set(row.tableGroup, list);
    }
    for (const [group, ordinals] of byGroup) {
      const sorted = [...ordinals].sort((a, b) => a - b);
      expect(sorted, group).toEqual(
        Array.from({ length: ordinals.length }, (_, i) => i + 1),
      );
    }
    expect(byGroup.get("Price & Levels")).toHaveLength(19);
    expect(byGroup.get("Sentiment & Positioning")).toHaveLength(12);
    expect(byGroup.get("Flows & Structure")).toHaveLength(11);
    expect(byGroup.get("Macro & Fiscal State")).toHaveLength(5);
  });

  it("exercises every one of the seven freshness states across the two datasets", () => {
    const seen = new Set<string>();
    for (const dataset of ["clean", "honest"] as const) {
      for (const row of readingOf(
        fixtureSnapshot(dataset).snapshotRows,
        snapshotRow,
      ).rows) {
        seen.add(row.freshness);
      }
    }
    for (const state of [
      "fresh",
      "due",
      "overdue",
      "gap",
      "asserted",
      "not_connected",
    ]) {
      expect(seen, state).toContain(state);
    }
  });

  it("carries two honest as-ofs side by side in one sentiment table", () => {
    const rows = readingOf(
      fixtureSnapshot("clean").snapshotRows,
      snapshotRow,
    ).rows;
    const cnn = rows.find((r) => r.key === "cnn_fear_greed")!;
    const crypto = rows.find((r) => r.key === "crypto_fear_greed")!;
    expect(cnn.tableGroup).toBe(crypto.tableGroup);
    expect(cnn.asof).not.toBe(crypto.asof);
    // Both fresh: one is fetched on the 09:30 run only and one is a calendar
    // day series, and neither is late.
    expect(cnn.freshness).toBe("fresh");
    expect(crypto.freshness).toBe("fresh");
  });

  it("puts each cash index directly after its future, by ordinal", () => {
    const rows = readingOf(
      fixtureSnapshot("clean").snapshotRows,
      snapshotRow,
    ).rows
      .filter((r) => r.tableGroup === "Price & Levels")
      .sort((a, b) => a.ordinal - b.ordinal);
    for (let i = 0; i < rows.length - 1; i += 1) {
      const key = rows[i]!.key;
      if (!key.endsWith("_front")) continue;
      expect(rows[i + 1]!.key).toBe(`${key.slice(0, -6)}_cash`);
    }
  });

  it("keeps a numeric with a unit the renderer would get wrong as the worker's display", () => {
    const rows = readingOf(
      fixtureSnapshot("clean").snapshotRows,
      snapshotRow,
    ).rows;
    const liquidity = rows.find((r) => r.key === "net_liquidity")!;
    // Stored in USD with zero decimals, so the raw value is thirteen digits.
    expect(liquidity.value).toBeGreaterThan(1e12);
    expect(liquidity.display).toBe("$5.755T");
    render(<SnapshotView gaps={[]} rows={[liquidity]} />);
    expect(screen.getByText("$5.755T")).toBeInTheDocument();
    expect(screen.queryByText("5,754,716,000,000")).not.toBeInTheDocument();
  });
});

describe("a delta is stated in the unit its value is stated in", () => {
  /**
   * The defect this describes, as the audit found it on screen.
   *
   * The worker computes every delta as a plain difference of two observations
   * of one series, so a delta is in that series' canonical unit: USD with zero
   * decimals for net liquidity, percentage points for a yield. The table
   * printed the raw figure. `-27,855,000,000` sat beside `$5.864T` in the next
   * column, and the reader had to count the digits to learn that the balance
   * sheet had lost twenty-eight billion dollars. A column that needs counting
   * is not a column.
   */
  it("reads a dollar delta at the magnitude its value is quoted at", () => {
    // Net liquidity: a trillion-dollar balance, a billion-dollar move. The
    // suffix steps down from the value's own, because `-$0.03T` has been
    // rounded past the point of saying anything.
    expect(formatMoney(-27_855_000_000, 5_864_000_000_000)).toBe("-$27.9B");
    // Stablecoin supply: a billion-dollar cap and a move under a billion, so
    // the value's own suffix holds and the figure keeps two decimals.
    expect(formatMoney(933_268_897, 310_914_460_984)).toBe("+$0.93B");
    expect(formatMoney(-3_203_665_075, 146_950_000_000)).toBe("-$3.2B");
    expect(formatMoney(468_930_000, 6_210_000)).toBe("+$469M");
    // Below a million nothing is suffixed, and the precision is the value's:
    // a $3,963.88 move in an $85,456 price is not quoted to the cent.
    expect(formatMoney(3963.88, 85_456)).toBe("+$3,964");
  });

  it("prints the dash for an absent delta rather than a dollar zero", () => {
    expect(formatMoney(null)).toBe(ABSENT);
    expect(formatBasisPoints(null, "percent")).toBe(ABSENT);
    expect(formatSeriesDelta(null, "usd", "WoW", 1)).toBe(ABSENT);
  });

  it("states a yield move in basis points, which is how one is quoted", () => {
    expect(formatBasisPoints(0.04, "percent")).toBe("+4 bp");
    // A percent that is a share moves in points; only a yield reads in bp.
    expect(formatSeriesDelta(0.99, "percent", "1d", 50.49)).toBe("+0.99 pp");
    expect(formatSeriesDelta(-0.6, "percent", "MoM", 57.4)).toBe("-0.60 pp");
    expect(formatSeriesDelta(-0.05, "yield_percent", "1d", 4.96)).toBe("-5 bp");
    expect(deltaUnit({ key: "ust_10y", unit: "percent" })).toBe(
      "yield_percent",
    );
    expect(deltaUnit({ key: "auction_high_yield_2y", unit: "percent" })).toBe(
      "yield_percent",
    );
    expect(
      deltaUnit({ key: "breadth_spx_above_200dma", unit: "percent" }),
    ).toBe("percent");
    expect(
      deltaUnit({ key: "state_street_equity_allocation", unit: "percent" }),
    ).toBe("percent");
    // Fourteen Yahoo symbols are one vendor in the health line.
    expect(sourceFamily("Yahoo chart API (^DJI)")).toBe("Yahoo");
    expect(sourceFamily("Yahoo chart API (ES=F)")).toBe("Yahoo");
    expect(sourceFamily("FRED")).toBe("FRED");
    expect(formatBasisPoints(-0.01, "percent")).toBe("-1 bp");
    // Under a basis point still has to be visible: a half-point move is a
    // move, and rounding it to zero would print a flat day.
    expect(formatBasisPoints(0.004, "percent")).toBe("+0.4 bp");
    expect(formatBasisPoints(0, "percent")).toBe("0 bp");
    // A spread is stored in basis points already and is not multiplied again.
    expect(formatBasisPoints(-2, "bps")).toBe("-2 bp");
  });

  it("trusts a label that names its own unit over the series' unit", () => {
    // `macro.series.delta_native_label` is a bare cadence and the delta beside
    // it is a raw difference. A label that names a unit is a different claim,
    // and re-reading a percentage as dollars would be a fabricated figure.
    expect(labelNamesUnit("WoW")).toBe(false);
    expect(labelNamesUnit("1d")).toBe(false);
    expect(labelNamesUnit(null)).toBe(false);
    for (const label of ["1d %", "WoW pp", "1d pts", "1d bps"]) {
      expect(labelNamesUnit(label), label).toBe(true);
    }
    expect(formatSeriesDelta(-2.33, "usd", "WoW %", 5_864_000_000_000)).toBe(
      "-2.33",
    );
    expect(formatSeriesDelta(-2.33, "usd", "WoW", 5_864_000_000_000)).toBe(
      "-$2.33",
    );
  });

  it("leaves an index, a 0-100 index and a percent of open interest alone", () => {
    // Each is already quoted in the unit its column is read in. Only the units
    // whose raw figure is unreadable are reformatted.
    expect(formatSeriesDelta(-7.06, "pct_of_oi", "WoW", 36.98)).toBe("-7.06");
    expect(formatSeriesDelta(-0.63, "index_points", "1d", 14.81)).toBe("-0.63");
    expect(formatSeriesDelta(3, "index_0_100", "1d", 70)).toBe("+3.00");
    // A count of trading sessions has no decimals to give.
    expect(formatSeriesDelta(6, "sessions", "WoW", 2)).toBe("+6");
  });

  it("renders the flow rows in dollars rather than in digits", () => {
    const rows = readingOf(
      fixtureSnapshot("clean").snapshotRows,
      snapshotRow,
    ).rows.filter((row) => row.tableGroup === "Flows & Structure");
    render(<SnapshotView gaps={[]} rows={rows} />);
    const flow = screen.getByText("BTC ETF weekly flow").closest("tr")!;
    // The defect, and its absence: the raw figure the column used to carry.
    expect(flow.textContent).not.toContain("468,900,000");
    expect(flow.textContent).toContain("+$469M");
    const eth = screen.getByText("ETH ETF weekly flow").closest("tr")!;
    expect(eth.textContent).toContain("-$338M");
  });
});

describe("a value cell carries one figure", () => {
  it("keeps the leading figure and hands the rest to the disclosure", () => {
    expect(leadFigure("$156.79B proxy; 1.835M BTC")).toBe("$156.79B proxy");
    expect(leadFigure("~$514M; $56M longs / $458M shorts")).toBe("~$514M");
    // A single figure is untouched, and an absent display stays absent.
    expect(leadFigure("$5.755T")).toBe("$5.755T");
    expect(leadFigure(null)).toBeNull();
  });

  it("shows the figures it held back once the row is opened", async () => {
    const user = userEvent.setup();
    render(
      <Providers>
        <SnapshotView
          gaps={[]}
          rows={[
            snapshot({
              key: "liquidations_24h",
              series_id: "ser-liq",
              label: "24h Liquidations",
              display: "~$514M; $56M longs / $458M shorts",
            }),
          ]}
        />
      </Providers>,
    );
    const row = screen.getByText("24h Liquidations").closest("tr")!;
    expect(row.textContent).toContain("~$514M");
    expect(row.textContent).not.toContain("longs");
    await user.click(screen.getByRole("button", { expanded: false }));
    // Nothing the worker wrote is thrown away; it is one click down.
    expect(
      await screen.findByText("$56M longs / $458M shorts"),
    ).toBeInTheDocument();
  });
});

describe("the trend column is one read for the page", () => {
  it("reads every series' window in a single query rather than one per row", async () => {
    const source = fixtureSource("clean");
    const since = daysBefore("2026-09-21", 30);
    expect(since).toBe("2026-08-22");
    const window_ = await fetchRecentObservations(source, since);
    const all = readingOf(
      fixtureSnapshot("clean").observations,
      observationHistory,
    );
    // One read, many series, and bounded by the window rather than by the
    // history: the cost does not grow as the arcs lengthen.
    expect(
      new Set(window_.rows.map((row) => row.seriesKey)).size,
    ).toBeGreaterThan(5);
    expect(window_.rows.every((row) => row.asof >= since)).toBe(true);
    expect(window_.rows.length).toBeLessThanOrEqual(all.rows.length);
  });

  it("narrows to the window and drops everything before it", async () => {
    const source = fixtureSource("clean");
    const narrow = await fetchRecentObservations(source, "2026-09-17");
    expect(narrow.rows.length).toBeGreaterThan(0);
    expect(narrow.rows.some((row) => row.asof === "2026-09-14")).toBe(false);
  });

  it("draws nothing across fewer than two points", () => {
    const rows = readingOf(
      fixtureSnapshot("clean").snapshotRows,
      snapshotRow,
    ).rows.filter((row) => row.key === "vix");
    const history = readingOf(
      fixtureSnapshot("clean").observations,
      observationHistory,
    ).rows;
    const one = history.filter(
      (row) => row.seriesKey === "vix" && row.asof === "2026-09-18",
    );
    const { container: single } = render(
      <SnapshotView gaps={[]} rows={rows} trends={one} />,
    );
    // A one-point line is not a trend, at any size. An empty cell is the
    // honest rendering of a series we hold one print of.
    expect(single.querySelector(".spark")).toBeNull();

    const { container: many } = render(
      <SnapshotView
        gaps={[]}
        rows={rows}
        trends={history.filter((row) => row.seriesKey === "vix")}
      />,
    );
    expect(many.querySelector(".spark")).not.toBeNull();
  });

  it("marks the rows whose as-of is the evaluation date and no others", () => {
    const rows = readingOf(
      fixtureSnapshot("clean").snapshotRows,
      snapshotRow,
    ).rows.filter((row) => row.tableGroup === "Price & Levels");
    const { container } = render(<SnapshotView gaps={[]} rows={rows} />);
    const labelOf = (key: string) => rows.find((row) => row.key === key)!.label;
    // BTC is a calendar-day series dated Monday; gold closed on Friday. Both
    // are current, and only one moved in this reading.
    expect(screen.getByText(labelOf("btc")).closest("tr")!.className).toContain(
      "row-moved",
    );
    expect(
      screen.getByText(labelOf("gold")).closest("tr")!.className,
    ).not.toContain("row-moved");
    expect(container.querySelectorAll("tr.row-moved").length).toBeGreaterThan(
      0,
    );
  });
});

describe("a standing state is a sentence, not a figure with empty columns", () => {
  it("renders the macro states as a list with no numeric columns", () => {
    const rows = readingOf(
      fixtureSnapshot("clean").snapshotRows,
      snapshotRow,
    ).rows.filter((row) => row.tableGroup === "Macro & Fiscal State");
    const { container } = render(<SnapshotView gaps={[]} rows={rows} />);
    expect(rows.length).toBeGreaterThan(0);
    // No table at all: four columns of nothing is a table shape that does not
    // fit what it holds.
    expect(container.querySelector("table")).toBeNull();
    expect(container.querySelectorAll(".standing-row")).toHaveLength(
      rows.length,
    );
    expect(
      screen.getByText(rows.find((row) => row.key === "fed_funds")!.label),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/3.75%–4.00% after a unanimous/),
    ).toBeInTheDocument();
    // The footnote counts the rows it is describing rather than asserting a
    // number that was typed once and never read again.
    expect(
      screen.getByText(new RegExp(`These ${rows.length} rows are written statements`)),
    ).toBeInTheDocument();
    // And no literal backticks, which is what a Markdown habit leaves in JSX.
    expect(container.textContent).not.toContain("`display`");
  });
});

describe("the snapshot's headers say what the column is", () => {
  it("names the delta, the anchor and the next print without a stale count", () => {
    const rows = readingOf(
      fixtureSnapshot("clean").snapshotRows,
      snapshotRow,
    ).rows.filter((row) => row.tableGroup === "Sentiment & Positioning");
    const { container } = render(<SnapshotView gaps={[]} rows={rows} />);
    const head = container.querySelector("thead")!.textContent!;
    expect(head).toContain("Δ");
    expect(head).not.toContain("Δ native");
    expect(head).toContain("Since read");
    expect(head).toContain("Next print");
    expect(head).toContain("30d");
    // The group heading is the heading. A row count beside it is a number the
    // reader has to reconcile against the rows they can already see.
    const heading = container.querySelector(".snapshot-group-head")!;
    expect(heading.textContent).toBe("Sentiment & Positioning");
    // The footnote states no count of release rules: the contract names five
    // cadences and the provenance footer names seven venues, and a third
    // number in a third place is one more thing that can be wrong.
    expect(container.textContent).not.toContain("six release rules");
    // `rel 16 Sept` was an abbreviation of a word that had room to be itself.
    expect(container.textContent).toContain("released 18 Sep");
    expect(container.textContent).not.toContain("rel 18 Sep");
  });
});

/* ------------------------------------- the history and calibration screens */

describe("a stored scenario set is not a malformed one", () => {
  const row = (swing: unknown) => ({
    id: "set-w39",
    week: "2026-W39",
    synthesis: null,
    probability_check: null,
    swing_variables: swing,
    rules_waived: [],
    status: "active",
    authored_at: "2026-09-21T01:30:00.000Z",
    valid_from: "2026-09-21T01:30:00.000Z",
    valid_to: null,
    amends: null,
    amend_reason: null,
  });

  // `macro_core.scenario_set.swing_variables` is `jsonb not null default
  // '[]'::jsonb` and every stored set holds an array of {name, cool, hot}.
  // Reading it as an object rejected every week in the judgment record and
  // emptied /history behind a banner that blamed the data.
  const ARRAY_SHAPE = [
    {
      name: "The 2Y/5Y/7Y cluster",
      cool: "clears near where-issued",
      hot: "a second record tail",
    },
  ];

  it("accepts the array the column actually stores", () => {
    const checked = scenarioSetHistory(row(ARRAY_SHAPE));
    expect(checked.ok).toBe(true);
  });

  it("reads the variables out of the array, not only out of a wrapper", () => {
    expect(readSwingVariables(ARRAY_SHAPE)).toHaveLength(1);
    expect(readSwingVariables({ variables: ARRAY_SHAPE })).toHaveLength(1);
    expect(readSwingVariables(ARRAY_SHAPE)[0]?.hot).toBe(
      "a second record tail",
    );
  });

  it("still refuses a scalar, which is no shape at all", () => {
    const checked = scenarioSetHistory(row(7));
    expect(checked.ok).toBe(false);
  });

  it("skips an entry missing a leg rather than dropping the week", () => {
    expect(readSwingVariables([{ name: "x" }, ...ARRAY_SHAPE])).toHaveLength(1);
    expect(readSwingVariables(null)).toHaveLength(0);
  });

  it("states a hidden-week banner in one line, not one line per row", () => {
    const rejected = Array.from(
      { length: 7 },
      (_, i) =>
        `row ${i}: swing_variables must be a json object or null, got an array`,
    );
    const line = hiddenWeeksLine(rejected);
    expect(line).toBe(
      "7 weeks hidden: swing_variables must be a json object or null",
    );
    // The detail is not in the banner. It is what the disclosure renders.
    expect(line).not.toContain("row 0");
    expect(hiddenWeeksLine(["row 0: week must be a YYYY-Www week label"])).toBe(
      "1 week hidden: week must be a YYYY-Www week label",
    );
    expect(
      hiddenWeeksLine(["row 0: week is bad", "row 1: brier is bad"]),
    ).toContain("2 different reasons");
  });
});

describe("the calibration read leads with what happened", () => {
  const ROW = {
    id: 1,
    gradedWeeks: 16,
    baseHitScore: 4.5,
    baseMaterialized: 1,
    meanBrier: 0.268,
    brierWeeks: 3,
    baseWasS2: 6,
    window: 8,
    computedAt: null,
  };

  it("turns the hit score into the three counts it is made of", () => {
    // 4.5 is Materialized 1 plus Partial 0.5, so 1 full and 7 partial of 8.
    // The operator should not have to invert the arithmetic to read the tile.
    expect(readHitRate(ROW)).toEqual({
      full: 1,
      partial: 7,
      missed: 0,
      window: 8,
    });
  });

  it("withholds the counts rather than guessing when they do not reconstruct", () => {
    expect(readHitRate({ ...ROW, window: 2 })).toBeNull();
    expect(readHitRate({ ...ROW, baseHitScore: null })).toBeNull();
    expect(readHitRate(null)).toBeNull();
  });

  it("states the finding in a sentence above the tiles", () => {
    expect(calibrationLead(ROW)).toBe(
      "The base case has been S2 in 6 of the last 8 weeks and fully materialised once. Mean Brier 0.268 over 3 scored weeks.",
    );
    expect(
      calibrationLead({ ...ROW, baseWasS2: null, meanBrier: null }),
    ).toBeNull();
  });

  it("reads a call's verdict without swallowing the reasoning behind it", () => {
    // Forty words wrapped into a ninety-pixel column made every ledger row
    // 140px tall. The chip is the verdict; the note moves to the disclosure.
    expect(verdictOf("valid - the shelf held into the FOMC").label).toBe(
      "VALID",
    );
    expect(verdictOf("valid but weakening - breadth is rolling").label).toBe(
      "TURNING",
    );
    expect(verdictOf("wrong - the phase call capped it").label).toBe("WRONG");
    expect(verdictOf("advanced - it arrived a week early").label).toBe(
      "ADVANCED",
    );
    // A verdict this reader has not met is a new verdict, not a bad row.
    expect(verdictOf("premature - nothing had confirmed").label).toBe(
      "PREMATURE",
    );
  });
});
