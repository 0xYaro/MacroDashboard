"""The two runs, and the command line that starts them.

**09:30 Asia/Singapore, `--full`.** Parse the three vault files the OpenClaw
crons wrote at 08:45 and 09:15, and archive every catalyst that has happened.
Fetch every deterministic source. Normalise,
upsert observations, record gaps. Evaluate every condition, target band and
setup gate. Recompute `macro_mart`. On a Singapore Monday, additionally grade
the prior week and recompute the calibration read.

**21:00 Asia/Singapore, `--rescore`.** Fetch every deterministic source.
Evaluate. Recompute `macro_mart`.

The rescore makes no LLM call, reads no vault file and imports nothing that
could. That is not a convention this file promises to keep: there is no LLM
client anywhere in this package, so a model being unreachable cannot fail this
path because there is nothing on it to be unreachable.

`--dry-run` needs no database, writes nothing, and prints the normalised rows as
JSON on stdout with the log on stderr, so `--dry-run | jq` works.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from . import archive, clock, config, drift, evaluate, grading, premise, vault
from . import series as series_module
from .calibration import read as calibration_read
from .expr import Context
from .fetchers import (
    FetchContext,
    breadth,
    breadth_history,
    deterministic,
    etf_flows,
    run_one,
)
from .fetchers import base as fetch_base
from .http import Client, FetchRefused
from .model import Finding, FindingScope, Gap, Observation, SourceResult, VaultFeed
from .observations import History
from .store import MartFlush, Store, WriteRefused

LOG = logging.getLogger("macro")

RunKind = Literal["full", "rescore"]

#: How much history the evaluator loads. Long enough for a 200-day average and
#: for the longest predicate window any condition has used, and short enough
#: that the read is one index scan.
HISTORY_DAYS = 420

#: The checks the fetch step raises. Both runs fetch every source.
FETCH_CHECK_KEYS: tuple[str, ...] = (
    *fetch_base.CHECK_KEYS,
    *breadth.CHECK_KEYS,
    *etf_flows.CHECK_KEYS,
)

#: The checks evaluating the active judgment raises. Both runs evaluate it.
EVALUATION_CHECK_KEYS: tuple[str, ...] = (
    *evaluate.CHECK_KEYS,
    *premise.CHECK_KEYS,
    *grading.EVALUATION_CHECK_KEYS,
)

WRITE_REFUSED = "database_write_refused"
SETUP_REFUSED = "setup_series_missing"


@dataclass(slots=True)
class RunOutcome:
    """What one run produced, for the dry run to print and the tests to assert on."""

    run_kind: RunKind
    now: datetime
    observations: list[Observation] = field(default_factory=list)
    gaps: list[Gap] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    sources: list[SourceResult] = field(default_factory=list)
    #: What this run looked at, and so which open findings it may close.
    evaluated: FindingScope = field(default_factory=FindingScope)

    def attempted(self, subject: str) -> None:
        """A write or read on `subject` was tried, so its refusal is this run's to close."""
        self.evaluated.add_subject(WRITE_REFUSED, subject)

    def as_json(self) -> dict[str, Any]:
        return {
            "run_kind": self.run_kind,
            "now": self.now.isoformat(),
            "sources": [
                {
                    "source": one.source,
                    "status": one.status,
                    "rows": len(one.observations),
                    "gaps": len(one.gaps),
                    "error": one.error,
                }
                for one in self.sources
            ],
            "observations": [
                {
                    "series": one.series_key,
                    "asof": one.asof.isoformat(),
                    "value": None if one.value is None else str(one.value),
                    "display": one.display,
                    "tier": one.tier,
                    "source": one.source,
                    "note": one.note,
                    "extra": one.extra,
                }
                for one in sorted(self.observations, key=lambda o: (o.series_key, o.asof))
            ],
            "gaps": [
                {
                    "series": one.series_key,
                    "asof": one.asof.isoformat(),
                    "permanent": one.permanent,
                    "reason": one.reason,
                }
                for one in self.gaps
            ],
            "findings": [
                {
                    "check_key": one.check_key,
                    "subject": one.subject,
                    "severity": one.severity,
                    "sentence": one.sentence,
                }
                for one in self.findings
            ],
        }


async def fetch_all(
    registry: series_module.Registry,
    *,
    now: datetime,
    cursors: dict[str, Any],
    store: Store | None,
    run_kind: RunKind,
) -> list[SourceResult]:
    """Run every deterministic source, one `ingest_run` row each, whatever happens.

    Both runs write one row per source. A source that fell over writes one too,
    with its status and its sentence, because a source that is silently absent
    and a source that failed are the same empty table otherwise.
    """
    ctx = FetchContext(now=now, registry=registry, cursor=cursors)
    results: list[SourceResult] = []
    async with Client() as client:
        for fetcher in deterministic():
            started = datetime.now(UTC)
            run_id = None
            if store is not None:
                try:
                    run_id = await store.start_run(fetcher.source, run_kind, started)
                except WriteRefused as exc:
                    LOG.error("%s: could not open an ingest_run row: %s", fetcher.source, exc)
            result = await run_one(fetcher, client, ctx)
            results.append(result)
            LOG.info(
                "%-18s %-7s %3d of %-3d rows %2d gaps%s",
                fetcher.source,
                result.status,
                len(result.delivered),
                len(result.declared),
                len(result.gaps),
                f"  {result.sentence}" if result.sentence else "",
            )
            if store is not None and run_id is not None:
                try:
                    await store.finish_run(
                        run_id,
                        finished_at=datetime.now(UTC),
                        rows=len(result.observations),
                        status=result.status,
                        # The sentence, not the exception: a partial run has no
                        # exception and is exactly the case this column exists
                        # to surface.
                        error=result.sentence,
                    )
                except WriteRefused as exc:
                    LOG.error("%s: could not close its ingest_run row: %s", fetcher.source, exc)
    return results


async def run_once(
    settings: config.Settings,
    *,
    run_kind: RunKind,
    now: datetime,
    dry_run: bool,
) -> RunOutcome:
    """One complete pass. The only function that orders the whole run."""
    registry = series_module.load(settings.series_yaml)
    outcome = RunOutcome(run_kind=run_kind, now=now)

    store: Store | None = None
    if not dry_run:
        store = await Store.connect(config.require_database(settings), ssl=settings.database_ssl)

    try:
        series_ids: dict[str, str] = {}
        cursors: dict[str, Any] = {}
        if store is not None:
            series_ids = await store.sync_series(registry)
            cursors = await store.cursors()

        results = await fetch_all(
            registry, now=now, cursors=cursors, store=store, run_kind=run_kind
        )
        outcome.sources = results
        outcome.evaluated.add(*FETCH_CHECK_KEYS)
        for result in results:
            outcome.observations.extend(result.observations)
            outcome.gaps.extend(result.gaps)
            outcome.findings.extend(result.findings)

        if run_kind == "full":
            # A feed the desk writes (MACRO_FEED_SOURCE_*=desk) is not read at all,
            # so its note can never retire or deactivate what the desk wrote.
            feed = vault.read_all(
                catalyst_file=(
                    settings.catalyst_file if settings.reads_vault("catalysts") else None
                ),
                watchlist_file=(
                    settings.watchlist_file if settings.reads_vault("holdings") else None
                ),
                blackswan_file=(
                    settings.blackswan_file if settings.reads_vault("tail_risks") else None
                ),
                seen_on=now.date(),
            )
            outcome.findings.extend(feed.findings)
            outcome.evaluated.add(*vault.CHECK_KEYS)
            LOG.info(
                "vault              ok     %3d catalysts %2d changes %2d holdings %2d risks%s",
                len(feed.catalysts),
                len(feed.changes),
                len(feed.holdings),
                len(feed.tail_risks),
                "".join(
                    f"  ({name} from the desk)"
                    for name in config.FEEDS
                    if not settings.reads_vault(name)
                ),
            )
        else:
            feed = VaultFeed()

        if store is None:
            return outcome

        await _persist(store, outcome, feed, registry, series_ids, now=now)
        if run_kind == "full":
            await _record_vault_feeds(store, settings, feed, now=now)
        if run_kind == "full" and clock.is_monday_in_singapore(now):
            await _close_the_week(store, outcome, registry, now=now)
        # Last, so every step's findings are in, including Monday's grading.
        await _sync_findings(store, outcome, now=now)
        return outcome
    finally:
        if store is not None:
            await store.aclose()


async def _persist(
    store: Store,
    outcome: RunOutcome,
    feed: VaultFeed,
    registry: series_module.Registry,
    series_ids: dict[str, str],
    *,
    now: datetime,
) -> None:
    """Write observations, the vault feed and the recomputed mart.

    A refused write never kills the run. The observations are upserts, so the
    next pass redoes them; the findings say what did not land.
    """
    for result in outcome.sources:
        try:
            await store.write_batches(result.batches)
        except WriteRefused as exc:
            LOG.error("%s: raw batches refused: %s", result.source, exc.sentence)

    outcome.attempted("macro_core.observation")
    try:
        written = await store.write_observations(
            outcome.observations, series_ids, received_at=now
        )
        LOG.info("observations       ok     %3d rows upserted", written)
    except WriteRefused as exc:
        outcome.findings.append(_write_finding("macro_core.observation", exc))
        LOG.error("observations refused: %s", exc.sentence)

    outcome.attempted("macro_quality.gap")
    try:
        # Clear first, then open: a reading that arrived closes its own
        # recoverable gap, and the gaps opened below are for dates nothing
        # arrived for, so the two sets cannot overlap.
        await store.clear_filled_gaps(outcome.observations, series_ids)
        await store.write_gaps(outcome.gaps, series_ids)
    except WriteRefused as exc:
        outcome.findings.append(_write_finding("macro_quality.gap", exc))

    if feed.catalysts or feed.holdings or feed.tail_risks or feed.changes:
        outcome.attempted("the vault feed")
        try:
            await store.write_catalysts(feed.catalysts)
            # Each feed retires only on its own whole file. The block runs when
            # any file produced a row, so a Monitor that was unreadable, empty or
            # missing a section beside a Watchlist that parsed would otherwise
            # compare every tracked catalyst against an empty list.
            if feed.catalysts_whole:
                stored = await store.tracked_catalysts()
                await store.retire_catalysts(
                    vault.retirements(
                        stored, [one.external_key for one in feed.catalysts], seen_on=now.date()
                    ),
                    retired_on=now.date(),
                )
            await store.write_watchlist_changes(feed.changes)
            await store.write_holdings(feed.holdings, deactivate_missing=feed.holdings_whole)
            await store.write_tail_risks(feed.tail_risks, retire_missing=feed.tail_risks_whole)
        except WriteRefused as exc:
            outcome.findings.append(_write_finding("the vault feed", exc))

    # The archive runs on every full run whoever wrote the calendar: with the
    # catalysts feed on the desk, the vault feed above can be empty.
    if outcome.run_kind == "full":
        outcome.attempted("the catalyst archive")
        try:
            passed = archive.from_live(await store.unarchived_catalysts(before=now), now=now)
            await store.write_occurrences(passed)
            if passed:
                LOG.info("catalyst archive   ok     %3d occurrences archived", len(passed))
        except WriteRefused as exc:
            outcome.findings.append(_write_finding("the catalyst archive", exc))

    outcome.attempted("the observation history")
    try:
        history = History(
            await store.history(since=(now.date() - timedelta(days=HISTORY_DAYS))),
            gaps=await store.open_gaps(since=(now.date() - timedelta(days=HISTORY_DAYS))),
        )
    except WriteRefused as exc:
        outcome.findings.append(_write_finding("the observation history", exc))
        return

    await _recompute_mart(store, outcome, registry, history, series_ids, now=now)


async def _sync_findings(store: Store, outcome: RunOutcome, *, now: datetime) -> None:
    """Open what this run found and close what it looked at and no longer finds.

    Runs once, after every step, so nothing a later step raised is left out,
    and closes only inside `outcome.evaluated`, so a finding another run or the
    authoring agent raised is left for its own writer.
    """
    try:
        await store.sync_findings(outcome.findings, now=now, scope=outcome.evaluated)
    except WriteRefused as exc:
        LOG.error("findings refused: %s", exc.sentence)


async def _recompute_mart(
    store: Store,
    outcome: RunOutcome,
    registry: series_module.Registry,
    history: History,
    series_ids: dict[str, str],
    *,
    now: datetime,
) -> None:
    """Evaluate every condition, band and gate, then flush `macro_mart` as one write.

    The judgment half needs `macro_core`'s active rows, which the weekly
    authoring agent writes. Before that agent has run there is nothing to score,
    and the snapshot is the whole of the mart; that is a system with no judgment
    in it yet, not a system that failed to evaluate one.
    """
    outcome.attempted("the active judgment rows")
    try:
        phase_calls = await store.active_phase_calls()
        scenario_set = await store.active_scenario_set()
        setups = await store.active_setups()
        outcome.findings.extend(store.drain_refusals())
        asserted = await store.asserted_conditions()
        catalysts = await store.catalysts_by_id()
        weeks = await store.graded_weeks()
    except WriteRefused as exc:
        outcome.findings.append(_write_finding("the active judgment rows", exc))
        return
    outcome.evaluated.add(*EVALUATION_CHECK_KEYS)
    for one in setups:
        outcome.evaluated.add_subject(SETUP_REFUSED, one.setup_id)

    asof = _evaluation_date(registry, history, now)
    anchor = scenario_set.authored_asof if scenario_set is not None else None
    ctx = Context(asof=asof, history=history, registry=registry)

    # Every series a failed source owns, not only the ones it managed to
    # deliver: a source that served nothing has no observations to name, and
    # its rows would otherwise read as series that quietly fell behind.
    failed_keys = {key for result in outcome.sources if result.error for key in result.series}
    snapshot_rows = evaluate.snapshot(
        registry,
        history,
        now=now,
        anchor_asof=anchor,
        source_status=dict.fromkeys(failed_keys, "failed"),
    )

    condition_states: list[Any] = []
    phase_scores = []
    for call in phase_calls:
        score, states = evaluate.phase(
            call, ctx=ctx, asserted=asserted, activated_on=call.activated_at.date()
        )
        phase_scores.append(score)
        condition_states.extend(states)
        stale = evaluate.stale_read_finding(call, score)
        if stale is not None:
            outcome.findings.append(stale)
        outcome.findings.extend(grading.phase_score_findings(call.asset, score.of))

    scenario_states: list[Any] = []
    target_states: list[Any] = []
    if scenario_set is not None:
        voidings, premise_findings = premise.voidings(scenario_set, catalysts, now=now)
        outcome.findings.extend(premise_findings)
        if voidings:
            outcome.attempted("macro_core.trigger_clause")
            try:
                await store.void_clauses(
                    [(one.condition_id, one.reason, one.void_at) for one in voidings]
                )
                scenario_set = await store.active_scenario_set() or scenario_set
            except WriteRefused as exc:
                outcome.findings.append(_write_finding("macro_core.trigger_clause", exc))

        scenario_states, target_states, clause_states = evaluate.scenarios(
            scenario_set,
            ctx=ctx,
            asserted=asserted,
            activated_on=scenario_set.activated_at.date(),
        )
        condition_states.extend(clause_states)
        outcome.findings.extend(grading.probability_findings(scenario_set))
        outcome.findings.extend(
            grading.unscored_set_finding(target_states, week=scenario_set.week)
        )

    setup_states = [evaluate.setup_state(one, ctx) for one in setups]

    drift_row = None
    if scenario_set is not None:
        outcome.attempted("macro_core.tail_risk")
        try:
            risks = await store.tail_risks_seen()
        except WriteRefused as exc:
            outcome.findings.append(_write_finding("macro_core.tail_risk", exc))
            risks = []
        drift_row = drift.compute(
            scenario_set=scenario_set,
            phase_calls=phase_calls,
            setups=setups,
            asserted=asserted,
            ctx=ctx,
            now_conditions=condition_states,
            now_scenarios=scenario_states,
            now_targets=target_states,
            now_setups=setup_states,
            tail_risks=risks,
        )
    outcome.findings.extend(evaluate.malformed_findings(condition_states))
    outcome.findings.extend(evaluate.coverage_findings(condition_states))

    all_clauses = [
        clause
        for scenario in (scenario_set.scenarios if scenario_set is not None else ())
        for clause in scenario.clauses
    ]
    catalyst_rows = evaluate.catalyst_window(
        list(catalysts.values()), all_clauses, now=now, ids=_identity(catalysts)
    )
    calibration = calibration_read(weeks) if weeks else None

    LOG.info(
        "evaluate           ok     %3d snapshot %2d conditions %2d setups %2d catalysts  drift %s",
        len(snapshot_rows),
        len(condition_states),
        len(setup_states),
        len(catalyst_rows),
        "none" if drift_row is None else f"{drift_row.score}/10",
    )

    outcome.attempted("macro_mart")
    try:
        await MartFlush(store).write(
            snapshot_rows=snapshot_rows,
            condition_states=condition_states,
            phase_scores=phase_scores,
            scenario_states=scenario_states,
            target_states=target_states,
            setup_states=setup_states,
            catalyst_rows=catalyst_rows,
            calibration=calibration,
            series_ids=series_ids,
            evaluated_at=now,
            drift=drift_row,
        )
    except WriteRefused as exc:
        # The whole flush rolls back, which is deliberate: a stale mart beats
        # half of one evaluation beside half of another.
        outcome.findings.append(_write_finding("macro_mart", exc))
        LOG.error("mart flush refused: %s", exc.sentence)


async def _record_vault_feeds(
    store: Store, settings: config.Settings, feed: VaultFeed, *, now: datetime
) -> None:
    """One `ingest_run` per feed still read from its note, for `macro_mart.feed_freshness`.

    A note that was unreadable, empty or missing a section is recorded as failed,
    so the page shows that feed stale rather than current.
    """
    counts = {
        "catalysts": (len(feed.catalysts), feed.catalysts_whole, "Catalyst Monitor.md"),
        "holdings": (len(feed.holdings) + len(feed.changes), feed.holdings_whole,
                     "Market Watchlist.md"),
        "tail_risks": (len(feed.tail_risks), feed.tail_risks_whole, "Blackswan Events.md"),
    }
    for name, (rows, whole, note) in counts.items():
        if not settings.reads_vault(name):
            continue
        try:
            await store.record_feed_run(
                name,
                started_at=now,
                finished_at=datetime.now(now.tzinfo),
                rows=rows,
                status="ok" if whole else "failed",
                error=None if whole else (
                    f"{note} was unreadable, empty or missing a section, so nothing it feeds "
                    "was retired"
                ),
            )
        except WriteRefused as exc:
            LOG.error("%s feed run not recorded: %s", name, exc.sentence)


def _identity(catalysts: dict[str, Any]) -> dict[str, str]:
    """`external_key` to `macro_core.catalyst.id`, for the catalyst window."""
    return {one.external_key: identifier for identifier, one in catalysts.items()}


def _evaluation_date(
    registry: series_module.Registry, history: History, now: datetime
) -> date:
    """The date every condition is evaluated at.

    The newest session the equity tape carries, not today. Evaluating against a
    calendar date the market has not reached yet would make every condition
    unevaluable at 09:30 on a Monday, which is true but useless; the state the
    dashboard reports is the state as of the last close.
    """
    sessions = history.sessions("spx_cash")
    if sessions:
        return sessions[-1]
    for key in sorted(registry.keys()):
        found = history.sessions(key)
        if found:
            return found[-1]
    return now.date()


async def _close_the_week(
    store: Store,
    outcome: RunOutcome,
    registry: series_module.Registry,
    *,
    now: datetime,
) -> None:
    """Monday's extra work: grade the prior week and check both ledgers.

    A setup's outcome is a fact about the tape and is graded here in full. A
    scenario's grade is a judgment and is not: `Materialized`, `Partial` and
    `Not` are read off a prose trigger and a table of bands, and a
    machine-assigned `Partial` would enter the Brier series beside eleven
    hand-assigned ones and make the whole record incomparable. The week is
    reported as awaiting judgment instead.
    """
    graded_week = clock.previous_week(clock.iso_week(now.date()))
    outcome.attempted("the weekly grading read")
    try:
        history = History(
            await store.history(since=(now.date() - timedelta(days=HISTORY_DAYS))),
            gaps=await store.open_gaps(since=(now.date() - timedelta(days=HISTORY_DAYS))),
        )
        proposed = await store.setups_for_week(graded_week)
        outcome.findings.extend(store.drain_refusals())
        already = await store.graded_week_keys()
        still_open = await store.open_setup_grades()
    except WriteRefused as exc:
        outcome.findings.append(_write_finding("the weekly grading read", exc))
        return
    outcome.evaluated.add(*grading.WEEKLY_CHECK_KEYS)
    for one in proposed:
        outcome.evaluated.add_subject(SETUP_REFUSED, one.setup_id)

    ctx = Context(asof=_evaluation_date(registry, history, now), history=history, registry=registry)
    grades = grading.grade_setups(proposed, ctx=ctx, graded_week=graded_week)
    outcome.attempted("macro_core.setup_grade")
    try:
        await store.write_setup_grades(grades)
    except WriteRefused as exc:
        outcome.findings.append(_write_finding("macro_core.setup_grade", exc))

    outcome.findings.extend(
        grading.setup_check_findings(proposed, grades, graded_week=graded_week)
    )
    outcome.findings.extend(
        grading.ledger_findings(
            still_open,
            current_week=clock.iso_week(now.date()),
            previous_week=graded_week,
        )
    )
    outcome.findings.extend(grading.scorecard_row_findings(already, [graded_week]))
    LOG.info(
        "grading            ok     %3d setups graded for %s",
        len(grades),
        graded_week,
    )


def _write_finding(subject: str, exc: WriteRefused) -> Finding:
    return Finding(
        check_key="database_write_refused",
        subject=subject,
        sentence=(
            f"A write to {subject} was refused: {exc.sentence} Nothing was partially applied and "
            "the next run redoes it, because every write in this worker is an idempotent upsert."
        ),
        severity=1,
    )


def _logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        stream=sys.stderr,
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
    )
    # A dependency's own INFO chatter would put a query string, and therefore a
    # key, into a log file that lives under ~/Library/Logs for ever.
    for noisy in ("httpx", "httpcore", "asyncpg", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="macro-worker",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--full",
        action="store_true",
        help="the 09:30 Asia/Singapore run: vault, every source, evaluate, and Monday grading",
    )
    mode.add_argument(
        "--rescore",
        action="store_true",
        help="the 21:00 Asia/Singapore run: every deterministic source, evaluate, no LLM",
    )
    mode.add_argument(
        "--archive-backfill",
        metavar="PATH",
        default=None,
        help="load a reviewed JSON file of past catalysts into the archive, then exit",
    )
    mode.add_argument(
        "--breadth-backfill",
        metavar="SINCE",
        default=None,
        help=(
            "rebuild breadth for every session from SINCE (YYYY-MM-DD) to the last close "
            "from Yahoo constituent closes, fill the sessions not stored, then exit"
        ),
    )
    mode.add_argument(
        "--etf-backfill",
        action="store_true",
        help=(
            "load every session of US spot BTC and ETH ETF flows SoSoValue serves (about "
            "300), checked against Farside, with the week-to-date and streak series derived "
            "from them; fill the sessions not stored, then exit"
        ),
    )
    parser.add_argument(
        "--breadth-overwrite",
        action="store_true",
        help=(
            "with --breadth-backfill, also redo sessions an earlier backfill stored; a "
            "session stored by any other method is never replaced"
        ),
    )
    parser.add_argument(
        "--etf-overwrite",
        action="store_true",
        help="with --etf-backfill, also replace sessions already stored",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="fetch and normalise, write nothing, print the rows as JSON on stdout",
    )
    parser.add_argument(
        "--at",
        default=None,
        help="pin the run to an ISO instant instead of now, for a replay",
    )
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    """The console entry point. `macro-worker --full` or `macro-worker --rescore`."""
    from dotenv import load_dotenv  # local: nothing in the package opens a file to find one

    load_dotenv()
    args = _parser().parse_args(argv)
    try:
        settings = config.load()
    except config.Misconfigured as exc:
        # The same sentence and exit code as a misconfiguration found later,
        # not a traceback: the level the settings would have set is unknown.
        _logging("INFO")
        LOG.error("%s", exc)
        return 2
    _logging(settings.log_level)

    if args.archive_backfill:
        return _archive_backfill(settings, Path(args.archive_backfill), dry_run=args.dry_run)
    if args.breadth_backfill:
        return _breadth_backfill(
            settings,
            date.fromisoformat(args.breadth_backfill),
            now=datetime.fromisoformat(args.at).astimezone(UTC) if args.at else datetime.now(UTC),
            dry_run=args.dry_run,
            overwrite=args.breadth_overwrite,
        )

    if args.etf_backfill:
        return _etf_backfill(
            settings,
            now=datetime.fromisoformat(args.at).astimezone(UTC) if args.at else datetime.now(UTC),
            dry_run=args.dry_run,
            overwrite=args.etf_overwrite,
        )

    run_kind: RunKind = "rescore" if args.rescore else "full"
    now = (
        datetime.fromisoformat(args.at).astimezone(UTC)
        if args.at
        else datetime.now(UTC)
    )
    local = clock.singapore_local(now)
    LOG.info(
        "%s run, %s Singapore%s",
        run_kind,
        local.strftime("%Y-%m-%d %H:%M"),
        " (Monday: the week closes)" if clock.is_monday_in_singapore(now) else "",
    )

    try:
        outcome = asyncio.run(
            run_once(settings, run_kind=run_kind, now=now, dry_run=args.dry_run)
        )
    except config.Misconfigured as exc:
        LOG.error("%s", exc)
        return 2
    except WriteRefused as exc:
        LOG.error("%s", exc.sentence)
        return 1

    if args.dry_run:
        json.dump(outcome.as_json(), sys.stdout, indent=1, ensure_ascii=False, default=str)
        sys.stdout.write("\n")

    unfinished = [one for one in outcome.sources if one.status != "ok"]
    for one in unfinished:
        LOG.warning("%s", one.sentence)
    if unfinished:
        LOG.warning(
            "%d of %d sources did not finish", len(unfinished), len(outcome.sources)
        )
    return 0


def _archive_backfill(settings: config.Settings, path: Path, *, dry_run: bool) -> int:
    """Load past catalysts into `macro_core.catalyst_occurrence`. Re-runnable."""
    try:
        rows = archive.from_backfill(path)
    except (OSError, ValueError) as exc:
        LOG.error("backfill refused: %s", exc)
        return 2
    families = sum(1 for one in rows if one.family)
    LOG.info(
        "backfill %s: %d occurrences, %d with a recurring family, %s to %s",
        path.name,
        len(rows),
        families,
        min(one.occurred_on for one in rows).isoformat(),
        max(one.occurred_on for one in rows).isoformat(),
    )
    if dry_run:
        return 0

    async def load() -> None:
        store = await Store.connect(config.require_database(settings), ssl=settings.database_ssl)
        try:
            await store.write_occurrences(rows)
        finally:
            await store.aclose()

    try:
        asyncio.run(load())
    except config.Misconfigured as exc:
        LOG.error("%s", exc)
        return 2
    except WriteRefused as exc:
        LOG.error("%s", exc.sentence)
        return 1
    LOG.info("backfill written: %d occurrences upserted", len(rows))
    return 0


def _breadth_backfill(
    settings: config.Settings,
    since: date,
    *,
    now: datetime,
    dry_run: bool,
    overwrite: bool,
) -> int:
    """Rebuild breadth history from Yahoo closes. Re-runnable.

    Fills only the sessions `macro_core.observation` does not hold, and with
    `overwrite` also redoes the ones an earlier backfill stored: a session
    stored any other way is the figure a past gate or grade was measured
    against, and is never replaced. Every write is an upsert on `(series_id, asof)`, and a gap
    on a session it fills is cleared the same way the daily run clears one.
    With `--dry-run` it fetches and computes, prints the rows as JSON on stdout
    and connects to nothing.
    """
    until = clock.last_completed_us_session(now)

    async def load() -> int:
        async with Client() as client:
            fetched = await breadth_history.fetch_history(client, now)
        LOG.info(
            "breadth backfill: %d of %d lines read, %d refused",
            len(fetched.closes),
            len(fetched.constituents),
            len(fetched.faults),
        )
        for fault in fetched.faults:
            LOG.warning("breadth backfill: %s", fault)
        sessions, skipped = breadth_history.compute_history(
            fetched.closes,
            {one.symbol: one.description for one in fetched.constituents},
            since=since,
            until=until,
        )
        for one in skipped:
            LOG.warning(
                "breadth backfill: %s skipped, %d companies with 200 closes, fewer than %d",
                one.asof,
                one.counted,
                breadth.MIN_CONSTITUENTS,
            )
        rows = breadth_history.history_observations(sessions, fetched.listed_on)
        if dry_run:
            json.dump(
                [
                    {"asof": one.asof, "value": one.value, **one.extra}
                    for one in rows
                ],
                sys.stdout,
                indent=1,
                default=str,
            )
            sys.stdout.write("\n")
            return 0

        registry = series_module.load(settings.series_yaml)
        store = await Store.connect(config.require_database(settings), ssl=settings.database_ssl)
        try:
            series_ids = await store.sync_series(registry)
            rows = backfill_rows(rows, await store.history(since=since), overwrite=overwrite)
            written = await store.write_observations(rows, series_ids, received_at=now)
            cleared = await store.clear_filled_gaps(rows, series_ids)
        finally:
            await store.aclose()
        LOG.info(
            "breadth backfill written: %d sessions upserted, %d gaps cleared, %s to %s",
            written,
            cleared,
            since.isoformat(),
            until.isoformat(),
        )
        return 0

    try:
        return asyncio.run(load())
    except config.Misconfigured as exc:
        LOG.error("%s", exc)
        return 2
    except WriteRefused as exc:
        LOG.error("%s", exc.sentence)
        return 1
    except FetchRefused as exc:
        LOG.error("breadth backfill refused: %s", exc)
        return 1


def backfill_rows(
    rows: Sequence[Observation], stored: Sequence[Any], *, overwrite: bool
) -> list[Observation]:
    """The backfilled breadth sessions to write, given what is stored.

    A session stored with a value is kept. With `overwrite`, a session the
    backfill itself stored is redone; one stored by any other method never is:
    the live scanner's figure is over the list the index held that day, and a
    journal S5TH row is what a past gate or grade was read against.
    """
    held = {
        one.asof: one
        for one in stored
        if one.series_key == breadth.SERIES_KEY and one.value is not None
    }
    return [
        one
        for one in rows
        if one.asof not in held or (overwrite and held[one.asof].extra.get("backfill") is True)
    ]


def _etf_backfill(
    settings: config.Settings,
    *,
    now: datetime,
    dry_run: bool,
    overwrite: bool,
) -> int:
    """Load the ETF flow history SoSoValue serves, checked against Farside. Re-runnable.

    The same fetch and the same derivation as the scheduled source, over every
    session rather than the replay window. Fills only the readings
    `macro_core.observation` does not hold with a value, unless `overwrite`:
    until 2026-09-24 the weekly flow and the streak were a search agent's and
    the journal's, and a stored figure is what a past week was read against. A
    stored row with a null value (the journal wrote some streaks as prose) is
    not a reading and is filled. Every write is an upsert on `(series_id, asof)`,
    and a gap on a session it fills is cleared. With `--dry-run` it fetches and
    computes, prints the rows as JSON on stdout and connects to nothing.
    """

    async def load() -> int:
        async with Client() as client:
            fetched = await etf_flows.fetch_all(client, now)
        for fault in fetched.faults:
            LOG.warning("etf backfill: %s", fault)
        rows = etf_flows.normalize(fetched.batches, now)
        for finding in etf_flows.review(rows):
            LOG.warning("etf backfill: %s", finding.sentence)
        for key in etf_flows.EtfFlows.series:
            mine = [one for one in rows if one.series_key == key]
            LOG.info(
                "etf backfill: %-22s %3d sessions, %3d at Tier 1%s",
                key,
                len(mine),
                sum(1 for one in mine if one.tier == 1),
                f", {mine[0].asof} to {mine[-1].asof}" if mine else "",
            )
        if dry_run:
            json.dump(
                [
                    {
                        "series": one.series_key,
                        "asof": one.asof,
                        "value": one.value,
                        "display": one.display,
                        "tier": one.tier,
                    }
                    for one in rows
                ],
                sys.stdout,
                indent=1,
                default=str,
            )
            sys.stdout.write("\n")
            return 0
        if not rows:
            LOG.error("etf backfill: neither source carried a session, nothing written")
            return 1

        registry = series_module.load(settings.series_yaml)
        store = await Store.connect(config.require_database(settings), ssl=settings.database_ssl)
        try:
            series_ids = await store.sync_series(registry)
            await store.write_batches(fetched.batches)
            if not overwrite:
                keys = set(etf_flows.EtfFlows.series)
                stored = {
                    (one.series_key, one.asof)
                    for one in await store.history(since=min(one.asof for one in rows))
                    if one.series_key in keys and one.value is not None
                }
                rows = [one for one in rows if (one.series_key, one.asof) not in stored]
            written = await store.write_observations(rows, series_ids, received_at=now)
            cleared = await store.clear_filled_gaps(rows, series_ids)
        finally:
            await store.aclose()
        LOG.info(
            "etf backfill written: %d readings upserted, %d gaps cleared", written, cleared
        )
        return 0

    try:
        return asyncio.run(load())
    except config.Misconfigured as exc:
        LOG.error("%s", exc)
        return 2
    except WriteRefused as exc:
        LOG.error("%s", exc.sentence)
        return 1
    except FetchRefused as exc:
        LOG.error("etf backfill refused: %s", exc)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(run())
