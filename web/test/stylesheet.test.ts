import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * Every class a component names exists in the stylesheet, and every token
 * shared with Accounting still carries Accounting's value.
 *
 * This file exists because of a failure that was caught by eye and should not
 * have been. A block replacement in `app/globals.css` swallowed four hundred
 * and thirty lines covering the phase, scenario-set and setups sections. Type
 * checking passed, lint passed, all two hundred tests passed, and the page was
 * visibly broken: columns ran together, headings lost their size, and a stage
 * strip rendered as nothing at all. A missing rule is invisible to every other
 * gate, because CSS has no compiler and a class that resolves to nothing looks
 * exactly like a class that was never needed.
 *
 * Running the audit by hand caught it. A habit is one distracted evening from
 * lapsing, so it is a test.
 *
 * The token check is the other half. `docs/CONTRACT.md` is explicit that the
 * values are copied exactly, because a page rendering `#64bd8b` instead of
 * `#63bc8a` is a different product wearing the same name, and nothing else in
 * this repository would notice the difference.
 *
 * It compares the tokens the two declare in common rather than the whole
 * `:root` block. Each repository legitimately holds tokens the other has no
 * surface for, and asserting sameness of the block would fail every time
 * either of them grew one.
 */

const WEB = process.cwd();
const CSS = readFileSync(join(WEB, "app", "globals.css"), "utf8");

/** Every `.class-name` the stylesheet defines. */
const defined = new Set(
  [...CSS.matchAll(/\.([a-z][a-z0-9-]*)/g)].map((m) => m[1]!),
);

/** Every `.tsx` under the directories that render. */
function sources(): { path: string; text: string }[] {
  const out: { path: string; text: string }[] = [];
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const path = join(dir, entry.name);
      if (entry.isDirectory()) walk(path);
      else if (entry.name.endsWith(".tsx")) {
        out.push({ path, text: readFileSync(path, "utf8") });
      }
    }
  };
  walk(join(WEB, "components"));
  walk(join(WEB, "app"));
  return out;
}

/**
 * Every class name a file hands to `className`.
 *
 * Both the literal form and the template form, because the template form is
 * where a conditional class hides and where a typo therefore survives longest.
 * An interpolation is dropped rather than guessed at: `focus-${band}` cannot be
 * resolved statically, and those families are asserted by hand below.
 */
function classesIn(text: string): string[] {
  const out: string[] = [];
  const attrs = [
    ...text.matchAll(/className=\{?[`"]([^`"]*)[`"]/g),
    ...text.matchAll(/className=\{`([^`]*)`\}/g),
  ];
  for (const attr of attrs) {
    for (const token of attr[1]!.replace(/\$\{[^}]*\}/g, " ").split(/\s+/)) {
      // A trailing hyphen is the stub an interpolation left behind, and
      // `null` is the other arm of a ternary rather than a class.
      if (token === "null" || token.endsWith("-")) continue;
      if (/^[a-z][a-z0-9-]*$/.test(token)) out.push(token);
    }
  }
  return out;
}

/**
 * Classes that are interpolated into a template and cannot be read statically.
 *
 * Listed by hand so the families are still covered: a template that builds
 * `focus-critical` from a band name is exactly as breakable as a literal, and
 * exactly as silent when it breaks.
 */
const INTERPOLATED: readonly string[] = [
  "cond-met",
  "cond-not-yet",
  "cond-failed",
  "cond-unevaluable",
  "cond-void",
  "focus-critical",
  "focus-hold",
  "focus-watch",
  "focus-avoid",
  "flag-critical",
  "flag-hold",
  "flag-watch",
  "flag-avoid",
  "grade-materialized",
  "grade-partial",
  "grade-not",
  "state-fresh",
  "state-dated",
  "state-stale",
  "state-empty",
  "state-unrated",
  "state-not-connected",
  "state-failed",
  "state-pending",
  "is-holding",
  "is-near",
  "is-outside",
  "is-unevaluable",
  "cal-kind-macro",
  "cal-kind-central-banks",
  "cal-kind-earnings",
  "cal-kind-market-events",
  "cal-kind-tech",
  "cal-kind-policy",
  "cal-kind-investor",
  "cal-kind-crypto",
  "cal-kind-other",
  "hold-src-accounting",
  "hold-src-manual",
  "hold-src-watch",
  "risk-sev-acute",
  "risk-sev-elevated",
  "risk-sev-watch",
];

describe("every class a component names exists in the stylesheet", () => {
  for (const file of sources()) {
    const names = [...new Set(classesIn(file.text))];
    if (names.length === 0) continue;
    it(file.path.slice(WEB.length + 1), () => {
      const missing = names.filter((name) => !defined.has(name));
      // A class that resolves to nothing looks exactly like a class that was
      // never needed, which is why no other gate sees this.
      expect(missing).toEqual([]);
    });
  }

  it("covers the families built by interpolation", () => {
    const missing = INTERPOLATED.filter((name) => !defined.has(name));
    expect(missing).toEqual([]);
  });

  it("parsed the stylesheet at all", () => {
    // A guard on the guard. An empty set would make every assertion above
    // vacuously false and every one below vacuously true.
    expect(defined.size).toBeGreaterThan(150);
    expect(defined.has("panel")).toBe(true);
    expect(defined.has("ledger-cell")).toBe(true);
  });
});

describe("the ported design tokens still carry Accounting's values", () => {
  const ACCOUNTING = join(
    WEB,
    "..",
    "..",
    "Accounting",
    "web",
    "app",
    "globals.css",
  );

  /**
   * Every token declared in both carries the same value.
   *
   * Not "the two `:root` blocks are identical". Accounting has since added a
   * categorical family for an exposure ring, and this app has added condition
   * and band tokens of its own; each repository legitimately declares tokens
   * the other has no surface for. What the contract requires is that the ones
   * this app *ports* are copied exactly, and that is what is checked.
   */
  it("gives every shared token the same value Accounting gives it", () => {
    let theirs: string;
    try {
      theirs = readFileSync(ACCOUNTING, "utf8");
    } catch {
      // The sibling repository is not a dependency and may not be checked out.
      // Skipping is correct: this app must build without it, by decision.
      return;
    }
    const tokens = (css: string) => {
      const root = /:root \{([\s\S]*?)\n\}/.exec(css)?.[1] ?? "";
      const out = new Map<string, string>();
      for (const m of root.matchAll(/(--[a-z0-9-]+):\s*([^;]+);/g)) {
        out.set(m[1]!, m[2]!.trim());
      }
      return out;
    };
    const mine = tokens(CSS);
    const accounting = tokens(theirs);
    expect(mine.size).toBeGreaterThan(20);
    expect(accounting.size).toBeGreaterThan(20);

    const shared: string[] = [];
    for (const [name, value] of mine) {
      if (!accounting.has(name)) continue;
      shared.push(name);
      // A page that renders #64bd8b instead of #63bc8a is a different product
      // wearing the same name.
      expect(value, name).toBe(accounting.get(name));
    }
    // And the port is not hollow: the market, urgency and freshness families
    // must all be among the shared ones.
    for (const name of [
      "--bg",
      "--ink",
      "--up",
      "--down",
      "--flat",
      "--urgency-2",
      "--state-unrated",
      "--accent",
    ]) {
      expect(shared, name).toContain(name);
    }
  });

  /**
   * Colour reaches a rule through `var(--token)`, never as a re-typed literal.
   *
   * This replaced a check that listed nine hex values and asserted each still
   * appeared somewhere in the file. That test could only ever go stale: when
   * Accounting moved `--muted` and `--faint`, porting the new values made it
   * fail for having correctly done so, and it contradicted the parity test
   * directly above it. It was also weaker than its own comment claimed, which
   * said a second literal meant a component reaching past the token, while the
   * assertion was `toBeGreaterThan(0)` and so could not detect one. It passed
   * `#8b98a4` on a coincidence: two tokens share that value, and the count of
   * two was read as presence rather than as the duplication being looked for.
   *
   * The invariant is checked directly instead, and it does not name a colour,
   * so a palette that legitimately moves does not break it.
   */
  it("never re-types a colour that a token already holds", () => {
    // Comments are prose *about* colour, including this file's own cautionary
    // `#64bd8b` and a note about a line being invisible on `#0b0f14`. Scanning
    // them would report the documentation as the violation it warns against.
    const code = CSS.replace(/\/\*[\s\S]*?\*\//g, "");
    const root = /:root \{[\s\S]*?\n\}/.exec(code);
    expect(root, ":root block").not.toBeNull();
    const outside =
      code.slice(0, root!.index) + code.slice(root!.index + root![0].length);
    const literals = [...outside.matchAll(/#[0-9a-fA-F]{3,8}\b/g)].map(
      (m) => m[0],
    );

    // One exception, and it is ported rather than invented: `.skip-link` sets a
    // near-black ink on the accent fill, and Accounting types the same literal
    // in the same rule. It is not a token because nothing else uses it.
    expect(literals).toEqual(["#16130b"]);

    try {
      expect(readFileSync(ACCOUNTING, "utf8")).toContain("#16130b");
    } catch {
      // Sibling repository absent, as above. Not a dependency, by decision.
    }
  });
});

