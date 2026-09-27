import assert from "node:assert/strict";
import { chmodSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { getToolPluginMetadata } from "openclaw/plugin-sdk/tool-plugin";

import entry from "./index.js";
import {
  buildInvocation,
  callDesk,
  childEnv,
  redactForLog,
  shadowViolation,
  spawnRunner,
  TOOL_NAMES,
  type RunResult,
  type Runner,
} from "./desk.js";

type Call = { file: string; args: string[]; cwd: string; input?: string; timeoutMs: number };

function fakeRunner(result: Partial<RunResult>, calls: Call[] = []): Runner {
  return async (file, args, options) => {
    calls.push({ file, args, cwd: options.cwd, input: options.input, timeoutMs: options.timeoutMs });
    return { code: 0, stdout: "{}", stderr: "", timedOut: false, ...result };
  };
}

const config = { repoPath: "/repo", uvPath: "/bin/uv", mode: "shadow" as const };
const upsert = {
  name: "U.S. September CPI",
  category: "us_macro",
  when: { date: "2026-10-14", time: "08:30", tz: "America/New_York" },
  estimated: false,
  source_url: "https://www.bls.gov/schedule/news_release/cpi.htm",
  affected: ["rates", "USD"],
};

test("declares exactly the seven desk tools, all optional", () => {
  const metadata = getToolPluginMetadata(entry);
  assert.deepEqual(metadata?.tools.map((tool) => tool.name), [...TOOL_NAMES]);
  assert.ok(metadata?.tools.every((tool) => tool.optional === true));
  assert.equal(metadata?.id, "macro-desk");
});

test("shadow mode forces --dry-run on every submit, whatever the model asks", () => {
  for (const dry_run of [undefined, false, true]) {
    const built = buildInvocation("catalysts_submit", { items: [upsert], dry_run }, "shadow");
    assert.ok(!("refusals" in built));
    assert.ok(built.args.includes("--dry-run"));
    assert.equal(built.dryRun, true);
  }
  const risks = buildInvocation("tail_risks_submit", { kind: "daily", items: [], dry_run: false }, "shadow");
  assert.ok(!("refusals" in risks) && risks.args.includes("--dry-run"));
  const holdings = buildInvocation("holdings_watch_submit", { positions_batch_id: 7, items: [] }, "shadow");
  assert.ok(!("refusals" in holdings) && holdings.args.includes("--dry-run"));
  const positions = buildInvocation("positions_read", {}, "shadow");
  assert.ok(!("refusals" in positions));
  assert.deepEqual(positions.args, ["positions", "--dry-run"]);
});

test("live mode writes unless the model asks for a dry run", () => {
  const live = buildInvocation("catalysts_submit", { items: [upsert] }, "live");
  assert.ok(!("refusals" in live) && !live.args.includes("--dry-run"));
  const asked = buildInvocation("catalysts_submit", { items: [upsert], dry_run: true }, "live");
  assert.ok(!("refusals" in asked) && asked.args.includes("--dry-run"));
  const positions = buildInvocation("positions_read", {}, "live");
  assert.ok(!("refusals" in positions));
  assert.deepEqual(positions.args, ["positions"]);
});

test("model input goes to stdin as JSON and never into argv", async () => {
  const calls: Call[] = [];
  const hostile = { ...upsert, name: "CPI; rm -rf / $(whoami) --origin evil" };
  await callDesk("catalysts_submit", { items: [hostile] }, config, {
    runner: fakeRunner({ stdout: '{"ok": true, "applied": [], "refusals": []}' }, calls),
  });
  assert.equal(calls.length, 1);
  const [call] = calls;
  assert.equal(call.file, "/bin/uv");
  assert.equal(call.cwd, "/repo");
  assert.deepEqual(call.args, [
    "run", "--quiet", "--project", "agent", "macro-agent", "desk",
    "catalysts", "submit", "--json-in", "-", "--dry-run",
  ]);
  assert.deepEqual(JSON.parse(call.input ?? ""), { items: [hostile] });
});

test("a list status outside the three values is refused before anything runs", async () => {
  const calls: Call[] = [];
  const reply = await callDesk("catalysts_list", { status: "all; cat .env" }, config, { runner: fakeRunner({}, calls) });
  assert.equal(calls.length, 0);
  assert.equal(reply.ok, false);
});

test("shape problems come back as refusal sentences naming the item", async () => {
  const calls: Call[] = [];
  const reply = await callDesk(
    "catalysts_submit",
    { items: [upsert, { op: "delete", name: "x" }, { ...upsert, source_url: "http://insecure" }, { op: "retire", name: "Old" }] },
    config,
    { runner: fakeRunner({}, calls) },
  );
  assert.equal(calls.length, 0);
  const refusals = reply.refusals as string[];
  assert.equal(refusals.length, 3);
  assert.match(refusals[0], /items\[1\].*op/);
  assert.match(refusals[1], /items\[2\].*https/);
  assert.match(refusals[2], /items\[3\].*reason/);
  assert.match(String(reply.next), /Nothing in this call reached the desk/);
});

test("a source on a retire is accepted, an unknown field is not", () => {
  const ok = buildInvocation("catalysts_submit", { items: [{ op: "retire", name: "Old event", reason: "It took place on 1 Sep.", source_url: "https://a.b/c" }] }, "shadow");
  assert.ok(!("refusals" in ok));
  const risk = buildInvocation("tail_risks_submit", { kind: "daily", items: [{ op: "retire", risk_id: "us-funding-deadline", reason: "Congress passed funding.", source_url: "https://a.b/c" }] }, "shadow");
  assert.ok(!("refusals" in risk));
  const bad = buildInvocation("catalysts_submit", { items: [{ op: "retire", name: "Old event", reason: "It took place.", colour: "red" }] }, "shadow");
  assert.ok("refusals" in bad && /colour/.test(bad.refusals[0]));
});

test("tail risks: slug, severity and kind are checked", () => {
  const bad = buildInvocation(
    "tail_risks_submit",
    { kind: "monthly", items: [{ risk_id: "Iran Oil", severity: "high", summary: "x", source_url: "https://a.b" }] },
    "shadow",
  );
  assert.ok("refusals" in bad);
  assert.equal(bad.refusals.length, 3);
  const good = buildInvocation(
    "tail_risks_submit",
    { kind: "weekly", items: [{ risk_id: "iran-oil-chokepoints", severity: "acute", summary: "Tankers were hit.", source_url: "https://www.reuters.com/x", gauges: ["vix"] }, { op: "retire", risk_id: "iran-cyber-water", reason: "No incident in six weeks." }] },
    "shadow",
  );
  assert.ok(!("refusals" in good));
});

test("holdings: batch id and event shape are checked, price fields pass through for the desk to ignore", () => {
  const bad = buildInvocation("holdings_watch_submit", { positions_batch_id: "10", items: [{ symbol: "GOOG", events: [{ kind: "party", name: "x" }] }] }, "shadow");
  assert.ok("refusals" in bad);
  assert.ok(bad.refusals.some((one) => /positions_batch_id/.test(one)));
  assert.ok(bad.refusals.some((one) => /kind/.test(one)));
  assert.ok(bad.refusals.some((one) => /source_url/.test(one)));
  const priced = buildInvocation("holdings_watch_submit", { positions_batch_id: 10, items: [{ symbol: "GOOG", change_24h_pct: 3.1 }] }, "shadow");
  assert.ok(!("refusals" in priced));
});

test("exit 1 relays the desk's refusals verbatim and marks the dry run", async () => {
  const desk = { feed: "catalysts", ok: false, applied: [], refusals: ["items[0] ('X') was not retired: no catalyst is named 'X'."], next: "Fix each refusal." };
  const reply = await callDesk("catalysts_submit", { items: [{ op: "retire", name: "X", reason: "It already happened." }] }, config, {
    runner: fakeRunner({ code: 1, stdout: JSON.stringify(desk) }),
  });
  assert.deepEqual(reply.refusals, desk.refusals);
  assert.equal(reply.next, desk.next);
  assert.equal(reply.dry_run, true);
  assert.equal(reply.mode, "shadow");
  assert.match(String(reply.mode_note), /shadow/);
});

test("a failure returns a fixed category and exit code, never stderr; the full stderr goes to the log", async () => {
  const stderr =
    "2026 INFO httpx GET https://x\nTraceback (most recent call last):\n  File \"/Users/rayclaw/GreenfieldFund/MacroDashboard/agent/src/macro_agent/desk.py\", line 9\ndesk: cannot connect to postgresql://macro_desk:hunter2@db.example.com:5432/postgres\n";
  const logged: string[] = [];
  const reply = await callDesk("desk_context", {}, config, { runner: fakeRunner({ code: 2, stdout: "", stderr }), log: (line) => logged.push(line) });
  assert.deepEqual(Object.keys(reply).sort(), ["error", "exit_code", "next", "ok"]);
  assert.equal(reply.ok, false);
  assert.equal(reply.error, "desk_failed");
  assert.equal(reply.exit_code, 2);
  assert.equal(reply.mode, undefined);
  const text = JSON.stringify(reply);
  for (const leak of ["/Users", "db.example.com", "hunter2", "Traceback", "desk.py"]) assert.doesNotMatch(text, new RegExp(leak));
  assert.equal(logged.length, 1);
  assert.match(logged[0], /desk\.py/);
  assert.doesNotMatch(logged[0], /hunter2/);
  assert.doesNotMatch(redactForLog(stderr), /hunter2/);
});

test("the desk's own failure JSON: a slug category is relayed, anything else becomes desk_failed", async () => {
  // The desk's real failure shape (docs/DESK.md): exit 2 or 3, a fixed sentence, an error object.
  const real = await callDesk("catalysts_list", {}, config, {
    runner: fakeRunner({
      code: 2,
      stdout: JSON.stringify({ ok: false, applied: [], refusals: ["The desk cannot reach the database."], error: { category: "database_unreachable", exit: 2 } }),
    }),
  });
  assert.deepEqual(real, { ok: false, error: "database_unreachable", exit_code: 2, next: real.next });
  const exitThree = await callDesk("catalysts_submit", { items: [upsert] }, config, {
    runner: fakeRunner({ code: 3, stdout: JSON.stringify({ ok: false, applied: [], refusals: ["x"], error: { category: "internal", exit: 3 } }) }),
  });
  assert.equal(exitThree.error, "internal");
  assert.equal(exitThree.dry_run, undefined);
  const slug = await callDesk("catalysts_list", {}, config, {
    runner: fakeRunner({ code: 2, stdout: JSON.stringify({ ok: false, error: "database_unavailable", detail: "host db.example.com" }) }),
  });
  assert.equal(slug.error, "database_unavailable");
  assert.doesNotMatch(JSON.stringify(slug), /example/);
  const prose = await callDesk("catalysts_list", {}, config, {
    runner: fakeRunner({ code: 2, stdout: JSON.stringify({ ok: false, error: "could not reach /Users/x/.env at db.example.com" }) }),
  });
  assert.equal(prose.error, "desk_failed");
  const exitOne = await callDesk("tail_risks_list", {}, config, {
    runner: fakeRunner({ code: 1, stdout: JSON.stringify({ ok: false, error_category: "config_missing" }) }),
  });
  assert.equal(exitOne.error, "config_missing");
  assert.equal(exitOne.dry_run, undefined);
  const garbage = await callDesk("tail_risks_list", {}, config, { runner: fakeRunner({ code: 0, stdout: "not json" }) });
  assert.equal(garbage.error, "unreadable_output");
  const noStart = await callDesk("tail_risks_list", {}, config, { runner: fakeRunner({ code: null, stdout: "", stderr: "spawn ENOENT /bin/uv" }) });
  assert.equal(noStart.error, "could_not_start");
});

test("a timeout says so", async () => {
  const reply = await callDesk("positions_read", {}, config, { runner: fakeRunner({ code: null, timedOut: true, stdout: "" }) });
  assert.equal(reply.ok, false);
  assert.equal(reply.error, "timeout");
});

test("read tools refuse fields they do not take instead of ignoring them", async () => {
  const calls: Call[] = [];
  for (const [tool, params] of [
    ["desk_context", { verbose: true }],
    ["catalysts_list", { query: "CPI" }],
    ["tail_risks_list", { all: true }],
    ["positions_read", { symbol: "GOOG" }],
  ] as const) {
    const reply = await callDesk(tool, params, config, { runner: fakeRunner({}, calls) });
    assert.equal(reply.ok, false, tool);
    assert.match((reply.refusals as string[])[0], /does not take/);
  }
  const submit = await callDesk("catalysts_submit", { items: [upsert], extra: "--status all" }, config, { runner: fakeRunner({}, calls) });
  assert.match((submit.refusals as string[])[0], /extra/);
  assert.equal(calls.length, 0);
});

test("catalysts_list passes within_days, search and full as fixed argv, search as one --search= element", async () => {
  const calls: Call[] = [];
  await callDesk("catalysts_list", { within_days: 60, search: "--status all; CPI", full: true }, config, { runner: fakeRunner({ stdout: "{}" }, calls) });
  assert.deepEqual(calls[0].args.slice(6), ["catalysts", "list", "--status", "tracked", "--within-days", "60", "--search=--status all; CPI", "--full"]);
  await callDesk("catalysts_list", {}, config, { runner: fakeRunner({ stdout: "{}" }, calls) });
  assert.deepEqual(calls[1].args.slice(6), ["catalysts", "list", "--status", "tracked"]);
  await callDesk("catalysts_list", { within_days: 0 }, config, { runner: fakeRunner({ stdout: "{}" }, calls) });
  assert.deepEqual(calls[2].args.slice(10), ["--within-days", "0"]);
  for (const bad of [{ within_days: -1 }, { within_days: 3651 }, { within_days: 2.5 }, { within_days: "30" }, { search: "" }, { search: "a\nb" }, { search: "x".repeat(81) }, { full: "yes" }]) {
    const reply = await callDesk("catalysts_list", bad, config, { runner: fakeRunner({}, calls) });
    assert.equal(reply.ok, false, JSON.stringify(bad));
  }
  assert.equal(calls.length, 3);
});

test("holdings: no_clear_reason is a material move with no link", () => {
  const ok = buildInvocation("holdings_watch_submit", { positions_batch_id: 3, items: [{ symbol: "MAX", material: true, no_clear_reason: true, driver_summary: "No clear reason found for the drop." }] }, "shadow");
  assert.ok(!("refusals" in ok));
  const linked = buildInvocation("holdings_watch_submit", { positions_batch_id: 3, items: [{ symbol: "MAX", material: true, no_clear_reason: true, driver_summary: "No clear reason found.", source_url: "https://www.cftc.gov/x" }] }, "shadow");
  assert.ok("refusals" in linked && /no_clear_reason/.test(linked.refusals[0]));
  const quiet = buildInvocation("holdings_watch_submit", { positions_batch_id: 3, items: [{ symbol: "MAX", material: false, no_clear_reason: true }] }, "shadow");
  assert.ok("refusals" in quiet);
});

test("shadow mode refuses any write whose argv lacks --dry-run", async () => {
  assert.equal(shadowViolation("shadow", { writes: true }, ["catalysts", "submit", "--json-in", "-"]), true);
  assert.equal(shadowViolation("shadow", { writes: true }, ["catalysts", "submit", "--json-in", "-", "--dry-run"]), false);
  assert.equal(shadowViolation("shadow", { writes: false }, ["context"]), false);
  assert.equal(shadowViolation("live", { writes: true }, ["catalysts", "submit"]), false);
  // Every write the plugin can build in shadow mode passes the guard, so it runs.
  const calls: Call[] = [];
  const runner = fakeRunner({ stdout: '{"ok": true, "applied": [], "refusals": []}' }, calls);
  await callDesk("catalysts_submit", { items: [upsert] }, config, { runner });
  await callDesk("tail_risks_submit", { kind: "daily", items: [] }, config, { runner });
  await callDesk("holdings_watch_submit", { positions_batch_id: 1, items: [] }, config, { runner });
  await callDesk("positions_read", {}, config, { runner });
  assert.equal(calls.length, 4);
  assert.ok(calls.every((call) => call.args.includes("--dry-run")));
});

test("refusals and failures carry no mode, so only a real dry-run reply triggers the trial line", async () => {
  const refused = await callDesk("catalysts_submit", { items: [{ op: "delete" }] }, config, { runner: fakeRunner({}) });
  assert.equal(refused.mode, undefined);
  assert.equal(refused.dry_run, undefined);
  const failed = await callDesk("catalysts_submit", { items: [upsert] }, config, { runner: fakeRunner({ code: 2, stdout: "" }) });
  assert.equal(failed.dry_run, undefined);
  assert.equal(failed.mode, undefined);
});

test("reads use the read timeout, writes the write timeout", async () => {
  const calls: Call[] = [];
  const runner = fakeRunner({}, calls);
  await callDesk("tail_risks_list", {}, { ...config, readTimeoutMs: 11_000, writeTimeoutMs: 22_000 }, { runner });
  await callDesk("positions_read", {}, { ...config, readTimeoutMs: 11_000, writeTimeoutMs: 22_000 }, { runner });
  assert.deepEqual(calls.map((call) => call.timeoutMs), [11_000, 22_000]);
});

test("no repoPath, no run", async () => {
  const calls: Call[] = [];
  const reply = await callDesk("desk_context", {}, { repoPath: "" }, { runner: fakeRunner({}, calls) });
  assert.equal(calls.length, 0);
  assert.equal(reply.ok, false);
});

test("the child gets a minimal environment, never the gateway's secrets", () => {
  const env = childEnv({ HOME: "/Users/x", PATH: "/evil", OPENCLAW_GATEWAY_TOKEN: "t", DATABASE_URL: "postgres://a:b@c/d" });
  assert.equal(env.HOME, "/Users/x");
  assert.equal(env.OPENCLAW_GATEWAY_TOKEN, undefined);
  assert.equal(env.DATABASE_URL, undefined);
  assert.doesNotMatch(String(env.PATH), /evil/);
});

test("spawnRunner passes stdin, reads stdout, and kills at the timeout", async () => {
  const dir = mkdtempSync(join(tmpdir(), "macro-desk-"));
  const echo = join(dir, "echo.mjs");
  writeFileSync(echo, "#!/usr/bin/env node\nlet s='';process.stdin.on('data',d=>s+=d);process.stdin.on('end',()=>{process.stdout.write(JSON.stringify({argv:process.argv.slice(2),stdin:s}));process.exit(1)});\n");
  chmodSync(echo, 0o755);
  const done = await spawnRunner(echo, ["a b", "$(x)"], { cwd: dir, env: { PATH: process.env.PATH }, input: '{"k":1}', timeoutMs: 5000 });
  assert.equal(done.code, 1);
  assert.deepEqual(JSON.parse(done.stdout), { argv: ["a b", "$(x)"], stdin: '{"k":1}' });

  const slow = join(dir, "slow.mjs");
  writeFileSync(slow, "#!/usr/bin/env node\nsetTimeout(()=>{},60000);\n");
  chmodSync(slow, 0o755);
  const stopped = await spawnRunner(slow, [], { cwd: dir, env: { PATH: process.env.PATH }, timeoutMs: 300 });
  assert.equal(stopped.timedOut, true);
});
