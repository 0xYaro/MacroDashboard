import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import {
  feedFreshness,
  holdingEvent,
  holdingWatch,
  ingestRun,
  manualPosition,
  tailRisk,
  tailRiskCheck,
  watchTicker,
  type FeedFreshness,
  type HoldingWatch,
  type IngestRun,
  type TailRiskCheck,
} from "@/lib/contract";
import { fixtureSource, readingOf, supabaseSource, type Source } from "@/lib/data";
import {
  expectedGapMs,
  feedStatuses,
  freshnessRows,
  isFeedRun,
  severityTrail,
} from "@/lib/feeds";
import { fixtureSnapshot } from "@/lib/fixtures";
import { basisWord, eventsFor, isQuiet, sortHoldings } from "@/lib/holdings";
import { whenWord } from "@/lib/time";
import { ProvenanceFooter } from "@/components/ProvenanceFooter";

/**
 * The desk feeds on the Catalysts page: the holdings watch, the freshness
 * line, the tail-risk trail, and the operator's two lists.
 */

const NOW = new Date("2026-09-21T02:00:00.000Z"); // 10:00 SGT, Mon 21 Sept

describe("the validators read the desk views as the fixtures send them", () => {
  for (const dataset of ["clean", "honest"] as const) {
    const fx = fixtureSnapshot(dataset);
    it(`rejects nothing in the ${dataset} fixtures`, () => {
      const reads = [
        readingOf(fx.holdings, holdingWatch),
        readingOf(fx.holdingEvents, holdingEvent),
        readingOf(fx.manualPositions, manualPosition),
        readingOf(fx.watchTickers, watchTicker),
        readingOf(fx.feeds, feedFreshness),
        readingOf(fx.tailRisks, tailRisk),
        readingOf(fx.tailRiskHistory, tailRiskCheck),
        readingOf(fx.runs, ingestRun),
      ];
      for (const read of reads) {
        expect(read.transportError).toBeNull();
        expect(read.rejected).toEqual([]);
        expect(read.rows.length).toBeGreaterThan(0);
      }
    });
  }

  it("reads a tail risk from before migration 20260924000300 with no id and no checks", () => {
    const old = tailRisk({
      key: "k",
      name: "n",
      severity: "watch",
      summary: null,
      priced_in: null,
      assets: [],
      source_url: null,
      first_seen: "2026-09-14",
      last_seen: "2026-09-21",
    });
    expect(old.ok && old.value.riskId).toBeNull();
    expect(old.ok && old.value.previousSeverity).toBeNull();
  });

  it("keeps a change the desk could not compute as null, never zero", () => {
    const fx = fixtureSnapshot("clean");
    const neko = readingOf(fx.holdings, holdingWatch).rows.find(
      (row) => row.symbol === "NEKO",
    )!;
    expect(neko.change24hPct).toBeNull();
    expect(neko.unavailableReason).toMatch(/did not trade/);
  });
});

/* ------------------------------------------------------------ the watch */

function holding(over: Partial<HoldingWatch>): HoldingWatch {
  return {
    asOf: "2026-09-21",
    symbol: "X",
    source: "accounting",
    name: null,
    assetClass: "equity",
    marketSymbol: "X",
    price: 1,
    priceAt: null,
    currency: "USD",
    change24hPct: 0,
    changeBasis: "close_to_close",
    changeSession: "2026-09-18",
    priceSource: null,
    unavailableReason: null,
    underlyingOnly: false,
    material: false,
    driverSummary: null,
    sourceUrl: null,
    noClearReason: false,
    recordedAt: "2026-09-21T01:07:40.000Z",
    ...over,
  };
}

describe("the holdings order and words", () => {
  it("puts material moves first, then the biggest moves either way, then no change", () => {
    const order = sortHoldings([
      holding({ symbol: "SMALL", change24hPct: 0.2 }),
      holding({ symbol: "NONE", change24hPct: null, unavailableReason: "r" }),
      holding({ symbol: "BIGDOWN", change24hPct: -5 }),
      holding({ symbol: "NEWS", change24hPct: 0.1, material: true }),
      holding({ symbol: "UP", change24hPct: 3 }),
    ]).map((row) => row.symbol);
    expect(order).toEqual(["NEWS", "BIGDOWN", "UP", "SMALL", "NONE"]);
  });

  it("names the basis a change was measured on", () => {
    expect(basisWord(holding({}))).toBe("since last close");
    expect(basisWord(holding({ changeBasis: "rolling_24h" }))).toBe("24h");
    expect(
      basisWord(holding({ underlyingOnly: true, marketSymbol: "META" })),
    ).toBe("META since last close");
    expect(basisWord(holding({ changeBasis: null }))).toBeNull();
  });

  it("calls a move with no reason quiet, but not one the desk could not price", () => {
    expect(isQuiet(holding({}))).toBe(true);
    expect(isQuiet(holding({ driverSummary: "why" }))).toBe(false);
    expect(isQuiet(holding({ unavailableReason: "no trade" }))).toBe(false);
  });

  it("drops an event more than a day past its end, even if the view still held it", () => {
    const events = readingOf(fixtureSnapshot("clean").holdingEvents, holdingEvent).rows;
    expect(eventsFor(events, "TLT", NOW).map((e) => e.name)).toEqual([
      "August core PCE inflation",
      "September jobs report",
    ]);
    expect(eventsFor(events, "TLT", new Date("2026-10-04T00:00:00.000Z"))).toHaveLength(0);
  });
});

/* ----------------------------------------------------- where feeds came from */

function feed(over: Partial<FeedFreshness>): FeedFreshness {
  return {
    feed: "catalysts",
    origin: "openclaw:macro-desk",
    lastRunAt: "2026-09-21T00:40:00.000Z",
    lastFinishedAt: "2026-09-21T00:51:00.000Z",
    lastStatus: "ok",
    lastError: null,
    lastOkAt: "2026-09-21T00:51:00.000Z",
    lastOkOrigin: "openclaw:macro-desk",
    rows: 6,
    ...over,
  };
}

function run(over: Partial<IngestRun>): IngestRun {
  return {
    id: 1,
    source: "yahoo",
    runKind: "full",
    startedAt: "2026-09-21T01:30:00.000Z",
    finishedAt: "2026-09-21T01:30:10.000Z",
    rows: 1,
    status: "ok",
    error: null,
    feed: null,
    origin: null,
    ...over,
  };
}

describe("the freshness line", () => {
  it("reads a desk feed from this morning as current", () => {
    const [calendar] = feedStatuses([feed({})], NOW, false);
    expect(calendar!.label).toBe("Calendar");
    expect(calendar!.text).toBe("OpenClaw, 08:51 today");
    expect(calendar!.stale).toBe(false);
  });

  it("gives an Obsidian note a day, since only the 09:30 full run reads the vault", () => {
    const vault = (lastOkAt: string) =>
      feed({ origin: "vault", lastOkOrigin: "vault", lastOkAt, lastRunAt: lastOkAt, lastFinishedAt: lastOkAt });
    // Read at 09:32 SGT, looked at 00:50 SGT the next day: 15 hours, on time.
    const [fifteen] = feedStatuses(
      [vault("2026-09-24T01:32:00.000Z")],
      new Date("2026-09-24T16:50:00.000Z"),
      false,
    );
    expect(fifteen!.text).toBe("Obsidian note, 09:32 yesterday");
    expect(fifteen!.stale).toBe(false);
    // A missed morning run: 25.5 hours since the last good read.
    const [missed] = feedStatuses(
      [vault("2026-09-23T01:32:00.000Z")],
      new Date("2026-09-24T03:00:00.000Z"),
      false,
    );
    expect(missed!.stale).toBe(true);
  });

  it("gives the tail risks a week between runs unless one is elevated or acute", () => {
    expect(expectedGapMs("tail_risks", "openclaw:macro-desk", false)).toBeGreaterThan(
      6 * 86_400_000,
    );
    expect(expectedGapMs("tail_risks", "openclaw:macro-desk", true)).toBeLessThan(
      2 * 86_400_000,
    );
  });

  it("says a failed feed failed and when it last worked", () => {
    const [calendar] = feedStatuses(
      [feed({ lastStatus: "failed", lastError: "note unreadable", lastRunAt: "2026-09-21T01:30:00.000Z" })],
      NOW,
      false,
    );
    expect(calendar!.stale).toBe(true);
    expect(calendar!.text).toBe("failed, last good 08:51 today");
    expect(calendar!.title).toContain("note unreadable");
  });

  it("names a feed that never ran rather than leaving it out", () => {
    const statuses = feedStatuses([], NOW, false);
    expect(statuses.map((s) => s.text)).toEqual(["no run yet", "no run yet", "no run yet"]);
    expect(statuses.every((s) => s.stale)).toBe(true);
  });

  it("rebuilds the line from the ingest runs when the view is not there yet", () => {
    const rows = freshnessRows(
      [],
      [
        run({ id: 1, source: "vault:catalysts", finishedAt: "2026-09-21T01:30:05.000Z", rows: 72 }),
        run({ id: 2, source: "desk:holdings", runKind: "desk", finishedAt: "2026-09-21T01:07:40.000Z" }),
        run({ id: 3, source: "fred" }),
      ],
    );
    expect(rows.map((r) => [r.feed, r.origin])).toEqual([
      ["catalysts", "vault"],
      ["holdings", "desk"],
    ]);
    const statuses = feedStatuses(rows, NOW, false);
    expect(statuses[0]!.text).toBe("Obsidian note, 09:30 today");
    expect(statuses[2]!.text).toBe("OpenClaw, 09:07 today");
  });

  it("words an instant the way the desk reads it", () => {
    expect(whenWord(new Date("2026-09-21T00:51:00.000Z"), NOW)).toBe("08:51 today");
    expect(whenWord(new Date("2026-09-20T13:00:00.000Z"), NOW)).toBe("21:00 yesterday");
    expect(whenWord(new Date("2026-09-18T01:30:00.000Z"), NOW)).toBe("Fri 18 Sept 09:30");
  });
});

describe("the Data page's source footer", () => {
  it("knows a feed run by its column or by its source prefix", () => {
    expect(isFeedRun(run({ feed: "catalysts" }))).toBe(true);
    expect(isFeedRun(run({ source: "vault:tail_risks" }))).toBe(true);
    expect(isFeedRun(run({ source: "desk:holdings" }))).toBe(true);
    expect(isFeedRun(run({ source: "search:cnn_fear_greed" }))).toBe(false);
  });

  it("leaves the feed runs out, since the Catalysts page shows them", async () => {
    const runs = readingOf(fixtureSnapshot("honest").runs, ingestRun).rows;
    expect(runs.some((one) => one.source.startsWith("vault:"))).toBe(true);
    render(<ProvenanceFooter now={NOW} runs={runs} />);
    expect(screen.getByText(/3 of 4 answered/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Sources/ }));
    expect(screen.queryByText(/^vault:/)).toBeNull();
    expect(screen.queryByText(/^desk:/)).toBeNull();
    expect(screen.getByText("yahoo")).toBeInTheDocument();
  });
});

/* ------------------------------------------------------ the tail-risk trail */

function check(severity: TailRiskCheck["severity"], checkedAt: string): TailRiskCheck {
  return {
    riskId: "r",
    name: "r",
    checkedAt,
    kind: "weekly",
    severity,
    summary: null,
    sourceUrl: null,
    retired: false,
  };
}

describe("a tail risk's escalation trail", () => {
  it("folds repeated levels into the step that reached them, oldest first", () => {
    const steps = severityTrail(
      [
        check("acute", "2026-09-22T01:20:00.000Z"),
        check("watch", "2026-09-14T00:00:00.000Z"),
        check("elevated", "2026-09-20T22:00:00.000Z"),
        check("elevated", "2026-09-21T01:20:00.000Z"),
      ],
      "r",
    );
    expect(steps.map((s) => `${s.severity} ${s.day}`)).toEqual([
      "watch 14 Sept",
      "elevated 21 Sept",
      "acute 22 Sept",
    ]);
  });

  it("reads only the risk it was asked about", () => {
    expect(severityTrail([check("watch", "2026-09-14T00:00:00.000Z")], "other")).toEqual([]);
  });
});

/* --------------------------------------------------- the operator's lists */

describe("the fixture source edits the two lists the way the functions do", () => {
  it("adds, edits and deactivates a manual position, and refuses what the SQL refuses", async () => {
    const source = fixtureSource("clean");
    const list = async () =>
      (await source.optionalTable("manual_position_state", manualPosition)).rows;
    expect((await list()).map((r) => r.symbol)).toEqual(["D05"]);

    const input = {
      id: null,
      symbol: " 012330 ",
      name: "Hyundai Mobis",
      venue: "Kiwoom",
      assetClass: "equity" as const,
      quantity: null,
      note: "  ",
    };
    expect(await source.saveManualPosition(input)).toEqual({ ok: true });
    const added = (await list()).find((r) => r.symbol === "012330")!;
    expect(added.note).toBeNull();

    expect(
      await source.saveManualPosition({ ...input, id: added.id, symbol: "012330", note: "KRX" }),
    ).toEqual({ ok: true });
    expect((await list()).find((r) => r.id === added.id)!.note).toBe("KRX");

    const again = await source.saveManualPosition({ ...input, venue: "kiwoom" });
    expect(again.ok).toBe(false);
    const bad = await source.saveManualPosition({ ...input, symbol: "no spaces" });
    expect(bad.ok === false && bad.reason).toMatch(/letters, digits/);
    const noVenue = await source.saveManualPosition({ ...input, symbol: "X", venue: " " });
    expect(noVenue.ok === false && noVenue.reason).toMatch(/broker or venue/);

    expect(await source.retireManualPosition(added.id)).toEqual({ ok: true });
    expect((await list()).map((r) => r.symbol)).toEqual(["D05"]);
    expect((await source.retireManualPosition(added.id)).ok).toBe(false);
  });

  it("refuses a second active watch on one symbol", async () => {
    const source = fixtureSource("clean");
    const result = await source.saveWatchTicker({
      id: null,
      symbol: "mu",
      name: null,
      assetClass: "equity",
      note: null,
    });
    expect(result.ok).toBe(false);
  });
});

let editorSource: Source = fixtureSource("clean");
vi.mock("@/hooks/useSource", () => ({
  useSource: () => ({
    source: editorSource,
    unavailable: null,
    fixtures: true,
    dataset: "clean",
  }),
}));

const { HoldingsEditor } = await import("@/components/HoldingsEditor");

describe("the list editor", () => {
  function renderEditor() {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    return render(
      <QueryClientProvider client={client}>
        <HoldingsEditor />
      </QueryClientProvider>,
    );
  }

  it("stays tucked away until opened, then explains what belongs on it", async () => {
    const user = userEvent.setup();
    renderEditor();
    expect(screen.queryByText(/come in from Accounting on their own/)).toBeNull();
    await user.click(screen.getByText("Manage list"));
    expect(
      await screen.findByText(/come in from Accounting on their own/),
    ).toBeInTheDocument();
    expect(await screen.findByText("D05")).toBeInTheDocument();
    expect(screen.getByText("MU")).toBeInTheDocument();
  });

  it("adds a position held elsewhere, edits its note and removes it", async () => {
    const user = userEvent.setup();
    renderEditor();
    await user.click(screen.getByText("Manage list"));
    await user.click(await screen.findByRole("button", { name: "+ Add a position" }));
    await user.type(screen.getByLabelText("Symbol"), "012330");
    await user.type(screen.getByLabelText("Broker"), "Kiwoom");
    await user.click(screen.getByRole("button", { name: "Add" }));
    const row = (await screen.findByText("012330")).closest("li")!;
    expect(within(row).getByText(/Kiwoom · Stock/)).toBeInTheDocument();

    await user.click(within(row).getByRole("button", { name: "Edit note" }));
    await user.type(within(row).getByLabelText("Note for 012330"), "KRX account");
    await user.click(within(row).getByRole("button", { name: "Save" }));
    expect(await within(row).findByText("KRX account")).toBeInTheDocument();

    expect(within(row).getByRole("button", { name: "Edit note" })).toHaveFocus();

    await user.click(within(row).getByRole("button", { name: "Remove" }));
    expect(
      within(row).getByText(/Remove 012330 from your held-elsewhere list\? It stays on record/),
    ).toBeInTheDocument();
    await user.click(within(row).getByRole("button", { name: "Keep" }));
    expect(within(row).getByRole("button", { name: "Remove" })).toHaveFocus();

    await user.click(within(row).getByRole("button", { name: "Remove" }));
    await user.click(within(row).getByRole("button", { name: "Remove" }));
    await vi.waitFor(() => expect(screen.queryByText("012330")).toBeNull());
    expect(screen.getByRole("button", { name: "+ Add a position" })).toHaveFocus();
  });

  it("asks to stop watching a watched name, and returns focus after Cancel", async () => {
    const user = userEvent.setup();
    renderEditor();
    await user.click(screen.getByText("Manage list"));
    const row = (await screen.findByText("MU")).closest("li")!;
    await user.click(within(row).getByRole("button", { name: "Remove" }));
    expect(within(row).getByText(/Stop watching MU\?/)).toBeInTheDocument();
    await user.click(within(row).getByRole("button", { name: "Keep" }));
    await user.click(within(row).getByRole("button", { name: "Edit note" }));
    await user.click(within(row).getByRole("button", { name: "Cancel" }));
    expect(within(row).getByRole("button", { name: "Edit note" })).toHaveFocus();
  });

  it("limits each field to what the database stores", async () => {
    const user = userEvent.setup();
    renderEditor();
    await user.click(screen.getByText("Manage list"));
    await user.click(await screen.findByRole("button", { name: "+ Add a position" }));
    expect(screen.getByLabelText("Broker")).toHaveAttribute("maxLength", "40");
    expect(screen.getByLabelText("Symbol")).toHaveAttribute("maxLength", "24");
    expect(screen.getByLabelText("Name (optional)")).toHaveAttribute("maxLength", "80");
  });

  it("turns a raw Postgres refusal into a plain sentence in place", async () => {
    const live = supabaseSource({
      schema: () => ({
        rpc: () =>
          Promise.resolve({
            data: null,
            error: {
              message:
                'duplicate key value violates unique constraint "uq_macro_watch_ticker_symbol"',
              code: "23505",
              details: "Key (symbol)=(MU) already exists.",
              hint: null,
            },
          }),
      }),
      auth: { signOut: () => {} },
    } as never);
    const fixture = fixtureSource("clean");
    editorSource = { ...fixture, saveWatchTicker: live.saveWatchTicker };
    try {
      const user = userEvent.setup();
      renderEditor();
      await user.click(screen.getByText("Manage list"));
      await user.click(await screen.findByRole("button", { name: "+ Watch a name" }));
      await user.type(screen.getByLabelText("Symbol"), "mu");
      await user.click(screen.getByRole("button", { name: "Add" }));
      expect(await screen.findByText(/already on the list/)).toBeInTheDocument();
      expect(screen.queryByText(/duplicate key|constraint/)).toBeNull();
    } finally {
      editorSource = fixtureSource("clean");
    }
  });
});
