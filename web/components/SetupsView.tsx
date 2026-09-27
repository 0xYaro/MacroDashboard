"use client";

import type { SectorFocus, SetupState, StrategyState } from "@/lib/contract";
import { gateRefusedATouch, isUnscored, resolveFocusBand } from "@/lib/contract";
import { ABSENT, formatBand, formatLevel, formatR, formatRR } from "@/lib/numbers";

/**
 * The week's setups, and the one row this whole system was built to surface.
 *
 * Three weeks running, a setup was blocked by a gate clause that was wrong, and
 * each time the error was found the following Monday. `band_touched_today` is
 * derived from the session high and low rather than the close, precisely so
 * that "entry band reached, gate refused, trade forgone" surfaces on the day it
 * happens. When it does, the row is marked and tinted, and the gate's own
 * reason is printed beside it.
 *
 * It is a caution, not a fault. Nothing broke; a decision was made against a
 * rule the operator wrote, and the point is to let them see the rule working
 * while the trade is still in front of them.
 *
 * The gate clause as written is shown under the setup's name, because a gate
 * state of `blocked` with no clause text is a refusal the operator cannot
 * check.
 */

const GATE_COPY: Readonly<Record<string, { label: string; token: string; meaning: string }>> = {
  open: {
    label: "OPEN",
    token: "cond-met",
    meaning: "Every gate clause evaluates true on the latest close.",
  },
  blocked: {
    label: "BLOCKED",
    token: "cond-not-yet",
    meaning: "A gate clause evaluates false, so the entry is refused.",
  },
  unevaluable: {
    label: "UNEVALUABLE",
    token: "cond-unevaluable",
    meaning:
      "A gate clause names a series with no observation for this date, so the gate cannot be evaluated. It is not treated as open and it is not treated as blocked.",
  },
};

const ENTRY_COPY: Readonly<Record<string, string>> = {
  "not triggered": "Entry never traded.",
  open: "Entry traded; neither target nor invalidation hit yet.",
  stopped: "Entry traded and the invalidation hit first.",
  target: "Entry traded and the target hit first.",
};

function SetupRow({
  setup,
  focus,
}: {
  setup: SetupState;
  /**
   * Every sector-focus row of the active strategies, for resolving this
   * setup's `sector_focus_label` back to the band it belongs to.
   */
  focus: readonly SectorFocus[];
}) {
  const refused = gateRefusedATouch(setup);
  // A setup that names no series was never scored at all, so its gate state
  // and entry state were never computed. Left unmarked it looks like a bug.
  const unscored = isUnscored(setup);
  const gate = GATE_COPY[setup.gateState] ?? GATE_COPY.blocked!;
  const band =
    setup.sectorFocusLabel === null
      ? null
      : (focus.find((row) => row.text === setup.sectorFocusLabel) ?? null);
  return (
    <tr
      className={
        refused ? "row-refused" : unscored ? "row-unscored" : undefined
      }
    >
      <td>
        <span className="setup-asset">{setup.asset}</span> {setup.name}
        {/* `sector_focus_label` is the row's *text*, not its band, because
            migration 0011 resolves it through `macro_core.setup.sector_focus_id`
            to the sentence. The band is recovered by matching that sentence
            against the active strategy's own rows rather than by storing it
            twice, which is the same rule the holdings check follows: one fact,
            one place. When it cannot be matched the sentence is shown as
            written, never guessed at. */}
        {band === null ? null : (
          <span className={`row-flag flag-${band.band.toLowerCase()}`} title={band.text}>
            {band.band}
          </span>
        )}
        {refused ? (
          <span
            className="refused-mark"
            title="The entry band was reached on this session and the gate refused, so the trade was forgone. This is the failure that occurred in W37, W38 and W39 and was each time found the following Monday."
          >
            band touched · gate refused
          </span>
        ) : null}
        {unscored ? (
          <span
            className="refused-mark"
            title="This setup names no series. Its entry, invalidation and target are levels on some price and the setup does not say which, so the worker will not resolve them and nothing in this row was evaluated: the gate and entry states below were never computed for it."
          >
            names no series · not scored
          </span>
        ) : null}
        {setup.sectorFocusLabel !== null && band === null ? (
          <small className="setup-focus" title={setup.sectorFocusLabel}>
            {setup.sectorFocusLabel}
          </small>
        ) : null}
        {setup.gateText === null ? null : (
          <small className="setup-detail">gate: {setup.gateText}</small>
        )}
        {/* Why the gate refused, under the clause it refused against. An
            unscored row is the one case this is suppressed: its reason only
            restates the tag already on the row, and the same fact was being
            told three times, as a banner, as a tag and as a sentence. */}
        {setup.gateReason === null || unscored || setup.gateState === "open" ? null : (
          <small className="setup-gate-reason">{setup.gateReason}</small>
        )}
      </td>
      <td className="num">
        {formatBand(setup.entryLo, setup.entryHi)}
        {/* The series these levels are quoted on. Equity setups are quoted
            against a cash index and crypto setups against BTC or ETH, so the
            mapping cannot be inferred from the book. */}
        <span className="cadence">
          {setup.seriesLabel ?? setup.seriesKey ?? "series not stated"}
        </span>
      </td>
      <td className="num">
        {formatLevel(setup.invalidation)}
        <span className="cadence">
          {setup.invalidationDir === null
            ? "direction not stated"
            : `close ${setup.invalidationDir}`}
        </span>
      </td>
      <td className="num">{formatLevel(setup.target)}</td>
      <td className="num">{formatRR(setup.rrPlanned)}</td>
      <td>
        <span className={`chip ${gate.token}`} title={gate.meaning}>
          {gate.label}
        </span>
      </td>
      <td title={ENTRY_COPY[setup.entryState]}>
        {setup.entryState}
        {setup.bandTouchedToday || setup.bandTouchedClose ? (
          <small
            className="setup-detail"
            title="Derived from the session high and low, not from the close. Without it a setup whose band was reached and refused intraday is indistinguishable from one price never approached."
          >
            band touched {setup.bandTouchedClose ? "on the close" : "intraday"}
          </small>
        ) : null}
        {/* Sessions in band and R realised ride under the state they qualify
            rather than in two columns of their own, which pushed the table
            past the panel at 1200px and hid all three off to the right. */}
        <small className="setup-detail">
          {setup.sessionsCarried} session{setup.sessionsCarried === 1 ? "" : "s"} in band
          {" · R "}
          {setup.rRunning === null ? ABSENT : formatR(setup.rRunning)}
        </small>
      </td>
    </tr>
  );
}

function StrategyCard({ strategy }: { strategy: StrategyState }) {
  return (
    <article className="phase-card" aria-label={`${strategy.asset} strategy`}>
      <header className="phase-card-head">
        <span className="phase-asset">{strategy.asset}</span>
        <span className="phase-name">{strategy.posture.split(".")[0]}</span>
        <span className="phase-score">
          {strategy.exposureLo === null || strategy.exposureHi === null
            ? ABSENT
            : `${strategy.exposureLo}–${strategy.exposureHi}% net`}
        </span>
      </header>
      <div className="phase-body">
        <p className="phase-reason">{strategy.stance}</p>
        {strategy.exposureOperatedLo === null ||
        strategy.exposureOperatedHi === null ? null : (
          <p className="exposure-operated">
            <b>Operated at</b> {strategy.exposureOperatedLo}–
            {strategy.exposureOperatedHi}% net, inside the{" "}
            {strategy.exposureLo}–{strategy.exposureHi}% mandate band. The two
            are separate rows because a book run at 60% is not a book run at
            110%.
          </p>
        )}
        <div className="focus-list">
          {strategy.sectorFocus.map((row) => (
            <p className="focus-row" key={row.band}>
              <span className={`chip focus-${row.band.toLowerCase()}`}>
                {row.band}
              </span>
              <span>{row.text}</span>
            </p>
          ))}
        </div>
        {strategy.holdingsCheck.length === 0 ? (
          <p className="holdings-empty">
            No holdings check is recorded against this strategy. Every active
            holding should carry a line; this is raised as a finding rather than
            refused outright, because holdings are re-parsed from the Watchlist
            on every run while the strategy is authored weekly.
          </p>
        ) : (
          <div className="tbl">
            <table className="holdings-table">
              <thead>
                <tr>
                  <th>Holding</th>
                  <th>Action</th>
                  <th>Sector row</th>
                  <th>Note</th>
                </tr>
              </thead>
              <tbody>
                {strategy.holdingsCheck.map((row) => {
                  // Resolved against the sibling sector-focus rows rather than
                  // read off the holding. The band lives in one place, so two
                  // surfaces cannot disagree about which row a ticker sits
                  // under.
                  const focus = resolveFocusBand(row, strategy.sectorFocus);
                  return (
                    <tr key={row.id}>
                      <td className="num">{row.ticker}</td>
                      <td>{row.action}</td>
                      <td>
                        {focus === null ? (
                          <span
                            className="unsupported"
                            title={
                              row.sectorFocusId === null
                                ? "An active holding with no sector-focus row supporting it. This is raised as a finding rather than refused outright, because holdings are re-parsed from the Watchlist on every run while the strategy is authored weekly, so a ticker added on a Wednesday would otherwise fail the parse that discovered it."
                                : "This holding names a sector-focus row that the active strategy does not carry. The band is not shown rather than guessed."
                            }
                          >
                            {row.sectorFocusId === null ? "no row" : "unresolved"}
                          </span>
                        ) : (
                          <span
                            className={`chip focus-${focus.band.toLowerCase()}`}
                            title={focus.text}
                          >
                            {focus.band}
                          </span>
                        )}
                      </td>
                      <td>{row.text}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </article>
  );
}

export function SetupsView({
  setups,
  strategies,
}: {
  setups: readonly SetupState[];
  strategies: readonly StrategyState[];
}) {
  const ordered = [...setups].sort(
    (a, b) => a.asset.localeCompare(b.asset) || a.name.localeCompare(b.name),
  );
  const refusedCount = ordered.filter(gateRefusedATouch).length;
  // Every sector-focus row across both books, so a setup can be matched back
  // to its band whichever strategy authored it.
  const allFocus = strategies.flatMap((strategy) => strategy.sectorFocus);

  return (
    <>
      {refusedCount === 0 ? null : (
        <p className="panel-banner">
          {refusedCount === 1 ? "One setup" : `${refusedCount} setups`} had the
          entry band reached this session and the gate refused. The trade was
          forgone. This is the failure that recurred in W37, W38 and W39 and was
          each time found the following Monday; it is on screen the same day.
        </p>
      )}

      {/* The no-series defect is marked once, on the row it happened to, and
          the page's own health line counts them. It used to be said three
          times over: a banner above the table, a tag on the row and a sentence
          under the row, all carrying the same fact. */}

      {ordered.length === 0 ? (
        <p className="muted">
          No setups are on record. Setups are authored weekly and evaluated on
          the close.
        </p>
      ) : (
        <div className="tbl tbl-wide">
          <table className="setups-table">
            <colgroup>
              <col className="c-setup" />
              <col className="c-band" />
              <col className="c-level" />
              <col className="c-level" />
              <col className="c-rr" />
              <col className="c-gate" />
              <col className="c-entry" />
            </colgroup>
            <thead>
              <tr>
                <th>Setup</th>
                <th className="num">Entry</th>
                <th className="num">Invalidation</th>
                <th className="num">Target</th>
                <th className="num" title="Reward to risk at entry.">
                  R:R
                </th>
                <th>Gate</th>
                <th>Entry state</th>
              </tr>
            </thead>
            <tbody>
              {ordered.map((setup) => (
                <SetupRow focus={allFocus} key={setup.setupId} setup={setup} />
              ))}
            </tbody>
          </table>
          <p
            className="tbl-note"
            title="Evaluating intraday would silently change what a recorded R-multiple means and break comparability with the rows already in the Setup Ledger. The band-touch flag is the one thing derived from the session high and low, because a band reached and refused intraday is the case a close-only reading cannot see."
          >
            Levels are closes.
          </p>
        </div>
      )}

      {strategies.length === 0 ? (
        <p className="muted" style={{ marginTop: 14 }}>
          No strategy is on record, so the stance, the exposure bands and the
          sector-focus rows each setup names are not shown.
        </p>
      ) : (
        <div className="phase-grid" style={{ marginTop: 14 }}>
          {[...strategies]
            .sort(
              (a, b) =>
                ["equities", "crypto"].indexOf(a.asset) -
                ["equities", "crypto"].indexOf(b.asset),
            )
            .map((strategy) => (
              <StrategyCard key={strategy.strategyId} strategy={strategy} />
            ))}
        </div>
      )}
    </>
  );
}
