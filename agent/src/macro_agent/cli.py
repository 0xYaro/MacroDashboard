"""The command line both modes are driven through.

Every subcommand is either a **read** that prints JSON on stdout, or a **write**
that takes JSON on `--in` and prints what it did. Nothing in between, and
nothing that does both, so that the run described in `SKILL.md` is a sequence of
steps each of which can be inspected before the next one is taken.

The model never writes to the database. It produces JSON; a command validates it
and writes what survives. That is the whole seam, and it is why the refusals in
`validate.py` are sentences rather than exceptions: they go back to the model,
which is asked to fix them.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from . import authoring, narrative, voice
from . import context as context_module
from . import grade as grade_module
from .config import Misconfigured, Settings, load, require_database
from .model import Refused
from .preflight import Holding, Tape, phase_read, walk
from .preflight import report as preflight_report
from .store import Store, WriteRefused
from .validate import attach_reads, phase_reads, scenario_grades
from .validate import proposal as decode_proposal
from .worker_bridge import evaluator
from .worker_bridge import registry as load_registry

LOG = logging.getLogger("macro_agent")


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _emit(payload: Any) -> None:
    json.dump(payload, sys.stdout, indent=2, default=_json_default, ensure_ascii=False)
    sys.stdout.write("\n")


def _read(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


async def _store(settings: Settings) -> Store:
    return await Store.connect(require_database(settings), ssl=settings.database_ssl)


def _asof(raw: str | None) -> date:
    return date.today() if raw is None else date.fromisoformat(raw)


async def _evaluation_asof(
    store: Store, raw: str | None, *, on_or_before: date | None = None
) -> date:
    """The date a condition is scored at: `--asof` when given, else the worker's own date.

    The worker evaluates at the newest session the equity tape carries, not at
    the calendar date (`worker/src/macro/main.py` `_evaluation_date`), and the
    evaluator reads a value dated exactly that day. Defaulting to today would
    score a Monday-morning read against a US session that has not happened,
    and call every condition over a daily series unevaluable.
    """
    if raw is not None:
        return date.fromisoformat(raw)
    found = await store.evaluation_date(on_or_before=on_or_before)
    return found or (on_or_before or date.today())


def _week_end(week: str) -> date:
    """The Sunday that closes an ISO week, `2026-W39` -> 2026-09-27."""
    year, number = week.split("-W")
    return date.fromisocalendar(int(year), int(number), 7)


def _lookback(asof: date) -> date:
    """How far back the evaluator has to see to resolve a predicate window.

    The longest window any predicate in `config/predicates.md` takes is ten
    sessions, and the target bands are scored on one day. `HISTORY_DAYS` is the
    worker's own figure and is reused so the two runs read the same tape.
    """
    return date.fromordinal(asof.toordinal() - context_module.HISTORY_DAYS)


# ---------------------------------------------------------------------------
# Mode A
# ---------------------------------------------------------------------------


async def narrative_context_cmd(args: argparse.Namespace, settings: Settings) -> int:
    store = await _store(settings)
    try:
        keys = narrative.agent_series(settings)
        ctx = await context_module.narrative_context(
            store,
            keys=keys,
            asof=_asof(args.asof),
            prev_week=args.prev_week or "",
            prev_synthesis=args.prev_expectation,
        )
    finally:
        await store.aclose()
    ctx["dispatch"] = narrative.dispatch(ctx)
    _emit(ctx)
    return 0


def narrative_retry_cmd(args: argparse.Namespace, settings: Settings) -> int:
    """The Retry-stage prompt for one category's Tier 3+ points, or nothing."""
    points = _read(args.input)
    rows = points["points"] if isinstance(points, dict) else points
    prompt = narrative.retry_prompt(
        args.category, narrative.flagged(rows), asof=args.asof or ""
    )
    _emit({"category": args.category, "prompt": prompt, "flagged": narrative.flagged(rows)})
    return 0


def narrative_merge_cmd(args: argparse.Namespace, settings: Settings) -> int:
    first = _read(args.input)
    recovered = _read(args.recovered)
    merged, notes = narrative.merge_retry(
        first["points"] if isinstance(first, dict) else first,
        recovered["points"] if isinstance(recovered, dict) else recovered,
    )
    _emit({"points": merged, "notes": notes})
    return 0


async def narrative_write_cmd(args: argparse.Namespace, settings: Settings) -> int:
    payload = _read(args.input)
    now = datetime.now(UTC)
    store = await _store(settings)
    try:
        result = await narrative.apply(
            store,
            settings,
            returns={
                key: (value["points"] if isinstance(value, dict) else value)
                for key, value in (payload.get("categories") or {}).items()
            },
            event_rows=(payload.get("events") or {}).get("events", payload.get("events") or []),
            asof=_asof(args.asof),
            now=now,
        )
    finally:
        await store.aclose()
    _emit(result)
    return 0 if not result["refusals"] else 1


# ---------------------------------------------------------------------------
# Mode B
# ---------------------------------------------------------------------------


async def author_phase_context_cmd(args: argparse.Namespace, settings: Settings) -> int:
    """The blind context for the fresh phase read. Step one on Monday."""
    store = await _store(settings)
    try:
        ctx = await context_module.phase_context(
            store, settings, week=args.week, now=datetime.now(UTC)
        )
    except Refused as exc:
        _emit({"refusals": [str(exc)]})
        return 1
    finally:
        await store.aclose()
    _emit(ctx)
    return 0


async def author_classify_cmd(args: argparse.Namespace, settings: Settings) -> int:
    """Write the fresh reads. Last week's call is not shown here; `author context` shows it."""
    payload = _read(args.input)
    now = datetime.now(UTC)
    try:
        reads = phase_reads(payload, classified_at=now)
    except Refused as exc:
        _emit({"refusals": [str(exc)]})
        return 1
    if reads and reads[0].week != args.week:
        _emit({"refusals": [f"the reads are for {reads[0].week}, not {args.week}"]})
        return 1
    store = await _store(settings)
    try:
        asof = await _evaluation_asof(store, args.asof)
        done = await authoring.classify(
            store, settings, reads, week=args.week, asof=asof, now=now
        )
        # Scored before the reveal, so a condition that is already failing can
        # still be fixed by writing the read again.
        holding = await _holding_today(store, settings, done, asof=asof)
    except Refused as exc:
        _emit({"refusals": [str(exc)]})
        return 1
    finally:
        await store.aclose()
    short: list[str] = []
    for one in done.reads:
        held = holding.get(one.asset)
        if held is None or (not held.failed and not held.unevaluable):
            continue
        line = (
            f"{one.asset}: {held.met} of {len(one.conditions)} conditions hold on "
            f"{asof.isoformat()}."
        )
        if held.failed:
            line += (
                f" {held.failed} failed. A condition that is not true on the day of the read is "
                "not evidence for it; fix it and run classify again before `author context`, "
                "which is the last point a read may be rewritten."
            )
        if held.unevaluable:
            line += (
                f" {held.unevaluable} could not be evaluated on that day (no reading, or a "
                "manual condition with no assertion). That is not a failure; check the series "
                "names if it was not expected."
            )
        short.append(line)
    _emit(
        {
            "week": done.week,
            "status": "proposed",
            "evaluated_at": asof.isoformat(),
            "phase_call_ids": done.phase_call_ids,
            "replaced_unrevealed_reads": done.replaced,
            "warnings": [one.sentence for one in done.checks if not one.ok] + short,
            # Warn-only, and not findings: advice to the writer, fixed by
            # running classify again before the reveal.
            "voice": [one.sentence for one in done.voice if not one.ok],
            # Deliberately not the prior. Last week's call is shown by `author
            # context`, and that moment is stamped on these rows as the reveal;
            # until then a read may still be rewritten and is still fresh.
            "reads": [
                {"asset": one.asset, "phase": one.phase, "phase_name": one.phase_name,
                 "stage": one.stage}
                for one in done.reads
            ],
            "next": (
                "The reads are written. Fix any warning above by running classify again; once "
                "`author context` has run, last week's call has been shown and the reads are "
                "fixed. Then write one change_note per asset in proposal.json's phase_calls: what "
                "changed since last week, in one sentence. A move of two or more steps names both "
                "phases and says why."
            ),
        }
    )
    return 0


async def author_context_cmd(args: argparse.Namespace, settings: Settings) -> int:
    """The full context, served only once the week's fresh reads are written.

    Serving it is the reveal: it carries last week's calls, so the moment it is
    printed is stamped on the week's reads as `revealed_at`. An amendment run
    passes `--amend` and skips both, because it never touches a phase.
    """
    now = datetime.now(UTC)
    store = await _store(settings)
    try:
        reads = await store.fresh_reads(args.week)
        missing = sorted({"equities", "crypto"} - {one["asset"] for one in reads})
        if args.amend:
            # A week carried in by the migration has an active call and no
            # fresh read, and never will: no author run is owed for it. That
            # call is the week's, so there is nothing left to keep blind.
            missing = sorted(set(missing) - await store.standing_assets(args.week))
        if args.amend and missing:
            # An amendment context carries the standing calls. Served for a
            # week whose phase has not been read yet, it would show a Monday
            # run last week's call before its fresh read, with no reveal stamped.
            _emit(
                {
                    "refusals": [
                        f"{args.week} has no phase read for {', '.join(missing)} yet, so there "
                        "is no week to amend: the week's judgment is written by Monday's author "
                        "run, fresh read first. Amend once that proposal exists; until then, "
                        "say so on the NO_CHANGE line."
                    ]
                }
            )
            return 1
        if not args.amend:
            if missing:
                _emit(
                    {
                        "refusals": [
                            f"{args.week} has no fresh phase read for {', '.join(missing)}. The "
                            "phase is classified before last week's call is shown: run `author "
                            "phase-context` and `author classify` first. This context carries "
                            "last week's call, so reading it first would make the read not fresh."
                        ]
                    }
                )
                return 1
            await store.reveal_reads(args.week, now)
        ctx = await context_module.authoring_context(
            store,
            settings,
            week=args.week,
            graded_week=args.graded_week,
            now=now,
        )
    finally:
        await store.aclose()
    _emit(ctx)
    return 0


async def author_grade_cmd(args: argparse.Namespace, settings: Settings) -> int:
    payload = _read(args.input)
    grades, bad = scenario_grades(payload.get("scenario_grades") or [])
    if bad:
        _emit({"refusals": [one.sentence for one in bad]})
        return 1

    now = datetime.now(UTC)
    store = await _store(settings)
    try:
        # The last session of the graded week, unless one is given: the bands
        # are scored on the week's close, not on the Monday the grade is run.
        asof = await _evaluation_asof(
            store, args.asof, on_or_before=_week_end(args.graded_week)
        )
        stored = await store.scenario_set_for_week(args.graded_week)
        if stored is None:
            _emit(
                {
                    "refusals": [
                        f"no scenario set is stored for {args.graded_week}, so there is nothing "
                        "to grade. A migrated week whose set was never imported is graded by "
                        "hand into macro_core.week_grade with a null base_scenario_id."
                    ]
                }
            )
            return 1
        row, findings = grade_module.close_the_week(
            settings=settings,
            week=args.graded_week,
            stored_set=stored,
            grades=grades,
            history_rows=await store.history_rows(since=_lookback(asof)),
            open_gaps=await store.open_gaps(since=_lookback(asof)),
            asof=asof,
            eq_call=str(payload.get("eq_call") or ""),
            crypto_call=str(payload.get("crypto_call") or ""),
            key_miss=str(payload.get("key_miss") or ""),
            graded_at=now,
        )
        await store.write_week_grade(row)

        # The ledger row has to exist before a rule can cite the week: the
        # teaching-week foreign key is real, so a rule cannot name a week
        # nobody graded.
        added: list[str] = []
        for number in payload.get("rule_weeks") or []:
            await store.add_rule_week(int(number), args.graded_week)
            added.append(f"R{number} now cites {args.graded_week}")
        for new_rule in payload.get("new_rules") or []:
            text = str(new_rule["text"])
            # A retried grade pass sends the same new rule again. It is the
            # same rule: cite the week on it rather than number it twice.
            existing = await store.rule_number_for_text(text)
            number = int(new_rule.get("number") or existing or await store.next_rule_number())
            await store.add_rule(number, text, new_rule.get("weeks") or [args.graded_week])
            added.append(f"R{number} {'already stands' if existing else 'added'}")

        calibration = grade_module.calibration_read(settings, await store.ledger())
    finally:
        await store.aclose()

    _emit(
        {
            "week": row.week,
            "base_scenario_label": row.base_scenario_label,
            "base_grade": row.base_grade,
            "targets_hit": row.targets_hit,
            "targets_n": row.targets_n,
            "brier": None if row.brier is None else str(row.brier),
            "premise_moved": row.premise_moved,
            "findings": findings,
            # Warn-only: whether the grading prose reads in `config/voice.md`'s voice.
            "voice": [
                one.sentence
                for one in voice.grade_checks(
                    grades,
                    eq_call=str(payload.get("eq_call") or ""),
                    crypto_call=str(payload.get("crypto_call") or ""),
                    key_miss=str(payload.get("key_miss") or ""),
                )
                if not one.ok
            ],
            "ledger_changes": added,
            "calibration": calibration,
        }
    )
    return 0


async def author_propose_cmd(args: argparse.Namespace, settings: Settings) -> int:
    payload = _read(args.input)
    now = datetime.now(UTC)
    try:
        found = decode_proposal(payload, authored_at=now)
    except Refused as exc:
        _emit({"refusals": [str(exc)]})
        return 1

    store = await _store(settings)
    try:
        try:
            asof = await _evaluation_asof(store, args.asof)
            found = await _with_reads(store, found, unrevealed="refuse", now=now)
            tape = await _tape(store, settings, found, asof=asof)
            written = await authoring.propose(
                store, settings, found, asof=asof, tape=tape, now=now
            )
        except Refused as exc:
            _emit({"refusals": [str(exc)]})
            return 1
    finally:
        await store.aclose()

    _emit(
        {
            "week": written.week,
            "status": "proposed",
            "scenario_set_id": written.scenario_set_id,
            "phase_call_ids": written.phase_call_ids,
            "strategy_ids": written.strategy_ids,
            "setup_ids": written.setup_ids,
            "evaluated_at": asof.isoformat(),
            "replaced_earlier_proposal": written.replaced,
            "warnings": [one.sentence for one in written.checks if not one.ok],
            "amendments": [one.sentence for one in authoring.amendment_checks(found)],
            "preflight": [
                {"key": one.key, "marker": one.marker, "sentence": one.sentence}
                for one in written.preflight
            ],
            "next": (
                "Nothing was activated. The operator accepts, which is what moves these rows "
                "from proposed to active."
            ),
        }
    )
    return 0


async def preflight_cmd(args: argparse.Namespace, settings: Settings) -> int:
    payload = _read(args.input)
    try:
        found = decode_proposal(payload)
    except Refused as exc:
        _emit({"refusals": [str(exc)]})
        return 1
    if args.phase:
        # Offline, the reads come from the file `author classify` was given, and
        # last week's phase is unknown, so no transition is checked.
        try:
            found = attach_reads(found, phase_reads(_read(args.phase)))
        except Refused as exc:
            _emit({"refusals": [str(exc)]})
            return 1
    if settings.database_url:
        store = await _store(settings)
        try:
            if not args.phase:
                try:
                    # The preflight prints last week's phase beside each read,
                    # so running it is a reveal and is stamped as one.
                    found = await _with_reads(
                        store, found, unrevealed="stamp", now=datetime.now(UTC)
                    )
                except Refused as exc:
                    _emit({"refusals": [str(exc)]})
                    return 1
            tape = await _tape(
                store, settings, found, asof=await _evaluation_asof(store, args.asof)
            )
        finally:
            await store.aclose()
    else:
        # Preflighting a draft offline is worth supporting: R2, R3 and R10 are
        # the only rules that read the tape, and each of them says in its own
        # sentence when it could not be checked rather than passing quietly.
        tape = Tape()
    checks = walk(found, tape) + phase_read(found, tape) + voice.proposal_checks(found)
    if args.text:
        sys.stdout.write(preflight_report(checks) + "\n")
    else:
        _emit(
            [
                {"key": one.key, "marker": one.marker, "ok": one.ok, "sentence": one.sentence}
                for one in checks
            ]
        )
    return 0 if all(one.ok or one.severity != "refuse" for one in checks) else 1


async def accept_cmd(args: argparse.Namespace, settings: Settings) -> int:
    """The operator's move. Supersede the outgoing row, then activate the incoming one.

    Not called by either run. It is here because the two statements have to
    share one transaction in one fixed order, and that is code, not a note in a
    runbook.
    """
    store = await _store(settings)
    try:
        outgoing = args.supersede
        if outgoing is None and args.subject:
            subject = dict(pair.split("=", 1) for pair in args.subject)
            outgoing = await store.active_row_id(args.table, **subject)
        await store.activate(
            args.table,
            incoming_id=args.id,
            outgoing_id=outgoing,
            activated_at=datetime.now(UTC),
        )
    finally:
        await store.aclose()
    _emit({"table": args.table, "activated": args.id, "superseded": outgoing})
    return 0


async def runner_tick_cmd(args: argparse.Namespace, settings: Settings) -> int:
    """One pass of the run queue. launchd calls this every two minutes."""
    from . import runner

    store = await _store(settings)
    try:
        report = await runner.tick(store)
    finally:
        await store.aclose()
    _emit(report)
    return 0


def _amends_anything(found: Any) -> bool:
    return bool(
        found.scenario_set.amends
        or any(one.amends for one in found.strategies)
        or any(one.amends for one in found.setups)
    )


async def _with_reads(
    store: Store, found: Any, *, unrevealed: str, now: datetime
) -> Any:
    """The proposal with the week's fresh reads attached, or a refusal saying why not.

    A weekly proposal needs the reads `author classify` wrote, each with the
    change note the proposal carries. An amendment carries no phase, and may
    not: the phase is read once a week.

    The attached reads carry last week's phase, so whatever prints them shows
    the prior. `unrevealed="refuse"` (`author propose`) refuses reads whose
    reveal was never stamped: a change note written without having seen last
    week's call is a guess. `unrevealed="stamp"` (`preflight`) stamps the reveal
    itself, because the preflight is about to print the prior.
    """
    reads = tuple(await store.proposed_reads(found.week))
    if _amends_anything(found):
        if found.phase_notes:
            raise Refused(
                "an amendment carries no phase. The phase is read fresh once a week, on Monday; "
                "a mid-week read taken after seeing the standing call would not be fresh."
            )
        return found
    if not reads:
        accepted = await store.fresh_reads(found.week)
        if accepted:
            raise Refused(
                f"{found.week}'s phase reads are already accepted, so this is not the week's "
                "first proposal. An amendment names the row it amends."
            )
        raise Refused(
            f"{found.week} has no fresh phase read. Run `author phase-context` and `author "
            "classify` before anything else on Monday."
        )
    hidden = {
        one["asset"]
        for one in await store.fresh_reads(found.week)
        if one["status"] == "proposed" and one["revealed_at"] is None
    }
    if hidden and unrevealed == "refuse":
        raise Refused(
            f"{found.week}'s reads for {', '.join(sorted(hidden))} have not been shown last "
            "week's call yet. Run `author context` first: it is the reveal, and the change notes "
            "this proposal carries are sentences about what changed since a call the run has to "
            "have seen."
        )
    if hidden:
        await store.reveal_reads(found.week, now)
    return attach_reads(found, reads)


async def _tape(store: Store, settings: Settings, found: Any, *, asof: date) -> Tape:
    """The few readings the preflight needs, out of the snapshot and the history."""
    from decimal import Decimal

    snapshot = {one["key"]: one for one in await store.snapshot()}

    def value(key: str) -> Decimal | None:
        row = snapshot.get(key)
        raw = None if row is None else row.get("value")
        return None if raw is None else Decimal(str(raw))

    eight_weeks = asof.toordinal() - 56
    brent = [
        one["value"]
        for one in await store.history_rows(since=date.fromordinal(eight_weeks))
        if one["key"] == "brent" and one["value"] is not None
    ]
    return Tape(
        index_close=value("spx_cash"),
        brent_8w_lo=min(brent) if brent else None,
        brent_8w_hi=max(brent) if brent else None,
        btc_streak=value("btc_etf_inflow_streak"),
        eth_streak=value("eth_etf_inflow_streak"),
        holding=await _holding_today(store, settings, found, asof=asof),
    )


async def _holding_today(
    store: Store, settings: Settings, found: Any, *, asof: date
) -> dict[str, Holding]:
    """How each read's four conditions stand on `asof`: met, failed, unevaluable.

    Computed, never taken from the proposal: a count the authoring run
    supplied would be a number checking itself.

    A `manual` condition counts as met or failed only on its own asserted
    status, which is the same rule the worker applies: a manual condition with
    no assertion is unevaluable and is not a third kind of met. Unevaluable is
    counted apart from failed, because a series with no reading on the day
    says nothing against the read.
    """
    expr_module, _predicates, observations = evaluator(settings)
    registry = load_registry(settings)
    rows = await store.history_rows(since=_lookback(asof))
    if not rows:
        return {}
    history = observations.History(
        observations.readings_from(rows), gaps=await store.open_gaps(since=_lookback(asof))
    )
    ctx = expr_module.Context(asof=asof, history=history, registry=registry)

    out: dict[str, Holding] = {}
    calls: tuple[Any, ...] = tuple(getattr(found, "phase_calls", ()) or getattr(found, "reads", ()))
    for call in calls:
        met = failed = unknown = 0
        for condition in call.conditions:
            if condition.kind == "manual":
                status = condition.asserted_status or ""
                met += status == "met"
                failed += status == "failed"
                unknown += status not in ("met", "failed")
                continue
            try:
                outcome = expr_module.evaluate(condition.expr, ctx)
            except expr_module.ExpressionRefused:
                unknown += 1
                continue
            if outcome.is_met:
                met += 1
            elif outcome.is_unevaluable:
                unknown += 1
            else:
                failed += 1
        out[call.asset] = Holding(met=met, failed=failed, unevaluable=unknown, asof=asof)
    return out


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="macro-agent",
        description=(
            "The judgment run. `narrative` fills the agent_0930 series and the event log "
            "after the 09:30 run; `author` grades the prior week and writes the new "
            "judgment as proposed. `desk` is the OpenClaw feeds' seam."
        ),
    )
    root.add_argument("--log-level", default=None)
    subs = root.add_subparsers(dest="command", required=True)

    narrative_parser = subs.add_parser("narrative", help="mode A: the 09:30 narrative half")
    narrative_subs = narrative_parser.add_subparsers(dest="step", required=True)

    one = narrative_subs.add_parser("context", help="the four prompts, already rendered")
    one.add_argument("--asof", default=None, help="the observation date, ISO")
    one.add_argument("--prev-week", default=None)
    one.add_argument("--prev-expectation", default=None)
    one.set_defaults(fn=narrative_context_cmd, is_async=True)

    two = narrative_subs.add_parser("retry", help="the retry prompt for one category")
    two.add_argument("--category", required=True)
    two.add_argument("--in", dest="input", required=True)
    two.add_argument("--asof", default=None)
    two.set_defaults(fn=narrative_retry_cmd, is_async=False)

    three = narrative_subs.add_parser("merge", help="keep a recovered point only if it is better")
    three.add_argument("--in", dest="input", required=True)
    three.add_argument("--recovered", required=True)
    three.set_defaults(fn=narrative_merge_cmd, is_async=False)

    four = narrative_subs.add_parser("write", help="validate and write what came back")
    four.add_argument("--in", dest="input", required=True)
    four.add_argument("--asof", default=None)
    four.set_defaults(fn=narrative_write_cmd, is_async=True)

    author = subs.add_parser("author", help="mode B: Monday's grade and author pass")
    author_subs = author.add_subparsers(dest="step", required=True)

    blind = author_subs.add_parser(
        "phase-context", help="step one: the blind context for the fresh phase read"
    )
    blind.add_argument("--week", required=True)
    blind.set_defaults(fn=author_phase_context_cmd, is_async=True)

    classify = author_subs.add_parser(
        "classify", help="step two: write the fresh reads, then reveal last week's call"
    )
    classify.add_argument("--week", required=True)
    classify.add_argument("--in", dest="input", required=True)
    classify.add_argument("--asof", default=None)
    classify.set_defaults(fn=author_classify_cmd, is_async=True)

    ctx = author_subs.add_parser(
        "context", help="everything the judgment reads; refused until the reads are written"
    )
    ctx.add_argument("--week", required=True)
    ctx.add_argument("--graded-week", required=True)
    ctx.add_argument(
        "--amend", action="store_true", help="an amendment run, which never touches a phase"
    )
    ctx.set_defaults(fn=author_context_cmd, is_async=True)

    grading = author_subs.add_parser("grade", help="close the prior week")
    grading.add_argument("--graded-week", required=True)
    grading.add_argument("--in", dest="input", required=True)
    grading.add_argument(
        "--asof",
        default=None,
        help="the last session of the graded week; defaults to the newest the tape carries in it",
    )
    grading.set_defaults(fn=author_grade_cmd, is_async=True)

    prop = author_subs.add_parser("propose", help="write the new judgment as proposed")
    prop.add_argument("--in", dest="input", required=True)
    prop.add_argument("--asof", default=None)
    prop.set_defaults(fn=author_propose_cmd, is_async=True)

    pre = subs.add_parser("preflight", help="walk R1 to R12 against a proposal")
    pre.add_argument("--in", dest="input", required=True)
    pre.add_argument("--asof", default=None)
    pre.add_argument("--text", action="store_true", help="one line per rule instead of JSON")
    pre.add_argument("--phase", default=None, help="the reads file, when preflighting offline")
    pre.set_defaults(fn=preflight_cmd, is_async=True)

    accept = subs.add_parser(
        "accept",
        help="the OPERATOR's move: supersede the outgoing row and activate the incoming one",
    )
    accept.add_argument("--table", required=True, choices=sorted(authoring_tables()))
    accept.add_argument("--id", required=True)
    accept.add_argument("--supersede", default=None)
    accept.add_argument("--subject", nargs="*", default=None, help="e.g. asset=equities")
    accept.set_defaults(fn=accept_cmd, is_async=True)

    run_parser = subs.add_parser(
        "runner", help="the run queue: scheduled narrative and author runs, and Refresh"
    )
    run_subs = run_parser.add_subparsers(dest="step", required=True)
    tick = run_subs.add_parser("tick", help="apply one decision or run one queued request")
    tick.set_defaults(fn=runner_tick_cmd, is_async=True)

    # The desk is the OpenClaw feed's seam, and it must never be able to take the
    # authoring commands down with it: a broken desk module would otherwise fail
    # every `author` step of a Monday run at argument parsing. On a failure the
    # desk is registered as a stub that answers with the failure, and every other
    # command parses as usual.
    try:
        from . import desk_cli

        desk_cli.register(subs)
    except Exception as exc:  # any import or registration failure
        stub = subs.add_parser("desk", help="unavailable: the desk module failed to load")
        stub.add_argument("rest", nargs=argparse.REMAINDER)
        stub.set_defaults(fn=_desk_unavailable(exc), is_async=False)

    return root


def _desk_unavailable(exc: Exception) -> Callable[..., int]:
    """The stub `desk` command: one JSON object and exit 3, as a desk failure owes."""

    def run_stub(*_args: Any, **_kwargs: Any) -> int:
        print(
            json.dumps(
                {
                    "ok": False,
                    "applied": [],
                    "refusals": ["The desk is unavailable: its module failed to load."],
                    "error": {"category": "desk_unavailable", "exit": 3},
                }
            )
        )
        print(f"desk module failed to load: {type(exc).__name__}", file=sys.stderr)
        return 3

    return run_stub


def authoring_tables() -> frozenset[str]:
    from .store import JUDGMENT_TABLES

    return JUDGMENT_TABLES


def run() -> None:
    try:
        args = parser().parse_args()
    except SystemExit as exc:
        # argparse has printed its usage line to stderr. A desk command still owes
        # the plugin one JSON object on stdout.
        if exc.code not in (0, None) and "desk" in sys.argv[1:4]:
            from . import desk_cli

            raise SystemExit(desk_cli.fail(desk_cli.BadArgument("usage"))) from exc
        raise
    try:
        # The same arrangement as the worker: dotenv is loaded here, at process
        # start, and nothing inside the package opens a file to find a `.env`.
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:  # pragma: no cover - dotenv is a declared dependency
        pass
    try:
        settings = load()
    except Misconfigured as exc:
        if args.command == "desk":
            # The desk's contract: one JSON object on stdout, whatever happened.
            from . import desk_cli

            raise SystemExit(desk_cli.fail(exc)) from exc
        sys.stderr.write(f"{exc}\n")
        raise SystemExit(2) from exc
    logging.basicConfig(
        level=getattr(logging, args.log_level or settings.log_level, logging.INFO),
        stream=sys.stderr,
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
    )
    for noisy in ("asyncpg",):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    try:
        code = asyncio.run(args.fn(args, settings)) if args.is_async else args.fn(args, settings)
    except (Misconfigured, WriteRefused) as exc:
        sentence = exc.sentence if isinstance(exc, WriteRefused) else str(exc)
        sys.stderr.write(f"{sentence}\n")
        raise SystemExit(1) from exc
    raise SystemExit(code)


def main() -> None:  # pragma: no cover - console entry point alias
    run()
