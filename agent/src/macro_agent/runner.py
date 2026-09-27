"""The runner: every Claude run this system makes goes through one queue.

`docs/DECISIONS.md` 2026-09-23 puts three runs on the operator's Mac and one
button on the Desk:

- **narrative**, daily after the 09:30 worker run: the search agents for the
  prose rows and the numbers no scraper serves yet.
- **author**, Monday after the narrative: grade the week just closed and propose
  the next one. It opens with the fresh phase read, before anything that names
  last week's call.
- **amend**, whenever the operator presses Refresh: re-read the active call
  against what has moved, and propose an amendment or say why none is due.

All three are rows in `macro_core.run_request`. The runner's own schedule
inserts the first two; the web's `macro_mart.request_refresh` inserts the third.
One `tick` of the runner, every two minutes under launchd, does at most one of:
apply a decision the operator recorded, or claim and run one queued request.
It never runs two Claude sessions at once, so a Monday author cannot race the
narrative whose output it reads.

**The review gate does not move.** An author or amend run stops at `proposed`,
exactly as `SKILL.md` says. The operator's Accept on the Desk records a decision
on the request; this runner then applies it with the same `Store.activate` that
`macro-agent accept` calls, in the order that command uses. The web never
activates anything, and neither does the run that wrote the proposal.

The pure half (the schedule, the activation plan, reading the run's last line)
is separate from the half that touches the database and the subprocess, so the
decisions it makes can be tested without either.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import subprocess
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from .store import Store, WriteRefused, activate_on

LOG = logging.getLogger(__name__)

SINGAPORE = ZoneInfo("Asia/Singapore")

Kind = Literal["narrative", "author", "amend"]

#: The narrative waits for the 09:30 worker run, which takes about three minutes
#: and lands the feeds it reads: the desk jobs write at 08:40, 09:05 and 09:20
#: (the old OpenClaw crons at 08:45 and 09:15, for the worker to parse at 09:30).
NARRATIVE_AFTER = time(9, 40)
#: The Monday author waits for the narrative, whose observations it reads.
AUTHOR_AFTER = time(10, 0)
#: A scheduled run that failed is tried once more, this long after it ended, so
#: a transient fault (a dropped connection, a rate limit) costs one retry and not
#: the day. Two failures in a day is a finding for the operator, not a loop.
RETRY_AFTER = timedelta(minutes=15)
ATTEMPTS_PER_DAY = 2
#: The days the week's author may run: Monday, and Tuesday as the fallback when
#: Monday's narrative or author never landed. A week whose phase was never read
#: is a week missing from the record, which is worse than a read a day late.
AUTHOR_DAYS = (0, 1)
#: A run that has not finished in this long is killed and recorded as failed, so
#: a hung session cannot hold the queue for the rest of the day.
RUN_TIMEOUT = timedelta(minutes=50)
#: A request left `running` longer than this by a runner that died is failed on
#: the next tick, which is what frees the in-flight unique index for a retry.
STALE_CLAIM = timedelta(minutes=70)

REPO_ROOT = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# The pure half
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Recent:
    """What the schedule needs to know about one earlier request."""

    kind: str
    origin: str
    requested_at: datetime
    status: str
    finished_at: datetime | None = None


def iso_week(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def scheduled_due(
    now: datetime, recent: Sequence[Recent], *, full_run_today: bool
) -> list[Kind]:
    """Which scheduled runs are owed and not yet requested.

    The narrative is owed once per Singapore weekday, after 09:40 and after the
    worker's 09:30 full run has landed; without that run the narrative would
    write beside a snapshot from yesterday.

    The author is owed once per ISO week, on Monday after 10:00, and only once
    the day's narrative is done, so the judgment is written against this
    morning's prose rows rather than Friday's. If Monday never produces one,
    because the narrative or the author failed twice, it is owed again on
    Tuesday. On Tuesday it does not wait for a narrative that has failed twice:
    a week read against Monday's prose is better than a week not read at all.

    A failed scheduled run is retried once, `RETRY_AFTER` after it ended, and
    then left: a failure is a finding for the operator, not a loop. A run the
    operator rejected is a decision, not a failure, and is never re-run.
    """
    local = now.astimezone(SINGAPORE)
    today = local.date()
    if local.weekday() >= 5:
        return []

    def made(kind: str, *, since: date) -> list[Recent]:
        return [
            one
            for one in recent
            if one.kind == kind
            and one.origin == "schedule"
            and one.requested_at.astimezone(SINGAPORE).date() >= since
        ]

    def owed_again(runs: list[Recent]) -> bool:
        """No run yet today, or exactly one that failed long enough ago."""
        if not runs:
            return True
        if len(runs) >= ATTEMPTS_PER_DAY or any(one.status != "failed" for one in runs):
            return False
        ended = runs[-1].finished_at or runs[-1].requested_at
        return now - ended >= RETRY_AFTER

    due: list[Kind] = []
    narratives = sorted(made("narrative", since=today), key=lambda one: one.requested_at)
    if local.time() >= NARRATIVE_AFTER and full_run_today and owed_again(narratives):
        due.append("narrative")

    monday = today - timedelta(days=local.weekday())
    week = sorted(made("author", since=monday), key=lambda one: one.requested_at)
    settled = any(one.status != "failed" for one in week)
    narrative_done = any(one.status == "done" for one in narratives)
    narrative_gone = (
        len(narratives) >= ATTEMPTS_PER_DAY and all(one.status == "failed" for one in narratives)
    )
    ready = narrative_done or (local.weekday() > 0 and narrative_gone)
    today_runs = [one for one in week if one.requested_at.astimezone(SINGAPORE).date() == today]
    if (
        local.weekday() in AUTHOR_DAYS
        and local.time() >= AUTHOR_AFTER
        and ready
        and not settled
        and owed_again(today_runs)
    ):
        due.append("author")
    return due


@dataclass(frozen=True, slots=True)
class ProposedRow:
    """One judgment row the run wrote as `proposed`."""

    table: str
    id: str
    week: str
    asset: str | None
    name: str | None
    amends: str | None


@dataclass(frozen=True, slots=True)
class ActiveRow:
    """One judgment row currently `active`."""

    table: str
    id: str
    week: str
    asset: str | None
    name: str | None


@dataclass(frozen=True, slots=True)
class Activation:
    """`macro-agent accept --table T --id ID --supersede OLD`, as data."""

    table: str
    id: str
    supersede: str | None

    def as_json(self) -> dict[str, Any]:
        return {"table": self.table, "id": self.id, "supersede": self.supersede}


@dataclass(frozen=True, slots=True)
class Plan:
    activations: tuple[Activation, ...]
    #: Active rows of an older week that nothing replaced. A weekly author
    #: retires them, because a W39 setup still `active` in W40 is a setup the
    #: operator never re-affirmed. An amendment retires nothing it did not name.
    retire: tuple[str, ...] = field(default_factory=tuple)
    retire_table: dict[str, str] = field(default_factory=dict)


#: The order `accept` must run in: the set before the phase calls and setups
#: that are read against it, and the strategy last.
_ORDER = {"scenario_set": 0, "phase_call": 1, "setup": 2, "strategy": 3}


def plan_activation(
    kind: str, proposed: Sequence[ProposedRow], active: Sequence[ActiveRow]
) -> Plan:
    """Which active row each proposed row supersedes, per table's natural subject.

    An amendment names its row in `amends`, and that is the only row it
    supersedes. Otherwise the subject is the one each partial unique index in
    `macro_core` enforces: a phase call per asset, a strategy per asset, the
    scenario set, and a setup by asset and name (a setup carried into a new
    week under the same name replaces its earlier self).
    """
    taken: set[str] = set()
    activations: list[Activation] = []
    for row in sorted(proposed, key=lambda one: (_ORDER.get(one.table, 9), one.id)):
        outgoing: str | None = row.amends
        if outgoing is None:
            candidates = [
                one
                for one in active
                if one.table == row.table and one.id not in taken and _same_subject(row, one)
            ]
            candidates.sort(key=lambda one: one.week, reverse=True)
            outgoing = candidates[0].id if candidates else None
        if outgoing is not None:
            taken.add(outgoing)
        activations.append(Activation(table=row.table, id=row.id, supersede=outgoing))

    retire: list[str] = []
    retire_table: dict[str, str] = {}
    if kind == "author" and proposed:
        new_week = max(one.week for one in proposed)
        for one in active:
            if one.id in taken or one.week >= new_week:
                continue
            if one.table in ("setup", "strategy", "phase_call", "scenario_set"):
                retire.append(one.id)
                retire_table[one.id] = one.table
    return Plan(activations=tuple(activations), retire=tuple(retire), retire_table=retire_table)


def _same_subject(row: ProposedRow, one: ActiveRow) -> bool:
    if row.table in ("phase_call", "strategy"):
        return row.asset == one.asset
    if row.table == "scenario_set":
        return True
    if row.table == "setup":
        return row.asset == one.asset and row.name == one.name
    return False


@dataclass(frozen=True, slots=True)
class Outcome:
    summary: str | None
    no_change: bool


def read_outcome(stdout: str) -> Outcome:
    """The run's own last word, from the `SUMMARY:` or `NO_CHANGE:` line it prints last."""
    for line in reversed(stdout.strip().splitlines()):
        stripped = line.strip()
        if stripped.startswith("NO_CHANGE:"):
            return Outcome(summary=stripped.removeprefix("NO_CHANGE:").strip(), no_change=True)
        if stripped.startswith("SUMMARY:"):
            return Outcome(summary=stripped.removeprefix("SUMMARY:").strip(), no_change=False)
    tail = stdout.strip()[-600:]
    return Outcome(summary=tail or None, no_change=False)


def prompt_for(kind: str, *, reason: str | None, now: datetime) -> str:
    """The instruction a headless Claude session is started with."""
    local = now.astimezone(SINGAPORE).date()
    week = iso_week(local)
    prior = iso_week(local - timedelta(days=7))
    common = (
        "You are running unattended from the MacroDashboard repository root, started by "
        "the runner in agent/src/macro_agent/runner.py. Read agent/SKILL.md first and follow "
        "it exactly; it is the runbook and it overrides anything below that conflicts with "
        "it. Never run `macro-agent accept`: the operator decides on the Desk. Never print "
        "DATABASE_URL or any secret. When you are done, print exactly one final line that "
        "starts with `SUMMARY:` and says in one or two sentences what you wrote"
    )
    if kind == "narrative":
        return (
            f"{common}.\n\nRun Mode A, `narrative`, for today ({local.isoformat()} Singapore), "
            "all four categories, with the retry stage, then write. A prose row's as-of is "
            f"the day you confirmed it, {local.isoformat()}, never the date of the event it "
            "describes: the Fed's decision day is in the sentence, not in the as-of, or the "
            "row reads overdue for ever."
        )
    if kind == "author":
        return (
            f"{common}.\n\nRun Mode B, `author`, for {week}, grading {prior}. Begin with B1, "
            "the fresh phase read: run `author phase-context` and `author classify` before you "
            "open anything else that could name last week's phase (the Desk, the database, the "
            "journals, the fixtures under agent/tests, or `author context`). Stop at B6 with "
            "everything written as proposed and the preflight printed."
        )
    ask = (reason or "no reason given").replace("\n", " ")
    return (
        f"{common}, or instead a final line starting with `NO_CHANGE:` if you wrote "
        "nothing.\n\n"
        "The operator pressed Refresh on the Desk and asked for a refreshed read of the "
        f"active call. Their words: \"{ask}\".\n"
        "Follow the `## Amending` section of agent/SKILL.md. Start from "
        f"`uv run --project agent macro-agent author context --amend --week {week} "
        f"--graded-week {prior}` and from macro_mart.drift, "
        "which lists every condition, band and gate that has changed state since the call "
        "and marks the material ones. A probability may be re-priced only on a material "
        "change as docs/DECISIONS.md defines it. Where nothing material has moved you may "
        "still amend a strategy's positioning or a setup's levels if the tape has made "
        "them stale, each with an amend_reason that says so. An amendment never carries a "
        "phase call: the phase is read fresh once a week, on Monday. If nothing warrants an "
        "amendment, write nothing and say why on the NO_CHANGE line."
    )


# ---------------------------------------------------------------------------
# The half that touches the database
# ---------------------------------------------------------------------------


_JUDGMENT = ("phase_call", "scenario_set", "setup", "strategy")


def _columns(table: str) -> str:
    asset = "asset" if table in ("phase_call", "setup", "strategy") else "null::text"
    name = "name" if table == "setup" else "null::text"
    return f"id::text as id, week, {asset} as asset, {name} as name"


async def recent_requests(store: Store, *, since: datetime) -> list[Recent]:
    async with store.transaction() as connection:
        rows = await connection.fetch(
            "select kind, origin, requested_at, status, finished_at from macro_core.run_request "
            "where requested_at >= $1",
            since,
        )
    return [
        Recent(
            kind=one["kind"],
            origin=one["origin"],
            requested_at=one["requested_at"],
            status=one["status"],
            finished_at=one["finished_at"],
        )
        for one in rows
    ]


async def full_run_since(store: Store, since: datetime) -> bool:
    async with store.transaction() as connection:
        found = await connection.fetchval(
            "select exists (select 1 from macro_quality.ingest_run "
            "where run_kind = 'full' and started_at >= $1 and finished_at is not null)",
            since,
        )
    return bool(found)


async def enqueue(store: Store, kind: Kind, *, reason: str | None = None) -> int | None:
    """Insert a scheduled request; `None` when one of that kind is already in flight."""
    async with store.transaction() as connection:
        made = await connection.fetchval(
            "insert into macro_core.run_request (kind, reason, origin) values ($1, $2, 'schedule') "
            "on conflict do nothing returning id",
            kind,
            reason,
        )
    return None if made is None else int(made)


async def fail_stale_claims(store: Store, now: datetime) -> int:
    """Fail every request a dead runner left `running`, and clear what it proposed.

    The same as any other failed run: its proposed rows are superseded, so
    the retry's `author classify` is not refused for reads the dead run had
    already been shown last week's call for.
    """
    async with store.transaction() as connection:
        rows = await connection.fetch(
            "update macro_core.run_request set status = 'failed', finished_at = $1, "
            "error = 'the runner stopped before this run finished' "
            "where status = 'running' and claimed_at < $2 returning kind, claimed_at",
            now,
            now - STALE_CLAIM,
        )
    for one in rows:
        if one["kind"] != "narrative":
            await discard_proposed(store, one["claimed_at"])
    return len(rows)


@dataclass(frozen=True, slots=True)
class Claimed:
    id: int
    kind: Kind
    reason: str | None
    claimed_at: datetime


async def claim(store: Store, now: datetime) -> Claimed | None:
    """Take the oldest queued request, skipping any another runner already holds."""
    async with store.transaction() as connection:
        row = await connection.fetchrow(
            "update macro_core.run_request set status = 'running', claimed_at = $1 "
            "where id = (select id from macro_core.run_request where status = 'queued' "
            "order by requested_at limit 1 for update skip locked) "
            "returning id, kind, reason, claimed_at",
            now,
        )
    if row is None:
        return None
    return Claimed(
        id=row["id"], kind=row["kind"], reason=row["reason"], claimed_at=row["claimed_at"]
    )


async def proposed_since(store: Store, since: datetime) -> list[ProposedRow]:
    out: list[ProposedRow] = []
    async with store.transaction() as connection:
        for table in _JUDGMENT:
            rows = await connection.fetch(
                f"select {_columns(table)}, amends::text as amends from macro_core.{table} "
                "where status = 'proposed' and authored_at >= $1",
                since,
            )
            out += [
                ProposedRow(
                    table=table,
                    id=one["id"],
                    week=one["week"],
                    asset=one["asset"],
                    name=one["name"],
                    amends=one["amends"],
                )
                for one in rows
            ]
    return out


async def active_rows(store: Store) -> list[ActiveRow]:
    out: list[ActiveRow] = []
    async with store.transaction() as connection:
        for table in _JUDGMENT:
            rows = await connection.fetch(
                f"select {_columns(table)} from macro_core.{table} where status = 'active'"
            )
            out += [
                ActiveRow(
                    table=table,
                    id=one["id"],
                    week=one["week"],
                    asset=one["asset"],
                    name=one["name"],
                )
                for one in rows
            ]
    return out


async def discard_proposed(store: Store, since: datetime) -> int:
    """Retire every row a failed run left `proposed`.

    A run that failed half way leaves a proposal nobody can review whole, and
    its phase reads may already have been shown last week's call, which would
    stop the retry from writing fresh ones. Superseding them clears the way and
    keeps them, so a failed run's partial work is still findable.
    """
    total = 0
    async with store.transaction() as connection:
        for table in _JUDGMENT:
            status = await connection.execute(
                f"update macro_core.{table} set status = 'superseded' "
                "where status = 'proposed' and authored_at >= $1",
                since,
            )
            total += int(status.split()[-1]) if status else 0
    return total


async def finish(
    store: Store,
    request_id: int,
    *,
    status: str,
    now: datetime,
    summary: str | None = None,
    error: str | None = None,
    proposed: Sequence[ProposedRow] = (),
) -> None:
    payload = [
        {"table": one.table, "id": one.id, "week": one.week, "asset": one.asset, "name": one.name}
        for one in proposed
    ]
    async with store.transaction() as connection:
        await connection.execute(
            "update macro_core.run_request set status = $2, finished_at = $3, summary = $4, "
            "error = $5, proposed = $6::jsonb where id = $1",
            request_id,
            status,
            now,
            summary,
            error,
            json.dumps(payload),
        )


@dataclass(frozen=True, slots=True)
class Decided:
    id: int
    kind: str
    decision: str
    proposed: tuple[ProposedRow, ...]


async def decided(store: Store) -> list[Decided]:
    async with store.transaction() as connection:
        rows = await connection.fetch(
            "select id, kind, decision, proposed from macro_core.run_request "
            "where status = 'proposed' and decision is not null order by decided_at"
        )
    out: list[Decided] = []
    for one in rows:
        raw = one["proposed"]
        items = json.loads(raw) if isinstance(raw, str) else (raw or [])
        out.append(
            Decided(
                id=one["id"],
                kind=one["kind"],
                decision=one["decision"],
                proposed=tuple(
                    ProposedRow(
                        table=item["table"],
                        id=item["id"],
                        week=item["week"],
                        asset=item.get("asset"),
                        name=item.get("name"),
                        amends=None,
                    )
                    for item in items
                ),
            )
        )
    return out


async def apply_decision(store: Store, found: Decided, now: datetime) -> str:
    """Accept or reject every row one run proposed, and close the request."""
    ids = [one.id for one in found.proposed]
    if found.decision == "reject":
        async with store.transaction() as connection:
            for table in _JUDGMENT:
                await connection.execute(
                    f"update macro_core.{table} set status = 'superseded' "
                    "where status = 'proposed' and id::text = any($1::text[])",
                    ids,
                )
            await connection.execute(
                "update macro_core.run_request set status = 'rejected', finished_at = $2 "
                "where id = $1",
                found.id,
                now,
            )
        return "rejected"

    # The amends pointer is re-read rather than carried: it is the database's
    # own statement of which row an amendment replaces.
    fresh = await proposed_by_id(store, ids)
    doubled = _doubled_subjects(fresh)
    if doubled:
        raise WriteRefused(
            "this proposal carries two proposed rows for "
            + ", ".join(doubled)
            + ", and only one row per subject can be active, so nothing was accepted and last "
            "week's rows still stand. Reject it on the Desk and let the run propose the week once."
        )
    plan = plan_activation(found.kind, fresh, await active_rows(store))
    # One transaction for the whole decision: every activation, every
    # retirement and the request's own status commit together or not at all,
    # so a refusal half way leaves last week's judgment standing whole.
    async with store.transaction() as connection:
        for one in plan.activations:
            await activate_on(
                connection,
                one.table,
                incoming_id=one.id,
                outgoing_id=one.supersede,
                activated_at=now,
            )
        for row_id in plan.retire:
            table = plan.retire_table[row_id]
            await connection.execute(
                f"update macro_core.{table} set status = 'superseded' where id::text = $1 "
                "and status = 'active'",
                row_id,
            )
        await connection.execute(
            "update macro_core.run_request set status = 'accepted', finished_at = $2 "
            "where id = $1",
            found.id,
            now,
        )
    return "accepted"


def _doubled_subjects(rows: Sequence[ProposedRow]) -> list[str]:
    """Subjects with two non-amendment proposed rows, which no Accept can stand up together."""
    seen: dict[tuple[str, str | None, str | None], int] = {}
    for one in rows:
        if one.amends is not None:
            continue
        key = (
            one.table,
            one.asset if one.table in ("phase_call", "strategy", "setup") else None,
            one.name if one.table == "setup" else None,
        )
        seen[key] = seen.get(key, 0) + 1
    return [
        " ".join(part for part in (table.replace("_", " "), asset, name) if part)
        for (table, asset, name), count in sorted(
            seen.items(), key=lambda item: tuple(part or "" for part in item[0])
        )
        if count > 1
    ]


async def proposed_by_id(store: Store, ids: Sequence[str]) -> list[ProposedRow]:
    out: list[ProposedRow] = []
    async with store.transaction() as connection:
        for table in _JUDGMENT:
            rows = await connection.fetch(
                f"select {_columns(table)}, amends::text as amends from macro_core.{table} "
                "where status = 'proposed' and id::text = any($1::text[])",
                list(ids),
            )
            out += [
                ProposedRow(
                    table=table,
                    id=one["id"],
                    week=one["week"],
                    asset=one["asset"],
                    name=one["name"],
                    amends=one["amends"],
                )
                for one in rows
            ]
    return out


# ---------------------------------------------------------------------------
# The subprocesses
# ---------------------------------------------------------------------------


#: The model every run is written by. Pinned so the calibration record keeps one
#: author: a CLI default that moves would change who writes the call mid-record
#: (docs/DECISIONS.md 2026-09-27). `MACRO_CLAUDE_MODEL` overrides it.
DEFAULT_MODEL = "claude-opus-5-5"


def claude_model() -> str:
    return os.environ.get("MACRO_CLAUDE_MODEL", "").strip() or DEFAULT_MODEL


def claude_command(prompt: str, *, session_id: str) -> list[str]:
    """The headless session. `MACRO_CLAUDE` overrides the binary, for tests.

    `session_id` is chosen here and stored on the request before the session
    starts, so the transcript of any run, including one that hangs or is killed,
    is `claude --resume <id>` from the repository root.
    """
    binary = os.environ.get("MACRO_CLAUDE", "claude")
    return [
        *shlex.split(binary),
        "-p",
        prompt,
        "--model",
        claude_model(),
        "--session-id",
        session_id,
        "--dangerously-skip-permissions",
        "--output-format",
        "text",
    ]


# ---------------------------------------------------------------------------
# What a run did to the repository
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RepoState:
    """`git status --porcelain` and the content hash of every path it lists."""

    porcelain: str
    blobs: dict[str, str]


def _git(root: Path, *args: str, stdin: str | None = None) -> str:
    done = subprocess.run(
        ["git", *args], cwd=root, input=stdin, capture_output=True, text=True, check=False
    )
    return done.stdout if done.returncode == 0 else ""


def repo_state(root: Path) -> RepoState:
    """The dirty and untracked-but-not-ignored paths, each with its content hash.

    Hashing the content, not only listing the paths, is what catches a run that
    edits a file which was already dirty before it started: the porcelain line
    is the same before and after, the blob is not. A deleted path hashes to "".
    """
    porcelain = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    paths = [line[3:].split(" -> ")[-1].strip('"') for line in porcelain.splitlines() if line]
    present = [one for one in paths if (root / one).is_file()]
    hashes = _git(root, "hash-object", "--stdin-paths", stdin="\n".join(present)).split()
    blobs = dict.fromkeys(paths, "")
    blobs.update(zip(present, hashes, strict=False))
    return RepoState(porcelain=porcelain, blobs=blobs)


def changed_paths(before: RepoState, after: RepoState) -> list[str]:
    """Paths whose content differs between the two states, sorted.

    A path clean in one state and dirty in the other changed (it appears in
    only one `blobs`); a path dirty in both changed only if its hash moved.
    """
    keys = set(before.blobs) | set(after.blobs)
    return sorted(one for one in keys if before.blobs.get(one) != after.blobs.get(one))


def repo_sentence(request_id: int, kind: str, paths: Sequence[str], session_id: str) -> str:
    shown = ", ".join(paths[:8]) + (f" and {len(paths) - 8} more" if len(paths) > 8 else "")
    noun = "file" if len(paths) == 1 else "files"
    return (
        f"Run {request_id} ({kind}) changed {len(paths)} repository {noun}: {shown}. "
        "Review them with `git diff` before the next commit; the session is "
        f"`claude --resume {session_id}` from the repository root."
    )


async def record_session(store: Store, request_id: int, session_id: str, before: RepoState) -> None:
    async with store.transaction() as connection:
        await connection.execute(
            "update macro_core.run_request set claude_session_id = $2::uuid, repo_before = $3 "
            "where id = $1",
            request_id,
            session_id,
            before.porcelain,
        )


async def record_repo(
    store: Store,
    request_id: int,
    kind: str,
    session_id: str,
    after: RepoState,
    changed: Sequence[str],
    now: datetime,
) -> None:
    """Store the after-state and, when the run changed anything, open a finding for it."""
    async with store.transaction() as connection:
        await connection.execute(
            "update macro_core.run_request set repo_after = $2, repo_changed = $3::text[] "
            "where id = $1",
            request_id,
            after.porcelain,
            list(changed),
        )
    if changed:
        await store.write_findings(
            [
                (
                    "run_changed_repo",
                    f"run {request_id}",
                    repo_sentence(request_id, kind, changed, session_id),
                    2,
                )
            ],
            now=now,
        )


async def run_process(argv: Sequence[str], *, timeout: timedelta) -> tuple[int, str]:
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=REPO_ROOT,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(process.communicate(), timeout.total_seconds())
    except TimeoutError:
        process.kill()
        await process.wait()
        return -1, f"killed after {int(timeout.total_seconds() // 60)} minutes"
    return process.returncode or 0, out.decode("utf-8", errors="replace")


def rescore_command() -> list[str]:
    binary = os.environ.get("MACRO_UV", "uv")
    return [*shlex.split(binary), "run", "--project", "worker", "macro-worker", "--rescore"]


# ---------------------------------------------------------------------------
# One tick
# ---------------------------------------------------------------------------


async def tick(store: Store, *, now: datetime | None = None) -> dict[str, Any]:
    """One pass: fail dead claims, apply one decision, schedule, then run at most one request."""
    at = now or datetime.now(UTC)
    report: dict[str, Any] = {"at": at.isoformat()}

    stale = await fail_stale_claims(store, at)
    if stale:
        report["failed_stale"] = stale

    pending = await decided(store)
    if pending:
        first = pending[0]
        try:
            report["decision"] = {"id": first.id, "result": await apply_decision(store, first, at)}
        except WriteRefused as exc:
            await finish(store, first.id, status="failed", now=at, error=exc.sentence)
            report["decision"] = {"id": first.id, "result": "failed", "error": exc.sentence}
            return report
        # The judgment just changed; the mart must say so before the operator looks.
        code, _ = await run_process(rescore_command(), timeout=timedelta(minutes=10))
        report["rescore_exit"] = code
        return report

    local_midnight = datetime.combine(
        at.astimezone(SINGAPORE).date(), time(0), tzinfo=SINGAPORE
    ).astimezone(UTC)
    due = scheduled_due(
        at,
        await recent_requests(store, since=at - timedelta(days=8)),
        full_run_today=await full_run_since(store, local_midnight),
    )
    for kind in due:
        made = await enqueue(store, kind)
        report.setdefault("scheduled", []).append({"kind": kind, "id": made})

    claimed = await claim(store, at)
    if claimed is None:
        return report
    report["claimed"] = {"id": claimed.id, "kind": claimed.kind}
    LOG.info("run %d (%s) claimed", claimed.id, claimed.kind)

    session_id = str(uuid.uuid4())
    before = repo_state(REPO_ROOT)
    await record_session(store, claimed.id, session_id, before)
    code, stdout = await run_process(
        claude_command(
            prompt_for(claimed.kind, reason=claimed.reason, now=at), session_id=session_id
        ),
        timeout=RUN_TIMEOUT,
    )
    ended = datetime.now(UTC) if now is None else at
    changed = changed_paths(before, after := repo_state(REPO_ROOT))
    await record_repo(store, claimed.id, claimed.kind, session_id, after, changed, ended)
    if changed:
        report["repo_changed"] = changed
    outcome = read_outcome(stdout)
    if code != 0:
        if claimed.kind != "narrative":
            report["discarded"] = await discard_proposed(store, claimed.claimed_at)
        await finish(
            store,
            claimed.id,
            status="failed",
            now=ended,
            error=f"claude exited {code}: {stdout.strip()[-500:]}",
        )
        report["result"] = "failed"
        return report

    if claimed.kind == "narrative":
        await finish(store, claimed.id, status="done", now=ended, summary=outcome.summary)
        report["result"] = "done"
        # The narrative writes macro_core.observation and nothing else; the mart
        # is the worker's. Without this the prose rows it just wrote would sit
        # unread behind yesterday's ratings until the 21:00 rescore.
        code, _ = await run_process(rescore_command(), timeout=timedelta(minutes=10))
        report["rescore_exit"] = code
        return report

    wrote = await proposed_since(store, claimed.claimed_at)
    if claimed.kind == "author" and wrote and not any(
        one.table == "scenario_set" for one in wrote
    ):
        # The phase reads went in and the rest of the week did not. Half a week
        # is not a proposal the operator can accept, so it counts as a failure
        # and is cleared for the retry.
        report["discarded"] = await discard_proposed(store, claimed.claimed_at)
        await finish(
            store,
            claimed.id,
            status="failed",
            now=ended,
            summary=outcome.summary,
            error="the run wrote its phase reads and stopped before proposing the rest of the week",
        )
        report["result"] = "failed"
        return report
    if wrote:
        await finish(
            store, claimed.id, status="proposed", now=ended, summary=outcome.summary, proposed=wrote
        )
        report["result"] = "proposed"
    elif outcome.no_change:
        await finish(store, claimed.id, status="done", now=ended, summary=outcome.summary)
        report["result"] = "no change"
    else:
        await finish(
            store,
            claimed.id,
            status="failed",
            now=ended,
            summary=outcome.summary,
            error="the run finished without proposing anything or saying why not",
        )
        report["result"] = "failed"
    return report
