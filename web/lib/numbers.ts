/**
 * Number discipline, in one place.
 *
 * Fixed decimals and tabular figures so a column cannot jitter, and a marker
 * for absent values so a missing reading never reads as zero. Every sign is an
 * ASCII hyphen-minus rather than the typographic U+2212: the minus is only
 * guaranteed to be digit-width inside `font-variant-numeric: tabular-nums`, and
 * a column that changes width when a figure turns negative is exactly the
 * jitter these helpers exist to prevent.
 */

/** What a reading with no measurable value prints. Never `0`, never blank. */
export const ABSENT = "—";

/**
 * Parsing at the edge: a Postgres `numeric` arrives as a string so no precision
 * is lost in transit, and anything that will not parse is `null` rather than
 * `NaN` dressed as a number.
 */
export function num(value: unknown): number | null {
  if (value === null || value === undefined) return null;
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  if (trimmed.length === 0) return null;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : null;
}

const GROUPED_0 = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 0,
  maximumFractionDigits: 0,
});

const GROUPED_2 = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

/**
 * A level, at the precision its own magnitude deserves.
 *
 * One rule rather than a per-series table: at or above a thousand the decimals
 * are noise beside the grouping, and below it they are the whole reading. It
 * gives 7,650.50 for the S&P cash, 81,492 for BTC, 14.81 for the VIX and 5.01
 * for a yield, which is what each of those is quoted in.
 *
 * The 1,000 threshold keeps two decimals rather than dropping them, because an
 * index level is quoted to the cent and 7,650 beside 7,650.50 in the same
 * column would be two different precisions in one table.
 */
export function formatLevel(
  value: number | null,
  /**
   * The magnitude to pick the precision from, when it is not this value's own.
   *
   * A band's two edges and the value sitting on it are one quantity shown three
   * times, so they take one precision: the one the largest of them deserves.
   */
  scale?: number,
): string {
  if (value === null) return ABSENT;
  const magnitude = Math.abs(scale ?? value);
  if (magnitude >= 10_000) return GROUPED_0.format(value);
  return GROUPED_2.format(value);
}

/**
 * A stated delta, with its sign always shown.
 *
 * The sign is the point of the column, so a positive figure carries a `+`. A
 * change of exactly zero prints `0.00` with no sign: `+0.00` claims a direction
 * that a flat week does not have.
 *
 * Precision follows magnitude, exactly as a level's does. A delta column holds
 * a −7.06 percentage-point move on the CFTC row and a +468,900,000 dollar move
 * on the ETF flow row, because a series' delta is in the series' own canonical
 * unit and those units are four orders of magnitude apart. Printing the second
 * as `+468900000.00` spends nine characters on decimals nobody reads and blows
 * the column the whole table is pinned to.
 */
export function formatDelta(value: number | null, decimals = 2): string {
  if (value === null) return ABSENT;
  if (value === 0) return (0).toFixed(decimals);
  const sign = value > 0 ? "+" : "";
  if (Math.abs(value) >= 10_000) return `${sign}${GROUPED_0.format(value)}`;
  return `${sign}${value.toFixed(decimals)}`;
}

/* ------------------------------------------------- a delta in its own unit */

/**
 * The magnitude suffixes a money figure is written in, largest first.
 *
 * Nothing below a million gets one: `$470,000` is read at a glance and
 * `$0.47M` is not.
 */
const MAGNITUDES: readonly { at: number; suffix: string }[] = [
  { at: 1e12, suffix: "T" },
  { at: 1e9, suffix: "B" },
  { at: 1e6, suffix: "M" },
];

/**
 * A dollar figure at the magnitude the reader is already holding.
 *
 * `scale` is the quantity this figure is a change in, and the suffix is picked
 * from it rather than from the figure itself, so a delta reads in the same
 * unit as the value beside it: a move in net liquidity is quoted against a
 * balance sheet in trillions, a move in stablecoin supply against a cap in
 * billions. One exception, and it is the reason the rule is not simply "use
 * the value's magnitude": a change small enough to print as `$0.03T` has been
 * rounded past the point of saying anything, so the suffix steps down until
 * the figure carries at least one tenth of its own unit.
 *
 * Precision falls as magnitude rises, which is three significant figures by
 * another name: `$0.93B`, `$27.9B`, `$469M`. Spending two decimals on a
 * four-hundred-million-dollar flow prints digits nobody reads.
 */
export function formatMoney(value: number | null, scale?: number | null): string {
  if (value === null) return ABSENT;
  const sign = value > 0 ? "+" : value < 0 ? "-" : "";
  const size = Math.abs(value);
  const reference = Math.abs(scale ?? value);

  let at = MAGNITUDES.findIndex((magnitude) => reference >= magnitude.at);
  // Step down while the figure would round away to nothing in this unit.
  while (at !== -1 && at < MAGNITUDES.length && size < MAGNITUDES[at]!.at * 0.1) {
    at += 1;
  }
  const chosen = at === -1 ? undefined : MAGNITUDES[at];

  if (chosen === undefined) {
    // Below a million, or a change too small for any suffix to describe.
    //
    // The precision is the one the *value's* magnitude deserves, so a
    // $3,963.88 move in an $85,456 price prints as `+$3,964` rather than to
    // the cent. That only holds while the value is itself in this range: a
    // two-dollar move in a five-trillion-dollar balance is not read at the
    // balance's precision, or it would print as `$2`, so a figure that has
    // stepped past every suffix takes its own.
    const smallest = MAGNITUDES[MAGNITUDES.length - 1]!.at;
    return `${sign}$${formatLevel(size, reference >= smallest ? size : reference || size)}`;
  }
  const scaled = size / chosen.at;
  const decimals = scaled < 1 ? 2 : scaled < 100 ? 1 : 0;
  return `${sign}$${scaled.toFixed(decimals)}${chosen.suffix}`;
}

/**
 * A yield or spread move, in the unit every rates desk states one in.
 *
 * A ten-year that moved from 4.97% to 5.01% moved four basis points, and
 * `+0.04` in a column of dollars and index points is a figure the reader has
 * to stop and identify. Whole basis points above one, one decimal below, so a
 * half-point move is still visible.
 */
export function formatBasisPoints(
  value: number | null,
  /** `percent` arrives as percentage points and is multiplied; `bps` is already there. */
  unit: "percent" | "bps",
): string {
  if (value === null) return ABSENT;
  const bp = unit === "percent" ? value * 100 : value;
  const sign = bp > 0 ? "+" : bp < 0 ? "-" : "";
  const size = Math.abs(bp);
  // A flat day is `0 bp`, unsigned and undecimated: `+0.0 bp` claims both a
  // direction and a precision that nothing moved.
  if (size === 0) return "0 bp";
  return `${sign}${size >= 1 ? Math.round(size) : size.toFixed(1)} bp`;
}

/**
 * Whether the worker has already named the unit its delta is in.
 *
 * `macro.series.delta_native_label` is a bare cadence: `1d`, `WoW`, `MoM`, or
 * null on an event series, and the delta beside it is a raw difference in the
 * series' own unit. A label that names a unit instead (`1d %`, `WoW pp`) is a
 * different claim: the number is already a percentage or a point count and
 * re-reading it as dollars would be a fabrication. Trusting the label rather
 * than assuming the worker's shape is what keeps this honest when the two
 * disagree.
 */
export function labelNamesUnit(label: string | null | undefined): boolean {
  if (label === null || label === undefined) return false;
  return /(^|\s)(%|pp|pts?|bps?)$/i.test(label.trim());
}

/**
 * A snapshot delta, in the same unit and at the same magnitude as its value.
 *
 * `-27855000000` beside `$5.864T` is arithmetic the reader has to do; the
 * column exists to spare them it. The value's unit is the authority, because
 * the worker computes the delta as a plain difference of two observations of
 * the same series, so the two are the same quantity by construction.
 */
export function formatSeriesDelta(
  value: number | null,
  unit: string | null,
  /** The cadence label the worker wrote, which may already name a unit. */
  label: string | null,
  /** The series' own value, which sets the magnitude the change is read at. */
  scale: number | null,
): string {
  if (value === null) return ABSENT;
  if (labelNamesUnit(label)) return formatDelta(value);
  switch (unit) {
    case "usd":
    case "usd_per_oz":
    case "usd_per_bbl":
      return formatMoney(value, scale);
    case "percent":
      // A share of something (breadth, an allocation, a probability) moves in
      // percentage points. `+99 bp` on breadth is a yield's vocabulary on a
      // figure that is not a yield.
      return formatPoints(value);
    case "yield_percent":
      return formatBasisPoints(value, "percent");
    case "bps":
      return formatBasisPoints(value, "bps");
    case "sessions":
      // A signed count of sessions. `+6.00` claims a precision a count of
      // whole trading days does not have.
      return `${value > 0 ? "+" : ""}${GROUPED_0.format(value)}`;
    default:
      // Index points, a 0-100 index, a percent of open interest, a funding
      // rate: each is already quoted in the unit its column is read in.
      return formatDelta(value);
  }
}

/** A change in percentage points, for a percent that is a share rather than a yield. */
export function formatPoints(value: number | null): string {
  if (value === null) return ABSENT;
  if (value === 0) return "0 pp";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(2)} pp`;
}

/** A percentage at a stated precision, signed when the direction matters. */
export function formatPercent(
  value: number | null,
  decimals = 2,
  signed = false,
): string {
  if (value === null) return ABSENT;
  const sign = signed && value > 0 ? "+" : "";
  return `${sign}${value.toFixed(decimals)}%`;
}

/** A probability, which is always a whole percent on a scenario. */
export function formatProbability(value: number | null): string {
  if (value === null) return ABSENT;
  return `${value}%`;
}

/** A dimensionless ratio: a Brier score, a hit score. Always three decimals. */
export function formatScore(value: number | null, decimals = 3): string {
  if (value === null) return ABSENT;
  return value.toFixed(decimals);
}

/** A realised R multiple, signed, to one decimal, as the Setup Ledger writes it. */
export function formatR(value: number | null): string {
  if (value === null) return ABSENT;
  return `${value > 0 ? "+" : ""}${value.toFixed(1)}R`;
}

/**
 * A planned reward-to-risk ratio, as `3.4:1`.
 *
 * Written the way the ledger writes it rather than as a bare number, because
 * `3.4` alone in a column beside `7,500` reads as another level.
 */
export function formatRR(value: number | null): string {
  if (value === null) return ABSENT;
  return `${value.toFixed(1)}:1`;
}

/**
 * An entry band, as one cell. `7,500.00 – 7,560.00`.
 *
 * Both edges take the precision the *wider* of the two deserves, so a band
 * running from 0 to 700,000,000 prints `0 – 700,000,000` rather than
 * `0.00 – 700,000,000`. Two precisions inside one cell reads as two different
 * quantities, and on a band it is the same quantity twice.
 */
export function formatBand(lo: number | null, hi: number | null): string {
  if (lo === null && hi === null) return ABSENT;
  const scale = Math.max(Math.abs(lo ?? 0), Math.abs(hi ?? 0));
  const at = (value: number | null) => formatLevel(value, scale);
  if (lo === null) return `≤ ${at(hi)}`;
  if (hi === null) return `≥ ${at(lo)}`;
  return `${at(lo)} – ${at(hi)}`;
}

/**
 * A target band's bounds, given its comparator.
 *
 * `between` prints both edges, `gt` and `lt` print the one bound they have. A
 * one-sided band drawn as though it had two would invent an edge the forecast
 * never stated.
 */
export function formatBounds(
  comparator: "between" | "gt" | "lt" | null,
  lo: number | null,
  hi: number | null,
): string {
  const scale = Math.max(Math.abs(lo ?? 0), Math.abs(hi ?? 0));
  if (comparator === "gt") {
    return lo === null ? "no floor stated" : `> ${formatLevel(lo, scale)}`;
  }
  if (comparator === "lt") {
    return hi === null ? "no ceiling stated" : `< ${formatLevel(hi, scale)}`;
  }
  if (comparator === "between") {
    // An edge the forecast should have stated and did not. Never printed as a
    // one-sided band: `< 95` is a forecast and a missing floor is an omission.
    if (lo === null) return `… – ${formatLevel(hi, scale)}`;
    if (hi === null) return `${formatLevel(lo, scale)} – …`;
  }
  return formatBand(lo, hi);
}

/**
 * Up, down and neutral, resolved here and nowhere else.
 *
 * Used only where a direction of travel is genuinely what is being shown: a
 * snapshot delta. It is deliberately not used on a scenario band, on a
 * condition, on a probability or on a grade, each of which is a statement about
 * a forecast rather than about a position.
 */
export function directionClass(
  value: number | null | undefined,
): "up" | "down" | "flat" {
  if (value === null || value === undefined || !Number.isFinite(value))
    return "flat";
  if (value === 0) return "flat";
  return value > 0 ? "up" : "down";
}

/** `x of n`, or a dash when either half is missing. Never `0 of 0`. */
export function formatOutOf(hit: number | null, of: number | null): string {
  if (hit === null || of === null) return ABSENT;
  return `${hit} of ${of}`;
}
