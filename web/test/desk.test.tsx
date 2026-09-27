import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { MacroData } from "@/hooks/useMacro";
import {
  catalystWindow,
  conditionState,
  drift as driftRow,
  runRequest,
  scenarioState,
  scenarioTargetState,
  snapshotRow,
  type ObservationHistory,
  type RunRequest,
  type ScenarioState,
  type ScenarioTargetState,
  type SnapshotRow,
} from "@/lib/contract";
import { fixtureSource, readingOf } from "@/lib/data";
import {
  awaitingDecision,
  belongsOnCatalysts,
  nearestEdges,
  nextSettlers,
  refreshInFlight,
  unusualMoves,
} from "@/lib/desk";
import { fixtureSnapshot } from "@/lib/fixtures";
import { assetName } from "@/lib/conditions";
import { cellFigure } from "@/components/SnapshotView";
import { ScenarioTable } from "@/components/ScenarioTable";

const refreshMutate = vi.fn();
const decideMutate = vi.fn();
vi.mock("@/hooks/useRunQueue", () => ({
  useRunQueue: () => ({
    refresh: { mutate: refreshMutate, isPending: false, data: undefined },
    decide: { mutate: decideMutate, isPending: false, data: undefined },
  }),
}));

// Imported after the mock so the component picks it up.
const { DeskView } = await import("@/components/DeskView");

function rows<T>(
  payload: unknown[],
  validator: Parameters<typeof readingOf<T>>[1],
): T[] {
  return [...readingOf(payload, validator).rows];
}

function honestDesk(): MacroData {
  const fx = fixtureSnapshot("honest");
  return {
    snapshot: rows(fx.snapshotRows, snapshotRow),
    conditions: rows(fx.conditions, conditionState),
    scenarios: rows(fx.scenarios, scenarioState),
    targets: rows(fx.targets, scenarioTargetState),
    catalysts: rows(fx.catalysts, catalystWindow),
    drift: rows(fx.drift, driftRow)[0] ?? null,
    runRequests: rows(fx.runRequests, runRequest).sort((a, b) => b.id - a.id),
  } as unknown as MacroData;
}

const NOW = new Date("2026-09-21T13:05:00.000Z");

/* ------------------------------------------------------------ band edges */

function target(over: Partial<ScenarioTargetState>): ScenarioTargetState {
  return {
    targetId: "t",
    scenarioId: "sc1",
    seriesId: "sid-spx",
    label: "S&P",
    kind: "band",
    comparator: "between",
    lo: 7520,
    hi: 7800,
    current: 7764.64,
    holding: true,
    distanceToBreach: null,
    ...over,
  } as ScenarioTargetState;
}

const SCENARIOS = [
  { scenarioId: "sc1", ordinal: 1 },
  { scenarioId: "sc3", ordinal: 3 },
] as ScenarioState[];

describe("the edges the Desk says are closest to changing", () => {
  it("measures each band to its nearer edge as a share of the level", () => {
    const [edge] = nearestEdges([target({})], SCENARIOS, [
      { seriesId: "sid-spx", key: "spx_cash" } as SnapshotRow,
    ]);
    expect(edge!.side).toBe("ceiling");
    expect(edge!.edge).toBe(7800);
    expect(edge!.pctAway).toBeCloseTo((35.36 / 7764.64) * 100, 3);
    expect(edge!.seriesKey).toBe("spx_cash");
    expect(edge!.holding).toBe(true);
  });

  it("never ranks an unevaluable band or a judgment target", () => {
    expect(
      nearestEdges(
        [
          target({ targetId: "a", holding: null }),
          target({ targetId: "b", kind: "manual", current: null }),
        ],
        SCENARIOS,
        [],
      ),
    ).toEqual([]);
  });

  it("lists at most two edges per scenario, so one leg cannot fill the card", () => {
    const edges = nearestEdges(
      [
        target({
          targetId: "s3a",
          scenarioId: "sc3",
          current: 5.34,
          lo: 5.0,
          hi: 5.28,
          holding: false,
        }),
        target({
          targetId: "s3b",
          scenarioId: "sc3",
          current: 4.76,
          lo: 4.44,
          hi: 4.7,
          holding: false,
        }),
        target({
          targetId: "s3c",
          scenarioId: "sc3",
          current: 14.81,
          lo: 11.5,
          hi: 15,
          holding: true,
        }),
        target({ targetId: "s1", scenarioId: "sc1" }),
      ],
      SCENARIOS,
      [],
    );
    // S1's S&P is 0.46% from its ceiling; S3's three are 1.1%, 1.26% and
    // 1.28% away, and the third is dropped.
    expect(edges.map((e) => e.targetId)).toEqual(["s1", "s3a", "s3b"]);
    expect(edges.filter((e) => e.scenarioOrdinal === 3)).toHaveLength(2);
  });
});

/* ------------------------------------------------------------- settlers */

describe("the dated events that settle a clause", () => {
  it("names the scenarios whose clauses a catalyst is bound to, soonest first", () => {
    const data = honestDesk();
    const settlers = nextSettlers(
      data.catalysts,
      data.conditions,
      data.scenarios,
    );
    expect(settlers[0]!.name).toBe("US 2Y/5Y/7Y Treasury auction cluster");
    expect(settlers[0]!.scenarios).toEqual([1, 2, 3]);
    // The pharma tariff is on the calendar but no clause names it: it belongs
    // to the Catalysts page, not to the list of things that decide the call.
    expect(settlers.some((s) => s.name.startsWith("100% pharma"))).toBe(false);
  });

  it("leaves out anything beyond the window", () => {
    const data = honestDesk();
    expect(
      nextSettlers(data.catalysts, data.conditions, data.scenarios, 5).map(
        (s) => s.daysOut,
      ),
    ).toEqual([1, 3]);
  });
});

/* ------------------------------------------------------------- the queue */

function run(over: Partial<RunRequest>): RunRequest {
  return {
    id: 1,
    kind: "amend",
    reason: "x",
    origin: "operator",
    requestedAt: "2026-09-21T12:00:00.000Z",
    status: "queued",
    claimedAt: null,
    finishedAt: null,
    decidedAt: null,
    decision: null,
    proposed: [],
    summary: null,
    error: null,
    ...over,
  };
}

describe("what the queue asks of the operator", () => {
  it("awaits a decision only on a proposal nobody has decided", () => {
    expect(awaitingDecision([run({ status: "proposed" })])?.id).toBe(1);
    expect(
      awaitingDecision([run({ status: "proposed", decision: "accept" })]),
    ).toBeNull();
    expect(awaitingDecision([run({ status: "done" })])).toBeNull();
  });

  it("holds the Refresh button while a refresh is queued, running or being applied", () => {
    expect(refreshInFlight([run({ status: "queued" })])).not.toBeNull();
    expect(refreshInFlight([run({ status: "running" })])).not.toBeNull();
    expect(
      refreshInFlight([run({ status: "proposed", decision: "reject" })]),
    ).not.toBeNull();
    expect(refreshInFlight([run({ status: "proposed" })])).toBeNull();
    expect(
      refreshInFlight([run({ kind: "narrative", status: "running" })]),
    ).toBeNull();
  });

  it("queues one refresh for a double click and refuses an empty reason", async () => {
    const source = fixtureSource("clean");
    expect(await source.requestRefresh("  ")).toEqual({
      ok: false,
      reason: "a refresh names what changed, in at least a few words",
    });
    await source.requestRefresh("oil broke $95");
    await source.requestRefresh("oil broke $95");
    const queue = (await source.optionalTable("run_request_state", runRequest))
      .rows;
    expect(
      queue.filter((r) => r.kind === "amend" && r.status === "queued"),
    ).toHaveLength(1);
  });

  it("records one decision on a proposal and refuses a second", async () => {
    const source = fixtureSource("honest");
    expect(await source.decideProposal(2, "accept")).toEqual({ ok: true });
    const second = await source.decideProposal(2, "reject");
    expect(second.ok).toBe(false);
    const decided = (
      await source.optionalTable("run_request_state", runRequest)
    ).rows.find((r) => r.id === 2);
    expect(decided?.decision).toBe("accept");
  });
});

/* ------------------------------------------------------- where rows go */

describe("which rows move to the Catalysts page", () => {
  it("takes the auctions and the prose policy rows, and leaves the macro prints on Data", () => {
    const row = (key: string, valueKind: "text" | "numeric") =>
      ({ key, valueKind, tableGroup: "Macro & Fiscal State" }) as SnapshotRow;
    expect(belongsOnCatalysts(row("auction_high_yield_10y", "numeric"))).toBe(
      true,
    );
    expect(belongsOnCatalysts(row("fed_funds", "text"))).toBe(true);
    expect(belongsOnCatalysts(row("cpi_core_mom", "numeric"))).toBe(false);
  });
});

/* ------------------------------------------------------- unusual moves */

function series(key: string, values: number[]): ObservationHistory[] {
  return values.map((value, i) => ({
    seriesId: key,
    seriesKey: key,
    asof: `2026-09-${String(i + 1).padStart(2, "0")}`,
    value,
  })) as ObservationHistory[];
}

describe("the day's unusual moves", () => {
  const quiet = [100, 101, 100, 101, 100, 101, 100, 101, 100, 101];
  it("ranks a move against its own series' daily changes", () => {
    const rowsIn = [
      {
        key: "vix",
        label: "VIX",
        valueKind: "numeric",
        deltaNative: 5,
        deltaNativeLabel: "1d",
        value: 106,
      },
      {
        key: "spx",
        label: "S&P",
        valueKind: "numeric",
        deltaNative: 1,
        deltaNativeLabel: "1d",
        value: 101,
      },
    ] as SnapshotRow[];
    const moved = unusualMoves(rowsIn, [
      ...series("vix", [...quiet, 106]),
      ...series("spx", [...quiet, 100]),
    ]);
    // The S&P's move is its ordinary day, under one sigma, so it is not listed.
    expect(moved.map((m) => m.key)).toEqual(["vix"]);
    expect(moved[0]!.sigma).toBeGreaterThan(1);
  });

  it("lists a folded index pair once, by its cash index", () => {
    const rowsIn = [
      {
        key: "rut_front",
        label: "Russell 2000 (RTY=F)",
        valueKind: "numeric",
        deltaNative: 5,
        deltaNativeLabel: "1d",
        value: 106,
      },
      {
        key: "rut_cash",
        label: "Russell 2000 (cash)",
        valueKind: "numeric",
        deltaNative: 5,
        deltaNativeLabel: "1d",
        value: 106,
      },
    ] as SnapshotRow[];
    const moved = unusualMoves(rowsIn, [
      ...series("rut_front", [...quiet, 106]),
      ...series("rut_cash", [...quiet, 106]),
    ]);
    expect(moved.map((m) => m.key)).toEqual(["rut_cash"]);
  });

  it("does not rank a monthly print or a series with too little history", () => {
    const rowsIn = [
      {
        key: "cpi",
        label: "CPI",
        valueKind: "numeric",
        deltaNative: 0.3,
        deltaNativeLabel: "MoM",
        value: 1,
      },
      {
        key: "new",
        label: "New",
        valueKind: "numeric",
        deltaNative: 9,
        deltaNativeLabel: "1d",
        value: 9,
      },
    ] as SnapshotRow[];
    expect(
      unusualMoves(rowsIn, [
        ...series("cpi", quiet),
        ...series("new", [1, 2, 9]),
      ]),
    ).toEqual([]);
  });
});

/* ---------------------------------------------------------------- Desk */

describe("the Desk", () => {
  it("puts the proposal in front of the operator and sends their decision", async () => {
    const user = userEvent.setup();
    decideMutate.mockClear();
    render(<DeskView data={honestDesk()} now={NOW} />);
    expect(screen.getByText("AWAITING YOU")).toBeInTheDocument();
    expect(
      screen.getByText(/Re-priced S3 from 23% to 18%/),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Accept" }));
    expect(decideMutate).toHaveBeenCalledWith({ id: 2, decision: "accept" });
  });

  it("shows the worker's drift and asks what changed before queueing a refresh", async () => {
    const user = userEvent.setup();
    refreshMutate.mockClear();
    render(<DeskView data={honestDesk()} now={NOW} />);
    // The honest evening: a new acute tail risk, two crypto phase conditions
    // failing (3 each, capped at 10), and the read flagged as possibly stale.
    expect(screen.getByText("10/10")).toBeInTheDocument();
    // The summary says it before the list is opened, as the worker writes it.
    expect(
      screen.getByText(/A refresh is warranted\. This week's crypto phase read may be out of date\./),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Show what changed/ }));
    expect(
      screen.getByText(/crypto phase read may be out of date: 2 of the 4/),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Refresh the call" }));
    const queue = screen.getByRole("button", { name: "Queue the refresh" });
    expect(queue).toBeDisabled();
    await user.type(screen.getByLabelText("What changed?"), "HY through 300");
    await user.click(queue);
    expect(refreshMutate).toHaveBeenCalledWith(
      "HY through 300",
      expect.anything(),
    );
  });

  it("links every ranked edge to its row on Data", () => {
    render(<DeskView data={honestDesk()} now={NOW} />);
    const links = screen.getAllByRole("link", { name: "Data →" });
    expect(links.length).toBeGreaterThan(0);
    for (const link of links) {
      expect(link.getAttribute("href")).toMatch(/^\/data#series-[a-z0-9_]+$/);
    }
  });
});

/* ----------------------------------------------------- target labels */

describe("the asset a target label names", () => {
  it("never cuts a label at a thousands comma", () => {
    // Live W39 labels carry their band. Splitting on a bare comma printed
    // `ETH $2`, `gold $4`, `S&P 7` and `Russell 2` on the Desk.
    expect(assetName("ETH $2,480–$2,980")).toBe("ETH");
    expect(assetName("gold $4,280–$4,620")).toBe("gold");
    expect(assetName("S&P 7,520–7,800")).toBe("S&P");
    expect(assetName("Russell 2,780–2,930")).toBe("Russell");
  });

  it("keeps a tenor as the name and stops at the first figure", () => {
    expect(assetName("10Y 4.90–5.12%")).toBe("10Y");
    expect(assetName("HY 262–292 bps")).toBe("HY");
    expect(assetName("BTC $64K–$76K with the $74K falsifier live again")).toBe(
      "BTC",
    );
    expect(assetName("BTC ETF week positive but under +$700M")).toBe(
      "BTC ETF week positive but under",
    );
  });

  it("still takes the short authored form apart at a colon or a comma", () => {
    expect(assetName("BTC, gated on an inflow week above +$700M")).toBe("BTC");
    expect(assetName("Leadership: Nasdaq beats the Dow again")).toBe(
      "Leadership",
    );
    expect(assetName("VIX")).toBe("VIX");
  });
});

/* ------------------------------------------------ a numeric row's cell */

describe("what a numeric row prints in its Value cell", () => {
  // The first live narrative run, 2026-09-23, wrote these displays.
  it("keeps a short display exactly as written", () => {
    expect(
      cellFigure({ display: "2.66% (266 bps)", value: 2.66, unit: "percent" }),
    ).toBe("2.66% (266 bps)");
    expect(
      cellFigure({ display: "35 (Fear)", value: 35, unit: "index_0_100" }),
    ).toBe("35 (Fear)");
  });

  it("cuts a sentence to the figure it leads with", () => {
    expect(
      cellFigure({
        display:
          "+$1.71B week-to-date (Mon Sep 21 +$998.95M, Tue Sep 22 +$715M), up from +$6.2M the week before",
        value: 1_713_950_000,
        unit: "usd",
      }),
    ).toBe("+$1.71B");
    expect(
      cellFigure({
        display:
          "~$461M over the 24h window read at 15:34 UTC Sep 22 (23:34 SGT): $178M longs",
        value: 461_000_000,
        unit: "usd",
      }),
    ).toBe("~$461M");
    expect(
      cellFigure({
        display: "57.4% (Aug data, released Sep) (-0.13 pp MoM)",
        value: 57.4,
        unit: "percent",
      }),
    ).toBe("57.4%");
  });

  it("falls back to the series' own number when the sentence leads with words", () => {
    expect(
      cellFigure({
        display:
          "FedWatch puts a 25 bp hike at the Oct 28 FOMC at 59.7% (quoted Sep 22).",
        value: 59.7,
        unit: "percent",
      }),
    ).toBe("59.70%");
    expect(
      cellFigure({
        display:
          "a fourth straight inflow session (Sep 17, 18, 21, 22), the streak intact",
        value: 4,
        unit: "sessions",
      }),
    ).toBe("+4 sessions");
    expect(
      cellFigure({
        display:
          "ISM Manufacturing PMI 54.6 in August (July 55.6), the eighth straight month",
        value: 54.6,
        unit: "index_points",
      }),
    ).toBe("54.60");
  });
});

/* ------------------------------------------- a trigger nobody evaluated */

describe("a trigger whose clauses were not evaluated", () => {
  it("drops the chip track, so the clause text is not squeezed into it", () => {
    // Live W39: every clause is manual and unasserted, so none carries a chip,
    // and the text auto-placed into the 8ch chip track, one word a line.
    const data = honestDesk();
    const unevaluated = data.conditions.map((c) =>
      c.ownerKind === "trigger" ? { ...c, status: "unevaluable" as const } : c,
    );
    const { container } = render(
      <ScenarioTable
        conditions={unevaluated}
        scenarios={data.scenarios}
        targets={data.targets}
      />,
    );
    const lists = container.querySelectorAll("ul.sc-clauses");
    expect(lists.length).toBeGreaterThan(0);
    for (const list of lists)
      expect(list.classList.contains("is-plain")).toBe(true);
    expect(container.querySelector(".sc-dot")).toBeNull();
  });
});
