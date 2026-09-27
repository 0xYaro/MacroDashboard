"use client";

import { Component, type ReactNode } from "react";

import type { Reading } from "@/lib/freshness";

/**
 * Every panel owns its own error boundary.
 *
 * One relation returning something the renderer cannot handle degrades one
 * panel; it must never blank the page. The boundary resets when the reading
 * behind it changes, so a panel that failed on one bad payload recovers on the
 * next good one instead of staying broken until a reload.
 */
interface BoundaryProps {
  children: ReactNode;
  title: string;
  resetKey?: string | number | null;
}

export class PanelBoundary extends Component<
  BoundaryProps,
  { failed: boolean }
> {
  override state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  override componentDidUpdate(previous: BoundaryProps) {
    if (
      this.state.failed &&
      !Object.is(previous.resetKey, this.props.resetKey)
    ) {
      this.setState({ failed: false });
    }
  }

  override render() {
    if (this.state.failed) {
      return (
        <p className="error" style={{ margin: 0 }}>
          {this.props.title} failed to render. Other panels are unaffected, and
          nothing from this one is being shown, because a half-drawn reading is
          worse than no reading.
        </p>
      );
    }
    return this.props.children;
  }
}

/**
 * The empty state, which always names the missing input.
 *
 * "No data" on its own tells the operator nothing about whether to wait. The
 * facts row names the relations that were read, the schedule that would fill
 * them and the clock they belong to, so a worker that has not started can be
 * told apart from a project whose migrations have not run.
 */
export function PanelEmpty({
  reading,
  relations,
  schedule,
}: {
  reading: Reading;
  relations: readonly string[];
  schedule: string;
}) {
  return (
    <div className="panel-empty">
      <span className="panel-empty-mark">
        {reading.state === "failed"
          ? "READ FAILED"
          : reading.state === "pending"
            ? "NO RESPONSE YET"
            : "NOT SHOWING DATA"}
      </span>
      <h3>Nothing is being shown</h3>
      <p>{reading.reason}</p>
      <dl className="panel-empty-facts">
        <div>
          <dt>Relations read</dt>
          <dd>{relations.join(", ")}</dd>
        </div>
        <div>
          <dt>Written by</dt>
          <dd>{schedule}</dd>
        </div>
        <div>
          <dt>Desk clock</dt>
          <dd>Asia/Singapore</dd>
        </div>
      </dl>
    </div>
  );
}

/**
 * A panel, and the guard that makes the rest of the terminal trustworthy.
 *
 * When `reading.hasData` is false the children are never rendered. That is
 * structural on purpose: a panel cannot forget the check, cannot fall through
 * to a zero row, and cannot decide its own empty state is close enough. The
 * price is one prop; the alternative is a terminal that looks fed when it is
 * not.
 *
 * One fault, one owner. The banner is the only place a reason is printed whole
 * and it is never clipped; the header carries a chip and nothing else.
 * Repeating one sentence in three places is not extra honesty, it claims three
 * faults.
 */
export function Panel({
  title,
  index,
  titleNote,
  reading,
  head,
  views,
  children,
  empty,
  footer,
}: {
  title: string;
  /**
   * An optional numeral before the title, and nothing at all when it is absent.
   *
   * It was a two-digit index on every panel, which looked like an ordering the
   * reader was meant to follow and was not one: there is no panel 01, the
   * numbers do not count anything, and a numeral in front of a heading is a
   * promise of structure. A panel that wants one may still pass one.
   */
  index?: string;
  /**
   * When this reading was last recomputed, beside the title.
   *
   * In the header rather than only in the strip below it, because "when was
   * this last updated" is the first question a page that changes twice a day
   * has to answer, and it should not need a scroll.
   */
  titleNote?: string | null;
  reading: Reading;
  head?: ReactNode;
  views?: ReactNode;
  children: ReactNode;
  empty: ReactNode;
  footer?: ReactNode;
}) {
  // One fault, one owner. When the panel is showing its empty state, that
  // state already carries the reason whole, above a facts row that says which
  // relations were read and what writes them. Printing the banner as well
  // would put the same sentence on screen twice and claim two faults.
  const banner =
    reading.hasData || reading.banner === null
      ? reading.banner
      : reading.banner.text === reading.reason
        ? null
        : reading.banner;
  return (
    <section
      aria-label={title}
      className={`panel${reading.dimmed ? " stale" : ""}`}
    >
      <header className="panel-head">
        {index ? <span className="panel-head-num">{index}</span> : null}
        <h2>{title}</h2>
        {titleNote ? (
          <span className="panel-head-when" title="When the worker last recomputed macro_mart. Every figure below is as-of this instant; the observation dates behind them are older by design.">
            {titleNote}
          </span>
        ) : null}
        <div className="panel-head-right">{head}</div>
      </header>
      {views}
      <div className="panel-body">
        {banner ? (
          <p
            className={`panel-banner${banner.severity === "fault" ? " fail" : ""}`}
            role={banner.severity === "fault" ? "alert" : undefined}
          >
            {banner.text}
          </p>
        ) : null}
        <PanelBoundary title={title} resetKey={reading.reason}>
          {reading.hasData ? children : empty}
        </PanelBoundary>
      </div>
      {reading.hasData ? footer : null}
    </section>
  );
}
