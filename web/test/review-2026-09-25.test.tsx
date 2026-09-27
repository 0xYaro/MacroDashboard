import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { GlossaryTerm } from "@/components/GlossaryTerm";
import { HoldingsWatch } from "@/components/HoldingsWatch";
import { standing } from "@/components/PhaseTile";
import { readableSource } from "@/components/SeriesDetail";
import { SourceLink } from "@/components/SourceLink";
import type { HoldingWatch, PhaseCallDetail } from "@/lib/contract";
import { supabaseSource } from "@/lib/data";
import { feedStatuses } from "@/lib/feeds";
import { checkChipLabel, healthLine } from "@/lib/freshness";
import { fixtureSnapshot } from "@/lib/fixtures";
import { glossaryFor } from "@/lib/glossary";
import { safeHref } from "@/lib/links";
import { dayLabel, fullDayLabel, shortDay, weekdayDay } from "@/lib/time";

/**
 * The fixes from the 2026-09-25 review, each pinned by the failure it had.
 */

describe("a source string with a dated URL", () => {
  it("keeps the URL whole and reformats only the dates outside it", () => {
    const [part] = readableSource(
      "Fed statement 2026-09-16 https://www.federalreserve.gov/newsevents/pressreleases/2026-09-16-fomc.htm",
    );
    expect(part?.href).toBe(
      "https://www.federalreserve.gov/newsevents/pressreleases/2026-09-16-fomc.htm",
    );
    expect(part?.before).toBe("Fed statement 16 Sept ");
    expect(part?.link).toBe("federalreserve.gov");
  });

  it("still reformats a date after the link", () => {
    const [part] = readableSource("federalreserve.gov/x/2026-09-16 read 2026-09-17");
    expect(part?.href).toBe("https://federalreserve.gov/x/2026-09-16");
    expect(part?.after).toBe(" read 17 Sept");
  });
});

describe("a refusal from a live write", () => {
  function rpcClient(error: unknown) {
    return {
      schema: () => ({
        rpc: () => Promise.resolve({ data: null, error }),
      }),
      auth: { signOut: () => {} },
    } as never;
  }

  it("never shows a raw Postgres error", async () => {
    const duplicate = supabaseSource(
      rpcClient({
        message:
          'duplicate key value violates unique constraint "uq_macro_watch_ticker_symbol"',
        code: "23505",
        details: "Key (symbol)=(MU) already exists.",
        hint: null,
      }),
    );
    const one = await duplicate.saveWatchTicker({
      id: null,
      symbol: "mu",
      name: null,
      assetClass: "equity",
      note: null,
    });
    expect(one.ok).toBe(false);
    expect(one.ok === false && one.reason).not.toMatch(/duplicate key|constraint/);
    expect(one.ok === false && one.reason).toMatch(/already on the list/);

    const check = supabaseSource(
      rpcClient({
        message:
          'new row for relation "manual_position" violates check constraint "ck_macro_manual_position_venue"',
        code: "23514",
        details: null,
        hint: null,
      }),
    );
    const two = await check.saveManualPosition({
      id: null,
      symbol: "D05",
      name: null,
      venue: "x".repeat(41),
      assetClass: "equity",
      quantity: null,
      note: null,
    });
    expect(two.ok === false && two.reason).not.toMatch(/violates|constraint/);
    expect(two.ok === false && two.reason).toMatch(/not allowed/);

    const other = supabaseSource(
      rpcClient({ message: "value too long for type character varying(40)", code: "22001" }),
    );
    const three = await other.retireWatchTicker("x");
    expect(three.ok === false && three.reason).toMatch(/too long/);
    expect(three.ok === false && three.reason).not.toMatch(/varying/);
  });

  it("passes the function's own plain sentence through", async () => {
    const source = supabaseSource(
      rpcClient({ message: "MU is already on the watch list", code: "P0001" }),
    );
    const result = await source.retireWatchTicker("x");
    expect(result).toEqual({ ok: false, reason: "MU is already on the watch list." });
  });
});

describe("the glossary popover", () => {
  const vix = glossaryFor({ key: "vix" });
  const hy = glossaryFor({ key: "hy_oas_bps" });

  // jsdom has no PointerEvent, so `pointerType` would never reach React.
  beforeAll(() => {
    if (typeof window.PointerEvent === "undefined") {
      class PointerEventShim extends MouseEvent {
        pointerType: string;
        constructor(type: string, init: PointerEventInit = {}) {
          super(type, init);
          this.pointerType = init.pointerType ?? "";
        }
      }
      Object.assign(window, { PointerEvent: PointerEventShim });
    }
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("closes a keyboard-pinned popover when focus leaves, so they never stack", () => {
    render(
      <>
        <GlossaryTerm entry={vix}>VIX</GlossaryTerm>
        <GlossaryTerm entry={hy}>HY</GlossaryTerm>
      </>,
    );
    const first = screen.getByRole("button", { name: "VIX" });
    const second = screen.getByRole("button", { name: "HY" });
    act(() => first.focus());
    // Enter on a button is a click.
    fireEvent.click(first);
    expect(screen.getAllByRole("tooltip")).toHaveLength(1);
    act(() => second.focus());
    fireEvent.click(second);
    const open = screen.getAllByRole("tooltip");
    expect(open).toHaveLength(1);
    expect(open[0]).toHaveTextContent(/extra interest/i);
  });

  it("keeps only one open at a time, however it was opened", () => {
    render(
      <>
        <GlossaryTerm entry={vix}>VIX</GlossaryTerm>
        <GlossaryTerm entry={hy}>HY</GlossaryTerm>
      </>,
    );
    fireEvent.click(screen.getByRole("button", { name: "VIX" }));
    fireEvent.click(screen.getByRole("button", { name: "HY" }));
    expect(screen.getAllByRole("tooltip")).toHaveLength(1);
  });

  it("stays open while the pointer is over the popover, and Escape closes it", () => {
    vi.useFakeTimers();
    render(<GlossaryTerm entry={vix}>VIX</GlossaryTerm>);
    const term = screen.getByRole("button", { name: "VIX" });
    fireEvent.pointerEnter(term, { pointerType: "mouse" });
    expect(screen.getByRole("tooltip")).toBeVisible();
    fireEvent.pointerLeave(term, { pointerType: "mouse" });
    const pop = screen.getByRole("tooltip");
    fireEvent.pointerEnter(pop, { pointerType: "mouse" });
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(screen.getByRole("tooltip")).toBeVisible();
    fireEvent.pointerLeave(pop, { pointerType: "mouse" });
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(screen.queryByRole("tooltip")).toBeNull();

    fireEvent.pointerEnter(term, { pointerType: "mouse" });
    expect(screen.getByRole("tooltip")).toBeVisible();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("tooltip")).toBeNull();
  });
});

describe("a late feed says so in words", () => {
  const NOW = new Date("2026-09-21T02:00:00.000Z");

  it("writes 'late' into the line, not only the colour", () => {
    const [calendar] = feedStatuses(
      [
        {
          feed: "catalysts",
          origin: "desk",
          lastRunAt: "2026-09-19T00:40:00.000Z",
          lastFinishedAt: "2026-09-19T00:45:00.000Z",
          lastStatus: "ok",
          lastError: null,
          lastOkAt: "2026-09-19T00:45:00.000Z",
          lastOkOrigin: "desk",
          rows: 12,
        },
      ],
      NOW,
      false,
    );
    expect(calendar?.stale).toBe(true);
    expect(calendar?.text).toMatch(/^late/);
  });

  it("says a week in words, not hours", () => {
    const tail = feedStatuses(
      [
        {
          feed: "tail_risks",
          origin: "desk",
          lastRunAt: "2026-09-20T22:00:00.000Z",
          lastFinishedAt: "2026-09-20T22:05:00.000Z",
          lastStatus: "ok",
          lastError: null,
          lastOkAt: "2026-09-20T22:05:00.000Z",
          lastOkOrigin: "desk",
          rows: 3,
        },
      ],
      NOW,
      false,
    ).find((one) => one.feed === "tail_risks");
    expect(tail?.title).toContain("Expected at least weekly");
    expect(tail?.title).not.toMatch(/168 hours/);
  });
});

describe("the health strip names the worker's own checks", () => {
  const counts = {
    fresh: 10,
    dated: 0,
    stale: 0,
    failed: 0,
    unknown: 0,
  } as never;

  it("counts each check key the worker emits, in agreement", () => {
    const line = healthLine({
      counts,
      lead: null,
      rows: [],
      findings: [
        { checkKey: "holdings_check_missing", subject: "MU", closedAt: null },
        { checkKey: "scenario_premise_moved", subject: "S2 clause 1", closedAt: null },
        { checkKey: "scenario_premise_moved", subject: "S2 clause 2", closedAt: null },
      ],
    });
    expect(line.clauses).toContain("2 scenario premises moved");
    expect(line.clauses).toContain("1 holding without a strategy row");
  });

  it("counts a check it has never heard of without broken grammar", () => {
    const line = healthLine({
      counts,
      lead: null,
      rows: [],
      findings: [
        { checkKey: "brand_new_check", subject: "a", closedAt: null },
      ],
    });
    expect(line.clauses).toEqual(["1 finding: brand new check"]);
    expect(checkChipLabel("holdings_check_missing", "MU")).toBe(
      "holding without a strategy row",
    );
  });

  it("uses only check keys the worker writes in the fixtures", () => {
    for (const dataset of ["clean", "honest"] as const) {
      for (const finding of fixtureSnapshot(dataset).findings as {
        check_key: string;
      }[]) {
        expect(
          [
            "holdings_check_coverage",
            "premise_moved",
            "permanent_gap",
            "series_freshness",
            "setup_gate_refused",
          ],
        ).not.toContain(finding.check_key);
      }
    }
  });
});

describe("plain words", () => {
  it("spells out weeks on the phase tile", () => {
    const call = { priorPhase: 2, weeksRunning: 8 } as PhaseCallDetail;
    expect(standing(call, 2)).toBe("same for 8 weeks");
    expect(standing({ ...call, weeksRunning: 1 }, 2)).toBe("same as last week");
    expect(standing({ ...call, priorPhase: 1 }, 2)).toBe("was P1");
  });

  it("writes one date format everywhere: no leading zero, Sept", () => {
    expect(dayLabel("2026-09-09")).toBe("9 Sept");
    expect(dayLabel("2026-10-01")).toBe("1 Oct");
    expect(fullDayLabel("2026-09-09")).toBe("9 Sept 2026");
    const at = new Date("2026-09-21T02:00:00.000Z");
    expect(shortDay(at, "Asia/Singapore")).toBe("21 Sept");
    expect(weekdayDay(at, "Asia/Singapore")).toBe("Mon 21 Sept");
  });
});

/* ---------------------------------------------------------- the holdings */

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

describe("the holdings watch", () => {
  const NOW = new Date("2026-09-21T02:00:00.000Z");

  it("counts one name as one name", () => {
    render(
      <HoldingsWatch events={[]} holdings={[holding({})]} now={NOW} watchlist={[]} />,
    );
    expect(screen.getByText(/^1 name ·/)).toBeInTheDocument();
  });

  it("says plainly when a material move has no clear reason, with no link", () => {
    render(
      <HoldingsWatch
        events={[]}
        holdings={[
          holding({
            symbol: "APP",
            change24hPct: 6.1,
            material: true,
            noClearReason: true,
            driverSummary: null,
            sourceUrl: null,
          }),
          holding({
            symbol: "GOOG",
            change24hPct: -3.2,
            material: true,
            noClearReason: true,
            driverSummary: "Shares fell with the rest of big tech.",
            sourceUrl: null,
          }),
        ]}
        now={NOW}
        watchlist={[]}
      />,
    );
    const app = screen.getByText("APP").closest("li")!;
    expect(within(app).getByText(/No clear reason found/)).toBeInTheDocument();
    expect(within(app).queryByText(/^Quiet/)).toBeNull();
    const goog = screen.getByText("GOOG").closest("li")!;
    expect(within(goog).getByText(/No clear reason found/)).toBeInTheDocument();
    expect(within(goog).getByText(/Shares fell with the rest/)).toBeInTheDocument();
    expect(within(goog).queryByRole("link")).toBeNull();
  });
});

describe("an agent-written URL", () => {
  it("is a link only when it parses as http or https", () => {
    expect(safeHref("https://www.reuters.com/x")).toBe("https://www.reuters.com/x");
    expect(safeHref(" http://example.com ")).toBe("http://example.com");
    expect(safeHref("javascript:alert(1)")).toBeNull();
    expect(safeHref(" JavaScript:alert(1)")).toBeNull();
    expect(safeHref("data:text/html,<script>alert(1)</script>")).toBeNull();
    expect(safeHref("/relative/path")).toBeNull();
    expect(safeHref("not a url")).toBeNull();
    expect(safeHref(null)).toBeNull();
  });

  it("renders as plain text otherwise", () => {
    const { container } = render(<SourceLink url="javascript:alert(1)" />);
    expect(container.querySelector("a")).toBeNull();
    const ok = render(<SourceLink url="https://www.reuters.com/x" />);
    expect(ok.getByRole("link")).toHaveAttribute("href", "https://www.reuters.com/x");
  });

  it("is never linked from a source string, a holding event or a watchlist row", () => {
    const [part] = readableSource("javascript:alert(1)//evil.com");
    expect(part?.href ?? null).not.toMatch(/^javascript:/);

    render(
      <HoldingsWatch
        events={[
          {
            id: "e1",
            symbol: "X",
            kind: "earnings",
            name: "Bad event",
            startsAt: "2026-09-25T00:00:00.000Z",
            endsAt: null,
            datePrecision: "day",
            dateEstimated: false,
            dateText: null,
            sourceUrl: "data:text/html,hi",
            catalystId: null,
          },
        ]}
        holdings={[holding({})]}
        now={NOW_FOR_EVENTS}
        watchlist={[]}
      />,
    );
    expect(screen.getByText("Bad event").closest("a")).toBeNull();
  });
});

const NOW_FOR_EVENTS = new Date("2026-09-21T02:00:00.000Z");
