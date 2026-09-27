import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import * as contract from "@/lib/contract";
import { fixtureSnapshot } from "@/lib/fixtures";

/**
 * The fixtures and the validators are checked against the **migrations**, not
 * against each other.
 *
 * This file exists because of a bug it would have caught and the rest of the
 * suite could not. `lib/contract.ts` read `sector_focus_row` from
 * `macro_mart.setup_state`; the column is `sector_focus_label`. Every test
 * passed, because `lib/fixtures.ts` emitted `sector_focus_row` too. Fixtures
 * catch a validator that disagrees with production data only when the two are
 * not written by the same hand in the same hour, and they were.
 *
 * So the third party is the SQL. Everything below parses
 * `supabase/migrations/*.sql` at test time and asserts that:
 *
 * 1. every column name the fixtures emit exists on the relation they claim,
 * 2. every column a validator reads exists,
 * 3. no validator requires a column the schema allows to be null.
 *
 * Rule 3 is the one that catches the quietest failure. `macro_mart.calibration`
 * is seeded by migration 0010 with every measured column null, and a validator
 * that required them would reject the seed row and make a correctly migrated
 * project report that its migrations had not run.
 *
 * If the migrations move again, this file fails before a deploy does.
 */

const MIGRATIONS = join(process.cwd(), "..", "supabase", "migrations");

/**
 * A view's select list, one item per column.
 *
 * Split on the commas outside any parentheses rather than on lines: the desk
 * views (20260924000300 onward) list several columns per line, and a
 * `coalesce(a, b) as x` holds a comma that is not a column boundary.
 */
function selectItems(list: string): string[] {
  const body = list
    .split("\n")
    .map((line) => line.replace(/--.*$/, ""))
    .join(" ")
    .trim()
    .replace(/^select\s+(distinct\s+)?/, "");
  const out: string[] = [];
  let depth = 0;
  let current = "";
  for (const ch of body) {
    if (ch === "(") depth += 1;
    if (ch === ")") depth -= 1;
    if (ch === "," && depth === 0) {
      out.push(current.trim());
      current = "";
      continue;
    }
    current += ch;
  }
  out.push(current.trim());
  return out.map((item) => item.replace(/\s+/g, " "));
}

/** Every relation the migrations define, and the columns it carries. */
function readSchema(): {
  columns: Map<string, Set<string>>;
  /**
   * Present only for a column whose nullability is knowable: a base-table
   * column, or a view column selected unrenamed from a single un-joined table.
   * Absent for everything else, which is how a renamed or joined view column
   * opts out of the nullability rule rather than being guessed at.
   */
  nullable: Map<string, boolean>;
} {
  const files = readdirSync(MIGRATIONS)
    .filter((name) => name.endsWith(".sql"))
    .sort();
  const sql = files
    .map((name) => readFileSync(join(MIGRATIONS, name), "utf8"))
    .join("\n");

  const columns = new Map<string, Set<string>>();
  const nullable = new Map<string, boolean>();
  const add = (rel: string, col: string) => {
    const set = columns.get(rel) ?? new Set<string>();
    set.add(col);
    columns.set(rel, set);
  };
  const drop = (rel: string, col: string) => {
    columns.get(rel)?.delete(col);
    nullable.delete(`${rel}.${col}`);
  };
  const dropRelation = (rel: string) => {
    for (const col of columns.get(rel) ?? []) nullable.delete(`${rel}.${col}`);
    columns.delete(rel);
  };

  // Every statement is applied in the order the migrations run it, because a
  // later file may drop what an earlier one added: 20260923000900 drops the
  // blind read from `phase_call` and `phase_score` and rebuilds the view that
  // served it. Read out of order, a dropped column would still look present.
  const steps: { at: number; apply: () => void }[] = [];

  // CREATE TABLE ... ( ... );
  for (const m of sql.matchAll(
    /create table if not exists (macro_\w+\.\w+)\s*\(([\s\S]*?)\n\);/g,
  )) {
    steps.push({
      at: m.index!,
      apply: () => {
        const rel = m[1]!;
        for (const raw of m[2]!.split("\n")) {
          const line = raw.trim();
          if (line === "" || line.startsWith("--")) continue;
          const cm = /^"?([a-z0-9_]+)"?\s+(.*)$/.exec(line);
          if (cm === null) continue;
          const col = cm[1]!;
          const rest = cm[2]!;
          // A table-level constraint is not a column.
          if (
            ["constraint", "check", "unique", "primary", "foreign"].includes(
              col,
            )
          ) {
            continue;
          }
          add(rel, col);
          nullable.set(
            `${rel}.${col}`,
            !/not null/.test(rest) && !/primary key/.test(rest),
          );
        }
      },
    });
  }

  // ALTER TABLE ... ADD COLUMN IF NOT EXISTS ... / DROP COLUMN IF EXISTS ...,
  // one or several at a time.
  //
  // A multi-column ALTER is one statement with the clauses comma-separated, so
  // matching only the first would silently miss the rest. That is how
  // `phase_score.blind_agrees` and `.stage` went unseen on the first run of
  // this parser: the columns were there and the guard said they were not.
  for (const m of sql.matchAll(
    /alter table (macro_\w+\.\w+)\s*([\s\S]*?);/g,
  )) {
    steps.push({
      at: m.index!,
      apply: () => {
        const rel = m[1]!;
        for (const clause of m[2]!.matchAll(
          /add column if not exists ([a-z0-9_]+)\s+([a-z[\] ]+)/g,
        )) {
          add(rel, clause[1]!);
          nullable.set(`${rel}.${clause[1]!}`, !/not null/.test(clause[2]!));
        }
        for (const clause of m[2]!.matchAll(
          /drop column if exists ([a-z0-9_]+)/g,
        )) {
          drop(rel, clause[1]!);
        }
      },
    });
  }

  // DROP VIEW: a rebuilt view carries only what its new select lists.
  for (const m of sql.matchAll(/drop view if exists (macro_\w+\.\w+)/g)) {
    steps.push({ at: m.index!, apply: () => dropRelation(m[1]!) });
  }

  // CREATE VIEW ... AS SELECT <list> FROM <base> [joins]
  //
  // A view over exactly one table with no join inherits that table's
  // nullability column for column, so those are checked as strictly as a table
  // is. `macro_mart.week_grade` is one, and that is how a required read of its
  // nullable `graded_at` is caught. A view with a join is not: a LEFT JOIN
  // makes even a NOT NULL base column nullable, and guessing would be worse
  // than not checking.
  for (const m of sql.matchAll(
    /create (?:or replace )?view (macro_\w+\.\w+)[\s\S]*?\nas\n([\s\S]*?)\n\s*from ([\s\S]*?);\n/g,
  )) {
    steps.push({
      at: m.index!,
      apply: () => {
        const rel = m[1]!;
        const fromClause = m[3]!;
        const simple = !/\bjoin\b/.test(fromClause);
        const base = /^\s*(macro_\w+\.\w+)/.exec(fromClause)?.[1] ?? null;
        for (const raw of selectItems(m[2]!)) {
          const line = raw.trim();
          if (line === "") continue;
          const aliased = /\bas\s+([a-z0-9_]+)$/.exec(line);
          if (aliased !== null) {
            // A renamed column is not traced back to its source, so its
            // nullability is left unknown even on a simple view.
            add(rel, aliased[1]!);
            continue;
          }
          const plain = /^(?:[a-z]+\.)?"?([a-z0-9_]+)"?$/.exec(line);
          if (plain !== null) {
            const col = plain[1]!;
            add(rel, col);
            const inherited =
              simple && base !== null
                ? nullable.get(`${base}.${col}`)
                : undefined;
            if (inherited !== undefined)
              nullable.set(`${rel}.${col}`, inherited);
          }
        }
      },
    });
  }

  steps.sort((x, y) => x.at - y.at);
  for (const step of steps) step.apply();

  return { columns, nullable };
}

const { columns, nullable } = readSchema();

/** Which relation each fixture table stands in for. */
const FIXTURE_RELATIONS: Readonly<Record<string, string>> = {
  snapshotRows: "macro_mart.snapshot_row",
  conditions: "macro_mart.condition_state",
  phaseScores: "macro_mart.phase_score",
  phaseCalls: "macro_mart.phase_call_detail",
  scenarioSet: "macro_mart.scenario_set_state",
  scenarios: "macro_mart.scenario_state",
  targets: "macro_mart.scenario_target_state",
  setups: "macro_mart.setup_state",
  strategies: "macro_mart.strategy_state",
  catalysts: "macro_mart.catalyst_window",
  calibration: "macro_mart.calibration",
  weekGrades: "macro_mart.week_grade",
  setupGrades: "macro_mart.setup_grade",
  rules: "macro_mart.scenario_rule",
  scenarioSetHistory: "macro_mart.scenario_set_history",
  scenarioHistory: "macro_mart.scenario_history",
  phaseHistory: "macro_mart.phase_call_history",
  setupHistory: "macro_mart.setup_history",
  observations: "macro_mart.observation_history",
  findings: "macro_quality.finding",
  gaps: "macro_quality.gap",
  runs: "macro_quality.ingest_run",
  tailRisks: "macro_mart.tail_risk_state",
  tailRiskHistory: "macro_mart.tail_risk_history",
  holdings: "macro_mart.holding_watch_state",
  holdingEvents: "macro_mart.holding_event_state",
  manualPositions: "macro_mart.manual_position_state",
  watchTickers: "macro_mart.watch_ticker_state",
  feeds: "macro_mart.feed_freshness",
};

/** Which relation each exported validator reads. */
const VALIDATOR_RELATIONS: Readonly<Record<string, string>> = {
  snapshotRow: "macro_mart.snapshot_row",
  conditionState: "macro_mart.condition_state",
  phaseScore: "macro_mart.phase_score",
  phaseCallDetail: "macro_mart.phase_call_detail",
  scenarioSetState: "macro_mart.scenario_set_state",
  scenarioState: "macro_mart.scenario_state",
  scenarioTargetState: "macro_mart.scenario_target_state",
  setupState: "macro_mart.setup_state",
  strategyState: "macro_mart.strategy_state",
  calibration: "macro_mart.calibration",
  weekGrade: "macro_mart.week_grade",
  setupGrade: "macro_mart.setup_grade",
  scenarioRule: "macro_mart.scenario_rule",
  scenarioSetHistory: "macro_mart.scenario_set_history",
  scenarioHistory: "macro_mart.scenario_history",
  phaseCallHistory: "macro_mart.phase_call_history",
  setupHistory: "macro_mart.setup_history",
  observationHistory: "macro_mart.observation_history",
  catalystWindow: "macro_mart.catalyst_window",
  ingestRun: "macro_quality.ingest_run",
  finding: "macro_quality.finding",
  gap: "macro_quality.gap",
  tailRisk: "macro_mart.tail_risk_state",
  tailRiskCheck: "macro_mart.tail_risk_history",
  holdingWatch: "macro_mart.holding_watch_state",
  holdingEvent: "macro_mart.holding_event_state",
  manualPosition: "macro_mart.manual_position_state",
  watchTicker: "macro_mart.watch_ticker_state",
  feedFreshness: "macro_mart.feed_freshness",
};

/** The validator helpers that refuse a null. */
const REQUIRING = new Set([
  "text",
  "oneOf",
  "integer",
  "boolean",
  "instant",
  "calendarDate",
  "requiredNumeric",
  "weekLabel",
  "textArray",
  "integerArray",
  "jsonObject",
]);

const CONTRACT_SRC = readFileSync(
  join(process.cwd(), "lib", "contract.ts"),
  "utf8",
);

/** Every `helper(r, "column")` a validator's body performs. */
function readsOf(validator: string): { helper: string; column: string }[] {
  const body = new RegExp(
    `export const ${validator}: RowValidator<[^>]+> = \\(row\\) => \\{([\\s\\S]*?)\\n\\};`,
  ).exec(CONTRACT_SRC);
  if (body === null) throw new Error(`validator ${validator} not found`);
  return [...body[1]!.matchAll(/(\w+)\(r, "([a-z0-9_]+)"/g)].map((m) => ({
    helper: m[1]!,
    column: m[2]!,
  }));
}

describe("the migrations were parsed at all", () => {
  it("found every relation the app reads", () => {
    // A guard on the guard. A regex that silently matched nothing would make
    // every assertion below vacuously true, which is worse than no test.
    for (const rel of new Set(Object.values(VALIDATOR_RELATIONS))) {
      expect(columns.get(rel), rel).toBeDefined();
      expect(columns.get(rel)!.size, rel).toBeGreaterThan(3);
    }
  });
});

/**
 * Columns a validator reads that the mart does not project.
 *
 * Each is read through a tolerant helper, so it yields null and the renderer
 * degrades in a stated way. They are listed here rather than merely allowed, so
 * that the absence stays a decision somebody made: adding a read of a column
 * that does not exist should fail this file, and removing one from this list
 * when the migration lands should pass it.
 */
const NOT_PROJECTED: Readonly<Record<string, readonly string[]>> = {};

describe("every column a validator reads exists in the migrations", () => {
  for (const [validator, rel] of Object.entries(VALIDATOR_RELATIONS)) {
    it(`${validator} reads only columns ${rel} has`, () => {
      const have = columns.get(rel)!;
      const allowed = new Set(NOT_PROJECTED[rel] ?? []);
      const missing = readsOf(validator)
        .filter((read) => !have.has(read.column))
        .filter((read) => {
          // A tolerant read of a listed column is the stated degradation. A
          // required read of any missing column is a break, listed or not.
          if (REQUIRING.has(read.helper)) return true;
          return !allowed.has(read.column);
        })
        .map((read) => read.column);
      expect(missing, `${rel} does not carry`).toEqual([]);
    });
  }
});

/**
 * Base tables only.
 *
 * A view's column nullability is not declarable in SQL and cannot be derived
 * from the select list: a LEFT JOIN makes even a NOT NULL base column nullable,
 * and `macro_mart` has several. Guessing in either direction would be worse
 * than not checking, so this rule covers the tables, which is where the failure
 * it exists to catch lives: `macro_mart.calibration` is a table, seeded with
 * every measured column null.
 *
 * The views are still covered by the two rules above, which is what caught the
 * `phase_call_detail.status` and `scenario_set_history.set_id` reads.
 */
describe("no validator requires a column a base table allows to be null", () => {
  for (const [validator, rel] of Object.entries(VALIDATOR_RELATIONS)) {
    it(`${validator} tolerates every null ${rel} permits`, () => {
      const offenders = readsOf(validator)
        .filter((read) => REQUIRING.has(read.helper))
        // `undefined` is a column whose nullability could not be resolved,
        // which is every renamed or joined view column. Only a column known to
        // be nullable is an offender.
        .filter((read) => nullable.get(`${rel}.${read.column}`) === true)
        .map((read) => `${read.column} read with ${read.helper}()`);
      // A required read of a nullable column rejects a row the database is
      // entitled to serve, and the panel then reports the wrong cause.
      expect(offenders, `${rel} permits null in`).toEqual([]);
    });
  }
});

describe("every key the fixtures emit exists in the migrations", () => {
  for (const dataset of ["clean", "honest"] as const) {
    for (const [table, rel] of Object.entries(FIXTURE_RELATIONS)) {
      it(`${dataset}.${table} is wire-shaped for ${rel}`, () => {
        const have = columns.get(rel)!;
        const rows = fixtureSnapshot(dataset)[
          table as keyof ReturnType<typeof fixtureSnapshot>
        ];
        const emitted = new Set<string>();
        for (const row of rows) {
          if (typeof row !== "object" || row === null) continue;
          for (const key of Object.keys(row)) emitted.add(key);
        }
        const unknown = [...emitted].filter((key) => !have.has(key));
        expect(unknown, `${rel} does not carry`).toEqual([]);
      });
    }
  }
});

describe("the fixtures exercise the columns the app depends on", () => {
  it("emits every column each validator requires", () => {
    // The other direction. A fixture that omitted a required column would make
    // the app look tolerant of a database that is not.
    const missing: string[] = [];
    for (const [table, rel] of Object.entries(FIXTURE_RELATIONS)) {
      const validator = Object.entries(VALIDATOR_RELATIONS).find(
        ([, r]) => r === rel,
      )?.[0];
      if (validator === undefined) continue;
      const rows = fixtureSnapshot("clean")[
        table as keyof ReturnType<typeof fixtureSnapshot>
      ];
      if (rows.length === 0) continue;
      const emitted = new Set(Object.keys(rows[0] as object));
      for (const read of readsOf(validator)) {
        if (REQUIRING.has(read.helper) && !emitted.has(read.column)) {
          missing.push(`${rel}.${read.column}`);
        }
      }
    }
    expect(missing).toEqual([]);
  });
});

describe("the phase is read fresh every week", () => {
  it("drops the blind read and the upgrade from the score, and carries failed and read_stale", () => {
    const have = columns.get("macro_mart.phase_score")!;
    for (const col of ["blind_phase", "blind_agrees", "upgrade_eligible"]) {
      expect(have, col).not.toContain(col);
    }
    for (const col of ["stage", "failed", "read_stale"]) {
      expect(have, col).toContain(col);
    }
  });

  it("serves when the read was taken and how it stands to last week", () => {
    const have = columns.get("macro_mart.phase_call_detail")!;
    for (const col of [
      "classification",
      "classified_at",
      "change_note",
      "prior_week",
      "since_week",
      "weeks_running",
    ]) {
      expect(have, col).toContain(col);
    }
    expect(have).not.toContain("blind_phase");
    expect(have).not.toContain("blind_reason");
  });

  it("reads classified_at tolerantly, because a carried call has none", () => {
    const read = readsOf("phaseCallDetail").find(
      (r) => r.column === "classified_at",
    );
    expect(read?.helper).toBe("instantOrNull");
  });

  it("carries failed and read_stale on every fixture phase score", () => {
    for (const dataset of ["clean", "honest"] as const) {
      for (const row of fixtureSnapshot(dataset).phaseScores as Record<
        string,
        unknown
      >[]) {
        expect(Object.keys(row)).toContain("failed");
        expect(Object.keys(row)).toContain("read_stale");
        expect(Object.keys(row)).not.toContain("blind_agrees");
      }
    }
  });
});

describe("the read travels with its own observation", () => {
  it("projects note, which is what makes an interpretation over time possible", () => {
    // Added in migration 0016. Without it the disclosure could show only the
    // current read beside a column of bare historical values.
    expect(columns.get("macro_mart.observation_history")).toContain("note");
  });

  it("is read tolerantly, because it is null on every migrated observation", () => {
    const reads = readsOf("observationHistory").find(
      (read) => read.column === "note",
    );
    expect(reads?.helper).toBe("textOrNull");
  });
});

describe("the fixtures satisfy the constraints the schema asserts", () => {
  it("gives every scenario set three probabilities summing to 100", () => {
    // A trigger on macro_core.scenario asserts it. A fixture that showed 106
    // would be teaching the reader that the constraint does not hold, and the
    // Brier score read against such a set is not comparable with the ledger.
    for (const dataset of ["clean", "honest"] as const) {
      const snap = fixtureSnapshot(dataset);
      const bySet = new Map<string, number>();
      for (const row of snap.scenarioHistory as Record<string, unknown>[]) {
        const key = row["set_id"] as string;
        bySet.set(key, (bySet.get(key) ?? 0) + (row["probability"] as number));
      }
      for (const [set, total] of bySet) {
        expect(total, `${dataset} ${set}`).toBe(100);
      }
      const live = (snap.scenarios as Record<string, unknown>[]).reduce(
        (sum, row) => sum + (row["probability"] as number),
        0,
      );
      expect(live, `${dataset} scenario_state`).toBe(100);
    }
  });

  it("gives every set exactly three scenarios", () => {
    for (const dataset of ["clean", "honest"] as const) {
      const counts = new Map<string, number>();
      for (const row of fixtureSnapshot(dataset).scenarioHistory as Record<
        string,
        unknown
      >[]) {
        const key = row["set_id"] as string;
        counts.set(key, (counts.get(key) ?? 0) + 1);
      }
      for (const [set, n] of counts) expect(n, `${dataset} ${set}`).toBe(3);
    }
  });

  it("gives every phase call exactly four tracker conditions", () => {
    for (const dataset of ["clean", "honest"] as const) {
      const counts = new Map<string, number>();
      for (const row of fixtureSnapshot(dataset).conditions as Record<
        string,
        unknown
      >[]) {
        if (row["owner_kind"] !== "phase") continue;
        const key = row["owner_id"] as string;
        counts.set(key, (counts.get(key) ?? 0) + 1);
      }
      for (const [call, n] of counts) expect(n, `${dataset} ${call}`).toBe(4);
    }
  });
});

describe("the nested jsonb shapes match what the views build", () => {
  it("builds holdings_check with sector_focus_id and no denormalised band", () => {
    const strategies = fixtureSnapshot("clean").strategies as Record<
      string,
      unknown
    >[];
    for (const strategy of strategies) {
      for (const holding of strategy["holdings_check"] as Record<
        string,
        unknown
      >[]) {
        expect(Object.keys(holding).sort()).toEqual([
          "action",
          "id",
          "sector_focus_id",
          "text",
          "ticker",
        ]);
        // The band is resolved through the sibling array, never copied in.
        // Two copies give a reader two places to disagree about one fact.
        expect(holding).not.toHaveProperty("band");
        expect(holding).not.toHaveProperty("sector_focus_row");
      }
      for (const focus of strategy["sector_focus"] as Record<
        string,
        unknown
      >[]) {
        expect(Object.keys(focus).sort()).toEqual(["band", "id", "text"]);
      }
    }
  });

  it("resolves every holding's band through the sector-focus array", () => {
    const reading = fixtureSnapshot("clean").strategies.map((row) =>
      contract.strategyState(row),
    );
    for (const checked of reading) {
      expect(checked.ok).toBe(true);
      if (!checked.ok) continue;
      for (const holding of checked.value.holdingsCheck) {
        const band = contract.resolveFocusBand(
          holding,
          checked.value.sectorFocus,
        );
        // Either it resolves, or the holding names no row at all, which is the
        // finding. It is never an id that points nowhere.
        expect(
          band !== null || holding.sectorFocusId === null,
          holding.ticker,
        ).toBe(true);
      }
    }
  });

  it("builds other_grades as an array carrying each stated probability", () => {
    const grades = fixtureSnapshot("clean").weekGrades as Record<
      string,
      unknown
    >[];
    for (const row of grades) {
      const other = row["other_grades"];
      // An array, not an object keyed by scenario id: the probability each one
      // was priced at is half of what the Brier score is computed from.
      expect(Array.isArray(other), row["week"] as string).toBe(true);
      for (const entry of other as Record<string, unknown>[]) {
        expect(Object.keys(entry).sort()).toEqual([
          "grade",
          "id",
          "note",
          "prob",
        ]);
      }
    }
  });
});
