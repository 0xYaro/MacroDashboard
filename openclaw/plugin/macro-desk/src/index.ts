import { Type } from "typebox";
import { defineToolPlugin } from "openclaw/plugin-sdk/tool-plugin";

import { callDesk, MAX_WITHIN_DAYS, type DeskConfig, type ToolName } from "./desk.js";

const configSchema = Type.Object(
  {
    repoPath: Type.String({ description: "Absolute path of the Macro Dashboard repository." }),
    uvPath: Type.Optional(Type.String({ description: "Absolute path of the uv binary." })),
    mode: Type.Optional(
      Type.Union([Type.Literal("shadow"), Type.Literal("live")], {
        description: "shadow forces --dry-run on every submit (the trial); live writes the feeds.",
      }),
    ),
    readTimeoutMs: Type.Optional(Type.Integer({ minimum: 5000, maximum: 85000 })),
    writeTimeoutMs: Type.Optional(Type.Integer({ minimum: 5000, maximum: 85000 })),
  },
  { additionalProperties: false },
);

const when = Type.Object(
  {
    date: Type.Optional(Type.String({ description: "YYYY-MM-DD" })),
    time: Type.Optional(Type.String({ description: "HH:MM, 24-hour, local to tz" })),
    tz: Type.Optional(Type.String({ description: "IANA zone, required with a time, e.g. America/New_York" })),
    from: Type.Optional(Type.String({ description: "window start: YYYY-MM-DD, or YYYY-MM-DDTHH:MM with tz" })),
    to: Type.Optional(Type.String({ description: "window end, same form as from" })),
    month: Type.Optional(Type.String({ description: "YYYY-MM when only the month is known" })),
    part: Type.Optional(Type.String({ description: "early, mid, late, end, or a range such as mid-to-late" })),
    unknown: Type.Optional(Type.Boolean({ description: "true when no date is known" })),
  },
  {
    description:
      "The structured date. One of: {date,time,tz} exact; {date} a day; {from,to} a window; {from,to,tz} with times; {month,part}; {unknown:true}.",
  },
);

const dryRun = Type.Optional(
  Type.Boolean({ description: "Check and archive only. The trial always runs as a dry run whatever this says." }),
);
const sourceUrl = Type.String({ description: "https link to the page that states it (primary source preferred)." });
const estimated = Type.Boolean({
  description: "false only when the source publishes this exact date; a month or unknown date is always true.",
});

const catalystItem = Type.Object({
  op: Type.Optional(Type.Union([Type.Literal("upsert"), Type.Literal("retire"), Type.Literal("rename")], { description: "default upsert" })),
  name: Type.Optional(Type.String({ description: "The catalyst's name. Identifies it for upsert and retire." })),
  id: Type.Optional(Type.String({ description: "The catalyst id from catalysts_list, for retire or rename." })),
  category: Type.Optional(Type.String({ description: "One of the sixteen desk categories, e.g. us_macro, fed, crypto, earnings." })),
  when: Type.Optional(when),
  date_text: Type.Optional(Type.String({ description: "Optional human date text; `when` is preferred." })),
  estimated: Type.Optional(estimated),
  source_url: Type.Optional(sourceUrl),
  affected: Type.Optional(Type.Array(Type.String(), { description: "Assets or themes it moves, e.g. rates, USD, equities, BTC." })),
  reason: Type.Optional(Type.String({ description: "Why, for retire and rename (three words or more)." })),
  new_name: Type.Optional(Type.String({ description: "For rename: the new name." })),
});

const riskItem = Type.Object({
  op: Type.Optional(Type.Union([Type.Literal("upsert"), Type.Literal("retire")], { description: "default upsert" })),
  risk_id: Type.String({ description: "Stable slug, e.g. iran-oil-chokepoints. Reuse the id tail_risks_list shows." }),
  name: Type.Optional(Type.String({ description: "Required for a new risk." })),
  severity: Type.Optional(Type.Union([Type.Literal("watch"), Type.Literal("elevated"), Type.Literal("acute")])),
  summary: Type.Optional(Type.String({ description: "Plain English: what is happening and how it would reach markets." })),
  priced_in: Type.Optional(Type.String({ description: "Plain English: what markets assume now, citing desk_context levels by name." })),
  gauges: Type.Optional(Type.Array(Type.String(), { description: "Series keys from desk_context levels, e.g. vix, move, hy_oas_bps, brent. No numbers." })),
  source_url: Type.Optional(sourceUrl),
  assets: Type.Optional(Type.Array(Type.String())),
  reason: Type.Optional(Type.String({ description: "For retire: why it no longer matters." })),
});

const holdingEvent = Type.Object({
  kind: Type.Union([Type.Literal("earnings"), Type.Literal("product"), Type.Literal("regulatory"), Type.Literal("unlock"), Type.Literal("other")]),
  name: Type.String({ description: "e.g. Alphabet Q3 2026 earnings" }),
  when: Type.Optional(when),
  date_text: Type.Optional(Type.String()),
  estimated,
  source_url: sourceUrl,
});

const holdingItem = Type.Object({
  symbol: Type.String({ description: "The symbol exactly as positions_read listed it." }),
  source: Type.Optional(Type.Union([Type.Literal("accounting"), Type.Literal("manual"), Type.Literal("watch")])),
  material: Type.Optional(Type.Boolean({ description: "true when something material happened; needs driver_summary." })),
  no_clear_reason: Type.Optional(
    Type.Boolean({
      description: "true for a material move with no sourced explanation: driver_summary says so plainly and there is no source_url.",
    }),
  ),
  driver_summary: Type.Optional(Type.String({ description: "One plain-English sentence: why it moved or what happened. No price numbers." })),
  source_url: Type.Optional(sourceUrl),
  events: Type.Optional(Type.Array(holdingEvent, { description: "Dated events in the next 14 days." })),
});

type Context = { signal?: AbortSignal; api?: { logger?: { warn?: (message: string) => void } } };

function deskTool(name: ToolName) {
  return (params: object, config: DeskConfig, context: Context) =>
    callDesk(name, params, config, {
      signal: context.signal,
      // Full stderr goes to the gateway log; the model gets only a failure category.
      log: (message) => context.api?.logger?.warn?.(message),
    });
}

const closed = { additionalProperties: false };

export default defineToolPlugin({
  id: "macro-desk",
  name: "Macro Desk",
  description: "Typed tools that read and write the Macro Dashboard feeds through the repository's `macro-agent desk` command.",
  configSchema,
  tools: (tool) => [
    tool({
      name: "desk_context",
      label: "Desk context",
      description:
        "The dashboard's board: today's date, US session, market phase, this week's scenarios, key levels (S&P 500, VIX, MOVE, high-yield spread, 10-year, dollar, Brent, gold, BTC, ETH) and feed freshness. Read-only. Use it instead of any journal.",
      optional: true,
      parameters: Type.Object({}, closed),
      execute: deskTool("desk_context"),
    }),
    tool({
      name: "catalysts_list",
      label: "Catalyst calendar",
      description:
        "The catalyst calendar as a compact table: `columns`, then one array per catalyst (id, name, category, starts_at, ends_at, date_precision, date_estimated). By default rows starting within 45 days, plus rows already started or undated; change the window with within_days, find an event by name or former name with search, add full for source_url, former_names and the rest. Read-only.",
      optional: true,
      parameters: Type.Object(
        {
          status: Type.Optional(Type.Union([Type.Literal("tracked"), Type.Literal("retired"), Type.Literal("all")], { description: "default tracked" })),
          within_days: Type.Optional(
            Type.Integer({
              minimum: 0,
              maximum: MAX_WITHIN_DAYS,
              description: "Rows starting within this many days of today (default 45); rows already started or undated are always listed; 0 lists every date.",
            }),
          ),
          search: Type.Optional(
            Type.String({ minLength: 1, maxLength: 80, description: "Only rows whose name or a former name contains this text, ignoring case, e.g. CPI or Alphabet." }),
          ),
          full: Type.Optional(Type.Boolean({ description: "true adds each row's source, former names and other stored fields." })),
        },
        closed,
      ),
      execute: deskTool("catalysts_list"),
    }),
    tool({
      name: "catalysts_submit",
      label: "Submit catalyst changes",
      description:
        "Upsert, retire (with a reason) or rename catalysts. Send changes only; anything not mentioned stays. Returns applied items and refusal sentences; fix each refusal and resend only those items.",
      optional: true,
      parameters: Type.Object({ items: Type.Array(catalystItem), dry_run: dryRun }, closed),
      execute: deskTool("catalysts_submit"),
    }),
    tool({
      name: "tail_risks_list",
      label: "Tail risks",
      description: "The live tail risks with their fixed risk_id, severity, recent checks and whether the daily recheck covers them. Read-only.",
      optional: true,
      parameters: Type.Object({ include_retired: Type.Optional(Type.Boolean()) }, closed),
      execute: deskTool("tail_risks_list"),
    }),
    tool({
      name: "tail_risks_submit",
      label: "Submit tail-risk checks",
      description:
        "A weekly scan or a daily recheck: upsert risks by risk_id or retire them with a reason. Gauges are named by series key; the desk records their values. Returns refusal sentences to fix and resend.",
      optional: true,
      parameters: Type.Object({
        kind: Type.Union([Type.Literal("weekly"), Type.Literal("daily")]),
        items: Type.Array(riskItem),
        dry_run: dryRun,
      }, closed),
      execute: deskTool("tail_risks_submit"),
    }),
    tool({
      name: "positions_read",
      label: "Positions",
      description:
        "The names the holdings watch covers, each with a price and 24h change computed from market data (null with a reason when unavailable), and positions_batch_id for holdings_watch_submit. Takes up to a minute.",
      optional: true,
      parameters: Type.Object({}, closed),
      execute: deskTool("positions_read"),
    }),
    tool({
      name: "holdings_watch_submit",
      label: "Submit holdings watch",
      description:
        "The reason for each holding's move and its events in the next 14 days. Prices are never sent; the desk records the ones positions_read computed. Returns refusal sentences to fix and resend.",
      optional: true,
      parameters: Type.Object({
        positions_batch_id: Type.Integer({ description: "The id positions_read returned." }),
        items: Type.Array(holdingItem),
        dry_run: dryRun,
      }, closed),
      execute: deskTool("holdings_watch_submit"),
    }),
  ],
});
