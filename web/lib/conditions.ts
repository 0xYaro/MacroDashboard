/**
 * The two claims this system makes that Accounting never has to, resolved into
 * classes here and nowhere else.
 *
 * A component that reached for `var(--down)` because a status "felt bad" is how
 * two surfaces end up describing the same clause differently. Semantic colour
 * lives in `app/globals.css`; this file picks the class.
 */

import { isManualTarget } from "./contract";
import type { ConditionStatus, ScenarioTargetState } from "./contract";

/* ------------------------------------------------------- week labels ----- */

/**
 * `2026-W39` as the operator says it: `W39`.
 *
 * The year is carried in the row and is never in doubt on a surface that shows
 * one week at a time, so spending four characters and a hyphen on it in a
 * section title buys nothing.
 */
export function shortWeek(week: string | null | undefined): string | null {
  if (week === null || week === undefined) return null;
  const match = /W\d{1,2}$/.exec(week.trim());
  return match === null ? week.trim() : match[0];
}

/**
 * The week before this one, as a short label.
 *
 * `unchanged since W38` is the fact a reader wants from a phase call that has
 * been carried; `was Phase 3 · unchanged · 2026-W39` restates the current week
 * and leaves them to work out when the call last moved. ISO years run to 52 or
 * 53 weeks and this system has no calendar of its own, so week 1 of a year
 * names the year rather than guessing which.
 */
export function previousWeek(week: string | null | undefined): string | null {
  if (week === null || week === undefined) return null;
  const match = /^(\d{4})-W(\d{1,2})$/.exec(week.trim());
  if (match === null) return shortWeek(week);
  const year = Number(match[1]);
  const number = Number(match[2]);
  if (number > 1) return `W${String(number - 1).padStart(2, "0")}`;
  // The last week of the prior year is 52 or 53 and which one is a calendar
  // question this app does not answer. It names the year instead of inventing
  // a number.
  return `the last week of ${year - 1}`;
}

/* --------------------------------------------------- condition status ---- */

export interface ConditionCopy {
  label: string;
  tokenClass: string;
  meaning: string;
}

export const CONDITION_COPY: Readonly<Record<ConditionStatus, ConditionCopy>> =
  {
    met: {
      label: "MET",
      tokenClass: "cond-met",
      meaning: "Checked against its data and true right now.",
    },
    "not yet": {
      label: "NOT YET",
      tokenClass: "cond-not-yet",
      meaning: "Checked against its data and not true yet.",
    },
    failed: {
      label: "FAILED",
      tokenClass: "cond-failed",
      meaning: "Checked, and moving away from the condition, not toward it.",
    },
    unevaluable: {
      label: "UNEVALUABLE",
      tokenClass: "cond-unevaluable",
      meaning:
        "Could not be checked: its data has no reading for this date, or a person has to judge it. It never counts as not yet.",
    },
    void: {
      label: "VOID",
      tokenClass: "cond-void",
      meaning:
        "The event this clause named was cancelled or moved past the scenario's time frame. The clause still counts, and the scenario's bar is not lowered.",
    },
  };

/** A void clause is struck through. The one place that rule is stated. */
export function isStruck(status: ConditionStatus): boolean {
  return status === "void";
}

/**
 * How a phase tracker's coverage reads, as the sentence that makes it
 * trustworthy unattended.
 *
 * "3 of 4 machine-checked, 1 awaiting judgment" is a tracker a reader can act
 * on. "3 of 4 met" alone is not, because it does not say whether the fourth was
 * evaluated and false or never evaluated at all.
 *
 * The all-manual case is said as one sentence rather than as an arithmetic
 * identity. "0 of 4 machine-checked, 4 awaiting judgment" is two figures the
 * reader has to subtract before learning the only fact in the line, which is
 * that nothing on this card was checked by the worker this run.
 */
export function coverageSentence(input: {
  machineChecked: number;
  manual: number;
  unevaluable: number;
  of: number;
}): string {
  if (input.of > 0 && input.manual === input.of) {
    return `All ${input.of} conditions need judgment this run`;
  }
  const parts = [`${input.machineChecked} of ${input.of} machine-checked`];
  if (input.manual > 0) parts.push(`${input.manual} awaiting judgment`);
  if (input.unevaluable > 0) parts.push(`${input.unevaluable} unevaluable`);
  return parts.join(", ");
}

/* ------------------------------------------------------ band occupancy --- */

/**
 * The asset a target is written against, taken off the front of its label.
 *
 * A target label is authored as `BTC, gated on an inflow week above +$700M` or
 * `Leadership: Nasdaq beats the Dow again`: an asset, then the qualification
 * the author wanted recorded with it. In a column three scenarios wide the
 * asset is the part the eye scans down and the qualification is the part that
 * truncated every row, so the column shows the asset and the full label is
 * kept on the row's own title.
 *
 * Two authored shapes are in production. The short one puts the qualification
 * after a colon or a comma-and-space. The long one, which the migrated W39
 * rows use, writes the band into the label itself: `ETH $2,480–$2,980`,
 * `10Y 4.90–5.12%`, `BTC $64K–$76K with the $74K falsifier live again`. The
 * band is already its own column, so the name stops at the first figure.
 *
 * A bare comma is not a separator: splitting on it cut `ETH $2,480–$2,980` to
 * `ETH $2` and `S&P 7,520–7,800` to `S&P 7`, which is how this was found. A
 * token like `10Y` or `2Y` is a name, not a figure, because a figure is a
 * number optionally signed, prefixed with `$` and suffixed with K, M, B, `%`
 * or a range dash.
 */
export function assetName(label: string): string {
  const cut = /:\s|,\s/.exec(label);
  const head = (cut === null ? label : label.slice(0, cut.index)).trim();
  const words = head.split(/\s+/);
  const firstFigure = words.findIndex(
    (word, index) => index > 0 && FIGURE.test(word),
  );
  const name =
    firstFigure === -1 ? head : words.slice(0, firstFigure).join(" ");
  return name || label.trim();
}

/** A band figure: `4.90–5.12%`, `$2,480–$2,980`, `+$700M`, `7,520–7,800`, `$76K`. */
const FIGURE = /^[<>≤≥+\-−~]?\$?\d[\d,.]*[KMBkmb%]?([–-]|$)/;

export type BandState = "holding" | "near" | "outside" | "unevaluable";

/**
 * How near a value must be to an edge before the band is called near-breach.
 *
 * A tenth of the band's own width, per `docs/CONTRACT.md`. Relative to the
 * band rather than absolute, because a ten-point cushion is nothing on an
 * S&P band 280 points wide and is the whole band on a VIX band six points wide.
 */
const NEAR_FRACTION = 0.1;

export interface BandGeometry {
  state: BandState;
  /** Where the band's left edge sits, as a percentage of the drawn extent. */
  spanLeftPct: number;
  spanWidthPct: number;
  /** Where the current value sits, clamped into the extent. Null draws nothing. */
  markPct: number | null;
  /** True when the value sat outside the drawn extent and was pinned to an edge. */
  clamped: "low" | "high" | null;
}

/**
 * Where to draw a target band and its current value.
 *
 * The drawn extent is the band padded by half its own width on each side, so a
 * value inside the band lands in the middle 50% of the rail and one that has
 * broken out is visibly outside it without the band collapsing to a sliver. A
 * value further out than that is pinned to the edge and given a caret, because
 * "just outside" and "miles outside" must not draw identically.
 *
 * A one-sided band (`gt` or `lt`) has one real edge and one that runs to the
 * end of the rail, since the forecast never stated a second one.
 */
export function bandGeometry(target: ScenarioTargetState): BandGeometry {
  const unevaluable: BandGeometry = {
    state: "unevaluable",
    spanLeftPct: 25,
    spanWidthPct: 50,
    markPct: null,
    clamped: null,
  };

  if (isManualTarget(target)) return unevaluable;
  const { lo, hi, current, comparator } = target;
  // `holding === null` is the contract's statement that nothing was read. It
  // outranks any arithmetic that could be done on the numbers beside it.
  if (target.holding === null || current === null) return unevaluable;

  // Which edges the forecast stated, which is not the same as which edges are
  // present. A `lt` band has no floor because none was meant; a `between` band
  // with a null `lo` has none because it was never written, and the second is
  // an incomplete forecast rather than a one-sided one. Before `comparator`
  // existed the two were indistinguishable and rendered identically, and only
  // one of them is a forecast.
  if (comparator === "between" && (lo === null || hi === null)) {
    return unevaluable;
  }
  const edgeLo = comparator === "lt" ? null : (lo ?? null);
  const edgeHi = comparator === "gt" ? null : (hi ?? null);
  if (edgeLo === null && edgeHi === null) return unevaluable;

  // The span the forecast named, and the width the extent is scaled against.
  // A one-sided band borrows its width from the distance between its own edge
  // and the current value, which is the only length the data actually states.
  const soleEdge = edgeLo ?? edgeHi!;
  const width =
    edgeLo !== null && edgeHi !== null
      ? edgeHi - edgeLo
      : Math.max(Math.abs(current - soleEdge) * 2, Math.abs(soleEdge) * 0.02);
  if (!(width > 0)) return unevaluable;

  const pad = width / 2;
  const railLo = (edgeLo ?? soleEdge) - pad;
  const railHi = (edgeHi ?? soleEdge) + pad;
  const railWidth = railHi - railLo;
  if (!(railWidth > 0)) return unevaluable;

  const toPct = (value: number) => ((value - railLo) / railWidth) * 100;

  const spanLeft = edgeLo === null ? 0 : toPct(edgeLo);
  const spanRight = edgeHi === null ? 100 : toPct(edgeHi);

  const raw = toPct(current);
  const clamped = raw < 0 ? "low" : raw > 100 ? "high" : null;
  const markPct = Math.min(100, Math.max(0, raw));

  return {
    state: bandState(target, width),
    spanLeftPct: spanLeft,
    spanWidthPct: Math.max(2, spanRight - spanLeft),
    markPct,
    clamped,
  };
}

/**
 * Which of the three occupancy colours a band wears.
 *
 * `holding` is the worker's verdict and is taken as given. Near-breach is a
 * presentation rule over `distance_to_breach`, read in the series' own unit
 * and compared against a tenth of the band's width. When the worker states no
 * distance, the band is drawn holding rather than near: inventing a nearness
 * from the numbers beside it would be this app computing a metric.
 *
 * There is no state for "broken to the upside" and none for "broken to the
 * downside", by design. A band is a statement about a forecast, not about a
 * position moving in your favour.
 */
function bandState(target: ScenarioTargetState, width: number): BandState {
  if (target.holding === null) return "unevaluable";
  if (!target.holding) return "outside";
  const distance = target.distanceToBreach;
  if (distance === null) return "holding";
  return Math.abs(distance) <= NEAR_FRACTION * width ? "near" : "holding";
}

export const BAND_STATE_CLASS: Readonly<Record<BandState, string>> = {
  holding: "is-holding",
  near: "is-near",
  outside: "is-outside",
  unevaluable: "is-unevaluable",
};

/**
 * What a band's state means, for the cell's own title.
 *
 * Spelled out because the three colours are deliberately not the market's two,
 * and a reader arriving from Accounting will expect green and red to mean what
 * they mean there.
 */
/**
 * Whether a band states both the edges its comparator implies.
 *
 * A `between` band missing one is an incomplete forecast, and saying so is the
 * whole reason `comparator` is carried: without it, a floor nobody wrote and a
 * floor nobody meant to write look the same.
 */
export function isIncompleteBand(target: ScenarioTargetState): boolean {
  if (isManualTarget(target)) return false;
  if (target.comparator === "between") {
    return target.lo === null || target.hi === null;
  }
  if (target.comparator === "gt") return target.lo === null;
  if (target.comparator === "lt") return target.hi === null;
  return false;
}

export const BAND_STATE_MEANING: Readonly<Record<BandState, string>> = {
  holding: "Inside the band the forecast named.",
  near: "Inside the band, but within a tenth of its width of an edge.",
  outside:
    "Outside the band. This is a forecast not currently satisfied, not a position losing money.",
  unevaluable:
    "No reading for the series this band is written against, so nothing is claimed about whether it holds.",
};
