/**
 * A hand-written runtime validator, and why there is no schema library here.
 *
 * Every row in this app crosses a trust boundary: PostgREST hands back JSON that
 * the type system has already forgotten about. A validator is therefore not
 * optional. It is hand-written rather than delegated because the interesting
 * decisions are not "is this a string" but "what does an unparseable numeric
 * mean", and a library's default answer to that is to coerce, which is the one
 * answer this book must never give.
 *
 * Two rules the whole file exists to enforce:
 *
 * 1. **Null, never zero.** A numeric column that cannot be parsed yields `null`
 *    with a reason, never `0` and never `NaN`. A zero NAV and an unmeasurable
 *    NAV look identical on screen and mean opposite things.
 * 2. **Row validity and envelope validity are separate.** A query that failed
 *    means the whole reading is failed and no rows are shown. A trustworthy
 *    response with one malformed row shows the valid neighbours and counts the
 *    bad one, because discarding good rows without explanation is its own
 *    dishonesty.
 */

export type Valid<T> = { ok: true; value: T };
export type Invalid = { ok: false; reason: string };
export type Checked<T> = Valid<T> | Invalid;

export function ok<T>(value: T): Valid<T> {
  return { ok: true, value };
}

export function bad(reason: string): Invalid {
  return { ok: false, reason };
}

/** A JSON object, or a named reason this was not one. */
export function asRecord(row: unknown): Checked<Record<string, unknown>> {
  if (typeof row !== "object" || row === null || Array.isArray(row)) {
    return bad(`expected an object, got ${describe(row)}`);
  }
  return ok(row as Record<string, unknown>);
}

export function describe(value: unknown): string {
  if (value === null) return "null";
  if (Array.isArray(value)) return "an array";
  return typeof value;
}

/** A required, non-empty string. */
export function text(
  row: Record<string, unknown>,
  key: string,
): Checked<string> {
  const value = row[key];
  if (typeof value !== "string" || value.length === 0) {
    return bad(`${key} must be a non-empty string, got ${describe(value)}`);
  }
  return ok(value);
}

/** A string, or null. An empty string is null: it states nothing. */
export function textOrNull(
  row: Record<string, unknown>,
  key: string,
): Checked<string | null> {
  const value = row[key];
  if (value === null || value === undefined) return ok(null);
  if (typeof value !== "string") {
    return bad(`${key} must be a string or null, got ${describe(value)}`);
  }
  const trimmed = value.trim();
  return ok(trimmed.length === 0 ? null : trimmed);
}

/** A required member of a closed set, which is how a CHECK constraint reads. */
export function oneOf<const T extends readonly string[]>(
  row: Record<string, unknown>,
  key: string,
  allowed: T,
): Checked<T[number]> {
  const value = row[key];
  if (typeof value !== "string" || !allowed.includes(value)) {
    return bad(`${key} must be one of ${allowed.join(", ")}, got ${describe(value)}`);
  }
  return ok(value as T[number]);
}

/**
 * A `numeric` column, parsed at the edge, or null.
 *
 * PostgREST serialises `numeric` as a string so no precision is lost in transit.
 * A value that will not parse is `null` with a reason, never `NaN`: `NaN`
 * propagates silently through arithmetic and formats as something that looks
 * like a reading.
 */
export function numeric(
  row: Record<string, unknown>,
  key: string,
): Checked<number | null> {
  const value = row[key];
  if (value === null || value === undefined) return ok(null);
  if (typeof value === "number") {
    return Number.isFinite(value)
      ? ok(value)
      : bad(`${key} is a non-finite number`);
  }
  if (typeof value !== "string") {
    return bad(`${key} must be a numeric string or null, got ${describe(value)}`);
  }
  const trimmed = value.trim();
  if (trimmed.length === 0) return ok(null);
  const parsed = Number(trimmed);
  if (!Number.isFinite(parsed)) return bad(`${key} is not a parseable numeric`);
  return ok(parsed);
}

/** A `numeric` column that the schema declares NOT NULL. */
export function requiredNumeric(
  row: Record<string, unknown>,
  key: string,
): Checked<number> {
  const checked = numeric(row, key);
  if (!checked.ok) return checked;
  if (checked.value === null) return bad(`${key} must not be null`);
  return ok(checked.value);
}

/** An `int` column. */
export function integer(
  row: Record<string, unknown>,
  key: string,
): Checked<number> {
  const value = row[key];
  const parsed = typeof value === "string" ? Number(value) : value;
  if (typeof parsed !== "number" || !Number.isInteger(parsed)) {
    return bad(`${key} must be an integer, got ${describe(value)}`);
  }
  return ok(parsed);
}

/** A `bool` column. */
export function boolean(
  row: Record<string, unknown>,
  key: string,
): Checked<boolean> {
  const value = row[key];
  if (typeof value !== "boolean") {
    return bad(`${key} must be a boolean, got ${describe(value)}`);
  }
  return ok(value);
}

/**
 * A `timestamptz`, kept as the ISO string it arrived as and checked parseable.
 *
 * The string is what travels, so it stays the stored form; the parse is what
 * makes the claim that it is a clock at all. An unparseable stamp fails the row
 * rather than becoming an `Invalid Date` that every age label downstream would
 * have to re-discover.
 */
export function instant(
  row: Record<string, unknown>,
  key: string,
): Checked<string> {
  const value = row[key];
  if (typeof value !== "string" || !Number.isFinite(Date.parse(value))) {
    return bad(`${key} must be a parseable timestamptz, got ${describe(value)}`);
  }
  return ok(value);
}

export function instantOrNull(
  row: Record<string, unknown>,
  key: string,
): Checked<string | null> {
  const value = row[key];
  if (value === null || value === undefined) return ok(null);
  return instant(row, key);
}

/** A `date` column, as `YYYY-MM-DD`. Kept as a calendar day, never an instant. */
export function calendarDate(
  row: Record<string, unknown>,
  key: string,
): Checked<string> {
  const value = row[key];
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    return bad(`${key} must be a YYYY-MM-DD date, got ${describe(value)}`);
  }
  return ok(value);
}

export function calendarDateOrNull(
  row: Record<string, unknown>,
  key: string,
): Checked<string | null> {
  const value = row[key];
  if (value === null || value === undefined) return ok(null);
  return calendarDate(row, key);
}

/** A `text[]` column. */
export function textArray(
  row: Record<string, unknown>,
  key: string,
): Checked<readonly string[]> {
  const value = row[key];
  if (value === null || value === undefined) return ok([]);
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string")) {
    return bad(`${key} must be an array of strings, got ${describe(value)}`);
  }
  return ok(value as string[]);
}

/**
 * Fold a set of field checks into one row, or the first reason it is not a row.
 *
 * First reason rather than all of them on purpose: a row that failed one field
 * is excluded whatever the other fields say, and a list of five complaints about
 * one bad row is harder to act on than the first one.
 */
export function fields<T extends object>(checks: {
  [K in keyof T]: Checked<T[K]>;
}): Checked<T> {
  const value = {} as T;
  for (const key of Object.keys(checks) as (keyof T)[]) {
    const checked = checks[key];
    if (!checked.ok) return checked;
    value[key] = checked.value;
  }
  return ok(value);
}

/** A row-level validator: one payload in, one row or one reason out. */
export type RowValidator<T> = (row: unknown) => Checked<T>;

export interface TableReading<T> {
  rows: readonly T[];
  /** Rows the validator excluded, each with the reason it was excluded. */
  rejected: readonly string[];
}

/**
 * Every valid row, and a named reason for each one that was not.
 *
 * The rejection list is not diagnostics: it is what the panel renders so that a
 * book missing a position says which one and why, instead of quietly showing a
 * shorter book.
 */
export function readTable<T>(
  payload: unknown,
  validator: RowValidator<T>,
): Checked<TableReading<T>> {
  if (!Array.isArray(payload)) {
    return bad(`expected an array of rows, got ${describe(payload)}`);
  }
  const rows: T[] = [];
  const rejected: string[] = [];
  payload.forEach((row, index) => {
    const checked = validator(row);
    if (checked.ok) rows.push(checked.value);
    else rejected.push(`row ${index}: ${checked.reason}`);
  });
  return ok({ rows, rejected });
}

/* ------------------------------------------- additions for this system ---- */

/**
 * An `int` column that the schema allows to be null.
 *
 * Separate from `integer` rather than a flag on it, because the two mean
 * different things at a call site: a null `prior_phase` is a first call with no
 * predecessor, and a null `targets_hit` is a count nobody computed.
 */
export function integerOrNull(
  row: Record<string, unknown>,
  key: string,
): Checked<number | null> {
  const value = row[key];
  if (value === null || value === undefined) return ok(null);
  return integer(row, key);
}

export function booleanOrNull(
  row: Record<string, unknown>,
  key: string,
): Checked<boolean | null> {
  const value = row[key];
  if (value === null || value === undefined) return ok(null);
  return boolean(row, key);
}

export function oneOfOrNull<const T extends readonly string[]>(
  row: Record<string, unknown>,
  key: string,
  allowed: T,
): Checked<T[number] | null> {
  const value = row[key];
  if (value === null || value === undefined) return ok(null);
  return oneOf(row, key, allowed);
}

/** An `int[]` column, which is how `rules_waived` arrives. */
export function integerArray(
  row: Record<string, unknown>,
  key: string,
): Checked<readonly number[]> {
  const value = row[key];
  if (value === null || value === undefined) return ok([]);
  if (!Array.isArray(value)) {
    return bad(`${key} must be an array of integers, got ${describe(value)}`);
  }
  const out: number[] = [];
  for (const item of value) {
    const parsed = typeof item === "string" ? Number(item) : item;
    if (typeof parsed !== "number" || !Number.isInteger(parsed)) {
      return bad(`${key} holds ${describe(item)} where an integer was required`);
    }
    out.push(parsed);
  }
  return ok(out);
}

/**
 * A `jsonb` column, kept opaque.
 *
 * The evidence blob on a condition, the components on an observation and the
 * swing variables on a scenario set are all shapes the worker owns. This app
 * renders them, it does not compute over them, so the validator only asserts
 * that the column is JSON at all and the renderer asks for the keys it wants
 * one at a time. Asserting a shape here would make an added component key a
 * rejected row, which is the wrong trade: a new field should widen the
 * evidence line, never delete the condition.
 */
export function jsonObject(
  row: Record<string, unknown>,
  key: string,
): Checked<Readonly<Record<string, unknown>>> {
  const value = row[key];
  if (value === null || value === undefined) return ok({});
  if (typeof value !== "object" || Array.isArray(value)) {
    return bad(`${key} must be a json object or null, got ${describe(value)}`);
  }
  return ok(value as Record<string, unknown>);
}

/**
 * A `jsonb` column whose top level the schema does not pin to an object.
 *
 * `macro_core.scenario_set.swing_variables` is declared
 * `jsonb not null default '[]'::jsonb` and every stored set holds an array of
 * `{name, cool, hot}`. Reading it with `jsonObject` rejected every row in the
 * judgment record and emptied `/history` behind a banner that blamed the data
 * for the reader's assumption.
 *
 * Kept opaque like `jsonObject`, for the same reason: this app renders the
 * blob, it does not compute over it, so the renderer asks for what it wants one
 * key at a time and a worker that adds a field widens a line rather than
 * deleting a week. A scalar is still refused, because nothing that reads one of
 * these treats a bare number or string as a shape.
 */
export function jsonBlob(
  row: Record<string, unknown>,
  key: string,
): Checked<unknown> {
  const value = row[key];
  if (value === null || value === undefined) return ok(null);
  if (typeof value !== "object") {
    return bad(
      `${key} must be a json object, array or null, got ${describe(value)}`,
    );
  }
  return ok(value);
}

/** A `jsonb` array of objects, each validated by the same row validator. */
export function jsonRows<T>(
  row: Record<string, unknown>,
  key: string,
  validator: RowValidator<T>,
): Checked<readonly T[]> {
  const value = row[key];
  if (value === null || value === undefined) return ok([]);
  if (!Array.isArray(value)) {
    return bad(`${key} must be a json array or null, got ${describe(value)}`);
  }
  const out: T[] = [];
  for (let index = 0; index < value.length; index += 1) {
    const checked = validator(value[index]);
    if (!checked.ok) return bad(`${key}[${index}]: ${checked.reason}`);
    out.push(checked.value);
  }
  return ok(out);
}

/**
 * A week label, as `2026-W39`.
 *
 * Checked rather than taken as free text because every ledger row, every rule's
 * teaching week and every judgment table keys on it, and a week that reads
 * `2026-W9` instead of `2026-W09` sorts before `2026-W10` in every list this app
 * renders.
 */
export function weekLabel(
  row: Record<string, unknown>,
  key: string,
): Checked<string> {
  const value = row[key];
  if (typeof value !== "string" || !/^\d{4}-W\d{2}$/.test(value)) {
    return bad(`${key} must be a YYYY-Www week label, got ${describe(value)}`);
  }
  return ok(value);
}

export function weekLabelOrNull(
  row: Record<string, unknown>,
  key: string,
): Checked<string | null> {
  const value = row[key];
  if (value === null || value === undefined) return ok(null);
  return weekLabel(row, key);
}
