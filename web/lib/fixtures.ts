/**
 * Two datasets built from the real 2026-W39 reading, and why they are in wire
 * shape rather than in app shape.
 *
 * Fixture mode exists so the whole surface can be looked at, and judged, with
 * no Supabase project. That is only worth anything if the fixtures travel the
 * same road the real rows do, so every row here is raw PostgREST JSON:
 * `numeric` as a string, `timestamptz` as an ISO string, `date` as
 * `YYYY-MM-DD`, snake_case keys. They go through `lib/contract.ts` exactly as a
 * live row does, which means a validator that would reject production data
 * rejects the fixture too, in development, where it is cheap to notice.
 *
 * The numbers are real. Every level, every probability, every band edge, every
 * ledger row and all twelve rules are the 2026-W39 journal's own, so the app
 * can be judged on content rather than on lorem ipsum. Made-up numbers would
 * have made every density and column-width decision in this app untestable.
 *
 * **Dataset A, `clean`** — every series fresh or on cadence, every condition
 * evaluated, no gap, no void clause, no refused gate. This is what a good
 * Monday looks like.
 *
 * **Dataset B, `honest`** — the same week with the six cases this product
 * exists to render truthfully:
 *
 * 1. A weekly series between prints. The CFTC pair is as-of Tuesday and
 *    released Friday, so on a Wednesday it reads `due`, not `overdue` and not
 *    stale.
 * 2. A permanent gap on a point-in-time series. CNN's own API answers 451/403,
 *    so the Friday reading was missed and can never be recovered.
 * 3. A condition that is `unevaluable`, because the series it names is the one
 *    with the gap. It is never scored `not yet`.
 * 4. A scenario flagged `premise moved`, with a void clause. The Senior Credit
 *    Officer survey it named was rescheduled out of the horizon; the clause
 *    stays in the denominator and the threshold is not renormalised.
 * 5. A setup whose band was touched and whose gate refused. That happened in
 *    W37, W38 and W39, and each time it was found the following Monday.
 * 6. A search-agent series that did not run, reading `overdue`, beside an
 *    asserted series that is not a complaint at all.
 *
 * Dev-only. Nothing imports this module unless `NEXT_PUBLIC_FIXTURES=1`.
 */

export type FixtureDataset = "clean" | "honest";

export const FIXTURE_DATASETS: readonly {
  id: FixtureDataset;
  name: string;
}[] = [
  { id: "clean", name: "W39 · all evaluable" },
  { id: "honest", name: "W39 · the honesty cases" },
];

/** A numeric, as PostgREST serialises one. */
function n(value: number, decimals = 2): string {
  return value.toFixed(decimals);
}

/* ------------------------------------------------------------- the clock -- */

/**
 * The week the fixtures describe, as calendar days.
 *
 * Fixed dates rather than dates relative to load, unlike Accounting's. The
 * whole point of this screen is that a weekly series as-of Tuesday and released
 * Friday is current on Wednesday, and that claim is only checkable against
 * named days. A fixture whose as-of slid forward with the clock would make the
 * six release rules indistinguishable.
 */
const FRI = "2026-09-18"; // the US close every traditional row is as-of
const MON = "2026-09-21"; // the authoring date, and the live crypto as-of
const TUE_CFTC = "2026-09-15"; // CFTC positions as-of
const WED_FRED = "2026-09-17"; // FRED's last row, one business day behind
const WALCL = "2026-09-16"; // the H.4.1 balance sheet
const AUG = "2026-08-31"; // State Street's monthly print
const TUE_NEXT = "2026-09-22"; // the next calendar day, for the 24-hour series

/** The four journal headings, verbatim, as `config/series.yaml` spells them. */
const G_PRICE = "Price & Levels";
const G_SENT = "Sentiment & Positioning";
const G_FLOWS = "Flows & Structure";
const G_MACRO = "Macro & Fiscal State";

/**
 * The order the four groups are rendered in.
 *
 * Asserted, like `ordinal` inside a group. `table_group` is a string and a
 * renderer that sorted groups alphabetically would put Flows above Price and
 * make the snapshot's shape depend on a spelling.
 */
export const GROUP_ORDER: readonly string[] = [
  G_PRICE,
  G_SENT,
  G_FLOWS,
  G_MACRO,
];

/** The 09:30 SGT full run on the authoring Monday, in UTC. */
const FULL_RUN = "2026-09-21T01:30:00.000Z";
/** The 21:00 SGT deterministic rescore that evening, in UTC. */
const RESCORE = "2026-09-21T13:00:00.000Z";

const WEEK = "2026-W39";

/**
 * The equities sector-focus rows, by band.
 *
 * Shared between `strategy_state.sector_focus` and the `sector_focus_label`
 * that migration 0011 resolves onto `setup_state`, so the two cannot drift.
 * In the database they are one row read two ways; here they are one constant
 * read two ways, which is the same guarantee.
 */
const FOCUS_TEXT: Readonly<Record<string, string>> = {
  CRITICAL:
    "Memory and HBM: a shortage now confirmed by the buyer rather than the sellers, which makes Micron on 30 September the complex's print.",
  HOLD: "Deposit-funded Asian banks, which earn a curve that is bulging rather than inverting, plus energy while the East-West line is only half restored.",
  WATCH:
    "Debt-funded AI infrastructure. Nebius raising GPU prices 17-41% is realised pricing power, but Nscale's billion-dollar half-year loss at a $30bn ask is the same capital market seen from the other side.",
  AVOID:
    "Rate-sensitive small caps and the discretionary consumer, where the Russell fell again and a headline retail beat was carried by gasoline and pre-tariff auto pull-forward.",
};

/* ============================================================== series === */

interface SeriesSeed {
  key: string;
  label: string;
  group: string;
  /** Unique inside the group. The only declaration of row order. */
  ordinal: number;
  /** `null` is a reading that could not be measured. Never zero. */
  value: number | null;
  display: string;
  unit: string | null;
  decimals: number;
  cadence: "session" | "daily" | "weekly" | "monthly" | "event";
  deltaNative: number | null;
  deltaLabel: string | null;
  deltaWeekOpen: number | null;
  asof: string | null;
  released?: string | null;
  nextExpected?: string | null;
  tier: number;
  source: string;
  /** Defaults to `fresh`; the honest dataset overrides a handful of these. */
  freshness?: string;
  note?: string;
  text?: boolean;
  extra?: Record<string, unknown>;
}

/**
 * Every declared series, with its real 2026-W39 reading.
 *
 * Keys, labels, ordinals, units, decimals and cadences are `config/series.yaml`
 * verbatim: 47 series across the journal's four headings, 19 / 12 / 11 / 5.
 * `ordinal` is unique inside its group and is the only declaration of row
 * order, so the renderer sorts by it and never alphabetically.
 *
 * Six release rules sit in this one table: a US session, a US business day, a
 * calendar day, a weekly Tuesday-as-of Friday-release, a weekly Wednesday
 * balance sheet, and a monthly data month released mid the following month.
 * Rendering all of them without any of them looking stale when they are merely
 * not due yet is the hardest thing this screen does, and this table is the test
 * of it.
 *
 * `display` is the worker's own formatting and the renderer prefers it over
 * anything it could compute. Net liquidity is stored in USD with zero decimals,
 * so its `value` is 5754716000000 and its `display` is `$5.755T`; a renderer
 * that formatted the number itself would print thirteen digits in a twelve-wide
 * column and would have to learn a per-series unit table to avoid it.
 */
const SERIES: readonly SeriesSeed[] = [
  /* -------------------------------------------------- Price & Levels (19) */
  { key: "spx_front", label: "S&P 500 (ES=F)", group: G_PRICE, ordinal: 1, value: 7657.35, display: "7,657.35", unit: "index_points", decimals: 2, cadence: "session", deltaNative: -0.08, deltaLabel: "1d %", deltaWeekOpen: -0.03, asof: FRI, nextExpected: MON, tier: 1, source: "Yahoo chart API (ES=F)", extra: { session_high: 7672.5, session_low: 7611.25 } },
  { key: "spx_cash", label: "S&P 500 (cash)", group: G_PRICE, ordinal: 2, value: 7650.5, display: "7,650.50", unit: "index_points", decimals: 2, cadence: "session", deltaNative: -0.09, deltaLabel: "1d %", deltaWeekOpen: -0.08, asof: FRI, nextExpected: MON, tier: 1, source: "Yahoo chart API (^GSPC)", note: "Every condition, band and setup level is written against the cash index, never the future. R1 exists because the two were once conflated.", extra: { dma200_pct: 6.51, dma200_slope: "rising", session_high: 7672.11, session_low: 7607.44 } },
  { key: "dow_front", label: "Dow Jones (YM=F)", group: G_PRICE, ordinal: 3, value: 51761, display: "51,761.00", unit: "index_points", decimals: 2, cadence: "session", deltaNative: -0.39, deltaLabel: "1d %", deltaWeekOpen: -1.57, asof: FRI, nextExpected: MON, tier: 1, source: "Yahoo chart API (YM=F)" },
  { key: "dow_cash", label: "Dow Jones (cash)", group: G_PRICE, ordinal: 4, value: 51682.64, display: "51,682.64", unit: "index_points", decimals: 2, cadence: "session", deltaNative: -0.44, deltaLabel: "1d %", deltaWeekOpen: -1.69, asof: FRI, nextExpected: MON, tier: 1, source: "Yahoo chart API (^DJI)", note: "Third consecutive losing week and the worst since March; the cyclical index is the one breaking." },
  { key: "nasdaq_cash", label: "Nasdaq Composite (cash)", group: G_PRICE, ordinal: 5, value: 26522.54, display: "26,522.54", unit: "index_points", decimals: 2, cadence: "session", deltaNative: 0.31, deltaLabel: "1d %", deltaWeekOpen: 0.72, asof: FRI, nextExpected: MON, tier: 1, source: "Yahoo chart API (^IXIC)", extra: { dma200_pct: 8.02, dma200_slope: "rising" } },
  { key: "rut_front", label: "Russell 2000 (RTY=F)", group: G_PRICE, ordinal: 6, value: 2873.23, display: "2,873.23", unit: "index_points", decimals: 2, cadence: "session", deltaNative: -0.55, deltaLabel: "1d %", deltaWeekOpen: -1.08, asof: FRI, nextExpected: MON, tier: 1, source: "Yahoo chart API (RTY=F)" },
  { key: "rut_cash", label: "Russell 2000 (cash)", group: G_PRICE, ordinal: 7, value: 2860.4, display: "2,860.40", unit: "index_points", decimals: 2, cadence: "session", deltaNative: -0.62, deltaLabel: "1d %", deltaWeekOpen: -1.5, asof: FRI, nextExpected: MON, tier: 1, source: "Yahoo chart API (^RUT)" },
  { key: "btc", label: "BTC", group: G_PRICE, ordinal: 8, value: 81492, display: "$81,492", unit: "usd", decimals: 0, cadence: "daily", deltaNative: 1.24, deltaLabel: "1d %", deltaWeekOpen: 5.08, asof: MON, nextExpected: TUE_NEXT, tier: 1, source: "CoinGecko market_chart", note: "A calendar-day series: there are no closed days, so a Monday reading beside a Friday equity close is two honest as-ofs rather than one stale one.", extra: { session_high: 81911, session_low: 79730 } },
  { key: "eth", label: "ETH", group: G_PRICE, ordinal: 9, value: 2667, display: "$2,667", unit: "usd", decimals: 0, cadence: "daily", deltaNative: 0.88, deltaLabel: "1d %", deltaWeekOpen: 6.2, asof: MON, nextExpected: TUE_NEXT, tier: 1, source: "CoinGecko market_chart", extra: { session_high: 2692, session_low: 2611 } },
  { key: "sol", label: "SOL", group: G_PRICE, ordinal: 10, value: 111.72, display: "$111.72", unit: "usd", decimals: 2, cadence: "daily", deltaNative: 2.14, deltaLabel: "1d %", deltaWeekOpen: 10.68, asof: MON, nextExpected: TUE_NEXT, tier: 1, source: "CoinGecko market_chart" },
  { key: "gold", label: "Gold", group: G_PRICE, ordinal: 11, value: 4424.9, display: "$4,424.90", unit: "usd_per_oz", decimals: 2, cadence: "session", deltaNative: 0.18, deltaLabel: "1d %", deltaWeekOpen: 0.36, asof: FRI, nextExpected: MON, tier: 1, source: "Yahoo chart API (GC=F)" },
  { key: "silver", label: "Silver", group: G_PRICE, ordinal: 12, value: 66.56, display: "$66.56", unit: "usd_per_oz", decimals: 2, cadence: "session", deltaNative: 0.71, deltaLabel: "1d %", deltaWeekOpen: 3.1, asof: FRI, nextExpected: MON, tier: 1, source: "Yahoo chart API (SI=F)", note: "Outran gold eight to one, which is an industrial bid rather than a hedging bid." },
  { key: "wti", label: "WTI crude", group: G_PRICE, ordinal: 13, value: 100.3, display: "$100.30", unit: "usd_per_bbl", decimals: 2, cadence: "session", deltaNative: -0.34, deltaLabel: "1d %", deltaWeekOpen: 0.25, asof: FRI, nextExpected: MON, tier: 1, source: "Yahoo chart API (CL=F)" },
  { key: "brent", label: "Brent crude", group: G_PRICE, ordinal: 14, value: 103.87, display: "$103.87", unit: "usd_per_bbl", decimals: 2, cadence: "session", deltaNative: -0.41, deltaLabel: "1d %", deltaWeekOpen: -0.71, asof: FRI, nextExpected: MON, tier: 1, source: "Yahoo chart API (BZ=F)" },
  { key: "dxy", label: "Dollar index (DXY)", group: G_PRICE, ordinal: 15, value: 100.22, display: "100.220", unit: "index_points", decimals: 3, cadence: "session", deltaNative: 0.24, deltaLabel: "1d %", deltaWeekOpen: 1.11, asof: FRI, nextExpected: MON, tier: 1, source: "Yahoo chart API (DX-Y.NYB)" },
  { key: "vix", label: "VIX", group: G_PRICE, ordinal: 16, value: 14.81, display: "14.81", unit: "index_points", decimals: 2, cadence: "session", deltaNative: -0.63, deltaLabel: "1d pts", deltaWeekOpen: -1.03, asof: FRI, nextExpected: MON, tier: 1, source: "Yahoo chart API (^VIX)", note: "An 18.94 FOMC-day spike fully sold; the week's most uncomfortable number.", extra: { session_high: 18.94 } },
  { key: "ust_30y", label: "30Y yield", group: G_PRICE, ordinal: 17, value: 5.34, display: "5.34%", unit: "percent", decimals: 2, cadence: "daily", deltaNative: -0.01, deltaLabel: "1d pp", deltaWeekOpen: -0.01, asof: FRI, nextExpected: MON, tier: 1, source: "US Treasury daily par curve" },
  { key: "ust_10y", label: "10Y yield", group: G_PRICE, ordinal: 18, value: 5.01, display: "5.01%", unit: "percent", decimals: 2, cadence: "daily", deltaNative: 0.02, deltaLabel: "1d pp", deltaWeekOpen: 0.05, asof: FRI, nextExpected: MON, tier: 1, source: "US Treasury daily par curve", note: "Closed above 5% for the first time since 2007 after a 5.04% intraweek print.", extra: { spread_2s10s_bps: 25 } },
  { key: "ust_2y", label: "2Y yield", group: G_PRICE, ordinal: 19, value: 4.76, display: "4.76%", unit: "percent", decimals: 2, cadence: "daily", deltaNative: 0.04, deltaLabel: "1d pp", deltaWeekOpen: 0.13, asof: FRI, nextExpected: MON, tier: 1, source: "US Treasury daily par curve", note: "The dots, not the hike, did this; 2s10s down to 25 bp as the curve bulges." },

  /* ------------------------------------------ Sentiment & Positioning (12) */
  // Fetched only on the 09:30 run, so on the 21:00 rescore it legitimately
  // carries Friday's as-of while `crypto_fear_greed` beside it carries Monday's.
  // Two rows, one table, two as-ofs, both fresh, and neither is a fault.
  { key: "cnn_fear_greed", label: "Fear & Greed, equities", group: G_SENT, ordinal: 1, value: 29, display: "29 (Fear)", unit: "index_0_100", decimals: 0, cadence: "daily", deltaNative: -1, deltaLabel: "1d pts", deltaWeekOpen: -4, asof: FRI, nextExpected: MON, tier: 1, source: "CNN Fear & Greed via aggregator (search agent)", note: "CNN's API answers 451, so two aggregators supply it and they agree within 0.6 points. Point-in-time: a missed day is a permanent gap." },
  { key: "crypto_fear_greed", label: "Fear & Greed, crypto", group: G_SENT, ordinal: 2, value: 70, display: "70 (Greed)", unit: "index_0_100", decimals: 0, cadence: "daily", deltaNative: 3, deltaLabel: "1d pts", deltaWeekOpen: 13, asof: MON, nextExpected: TUE_NEXT, tier: 1, source: "Alternative.me API", note: "Highest of the arc; the two books' sentiment has fully decoupled." },
  { key: "state_street_equity_allocation", label: "State Street equity allocation", group: G_SENT, ordinal: 3, value: 57.4, display: "57.4% (Aug)", unit: "percent", decimals: 1, cadence: "monthly", deltaNative: -0.13, deltaLabel: "MoM pp", deltaWeekOpen: 0, asof: AUG, released: "2026-09-15", nextExpected: "2026-10-15", tier: 2, source: "State Street Institutional Investor Indicators (asserted)", freshness: "asserted", note: "September data is released on 15 October. Hand-asserted rather than fetched, so it is a different kind of claim, not a complaint." },
  { key: "state_street_risk_appetite", label: "State Street risk appetite", group: G_SENT, ordinal: 4, value: 0.36, display: "+0.36", unit: "index_points", decimals: 2, cadence: "monthly", deltaNative: 0.18, deltaLabel: "MoM", deltaWeekOpen: 0, asof: AUG, released: "2026-09-15", nextExpected: "2026-10-15", tier: 2, source: "State Street Institutional Investor Indicators (asserted)", freshness: "asserted", note: "Fifth straight positive month and the third-strongest reading of 2026." },
  { key: "cftc_asset_managers_es", label: "CFTC asset managers, ES net", group: G_SENT, ordinal: 5, value: 36.98, display: "+36.98% of OI", unit: "pct_of_oi", decimals: 2, cadence: "weekly", deltaNative: -7.06, deltaLabel: "WoW pp", deltaWeekOpen: -7.06, asof: TUE_CFTC, released: FRI, nextExpected: "2026-09-25", tier: 1, source: "CFTC TFF API (gpe5-46if)", note: "Positions are dated Tuesday and released the following Friday. Neutral band, largest cut of the arc, still 10.4 pp above the sidelined threshold.", extra: { band: "neutral", open_interest_all: 2446519 } },
  { key: "cftc_leveraged_funds_es", label: "CFTC leveraged funds, ES net", group: G_SENT, ordinal: 6, value: -11.98, display: "-11.98% of OI", unit: "pct_of_oi", decimals: 2, cadence: "weekly", deltaNative: 4.48, deltaLabel: "WoW pp", deltaWeekOpen: 4.48, asof: TUE_CFTC, released: FRI, nextExpected: "2026-09-25", tier: 1, source: "CFTC TFF API (gpe5-46if)", note: "Neutral band, first cover of the arc; 2.0 pp from the -10% crowding threshold.", extra: { band: "neutral", open_interest_all: 2446519 } },
  { key: "breadth_spx_above_200dma", label: "S&P breadth, % above 200dma", group: G_SENT, ordinal: 7, value: 49.5, display: "49.50%", unit: "percent", decimals: 2, cadence: "daily", deltaNative: -0.79, deltaLabel: "1d pp", deltaWeekOpen: -6.96, asof: FRI, nextExpected: MON, tier: 2, source: "Investing.com S5TH (scraper)", note: "Through 50% for the first time in the arc. Roughly $6.2tn of options expired on this date, so the print carries that distortion.", extra: { path: [56.26, 52.88, 50.09, 50.29, 49.5] } },
  { key: "hy_oas_bps", label: "HY spread (OAS)", group: G_SENT, ordinal: 8, value: 270, display: "270 bps (2.70%)", unit: "bps", decimals: 0, cadence: "daily", deltaNative: 0, deltaLabel: "1d bps", deltaWeekOpen: 0, asof: WED_FRED, released: FRI, nextExpected: MON, tier: 1, source: "FRED BAMLH0A0HYM2 (fredgraph.csv)", note: "Stored in basis points, the unit every condition is written in. ICE publishes a day late, so the 17th is the freshest print, not a late one." },
  { key: "ig_oas_bps", label: "IG spread (OAS)", group: G_SENT, ordinal: 9, value: 78, display: "78 bps (0.78%)", unit: "bps", decimals: 0, cadence: "daily", deltaNative: 0, deltaLabel: "1d bps", deltaWeekOpen: -2, asof: WED_FRED, released: FRI, nextExpected: MON, tier: 1, source: "FRED BAMLC0A0CM (fredgraph.csv)", note: "Tightened while HY sat flat: credit refuses the contraction case." },
  { key: "move", label: "MOVE index", group: G_SENT, ordinal: 10, value: 80.64, display: "80.64", unit: "index_points", decimals: 2, cadence: "session", deltaNative: -0.44, deltaLabel: "1d pts", deltaWeekOpen: -1.57, asof: FRI, nextExpected: MON, tier: 1, source: "Yahoo chart API (^MOVE)", note: "First decline in four weeks, in the week the 10Y crossed 5%." },
  { key: "btc_dominance", label: "BTC dominance", group: G_SENT, ordinal: 11, value: 58.16, display: "58.16%", unit: "percent", decimals: 2, cadence: "daily", deltaNative: -0.12, deltaLabel: "1d pp", deltaWeekOpen: -0.67, asof: MON, nextExpected: TUE_NEXT, tier: 1, source: "CoinGecko global API" },
  { key: "funding_skew", label: "Funding skew", group: G_SENT, ordinal: 12, value: 0.005, display: "+0.0050%/8h", unit: "pct_per_8h", decimals: 4, cadence: "daily", deltaNative: 0, deltaLabel: "1d", deltaWeekOpen: 0, asof: MON, nextExpected: TUE_NEXT, tier: 1, source: "Binance Futures API", note: "The median across 859 listed perpetuals. BTC +0.0084, ETH +0.0094, SOL +0.0087: the rally carries no leverage premium.", extra: { n_perps: 859, btc: 0.0084, eth: 0.0094, sol: 0.0087 } },

  /* --------------------------------------------- Flows & Structure (11) -- */
  { key: "net_liquidity", label: "Net liquidity (Fed BS − TGA − RRP)", group: G_FLOWS, ordinal: 1, value: 5754716000000, display: "$5.755T", unit: "usd", decimals: 0, cadence: "weekly", deltaNative: -2.33, deltaLabel: "WoW %", deltaWeekOpen: -2.33, asof: WALCL, released: WED_FRED, nextExpected: "2026-09-24", tier: 1, source: "FRED WALCL, WTREGEN, RRPONTSYD", note: "Weekly on the H.4.1 Wednesday balance, released Thursday. The 15 September tax date drained it, as the prior week flagged.", extra: { walcl: "$6.747T", tga: "$0.992T", rrp: "$0.576B" } },
  { key: "btc_etf_weekly_flow", label: "BTC ETF weekly flow", group: G_FLOWS, ordinal: 2, value: 6210000, display: "+$6.2M", unit: "usd", decimals: 0, cadence: "daily", deltaNative: 468900000, deltaLabel: "WoW", deltaWeekOpen: 468900000, asof: FRI, released: "2026-09-19", nextExpected: MON, tier: 1, source: "SoSoValue via The Block (search agent)", note: "Smallest weekly net inflow in the products' 141 weeks of trading: a $433M Friday print cancelled four days of selling." },
  { key: "btc_etf_inflow_streak", label: "BTC ETF inflow streak", group: G_FLOWS, ordinal: 3, value: 2, display: "+2 sessions", unit: "sessions", decimals: 0, cadence: "daily", deltaNative: 6, deltaLabel: "WoW", deltaWeekOpen: 6, asof: FRI, released: "2026-09-19", nextExpected: MON, tier: 2, source: "SoSoValue via The Block (search agent)", note: "A signed session count: +2 in, -4 out. Two sessions is not a regime, and R10 exists because a streak was once extrapolated into one." },
  { key: "eth_etf_weekly_flow", label: "ETH ETF weekly flow", group: G_FLOWS, ordinal: 4, value: -140600000, display: "-$140.6M", unit: "usd", decimals: 0, cadence: "daily", deltaNative: -337700000, deltaLabel: "WoW", deltaWeekOpen: -337700000, asof: FRI, released: "2026-09-19", nextExpected: MON, tier: 1, source: "SoSoValue via The Block (search agent)", note: "Snapped a four-week, $1.94B inflow streak in the week ETH broke out on price." },
  { key: "eth_etf_inflow_streak", label: "ETH ETF inflow streak", group: G_FLOWS, ordinal: 5, value: 1, display: "+1 session", unit: "sessions", decimals: 0, cadence: "daily", deltaNative: 0, deltaLabel: "WoW", deltaWeekOpen: 0, asof: FRI, released: "2026-09-19", nextExpected: MON, tier: 2, source: "SoSoValue via The Block (search agent)", note: "Preceded by three outflow sessions, 15 to 17 September, worth -$404.8M." },
  { key: "stablecoin_mcap", label: "Stablecoin market cap", group: G_FLOWS, ordinal: 6, value: 310914460984, display: "$310.91B", unit: "usd", decimals: 0, cadence: "daily", deltaNative: -0.08, deltaLabel: "1d %", deltaWeekOpen: -0.37, asof: MON, nextExpected: TUE_NEXT, tier: 1, source: "DefiLlama stablecoins API", note: "USDT $183.30B, USDC $74.41B: no new dry powder arrived for the break.", extra: { usdt: "$183.30B", usdc: "$74.41B" } },
  { key: "defi_tvl", label: "DeFi TVL", group: G_FLOWS, ordinal: 7, value: 88124454738, display: "$88.12B", unit: "usd", decimals: 0, cadence: "daily", deltaNative: 0.42, deltaLabel: "1d %", deltaWeekOpen: 1.85, asof: MON, nextExpected: TUE_NEXT, tier: 1, source: "DefiLlama historicalChainTvl" },
  { key: "crypto_open_interest", label: "Crypto open interest", group: G_FLOWS, ordinal: 8, value: 146950000000, display: "$146.95B", unit: "usd", decimals: 0, cadence: "daily", deltaNative: 1.8, deltaLabel: "1d %", deltaWeekOpen: 12.06, asof: MON, nextExpected: TUE_NEXT, tier: 2, source: "CoinGecko derivatives top-10 proxy", note: "A proxy, never a measurement: the sum of open interest in BTC across the top ten exchanges, converted at spot. This series can never attain Tier 1.", extra: { oi_btc: 1803195.38, btc_px: 81492.07 } },
  { key: "funding_extremes", label: "Funding extremes", group: G_FLOWS, ordinal: 9, value: -0.4484, display: "CELRUSDT -0.448%/8h", unit: "pct_per_8h", decimals: 4, cadence: "daily", deltaNative: 0.9736, deltaLabel: "1d pp", deltaWeekOpen: 0.9736, asof: MON, nextExpected: TUE_NEXT, tier: 1, source: "Binance Futures API", note: "The most negative funding across all listed perpetuals. The crowded short tail collapsed by two-thirds; the squeeze fuel is spent.", extra: { most_negative: [["CELRUSDT", -0.4484], ["ONGUSDT", -0.3976]], most_positive: [["BNCUSDT", 0.2657], ["GPROUSDT", 0.2135]] } },
  { key: "liquidations_24h", label: "Liquidations, 24h", group: G_FLOWS, ordinal: 10, value: 514000000, display: "~$514M", unit: "usd", decimals: 0, cadence: "daily", deltaNative: -170000000, deltaLabel: "WoW", deltaWeekOpen: -170000000, asof: FRI, released: FRI, nextExpected: MON, tier: 1, source: "Coinglass via aggregator (search agent)", note: "Eight shorts liquidated for every long: the break was a squeeze, not accumulation. A rolling window, so a missed day is gone.", extra: { longs: 56000000, shorts: 458000000 } },
  { key: "alt_season_index", label: "Alt season index", group: G_FLOWS, ordinal: 11, value: 49, display: "49 (Neutral)", unit: "index_0_100", decimals: 0, cadence: "daily", deltaNative: 2, deltaLabel: "1d pts", deltaWeekOpen: 12, asof: MON, nextExpected: TUE_NEXT, tier: 2, source: "CoinMarketCap public API", note: "Still Neutral, but the largest weekly rise of the arc as SOL led." },

  /* -------------------------------------------- Macro & Fiscal State (5) - */
  { key: "fed_funds", label: "Fed funds rate", group: G_MACRO, ordinal: 1, value: null, display: "3.75%–4.00% after a unanimous 12–0 25 bp hike on 16 September, the first increase in more than three years. The SEP median puts year-end 2026 at 4.1% and 2027 at 4.1%, implying no cuts next year.", unit: null, decimals: 0, cadence: "event", deltaNative: null, deltaLabel: null, deltaWeekOpen: null, asof: "2026-09-16", released: "2026-09-16", nextExpected: null, tier: 1, source: "FOMC coverage (search agent)", text: true },
  { key: "next_fomc", label: "Next FOMC", group: G_MACRO, ordinal: 2, value: null, display: "27–28 October 2026. Statement 2:00 PM ET Wednesday 28 October, press conference 2:30 PM ET, and no SEP at this meeting: the 2026 projection meetings are March, June, September and December.", unit: null, decimals: 0, cadence: "event", deltaNative: null, deltaLabel: null, deltaWeekOpen: null, asof: FRI, released: null, nextExpected: null, tier: 2, source: "federalreserve.gov FOMC calendar (search agent)", text: true },
  { key: "fedwatch_next_fomc", label: "FedWatch quote, next FOMC", group: G_MACRO, ordinal: 3, value: null, display: "Hold ~49.1% and +25 bp ~50.9% for 28 October as quoted on 17 September, with ~88.5% odds of at least one further hike by December. A 20 September quote had the hike drifting to ~59.7%, so this line moves fast.", unit: null, decimals: 0, cadence: "daily", deltaNative: null, deltaLabel: null, deltaWeekOpen: null, asof: WED_FRED, released: null, nextExpected: MON, tier: 2, source: "CME FedWatch via aggregator (search agent)", text: true, note: "Its own series key. The meeting date does not move daily and this does, so the two could never have shared a row." },
  { key: "tariff_state", label: "Tariff regime", group: G_MACRO, ordinal: 4, value: null, display: "Unchanged and still escalating: a layered Section 232/301/338 regime, 100% on patented drugs and biologics, 25% on autos and parts, 10–50% on steel and aluminium, 100% on maritime cargo equipment, 25% on Brazil, and Canadian 15/25/50% retaliation live since 8 September. The dated item inside 30 days is the 100% pharma tariff extending to all non-Annex-III manufacturers on 29 September.", unit: null, decimals: 0, cadence: "event", deltaNative: null, deltaLabel: null, deltaWeekOpen: null, asof: FRI, released: "2026-09-10", nextExpected: null, tier: 2, source: "Tariff tracker coverage (asserted, search agent)", freshness: "asserted", text: true },
  { key: "geopolitical_state", label: "Geopolitical state", group: G_MACRO, ordinal: 5, value: null, display: "US/Israel–Iran war at roughly day 203, now a tanker war around a Strait of Hormuz whose traffic remains well below normal. Drone strikes from Iraqi territory hit Saudi Arabia's East-West pipeline on 10–11 September and Houthi missiles struck Saudi targets again on 19 September; Aramco is bypassing the damaged section and guiding to full capacity in about six weeks. Russia–Ukraine grinds on, and the Israel–Lebanon ceasefire is holding but strained.", unit: null, decimals: 0, cadence: "event", deltaNative: null, deltaLabel: null, deltaWeekOpen: null, asof: FRI, released: null, nextExpected: null, tier: 2, source: "Wire and analysis coverage (asserted, search agent)", freshness: "asserted", text: true },
];

/**
 * How the honest dataset differs, series by series.
 *
 * Each override is a real situation the worker would produce, not an invented
 * fault. The `due` pair is the case the whole freshness vocabulary exists for.
 */
const HONEST_OVERRIDES: Readonly<
  Record<
    string,
    { freshness: string; value?: null; note?: string }
  >
> = {
  // 1. A weekly series between prints, read on a Wednesday. Current, not late.
  cftc_asset_managers_es: {
    freshness: "due",
    note: "Positions are dated Tuesday 15 September and were released Friday 18 September. The next observation is due Friday 25 September and is not yet expected, so this is the current reading for a weekly series rather than a stale one.",
  },
  cftc_leveraged_funds_es: {
    freshness: "due",
    note: "Dated Tuesday 15 September, next due Friday 25 September. Between observations, which is not the same as overdue.",
  },
  net_liquidity: {
    freshness: "due",
    note: "Weekly on the H.4.1 Wednesday balance, released Thursday. The next balance sheet lands Thursday 24 September.",
  },
  // 2. A permanent gap on a point-in-time series. Nothing is interpolated and
  //    no prior value is carried forward into a day it did not describe.
  cnn_fear_greed: {
    freshness: "gap",
    value: null,
    note: "CNN's own API answered 451 and both aggregators answered 403 on the 09:30 run, so there is no reading for 18 September. This source is not replayable: the day cannot be recovered, nothing is interpolated, and the 33 from the prior week is not carried forward into a day it did not describe.",
  },
  // 6a. A search-agent series whose 09:30 leg did not complete.
  liquidations_24h: {
    freshness: "overdue",
    note: "No observation for 21 September against a next_expected of 21 September. The 09:30 search agent did not complete, so the figure shown is the last one received and is no longer current for a daily series.",
  },
  // 6b. A series declared in config/series.yaml that nothing has ever written.
  fedwatch_next_fomc: {
    freshness: "not_connected",
    value: null,
    note: "Declared in config/series.yaml with its own key, after the merge defect that filed this reading under a second row labelled Next FOMC. No source has written an observation for it yet, so any condition naming it resolves to unevaluable rather than to false.",
  },
};

/* ======================================================== snapshot rows == */

function snapshotRowJson(
  seed: SeriesSeed,
  dataset: FixtureDataset,
  evaluatedAt: string,
  ordinal: number,
): Record<string, unknown> {
  const override = dataset === "honest" ? HONEST_OVERRIDES[seed.key] : undefined;
  const freshness = override?.freshness ?? seed.freshness ?? "fresh";
  const blanked = override !== undefined && "value" in override;
  const value = blanked ? null : seed.value;
  return {
    series_id: `ser-${seed.key}`,
    key: seed.key,
    label: seed.label,
    table_group: seed.group,
    ordinal,
    value: value === null ? null : n(value, seed.decimals),
    // A blanked reading keeps no display string. The dash the renderer prints
    // is the truth; the last good formatting would be a value on screen.
    display: blanked ? null : seed.display,
    unit: seed.unit,
    value_kind: seed.text === true ? "text" : "numeric",
    delta_native: blanked || seed.deltaNative === null ? null : n(seed.deltaNative, 2),
    delta_native_label: blanked ? null : seed.deltaLabel,
    delta_week_open:
      blanked || seed.deltaWeekOpen === null ? null : n(seed.deltaWeekOpen, 2),
    anchor_asof: seed.deltaWeekOpen === null ? null : "2026-09-11",
    tier: blanked ? 4 : seed.tier,
    source: seed.source,
    asof: blanked ? null : seed.asof,
    released: seed.released ?? null,
    next_expected: seed.nextExpected ?? null,
    cadence: seed.cadence,
    // The run this reading came from. The 09:30 full run for an agent series,
    // the evaluating run for a deterministic one.
    observed_at: blanked ? null : evaluatedAt,
    freshness,
    note: override?.note ?? seed.note ?? null,
    extra: seed.extra ?? {},
    updated_at: evaluatedAt,
  };
}

/* =========================================================== phase calls = */

const EQ_CALL_ID = "pc-eq-w39";
const CR_CALL_ID = "pc-cr-w39";

// Both calls are fresh reads: classified on the Monday from the data and the
// frameworks with last week's call out of view, then shown last week's call
// only to write the one-sentence change note. Any phase may follow any phase,
// so the four conditions are what must stay true for this read to hold, not
// progress toward the next phase.
const PHASE_CALLS = [
  {
    phase_call_id: EQ_CALL_ID,
    asset: "equities",
    week: WEEK,
    phase: 3,
    phase_name: "Late Cycle / Distribution",
    prior_phase: 3,
    direction: "unchanged",
    reason:
      "Price is still rising on a narrowing base while credit stays calm and vol sits at a run low. That is Phase 3 on the framework: the index holds while the internals decay.",
    evidence: [
      "Breadth broke 50% for the first time in this arc and the Dow took a third straight losing week while the S&P finished flat and the Nasdaq alone rose: index-level calm on a visibly narrowing base is the Phase 3 signature, not a Phase 4 one.",
      "Leadership compressed to a single trade. Memory and AI infrastructure carried the Nasdaq on Intel's capacity warning, and UBS's capex arithmetic now routes nine in ten incremental AI dollars through memory, so one 30 September print carries the index's only working leg.",
      "Positioning churned rather than accumulated: asset managers cut the ES overlay by the largest step of the arc while leveraged funds covered shorts into the same meeting, which is two crowds leaving opposite sides at once, not a bid arriving.",
    ],
    falsifier:
      "A close under the 200dma that fails its retest, with breadth under 45% and HY through 300 bps, reads Phase 4. Breadth back above 60% with the Russell confirming reads Phase 2. The 22–24 September auction cluster and Micron on 30 September are the dated settlers.",
    stage: "late",
    stage_reason:
      "Late. Breadth has fallen five weeks running and is through 50%, leadership has compressed to a single trade, and asset managers cut the overlay by the largest step of the arc.",
    activated_at: FULL_RUN,
    classification: "fresh",
    classified_at: FULL_RUN,
    change_note:
      "Breadth slipped under 50% for the first time in this arc; nothing that prices risk moved.",
    prior_week: "2026-W38",
    since_week: "2026-W32",
    weeks_running: 8,
  },
  {
    phase_call_id: CR_CALL_ID,
    asset: "crypto",
    week: WEEK,
    phase: 1,
    phase_name: "Early Bull",
    prior_phase: 1,
    direction: "unchanged",
    reason:
      "BTC holds well above its 50-week MA and cleared $80K, but the sustained fund flow that marks Wealth Creation has not arrived, so the data reads Phase 1, late.",
    evidence: [
      "BTC defended the $74K level on FOMC day and then cleared the $80K level that had rejected it twice, in the same week the Fed hiked and the CLARITY Act died, so the move cannot be read as a rates trade.",
      "The regulatory driver moved from Congress to the SEC inside 48 hours, and SOL, whose chain already carries roughly $465m of tokenised stock, led the majors while BTC dominance fell.",
      "The impulse was squeeze-shaped rather than flow-shaped: eight shorts liquidated for every long, open interest rebuilt with funding flat, and the ETF week finished at a rounding error while ETH's four-week inflow streak snapped.",
    ],
    falsifier:
      "A BTC ETF inflow week above roughly +$500M with BTC accepting above $82K reads Phase 2. A close back under $76K that breaks the 15 September low puts the $74K level live again and reopens the Phase 5 question.",
    stage: "late",
    stage_reason:
      "Late within Phase 1. Price and sentiment already look like Phase 2; only the flow is missing, so the phase is near its end rather than in it.",
    activated_at: FULL_RUN,
    classification: "fresh",
    classified_at: FULL_RUN,
    change_note:
      "BTC cleared the $80K level that had rejected it twice; fund flow is still flat.",
    prior_week: "2026-W38",
    since_week: "2026-W35",
    weeks_running: 5,
  },
];

interface ConditionSeed {
  owner: string;
  kind: "expr" | "predicate" | "manual";
  text: string;
  status: string;
  current: string | null;
  /** The honest dataset's evening rescore, where it differs. */
  honest?: { status: string; current: string };
}

// What must stay true for each read to hold. On a fresh call a condition is
// met or failed: there is no `not yet`, because nothing is being waited for.
const PHASE_CONDITIONS: readonly ConditionSeed[] = [
  {
    owner: EQ_CALL_ID,
    kind: "expr",
    text: "The S&P holds above its 200dma",
    status: "met",
    current: "S&P 6.51% over a rising 200dma",
  },
  {
    owner: EQ_CALL_ID,
    kind: "expr",
    text: "Breadth stays under 60%: the index is not broadly confirmed",
    status: "met",
    current: "breadth 49.50% against 60%",
  },
  {
    owner: EQ_CALL_ID,
    kind: "expr",
    text: "HY stays under 300 bps",
    status: "met",
    current: "HY 270 bps; IG 78 bps",
  },
  {
    owner: EQ_CALL_ID,
    kind: "expr",
    text: "VIX stays under 22",
    status: "met",
    current: "VIX 14.81; MOVE 80.64",
  },
  {
    owner: CR_CALL_ID,
    kind: "expr",
    text: "BTC holds above its 50-week MA",
    status: "met",
    current: "BTC $81,492 against a 50W MA of $59,200",
  },
  {
    owner: CR_CALL_ID,
    kind: "expr",
    text: "Crypto F&G stays out of fear, at 25 or above",
    status: "met",
    current: "Crypto F&G 70",
  },
  {
    owner: CR_CALL_ID,
    kind: "predicate",
    text: "The BTC ETF inflow streak stays intact",
    status: "met",
    current: "BTC ETF week +$6.2M, streak intact",
    honest: {
      status: "failed",
      current: "streak broke on 21 Sep with a -$84M session",
    },
  },
  {
    owner: CR_CALL_ID,
    kind: "manual",
    text: "Liquidation dips are still bought back within three sessions",
    status: "met",
    current:
      "the sub-$75K flush was fully recovered in three sessions on $458M of short liquidations",
    honest: {
      status: "failed",
      current: "the 21 Sep flush under $78K has not been bought back",
    },
  },
];

/* ============================================================= scenarios = */

const SET_ID = "set-w39";

interface TargetSeed {
  scenario: string;
  label: string;
  series: string | null;
  comparator: "between" | "gt" | "lt" | null;
  lo: number | null;
  hi: number | null;
  current: number | null;
  holding: boolean | null;
  distance: number | null;
  text?: string;
}

/**
 * Every target band the W39 house view stated, with the live value on it.
 *
 * S1's labels are written the way the live rows are, with the band inside the
 * label (`ETH $2,480–$2,980`) and a judgment target whose label is its whole
 * sentence; S2 and S3 keep the short form. Both shapes are in production, and
 * a fixture holding only the short one hid a label cut at the thousands comma.
 */
const TARGETS: readonly TargetSeed[] = [
  // S1, the base case at 45%. Eleven bands plus a leadership line.
  { scenario: "S1", label: "S&P 7,520–7,800", series: "spx_cash", comparator: "between", lo: 7520, hi: 7800, current: 7650.5, holding: true, distance: 130.5 },
  { scenario: "S1", label: "Breadth", series: "breadth_spx_above_200dma", comparator: "between", lo: 44, hi: 55, current: 49.5, holding: true, distance: 5.5 },
  { scenario: "S1", label: "Russell", series: "rut_cash", comparator: "between", lo: 2780, hi: 2930, current: 2860.4, holding: true, distance: 69.6 },
  { scenario: "S1", label: "HY 262–292 bps", series: "hy_oas_bps", comparator: "between", lo: 262, hi: 292, current: 270, holding: true, distance: 8 },
  { scenario: "S1", label: "VIX", series: "vix", comparator: "between", lo: 13, hi: 19, current: 14.81, holding: true, distance: 1.81 },
  { scenario: "S1", label: "Nasdaq beats the Dow again and memory leads the Nasdaq", series: null, comparator: null, lo: null, hi: null, current: null, holding: null, distance: null, text: "Nasdaq beats the Dow again and memory leads the Nasdaq" },
  { scenario: "S1", label: "BTC", series: "btc", comparator: "between", lo: 76000, hi: 88000, current: 81492, holding: true, distance: 5492.07 },
  { scenario: "S1", label: "ETH $2,480–$2,980", series: "eth", comparator: "between", lo: 2480, hi: 2980, current: 2667, holding: true, distance: 187.48 },
  { scenario: "S1", label: "BTC ETF week positive but under +$700M", series: "btc_etf_weekly_flow", comparator: "between", lo: 0, hi: 700_000_000, current: 6_210_000, holding: true, distance: 6_210_000 },
  { scenario: "S1", label: "2Y", series: "ust_2y", comparator: "between", lo: 4.66, hi: 4.92, current: 4.76, holding: true, distance: 0.1 },
  { scenario: "S1", label: "10Y 4.90–5.12%", series: "ust_10y", comparator: "between", lo: 4.9, hi: 5.12, current: 5.01, holding: true, distance: 0.11 },
  { scenario: "S1", label: "30Y", series: "ust_30y", comparator: "between", lo: 5.24, hi: 5.52, current: 5.34, holding: true, distance: 0.1 },
  { scenario: "S1", label: "gold $4,280–$4,620", series: "gold", comparator: "between", lo: 4280, hi: 4620, current: 4424.9, holding: true, distance: 144.9 },
  { scenario: "S1", label: "Brent", series: "brent", comparator: "between", lo: 92, hi: 112, current: 103.87, holding: true, distance: 8.13 },

  // S2, the tail at 32%.
  { scenario: "S2", label: "S&P", series: "spx_cash", comparator: "between", lo: 7230, hi: 7520, current: 7650.5, holding: false, distance: 130.5 },
  { scenario: "S2", label: "Breadth", series: "breadth_spx_above_200dma", comparator: "between", lo: 36, hi: 46, current: 49.5, holding: false, distance: 3.5 },
  { scenario: "S2", label: "Russell", series: "rut_cash", comparator: "between", lo: 2600, hi: 2790, current: 2860.4, holding: false, distance: 70.4 },
  { scenario: "S2", label: "HY OAS", series: "hy_oas_bps", comparator: "between", lo: 300, hi: 350, current: 270, holding: false, distance: 30 },
  { scenario: "S2", label: "VIX", series: "vix", comparator: "between", lo: 20, hi: 30, current: 14.81, holding: false, distance: 5.19 },
  { scenario: "S2", label: "Leadership: memory and AI infrastructure underperform the index", series: null, comparator: null, lo: null, hi: null, current: null, holding: null, distance: null, text: "memory and AI infrastructure underperform the index — the crowded leg goes first" },
  { scenario: "S2", label: "BTC", series: "btc", comparator: "between", lo: 64000, hi: 76000, current: 81492, holding: false, distance: 5492.07 },
  { scenario: "S2", label: "ETH", series: "eth", comparator: "between", lo: 2020, hi: 2480, current: 2667, holding: false, distance: 187.48 },
  { scenario: "S2", label: "10Y", series: "ust_10y", comparator: "between", lo: 5.12, hi: 5.38, current: 5.01, holding: false, distance: 0.11 },
  { scenario: "S2", label: "30Y", series: "ust_30y", comparator: "between", lo: 5.52, hi: 5.85, current: 5.34, holding: false, distance: 0.18 },
  { scenario: "S2", label: "2Y: ambiguous, a growth scare and the 2027 dots pull opposite ways", series: null, comparator: null, lo: null, hi: null, current: null, holding: null, distance: null, text: "the 2Y is ambiguous because a growth scare and the 2027 dots pull opposite ways" },
  { scenario: "S2", label: "Gold, on a term-premium bid", series: "gold", comparator: "between", lo: 4500, hi: 4850, current: 4424.9, holding: false, distance: 75.1 },
  { scenario: "S2", label: "Brent", series: "brent", comparator: "between", lo: 88, hi: 115, current: 103.87, holding: true, distance: 11.13 },

  // S3, the repair case at 23%.
  { scenario: "S3", label: "S&P", series: "spx_cash", comparator: "between", lo: 7800, hi: 8000, current: 7650.5, holding: false, distance: 149.5 },
  { scenario: "S3", label: "Breadth", series: "breadth_spx_above_200dma", comparator: "between", lo: 55, hi: 66, current: 49.5, holding: false, distance: 5.5 },
  { scenario: "S3", label: "Russell", series: "rut_cash", comparator: "between", lo: 2930, hi: 3070, current: 2860.4, holding: false, distance: 69.6 },
  { scenario: "S3", label: "HY OAS", series: "hy_oas_bps", comparator: "between", lo: 242, hi: 266, current: 270, holding: false, distance: 4 },
  { scenario: "S3", label: "VIX", series: "vix", comparator: "between", lo: 11.5, hi: 15, current: 14.81, holding: true, distance: 0.19 },
  { scenario: "S3", label: "Leadership: the Russell and equal-weight beat the Nasdaq", series: null, comparator: null, lo: null, hi: null, current: null, holding: null, distance: null, text: "the Russell and equal-weight beat the Nasdaq for the first time since August" },
  { scenario: "S3", label: "BTC, gated on an inflow week above +$700M", series: "btc", comparator: "between", lo: 86000, hi: 96000, current: 81492, holding: false, distance: 4507.93 },
  { scenario: "S3", label: "ETH", series: "eth", comparator: "between", lo: 2950, hi: 3400, current: 2667, holding: false, distance: 282.52 },
  { scenario: "S3", label: "2Y", series: "ust_2y", comparator: "between", lo: 4.44, hi: 4.7, current: 4.76, holding: false, distance: 0.06 },
  { scenario: "S3", label: "10Y", series: "ust_10y", comparator: "between", lo: 4.62, hi: 4.92, current: 5.01, holding: false, distance: 0.09 },
  { scenario: "S3", label: "30Y", series: "ust_30y", comparator: "between", lo: 5.0, hi: 5.28, current: 5.34, holding: false, distance: 0.06 },
  { scenario: "S3", label: "Gold", series: "gold", comparator: "between", lo: 4200, hi: 4500, current: 4424.9, holding: true, distance: 75.1 },
  { scenario: "S3", label: "Brent", series: "brent", comparator: "lt", lo: null, hi: 95, current: 103.87, holding: false, distance: 8.87 },
];

interface ClauseSeed {
  scenario: string;
  text: string;
  status: string;
  current: string | null;
  required: boolean;
  kind: "expr" | "predicate" | "manual";
  voidReason?: string;
}

const CLAUSES: readonly ClauseSeed[] = [
  { scenario: "S1", required: true, kind: "manual", text: "The 22–24 September cluster clears without a second record tail", status: "not yet", current: "the 2Y/5Y/7Y cluster has not yet been auctioned" },
  { scenario: "S1", required: true, kind: "manual", text: "Core PCE on 30 September lands 0.2–0.3% and 28 October stays near a coin flip", status: "not yet", current: "FedWatch 50.9% hike as quoted 17 Sep; PCE not yet released" },
  { scenario: "S1", required: true, kind: "manual", text: "Micron confirms HBM pricing", status: "not yet", current: "Q4 FY2026 prints 30 September" },
  { scenario: "S1", required: true, kind: "expr", text: "Brent stays in a wide band as the East-West line restores in stages", status: "met", current: "Brent $103.87, inside $92–$112" },

  { scenario: "S2", required: false, kind: "manual", text: "A second tailing auction on 22–24 September", status: "not yet", current: "not yet auctioned" },
  { scenario: "S2", required: false, kind: "manual", text: "A tightening Senior Credit Officer survey on 24 September", status: "not yet", current: "survey due 24 September" },
  { scenario: "S2", required: false, kind: "manual", text: "A dateless shock: a model-efficiency release, a hyperscaler capex trim, or a private-credit markdown cluster", status: "not yet", current: "nothing on the tape" },
  { scenario: "S2", required: false, kind: "expr", text: "Confirming leg: VIX above 20", status: "not yet", current: "VIX 14.81" },
  { scenario: "S2", required: false, kind: "expr", text: "Confirming leg: HY above 300 bps", status: "not yet", current: "HY 270 bps" },
  { scenario: "S2", required: false, kind: "expr", text: "Confirming leg: breadth below 45%", status: "not yet", current: "breadth 49.50%" },

  { scenario: "S3", required: true, kind: "expr", text: "Full East-West restoration or an Oman reschedule takes Brent under $92", status: "not yet", current: "Brent $103.87 against $92" },
  { scenario: "S3", required: true, kind: "manual", text: "Core PCE 0.2% and a soft JOLTS and NFP push 28 October to a hold", status: "not yet", current: "JOLTS 29 September, NFP 2 October" },
  { scenario: "S3", required: true, kind: "manual", text: "The auction cluster clears cleanly", status: "not yet", current: "not yet auctioned" },
  { scenario: "S3", required: true, kind: "manual", text: "Micron confirms", status: "not yet", current: "prints 30 September" },
];

const SCENARIOS = [
  {
    id: "S1",
    ordinal: 1,
    title: "Narrow and high-yielding: memory carries a thinning tape",
    probability: 45,
    positioning:
      "Hold quality at the floor of the band; memory stays the CRITICAL row but gets no add before 30 September; no index adds until breadth reclaims 55%.",
  },
  {
    id: "S2",
    ordinal: 2,
    title: "Duration fails and the 5% ten-year finally transmits",
    probability: 32,
    positioning:
      "Cut high-beta, not quality, and only once a confirming leg prints; hold cash for a retest of the rising 200dma; do not use gold or crypto as the equity hedge.",
  },
  {
    id: "S3",
    ordinal: 3,
    title: "Oil breaks the inflation leg and breadth repairs",
    probability: 23,
    positioning:
      "Add toward the middle of the equity band only after breadth reclaims 55%; the crypto add is gated on the inflow week, not on price.",
  },
];

/* =============================================================== setups == */

interface SetupSeed {
  id: string;
  asset: "equities" | "crypto";
  name: string;
  /** `FTR, HTF` and the rest, as the Setup Ledger writes them. */
  style: string;
  entryLo: number;
  entryHi: number;
  invalidation: number;
  invalidationDir: "above" | "below";
  target: number;
  rr: number;
  gateText: string;
  row: string;
  /**
   * The series this setup's levels are quoted on.
   *
   * Null on the last one: the worker will not score a setup that names no
   * series, and that is the case `series_key` exists to let the surface
   * explain. Equity setups are quoted against a cash index and crypto setups
   * against BTC or ETH, so the mapping cannot be inferred from `asset`.
   */
  series: { key: string; label: string } | null;
}

const SETUPS: readonly SetupSeed[] = [
  {
    id: "su-eq-spx",
    series: { key: "spx_cash", label: "S&P 500 (cash)" },
    style: "FTR, HTF",
    asset: "equities",
    name: "S&P FOMC-shelf FTR long",
    entryLo: 7500,
    entryHi: 7560,
    invalidation: 7450,
    invalidationDir: "below",
    target: 7800,
    rr: 3.4,
    gateText: "HY under 292 bps and breadth not printing below 45% on the entry close",
    row: "HOLD",
  },
  {
    id: "su-eq-ndx",
    series: { key: "nasdaq_cash", label: "Nasdaq Composite (cash)" },
    style: "O/U, HTF",
    asset: "equities",
    name: "Nasdaq failed-push fade",
    entryLo: 26900,
    entryHi: 27100,
    invalidation: 27300,
    invalidationDir: "above",
    target: 26100,
    rr: 3.0,
    gateText: "breadth still under 52%",
    row: "CRITICAL",
  },
  {
    id: "su-cr-btc",
    series: { key: "btc", label: "BTC" },
    style: "FTR, HTF",
    asset: "crypto",
    name: "BTC $82K acceptance retest",
    entryLo: 78500,
    entryHi: 80500,
    invalidation: 76000,
    invalidationDir: "below",
    target: 92000,
    rr: 3.6,
    gateText:
      "a daily close above $82,000 first, then a retest that holds with the ETF week still positive",
    row: "HOLD",
  },
  {
    id: "su-cr-eth",
    series: { key: "eth", label: "ETH" },
    style: "FTR, HTF",
    asset: "crypto",
    name: "ETH breakout-shelf FTR",
    entryLo: 2480,
    entryHi: 2560,
    invalidation: 2380,
    invalidationDir: "below",
    target: 3050,
    rr: 3.8,
    gateText: "none: the W38 ETF-flows-positive clause is deliberately dropped",
    row: "WATCH",
  },
];

/* ============================================================= the ledgers */

/**
 * The Scorecard Ledger.
 *
 * `other_grades` is a jsonb **array** of `{id, prob, grade, note}`, matching
 * `macro_core.week_grade`. It carries the probability each non-base scenario
 * was priced at as well as how it landed, which is the pair the Brier score is
 * computed from; an object keyed by scenario id would carry only the grade.
 *
 * `graded_at` is null for the weeks before W36, which were graded by archive
 * journals this system deliberately does not read. A derived Monday would be a
 * manufactured fact.
 */
const WEEK_GRADES = [
  { week: "2026-W31", label: "S2 · Hold with hawkish framing; capex mixed; the box holds (40%)", grade: "Partial", other: [{ id: "S1", prob: 30, grade: "Partial", note: null }, { id: "S3", prob: 30, grade: "Partial", note: null }], eq: "valid", crypto: "valid but weakening", hit: null, of: null, brier: null, miss: "Bounded oil in an $85–100 band across all three branches and Brent went through the floor to $83.30 on a negotiated Hormuz reopening: two-sided is not the same as wide enough." },
  { week: "2026-W32", label: "S2 · Framework wobbles; distribution grinds on; leadership stays split (40%)", grade: "Partial", other: [{ id: "S1", prob: 35, grade: "Partial", note: null }, { id: "S3", prob: 25, grade: "Not", note: null }], eq: "wrong", crypto: "valid", hit: null, of: null, brier: null, miss: "The phase call capped the scenario, and gold was priced off the war leg alone while the term-premium bid drove +8%. Graded over two weeks, with no W33 journal." },
  { week: "2026-W34", label: "S2 · Records hold; reward-per-beat decay spreads; chop under 7,850 (40%)", grade: "Partial", other: [{ id: "S1", prob: 30, grade: "Partial", note: null }, { id: "S3", prob: 30, grade: "Partial", note: null }], eq: "valid", crypto: "wrong", hit: null, of: null, brier: null, miss: "Treated equities and crypto as one risk regime with a decoupling footnote when they traded opposite sides of the same variable. A Lull call has no mechanism for a +20% week." },
  { week: "2026-W35", label: "S2 · The turf fight stays unresolved; the long end stays elevated (40%)", grade: "Partial", other: [{ id: "S1", prob: 30, grade: "Partial", note: null }, { id: "S3", prob: 30, grade: "Not", note: null }], eq: "valid", crypto: "valid", hit: null, of: null, brier: null, miss: "Coupled Fed credibility to continued tech reward decay, and read sanctions escalation as oil direction when reopening hopes drove the weekly move." },
  { week: "2026-W36", label: "S1 · Hawkish resilience, exceptional AI holds the index (45%)", grade: "Partial", other: [{ id: "S2", prob: 30, grade: "Not", note: null }, { id: "S3", prob: 25, grade: "Partial", note: null }], eq: "valid", crypto: "valid", hit: 6, of: 9, brier: 0.155, miss: "Bounded Brent below $92 for the third time in five weeks and it settled $96. The oil trigger fired for the fifth week with no confirming risk regime, so the oil leg needs its own scenario axis." },
  { week: "2026-W37", label: "S2 · Hot-enough CPI keeps the hike live while oil taxes the tape (40%)", grade: "Materialized", other: [{ id: "S1", prob: 35, grade: "Not", note: null }, { id: "S3", prob: 25, grade: "Not", note: null }], eq: "valid", crypto: "valid but weakening", hit: 9, of: 11, brier: 0.545, miss: "The base case's mechanism landed almost exactly and was still priced at only 40%, so Brier punished under-confidence, not direction." },
  { week: "2026-W38", label: "S1 · The Fed hikes with a path while oil stays above $100 (45%)", grade: "Partial", other: [{ id: "S2", prob: 30, grade: "Partial", note: null }, { id: "S3", prob: 25, grade: "Not", note: null }], eq: "valid", crypto: "valid", hit: 7, of: 11, brier: 0.105, miss: "Every macro, rates, credit and oil band landed in S1 while every vol, crypto and metals band landed in S2, which is R5 recurring on the volatility-and-crypto axis. BTC was capped at $79K because the Fed was hiking, pricing the asset off the rates channel when the SEC's agency track was the driver." },
];

const SETUP_GRADES = [
  { id: "sg-w37-1", week: "2026-W37", graded: "2026-W38", asset: "equities", name: "S&P shelf FTR long", outcome: "not triggered", r: null, note: "Price traded through the band but breadth was 56.85% and 54.67% on those closes, so the breadth-above-60% gate kept it shut." },
  { id: "sg-w37-2", week: "2026-W37", graded: "2026-W38", asset: "equities", name: "Record-shelf fade", outcome: "not triggered", r: null, note: "The S&P's best close of the week was 7,656.98; the August record shelf was never approached." },
  { id: "sg-w37-3", week: "2026-W37", graded: "2026-W38", asset: "crypto", name: "BTC $82K acceptance retest", outcome: "not triggered", r: null, note: "BTC failed near $80K and never printed a daily close above $82,000, so the acceptance condition never existed." },
  { id: "sg-w38-1", week: "2026-W38", graded: "2026-W39", asset: "equities", name: "S&P shelf FTR long", outcome: "not triggered", r: null, note: "Price closed 7,585.73 inside the band on 15 September and traded to 7,507.77 on the 16th, but breadth was 52.88% and 50.09% against the above-55% gate. Third consecutive week a rising-breadth gate blocked a falling-price entry." },
  { id: "sg-w38-2", week: "2026-W38", graded: "2026-W39", asset: "equities", name: "Post-FOMC failed-push fade", outcome: "not triggered", r: null, note: "The week's highest print was 7,657.17; the post-FOMC push the setup was designed to fade never happened." },
  { id: "sg-w38-3", week: "2026-W38", graded: "2026-W39", asset: "crypto", name: "BTC old-box retest", outcome: "not triggered", r: null, note: "The weekly low was $74,944.59, still $945 above the band. Carried four weeks without an entry and retired in W39 with BTC $7K above it; the $81.5K target was reached without the trade." },
  { id: "sg-w38-4", week: "2026-W38", graded: "2026-W39", asset: "crypto", name: "ETH breakout-shelf FTR", outcome: "not triggered", r: null, note: "ETH closed inside the band on 15 to 17 September, but its ETFs ran outflows on exactly those three sessions and the week finished -$140.6M, so the flows-positive clause kept it shut. ETH then ran to $2,692, a forgone roughly +1.7R." },
  { id: "sg-w35-5", week: "2026-W35", graded: "2026-W37", asset: "crypto", name: "Extension fade", outcome: "triggered → stopped", r: -1.0, note: "BTC printed $82,200 on 3 September after Waller, clearing the $81,500 invalidation outright; the fade never reached $72,000." },
];

const RULES = [
  { number: 1, text: "Compute a futures-quoted index's weekly % change from the cash index, never from the front-month future; contract rolls contaminate the week-over-week math.", weeks: ["2026-W27"] },
  { number: 2, text: "The scenario set must span both directions of the dominant swing variable. Two-sided is a requirement, not a stylistic preference.", weeks: ["2026-W26"] },
  { number: 3, text: "Oil bands must be wide enough to survive a chokepoint headline in either direction. Size the band from the last 8 weeks of Brent range, not from the current narrative.", weeks: ["2026-W26", "2026-W31", "2026-W34", "2026-W36"] },
  { number: 4, text: "Every risk-off trigger needs a confirming-regime clause: a price level alone is not a trigger. Pair it with at least one of VIX above a stated level, HY OAS above a stated level, or breadth below a stated level.", weeks: ["2026-W30", "2026-W31", "2026-W34"] },
  { number: 5, text: "Leadership gets its own two-sided axis, separate from the macro trigger. A correct macro branch and a correct tape have landed in different scenarios three times.", weeks: ["2026-W29", "2026-W31", "2026-W38"] },
  { number: 6, text: "Reserve explicit probability for a dateless structural shock. Two of the last ten weeks were driven by an event no calendar carried.", weeks: ["2026-W29", "2026-W31"] },
  { number: 7, text: "When equities and crypto sit on opposite sides of the swing variable, give them separate target rows rather than a decoupling footnote.", weeks: ["2026-W34"] },
  { number: 8, text: "When a phase falsifier is a conjunction, price the case where both arrive in the same week; a Lull call with no mechanism for a +20% week is not a call.", weeks: ["2026-W34"] },
  { number: 9, text: "Do not couple an index direction to a specific leadership regime continuing; leadership flips faster than phase calls.", weeks: ["2026-W28", "2026-W36"] },
  { number: 10, text: "Do not extrapolate a three-week flow streak into a regime; streaks inflect exactly when they look most established.", weeks: ["2026-W27", "2026-W37"] },
  { number: 11, text: "Oil gets its own two-sided axis, separate from the risk-off tail. Crude is an inflation-and-rates variable that only sometimes becomes a risk variable, so pricing it only inside the risk-off branch guarantees a Partial.", weeks: ["2026-W34", "2026-W36"] },
  { number: 12, text: "Name the dominant driver per asset and state which channel its band is priced off. When an asset has a live policy or regulatory channel of its own alongside the macro one, price the band off the channel that moved it last, or widen it to span both.", weeks: ["2026-W35", "2026-W38"] },
];

/* ------------------------------------------------- observation history -- */

/**
 * The read each observation was written with, where one was.
 *
 * Keyed `series_key@asof`. Deliberately sparse: the reads only start being
 * stored partway through each arc, which is what the real migration looks
 * like, and an observation with none renders a blank rather than borrowing
 * its neighbour's.
 */
const READS: Readonly<Record<string, string>> = {
  "spx_cash@2026-09-16":
    "Slid to 7,507.77 on the FOMC, the low of the arc and the shelf every W39 setup is written against.",
  "spx_cash@2026-09-17":
    "Bought back in one session; the FOMC low held on the first retest.",
  "spx_cash@2026-09-18":
    "Every condition, band and setup level is written against the cash index, never the future. R1 exists because the two were once conflated.",
  "breadth_spx_above_200dma@2026-09-15":
    "Through 55% on the first session of the week, which shut the W38 setup's gate.",
  "breadth_spx_above_200dma@2026-09-16":
    "Through 50% intraday for the first time in the arc, on the FOMC.",
  "breadth_spx_above_200dma@2026-09-18":
    "Through 50% for the first time in the arc. Roughly $6.2tn of options expired on this date, so the print carries that distortion.",
  "vix@2026-09-16": "18.94 on the FOMC, the high of the arc, fully sold by Friday.",
  "vix@2026-09-18":
    "An 18.94 FOMC-day spike fully sold; the week's most uncomfortable number.",
  "cftc_asset_managers_es@2026-09-08":
    "Second consecutive trim, still deep in the neutral band.",
  "cftc_asset_managers_es@2026-09-15":
    "Positions are dated Tuesday and released the following Friday. Neutral band, largest cut of the arc, still 10.4 pp above the sidelined threshold.",
  "btc@2026-09-15":
    "Defended the $74K falsifier by under $1,000 on the CLARITY flush.",
  "btc@2026-09-19": "Cleared $80K to $81,911 on a squeeze, not on flow.",
  "btc@2026-09-21":
    "A calendar-day series: there are no closed days, so a Monday reading beside a Friday equity close is two honest as-ofs rather than one stale one.",
  "hy_oas_bps@2026-09-17":
    "Stored in basis points, the unit every condition is written in. ICE publishes a day late, so the 17th is the freshest print, not a late one.",
  "eth@2026-09-19": "Broke to $2,692 on the shelf the W39 setup retests.",
  "btc_etf_weekly_flow@2026-09-18":
    "Smallest weekly net inflow in the products' 141 weeks of trading: a $433M Friday print cancelled four days of selling.",
};

/**
 * The trailing path of the series that have one on record.
 *
 * Real W39 arcs, the ones the journal's §0 trajectory lines carry: the CFTC
 * pair over three prints, the two Fear and Greed series, HY, the breadth
 * series' intraweek path, the BTC ETF weekly flow, and the index levels.
 *
 * Only some series carry a path. A series with no history renders its
 * disclosure as a stated absence rather than a flat line through one point,
 * because a sparkline drawn across a single observation is a trend nobody
 * measured.
 */
const PATHS: Readonly<Record<string, readonly [string, number][]>> = {
  spx_cash: [
    ["2026-09-14", 7596.2], ["2026-09-15", 7585.73], ["2026-09-16", 7507.77],
    ["2026-09-17", 7641.02], ["2026-09-18", 7650.5],
  ],
  nasdaq_cash: [
    ["2026-09-14", 26301.1], ["2026-09-15", 26244.8], ["2026-09-16", 25988.4],
    ["2026-09-17", 26410.2], ["2026-09-18", 26522.54],
  ],
  dow_cash: [
    ["2026-09-14", 52410.3], ["2026-09-15", 52188.0], ["2026-09-16", 51702.4],
    ["2026-09-17", 51840.1], ["2026-09-18", 51682.64],
  ],
  rut_cash: [
    ["2026-09-14", 2898.1], ["2026-09-15", 2884.4], ["2026-09-16", 2841.9],
    ["2026-09-17", 2875.2], ["2026-09-18", 2860.4],
  ],
  // The intraweek path the journal prints: 56.26 → 52.88 → 50.09 → 50.29 → 49.50.
  breadth_spx_above_200dma: [
    ["2026-09-14", 56.26], ["2026-09-15", 52.88], ["2026-09-16", 50.09],
    ["2026-09-17", 50.29], ["2026-09-18", 49.5],
  ],
  vix: [
    ["2026-09-14", 15.62], ["2026-09-15", 16.1], ["2026-09-16", 18.94],
    ["2026-09-17", 15.44], ["2026-09-18", 14.81],
  ],
  // Weekly, Tuesday as-of. Three prints, which is the whole arc on record.
  cftc_asset_managers_es: [
    ["2026-09-01", 45.64], ["2026-09-08", 44.04], ["2026-09-15", 36.98],
  ],
  cftc_leveraged_funds_es: [
    ["2026-09-01", -15.51], ["2026-09-08", -16.46], ["2026-09-15", -11.98],
  ],
  cnn_fear_greed: [
    ["2026-09-14", 34], ["2026-09-15", 33], ["2026-09-16", 31],
    ["2026-09-17", 30], ["2026-09-18", 29],
  ],
  crypto_fear_greed: [
    ["2026-09-15", 57], ["2026-09-16", 52], ["2026-09-17", 61],
    ["2026-09-18", 66], ["2026-09-19", 68], ["2026-09-20", 67],
    ["2026-09-21", 70],
  ],
  hy_oas_bps: [
    ["2026-09-11", 270], ["2026-09-14", 272], ["2026-09-15", 274],
    ["2026-09-16", 273], ["2026-09-17", 270],
  ],
  ig_oas_bps: [
    ["2026-09-11", 80], ["2026-09-14", 80], ["2026-09-15", 79],
    ["2026-09-16", 78], ["2026-09-17", 78],
  ],
  ust_10y: [
    ["2026-09-14", 4.97], ["2026-09-15", 4.99], ["2026-09-16", 5.04],
    ["2026-09-17", 4.99], ["2026-09-18", 5.01],
  ],
  btc: [
    ["2026-09-15", 74944], ["2026-09-16", 77120], ["2026-09-17", 79340],
    ["2026-09-18", 80110], ["2026-09-19", 81911], ["2026-09-20", 80480],
    ["2026-09-21", 81492],
  ],
  eth: [
    ["2026-09-15", 2399], ["2026-09-16", 2416], ["2026-09-17", 2447],
    ["2026-09-18", 2561], ["2026-09-19", 2692], ["2026-09-20", 2611],
    ["2026-09-21", 2667],
  ],
  // The daily prints the weekly net is built from: +160.0, -450.3, -296.0,
  // +159.5, +433.0, netting +6.21M.
  btc_etf_weekly_flow: [
    ["2026-09-14", 160000000], ["2026-09-15", -450300000],
    ["2026-09-16", -296000000], ["2026-09-17", 159500000],
    ["2026-09-18", 6210000],
  ],
};

/**
 * The restatements of the text series that have some on record.
 *
 * A text series has no value to draw, so its history is the sentence it said
 * each week, in full, with the source the worker recorded against it. Adapted
 * from the real `macro_mart.observation_history` rows, including their source
 * strings as the migrator and the search agent wrote them: the parenthetical
 * provenance is the storage's, and the page is what has to read cleanly.
 *
 * `next_fomc` restates the same sentence two weeks running on purpose, which is
 * what the real series does between meetings. Most rows carry no read, as in
 * the real data: the reads began with the live worker, not the migration.
 */
interface ProseSeed {
  asof: string;
  display: string;
  source: string;
  tier: number;
  note?: string;
}

const PROSE_PATHS: Readonly<Record<string, readonly ProseSeed[]>> = {
  fed_funds: [
    {
      asof: "2026-09-16",
      display:
        "3.75%–4.00% after a unanimous 12–0 25 bp hike on 16 September, the first increase in more than three years. The SEP median puts year-end 2026 at 4.1% and 2027 at 4.1%, implying no cuts next year.",
      source:
        "search agent (macro): CNBC 2026-09-16 Fed decision; https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm",
      tier: 1,
      note: "Multiple sources agree on range, vote and SEP median.",
    },
    {
      asof: "2026-09-11",
      display:
        "3.50%–3.75% target range, midpoint 3.625%, unchanged since the 29 July hold, which carried three dissents (Hammack, Kashkari, Logan) all in favour of a 25 bp hike. Chair is Kevin Warsh.",
      source:
        "FOMC coverage (search agent) (migrated from Weekly Market Journal 2026-W38)",
      tier: 2,
    },
    {
      asof: "2026-09-04",
      display:
        "3.50%–3.75% target range, midpoint 3.625%, unchanged since the 28–29 July hold, which carried three dissents (Hammack, Kashkari, Logan) all in favour of a 25 bp hike. Chair is Kevin Warsh.",
      source:
        "FOMC coverage (search agent) (migrated from Weekly Market Journal 2026-W37)",
      tier: 2,
    },
    {
      asof: "2026-08-28",
      display:
        "3.50%–3.75%, midpoint 3.625%; held 29 July by 9–3, with three dissents for a 25 bp hike.",
      source:
        "FOMC coverage (search agent) (migrated from Weekly Market Journal 2026-W36)",
      tier: 2,
    },
  ],
  next_fomc: [
    {
      asof: FRI,
      display:
        "27–28 October 2026. Statement 2:00 PM ET Wednesday 28 October, press conference 2:30 PM ET, and no SEP at this meeting: the 2026 projection meetings are March, June, September and December.",
      source: "search agent (macro): federalreserve.gov FOMC calendar",
      tier: 2,
      note: "Dates and no-SEP status confirmed on the Fed calendar.",
    },
    {
      asof: "2026-09-11",
      display:
        "15–16 September with the SEP and dot plot; statement Wednesday 16 September at 2:00 PM ET, press conference 2:30 PM ET.",
      source:
        "federalreserve.gov FOMC calendar (search agent) (migrated from Weekly Market Journal 2026-W38)",
      tier: 2,
    },
    {
      asof: "2026-09-04",
      display:
        "15–16 September with the SEP and dot plot; statement Wednesday 16 September at 2:00 PM ET, press conference 2:30 PM ET.",
      source:
        "federalreserve.gov FOMC calendar (search agent) (migrated from Weekly Market Journal 2026-W37)",
      tier: 2,
    },
  ],
  geopolitical_state: [
    {
      asof: FRI,
      display:
        "US/Israel–Iran war at roughly day 203, now a tanker war around a Strait of Hormuz whose traffic remains well below normal. Drone strikes from Iraqi territory hit Saudi Arabia's East-West pipeline on 10–11 September and Houthi missiles struck Saudi targets again on 19 September; Aramco is bypassing the damaged section and guiding to full capacity in about six weeks. Russia–Ukraine grinds on, and the Israel–Lebanon ceasefire is holding but strained.",
      source:
        "search agent (macro): GlobalSecurity Iran War Day 203 update (2026-09-17); Al Jazeera Iran war coverage",
      tier: 2,
    },
    {
      asof: "2026-09-11",
      display:
        "The US–Iran war is now a tanker-for-tanker campaign: the US sank five Iranian tankers, Iran declared strikes on 10 ships and a US base in Jordan, and Saudi output fell to 6.24 mb/d, the lowest since 1990. Oman's 14 September Iran–GCC Hormuz meeting was postponed with no new date. Russia–Ukraine's 5–8 September pause ended with no ceasefire.",
      source:
        "Wire and analysis coverage (asserted, search agent) (migrated from Weekly Market Journal 2026-W38)",
      tier: 2,
    },
    {
      asof: "2026-09-04",
      display:
        "The Iran war is past its air campaign but the Hormuz chokepoint is now the active theatre: US strikes on three Iranian tankers, an Iranian ballistic missile fired toward US carriers, and Houthi strikes that suspended some Saudi Aramco operations. Israel–Hezbollah sits under a fragile ceasefire; Ukraine has a capital-strike pause but no ceasefire.",
      source:
        "Wire and analysis coverage (asserted, search agent) (migrated from Weekly Market Journal 2026-W37)",
      tier: 2,
    },
    {
      asof: "2026-08-28",
      display:
        "Iran and Hormuz remain the dominant cross-asset flashpoint. Financial sanctions expanded even as reopening hopes cut oil's premium, then US strikes on Iranian launchers on 30 August restored Monday gap risk; Russia–Ukraine and Taiwan tensions remain active.",
      source:
        "Wire and analysis coverage (asserted, search agent) (migrated from Weekly Market Journal 2026-W36)",
      tier: 2,
    },
  ],
};

function observationRows(
  dataset: FixtureDataset,
  evaluatedAt: string,
): unknown[] {
  const honest = dataset === "honest";
  const rows: unknown[] = [];
  for (const seed of SERIES) {
    const path = PATHS[seed.key];
    if (path === undefined) continue;
    for (const [asof, value] of path) {
      // The day the point-in-time source was missed has no row at all. A gap
      // is an absence in the history as much as in the snapshot: nothing is
      // interpolated and no neighbour is stretched across it.
      if (honest && seed.key === "cnn_fear_greed" && asof === FRI) continue;
      rows.push({
        series_id: `ser-${seed.key}`,
        series_key: seed.key,
        asof,
        value: n(value, seed.decimals),
        display: null,
        unit: seed.unit,
        tier: seed.tier,
        source: seed.source,
        released: seed.released ?? null,
        observed_at: asof === seed.asof ? evaluatedAt : `${asof}T13:00:00.000Z`,
        // Null wherever none was written, which is most of them. The reads
        // began to be stored partway through each arc, and an observation
        // without one shows a blank rather than borrowing a neighbour's.
        note: READS[`${seed.key}@${asof}`] ?? null,
      });
    }
  }
  for (const [key, path] of Object.entries(PROSE_PATHS)) {
    const seed = SERIES.find((candidate) => candidate.key === key);
    for (const step of path) {
      rows.push({
        series_id: `ser-${key}`,
        series_key: key,
        asof: step.asof,
        value: null,
        display: step.display,
        unit: null,
        tier: step.tier,
        source: step.source,
        released: null,
        observed_at: step.asof === seed?.asof ? evaluatedAt : null,
        note: step.note ?? null,
      });
    }
  }
  return rows;
}

/* ====================================================== the whole snapshot */

export interface FixtureSnapshot {
  snapshotRows: unknown[];
  conditions: unknown[];
  phaseScores: unknown[];
  phaseCalls: unknown[];
  scenarioSet: unknown[];
  scenarios: unknown[];
  targets: unknown[];
  setups: unknown[];
  strategies: unknown[];
  catalysts: unknown[];
  calibration: unknown[];
  weekGrades: unknown[];
  setupGrades: unknown[];
  rules: unknown[];
  observations: unknown[];
  scenarioSetHistory: unknown[];
  scenarioHistory: unknown[];
  phaseHistory: unknown[];
  setupHistory: unknown[];
  findings: unknown[];
  gaps: unknown[];
  runs: unknown[];
  drift: unknown[];
  runRequests: unknown[];
  tailRisks: unknown[];
  tailRiskHistory: unknown[];
  watchlist: unknown[];
  holdings: unknown[];
  holdingEvents: unknown[];
  manualPositions: unknown[];
  watchTickers: unknown[];
  feeds: unknown[];
}

export function fixtureSnapshot(dataset: FixtureDataset): FixtureSnapshot {
  const honest = dataset === "honest";
  const evaluatedAt = honest ? RESCORE : FULL_RUN;

  /* -- the snapshot ------------------------------------------------------ */
  const snapshotRows = SERIES.map((seed) =>
    snapshotRowJson(seed, dataset, evaluatedAt, seed.ordinal),
  );

  /* -- conditions -------------------------------------------------------- */
  const phaseByOwner = new Map<string, number>();
  const conditions: unknown[] = PHASE_CONDITIONS.map((seed) => {
    const ordinal = (phaseByOwner.get(seed.owner) ?? 0) + 1;
    phaseByOwner.set(seed.owner, ordinal);
    // The equities breadth condition names the series that lost its day, so in
    // the honest dataset it is unevaluable rather than failed. That is the
    // whole distinction: an unevaluated condition counted as failed would make
    // the read look weaker than the evidence says.
    const gapped =
      honest && seed.owner === EQ_CALL_ID && seed.text.startsWith("Breadth");
    return {
      condition_id: `cond-${seed.owner}-${ordinal}`,
      owner_kind: "phase",
      owner_id: seed.owner,
      ordinal,
      text: seed.text,
      kind: seed.kind,
      status: gapped
        ? "unevaluable"
        : honest && seed.honest
          ? seed.honest.status
          : seed.status,
      current_display: gapped
        ? "no reading for 2026-09-18: the CNN and breadth scrapers both failed and the day cannot be recovered"
        : honest && seed.honest
          ? seed.honest.current
          : seed.current,
      evidence: gapped ? { missing_series: "breadth_spx_above_200dma" } : {},
      evaluated_at: evaluatedAt,
      void_reason: null,
      void_at: null,
      source: seed.kind === "manual" ? "asserted" : "computed",
    };
  });

  const clauseByOwner = new Map<string, number>();
  for (const seed of CLAUSES) {
    const ordinal = (clauseByOwner.get(seed.scenario) ?? 0) + 1;
    clauseByOwner.set(seed.scenario, ordinal);
    // The Senior Credit Officer survey was rescheduled out of the horizon, so
    // the clause is voided. It is kept, stays in the denominator, and the
    // n_of threshold is not renormalised: renormalising would quietly make the
    // scenario easier to satisfy and corrupt the Brier record.
    const voided =
      honest && seed.scenario === "S2" && seed.text.includes("Senior Credit Officer");
    conditions.push({
      condition_id: `clause-${seed.scenario}-${ordinal}`,
      owner_kind: "trigger",
      owner_id: seed.scenario,
      ordinal,
      text: seed.text,
      kind: seed.kind,
      status: voided ? "void" : seed.status,
      current_display: voided ? null : seed.current,
      evidence: {},
      evaluated_at: evaluatedAt,
      void_reason: voided
        ? "The Senior Credit Officer Opinion Survey was rescheduled from 24 September to 5 November, outside this scenario's one-to-two-week horizon. The clause is kept and still counts in the denominator; the trigger threshold is not renormalised."
        : null,
      void_at: voided ? RESCORE : null,
      source: seed.kind === "manual" ? "asserted" : "computed",
    });
  }

  /* -- phase scores ------------------------------------------------------ */
  const phaseScores = [
    {
      asset: "equities",
      phase_call_id: EQ_CALL_ID,
      phase: 3,
      phase_name: "Late Cycle / Distribution",
      stage: "late",
      // All four hold in the clean dataset. In the honest one breadth has no
      // reading for the day, so it is unevaluable and never counted as failed.
      met: honest ? 3 : 4,
      of: 4,
      machine_checked: honest ? 3 : 4,
      manual: 0,
      unevaluable: honest ? 1 : 0,
      failed: 0,
      read_stale: false,
      evaluated_at: evaluatedAt,
    },
    {
      asset: "crypto",
      phase_call_id: CR_CALL_ID,
      phase: 1,
      phase_name: "Early Bull",
      stage: "late",
      // The honest evening rescore: the ETF streak broke and the flush was not
      // bought back, so two of the four things the read holds on have failed
      // and this week's read may be out of date. Flagged, never acted on.
      met: honest ? 2 : 4,
      of: 4,
      machine_checked: 3,
      manual: 1,
      unevaluable: 0,
      failed: honest ? 2 : 0,
      read_stale: honest,
      evaluated_at: evaluatedAt,
    },
  ];

  /* -- scenarios --------------------------------------------------------- */
  const scenarios = SCENARIOS.map((scenario) => {
    const clauses = CLAUSES.filter((c) => c.scenario === scenario.id);
    const all = TARGETS.filter((t) => t.scenario === scenario.id);
    const bands = all.filter((t) => t.series !== null);
    const premiseMoved = honest && scenario.id === "S2";
    const voidCount = premiseMoved ? 1 : 0;
    return {
      scenario_id: scenario.id,
      set_id: SET_ID,
      ordinal: scenario.ordinal,
      title: scenario.title,
      probability: scenario.probability,
      positioning: scenario.positioning,
      clauses_fired: clauses.filter((c) => c.status === "met").length,
      // The void clause stays in the total. It is never removed.
      clauses_total: clauses.length,
      clauses_void: voidCount,
      required_unmet: clauses.filter((c) => c.required && c.status !== "met")
        .length,
      // Two denominators. `bands_*` counts only the automatically scorable
      // targets; `targets_*` counts every one, manual included, and is the `n`
      // that `targets hit x of n` uses at grading.
      bands_holding: bands.filter((t) => t.holding === true).length,
      bands_total: bands.length,
      targets_holding: all.filter((t) => t.holding === true).length,
      targets_total: all.length,
      premise_moved: premiseMoved,
      evaluated_at: evaluatedAt,
    };
  });

  const targets = TARGETS.map((seed, index) => ({
    target_id: `tg-${seed.scenario}-${index}`,
    scenario_id: seed.scenario,
    series_id: seed.series === null ? null : `ser-${seed.series}`,
    label: seed.label,
    kind: seed.series === null ? "manual" : "band",
    // Both added in migration 0014. `comparator` is what tells a one-sided
    // `lt` band from a two-sided band whose floor was never written, which
    // render identically without it and are not the same statement. `text`
    // carries a manual target's sentence, which has no numbers at all and
    // still counts toward targets_n.
    comparator: seed.comparator,
    lo: seed.lo === null ? null : n(seed.lo, 2),
    hi: seed.hi === null ? null : n(seed.hi, 2),
    // A band written against the series that lost its day reads unevaluable,
    // and `holding: null` is the contract's way of saying nothing is claimed.
    current:
      honest && seed.series === "breadth_spx_above_200dma"
        ? null
        : seed.current === null
          ? null
          : n(seed.current, 2),
    holding:
      honest && seed.series === "breadth_spx_above_200dma"
        ? null
        : seed.holding,
    distance_to_breach:
      honest && seed.series === "breadth_spx_above_200dma"
        ? null
        : seed.distance === null
          ? null
          : n(seed.distance, 2),
    text: seed.text ?? null,
    evaluated_at: evaluatedAt,
  }));

  /* -- setups ------------------------------------------------------------ */
  // The honest dataset carries a fifth setup that names no series. The worker
  // will not score it, so its gate and entry states were never computed, and
  // without `series_key` it would sit there looking like a bug rather than a
  // refusal.
  const seeds: readonly SetupSeed[] = honest
    ? [
        ...SETUPS,
        {
          id: "su-eq-sox",
          series: null,
          style: "O/U, HTF",
          asset: "equities",
          name: "Semis relative-strength fade",
          entryLo: 5900,
          entryHi: 6000,
          invalidation: 6120,
          invalidationDir: "above",
          target: 5500,
          rr: 2.6,
          gateText: "memory underperforming the index on the entry close",
          row: "CRITICAL",
        },
      ]
    : SETUPS;
  const setups = seeds.map((seed) => {
    // In the honest dataset the S&P setup's band was reached intraday and the
    // gate refused, which is the exact failure that occurred in W37, W38 and
    // W39 and was each time found the following Monday.
    const refused = honest && seed.id === "su-eq-spx";
    return {
      setup_id: seed.id,
      week: WEEK,
      asset: seed.asset,
      name: seed.name,
      entry_lo: n(seed.entryLo, 2),
      entry_hi: n(seed.entryHi, 2),
      invalidation: n(seed.invalidation, 2),
      invalidation_dir: seed.invalidationDir,
      target: n(seed.target, 2),
      rr_planned: n(seed.rr, 2),
      // A setup nobody scored has no gate verdict either. `unevaluable` is the
      // honest state: not open, and not blocked on anything that was checked.
      gate_state:
        seed.series === null
          ? "unevaluable"
          : refused
            ? "blocked"
            : seed.id === "su-cr-btc"
              ? "blocked"
              : "open",
      gate_reason:
        seed.series === null
          ? "This setup names no series, so its levels cannot be resolved against a price and the worker does not score it. Nothing here was evaluated."
          : refused
            ? "HY at 270 bps passes, but breadth printed 44.10% on the entry close, under the 45% floor. The gate refused."
            : seed.id === "su-cr-btc"
              ? "No daily close above $82,000 yet, so the acceptance condition the entry depends on does not exist."
              : null,
      gate_text: seed.gateText,
      band_touched_today: refused,
      band_touched_close: refused,
      entry_state: "not triggered",
      sessions_carried: honest ? 3 : 0,
      r_running: null,
      sector_focus_label: FOCUS_TEXT[seed.row],
      series_key: seed.series?.key ?? null,
      series_label: seed.series?.label ?? null,
      evaluated_at: evaluatedAt,
    };
  });

  /* -- strategy ---------------------------------------------------------- */
  // `sector_focus` carries an id and `holdings_check` points at it, exactly as
  // migration 0012 aggregates them. The band is not denormalised into the
  // holding: the array on the same row already resolves the id, and two copies
  // would give a reader two places to disagree about one fact.
  const strategies = [
    {
      strategy_id: "st-eq-w39",
      week: WEEK,
      asset: "equities",
      stance:
        "Selective, treating the index as a duration trade for as long as breadth sits under 55%.",
      posture:
        "Hold and upgrade quality. Phase 3, late, with nothing that prices risk moving, so there is no case to cut gross; breadth through 50% is no case to add either.",
      exposure_lo: n(25, 0),
      exposure_hi: n(50, 0),
      exposure_operated_lo: null,
      exposure_operated_hi: null,
      sector_focus: [
        { id: "sf-eq-critical", band: "CRITICAL", text: FOCUS_TEXT.CRITICAL },
        { id: "sf-eq-hold", band: "HOLD", text: FOCUS_TEXT.HOLD },
        { id: "sf-eq-watch", band: "WATCH", text: FOCUS_TEXT.WATCH },
        { id: "sf-eq-avoid", band: "AVOID", text: FOCUS_TEXT.AVOID },
      ],
      // Empty on purpose. The recorded positions these fixtures carried were
      // the operator's real W39 book and are out of date, so they are cleared
      // rather than left to be read as current. The holdings check itself is
      // untouched: `macro_core.holding` is re-parsed from the Watchlist on
      // every run, and the surface renders the list when there is one and its
      // named empty state when there is not.
      holdings_check: [],
      activated_at: FULL_RUN,
    },
    {
      strategy_id: "st-cr-w39",
      week: WEEK,
      asset: "crypto",
      stance: "Selective and constructive, adding on confirmed fund flow rather than on price.",
      posture:
        "Hold. Phase 1, late: price and sentiment look like Phase 2 but the flow has not arrived, so the book does not size up on a squeeze.",
      exposure_lo: n(50, 0),
      exposure_hi: n(110, 0),
      exposure_operated_lo: n(60, 0),
      exposure_operated_hi: n(70, 0),
      sector_focus: [
        { id: "sf-cr-critical", band: "CRITICAL", text: "SOL: the Innovation Exemption names the venue where roughly $465m of tokenised equity already trades, and SOL led the majors on the week." },
        { id: "sf-cr-hold", band: "HOLD", text: "BTC: it cleared $80K, but on a short squeeze with a rounding-error ETF week, so hold the core and let the flow condition decide the add." },
        { id: "sf-cr-watch", band: "WATCH", text: "ETH: price reclaimed $2,600 while its own ETFs sold $140.6M, the cleanest price-versus-flow divergence in the complex." },
        { id: "sf-cr-avoid", band: "AVOID", text: "Chasing the tokenisation headline into illiquid venue proxies before the 20 October SEC comment deadline defines the perimeter." },
      ],
      holdings_check: [],
      activated_at: FULL_RUN,
    },
  ];

  /* -- quality ----------------------------------------------------------- */
  const findings: unknown[] = [];
  const gaps: unknown[] = [];
  if (honest) {
    gaps.push({
      id: 1,
      series_id: "ser-cnn_fear_greed",
      series_key: "cnn_fear_greed",
      asof: FRI,
      reason:
        "CNN's own API answered 451 and both aggregators answered 403. This is a point-in-time source with no history endpoint, so the reading for this day cannot be recovered.",
      permanent: true,
      opened_at: FULL_RUN,
    });
    findings.push(
      {
        id: 1,
        check_key: "source_incomplete",
        subject: "CNN Fear & Greed",
        sentence:
          "No reading exists for 2026-09-18 and none can be obtained. Nothing is interpolated and the prior value is not carried forward, so any condition or band referencing this series on that date reports unevaluable rather than false.",
        severity: 2,
        opened_at: FULL_RUN,
        closed_at: null,
      },
      {
        id: 2,
        check_key: "condition_coverage",
        subject: "phase_call equities",
        sentence:
          "1 of 4 equities phase conditions is unevaluable: breadth has no reading for 2026-09-18. It is never counted as failed, so the read shows 3 of 4 holding and 1 unknown.",
        severity: 2,
        opened_at: RESCORE,
        closed_at: null,
      },
      {
        id: 4,
        check_key: "scenario_premise_moved",
        subject: "S2 clause 3",
        sentence:
          "The Senior Credit Officer Opinion Survey named by one of S2's trigger clauses was rescheduled to 5 November, outside the scenario's horizon. The clause is void and kept in the denominator; the threshold is not renormalised and the Brier score will not be forgiven.",
        severity: 1,
        opened_at: RESCORE,
        closed_at: null,
      },
      {
        id: 7,
        check_key: "phase_read_stale",
        subject: "phase_call crypto",
        sentence:
          "This week's crypto phase read may be out of date: 2 of the 4 things it holds on have failed since Monday, the BTC ETF inflow streak and liquidation dips being bought back. The phase is read fresh again on Mon 28 Sep.",
        severity: 2,
        opened_at: RESCORE,
        closed_at: null,
      },
      {
        id: 5,
        check_key: "source_incomplete",
        subject: "search agent",
        sentence:
          "No observation for 2026-09-18 against a next_expected of 2026-09-18. The 09:30 search agent did not complete and this series is overdue, which is a different state from the weekly series that are merely between prints.",
        severity: 2,
        opened_at: RESCORE,
        closed_at: null,
      },
      {
        id: 6,
        check_key: "holdings_check_missing",
        subject: "GOOG",
        sentence:
          "GOOG is an active equities holding with no holdings-check line on the active equities strategy, so the week's strategy does not say what to do with a position the book actually holds.",
        severity: 1,
        opened_at: FULL_RUN,
        closed_at: null,
      },
    );
  } else {
    findings.push({
      id: 1,
      check_key: "holdings_check_missing",
      subject: "GOOG",
      sentence:
        "GOOG is an active equities holding with no holdings-check line on the active equities strategy, so the week's strategy does not say what to do with a position the book actually holds.",
      severity: 1,
      opened_at: FULL_RUN,
      closed_at: null,
    });
  }

  const runs = [
    {
      id: 1,
      source: "yahoo",
      run_kind: honest ? "rescore" : "full",
      started_at: evaluatedAt,
      finished_at: evaluatedAt,
      rows: 17,
      status: "ok",
      error: null,
    },
    {
      id: 2,
      source: "fred",
      run_kind: honest ? "rescore" : "full",
      started_at: evaluatedAt,
      finished_at: evaluatedAt,
      rows: 5,
      status: "ok",
      error: null,
    },
    {
      id: 3,
      source: "cftc",
      run_kind: honest ? "rescore" : "full",
      started_at: evaluatedAt,
      finished_at: evaluatedAt,
      rows: honest ? 0 : 2,
      status: "ok",
      error: null,
    },
    {
      id: 4,
      source: "search:cnn_fear_greed",
      run_kind: "full",
      started_at: FULL_RUN,
      finished_at: FULL_RUN,
      rows: honest ? 0 : 1,
      status: honest ? "failed" : "ok",
      error: honest ? "451 from cnn.com, 403 from both aggregators" : null,
    },
    // The three desk feeds' own runs. The Data page's footer leaves them out:
    // the Catalysts page's freshness line is where they are read.
    ...feedRunsJson(honest),
  ];

  /* -- history ----------------------------------------------------------- */
  const W38_FROM = "2026-09-14T01:30:00.000Z";

  // `scenario_set_history` projects macro_core.scenario_set, so it carries the
  // set and not the three scenarios inside it. Its primary key column is `id`.
  //
  // The two rows deliberately carry `swing_variables` in the two shapes that
  // exist. W38 holds the bare array the column actually stores: it is declared
  // `jsonb not null default '[]'::jsonb` and every set in the database is an
  // array of `{name, cool, hot}`. W39 holds the `{variables: [...]}` wrapper an
  // early draft wrote. A fixture that only ever exercised the wrapper is how the
  // reader came to assert it, and how the live deployment came to hide every
  // week behind a validator banner.
  const scenarioSetHistory = [
    {
      id: SET_ID,
      week: WEEK,
      synthesis: SYNTHESIS,
      probability_check: PROBABILITY_CHECK,
      swing_variables: { variables: SWING_VARIABLES },
      rules_waived: [],
      status: "active",
      authored_at: FULL_RUN,
      valid_from: FULL_RUN,
      valid_to: null,
      superseded_by: null,
      amends: null,
      amend_reason: null,
    },
    {
      id: "set-w38",
      week: "2026-W38",
      synthesis:
        "The week's question is whether a hiking Fed can coexist with a 5% ten-year and triple-digit crude without the risk complex repricing.",
      probability_check:
        "The base stays at 45%. S2 was lifted from 30% to 36% mid-week on the 20Y's record tail, which is a material change rather than noise; R4 gates it on VIX, HY or breadth.",
      swing_variables: [
        {
          name: "The 20Y auction on 18 September, and whether its tail was the exception",
          cool: "the 2Y/5Y/7Y cluster clears near where-issued and the long end settles",
          hot: "a second record tail, which is demand failing rather than price adjusting",
        },
        {
          name: "Whether a hiking Fed can coexist with a 5% ten-year",
          cool: "the risk complex keeps absorbing it and credit spreads stay tight",
          hot: "credit reprices and the index's narrow leadership goes with it",
        },
      ],
      rules_waived: [10],
      status: "superseded",
      authored_at: W38_FROM,
      valid_from: W38_FROM,
      valid_to: FULL_RUN,
      superseded_by: SET_ID,
      amends: null,
      amend_reason:
        "S2 was re-priced from 30% to 36% on 18 September. The 20Y auction tailed 2 bp at a record 5.42%, which is the first hard evidence behind a tail-risk item that had only commentary behind it. A material change, not a daily nudge: daily runs compute scenario state and never touch a stated probability.",
    },
  ];

  // `macro_mart.scenario_history`, added in migration 0014. Each of a set's
  // three scenarios with the probability it carried, and the set's own
  // synthesis and lifecycle repeated onto each row because the view joins them.
  const scenarioHistory = [
    ...SCENARIOS.map((scenario) => ({
      scenario_id: scenario.id,
      set_id: SET_ID,
      week: WEEK,
      ordinal: scenario.ordinal,
      title: scenario.title,
      probability: scenario.probability,
      positioning: scenario.positioning,
      status: "active",
      synthesis: SYNTHESIS,
      probability_check: PROBABILITY_CHECK,
      valid_from: FULL_RUN,
      valid_to: null,
      superseded_by: null,
      amends: null,
      amend_reason: null,
    })),
    // W38, superseded, as it stood after the mid-week amendment.
    //
    // The three still sum to 100. A re-price is a re-price of the set: S2 went
    // from 30 to 36 on the 20Y's record tail, and the six points came off S1
    // and S3 rather than being added to the total. A constraint asserts the
    // sum, so an amendment that moved one probability alone would not have
    // been writable, and a fixture that showed 106 would be teaching the
    // reader that the constraint does not hold.
    ...[
      { id: "S1", ordinal: 1, title: "The Fed hikes with a path while oil stays above $100", probability: 42, positioning: "Hold quality; no index adds until breadth reclaims 55%." },
      { id: "S2", ordinal: 2, title: "Duration fails and the long end transmits", probability: 36, positioning: "Cut high-beta, not quality, and only on a confirming leg." },
      { id: "S3", ordinal: 3, title: "Oil breaks the inflation leg and breadth repairs", probability: 22, positioning: "Add only after breadth reclaims 55%." },
    ].map((scenario) => ({
      scenario_id: `${scenario.id}-w38`,
      set_id: "set-w38",
      week: "2026-W38",
      ordinal: scenario.ordinal,
      title: scenario.title,
      probability: scenario.probability,
      positioning: scenario.positioning,
      status: "superseded",
      synthesis:
        "The week's question is whether a hiking Fed can coexist with a 5% ten-year and triple-digit crude without the risk complex repricing.",
      probability_check:
        "The base stays at 45%. S2 was lifted from 30% to 36% mid-week on the 20Y's record tail, which is a material change rather than noise; R4 gates it on VIX, HY or breadth.",
      valid_from: W38_FROM,
      valid_to: FULL_RUN,
      superseded_by: SET_ID,
      amends: null,
      amend_reason:
        scenario.id === "S2"
          ? "Re-priced from 30% to 36% on 18 September. The 20Y auction tailed 2 bp at a record 5.42%, the first hard evidence behind a tail-risk item that had only commentary behind it. The six points came off S1 and S3, which is why all three moved. A material change, not a daily nudge: daily runs compute scenario state and never touch a stated probability."
          : null,
    })),
  ];

  // `phase_call_history` names its primary key `id`, not `phase_call_id`.
  const phaseHistory = [
    ...PHASE_CALLS.map((call) => ({
      id: call.phase_call_id,
      asset: call.asset,
      week: call.week,
      phase: call.phase,
      phase_name: call.phase_name,
      prior_phase: call.prior_phase,
      direction: call.direction,
      reason: call.reason,
      evidence: call.evidence,
      falsifier: call.falsifier,
      status: "active",
      authored_at: FULL_RUN,
      valid_from: FULL_RUN,
      valid_to: null,
      superseded_by: null,
      amends: null,
      amend_reason: null,
    })),
    {
      id: "pc-eq-w38",
      asset: "equities",
      week: "2026-W38",
      phase: 3,
      phase_name: "Late Cycle / Distribution",
      prior_phase: 3,
      direction: "unchanged",
      reason:
        "Phase 3 held and narrowed: breadth through 50%, the Dow's third straight losing week, only the Nasdaq up.",
      evidence: [
        "Every contraction condition moved away: VIX 14.81, MOVE down, HY flat, IG tighter.",
      ],
      falsifier:
        "Phase 4 needs three of four: a close below 7,450 that fails its retest, breadth under 45%, HY above 300 bps, VIX above 22 with MOVE above 90.",
      status: "superseded",
      authored_at: W38_FROM,
      valid_from: W38_FROM,
      valid_to: FULL_RUN,
      superseded_by: EQ_CALL_ID,
      amends: null,
      amend_reason: null,
    },
    {
      id: "pc-cr-w38",
      asset: "crypto",
      week: "2026-W38",
      phase: 1,
      phase_name: "Early Bull",
      prior_phase: 1,
      direction: "unchanged",
      reason:
        "Phase 1 held. BTC defended the $74K falsifier by under $1,000 then cleared $80K, but the ETF week finished at +$6.2M.",
      evidence: ["ETH's four-week inflow streak snapped at -$140.6M."],
      falsifier:
        "A close back under $76K that breaks the 15 September low puts the $74K falsifier live again.",
      status: "superseded",
      authored_at: W38_FROM,
      valid_from: W38_FROM,
      valid_to: FULL_RUN,
      superseded_by: CR_CALL_ID,
      amends: null,
      amend_reason: null,
    },
  ];

  /* -- setup history ----------------------------------------------------- */
  // What each setup was WRITTEN as. The evaluated columns of setup_state are
  // absent by design: they are recomputed from the tape on every run and were
  // never stored per version, so there is no past value of them to project.
  const setupHistory = [
    ...SETUPS.map((seed) => ({
      setup_id: seed.id,
      week: WEEK,
      asset: seed.asset,
      name: seed.name,
      style: seed.style,
      entry_lo: n(seed.entryLo, 2),
      entry_hi: n(seed.entryHi, 2),
      invalidation: n(seed.invalidation, 2),
      invalidation_dir: seed.invalidationDir,
      target: n(seed.target, 2),
      rr_planned: n(seed.rr, 2),
      gate_text: seed.gateText,
      sector_focus_label: FOCUS_TEXT[seed.row] ?? null,
      rationale: null,
      status: "active",
      authored_at: FULL_RUN,
      valid_from: FULL_RUN,
      valid_to: null,
      superseded_by: null,
      amends: null,
      amend_reason: null,
    })),
    {
      setup_id: "su-eq-spx-w38",
      week: "2026-W38",
      asset: "equities",
      name: "S&P shelf FTR long",
      style: "FTR, HTF",
      entry_lo: n(7540, 2),
      entry_hi: n(7590, 2),
      invalidation: n(7470, 2),
      invalidation_dir: "below",
      target: n(7780, 2),
      rr_planned: n(2.3, 2),
      gate_text: "breadth above 55% and HY under 285 bps",
      sector_focus_label: FOCUS_TEXT.HOLD ?? null,
      rationale: null,
      status: "superseded",
      authored_at: W38_FROM,
      valid_from: W38_FROM,
      valid_to: FULL_RUN,
      superseded_by: "su-eq-spx",
      amends: null,
      amend_reason: null,
    },
    {
      setup_id: "su-cr-eth-w38",
      week: "2026-W38",
      asset: "crypto",
      name: "ETH breakout-shelf FTR",
      style: "FTR, HTF",
      entry_lo: n(2400, 2),
      entry_hi: n(2460, 2),
      invalidation: n(2290, 2),
      invalidation_dir: "below",
      target: n(2850, 2),
      rr_planned: n(3.0, 2),
      gate_text: "ETH ETF flows positive on the week",
      sector_focus_label: null,
      rationale: null,
      status: "superseded",
      authored_at: W38_FROM,
      valid_from: W38_FROM,
      valid_to: FULL_RUN,
      superseded_by: "su-cr-eth",
      amends: null,
      amend_reason:
        "The ETF-flows-positive clause is dropped. It blocked a structurally correct entry for roughly +1.7R, and ETH's higher low at $2,358 is the structure the trade is actually built on. R10 is consciously waived and the invalidation is tightened to pay for it.",
    },
  ];

  return {
    snapshotRows,
    conditions,
    phaseScores,
    phaseCalls: PHASE_CALLS,
    scenarioSet: [
      {
        set_id: SET_ID,
        week: WEEK,
        synthesis: SYNTHESIS,
        probability_check: PROBABILITY_CHECK,
        swing_variables: { variables: SWING_VARIABLES },
        rules_waived: [],
        activated_at: FULL_RUN,
      },
    ],
    scenarios,
    targets,
    setups,
    strategies,
    catalysts: CATALYSTS,
    drift: [driftJson(honest, evaluatedAt)],
    // A fresh array per snapshot: the fixture source's Refresh writes into it.
    runRequests: runRequestsJson(honest),
    tailRisks: tailRisksJson(honest),
    tailRiskHistory: tailRiskHistoryJson(honest),
    watchlist: WATCHLIST,
    holdings: HOLDINGS,
    holdingEvents: HOLDING_EVENTS,
    // Fresh arrays per snapshot: the fixture source's list editor writes into them.
    manualPositions: MANUAL_POSITIONS.map((row) => ({ ...row })),
    watchTickers: WATCH_TICKERS.map((row) => ({ ...row })),
    feeds: feedsJson(honest),
    calibration: [
      {
        id: 1,
        graded_weeks: 16,
        base_hit_score: n(4.5, 1),
        base_materialized: 1,
        mean_brier: n(0.268, 3),
        brier_weeks: 3,
        base_was_s2: 6,
        window: 8,
        computed_at: FULL_RUN,
      },
    ],
    weekGrades: WEEK_GRADES.map((row) => ({
      week: row.week,
      base_scenario_id: null,
      base_scenario_label: row.label,
      base_grade: row.grade,
      other_grades: row.other,
      eq_call: row.eq,
      crypto_call: row.crypto,
      key_miss: row.miss,
      targets_hit: row.hit,
      targets_n: row.of,
      brier: row.brier === null ? null : n(row.brier, 3),
      premise_moved: false,
      // Null for the migrated weeks: their grading moment is unknown, and null
      // never zero applies to a timestamp as much as to a figure.
      graded_at: row.brier === null ? null : FULL_RUN,
    })),
    setupGrades: SETUP_GRADES.map((row) => ({
      setup_id: row.id,
      graded_week: row.graded,
      proposed_week: row.week,
      asset: row.asset,
      name: row.name,
      outcome: row.outcome,
      r_realised: row.r === null ? null : n(row.r, 1),
      note: row.note,
    })),
    rules: RULES.map((rule) => ({
      number: rule.number,
      text: rule.text,
      retired_week: null,
      teaching_weeks: rule.weeks,
      fire_count: rule.weeks.length,
    })),
    observations: observationRows(dataset, evaluatedAt),
    scenarioSetHistory,
    scenarioHistory,
    phaseHistory,
    setupHistory,
    findings,
    gaps,
    runs,
  };
}

/* ------------------------------------------------------------ long prose -- */

const SYNTHESIS =
  "The Fed hiked into a 5% ten-year and a closed Hormuz, and the risk complex answered by sending the VIX to a run low and tightening investment-grade spreads, which is the week's real information: nothing that prices risk is treating the macro as a risk. What is pricing it is the index's composition, where breadth broke 50%, the Dow lost a third straight week, and the only leg working is a memory trade whose shortage was confirmed by its largest buyer rather than by its sellers. Crypto broke $80K off an entirely different driver, so the two books are no longer trading the same variable and should not share a scenario row.";

const PROBABILITY_CHECK =
  "The ledger's base case scores 4.5 of the last 8 graded weeks with only 1 full materialization and a mean Brier of 0.27 across three scored weeks, and last week's 0.105 was the best of that record. The record says the habit is a base that is directionally right and half-graded, which argues for tighter bands rather than a higher probability. The base stays at 45%: its rates, credit and breadth legs all extend a trend that three consecutive weeks have confirmed, but it cannot go higher while the 22–24 September cluster and Micron are both unresolved and both genuinely binary. S2 is lifted to 32% because the 20Y's record tail is the first hard evidence behind a tail-risk item that had only commentary behind it. R2 and R11 are met with oil two-sided across all three branches and its own band in each. R4 gates S2 on VIX, HY or breadth. R5 gives leadership its own line in every branch. R6 reserves the dateless shocks inside S2. R7 and R12 give crypto its own row priced off the regulatory channel that actually moved it. R10 keeps the two-session BTC inflow run out of the base case's flow target.";

const SWING_VARIABLES = [
  {
    name: "The 22–24 September 2Y/5Y/7Y cluster, after the 20Y tailed 2 bp at a record 5.42%",
    cool: "the cluster clears near where-issued and the 10Y falls back under 4.95%",
    hot: "a second tail with the 10Y through 5.12% and the 2Y through 4.90%, which is demand failing rather than price adjusting",
  },
  {
    name: "Micron Q4 FY2026 on 30 September, now the whole complex's print",
    cool: "HBM pricing and a raised guide confirm Intel's shortage framing and memory pulls breadth up with it",
    hot: "any sign of customer pushback on five-to-sevenfold memory prices turns the index's only working leg into the crowded-AI unwind",
  },
  {
    name: "Oil as the East-West line restores into OPEC+ on 4 October",
    cool: "full restoration or an Oman reschedule takes Brent under $92 and pulls the inflation leg out of the October decision",
    hot: "the 19 September Houthi pattern reaches the repaired line, or Hormuz enforcement returns, and Brent clears $115",
  },
  {
    name: "The 28 October hike, priced at a coin flip, against PCE on 30 September and NFP on 2 October",
    cool: "core PCE at 0.2% with payrolls under 125K pushes October back to a hold and the 2Y under 4.70%",
    hot: "0.3% with firm payrolls takes October past 70% and validates the 2027 dots",
  },
];

const CATALYSTS = [
  { catalyst_id: "cat-auctions", name: "US 2Y/5Y/7Y Treasury auction cluster", starts_at: "2026-09-22T17:00:00.000Z", ends_at: null, date_precision: "day", date_text: "auctions 2026-09-22 to 2026-09-24 at 13:00 ET", date_estimated: false, family: "treasury_auction", category: "treasury_liquidity", affected: ["rates", "USD", "equities"], days_out: 1, referenced_by: ["clause-S1-1", "clause-S2-1", "clause-S3-3"], source_url: "https://home.treasury.gov/system/files/221/Tentative-Auction-Schedule.pdf" },
  { catalyst_id: "cat-meta-connect", name: "Meta Connect 2026 keynote and developer sessions", starts_at: "2026-09-23T00:00:00.000Z", ends_at: "2026-09-25T00:00:00.000Z", date_precision: "window", date_text: "2026-09-23 to 2026-09-24 PT / 2026-09-23 to 2026-09-25 SGT; exact session times TBD", date_estimated: false, family: "meta_connect", category: "ai_tech", affected: ["AI devices", "megacap tech", "Nasdaq"], days_out: 2, referenced_by: [], source_url: "https://www.metaconnect.com/" },
  { catalyst_id: "cat-scoos", name: "Fed Senior Credit Officer Opinion Survey on Dealer Financing Terms", starts_at: "2026-09-24T18:00:00.000Z", ends_at: null, date_precision: "day", date_text: "2026-09-24 14:00 ET / 2026-09-25 02:00 SGT", date_estimated: false, family: "fed_scoos", category: "fed", affected: ["dealer financing", "repo", "hedge-fund leverage"], days_out: 3, referenced_by: ["clause-S2-2"], source_url: null },
  { catalyst_id: "cat-pharma", name: "100% pharma tariff extends to non-Annex-III manufacturers", starts_at: "2026-09-29T04:00:00.000Z", ends_at: null, date_precision: "day", date_text: "2026-09-29", date_estimated: false, family: null, category: "trade_tariffs", affected: ["pharma", "trade"], days_out: 8, referenced_by: [], source_url: null },
  { catalyst_id: "cat-micron", name: "Micron Q4 FY2026", starts_at: "2026-09-30T20:30:00.000Z", ends_at: null, date_precision: "day", date_text: "2026-09-30 16:30 ET / 2026-10-01 04:30 SGT", date_estimated: false, family: "earnings_mu", category: "earnings", affected: ["HBM", "DRAM", "semis", "Nasdaq"], days_out: 9, referenced_by: ["clause-S1-3", "clause-S3-4"], source_url: null },
  { catalyst_id: "cat-pce", name: "August Personal Income and Outlays / core PCE", starts_at: "2026-09-30T12:30:00.000Z", ends_at: null, date_precision: "day", date_text: "2026-09-30 08:30 ET / 2026-09-30 20:30 SGT", date_estimated: false, family: "us_pce", category: "us_macro", affected: ["inflation", "rates", "crypto"], days_out: 9, referenced_by: ["clause-S1-2", "clause-S3-2"], source_url: null },
  { catalyst_id: "cat-nfp", name: "September Employment Situation / NFP", starts_at: "2026-10-02T12:30:00.000Z", ends_at: null, date_precision: "day", date_text: "2026-10-02 08:30 ET / 2026-10-02 20:30 SGT", date_estimated: false, family: "us_nfp", category: "us_macro", affected: ["rates", "USD", "equities", "crypto"], days_out: 11, referenced_by: ["clause-S3-2"], source_url: null },
  { catalyst_id: "cat-opec", name: "OPEC+ voluntary-adjustment countries meeting", starts_at: "2026-10-04T11:00:00.000Z", ends_at: null, date_precision: "day", date_text: "2026-10-04; exact time TBD", date_estimated: false, family: "opec_meeting", category: "commodities_energy", affected: ["oil", "inflation expectations"], days_out: 13, referenced_by: ["clause-S3-1"], source_url: null },
  { catalyst_id: "cat-sohn", name: "Sohn San Francisco Investment Conference", starts_at: "2026-10-13T00:00:00.000Z", ends_at: null, date_precision: "day", date_text: "2026-10-13", date_estimated: false, family: "sohn_conference", category: "investor_forum", affected: ["U.S. equities", "hedge-fund positioning"], days_out: 22, referenced_by: [], source_url: "https://www.sohnconference.org/san-francisco" },
  { catalyst_id: "cat-cpi", name: "September CPI and the Fed Beige Book", starts_at: "2026-10-14T12:30:00.000Z", ends_at: null, date_precision: "day", date_text: "2026-10-14 08:30 ET / 2026-10-14 20:30 SGT", date_estimated: false, family: "us_cpi", category: "us_macro", affected: ["inflation", "rates", "USD", "gold"], days_out: 23, referenced_by: [], source_url: null },
  { catalyst_id: "cat-sec-crypto", name: "SEC Regulation Crypto Assets proposed-rule comment deadline", starts_at: "2026-10-20T00:00:00.000Z", ends_at: null, date_precision: "day", date_text: "2026-10-20; exact closing time TBD", date_estimated: false, family: null, category: "crypto", affected: ["BTC", "ETH", "stablecoins"], days_out: 29, referenced_by: [], source_url: "https://www.sec.gov/files/rules/proposed/2026/33-11434.pdf" },
  { catalyst_id: "cat-lpr", name: "October PBoC loan prime rate fixing", starts_at: "2026-10-20T00:00:00.000Z", ends_at: null, date_precision: "day", date_text: "around 2026-10-20 expected; exact date/time TBD", date_estimated: true, family: "pboc_lpr", category: "global_central_bank", affected: ["CNY", "China equities", "Asian rates"], days_out: 29, referenced_by: [], source_url: "https://www.pbc.gov.cn/en/3688229/index.html" },
  { catalyst_id: "cat-tsla", name: "Tesla Q3 2026 earnings", starts_at: "2026-10-11T00:00:00.000Z", ends_at: "2026-10-31T00:00:00.000Z", date_precision: "month", date_text: "mid-to-late October 2026 expected; exact date/time TBD", date_estimated: true, family: "earnings_tsla", category: "earnings", affected: ["megacap tech", "Nasdaq", "EV demand"], days_out: 20, referenced_by: [], source_url: "https://ir.tesla.com/" },
  { catalyst_id: "cat-msft", name: "Microsoft FY2027 Q1 earnings", starts_at: "2026-10-21T00:00:00.000Z", ends_at: "2026-10-31T00:00:00.000Z", date_precision: "month", date_text: "late October 2026 expected; exact date/time TBD", date_estimated: true, family: "earnings_msft", category: "earnings", affected: ["Azure", "hyperscaler capex", "Nasdaq"], days_out: 30, referenced_by: [], source_url: "https://www.microsoft.com/en-us/Investor" },
  { catalyst_id: "cat-fomc", name: "FOMC, 27–28 October", starts_at: "2026-10-28T18:00:00.000Z", ends_at: null, date_precision: "day", date_text: "2026-10-28 14:00 ET / 2026-10-29 02:00 SGT", date_estimated: false, family: "fomc_decision", category: "fed", affected: ["rates", "USD", "equities", "crypto"], days_out: 37, referenced_by: ["clause-S1-2", "clause-S3-2"], source_url: null },
];


/* -- the Desk: drift, the run queue, and the Catalysts page's feeds ------- */

/**
 * The drift a W39 reader saw on the 21st's evening rescore: in the clean
 * dataset a quiet day, in the honest one the day VIX dropped back inside S3's
 * band, a new acute tail risk was listed, and two of the four things the
 * crypto phase read holds on failed, so the read may be out of date. The tail
 * risk and the two condition flips are material and suggest a refresh.
 */
function driftJson(honest: boolean, evaluatedAt: string): Record<string, unknown> {
  const items = honest
    ? [
        {
          kind: "phase_condition",
          subject: "crypto condition 3",
          before: "met",
          after: "failed",
          material: true,
          weight: 3,
          sentence:
            'The crypto phase condition "The BTC ETF inflow streak stays intact" went from met to failed.',
        },
        {
          kind: "phase_condition",
          subject: "crypto condition 4",
          before: "met",
          after: "failed",
          material: true,
          weight: 3,
          sentence:
            'The crypto phase condition "Liquidation dips are still bought back within three sessions" went from met to failed.',
        },
        {
          kind: "tail_risk",
          subject: "Hormuz enforcement returns",
          before: null,
          after: "acute",
          material: true,
          weight: 3,
          sentence: "A new acute tail risk was listed on 2026-09-21: Hormuz enforcement returns.",
        },
        {
          kind: "band",
          subject: "S3 VIX",
          before: "out of band",
          after: "in band",
          material: false,
          weight: 1,
          sentence: "VIX 11.50-15.00 entered S3's band at 14.21.",
        },
        {
          kind: "phase_stale",
          subject: "crypto phase",
          before: null,
          after: "2 of 4 failed",
          material: false,
          weight: 0,
          sentence:
            "This week's crypto phase read may be out of date: 2 of the 4 things it holds on have failed since Monday. The phase is read fresh again next Monday.",
        },
      ]
    : [
        {
          kind: "band",
          subject: "S3 VIX",
          before: "out of band",
          after: "in band",
          material: false,
          weight: 1,
          sentence: "VIX 11.50-15.00 entered S3's band at 14.21.",
        },
      ];
  const score = Math.min(
    10,
    items.reduce((sum, one) => sum + one.weight, 0),
  );
  const material = items.filter((one) => one.material).length;
  return {
    set_id: SET_ID,
    week: WEEK,
    anchor_asof: "2026-09-18",
    asof: "2026-09-21",
    score,
    material,
    refresh_suggested: material > 0 || score >= 6,
    // The worker's own wording, including the sentence it appends when a
    // fresh phase read has gone stale (`drift._stale_sentence`).
    reason:
      (material
        ? `${items.length} changes since the call, ${material} of them material under the re-price rule. A refresh is warranted.`
        : "1 change since the call, none material. The call stands as written.") +
      (items.some((one) => one.kind === "phase_stale")
        ? " This week's crypto phase read may be out of date."
        : ""),
    items,
    evaluated_at: evaluatedAt,
  };
}

function runRequestsJson(honest: boolean): Record<string, unknown>[] {
  const narrative = {
    id: 1,
    kind: "narrative",
    reason: null,
    origin: "schedule",
    requested_at: "2026-09-21T01:40:00.000Z",
    status: "done",
    claimed_at: "2026-09-21T01:40:05.000Z",
    finished_at: "2026-09-21T01:58:40.000Z",
    decided_at: null,
    decision: null,
    proposed: [],
    summary: "Wrote 15 series and 6 dated events; ISM services came back Tier 4.",
    error: null,
  };
  if (!honest) return [narrative];
  return [
    {
      id: 2,
      kind: "amend",
      reason: "Hormuz enforcement is back on the tape; is S3 still 23%?",
      origin: "operator",
      requested_at: "2026-09-21T12:10:00.000Z",
      status: "proposed",
      claimed_at: "2026-09-21T12:10:30.000Z",
      finished_at: "2026-09-21T12:31:02.000Z",
      decided_at: null,
      decision: null,
      proposed: [
        { table: "scenario_set", id: "set-w39b", week: WEEK, asset: null, name: null },
        { table: "strategy", id: "strategy-eq-w39b", week: WEEK, asset: "equities", name: null },
      ],
      summary:
        "Re-priced S3 from 23% to 18% and S2 from 32% to 37% on the new acute Hormuz risk; equities strategy moves to 25-40% net.",
      error: null,
    },
    narrative,
  ];
}

/**
 * The three live risks on the W39 Monday, under the slugs migration
 * 20260924000300 gave them. In the clean dataset the desk has taken the feed
 * over and each risk carries its check history; in the honest one the worker
 * still reads Blackswan Events.md, so each has only the one starting check the
 * migration seeded and no trail to draw.
 */
function tailRisksJson(honest: boolean): Record<string, unknown>[] {
  const desk = !honest;
  const checked = desk ? TAIL_SCAN : "2026-09-21T00:00:00.000Z";
  const common = {
    feed_source: desk ? "openclaw:macro-desk" : "vault",
    last_checked_at: checked,
    last_check_kind: "weekly",
  };
  return [
    { key: "e4bfe255867d8c777a84b4fe58740d14", risk_id: "iran-oil-chokepoints", name: "Iran-war tanker and oil-chokepoint escalation", severity: "acute", summary: "U.S.-Iran tanker strikes intensified into the conflict's largest shipping attack as crude moved above $100.", priced_in: "Cboe VIX fell to 14.81 on Sep 18; crude term structure carries the risk, equities do not.", assets: ["oil", "rates", "equities"], source_url: "https://www.reuters.com", first_seen: "2026-09-09", last_seen: "2026-09-21", ...common, previous_severity: desk ? "elevated" : null },
    { key: "1d8462d5697169ff5a63594f34ea03d9", risk_id: "us-funding-deadline", name: "U.S. government-funding deadline meets elevated Treasury supply", severity: "elevated", summary: "The funding deadline is inside ten days while the Sep 22-24 auction cluster leaves a shutdown or auction-liquidity path into rates.", priced_in: "Not in VIX; visible only in bill spreads.", assets: ["rates", "USD", "repo"], source_url: "https://www.edwardjones.com/us-en/market-news-insights/capital-currents/august-2026", first_seen: "2026-09-16", last_seen: "2026-09-21", ...common, previous_severity: null },
    { key: "1d4b8d7401c59b13f88c69e2bacc965b", risk_id: "private-credit-insurers", name: "Opaque private-credit leverage and insurer feedback loop", severity: "watch", summary: "A cluster of markdowns, redemptions or insurer losses could transmit into leveraged loans and high yield.", priced_in: "HY at 270 bps prices none of it.", assets: ["credit", "banks"], source_url: "https://www.wsj.com", first_seen: "2026-09-14", last_seen: "2026-09-21", ...common, previous_severity: desk ? "elevated" : null },
  ];
}

/** The Monday 06:00 SGT full tail-risk scan, in UTC. */
const TAIL_SCAN = "2026-09-20T22:04:00.000Z";

/**
 * Every check of the three risks. The Iranian one climbed watch, elevated,
 * acute over three Mondays; private credit went the other way.
 */
function tailRiskHistoryJson(honest: boolean): Record<string, unknown>[] {
  const check = (
    riskId: string,
    name: string,
    checkedAt: string,
    severity: string,
    kind = "weekly",
  ) => ({
    risk_id: riskId,
    name,
    checked_at: checkedAt,
    kind,
    severity,
    summary: null,
    priced_in: null,
    gauges: {},
    source_url: null,
    retired: false,
  });
  const IRAN = ["iran-oil-chokepoints", "Iran-war tanker and oil-chokepoint escalation"] as const;
  const FUNDING = ["us-funding-deadline", "U.S. government-funding deadline meets elevated Treasury supply"] as const;
  const CREDIT = ["private-credit-insurers", "Opaque private-credit leverage and insurer feedback loop"] as const;
  if (honest) {
    // Only the starting point migration 20260924000300 seeded, dated by the
    // note that last listed each risk.
    return [
      check(...IRAN, "2026-09-21T00:00:00.000Z", "acute"),
      check(...FUNDING, "2026-09-21T00:00:00.000Z", "elevated"),
      check(...CREDIT, "2026-09-21T00:00:00.000Z", "watch"),
    ];
  }
  return [
    check(...IRAN, "2026-09-09T00:00:00.000Z", "watch"),
    check(...IRAN, "2026-09-14T00:00:00.000Z", "elevated"),
    check(...IRAN, "2026-09-17T01:20:00.000Z", "elevated", "daily"),
    check(...IRAN, TAIL_SCAN, "acute"),
    check(...FUNDING, "2026-09-16T00:00:00.000Z", "elevated"),
    check(...FUNDING, TAIL_SCAN, "elevated"),
    check(...CREDIT, "2026-09-14T00:00:00.000Z", "elevated"),
    check(...CREDIT, TAIL_SCAN, "watch"),
  ];
}

/* -- the holdings watch ---------------------------------------------------- */

/** When the 09:05 SGT holdings watch finished on the W39 Monday, in UTC. */
const HOLDINGS_RUN = "2026-09-21T01:07:40.000Z";
/** Friday's New York close, the session a Monday-morning watch reports. */
const NY_CLOSE = "2026-09-18T20:00:00.000Z";

function usHolding(
  symbol: string,
  name: string | null,
  price: number,
  change: number,
  extra: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    as_of: MON,
    symbol,
    source: "accounting",
    name,
    asset_class: "equity",
    market_symbol: symbol,
    price: n(price),
    price_at: NY_CLOSE,
    currency: "USD",
    change_24h_pct: n(change),
    change_basis: "close_to_close",
    change_session: FRI,
    price_source: `Yahoo chart API (${symbol})`,
    unavailable_reason: null,
    underlying_only: false,
    quantity: null,
    exposure_usd: null,
    material: false,
    driver_summary: null,
    source_url: null,
    recorded_at: HOLDINGS_RUN,
    ...extra,
  };
}

function cryptoHolding(
  symbol: string,
  name: string | null,
  price: string,
  change: number | null,
  extra: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    as_of: MON,
    symbol,
    source: "accounting",
    name,
    asset_class: "crypto",
    market_symbol: symbol,
    price,
    price_at: HOLDINGS_RUN,
    currency: "USDC",
    change_24h_pct: change === null ? null : n(change),
    change_basis: change === null ? null : "rolling_24h",
    change_session: null,
    price_source: `Hyperliquid spot (${symbol}/USDC)`,
    unavailable_reason: null,
    underlying_only: false,
    quantity: null,
    exposure_usd: null,
    material: false,
    driver_summary: null,
    source_url: null,
    recorded_at: HOLDINGS_RUN,
    ...extra,
  };
}

/**
 * The Monday watch over Accounting's ten open positions (Tiger and
 * Hyperliquid), the one Singapore position on the manual list and the one
 * name watched without being held. NEKO did not trade, so its change is null
 * with the desk's own reason; the META call carries META's move.
 */
const HOLDINGS = [
  usHolding("APP", "AppLovin", 612.4, 6.84, {
    material: true,
    driver_summary: "AppLovin rose after two brokers raised their price targets on stronger ad sales.",
    source_url: "https://www.investing.com/news/stock-market-news/applovin-price-target-raised",
  }),
  usHolding("GOOG", "Alphabet", 334.57, -1.41, {
    material: true,
    driver_summary: "Alphabet fell after a court limited its search deals with phone makers.",
    source_url: "https://www.reuters.com/legal/alphabet-search-deals-ruling",
  }),
  usHolding("META", "Meta Platforms", 750.0, 0.53, {
    driver_summary: "Meta edged up ahead of its Connect event, where it is expected to show new AI glasses.",
    source_url: "https://www.cnbc.com/meta-connect-2026-preview",
  }),
  usHolding("META  260925C00737500", "META 25 Sep 2026 737.5 call", 750.0, 0.53, {
    asset_class: "option",
    market_symbol: "META",
    price_source: "Yahoo chart API (META), underlying",
    underlying_only: true,
  }),
  usHolding("HOOD", "Robinhood Markets", 118.36, -3.12, {
    driver_summary: "Robinhood fell with other crypto-linked stocks as bitcoin slipped.",
    source_url: "https://www.coindesk.com/markets/crypto-stocks-slide",
  }),
  usHolding("DRAM", null, 41.27, 2.95, {
    asset_class: "etf",
    driver_summary: "Memory stocks rose again as DRAM contract prices climbed for a fourth month.",
    source_url: "https://www.trendforce.com/presscenter/news/dram-contract-prices",
  }),
  usHolding("TLT", "iShares 20+ Year Treasury Bond ETF", 84.12, -0.62, {
    asset_class: "etf",
  }),
  cryptoHolding("HYPE", "Hyperliquid", "54.21", 4.37, {
    price_source: "Hyperliquid spot (HYPE/USDC, @107)",
    driver_summary: "HYPE rose as daily trading on Hyperliquid reached its highest this month.",
    source_url: "https://www.theblock.co/data/decentralized-finance/hyperliquid",
  }),
  cryptoHolding("MAX", null, "0.3182", -2.4, {
    price_source: "Hyperliquid spot (MAX/USDC, @142)",
  }),
  cryptoHolding("NEKO", null, "0.000105", null, {
    price_source: "Hyperliquid spot (NEKO/USDC, @188)",
    unavailable_reason:
      "NEKO did not trade on Hyperliquid in the last 24 hours, so its mark has not moved and there is no change to report",
  }),
  usHolding("D05", "DBS Group", 39.86, 0.38, {
    source: "manual",
    market_symbol: "D05.SI",
    price_at: "2026-09-18T09:00:00.000Z",
    currency: "SGD",
    price_source: "Yahoo chart API (D05.SI)",
  }),
  usHolding("MU", "Micron Technology", 162.3, 3.4, {
    source: "watch",
    driver_summary: "Micron rose on reports of higher memory prices before its 30 Sept results.",
    source_url: "https://www.investing.com/news/stock-market-news/ubs-ai-capex-4908051",
  }),
];

function holdingEvent(
  id: string,
  symbol: string,
  kind: string,
  name: string,
  when: {
    starts_at: string;
    ends_at?: string | null;
    date_precision: string;
    date_estimated?: boolean;
    date_text: string;
  },
  source_url: string,
  catalyst_id: string | null = null,
): Record<string, unknown> {
  return {
    id,
    symbol,
    kind,
    name,
    starts_at: when.starts_at,
    ends_at: when.ends_at ?? null,
    date_precision: when.date_precision,
    date_estimated: when.date_estimated ?? false,
    date_text: when.date_text,
    source_url,
    catalyst_id,
    first_seen: MON,
    last_seen: MON,
  };
}

/** What the watch found coming up in the next fourteen days. */
const HOLDING_EVENTS = [
  holdingEvent("he-meta-connect", "META", "product", "Meta Connect 2026 keynote", { starts_at: "2026-09-23T00:00:00.000Z", ends_at: "2026-09-25T00:00:00.000Z", date_precision: "window", date_text: "Sept 23 to 24 PT" }, "https://www.metaconnect.com/", "cat-meta-connect"),
  holdingEvent("he-meta-call", "META  260925C00737500", "other", "The call expires", { starts_at: "2026-09-25T20:00:00.000Z", date_precision: "exact", date_text: "Sept 25 at the 16:00 ET close" }, "https://www.nasdaq.com/market-activity/stocks/meta/option-chain"),
  holdingEvent("he-mu-q4", "MU", "earnings", "Micron Q4 FY2026 earnings", { starts_at: "2026-09-30T20:30:00.000Z", date_precision: "exact", date_text: "Sept 30 after the close" }, "https://investors.micron.com/", "cat-micron"),
  holdingEvent("he-tlt-pce", "TLT", "other", "August core PCE inflation", { starts_at: "2026-09-30T12:30:00.000Z", date_precision: "exact", date_text: "Sept 30 08:30 ET" }, "https://www.bea.gov/news/schedule", "cat-pce"),
  holdingEvent("he-tlt-nfp", "TLT", "other", "September jobs report", { starts_at: "2026-10-02T12:30:00.000Z", date_precision: "exact", date_text: "Oct 2 08:30 ET" }, "https://www.bls.gov/schedule/news_release/empsit.htm", "cat-nfp"),
  holdingEvent("he-goog-hw", "GOOG", "product", "Google hardware and Gemini launch event", { starts_at: "2026-10-01T00:00:00.000Z", ends_at: "2026-10-10T00:00:00.000Z", date_precision: "month", date_estimated: true, date_text: "early October 2026 expected" }, "https://blog.google/"),
];

/** The operator's two lists, as the page's editor reads them. */
const MANUAL_POSITIONS: readonly Record<string, unknown>[] = [
  { id: "7d0f1c52-3a57-4d1e-9b6b-1f0f5a2c9d05", symbol: "D05", name: "DBS Group", venue: "DBS Vickers", asset_class: "equity", quantity: "1000", note: "SGX, in the CDP account", created_at: "2026-09-21T00:12:00.000Z", updated_at: "2026-09-21T00:12:00.000Z" },
];

const WATCH_TICKERS: readonly Record<string, unknown>[] = [
  { id: "a3c9e1f4-8b2d-4f6a-9e0c-5d7b1a2f3c41", symbol: "MU", name: "Micron Technology", asset_class: "equity", note: "Memory bellwether; results 30 Sept", created_at: "2026-09-21T00:14:00.000Z", updated_at: "2026-09-21T00:14:00.000Z" },
];

/* -- where the three feeds came from --------------------------------------- */

/**
 * The feeds' own runs. Clean: the desk has taken all three over. Honest: the
 * trial week, when the worker still reads the calendar and the tail risks from
 * their notes, this morning's Blackswan note would not parse, and the holdings
 * watch is already the desk's.
 */
function feedRunsJson(honest: boolean) {
  const run = (
    id: number,
    feed: string,
    origin: string,
    started: string,
    finished: string,
    rows: number,
    status = "ok",
    error: string | null = null,
  ) => ({
    id,
    source: `${origin === "vault" ? "vault" : "desk"}:${feed}`,
    run_kind: origin === "vault" ? "full" : "desk",
    started_at: started,
    finished_at: finished,
    rows,
    status,
    error,
    feed,
    origin,
  });
  if (!honest) {
    return [
      run(11, "tail_risks", "openclaw:macro-desk", "2026-09-20T22:00:00.000Z", TAIL_SCAN, 3),
      run(12, "catalysts", "openclaw:macro-desk", "2026-09-21T00:40:00.000Z", "2026-09-21T00:51:12.000Z", 6),
      run(13, "holdings", "openclaw:macro-desk", "2026-09-21T01:05:00.000Z", HOLDINGS_RUN, 12),
    ];
  }
  return [
    run(11, "tail_risks", "vault", "2026-09-20T13:00:00.000Z", "2026-09-20T13:00:02.000Z", 3),
    run(12, "catalysts", "vault", FULL_RUN, FULL_RUN, 72),
    run(13, "holdings", "openclaw:macro-desk", "2026-09-21T01:05:00.000Z", HOLDINGS_RUN, 12),
    run(14, "tail_risks", "vault", FULL_RUN, FULL_RUN, 0, "failed", "Blackswan Events.md was unreadable, empty or missing a section, so nothing it feeds was retired"),
  ];
}

/** `macro_mart.feed_freshness`, as it reads over `feedRunsJson`. */
function feedsJson(honest: boolean): Record<string, unknown>[] {
  const runs = feedRunsJson(honest);
  const feeds = [...new Set(runs.map((one) => one.feed))];
  return feeds.map((feed) => {
    const mine = runs
      .filter((one) => one.feed === feed)
      .sort((a, b) => b.started_at.localeCompare(a.started_at));
    const last = mine[0]!;
    const ok = mine.find((one) => one.status === "ok") ?? null;
    return {
      feed,
      origin: last.origin,
      last_run_at: last.started_at,
      last_finished_at: last.finished_at,
      last_status: last.status,
      last_error: last.error,
      last_ok_at: ok?.finished_at ?? null,
      last_ok_origin: ok?.origin ?? null,
      rows: ok?.rows ?? null,
    };
  });
}

const WATCHLIST = [
  { ticker: "MU", observed_at: "2026-09-21T01:15:00.000Z", change_pct: n(3.4, 1), summary: "HBM pricing chatter ahead of the 30 September print.", source_url: "https://www.investing.com/news/stock-market-news/ubs-ai-capex-4908051", session_on: "2026-09-18" },
  { ticker: "NBIS", observed_at: "2026-09-21T01:15:00.000Z", change_pct: n(-6.2, 1), summary: "GPU price rise read as pricing power; the $30bn raise read the other way.", source_url: null, session_on: "2026-09-18" },
  { ticker: "COIN", observed_at: "2026-09-18T01:15:00.000Z", change_pct: n(4.1, 1), summary: "Innovation Exemption names the venue for tokenised equities.", source_url: "https://www.coindesk.com/policy", session_on: "2026-09-17" },
];
