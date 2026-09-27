import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import {
  catalystWindow,
  feedFreshness,
  holdingEvent,
  holdingWatch,
  tailRisk,
  tailRiskCheck,
  watchlistChange,
  type CatalystWindow,
  type HoldingWatch,
} from "@/lib/contract";
import {
  endOf,
  groupCounts,
  groupOf,
  isPast,
  monthLabel,
  sortKey,
  withinWindow,
} from "@/lib/catalysts";
import { readingOf } from "@/lib/data";
import { fixtureSnapshot } from "@/lib/fixtures";
import { readReading } from "@/lib/freshness";

/**
 * The Catalysts page: what kind each event is, which dates are only guesses,
 * and what the page drops once it is over.
 */

function row(overrides: Partial<CatalystWindow>): CatalystWindow {
  return {
    catalystId: "c",
    name: "c",
    startsAt: "2026-10-14T12:30:00.000Z",
    endsAt: null,
    datePrecision: "exact",
    dateText: null,
    dateEstimated: false,
    category: "us_macro",
    affected: [],
    daysOut: 21,
    referencedBy: [],
    sourceUrl: null,
    ...overrides,
  };
}

describe("the eight groups", () => {
  it("folds the sixteen worker categories into the groups a reader knows", () => {
    expect(groupOf("us_macro")).toBe("macro");
    expect(groupOf("global_macro")).toBe("macro");
    expect(groupOf("fed")).toBe("central_banks");
    expect(groupOf("global_central_bank")).toBe("central_banks");
    expect(groupOf("earnings")).toBe("earnings");
    for (const c of [
      "index_options",
      "market_closure",
      "market_structure",
      "treasury_liquidity",
    ]) {
      expect(groupOf(c), c).toBe("market_events");
    }
    expect(groupOf("ai_tech")).toBe("tech");
    for (const c of ["policy_geopolitics", "trade_tariffs", "commodities_energy"]) {
      expect(groupOf(c), c).toBe("policy");
    }
    expect(groupOf("investor_forum")).toBe("investor");
    expect(groupOf("crypto")).toBe("crypto");
  });

  it("puts other, a null and an unheard-of category in Other rather than dropping them", () => {
    expect(groupOf("other")).toBe("other");
    expect(groupOf(null)).toBe("other");
    expect(groupOf("sports")).toBe("other");
  });

  it("counts every row once", () => {
    const counts = groupCounts([
      row({ category: "fed" }),
      row({ category: "global_central_bank" }),
      row({ category: "earnings" }),
    ]);
    expect(counts.get("central_banks")).toBe(2);
    expect(counts.get("earnings")).toBe(1);
  });
});

describe("estimated dates", () => {
  const late = row({
    datePrecision: "month",
    startsAt: "2026-10-21T00:00:00.000Z",
    endsAt: "2026-10-31T00:00:00.000Z",
    dateEstimated: true,
  });

  it("names the part of the month the monitor gave", () => {
    expect(monthLabel(late)).toBe("Late Oct");
    expect(
      monthLabel({
        ...late,
        startsAt: "2026-10-11T00:00:00.000Z",
        endsAt: "2026-10-31T00:00:00.000Z",
      }),
    ).toBe("Mid-late Oct");
    expect(
      monthLabel({
        ...late,
        startsAt: "2026-10-01T00:00:00.000Z",
        endsAt: "2026-10-31T00:00:00.000Z",
      }),
    ).toBe("October");
  });

  it("sorts a month-only estimate after every dated row of its month", () => {
    const dated = row({ startsAt: "2026-10-30T12:30:00.000Z" });
    const november = row({ startsAt: "2026-11-02T15:00:00.000Z" });
    const order = [november, late, dated].sort((a, b) =>
      sortKey(a).localeCompare(sortKey(b)),
    );
    expect(order).toEqual([dated, late, november]);
  });
});

describe("what the page drops", () => {
  const now = new Date("2026-09-23T01:30:00.000Z");

  it("keeps an event until it is more than a day past its end", () => {
    const lastNight = row({ startsAt: "2026-09-22T18:00:00.000Z" });
    const twoDaysAgo = row({ startsAt: "2026-09-21T00:00:00.000Z" });
    const runningWindow = row({
      datePrecision: "window",
      startsAt: "2026-09-21T00:00:00.000Z",
      endsAt: "2026-09-25T00:00:00.000Z",
    });
    expect(withinWindow(lastNight, now)).toBe(true);
    expect(isPast(lastNight, now)).toBe(true);
    expect(withinWindow(twoDaysAgo, now)).toBe(false);
    expect(withinWindow(runningWindow, now)).toBe(true);
    expect(isPast(runningWindow, now)).toBe(false);
  });

  it("gives a day-dated event its whole day", () => {
    const today = row({
      datePrecision: "day",
      startsAt: "2026-09-23T00:00:00.000Z",
    });
    expect(endOf(today)).toBe(Date.parse("2026-09-24T00:00:00.000Z"));
    expect(isPast(today, now)).toBe(false);
  });
});

describe("the validators", () => {
  it("reads a window row from before migration 20260923001000 as firm and uncategorised", () => {
    const old = catalystWindow({
      catalyst_id: "c",
      name: "c",
      starts_at: "2026-10-14T12:30:00Z",
      date_precision: "exact",
      affected: [],
      days_out: 21,
      referenced_by: [],
      source_url: null,
    });
    expect(old.ok).toBe(true);
    if (old.ok) {
      expect(old.value.dateEstimated).toBe(false);
      expect(old.value.category).toBeNull();
      expect(old.value.endsAt).toBeNull();
    }
  });

  it("reads the session a watchlist move belongs to", () => {
    const move = watchlistChange({
      ticker: "MU",
      observed_at: "2026-09-21T01:15:00Z",
      change_pct: "3.92",
      summary: null,
      source_url: "https://www.investing.com/x",
      session_on: "2026-09-18",
    });
    expect(move.ok && move.value.sessionOn).toBe("2026-09-18");
  });
});

/* ---------------------------------------------------------------- the page */

const fx = fixtureSnapshot("honest");
const clean = fixtureSnapshot("clean");
const HOLDINGS = readingOf(fx.holdings, holdingWatch).rows;
const macro: Record<string, unknown> & { holdings: readonly HoldingWatch[] } = {
  reading: readReading({
    states: ["fresh"],
    rejected: [],
    transportError: null,
    evaluatedAt: "2026-09-21T13:00:00.000Z",
    answered: true,
    subject: "the catalysts",
    relations: ["macro_mart.catalyst_window"],
  }),
  catalysts: readingOf(fx.catalysts, catalystWindow).rows,
  tailRisks: readingOf(fx.tailRisks, tailRisk).rows,
  watchlist: readingOf(fx.watchlist, watchlistChange).rows,
  tailRiskHistory: readingOf(fx.tailRiskHistory, tailRiskCheck).rows,
  holdings: HOLDINGS,
  holdingEvents: readingOf(fx.holdingEvents, holdingEvent).rows,
  feeds: readingOf(fx.feeds, feedFreshness).rows,
  runs: [],
  snapshot: [],
  conditions: [],
  scenarios: [],
  runRequests: [],
  gaps: [],
  trends: [],
};

vi.mock("@/hooks/useMacro", () => ({ useMacro: () => macro }));
vi.mock("@/hooks/useRenderTick", () => ({ useRenderTick: () => undefined }));

const { CatalystsPanel } = await import("@/components/CatalystsPanel");

/**
 * An in-memory Storage. Node's own experimental `localStorage` global shadows
 * jsdom's and is undefined without a file flag, so the page's store is given
 * one explicitly rather than depending on which runtime wins.
 */
function memoryStorage(): Storage {
  const items = new Map<string, string>();
  return {
    get length() {
      return items.size;
    },
    clear: () => items.clear(),
    getItem: (key) => items.get(key) ?? null,
    key: (index) => [...items.keys()][index] ?? null,
    removeItem: (key) => void items.delete(key),
    setItem: (key, value) => void items.set(key, String(value)),
  };
}

describe("the Catalysts page", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-09-21T02:00:00.000Z"));
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      value: memoryStorage(),
    });
  });
  afterEach(() => {
    vi.useRealTimers();
    macro.catalysts = readingOf(fx.catalysts, catalystWindow).rows;
    macro.conditions = [];
    macro.scenarios = [];
    macro.holdings = HOLDINGS;
    macro.tailRisks = readingOf(fx.tailRisks, tailRisk).rows;
    macro.tailRiskHistory = readingOf(fx.tailRiskHistory, tailRiskCheck).rows;
  });

  it("still renders when storage throws, on All", () => {
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      get() {
        throw new Error("blocked");
      },
    });
    render(<CatalystsPanel />);
    expect(screen.getByRole("button", { name: /All/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("says where the feeds come from, once", () => {
    render(<CatalystsPanel />);
    expect(screen.getAllByText(/Found by an AI web search/)).toHaveLength(1);
  });

  it("files a late-October estimate at the end of October, marked est.", () => {
    render(<CatalystsPanel />);
    const msft = screen.getByText("Microsoft FY2027 Q1 earnings").closest("li")!;
    expect(within(msft).getByText("Late Oct")).toBeInTheDocument();
    expect(within(msft).getByText("est.")).toBeInTheDocument();
    expect(within(msft).getByText("Earnings")).toBeInTheDocument();
    const tesla = screen.getByText("Tesla Q3 2026 earnings").closest("li")!;
    expect(within(tesla).getByText("Mid-late Oct")).toBeInTheDocument();
    // The month bucket comes after every dated October row, not on the 1st.
    const names = screen
      .getAllByRole("listitem")
      .map((li) => li.querySelector(".cal-name a, .cal-name")?.textContent ?? "");
    const at = (needle: string) => names.findIndex((n) => n.includes(needle));
    expect(at("Tesla")).toBeGreaterThan(at("SEC Regulation Crypto Assets"));
  });

  it("filters the calendar by group and remembers the choice", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const { unmount } = render(<CatalystsPanel />);
    const earnings = screen.getByRole("button", { name: /Earnings\s*3/ });
    await user.click(earnings);
    expect(earnings).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByText("September Employment Situation / NFP")).toBeNull();
    expect(screen.getByText("Tesla Q3 2026 earnings")).toBeInTheDocument();
    expect(window.localStorage.getItem("macro.catalysts.group")).toBe("earnings");

    unmount();
    render(<CatalystsPanel />);
    expect(
      screen.getByRole("button", { name: /Earnings\s*3/ }),
    ).toHaveAttribute("aria-pressed", "true");
  });

  it("links every tail risk and holding driver to its source", () => {
    render(<CatalystsPanel />);
    expect(
      screen.getAllByRole("link", { name: /reuters\.com/ }).map((a) => a.getAttribute("href")),
    ).toContain("https://www.reuters.com");
    expect(
      screen.getAllByRole("link", { name: /investing\.com/ }).length,
    ).toBeGreaterThan(0);
  });

  it("keeps the old watchlist moves, dated to their session, until the holdings watch has run", () => {
    macro.holdings = [];
    render(<CatalystsPanel />);
    expect(screen.getByText(/The holdings watch has not run yet/)).toBeInTheDocument();
    expect(screen.getAllByText("Fri 18 Sept session").length).toBeGreaterThan(0);
  });

  it("shows your holdings, material moves first, once the watch has run", () => {
    render(<CatalystsPanel />);
    expect(screen.queryByText(/The holdings watch has not run yet/)).toBeNull();
    expect(screen.getByText(/12 names · watch ran 09:07 today/)).toBeInTheDocument();
    const symbols = [...document.querySelectorAll(".hold-row .hold-symbol")].map(
      (b) => b.textContent,
    );
    // APP (+6.84) and GOOG (-1.41) are material; HYPE (+4.37) leads the rest.
    expect(symbols.slice(0, 3)).toEqual(["APP", "GOOG", "HYPE"]);
    // No change to rank NEKO by, so it sorts last.
    expect(symbols.at(-1)).toBe("NEKO");
  });

  it("names the basis of each move and says so when there is none", () => {
    render(<CatalystsPanel />);
    const goog = screen.getByText("GOOG").closest("li")!;
    expect(within(goog).getByText("since last close")).toBeInTheDocument();
    expect(within(goog).getByText("Accounting")).toBeInTheDocument();
    expect(within(goog).getByText("-1.41%").closest("[title]")).toHaveAttribute(
      "title",
      expect.stringContaining("Yahoo chart API (GOOG)"),
    );
    const hype = screen.getByText("HYPE").closest("li")!;
    expect(within(hype).getByText("24h")).toBeInTheDocument();
    const option = screen.getByText("META 260925C00737500").closest("li")!;
    expect(within(option).getByText("META since last close")).toBeInTheDocument();
    const neko = screen.getByText("NEKO").closest("li")!;
    expect(within(neko).getByText("no change")).toBeInTheDocument();
    expect(within(neko).getByText(/did not trade on Hyperliquid/)).toBeInTheDocument();
    const tlt = screen.getByText("TLT").closest("li")!;
    expect(within(tlt).getByText(/^Quiet\./)).toBeInTheDocument();
    expect(within(screen.getByText("D05").closest("li")!).getByText("manual")).toBeInTheDocument();
    expect(within(screen.getByText("MU").closest("li")!).getByText("watching")).toBeInTheDocument();
  });

  it("lists what is coming up for a holding, with estimated dates marked", () => {
    render(<CatalystsPanel />);
    const goog = screen.getByText("GOOG").closest("li")!;
    expect(within(goog).getByText("Early Oct")).toBeInTheDocument();
    expect(within(goog).getByText("est.")).toBeInTheDocument();
    const mu = screen.getByText("MU").closest("li")!;
    expect(within(mu).getByText("Thu 1 Oct 04:30")).toBeInTheDocument();
    expect(
      within(mu).getByRole("link", { name: "Micron Q4 FY2026 earnings" }),
    ).toHaveAttribute("href", "https://investors.micron.com/");
  });

  it("says where each feed came from, and draws a failed one stale", () => {
    render(<CatalystsPanel />);
    const line = document.querySelector(".feed-line") as HTMLElement;
    expect(line).not.toHaveAttribute("aria-label");
    expect(line).toHaveTextContent("Where each feed last came from");
    expect(line).toHaveTextContent("Calendar: Obsidian note, 09:30 today");
    expect(line).toHaveTextContent("Holdings: OpenClaw, 09:07 today");
    const tail = within(line).getByText(/Tail risks:/).closest(".feed-item")!;
    expect(tail).toHaveClass("feed-stale");
    expect(tail).toHaveTextContent("failed, last good 21:00 yesterday");
    expect(tail).toHaveAttribute("title", expect.stringContaining("Blackswan Events.md"));
  });

  it("draws a tail risk's escalation trail from its checks", () => {
    macro.tailRisks = readingOf(clean.tailRisks, tailRisk).rows;
    macro.tailRiskHistory = readingOf(clean.tailRiskHistory, tailRiskCheck).rows;
    render(<CatalystsPanel />);
    const trails = [...document.querySelectorAll(".risk-trail")];
    for (const trail of trails) expect(trail).not.toHaveAttribute("aria-label");
    expect(trails[0]).toHaveTextContent("How the level has changed:");
    expect(trails[0]).toHaveTextContent("watch 9 Sept → elevated 14 Sept → acute 21 Sept");
    expect(trails[1]).toHaveTextContent("elevated 14 Sept → watch 21 Sept");
    // One level all along draws no trail.
    const funding = screen.getByText(/government-funding deadline/).closest("li")!;
    expect(funding.querySelector(".risk-trail")).toBeNull();
  });

  it("counts only what is still to come in the title", () => {
    const all = [
      ...readingOf(fx.catalysts, catalystWindow).rows,
      // Over twelve hours ago: still listed, dimmed, but not to come.
      row({
        catalystId: "c-past",
        name: "Earlier today",
        startsAt: "2026-09-20T13:30:00.000Z",
      }),
    ];
    macro.catalysts = all;
    const now = new Date("2026-09-21T02:00:00.000Z");
    const upcoming = all.filter(
      (one) => withinWindow(one, now) && !isPast(one, now),
    ).length;
    const shown = all.filter((one) => withinWindow(one, now)).length;
    expect(shown).toBeGreaterThan(upcoming);
    render(<CatalystsPanel />);
    expect(
      screen.getByText(`${upcoming} still to come in the next thirty days`),
    ).toBeInTheDocument();
  });

  it("orders scenario tags by number, so S2 comes before S10", () => {
    macro.catalysts = [
      row({
        catalystId: "c-x",
        name: "Bound event",
        startsAt: "2026-09-25T12:30:00.000Z",
        referencedBy: ["k10", "k2"],
      }),
    ];
    macro.scenarios = [
      { scenarioId: "s10", ordinal: 10 },
      { scenarioId: "s2", ordinal: 2 },
    ];
    macro.conditions = [
      { conditionId: "k10", ownerKind: "trigger", ownerId: "s10" },
      { conditionId: "k2", ownerKind: "trigger", ownerId: "s2" },
    ];
    render(<CatalystsPanel />);
    const bound = screen.getByText("Bound event").closest("li")!;
    expect(within(bound).getByText("S2 · S10")).toBeInTheDocument();
  });

  it("links a calendar row only to an http or https address", () => {
    macro.catalysts = [
      row({
        catalystId: "c-bad",
        name: "Scripted event",
        startsAt: "2026-09-25T12:30:00.000Z",
        sourceUrl: "javascript:alert(1)",
      }),
    ];
    macro.tailRisks = [
      {
        ...readingOf(fx.tailRisks, tailRisk).rows[0]!,
        sourceUrl: "data:text/html,hi",
      },
    ];
    render(<CatalystsPanel />);
    expect(screen.getByText("Scripted event").closest("a")).toBeNull();
    for (const a of document.querySelectorAll("a[href]")) {
      expect(a.getAttribute("href")).not.toMatch(/^(javascript|data):/i);
    }
  });
});
