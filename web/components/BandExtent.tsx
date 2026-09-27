"use client";

import { isManualTarget, type ScenarioTargetState } from "@/lib/contract";
import {
  BAND_STATE_CLASS,
  BAND_STATE_MEANING,
  assetName,
  bandGeometry,
  isIncompleteBand,
  type BandState,
} from "@/lib/conditions";
import { glossaryFor } from "@/lib/glossary";
import { ABSENT, formatBounds, formatLevel } from "@/lib/numbers";
import { GlossaryTerm } from "./GlossaryTerm";

/**
 * A target band, drawn as a small horizontal extent with the current value
 * marked on it.
 *
 * **Direction colour is deliberately absent.** Green and red mean "this moved
 * in your favour today" on a live book. A scenario target is a statement about
 * a forecast, not about a position, and painting a band that has broken to the
 * upside in the green a winning trade wears would make a forecast read as a
 * P&L. So the three occupancy states are `--flat` inside the band,
 * `--urgency-2` within a tenth of its width of an edge, and `--faint` outside
 * it, which is a gradient of attention rather than of fortune.
 *
 * A band whose series has no reading draws dashed in the unrated token with no
 * marker at all. A marker pinned to either end would be a position this system
 * does not hold, and `holding: null` is the contract's way of saying nothing is
 * claimed.
 *
 * A `manual` target carries no band. It is presented for judgment at grading
 * and the empty slot keeps the row's geometry so the column does not collapse.
 *
 * **The compact variant does not draw the extent at all.** At 76 pixels, inside
 * a cell holding thirteen of them, the mark and the span were a diagram the
 * reader had to be taught to read, and the operator could not. A reader
 * scanning three scenarios across one row is comparing bands to each other,
 * not studying one, so the compact variant answers the three questions in
 * words and figures instead: what is it now, does it hold, and what was
 * forecast. `IN`, `NEAR` and `OUT` need no key.
 *
 * **Its four columns are asset, band, now, state, in that order and each at a
 * width its own content fits.** The earlier order put the label, the value and
 * the state chip in front of the band and gave the four of them about 260
 * pixels between them, so every label truncated: `10Y 4.90–…`, `BTC $76K…`,
 * `Nasdaq be…`. A column of ellipses is not a comparison. The asset is the
 * short name off the front of the label, the authored qualification lives on
 * the row's title, and nothing wraps or truncates at 1280.
 */
const STATE_WORD: Readonly<Record<BandState, string>> = {
  holding: "IN",
  near: "NEAR",
  outside: "OUT",
  unevaluable: "—",
};

/**
 * One target, read at a glance and across three scenarios at once.
 *
 * `S&P   7,520 – 7,800   7,650.50   IN`: which asset, what was forecast, what
 * it is now, whether it holds. The band sits beside the asset because the two
 * are the forecast and the third figure is the tape's answer to it, and the
 * state word sits last because it is the column the eye runs down.
 */
function CompactBand({ target }: { target: ScenarioTargetState }) {
  const full = target.text ?? target.label;

  if (isManualTarget(target)) {
    // A judgment target whose label is its own sentence has no short name to
    // put in the label column; printing it there as well drew the sentence
    // twice, one copy over the other. It takes the row alone instead.
    const name = assetName(target.label);
    const alone = full.startsWith(name) || name.length > 24;
    const glossary = alone ? null : glossaryFor({ label: name });
    return (
      <div
        className="band-row is-compact is-manual-row"
        title={glossary === null ? full : undefined}
      >
        {alone ? null : (
          <span className="band-label">
            <GlossaryTerm entry={glossary}>{name}</GlossaryTerm>
          </span>
        )}
        <span className={`band-manual-text${alone ? " is-alone" : ""}`}>
          {full}
        </span>
        <span
          className="band-state is-judgment"
          title="A judgment target. It counts toward the targets this scenario is graded on, and it is never scored by the worker: it is read and called at grading."
        >
          JUDGE
        </span>
      </div>
    );
  }

  const geometry = bandGeometry(target);
  const state = geometry.state;
  const incomplete = isIncompleteBand(target);
  const glossary = glossaryFor({ label: target.label });

  return (
    // With a definition, the popover carries the full authored label and the
    // row drops its own title, so the two never stack over each other.
    <div
      className="band-row is-compact"
      title={glossary === null ? target.label : undefined}
    >
      <span className="band-label">
        <GlossaryTerm
          entry={glossary}
          heading={
            assetName(target.label) === target.label ? undefined : target.label
          }
        >
          {assetName(target.label)}
        </GlossaryTerm>
      </span>
      <span className={`band-bounds${incomplete ? " is-incomplete" : ""}`}>
        {formatBounds(target.comparator, target.lo, target.hi)}
      </span>
      <span className={`band-current ${BAND_STATE_CLASS[state]}`}>
        {target.current === null
          ? ABSENT
          : formatLevel(
              target.current,
              Math.max(Math.abs(target.lo ?? 0), Math.abs(target.hi ?? 0)),
            )}
      </span>
      <span
        className={`band-state ${BAND_STATE_CLASS[state]}`}
        title={
          incomplete
            ? "This band was written as a range and one of its two edges was never stated, so an edge the forecast should have named is missing."
            : BAND_STATE_MEANING[state]
        }
      >
        {STATE_WORD[state]}
      </span>
    </div>
  );
}

export function BandRow({
  target,
  compact = false,
}: {
  target: ScenarioTargetState;
  /**
   * The variant used inside the scenario comparison table, where the extent
   * shares a cell with twelve others. Narrower, and the label sits above the
   * rail rather than beside it, so thirteen bands fit a column instead of
   * needing a card.
   */
  compact?: boolean;
}) {
  const cls = `band-row${compact ? " is-compact" : ""}`;

  if (compact) return <CompactBand target={target} />;

  if (isManualTarget(target)) {
    return (
      <div className={cls}>
        <span className="band-label">{target.text ?? target.label}</span>
        <span
          className="band is-manual"
          title="A judgment target. It counts toward the targets this scenario is graded on, and it is never scored by the worker: it is read and called at grading."
        >
          JUDGMENT
        </span>
        <span className="band-bounds">—</span>
        <span className="band-current is-unevaluable">not scored</span>
      </div>
    );
  }

  const geometry = bandGeometry(target);
  const stateClass = BAND_STATE_CLASS[geometry.state];
  const incomplete = isIncompleteBand(target);

  return (
    <div className={cls}>
      <span className="band-label">
        <GlossaryTerm entry={glossaryFor({ label: target.label })}>
          {target.label}
        </GlossaryTerm>
      </span>
      <span
        className={`band ${stateClass}`}
        role="img"
        aria-label={`${target.label}: ${
          incomplete
            ? "an edge this band should state was never written"
            : BAND_STATE_MEANING[geometry.state]
        }`}
        title={
          incomplete
            ? "This band was written as a range and one of its two edges was never stated, so an edge the forecast should have named is missing. It is not drawn as a one-sided band: below 95 is a forecast, and a missing floor is an omission."
            : BAND_STATE_MEANING[geometry.state]
        }
      >
        <span className="band-rail" />
        <span
          className="band-span"
          style={{
            left: `${geometry.spanLeftPct}%`,
            width: `${geometry.spanWidthPct}%`,
          }}
        />
        {geometry.markPct === null ? null : (
          <span
            className={`band-mark${
              geometry.clamped === null
                ? ""
                : ` is-clamped clamp-${geometry.clamped}`
            }`}
            style={{ left: `${geometry.markPct}%` }}
          />
        )}
      </span>
      <span className={`band-bounds${incomplete ? " is-incomplete" : ""}`}>
        {formatBounds(target.comparator, target.lo, target.hi)}
      </span>
      <span className={`band-current ${stateClass}`}>
        {target.current === null
          ? ABSENT
          : formatLevel(
              target.current,
              // The value takes the band's precision, not its own: 6,210,000
              // beside `0 – 700,000,000` is one quantity shown twice.
              Math.max(Math.abs(target.lo ?? 0), Math.abs(target.hi ?? 0)),
            )}
      </span>
    </div>
  );
}
