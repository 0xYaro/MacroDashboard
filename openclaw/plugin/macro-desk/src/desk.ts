// The part of the plugin that does the work, kept free of the OpenClaw SDK so
// the tests and the smoke script drive exactly the code the tools run.
//
// Each tool maps onto one `macro-agent desk ...` command (docs/DESK.md in the
// Macro Dashboard repo). The command runs from the gateway process, outside the
// agent's sandbox, as an argv array with no shell. Model input never reaches
// argv: it is checked here and then handed to the command as JSON on stdin.

import { spawn } from "node:child_process";

export type DeskMode = "shadow" | "live";

export type DeskConfig = {
  /** Absolute path of the Macro Dashboard repository. */
  repoPath: string;
  /** Absolute path of the `uv` binary. */
  uvPath?: string;
  /** `shadow` forces --dry-run on every submit and on the positions archive. */
  mode?: DeskMode;
  /** Timeout for the read commands (context, lists), in milliseconds. */
  readTimeoutMs?: number;
  /** Timeout for positions and the submits, which price names and write, in milliseconds. */
  writeTimeoutMs?: number;
};

export type ToolName =
  | "desk_context"
  | "catalysts_list"
  | "catalysts_submit"
  | "tail_risks_list"
  | "tail_risks_submit"
  | "positions_read"
  | "holdings_watch_submit";

export const TOOL_NAMES: readonly ToolName[] = [
  "desk_context",
  "catalysts_list",
  "catalysts_submit",
  "tail_risks_list",
  "tail_risks_submit",
  "positions_read",
  "holdings_watch_submit",
];

export const DEFAULT_UV = "/Users/rayclaw/.local/bin/uv";
// The Codex app-server gives an OpenClaw tool call 90 seconds before it gives
// up on it, so every command finishes, or is killed, inside that.
export const DEFAULT_READ_TIMEOUT_MS = 60_000;
export const DEFAULT_WRITE_TIMEOUT_MS = 85_000;
const MAX_STDOUT_BYTES = 8 * 1024 * 1024;
const MAX_INPUT_BYTES = 256 * 1024;
// Hidden `--origin` the desk command accepts; the smoke script tags its own
// shadow batches with it so the trial comparison can leave them out.
export const ORIGIN = "openclaw:macro-desk";

export type Invocation = {
  tool: ToolName;
  /** Arguments after `uv run --project agent macro-agent desk`. */
  args: string[];
  /** The JSON handed to the command on stdin, for the submits. */
  stdin?: string;
  writes: boolean;
  dryRun: boolean;
  /** True when shadow mode added --dry-run the model did not ask for. */
  dryRunForced: boolean;
};

export type Refused = { ok: false; refusals: string[]; applied: []; checked_by: "macro-desk plugin"; next: string };

/** The fields each read tool takes; anything else is refused, never silently dropped. */
const READ_KEYS: Partial<Record<ToolName, Set<string>>> = {
  desk_context: new Set(),
  catalysts_list: new Set(["status", "within_days", "search", "full"]),
  tail_risks_list: new Set(["include_retired"]),
  positions_read: new Set(),
};
export const MAX_WITHIN_DAYS = 3650;
const MAX_SEARCH_CHARS = 80;

const NOTHING_SENT =
  "Nothing in this call reached the desk. Fix each problem and resend the whole submission.";

// ---------------------------------------------------------------------------
// Checking what the model sent
// ---------------------------------------------------------------------------

type Problems = string[];

const RISK_ID = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const WHEN_KEYS = new Set(["date", "time", "tz", "from", "to", "month", "part", "unknown"]);
const CATALYST_KEYS: Record<string, Set<string>> = {
  upsert: new Set(["op", "name", "category", "when", "date_text", "estimated", "source_url", "affected"]),
  // A source on a retire or rename is harmless; refusing the whole call for it is not.
  retire: new Set(["op", "name", "id", "reason", "source_url"]),
  rename: new Set(["op", "name", "id", "new_name", "reason", "source_url"]),
};
const RISK_KEYS: Record<string, Set<string>> = {
  upsert: new Set([
    "op", "risk_id", "name", "severity", "summary", "priced_in", "gauges", "source_url", "assets",
  ]),
  retire: new Set(["op", "risk_id", "reason", "source_url", "name"]),
};
// Price fields are allowed through so the desk can say it ignored them: the
// recorded numbers are always its own.
const HOLDING_KEYS = new Set([
  "symbol", "source", "material", "no_clear_reason", "driver_summary", "source_url", "events",
  "price", "change", "change_pct", "change_24h", "change_24h_pct", "pct", "move",
]);
const EVENT_KEYS = new Set(["kind", "name", "when", "date_text", "estimated", "source_url"]);

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function label(index: number, item: unknown, key = "name"): string {
  const name = isObject(item) && typeof item[key] === "string" ? ` ('${String(item[key]).slice(0, 60)}')` : "";
  return `items[${index}]${name}`;
}

function checkKeys(where: string, item: Record<string, unknown>, allowed: Set<string>, problems: Problems): void {
  for (const key of Object.keys(item)) {
    if (!allowed.has(key)) {
      problems.push(`${where} has a field the desk does not take, \`${key}\`; the fields are ${[...allowed].join(", ")}.`);
    }
  }
}

function checkString(where: string, field: string, value: unknown, most: number, problems: Problems, required = false): void {
  if (value === undefined || value === null) {
    if (required) problems.push(`${where} needs \`${field}\`.`);
    return;
  }
  if (typeof value !== "string") {
    problems.push(`${where}: \`${field}\` must be text.`);
    return;
  }
  if (value.length > most) problems.push(`${where}: \`${field}\` is ${value.length} characters; the most is ${most}.`);
}

function checkUrl(where: string, value: unknown, problems: Problems, required: boolean): void {
  if (value === undefined || value === null) {
    if (required) problems.push(`${where} needs \`source_url\`, an https link to the page that says it.`);
    return;
  }
  if (typeof value !== "string" || !/^https:\/\/\S+$/.test(value) || value.length > 2000) {
    problems.push(`${where}: \`source_url\` must be one https link with no spaces.`);
  }
}

function checkStringList(where: string, field: string, value: unknown, most: number, problems: Problems): void {
  if (value === undefined) return;
  if (!Array.isArray(value) || value.some((one) => typeof one !== "string" || one.length > 80)) {
    problems.push(`${where}: \`${field}\` must be a list of short texts.`);
  } else if (value.length > most) {
    problems.push(`${where}: \`${field}\` has ${value.length} entries; the most is ${most}.`);
  }
}

function checkWhen(where: string, value: unknown, problems: Problems): void {
  if (value === undefined || value === null) return;
  if (!isObject(value)) {
    problems.push(`${where}: \`when\` must be an object such as {"date": "2026-10-14", "time": "08:30", "tz": "America/New_York"}.`);
    return;
  }
  for (const [key, one] of Object.entries(value)) {
    if (!WHEN_KEYS.has(key)) {
      problems.push(`${where}: \`when\` has a field it does not take, \`${key}\`; it takes ${[...WHEN_KEYS].join(", ")}.`);
    } else if (key === "unknown" ? typeof one !== "boolean" : typeof one !== "string" || one.length > 40) {
      problems.push(`${where}: \`when.${key}\` must be ${key === "unknown" ? "true or false" : "short text"}.`);
    }
  }
}

function checkEstimated(where: string, item: Record<string, unknown>, problems: Problems): void {
  if (item.estimated !== undefined && typeof item.estimated !== "boolean") {
    problems.push(`${where}: \`estimated\` must be true or false.`);
  }
}

function itemsOf(params: unknown, most: number, problems: Problems, allowEmpty = false): unknown[] {
  if (!isObject(params) || !Array.isArray(params.items)) {
    problems.push("The submission needs an `items` list.");
    return [];
  }
  if (!allowEmpty && params.items.length === 0) problems.push("`items` is empty; send at least one item.");
  if (params.items.length > most) problems.push(`\`items\` has ${params.items.length} entries; the most in one call is ${most}. Split it.`);
  return params.items;
}

export function checkCatalysts(params: unknown): Problems {
  const problems: Problems = [];
  itemsOf(params, 120, problems).forEach((item, index) => {
    const where = label(index, item);
    if (!isObject(item)) {
      problems.push(`${where} is not an object.`);
      return;
    }
    const op = item.op ?? "upsert";
    if (typeof op !== "string" || !(op in CATALYST_KEYS)) {
      problems.push(`${where}: \`op\` must be upsert, retire or rename.`);
      return;
    }
    checkKeys(where, item, CATALYST_KEYS[op], problems);
    checkString(where, "name", item.name, 200, problems, op === "upsert");
    checkString(where, "id", item.id, 64, problems);
    checkString(where, "reason", item.reason, 300, problems, op !== "upsert");
    checkString(where, "new_name", item.new_name, 200, problems, op === "rename");
    checkString(where, "category", item.category, 40, problems);
    checkString(where, "date_text", item.date_text, 120, problems);
    if (op === "upsert") {
      checkUrl(where, item.source_url, problems, true);
      checkWhen(where, item.when, problems);
      checkEstimated(where, item, problems);
      checkStringList(where, "affected", item.affected, 12, problems);
    }
    if (op !== "upsert" && item.name === undefined && item.id === undefined) {
      problems.push(`${where} names no catalyst: give \`name\` or \`id\`.`);
    }
  });
  return problems;
}

export function checkTailRisks(params: unknown): Problems {
  const problems: Problems = [];
  if (!isObject(params) || (params.kind !== "weekly" && params.kind !== "daily")) {
    problems.push("`kind` must be weekly (the Monday scan) or daily (the recheck).");
  }
  itemsOf(params, 60, problems, isObject(params) && params.kind === "daily").forEach((item, index) => {
    const where = label(index, item, "risk_id");
    if (!isObject(item)) {
      problems.push(`${where} is not an object.`);
      return;
    }
    const op = item.op ?? "upsert";
    if (typeof op !== "string" || !(op in RISK_KEYS)) {
      problems.push(`${where}: \`op\` must be upsert or retire.`);
      return;
    }
    checkKeys(where, item, RISK_KEYS[op], problems);
    if (typeof item.risk_id !== "string" || !RISK_ID.test(item.risk_id) || item.risk_id.length < 3 || item.risk_id.length > 48) {
      problems.push(`${where}: \`risk_id\` must be a lower-case slug of 3 to 48 characters, words joined by hyphens, such as iran-oil-chokepoints.`);
    }
    if (op === "retire") {
      checkString(where, "reason", item.reason, 300, problems, true);
      return;
    }
    checkString(where, "name", item.name, 160, problems);
    checkString(where, "summary", item.summary, 700, problems, true);
    checkString(where, "priced_in", item.priced_in, 500, problems);
    if (!["watch", "elevated", "acute"].includes(String(item.severity))) {
      problems.push(`${where}: \`severity\` must be watch, elevated or acute.`);
    }
    checkUrl(where, item.source_url, problems, true);
    checkStringList(where, "gauges", item.gauges, 12, problems);
    checkStringList(where, "assets", item.assets, 12, problems);
  });
  return problems;
}

export function checkHoldings(params: unknown): Problems {
  const problems: Problems = [];
  if (!isObject(params) || !Number.isSafeInteger(params.positions_batch_id) || Number(params.positions_batch_id) <= 0) {
    problems.push("`positions_batch_id` must be the whole number positions_read returned.");
  }
  itemsOf(params, 150, problems, true).forEach((item, index) => {
    const where = label(index, item, "symbol");
    if (!isObject(item)) {
      problems.push(`${where} is not an object.`);
      return;
    }
    checkKeys(where, item, HOLDING_KEYS, problems);
    checkString(where, "symbol", item.symbol, 40, problems, true);
    if (item.source !== undefined && !["accounting", "manual", "watch"].includes(String(item.source))) {
      problems.push(`${where}: \`source\` must be accounting, manual or watch.`);
    }
    if (item.material !== undefined && typeof item.material !== "boolean") {
      problems.push(`${where}: \`material\` must be true or false.`);
    }
    if (item.no_clear_reason !== undefined && typeof item.no_clear_reason !== "boolean") {
      problems.push(`${where}: \`no_clear_reason\` must be true or false.`);
    }
    if (item.no_clear_reason === true) {
      if (item.material === false) {
        problems.push(`${where}: \`no_clear_reason\` is for a material move; set \`material\` to true.`);
      }
      if (item.source_url !== undefined && item.source_url !== null) {
        problems.push(
          `${where}: an item with \`no_clear_reason\` carries no \`source_url\`; leave the link out rather than citing a page that does not explain the move.`,
        );
      }
    }
    checkString(where, "driver_summary", item.driver_summary, 700, problems);
    checkUrl(where, item.source_url, problems, false);
    if (item.events === undefined) return;
    if (!Array.isArray(item.events) || item.events.length > 12) {
      problems.push(`${where}: \`events\` must be a list of at most 12 events.`);
      return;
    }
    item.events.forEach((event, at) => {
      const place = `${where}.events[${at}]`;
      if (!isObject(event)) {
        problems.push(`${place} is not an object.`);
        return;
      }
      checkKeys(place, event, EVENT_KEYS, problems);
      if (!["earnings", "product", "regulatory", "unlock", "other"].includes(String(event.kind))) {
        problems.push(`${place}: \`kind\` must be earnings, product, regulatory, unlock or other.`);
      }
      checkString(place, "name", event.name, 200, problems, true);
      checkString(place, "date_text", event.date_text, 120, problems);
      checkWhen(place, event.when, problems);
      checkEstimated(place, event, problems);
      checkUrl(place, event.source_url, problems, true);
    });
  });
  return problems;
}

// ---------------------------------------------------------------------------
// Building the command
// ---------------------------------------------------------------------------

const SUBMIT_KEYS: Partial<Record<ToolName, string[]>> = {
  catalysts_submit: ["items", "dry_run"],
  tail_risks_submit: ["kind", "items", "dry_run"],
  holdings_watch_submit: ["positions_batch_id", "items", "dry_run"],
};

function checkTopLevel(tool: ToolName, params: Record<string, unknown>, problems: Problems): void {
  const allowed = SUBMIT_KEYS[tool];
  if (!allowed) return;
  for (const key of Object.keys(params)) {
    if (!allowed.includes(key)) problems.push(`The call has a field \`${tool}\` does not take, \`${key}\`; it takes ${allowed.join(", ")}.`);
  }
}

function submission(params: Record<string, unknown>, keys: string[]): string {
  const body: Record<string, unknown> = {};
  for (const key of keys) if (params[key] !== undefined) body[key] = params[key];
  return JSON.stringify(body);
}

/**
 * The command for one tool call, or the refusal when the call's shape is wrong.
 * `mode` decides dry runs: shadow always dry-runs, live dry-runs only on request.
 */
export function buildInvocation(tool: ToolName, rawParams: unknown, mode: DeskMode): Invocation | Refused {
  const params = isObject(rawParams) ? rawParams : {};
  const wantsDryRun = params.dry_run === true;
  const dryRun = mode === "shadow" || wantsDryRun;
  const dryRunForced = mode === "shadow" && !wantsDryRun;
  const refused = (refusals: string[]): Refused => ({ ok: false, refusals, applied: [], checked_by: "macro-desk plugin", next: NOTHING_SENT });

  const readKeys = READ_KEYS[tool];
  if (readKeys) {
    const unknown = Object.keys(params).filter((key) => !readKeys.has(key));
    if (unknown.length) {
      const takes = readKeys.size ? `it takes only ${[...readKeys].join(", ")}` : "it takes no arguments";
      return refused([`\`${tool}\` does not take ${unknown.map((key) => `\`${key}\``).join(", ")}; ${takes}.`]);
    }
  }

  switch (tool) {
    case "desk_context":
      return { tool, args: ["context"], writes: false, dryRun: false, dryRunForced: false };
    case "catalysts_list": {
      const status = params.status ?? "tracked";
      const problems: Problems = [];
      if (status !== "tracked" && status !== "retired" && status !== "all") {
        problems.push("`status` must be tracked, retired or all.");
      }
      const args = ["catalysts", "list", "--status", String(status)];
      if (params.within_days !== undefined) {
        const days = params.within_days;
        if (typeof days !== "number" || !Number.isSafeInteger(days) || days < 0 || days > MAX_WITHIN_DAYS) {
          problems.push(`\`within_days\` must be a whole number from 0 (every date) to ${MAX_WITHIN_DAYS}.`);
        } else {
          args.push("--within-days", String(days));
        }
      }
      if (params.search !== undefined) {
        const text = params.search;
        // One argv element in the --flag=value form, so the text can never be read as a flag.
        if (typeof text !== "string" || !text.trim() || text.length > MAX_SEARCH_CHARS || /[\u0000-\u001f\u007f]/.test(text)) {
          problems.push(`\`search\` must be plain text of 1 to ${MAX_SEARCH_CHARS} characters.`);
        } else {
          args.push(`--search=${text.trim()}`);
        }
      }
      if (params.full !== undefined) {
        if (typeof params.full !== "boolean") problems.push("`full` must be true or false.");
        else if (params.full) args.push("--full");
      }
      if (problems.length) return refused(problems);
      return { tool, args, writes: false, dryRun: false, dryRunForced: false };
    }
    case "tail_risks_list":
      if (params.include_retired !== undefined && typeof params.include_retired !== "boolean") {
        return refused(["`include_retired` must be true or false."]);
      }
      return {
        tool,
        args: params.include_retired === true ? ["tail-risks", "list", "--all"] : ["tail-risks", "list"],
        writes: false,
        dryRun: false,
        dryRunForced: false,
      };
    case "positions_read":
      // In shadow mode the archived prices are a shadow batch too.
      return { tool, args: mode === "shadow" ? ["positions", "--dry-run"] : ["positions"], writes: true, dryRun: mode === "shadow", dryRunForced: mode === "shadow" };
    case "catalysts_submit": {
      const problems = checkCatalysts(params);
      checkTopLevel(tool, params, problems);
      if (problems.length) return refused(problems);
      return submit(tool, ["catalysts", "submit"], submission(params, ["items"]), dryRun, dryRunForced);
    }
    case "tail_risks_submit": {
      const problems = checkTailRisks(params);
      checkTopLevel(tool, params, problems);
      if (problems.length) return refused(problems);
      return submit(tool, ["tail-risks", "submit"], submission(params, ["kind", "items"]), dryRun, dryRunForced);
    }
    case "holdings_watch_submit": {
      const problems = checkHoldings(params);
      checkTopLevel(tool, params, problems);
      if (problems.length) return refused(problems);
      return submit(tool, ["holdings", "submit"], submission(params, ["positions_batch_id", "items"]), dryRun, dryRunForced);
    }
  }
}

function submit(tool: ToolName, args: string[], stdin: string, dryRun: boolean, dryRunForced: boolean): Invocation | Refused {
  if (Buffer.byteLength(stdin, "utf8") > MAX_INPUT_BYTES) {
    return {
      ok: false,
      refusals: [`The submission is larger than ${MAX_INPUT_BYTES / 1024} KB. Split it into smaller calls.`],
      applied: [],
      checked_by: "macro-desk plugin",
      next: NOTHING_SENT,
    };
  }
  return {
    tool,
    args: [...args, "--json-in", "-", ...(dryRun ? ["--dry-run"] : [])],
    stdin,
    writes: true,
    dryRun,
    dryRunForced,
  };
}

// ---------------------------------------------------------------------------
// Running it
// ---------------------------------------------------------------------------

export type RunResult = { code: number | null; stdout: string; stderr: string; timedOut: boolean };

export type Runner = (
  file: string,
  args: string[],
  options: { cwd: string; env: NodeJS.ProcessEnv; input?: string; timeoutMs: number; signal?: AbortSignal },
) => Promise<RunResult>;

/** spawn without a shell, stdin written once, killed at the timeout. */
export const spawnRunner: Runner = (file, args, options) =>
  new Promise((resolve) => {
    const child = spawn(file, args, { cwd: options.cwd, env: options.env, shell: false, stdio: ["pipe", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    let timedOut = false;
    let settled = false;
    const finish = (code: number | null) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      options.signal?.removeEventListener("abort", onAbort);
      resolve({ code, stdout, stderr, timedOut });
    };
    const kill = () => {
      child.kill("SIGTERM");
      setTimeout(() => child.kill("SIGKILL"), 2000).unref();
    };
    const timer = setTimeout(() => {
      timedOut = true;
      kill();
    }, options.timeoutMs);
    const onAbort = () => kill();
    options.signal?.addEventListener("abort", onAbort, { once: true });
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk: string) => {
      if (stdout.length < MAX_STDOUT_BYTES) stdout += chunk;
    });
    child.stderr.on("data", (chunk: string) => {
      stderr = (stderr + chunk).slice(-16_384);
    });
    child.on("error", (error) => {
      stderr += `\n${error.message}`;
      finish(null);
    });
    child.on("close", (code) => finish(code));
    child.stdin.on("error", () => undefined);
    child.stdin.end(options.input ?? "");
  });

/** Only what `uv` and the command need; the gateway's own secrets stay behind. */
export function childEnv(source: NodeJS.ProcessEnv = process.env): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = {
    PATH: "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin",
    HOME: source.HOME ?? "",
    LANG: source.LANG ?? "en_US.UTF-8",
    TZ: source.TZ,
    TMPDIR: source.TMPDIR,
    UV_NO_PROGRESS: "1",
  };
  for (const key of Object.keys(env)) if (env[key] === undefined) delete env[key];
  return env;
}

/** Credentials removed, for the gateway log only; the model never sees stderr. */
export function redactForLog(stderr: string): string {
  return stderr
    .replace(/[a-z][a-z0-9+.-]*:\/\/[^\s'"]*@[^\s'"]*/gi, "<url withheld>")
    .replace(/(password|token|secret|key)=\S+/gi, "$1=<withheld>")
    .slice(-4000);
}

/**
 * The failure categories a reply can carry. They name what went wrong without
 * a path, a host or any stderr text, so nothing about the machine reaches the model.
 */
export const FAILURE_CATEGORIES = [
  "timeout",
  "could_not_start",
  "unreadable_output",
  "desk_failed",
  "shadow_guard",
  "not_configured",
] as const;

const SAFE_CATEGORY = /^[a-z][a-z0-9_]{1,47}$/;

/**
 * A category the desk itself printed, when it is a plain slug; otherwise none.
 * The desk's failure reply is `{"ok": false, "error": {"category", "exit"}, ...}`
 * (docs/DESK.md); a bare string is read too, in case the shape changes.
 */
function deskCategory(reply: unknown): string | undefined {
  if (!isObject(reply)) return undefined;
  const candidates = [isObject(reply.error) ? reply.error.category : undefined, reply.error_category, reply.error];
  for (const value of candidates) {
    if (typeof value === "string" && SAFE_CATEGORY.test(value)) return value;
  }
  return undefined;
}

export type Failure = { ok: false; error: string; exit_code: number | null; next: string };

function failure(category: string, exitCode: number | null): Failure {
  return {
    ok: false,
    error: category,
    exit_code: exitCode,
    next: "The desk itself failed; resending the same call will not fix it. Try once more at most, then report it on the job's failure line.",
  };
}

/**
 * A desk reply is a failure when it carries an error: the desk's own failure
 * object (which also holds one fixed refusal sentence), or an error with no
 * refusals to fix.
 */
function isDeskFailure(reply: Record<string, unknown>): boolean {
  if (reply.ok !== false) return false;
  if (isObject(reply.error) || reply.error_category !== undefined) return true;
  return reply.error !== undefined && reply.error !== null && !Array.isArray(reply.refusals);
}

/** Defence in depth: in shadow mode nothing that writes may run without --dry-run. */
export function shadowViolation(mode: DeskMode, invocation: Pick<Invocation, "writes">, argv: string[]): boolean {
  return mode === "shadow" && invocation.writes && !argv.includes("--dry-run");
}

export type CallOptions = {
  runner?: Runner;
  signal?: AbortSignal;
  origin?: string;
  env?: NodeJS.ProcessEnv;
  /** Where the full stderr of a failed command goes: the gateway log. */
  log?: (message: string) => void;
};

/** Run one tool call end to end: check, build, run, and shape the reply. */
export async function callDesk(tool: ToolName, params: unknown, config: DeskConfig, options: CallOptions = {}): Promise<Record<string, unknown>> {
  const mode: DeskMode = config.mode === "live" ? "live" : "shadow";
  const log = options.log ?? (() => undefined);
  if (!config.repoPath || !config.repoPath.startsWith("/")) {
    log(`macro-desk: ${tool} not run: the plugin config has no absolute repoPath`);
    return failure("not_configured", null);
  }
  const built = buildInvocation(tool, params, mode);
  if ("refusals" in built) return { ...built };

  const uv = config.uvPath ?? DEFAULT_UV;
  const originArgs = options.origin && built.stdin !== undefined ? ["--origin", options.origin] : [];
  const argv = ["run", "--quiet", "--project", "agent", "macro-agent", "desk", ...built.args, ...originArgs];
  if (shadowViolation(mode, built, argv)) {
    log(`macro-desk: ${tool} refused: shadow mode and the command lacks --dry-run`);
    return failure("shadow_guard", null);
  }
  const timeoutMs = built.writes ? config.writeTimeoutMs ?? DEFAULT_WRITE_TIMEOUT_MS : config.readTimeoutMs ?? DEFAULT_READ_TIMEOUT_MS;
  const run = options.runner ?? spawnRunner;
  const result = await run(uv, argv, { cwd: config.repoPath, env: options.env ?? childEnv(), input: built.stdin, timeoutMs, signal: options.signal });

  if (result.timedOut) {
    log(`macro-desk: ${tool} timed out after ${Math.round(timeoutMs / 1000)} s; stderr: ${redactForLog(result.stderr)}`);
    return failure("timeout", null);
  }
  let reply: unknown;
  try {
    reply = JSON.parse(result.stdout.trim());
  } catch {
    reply = undefined;
  }
  if ((result.code === 0 || result.code === 1) && isObject(reply) && !isDeskFailure(reply)) {
    const shaped: Record<string, unknown> = { ...reply, mode };
    if (built.dryRun) {
      shaped.dry_run = true;
      if (built.stdin !== undefined) {
        shaped.mode_note =
          mode === "shadow"
            ? "Trial (shadow) mode: this submit was checked and archived as a dry run. Nothing reached the live tables. Treat accepted items as done."
            : "Dry run on request: nothing reached the live tables.";
      }
    }
    return shaped;
  }
  const category =
    deskCategory(reply) ?? (result.code === null ? "could_not_start" : isObject(reply) ? "desk_failed" : result.code <= 1 ? "unreadable_output" : "desk_failed");
  log(`macro-desk: ${tool} failed (${category}, exit ${result.code ?? "none"}); stderr: ${redactForLog(result.stderr)}`);
  return failure(category, result.code);
}
