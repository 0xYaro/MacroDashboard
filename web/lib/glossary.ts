/**
 * Plain one-line definitions for the market terms the page has to keep.
 *
 * The reader is a smart friend who does not work in finance. A term like VIX
 * or bid-to-cover stays on the page because it is the name of the thing being
 * measured, so the page explains it in place instead of renaming it.
 *
 * Each entry matches on series keys first, because a key is stable, and on the
 * label text second, because a scenario target carries only its authored
 * label. Order matters: the first entry that matches wins, so the narrower
 * entries sit above the broader ones (breadth before the 200-day average,
 * auction high yield before Treasury yields).
 *
 * Definitions are written in our own words. Investor.gov's glossary was the
 * reference for the plain meaning. Keep each one short: one or two sentences,
 * no em dashes, no jargon inside the explanation.
 */

export interface GlossaryEntry {
  /** A stable id, for tests and for linking. */
  id: string;
  /** The term as the reader sees it. */
  term: string;
  /** One or two short plain sentences. */
  definition: string;
  /** Series keys this entry explains. A string matches exactly. */
  keys?: readonly (string | RegExp)[];
  /** Label patterns, tried when no key matches. */
  labels?: readonly RegExp[];
}

export const GLOSSARY: readonly GlossaryEntry[] = [
  {
    id: "vix",
    term: "VIX",
    definition:
      "A fear gauge for US stocks. It shows how much traders expect the S&P 500 to swing over the next month. Higher means more fear.",
    keys: ["vix"],
    labels: [/^VIX\b/i],
  },
  {
    id: "move",
    term: "MOVE index",
    definition:
      "The bond market's version of the VIX. It shows how much traders expect US Treasury yields to swing. Higher means more stress.",
    keys: ["move"],
    labels: [/\bMOVE\b/],
  },
  {
    id: "hy_oas",
    term: "HY spread (OAS)",
    definition:
      "The extra interest riskier, lower-rated companies pay to borrow compared with the US government, in basis points. It rises when lenders get nervous.",
    keys: ["hy_oas_bps"],
    labels: [/\bHY\b/i, /high[- ]yield (credit )?spread/i, /junk/i],
  },
  {
    id: "ig_oas",
    term: "IG spread (OAS)",
    definition:
      "The extra interest safer, investment-grade companies pay to borrow compared with the US government, in basis points. It widens when investors worry about credit.",
    keys: ["ig_oas_bps"],
    labels: [/\bIG\b/i, /investment[- ]grade/i],
  },
  {
    id: "breadth",
    term: "Breadth",
    definition:
      "The share of S&P 500 stocks trading above their 200-day average. High means most stocks are rising together. Low means a few big stocks are carrying the index.",
    keys: ["breadth_spx_above_200dma"],
    labels: [/breadth/i, /%\s*S&P above 200/i],
  },
  {
    id: "dma200",
    term: "200-day average",
    definition:
      "The average closing price over the last 200 trading days, about ten months. A price above it is in a long uptrend.",
    labels: [/200[- ]?(dma|day)/i],
  },
  {
    id: "ma50w",
    term: "50-week average",
    definition:
      "Bitcoin's average weekly close over the last 50 weeks, about a year. Staying above it is a sign of a long uptrend.",
    keys: ["btc_50w_ma"],
    labels: [/50[- ]week/i],
  },
  {
    id: "pce",
    term: "PCE inflation",
    definition:
      "The Fed's preferred measure of how fast consumer prices are rising. Core leaves out food and energy, which jump around. MoM is the change from last month.",
    keys: [/^pce_/],
    labels: [/\bPCE\b/],
  },
  {
    id: "cpi",
    term: "CPI inflation",
    definition:
      "The Consumer Price Index: the best-known measure of how fast everyday prices are rising. Core leaves out food and energy. MoM is the change from last month.",
    keys: [/^cpi_/],
    labels: [/\bCPI\b/],
  },
  {
    id: "payrolls",
    term: "Nonfarm payrolls",
    definition:
      "How many jobs US employers added last month, from the monthly jobs report. Also called NFP.",
    keys: ["payrolls_change"],
    labels: [/payrolls/i, /\bNFP\b/],
  },
  {
    id: "jolts",
    term: "JOLTS job openings",
    definition:
      "A monthly government count of jobs employers are trying to fill. Fewer openings means hiring is cooling.",
    keys: ["jolts_openings"],
    labels: [/\bJOLTS\b/],
  },
  {
    id: "ism",
    term: "ISM survey (PMI)",
    definition:
      "A monthly survey of company buyers. Above 50 means the sector is growing. Below 50 means it is shrinking.",
    keys: [/^ism_/],
    labels: [/\bISM\b/, /\bPMI\b/],
  },
  {
    id: "retail_control",
    term: "Retail sales control group",
    definition:
      "Retail sales without cars, gas, building materials and restaurants. It is the part that feeds straight into GDP.",
    keys: ["retail_control_mom"],
    labels: [/control group/i],
  },
  {
    id: "fedwatch",
    term: "FedWatch",
    definition:
      "A CME tool that turns futures prices into the market's odds of a Fed rate change at the next meeting.",
    keys: ["fedwatch_hike_prob_next", "fedwatch_next_fomc"],
    labels: [/FedWatch/i],
  },
  {
    id: "fomc",
    term: "FOMC",
    definition:
      "The Federal Open Market Committee: the Fed group that meets eight times a year to set US interest rates.",
    keys: ["next_fomc"],
    labels: [/\bFOMC\b/],
  },
  {
    id: "sep",
    term: "SEP and dot plot",
    definition:
      "The Fed's forecasts, published four times a year. The dot plot inside it shows where each official expects interest rates to go.",
    labels: [/\bSEP\b/, /dot plot|\bdots\b/i],
  },
  {
    id: "fed_funds",
    term: "Fed funds rate",
    definition:
      "The Fed's main interest rate: what banks charge each other for overnight loans. Most other rates follow it.",
    keys: ["fed_funds"],
    labels: [/fed funds/i],
  },
  {
    id: "bid_to_cover",
    term: "Bid-to-cover",
    definition:
      "At a Treasury auction, the dollars bid for every dollar of bonds sold. Higher means stronger demand.",
    keys: [/^auction_bid_to_cover_/],
    labels: [/bid[- ]to[- ]cover/i],
  },
  {
    id: "auction_high_yield",
    term: "Auction high yield",
    definition:
      "The highest interest rate the Treasury had to accept to sell all the bonds at an auction. Higher than expected means demand was weak.",
    keys: [/^auction_high_yield_/],
    labels: [/auction high yield/i],
  },
  {
    id: "auction_tail",
    term: "Auction tail",
    definition:
      "How far an auction's final yield came in above the market price just before it. A big tail means buyers were scarce.",
    labels: [/\btail\b/i],
  },
  {
    id: "treasury_yield",
    term: "Treasury yield",
    definition:
      "The yearly interest the US government pays to borrow for that many years. When it rises, borrowing gets dearer across the economy.",
    keys: [/^ust_\d+y$/],
    labels: [/^\d+Y (Treasury )?yield/i],
  },
  {
    id: "spread_2s10s",
    term: "2s10s",
    definition:
      "The 10-year Treasury yield minus the 2-year one. Below zero (inverted) has often come before recessions.",
    labels: [/2s10s/i],
  },
  {
    id: "basis_point",
    term: "Basis point",
    definition:
      "One hundredth of a percentage point. 25 basis points is 0.25%.",
    labels: [/basis point|\bbps?\b/i],
  },
  {
    id: "dxy",
    term: "Dollar index (DXY)",
    definition:
      "The US dollar's value against six major currencies, led by the euro. Higher means a stronger dollar.",
    keys: ["dxy"],
    labels: [/\bDXY\b/, /dollar index/i],
  },
  {
    id: "brent",
    term: "Brent crude",
    definition:
      "The global benchmark price for a barrel of oil, in US dollars.",
    keys: ["brent"],
    labels: [/brent/i],
  },
  {
    id: "wti",
    term: "WTI crude",
    definition: "The US benchmark price for a barrel of oil, in US dollars.",
    keys: ["wti"],
    labels: [/\bWTI\b/],
  },
  {
    id: "russell",
    term: "Russell 2000",
    definition:
      "An index of 2,000 smaller US companies. It tends to react more than the S&P 500 to interest rates and the economy.",
    keys: ["rut_cash", "rut_front"],
    labels: [/russell/i],
  },
  {
    id: "net_liquidity",
    term: "Net liquidity",
    definition:
      "A rough gauge of spare cash in the financial system: the Fed's balance sheet minus the Treasury's cash account and money parked at the Fed overnight. Rising tends to help markets.",
    keys: ["net_liquidity"],
    labels: [/net liquidity/i],
  },
  {
    id: "cftc",
    term: "CFTC positioning",
    definition:
      "Weekly US regulator data on how big investors are betting in S&P 500 futures. Net long means more bets on a rise than on a fall.",
    keys: [/^cftc_/],
    labels: [/\bCFTC\b/],
  },
  {
    id: "naaim",
    term: "NAAIM exposure",
    definition:
      "A weekly survey of how much of their clients' money active US fund managers hold in stocks, from 0 to 200%.",
    labels: [/NAAIM/i],
  },
  {
    id: "state_street",
    term: "State Street indicators",
    definition:
      "Monthly readings from State Street of how much big investors hold in stocks and how much risk they are willing to take.",
    keys: [/^state_street_/],
    labels: [/state street/i],
  },
  {
    id: "fear_greed",
    term: "Fear & Greed",
    definition:
      "A 0 to 100 mood score built from market signals. Low is fear, high is greed. There is one for stocks and one for crypto.",
    keys: ["cnn_fear_greed", "crypto_fear_greed"],
    labels: [/fear\s*(&|and)\s*greed|\bF&G\b/i],
  },
  {
    id: "etf_daily_flow",
    term: "ETF daily flow",
    definition:
      "Money going into (positive) or out of (negative) the US funds that hold bitcoin or ether, on one trading day.",
    keys: [/_etf_net_flow$/],
    labels: [/ETF daily flow/i],
  },
  {
    id: "etf_flows",
    term: "ETF flows",
    definition:
      "Money going into (positive) or out of (negative) the US funds that hold bitcoin or ether, added up over the week.",
    keys: [/_etf_weekly_flow$/],
    labels: [/ETF (weekly )?flow/i],
  },
  {
    id: "etf_streak",
    term: "ETF inflow streak",
    definition:
      "How many trading days in a row those funds have taken in money (positive) or lost it (negative).",
    keys: [/_etf_inflow_streak$/],
    labels: [/inflow streak/i],
  },
  {
    id: "funding_rate",
    term: "Funding rate",
    definition:
      "The fee crypto futures traders pay each other to keep a bet open. Positive means buyers are paying, a sign of eager borrowed bets on a rise.",
    keys: ["funding_skew", "funding_extremes"],
    labels: [/funding/i],
  },
  {
    id: "open_interest",
    term: "Open interest",
    definition:
      "The total value of crypto futures bets still open. Rising means more money, often borrowed, is riding on the price.",
    keys: ["crypto_open_interest"],
    labels: [/open interest|\bOI\b/i],
  },
  {
    id: "liquidations",
    term: "Liquidations",
    definition:
      "Borrowed crypto bets that exchanges closed by force because the trader ran out of collateral, over the last 24 hours.",
    keys: ["liquidations_24h"],
    labels: [/liquidation/i],
  },
  {
    id: "btc_dominance",
    term: "BTC dominance",
    definition: "Bitcoin's share of the total value of all cryptocurrencies.",
    keys: ["btc_dominance"],
    labels: [/dominance/i],
  },
  {
    id: "stablecoins",
    term: "Stablecoin market cap",
    definition:
      "The total value of stablecoins, crypto tokens pegged to the US dollar. Growth means more cash is waiting to buy crypto.",
    keys: ["stablecoin_mcap"],
    labels: [/stablecoin/i],
  },
  {
    id: "defi_tvl",
    term: "DeFi TVL",
    definition:
      "Total value locked: the money deposited in crypto lending and trading apps that run without a bank or broker.",
    keys: ["defi_tvl"],
    labels: [/\bTVL\b/],
  },
  {
    id: "alt_season",
    term: "Alt season index",
    definition:
      "A 0 to 100 score of how many large coins other than bitcoin beat it over the last 90 days. High means those coins are leading.",
    keys: ["alt_season_index"],
    labels: [/alt ?season/i],
  },
  {
    id: "leadership",
    term: "Leadership",
    definition:
      "Which stocks or sectors are doing the most to push the market up.",
    labels: [/^leadership$/i],
  },
];

function keyMatches(pattern: string | RegExp, key: string): boolean {
  return typeof pattern === "string" ? pattern === key : pattern.test(key);
}

/**
 * The definition for a row, by series key first and label second.
 *
 * Returns null when nothing matches, and the caller renders the label plain.
 * An unmatched label is the normal case for a plain name like `Gold`.
 */
export function glossaryFor({
  key,
  label,
}: {
  key?: string | null;
  label?: string | null;
}): GlossaryEntry | null {
  if (key) {
    const byKey = GLOSSARY.find((entry) =>
      (entry.keys ?? []).some((pattern) => keyMatches(pattern, key)),
    );
    if (byKey !== undefined) return byKey;
  }
  if (label) {
    const byLabel = GLOSSARY.find((entry) =>
      (entry.labels ?? []).some((pattern) => pattern.test(label)),
    );
    if (byLabel !== undefined) return byLabel;
  }
  return null;
}
