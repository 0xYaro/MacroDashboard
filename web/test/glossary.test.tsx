import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { GlossaryTerm } from "@/components/GlossaryTerm";
import { GLOSSARY, glossaryFor } from "@/lib/glossary";

/**
 * The glossary explains the terms the page has to keep, and the tooltip that
 * carries it is reachable by mouse, keyboard, tap and screen reader.
 */

describe("glossaryFor", () => {
  it("matches on the series key before the label", () => {
    expect(glossaryFor({ key: "vix", label: "anything" })?.id).toBe("vix");
    expect(glossaryFor({ key: "hy_oas_bps" })?.id).toBe("hy_oas");
    expect(glossaryFor({ key: "breadth_spx_above_200dma" })?.id).toBe(
      "breadth",
    );
    expect(glossaryFor({ key: "auction_bid_to_cover_10y" })?.id).toBe(
      "bid_to_cover",
    );
    expect(glossaryFor({ key: "auction_high_yield_30y" })?.id).toBe(
      "auction_high_yield",
    );
    expect(glossaryFor({ key: "ust_10y" })?.id).toBe("treasury_yield");
    expect(glossaryFor({ key: "pce_core_mom" })?.id).toBe("pce");
    expect(glossaryFor({ key: "crypto_fear_greed" })?.id).toBe("fear_greed");
    expect(glossaryFor({ key: "btc_etf_weekly_flow" })?.id).toBe("etf_flows");
    expect(glossaryFor({ key: "eth_etf_net_flow" })?.id).toBe(
      "etf_daily_flow",
    );
  });

  it("falls back to the label, as a scenario target carries only its label", () => {
    expect(glossaryFor({ label: "% S&P above 200dma" })?.id).toBe("breadth");
    expect(glossaryFor({ label: "HY credit spread (OAS)" })?.id).toBe("hy_oas");
    expect(glossaryFor({ label: "10Y Treasury Yield" })?.id).toBe(
      "treasury_yield",
    );
    expect(glossaryFor({ label: "BTC ETF weekly flow" })?.id).toBe(
      "etf_flows",
    );
    expect(glossaryFor({ label: "ETH ETF daily flow" })?.id).toBe(
      "etf_daily_flow",
    );
    expect(glossaryFor({ label: "Brent Crude (BZ=F)" })?.id).toBe("brent");
    expect(glossaryFor({ label: "Leadership" })?.id).toBe("leadership");
  });

  it("returns null for a plain name, which renders as it is", () => {
    expect(glossaryFor({ key: "gold", label: "Gold (GC=F)" })).toBeNull();
    expect(glossaryFor({ label: "S&P 500 (cash)" })).toBeNull();
  });

  it("keeps every definition short and free of em dashes", () => {
    const ids = new Set<string>();
    for (const entry of GLOSSARY) {
      expect(ids.has(entry.id)).toBe(false);
      ids.add(entry.id);
      expect(entry.definition).not.toContain(String.fromCharCode(0x2014));
      expect(entry.definition.split(/\s+/).length).toBeLessThanOrEqual(40);
    }
  });
});

describe("GlossaryTerm", () => {
  const vix = glossaryFor({ key: "vix" });

  it("renders the label plain when there is no entry", () => {
    render(<GlossaryTerm entry={null}>Gold</GlossaryTerm>);
    expect(screen.getByText("Gold").tagName).not.toBe("BUTTON");
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("gives the term an accessible description and a keyboard stop", () => {
    render(<GlossaryTerm entry={vix}>VIX</GlossaryTerm>);
    const term = screen.getByRole("button", { name: "VIX" });
    expect(term).toHaveAccessibleDescription(/fear gauge/i);
    expect(screen.getByRole("tooltip", { hidden: true })).not.toBeVisible();
  });

  it("toggles on a tap and closes on Escape", () => {
    render(<GlossaryTerm entry={vix}>VIX</GlossaryTerm>);
    const term = screen.getByRole("button", { name: "VIX" });
    fireEvent.click(term);
    expect(screen.getByRole("tooltip")).toBeVisible();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.getByRole("tooltip", { hidden: true })).not.toBeVisible();
    fireEvent.click(term);
    expect(screen.getByRole("tooltip")).toBeVisible();
    fireEvent.click(term);
    expect(screen.getByRole("tooltip", { hidden: true })).not.toBeVisible();
  });

  it("closes when a tap lands anywhere else", () => {
    render(
      <>
        <GlossaryTerm entry={vix}>VIX</GlossaryTerm>
        <p>elsewhere</p>
      </>,
    );
    fireEvent.click(screen.getByRole("button", { name: "VIX" }));
    expect(screen.getByRole("tooltip")).toBeVisible();
    fireEvent.pointerDown(screen.getByText("elsewhere"));
    expect(screen.getByRole("tooltip", { hidden: true })).not.toBeVisible();
  });
});
