"""`macro-agent desk ...`: the argument parsing and the one JSON object each command prints.

Kept beside `cli.py` rather than inside it so the tool contract (docs/DESK.md)
maps onto one small file. Every handler has `cli.py`'s shape, `(args,
settings) -> exit code`, so the same dispatch runs it.

Input is `--json-in PATH`, or `-` (the default) for stdin, which is how the
OpenClaw plugin passes it. Output is one JSON object on stdout, compact (no
indentation: the plugin's tool results are cut at 16,000 characters), even
when the command fails. The exit code says which kind of answer it is:

- 0: everything was accepted;
- 1: something was refused, and `refusals` says what to fix;
- 2: the environment cannot support a run (no `macro_desk` login, the database
  unreachable or refusing the login, the worker package missing);
- 3: the run failed after it started (a statement the database refused, a
  bug). Nothing from the submission was applied.

A failure's reply is `{"ok": false, "applied": [], "refusals": [sentence],
"error": {"category": ..., "exit": ...}}`. The sentence is one of a fixed set,
so it never carries a file path, a host name, a role name or a traceback; the
traceback goes to stderr only when LOG_LEVEL is DEBUG.
"""

from __future__ import annotations

import argparse
import functools
import json
import logging
import re
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import asyncpg

from . import desk, desk_compare
from .config import Misconfigured, Settings, require_database, require_desk_database
from .desk_store import DeskStore
from .store import WriteRefused
from .worker_bridge import WorkerUnavailable

LOG = logging.getLogger(__name__)

EXIT_REFUSED = 1
EXIT_ENVIRONMENT = 2
EXIT_FAILED = 3

Handler = Callable[[argparse.Namespace, Settings], Awaitable[int]]

#: The sentences a failure may say, by category. Nothing else reaches the reply.
FAILURES: dict[str, tuple[int, str]] = {
    "environment": (EXIT_ENVIRONMENT, "The desk is not configured to run here."),
    "worker_unavailable": (
        EXIT_ENVIRONMENT,
        "The worker package the desk takes its date and category rules from could not be "
        "loaded, so nothing was checked or written. The operator checks WORKER_SRC.",
    ),
    "database_unreachable": (
        EXIT_ENVIRONMENT,
        "The desk could not reach the database, so nothing was read or written. Try once "
        "more; if it fails again, report it.",
    ),
    "database_login_refused": (
        EXIT_ENVIRONMENT,
        "The database refused the desk's login, so nothing was read or written. The operator "
        "checks MACRO_DESK_DATABASE_URL.",
    ),
    "input_unreadable": (
        EXIT_ENVIRONMENT,
        "The submission could not be read from the file it was given, so nothing was checked "
        "or written.",
    ),
    "invalid_argument": (
        EXIT_ENVIRONMENT,
        "An argument to the desk command was not one it takes, or not in its form (a date is "
        "YYYY-MM-DD), so nothing was run.",
    ),
    "database_permission": (
        EXIT_FAILED,
        "The desk's database login may not make a read or write this command needs; a "
        "migration is probably not applied. Nothing from this call was applied.",
    ),
    "database_schema": (
        EXIT_FAILED,
        "A table, column or function this command needs is missing from the database; a "
        "migration is probably not applied. Nothing from this call was applied.",
    ),
    "database_constraint": (
        EXIT_FAILED,
        "The database refused a write that breaks one of its rules, so nothing from this "
        "call was applied. This is a desk bug to report, not something a resubmission fixes.",
    ),
    "database_busy": (
        EXIT_FAILED,
        "The database cancelled a statement that waited or ran too long, so nothing from "
        "this call was applied. Try once more.",
    ),
    "database_refused": (
        EXIT_FAILED,
        "The database refused a statement, so nothing from this call was applied. Report it.",
    ),
    "internal": (
        EXIT_FAILED,
        "The desk command failed on an internal error, so nothing from this call was "
        "applied. This is a bug to report, not something a resubmission fixes.",
    ),
}

_UNREACHABLE_STATES = frozenset({"57P01", "57P02", "57P03", "53300"})
_BUSY_STATES = frozenset({"57014", "55P03", "25P03", "40001", "40P01"})
_SCHEMA_STATES = frozenset({"42P01", "42883", "42703", "3F000", "42704"})
#: What a Misconfigured sentence may not carry into the reply.
_UNSAFE = re.compile(r"[a-z][a-z0-9+.-]*://\S+|(?<![\w.])/[\w.~-]+(?:/[\w.~-]+)+|\S+@\S+",
                     re.IGNORECASE)


class BadArgument(Exception):
    """An operator's argument (`--now`, `--since`) was not in the form it takes."""


def classify(exc: BaseException) -> tuple[str, str]:
    """A failure's category, and the sentence the reply carries."""
    if isinstance(exc, BadArgument):
        return "invalid_argument", FAILURES["invalid_argument"][1]
    if isinstance(exc, Misconfigured):
        said = _UNSAFE.sub("[withheld]", str(exc)).strip()[:400]
        return "environment", said or FAILURES["environment"][1]
    if isinstance(exc, WorkerUnavailable):
        return "worker_unavailable", FAILURES["worker_unavailable"][1]
    if isinstance(exc, (FileNotFoundError, IsADirectoryError, PermissionError)):
        return "input_unreadable", FAILURES["input_unreadable"][1]
    if isinstance(exc, WriteRefused):
        cause = exc.__cause__
        if isinstance(cause, asyncpg.InvalidAuthorizationSpecificationError):
            category = "database_login_refused"
        elif cause is None or isinstance(
            cause, (OSError, asyncpg.PostgresConnectionError, asyncpg.InterfaceError)
        ):
            category = "database_unreachable"
        elif isinstance(cause, asyncpg.PostgresError):
            state = str(getattr(cause, "sqlstate", "") or "")
            if state.startswith("08") or state in _UNREACHABLE_STATES:
                category = "database_unreachable"
            elif state == "42501":
                category = "database_permission"
            elif state in _SCHEMA_STATES:
                category = "database_schema"
            elif state.startswith("23"):
                category = "database_constraint"
            elif state in _BUSY_STATES:
                category = "database_busy"
            else:
                category = "database_refused"
        else:
            category = "database_refused"
        return category, FAILURES[category][1]
    if isinstance(exc, (OSError, asyncpg.PostgresConnectionError, asyncpg.InterfaceError)):
        return "database_unreachable", FAILURES["database_unreachable"][1]
    return "internal", FAILURES["internal"][1]


def fail(exc: BaseException) -> int:
    """Print the one JSON object a failed command answers with. Returns its exit code."""
    category, sentence = classify(exc)
    code = FAILURES[category][0]
    _emit({"ok": False, "applied": [], "refusals": [sentence],
           "error": {"category": category, "exit": code}})
    sys.stderr.write(f"desk: {category}: {sentence}\n")
    if LOG.isEnabledFor(logging.DEBUG):
        LOG.debug("desk command failed", exc_info=exc)
    return code


def _guarded(handler: Handler) -> Handler:
    """Run a command so that every failure is one JSON object and a distinct exit code."""

    @functools.wraps(handler)
    async def run(args: argparse.Namespace, settings: Settings) -> int:
        try:
            return await handler(args, settings)
        except Exception as exc:
            return fail(exc)

    return run


def _default(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return str(value)


def _emit(payload: Any) -> None:
    json.dump(payload, sys.stdout, separators=(",", ":"), default=_default, ensure_ascii=False)
    sys.stdout.write("\n")


def _payload(path: str) -> Any:
    try:
        text = (
            sys.stdin.buffer.read() if path == "-" else Path(path).read_bytes()
        ).decode("utf-8")
    except UnicodeDecodeError:
        return {"__unreadable__": "the input is not UTF-8 text, so it was not read. Nothing "
                                  "was applied."}
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        return {"__unreadable__": f"the input is not JSON ({exc.msg} at line {exc.lineno}, "
                                  f"column {exc.colno}). Nothing was applied."}


def _now(raw: str | None) -> datetime:
    if raw is None:
        return datetime.now(UTC)
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise BadArgument(raw) from exc
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _date(raw: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise BadArgument(raw) from exc


async def _store(settings: Settings, args: argparse.Namespace) -> DeskStore:
    fallback = bool(getattr(args, "use_database_url", False))
    url = require_desk_database(settings, allow_fallback=fallback)
    if fallback and not settings.desk_database_url:
        sys.stderr.write(
            "desk: --use-database-url: this run connects with DATABASE_URL, which can write "
            "far more than the three feeds.\n"
        )
    return cast(DeskStore, await DeskStore.connect(url, ssl=settings.database_ssl))


def _unreadable(payload: Any) -> int | None:
    if isinstance(payload, dict) and "__unreadable__" in payload:
        _emit({"ok": False, "refusals": [payload["__unreadable__"]], "applied": []})
        return EXIT_REFUSED
    return None


async def context_cmd(args: argparse.Namespace, settings: Settings) -> int:
    store = await _store(settings, args)
    try:
        _emit(await desk.context(store, settings, now=_now(args.now)))
    finally:
        await store.aclose()
    return 0


async def catalysts_list_cmd(args: argparse.Namespace, settings: Settings) -> int:
    store = await _store(settings, args)
    try:
        _emit(await desk.catalysts_list(
            store, status=args.status, within_days=args.within_days, search=args.search,
            full=args.full, now=_now(args.now),
        ))
    finally:
        await store.aclose()
    return 0


async def catalysts_submit_cmd(args: argparse.Namespace, settings: Settings) -> int:
    payload = _payload(args.input)
    if (code := _unreadable(payload)) is not None:
        return code
    store = await _store(settings, args)
    try:
        reply = await desk.catalysts_submit(
            store, settings, payload, now=_now(args.now), dry_run=args.dry_run,
            origin=args.origin,
        )
    finally:
        await store.aclose()
    _emit(reply.as_json())
    return 0 if not reply.refusals else EXIT_REFUSED


async def tail_risks_list_cmd(args: argparse.Namespace, settings: Settings) -> int:
    store = await _store(settings, args)
    try:
        _emit(await desk.tail_risks_list(store, include_retired=args.all))
    finally:
        await store.aclose()
    return 0


async def tail_risks_submit_cmd(args: argparse.Namespace, settings: Settings) -> int:
    payload = _payload(args.input)
    if (code := _unreadable(payload)) is not None:
        return code
    store = await _store(settings, args)
    try:
        reply = await desk.tail_risks_submit(
            store, settings, payload, now=_now(args.now), dry_run=args.dry_run,
            origin=args.origin,
        )
    finally:
        await store.aclose()
    _emit(reply.as_json())
    return 0 if not reply.refusals else EXIT_REFUSED


async def positions_cmd(args: argparse.Namespace, settings: Settings) -> int:
    now = _now(args.now)
    store = await _store(settings, args)
    try:
        found = await desk.positions(store, settings, now=now)
        batch_id = await store.archive("positions", "positions", found, at=now,
                                       shadow=args.dry_run)
    finally:
        await store.aclose()
    _emit(desk.public_positions(found, batch_id=batch_id))
    return 0


async def holdings_submit_cmd(args: argparse.Namespace, settings: Settings) -> int:
    payload = _payload(args.input)
    if (code := _unreadable(payload)) is not None:
        return code
    store = await _store(settings, args)
    try:
        reply = await desk.holdings_submit(
            store, settings, payload, now=_now(args.now), dry_run=args.dry_run,
            origin=args.origin,
        )
    finally:
        await store.aclose()
    _emit(reply.as_json())
    return 0 if not reply.refusals else EXIT_REFUSED


async def compare_cmd(args: argparse.Namespace, settings: Settings) -> int:
    # Reads the vault-only holdings tables, which the macro_desk role cannot, so this
    # operator command connects with DATABASE_URL. It writes nothing.
    now = _now(args.now)
    until = _date(args.until) if args.until else desk.singapore_day(now)
    since = _date(args.since)
    if since > until:
        raise Misconfigured(f"--since {since} is after --until {until}.")
    store = cast(desk_compare.CompareStore,
                 await desk_compare.CompareStore.connect(require_database(settings),
                                                         ssl=settings.database_ssl))
    try:
        inputs = await desk_compare.load(store, since)
    finally:
        await store.aclose()
    days = desk_compare.compare(inputs, since=since, until=until)
    if args.json:
        _emit({"since": since, "until": until, "days": desk_compare.as_json(days)})
    else:
        sys.stdout.write(desk_compare.render(days, since=since, until=until))
    return 0


def _writes(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--json-in", "--in", dest="input", default="-",
        help="the submission as JSON: a file path, or - for stdin (the default)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="validate and archive as a shadow batch; write nothing else",
    )
    parser.add_argument("--origin", default=desk.ORIGIN, help=argparse.SUPPRESS)


def _days(raw: str) -> int:
    value = int(raw)
    if not 0 <= value <= 3650:
        raise argparse.ArgumentTypeError("--within-days is a whole number from 0 to 3650")
    return value


def _clock(parser: argparse.ArgumentParser) -> None:
    # For replaying a day in tests and in the trial comparison; the tools never pass it.
    parser.add_argument("--now", default=None, help=argparse.SUPPRESS)


def register(subs: Any) -> None:
    """Add `desk` and its subcommands to the root parser's subparsers."""
    root = subs.add_parser(
        "desk",
        help="the OpenClaw macro-desk tools: context, catalysts, tail risks, positions, holdings",
    )
    root.add_argument(
        "--use-database-url", action="store_true",
        help="operator only: connect with DATABASE_URL when MACRO_DESK_DATABASE_URL is not "
        "set. The tools never pass it, so a model's call without the macro_desk login exits 2",
    )
    desk_subs = root.add_subparsers(dest="step", required=True)

    ctx = desk_subs.add_parser("context", help="phase, scenarios, key levels and drift")
    _clock(ctx)
    ctx.set_defaults(fn=_guarded(context_cmd), is_async=True)

    catalysts = desk_subs.add_parser("catalysts", help="the catalyst calendar")
    cat_subs = catalysts.add_subparsers(dest="action", required=True)
    cat_list = cat_subs.add_parser("list", help="the calendar with ids, as a compact table")
    cat_list.add_argument("--status", choices=("tracked", "retired", "all"), default="tracked")
    cat_list.add_argument(
        "--within-days", type=_days, default=None,
        help=f"catalysts starting within N days of today (default {desk.LIST_WITHIN_DAYS}, "
        "or every date with --search); undated ones and ones already started are always "
        "listed; 0 lists every date",
    )
    cat_list.add_argument("--search", default=None,
                          help="only names, or former names, containing this text, "
                          "ignoring case")
    cat_list.add_argument(
        "--full", action="store_true",
        help="every column: affected, former_names, date_text, source_url, external_key, "
        "family, first/last seen, feed_source",
    )
    _clock(cat_list)
    cat_list.set_defaults(fn=_guarded(catalysts_list_cmd), is_async=True)
    cat_submit = cat_subs.add_parser("submit", help="upsert, retire or rename catalysts")
    _writes(cat_submit)
    _clock(cat_submit)
    cat_submit.set_defaults(fn=_guarded(catalysts_submit_cmd), is_async=True)

    risks = desk_subs.add_parser("tail-risks", help="the tail risks")
    risk_subs = risks.add_subparsers(dest="action", required=True)
    risk_list = risk_subs.add_parser("list", help="the live risks with their recent checks")
    risk_list.add_argument("--all", action="store_true", help="include retired risks")
    risk_list.set_defaults(fn=_guarded(tail_risks_list_cmd), is_async=True)
    risk_submit = risk_subs.add_parser("submit", help="a weekly scan or a daily recheck")
    _writes(risk_submit)
    _clock(risk_submit)
    risk_submit.set_defaults(fn=_guarded(tail_risks_submit_cmd), is_async=True)

    pos = desk_subs.add_parser(
        "positions", help="open positions and watched names with a computed 24h change"
    )
    pos.add_argument(
        "--dry-run", action="store_true", help="mark the archived positions as a shadow batch"
    )
    _clock(pos)
    pos.set_defaults(fn=_guarded(positions_cmd), is_async=True)

    holdings = desk_subs.add_parser("holdings", help="the holdings watch")
    hold_subs = holdings.add_subparsers(dest="action", required=True)
    hold_submit = hold_subs.add_parser(
        "submit", help="the reason for each move and upcoming events; prices are computed"
    )
    _writes(hold_submit)
    _clock(hold_submit)
    hold_submit.set_defaults(fn=_guarded(holdings_submit_cmd), is_async=True)

    cmp = desk_subs.add_parser(
        "compare",
        help="the trial's shadow batches against the vault feeds, per Singapore day",
    )
    cmp.add_argument("--since", required=True, help="first Singapore day, YYYY-MM-DD")
    cmp.add_argument("--until", default=None, help="last Singapore day (default today)")
    cmp.add_argument("--json", action="store_true", help="print JSON instead of the report")
    _clock(cmp)
    cmp.set_defaults(fn=_guarded(compare_cmd), is_async=True)
