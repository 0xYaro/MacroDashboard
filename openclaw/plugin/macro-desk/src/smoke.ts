// Calls every tool once against the real repository through the same
// `callDesk` the plugin's tools use, always in shadow mode. The submits are
// built to be refused (or, for holdings, to change nothing live), and they are
// tagged with their own origin so the trial comparison leaves them out.
//
//   node dist/smoke.js [/path/to/MacroDashboard]

import { resolve } from "node:path";

import { callDesk, ORIGIN, type DeskConfig } from "./desk.js";

const repoPath = resolve(process.argv[2] ?? new URL("../../../..", import.meta.url).pathname);
const config: DeskConfig = { repoPath, mode: "shadow", uvPath: process.env.UV_PATH };
const origin = `${ORIGIN}-smoke`;
let failed = 0;

function check(name: string, ok: boolean, detail: string): void {
  if (!ok) failed += 1;
  console.log(`${ok ? "ok  " : "FAIL"} ${name}: ${detail}`);
}

const context = await callDesk("desk_context", {}, config);
check("desk_context", typeof context.today === "string", `today ${String(context.today)}, week ${String(context.week)}`);

const list = await callDesk("catalysts_list", { status: "tracked" }, config);
const listChars = JSON.stringify(list).length;
check(
  "catalysts_list (compact, 45 days)",
  typeof list.count === "number" && Array.isArray(list.columns) && listChars < 64_000,
  `${String(list.count)} of ${String(list.total)} tracked, ${listChars} characters`,
);

const wide = await callDesk("catalysts_list", { within_days: 60, full: true }, config);
const wideChars = JSON.stringify(wide).length;
check(
  "catalysts_list (60 days, full)",
  Array.isArray(wide.columns) && (wide.columns as string[]).includes("source_url") && wideChars < 64_000,
  `${String(wide.count)} rows, ${wideChars} characters`,
);

const found = await callDesk("catalysts_list", { search: "consumer price", within_days: 0 }, config);
check("catalysts_list (search)", typeof found.count === "number", `${String(found.count)} match "consumer price"`);

const unknown = await callDesk("catalysts_list", { query: "cpi" }, config);
check("catalysts_list (unknown field refused)", unknown.ok === false && Array.isArray(unknown.refusals), String((unknown.refusals as string[] | undefined)?.[0]));

const risks = await callDesk("tail_risks_list", {}, config);
check("tail_risks_list", typeof risks.count === "number", `${String(risks.count)} live`);

const plugin = await callDesk("catalysts_submit", { items: [{ op: "delete", name: "Nothing" }] }, config);
check("catalysts_submit (plugin refusal)", plugin.ok === false && Array.isArray(plugin.refusals), String((plugin.refusals as string[])[0]));

const catalysts = await callDesk(
  "catalysts_submit",
  { items: [{ op: "retire", name: "Macro desk smoke test catalyst", reason: "Smoke test, never a real event." }] },
  config,
  { origin },
);
check(
  "catalysts_submit (desk refusal, dry run)",
  catalysts.dry_run === true && Array.isArray(catalysts.refusals) && (catalysts.refusals as string[]).length === 1,
  String((catalysts.refusals as string[] | undefined)?.[0]),
);

const riskSubmit = await callDesk(
  "tail_risks_submit",
  { kind: "daily", items: [{ op: "retire", risk_id: "macro-desk-smoke-test", reason: "Smoke test, never a real risk." }] },
  config,
  { origin },
);
check(
  "tail_risks_submit (desk refusal, dry run)",
  riskSubmit.dry_run === true && Array.isArray(riskSubmit.refusals) && (riskSubmit.refusals as string[]).length === 1,
  String((riskSubmit.refusals as string[] | undefined)?.[0]),
);

const positions = await callDesk("positions_read", {}, config);
const batch = positions.positions_batch_id;
check("positions_read", typeof batch === "number", `batch ${String(batch)}, ${(positions.positions as unknown[] | undefined)?.length ?? 0} names`);

const holdings = await callDesk("holdings_watch_submit", { positions_batch_id: batch, items: [] }, config, { origin });
check(
  "holdings_watch_submit (dry run)",
  holdings.dry_run === true && Array.isArray(holdings.applied),
  `${(holdings.applied as unknown[] | undefined)?.length ?? 0} names recorded in the shadow batch ${String(holdings.batch_id)}, refusals ${JSON.stringify(holdings.refusals ?? holdings.error)}`,
);

console.log(failed ? `${failed} check(s) failed` : "all tools answered");
process.exit(failed ? 1 : 0);
