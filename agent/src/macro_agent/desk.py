"""`macro-agent desk`: the commands the OpenClaw `macro-desk` agent's tools call.

The plan of 2026-09-24 (docs/DECISIONS.md, "OpenClaw writes the three feeds
through the desk command"): the agent that watches catalysts, tail risks and
the operator's holdings stops writing Obsidian notes for the worker to parse
and calls typed tools instead. Each tool runs one command here, which holds the
`macro_desk` login, validates what the agent sent against the same rules the
vault parser applied, and writes what survives.

Every command prints one JSON object on stdout for the tool to hand back to
the agent. A submission that refused anything exits 1, and every refusal is a
sentence naming the item and what to fix, so the agent can resend it.

## What every submission does

1. Decode and validate the payload (`desk_validate.py`), then check each item
   against the stored rows: does the catalyst exist, is it retired, was it
   renamed, may a daily recheck touch this risk.
2. `--dry-run`: archive the payload and what would have been done in
   `macro_raw.batch` with `shadow = true`, and write nothing else. This is how
   the trial week runs beside the old crons.
3. Otherwise: open a `macro_quality.ingest_run` (kind `desk`, with its feed and
   origin), archive the payload, apply every surviving item in one
   transaction, open a finding per refusal, close the run.

Nothing here trusts a number the model sent. A tail risk's gauges are read
from the snapshot; a holding's price and 24h change are computed by
`prices.py`.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from . import prices, voice
from .config import Settings
from .desk_store import DeskStore, redact_accounts
from .desk_validate import (
    CatalystOp,
    CatalystRename,
    CatalystRetire,
    CatalystUpsert,
    HoldingEvent,
    HoldingItem,
    RiskRetire,
    RiskUpsert,
    catalyst_ops,
    holding_items,
    note,
    refuse,
    risk_ops,
    scrubbed,
    severity_rank,
)
from .model import Check
from .store import WriteRefused
from .worker_bridge import clock as bridge_clock
from .worker_bridge import families as bridge_families
from .worker_bridge import http as bridge_http
from .worker_bridge import vault as bridge_vault

LOG = logging.getLogger(__name__)

#: Who sends the feeds. The only value `macro_core.catalyst.feed_source` and
#: `macro_core.tail_risk.feed_source` accept besides `vault`.
ORIGIN = "openclaw:macro-desk"

NEW_YORK = ZoneInfo("America/New_York")

#: How old a `desk positions` archive may be for `holdings submit` to reuse it.
POSITIONS_REUSE = timedelta(hours=3)


# ---------------------------------------------------------------------------
# The reply every submission returns
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Reply:
    feed: str
    dry_run: bool
    applied: list[dict[str, Any]] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)
    voice: list[Check] = field(default_factory=list)
    run_id: int | None = None
    batch_id: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def refusals(self) -> list[str]:
        return [one.sentence for one in self.checks if not one.ok and one.severity == "refuse"]

    @property
    def warnings(self) -> list[str]:
        return [one.sentence for one in self.checks if not one.ok and one.severity != "refuse"]

    def as_json(self) -> dict[str, Any]:
        refusals = self.refusals
        return {
            "feed": self.feed,
            "ok": not refusals,
            "dry_run": self.dry_run,
            "run_id": self.run_id,
            "batch_id": self.batch_id,
            "applied": self.applied,
            "refusals": refusals,
            "warnings": self.warnings,
            "voice": [one.sentence for one in self.voice if not one.ok],
            **self.extra,
            "next": (
                (
                    "Dry run: nothing was written except the archived submission. "
                    if self.dry_run
                    else ""
                )
                + (
                    "Fix each refusal and resend only those items; the rest "
                    + ("would have been applied." if self.dry_run else "were applied.")
                    if refusals
                    else "Every item was accepted."
                )
            ),
        }


def _field(path: str, label: str, text: str | None) -> list[voice.Field]:
    return [voice.Field(path, label, text)] if text and text.strip() else []


def _voice(fields: list[voice.Field], scope: str) -> list[Check]:
    if not fields:
        return []
    return [one for one in voice.checks(fields, scope=scope) if not one.ok]


async def _finish(
    store: DeskStore,
    reply: Reply,
    *,
    run_id: int,
    rows: int,
    now: datetime,
    finding_key: str,
) -> None:
    await store.write_findings(
        [
            (finding_key, reply.feed, sentence, 2)
            for sentence in reply.refusals
        ],
        now=now,
    )
    await store.finish_run(
        run_id,
        finished_at=datetime.now(UTC),
        rows=rows,
        status="ok",
        error=(f"{len(reply.refusals)} item(s) refused" if reply.refusals else None),
    )


async def _failed(store: DeskStore, run_id: int, exc: Exception) -> None:
    try:
        await store.finish_run(
            run_id,
            finished_at=datetime.now(UTC),
            rows=0,
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
        )
    except WriteRefused:  # pragma: no cover - the run row is best effort once a write failed
        LOG.exception("could not close ingest_run %s", run_id)


def _iso(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def singapore_day(now: datetime) -> date:
    return now.astimezone(ZoneInfo("Asia/Singapore")).date()


# ---------------------------------------------------------------------------
# desk context
# ---------------------------------------------------------------------------


async def context(store: DeskStore, settings: Settings, *, now: datetime) -> dict[str, Any]:
    """This week's phase, scenarios, key levels and drift: what the journal used to supply."""
    clock = bridge_clock(settings)
    ny = now.astimezone(NEW_YORK)
    session_today = clock.is_us_session(ny.date())
    open_now = bool(
        session_today
        and time(9, 30) <= ny.time() < clock.session_close_time(ny.date())
    )
    sgt = now.astimezone(ZoneInfo("Asia/Singapore"))
    return {
        "now": now.isoformat(),
        "today": sgt.date().isoformat(),
        "weekday": sgt.strftime("%A"),
        "week": clock.iso_week(sgt.date()),
        "us_session": {
            "last_completed": clock.last_completed_us_session(now).isoformat(),
            "trading_today": session_today,
            "open_now": open_now,
        },
        "phase": await store.phases(),
        "scenarios": await store.scenarios(),
        "levels": await store.levels(),
        "drift": await store.drift(),
        "feeds": await store.feed_freshness(),
        "notes": [
            "levels are the dashboard's own readings; quote them, never a web page's, when you "
            "say what a tail risk has priced in",
            "change is each series' own cadence (change_label), not always one day",
        ],
    }


# ---------------------------------------------------------------------------
# Catalysts
# ---------------------------------------------------------------------------


#: `desk catalysts list` shows catalysts starting within this many days by default.
LIST_WITHIN_DAYS = 45

#: The columns of `desk catalysts list`, one array per catalyst in this order.
#: Compact by default: OpenClaw cuts a tool result at 16,000 characters, and the
#: calendar printed as one object per row was 62,000.
LIST_COLUMNS: tuple[str, ...] = (
    "id", "name", "category", "starts_at", "ends_at", "date_precision", "date_estimated",
)
#: What `--full` adds.
FULL_COLUMNS: tuple[str, ...] = (
    "affected", "former_names", "date_text", "source_url", "external_key", "family",
    "first_seen", "last_seen", "feed_source",
)


def _stamp(value: Any, precision: Any) -> str | None:
    """A catalyst's date as the list prints it: a day, or a UTC minute for an exact time."""
    if value is None:
        return None
    moment = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    moment = moment.astimezone(UTC)
    if precision == "exact":
        return moment.strftime("%Y-%m-%dT%H:%MZ")
    return moment.date().isoformat()


def _cell(row: dict[str, Any], column: str, aliases: list[str]) -> Any:
    if column in ("starts_at", "ends_at"):
        return _stamp(row[column], row["date_precision"])
    if column == "affected":
        return list(row["affected"] or ())
    if column == "former_names":
        return aliases
    return _iso(row[column])


def _listed(
    row: dict[str, Any], aliases: list[str], *, within_days: int, search: str | None,
    now: datetime,
) -> bool:
    if search and not any(
        search in one.casefold() for one in (str(row["name"]), *aliases)
    ):
        return False
    if within_days == 0 or row["starts_at"] is None:
        return True
    # Anything that has already started is listed too: a passed catalyst still
    # tracked is one to retire, and an estimated month may have begun already.
    return bool(row["starts_at"] <= now + timedelta(days=within_days))


async def catalysts_list(
    store: DeskStore,
    *,
    status: str,
    within_days: int | None = None,
    search: str | None = None,
    full: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The calendar as a compact table: `columns`, then one array per catalyst.

    `within_days` keeps catalysts starting within that many days of `now`
    (0 keeps every date); undated ones and ones already started are always
    kept. Left out, it is `LIST_WITHIN_DAYS`, or every date when there is a
    `search`, which keeps names, or former names, containing the text, ignoring
    case: a search is how the agent finds an event before adding it again. `full`
    adds the affected assets, former names, the source, the words the date came
    from, the key, the family and the provenance columns.
    """
    moment = now or datetime.now(UTC)
    if within_days is None:
        within_days = 0 if (search or "").strip() else LIST_WITHIN_DAYS
    rows = await store.catalysts()
    aliases = await store.catalyst_aliases()
    names: dict[str, list[str]] = {}
    for alias in aliases.values():
        names.setdefault(alias["catalyst_id"], []).append(alias["name"])
    wanted = (search or "").strip().casefold() or None
    in_status = [one for one in rows if status == "all" or one["status"] == status]
    chosen = [
        one for one in in_status
        if _listed(one, names.get(one["id"], []), within_days=within_days, search=wanted,
                   now=moment)
    ]
    columns = LIST_COLUMNS + (FULL_COLUMNS if full else ())
    if status != "tracked":
        columns += ("status", "retired_reason")
    return {
        "status": status,
        "within_days": within_days,
        "search": search,
        "count": len(chosen),
        "total": len(in_status),
        "columns": list(columns),
        "catalysts": [
            [_cell(one, column, names.get(one["id"], [])) for column in columns]
            for one in chosen
        ],
        "notes": [
            "each catalyst is one array in the order of `columns`; starts_at and ends_at are "
            "UTC, a day unless date_precision is exact",
            f"{len(in_status) - len(chosen)} more {status} catalyst(s) are outside "
            "within_days or search; widen them, or use full for sources and former names",
        ],
    }


def _resolve(
    name: str | None,
    catalyst_id: str | None,
    *,
    by_id: dict[str, dict[str, Any]],
    by_key: dict[str, dict[str, Any]],
    aliases: dict[str, dict[str, Any]],
    vault: Any,
) -> tuple[dict[str, Any] | None, str | None]:
    """The stored catalyst an op names, or why none could be found."""
    if catalyst_id is not None:
        row = by_id.get(catalyst_id)
        if row is None:
            return None, f"no catalyst has id {catalyst_id}"
        if name is not None and vault.external_key(name) not in (
            row["external_key"],
            *[k for k, a in aliases.items() if a["catalyst_id"] == catalyst_id],
        ):
            return None, f"id {catalyst_id} is {row['name']!r}, not {name!r}"
        return row, None
    assert name is not None
    key = vault.external_key(name)
    if key in by_key:
        return by_key[key], None
    if key in aliases and aliases[key]["catalyst_id"] in by_id:
        return by_id[aliases[key]["catalyst_id"]], None
    return None, (
        f"no catalyst is named {name!r}; `desk catalysts list` shows the calendar with ids"
    )


def _one_op_per_catalyst(
    ops: list[CatalystOp],
    *,
    by_id: dict[str, dict[str, Any]],
    by_key: dict[str, dict[str, Any]],
    aliases: dict[str, dict[str, Any]],
    vault: Any,
) -> tuple[list[CatalystOp], list[Check]]:
    """Refuse every item that touches a catalyst another item also touches.

    Each op is resolved to the stored catalyst it would change (by id, current
    name or former name) before comparing, because the same catalyst can be
    named three ways: a rename by id beside an upsert by its old name, or a
    rename by a former name beside an upsert by the current one, would
    otherwise both pass and the upsert would insert a second row. The one pair
    allowed is a rename and an upsert under its new name.
    """
    renames_to = {op.new_key: op for op in ops if isinstance(op, CatalystRename)}

    def stored(name: str | None, catalyst_id: str | None) -> dict[str, Any] | None:
        found, _ = _resolve(name, catalyst_id, by_id=by_id, by_key=by_key, aliases=aliases,
                            vault=vault)
        return found

    def target(op: CatalystOp) -> tuple[str, str]:
        if isinstance(op, CatalystUpsert):
            row = by_key.get(op.key)
            if row is None and op.key in aliases:
                row = by_id.get(aliases[op.key]["catalyst_id"])
            if row is None and op.key in renames_to:
                rename = renames_to[op.key]
                row = stored(rename.name, rename.catalyst_id)
            return ("id", row["id"]) if row is not None else ("new", op.key)
        row = stored(op.name, op.catalyst_id)
        if row is not None:
            return ("id", row["id"])
        return ("unknown", op.catalyst_id or vault.external_key(op.name or ""))

    groups: dict[tuple[str, str], list[CatalystOp]] = {}
    for op in ops:
        groups.setdefault(target(op), []).append(op)
    clash: dict[int, str] = {}
    for (kind, ident), members in groups.items():
        if len(members) < 2:
            continue
        renames = [one for one in members if isinstance(one, CatalystRename)]
        upserts = [one for one in members if isinstance(one, CatalystUpsert)]
        if (len(members) == 2 and len(renames) == 1 and len(upserts) == 1
                and upserts[0].key == renames[0].new_key):
            continue
        row = by_id.get(ident) if kind == "id" else None
        what = f"{row['name']!r} (id {row['id']})" if row else "the same catalyst"
        for one in members:
            others = ", ".join(f"items[{m.index}]" for m in members if m is not one)
            clash[one.index] = (
                f"items[{one.index}]: {others} in this submission also touch {what}, so none "
                "of them was applied. Send one item per catalyst; a rename may travel with one "
                "upsert under its new name."
            )
    checks = [refuse("desk_catalyst_duplicate", clash[index]) for index in sorted(clash)]
    return [op for op in ops if op.index not in clash], checks


async def catalysts_submit(
    store: DeskStore,
    settings: Settings,
    payload: Any,
    *,
    now: datetime,
    dry_run: bool,
    origin: str = ORIGIN,
) -> Reply:
    vault = bridge_vault(settings)
    families = bridge_families(settings)
    reply = Reply(feed="catalysts", dry_run=dry_run)
    ops, checks = catalyst_ops(payload, now=now, vault=vault, families=families)
    reply.checks.extend(checks)

    rows = await store.catalysts()
    aliases = await store.catalyst_aliases()
    by_id = {one["id"]: one for one in rows}
    by_key = {one["external_key"]: one for one in rows}
    today = singapore_day(now)

    renames: list[tuple[CatalystRename, dict[str, Any]]] = []
    upserts: list[tuple[CatalystUpsert, str, dict[str, Any] | None]] = []
    retires: list[tuple[CatalystRetire, dict[str, Any]]] = []
    renamed_to: dict[str, dict[str, Any]] = {}
    renamed_by: dict[str, CatalystRename] = {}

    ops, clashes = _one_op_per_catalyst(
        ops, by_id=by_id, by_key=by_key, aliases=aliases, vault=vault
    )
    reply.checks.extend(clashes)

    for op in ops:
        if isinstance(op, CatalystRename):
            target, problem = _resolve(op.name, op.catalyst_id, by_id=by_id, by_key=by_key,
                                       aliases=aliases, vault=vault)
            if target is None:
                reply.checks.append(refuse("desk_catalyst_rename",
                                           f"items[{op.index}] was not renamed: {problem}."))
                continue
            if op.new_key == target["external_key"]:
                reply.checks.append(refuse(
                    "desk_catalyst_rename",
                    f"items[{op.index}]: {target['name']!r} already has that name ignoring case "
                    "and spacing; nothing to rename."))
                continue
            other = by_key.get(op.new_key)
            if other is not None:
                reply.checks.append(refuse(
                    "desk_catalyst_rename",
                    f"items[{op.index}]: a catalyst named {other['name']!r} already exists "
                    f"(id {other['id']}). Two rows would share one identity; retire the one "
                    "that is wrong instead of renaming onto it."))
                continue
            first = renamed_by.get(op.new_key)
            if first is not None:
                reply.checks.append(refuse(
                    "desk_catalyst_rename",
                    f"items[{op.index}] was not renamed: items[{first.index}] already renames a "
                    f"catalyst to {op.new_name!r} in this submission, and two catalysts cannot "
                    "share one name. Send one rename per new name."))
                continue
            if op.new_key in aliases:
                owner = aliases[op.new_key]["catalyst_id"]
                whose = "this catalyst's own" if owner == target["id"] else "another catalyst's"
                reply.checks.append(refuse(
                    "desk_catalyst_rename",
                    f"items[{op.index}]: {op.new_name!r} is {whose} former name, and a former "
                    "name keeps pointing where it pointed. Pick a new name."))
                continue
            renames.append((op, target))
            renamed_to[op.new_key] = target
            renamed_by[op.new_key] = op
        elif isinstance(op, CatalystUpsert):
            alias = aliases.get(op.key)
            if alias is not None and op.key not in by_key and op.key not in renamed_to:
                current = by_id.get(alias["catalyst_id"])
                current_name = current["name"] if current else "a newer name"
                reply.checks.append(refuse(
                    "desk_catalyst_renamed",
                    f"items[{op.index}] ({op.name!r}) was not applied: that catalyst was renamed "
                    f"to {current_name!r}. Submit it under its current name."))
                continue
            existing = by_key.get(op.key) or renamed_to.get(op.key)
            if existing is not None and not op.category_given and existing["category"]:
                # A category the calendar already holds (set by hand, or by an earlier
                # item that named it) stays unless this item names another.
                op = replace(op, category=existing["category"])
            if existing is None:
                result = "inserted"
            elif existing["status"] == "retired":
                result = "revived"
                reply.checks.append(note(
                    "desk_catalyst_revived",
                    f"items[{op.index}] ({op.name!r}) was retired ({existing['retired_reason']}) "
                    "and is tracked again."))
            else:
                changed = _changed(existing, op)
                result = "updated" if changed else "unchanged"
            upserts.append((op, result, existing))
        elif isinstance(op, CatalystRetire):
            target, problem = _resolve(op.name, op.catalyst_id, by_id=by_id, by_key=by_key,
                                       aliases=aliases, vault=vault)
            if target is None:
                reply.checks.append(refuse("desk_catalyst_retire",
                                           f"items[{op.index}] was not retired: {problem}."))
                continue
            if target["status"] == "retired":
                reply.checks.append(note(
                    "desk_catalyst_already_retired",
                    f"items[{op.index}] ({target['name']!r}) was already retired on "
                    f"{_iso(target['retired_at'])}: {target['retired_reason']}."))
                continue
            retires.append((op, target))

    for op, target in renames:
        reply.applied.append({
            "index": op.index, "op": "rename", "result": "renamed", "id": target["id"],
            "name": op.new_name, "former_name": target["name"], "external_key": op.new_key,
        })
    for up, result, existing in upserts:
        entry: dict[str, Any] = {
            "index": up.index, "op": "upsert", "result": result,
            "id": None if existing is None else existing["id"],
            "name": up.name, "external_key": up.key, "category": up.category,
            "family": up.family, **up.when.as_json(), "date_estimated": up.estimated,
        }
        if existing is not None and result == "updated":
            entry["changed"] = _changed(existing, up)
        reply.applied.append(entry)
    for ret, target in retires:
        reply.applied.append({
            "index": ret.index, "op": "retire", "result": "retired", "id": target["id"],
            "name": target["name"], "reason": ret.reason,
        })
    reply.applied.sort(key=lambda one: one["index"])

    record = {"payload": scrubbed(payload), "applied": reply.applied,
              "refusals": reply.refusals, "origin": origin}
    if dry_run:
        reply.batch_id = await store.archive("catalysts", "submission", record, at=now,
                                             shadow=True)
        return reply

    run_id = await store.start_desk_run("catalysts", origin, now)
    reply.run_id = run_id
    try:
        reply.batch_id = await store.archive("catalysts", "submission", record, at=now,
                                             shadow=False)
        async with store.transaction() as connection:
            for op, target in renames:
                family, _ = families.classify(op.new_name)
                await store.rename_catalyst(
                    connection, target["id"], old_key=target["external_key"],
                    old_name=target["name"], new_name=op.new_name, new_key=op.new_key,
                    family=family, category=target["category"] or "other", reason=op.reason,
                    run_id=run_id, at=now, feed_source=ORIGIN,
                )
            for up, _result, _existing in upserts:
                catalyst_id = await store.upsert_catalyst(connection, {
                    "external_key": up.key, "name": up.name,
                    "starts_at": up.when.starts_at, "ends_at": up.when.ends_at,
                    "date_precision": up.when.precision, "date_text": up.date_text,
                    "date_estimated": up.estimated, "family": up.family,
                    "category": up.category, "source_url": up.source_url,
                    "affected": up.affected, "seen_on": today, "feed_source": ORIGIN,
                    "run_id": run_id, "at": now,
                })
                for entry in reply.applied:
                    if entry["index"] == up.index:
                        entry["id"] = catalyst_id
            for ret, target in retires:
                await store.retire_catalyst(
                    connection, target["id"], reason=ret.reason, on=today, run_id=run_id,
                    at=now, feed_source=ORIGIN,
                )
            confirmed = await store.confirm_listed(connection, on=today)
        reply.extra["still_listed_confirmed"] = confirmed
        await _finish(store, reply, run_id=run_id, rows=len(reply.applied), now=now,
                      finding_key="desk_catalyst_refused")
    except Exception as exc:
        await _failed(store, run_id, exc)
        raise
    return reply


def _changed(existing: dict[str, Any], op: CatalystUpsert) -> list[str]:
    """Which stored fields this upsert would change, for the reply."""
    pairs = {
        "name": (existing["name"], op.name),
        "starts_at": (existing["starts_at"], op.when.starts_at),
        "ends_at": (existing["ends_at"], op.when.ends_at),
        "date_precision": (existing["date_precision"], op.when.precision),
        "date_estimated": (existing["date_estimated"], op.estimated),
        "date_text": (existing["date_text"], op.date_text),
        "category": (existing["category"], op.category),
        "source_url": (existing["source_url"], op.source_url),
        "affected": (tuple(existing["affected"] or ()), op.affected),
    }
    return [key for key, (old, new) in pairs.items() if old != new]


# ---------------------------------------------------------------------------
# Tail risks
# ---------------------------------------------------------------------------


def _check_line(check: dict[str, Any]) -> str:
    """One recent check as the list prints it: `2026-09-24 daily elevated`."""
    day = str(check["checked_at"])[:10]
    return f"{day} {check['kind']} {check['severity']}" + (" retired" if check["retired"] else "")


async def tail_risks_list(store: DeskStore, *, include_retired: bool) -> dict[str, Any]:
    """The live risks (every risk with `include_retired`), compact enough for one tool result.

    Each recent check is one line, newest first. First and last seen and the
    feed that wrote the row are left out: the agent never acts on them.
    """
    rows = await store.tail_risks()
    checks = await store.tail_risk_checks()
    chosen = [one for one in rows if include_retired or one["retired_on"] is None]
    return {
        "count": len(chosen),
        "tail_risks": [
            {
                "risk_id": one["risk_id"],
                "name": one["name"],
                "severity": one["severity"],
                "summary": one["summary"],
                "priced_in": one["priced_in"],
                "assets": list(one["assets"] or ()),
                "source_url": one["source_url"],
                "retired_on": _iso(one["retired_on"]),
                "retired_reason": one["retired_reason"],
                "daily_recheck": one["retired_on"] is None
                and one["severity"] in ("elevated", "acute"),
                "recent_checks": [_check_line(c) for c in checks.get(one["risk_id"], [])],
            }
            for one in chosen
        ],
    }


async def tail_risks_submit(
    store: DeskStore,
    settings: Settings,
    payload: Any,
    *,
    now: datetime,
    dry_run: bool,
    origin: str = ORIGIN,
) -> Reply:
    vault = bridge_vault(settings)
    reply = Reply(feed="tail_risks", dry_run=dry_run)
    kind, ops, checks = risk_ops(payload)
    reply.checks.extend(checks)
    reply.extra["kind"] = kind
    if kind is None:
        return reply

    rows = await store.tail_risks()
    by_id = {one["risk_id"]: one for one in rows}
    by_key = {one["key"]: one for one in rows}
    by_name = {vault.external_key(one["name"]): one for one in rows}
    today = singapore_day(now)
    wanted = sorted({g for op in ops if isinstance(op, RiskUpsert) for g in op.gauges})
    gauges = await store.gauge_values(wanted)
    unknown = [g for g in wanted if g not in gauges]
    if unknown:
        reply.checks.append(note(
            "desk_tail_risk_gauge",
            f"gauges {', '.join(unknown)} are not dashboard series and were left out; "
            "`desk context` lists the levels by key."))

    writes: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    voice_fields: list[voice.Field] = []
    for op in ops:
        row = by_id.get(op.risk_id)
        active = row is not None and row["retired_on"] is None
        label = f"items[{op.index}] ({op.risk_id})"
        if isinstance(op, RiskRetire):
            if row is None:
                reply.checks.append(refuse("desk_tail_risk_retire",
                                           f"{label}: no tail risk has this risk_id."))
                continue
            if not active:
                reply.checks.append(note("desk_tail_risk_already_retired",
                                         f"{label} was already retired on "
                                         f"{_iso(row['retired_on'])}."))
                continue
            if kind == "daily" and row["severity"] == "watch":
                reply.checks.append(refuse(
                    "desk_tail_risk_daily_scope",
                    f"{label}: the daily recheck covers elevated and acute risks; a watch-level "
                    "risk is retired by the Monday scan."))
                continue
            check_row = {
                "risk_id": op.risk_id, "checked_at": now, "kind": kind,
                "severity": row["severity"], "summary": op.reason, "priced_in": None,
                "gauges": {}, "source_url": None, "retired": True,
            }
            writes.append(("retire", {"risk_id": op.risk_id, "reason": op.reason}, check_row))
            reply.applied.append({"index": op.index, "op": "retire", "result": "retired",
                                  "risk_id": op.risk_id, "reason": op.reason})
            continue

        assert isinstance(op, RiskUpsert)
        if row is None:
            if op.name is None:
                reply.checks.append(refuse(
                    "desk_tail_risk_new",
                    f"{label}: a new risk needs a `name`; this risk_id is not on the list."))
                continue
            twin = by_name.get(vault.external_key(op.name)) or by_key.get(op.risk_id)
            if twin is not None:
                reply.checks.append(refuse(
                    "desk_tail_risk_twin",
                    f"{label}: {twin['name']!r} is already tracked as {twin['risk_id']}. Update "
                    "that risk_id instead of opening a second one."))
                continue
        if kind == "daily":
            if not active and op.severity == "watch":
                reply.checks.append(refuse(
                    "desk_tail_risk_daily_scope",
                    f"{label}: the daily recheck does not add watch-level risks; a new risk at "
                    "watch waits for the Monday scan. Add it now only if it is already elevated "
                    "or acute."))
                continue
            if active and row is not None and row["severity"] == "watch" and (
                severity_rank(op.severity) <= severity_rank("watch")
            ):
                reply.checks.append(refuse(
                    "desk_tail_risk_daily_scope",
                    f"{label}: the daily recheck covers elevated and acute risks, and escalations. "
                    "This risk is at watch and stays at watch, so it is read on Monday."))
                continue
        if row is None:
            result = "inserted"
        elif not active:
            result = "revived"
        elif severity_rank(op.severity) > severity_rank(row["severity"]):
            result = "escalated"
        elif severity_rank(op.severity) < severity_rank(row["severity"]):
            result = "de-escalated"
        else:
            result = "updated"
        name = op.name or (row["name"] if row else op.risk_id)
        values = {g: gauges[g] for g in op.gauges if g in gauges}
        risk_row = {
            "key": row["key"] if row else op.risk_id,
            "risk_id": op.risk_id,
            "name": name,
            "severity": op.severity,
            "summary": op.summary,
            "priced_in": op.priced_in if op.priced_in is not None
            else (row["priced_in"] if row else None),
            "assets": op.assets if op.assets is not None
            else tuple(row["assets"] or ()) if row else (),
            "source_url": op.source_url,
            "seen_on": today,
        }
        check_row = {
            "risk_id": op.risk_id, "checked_at": now, "kind": kind, "severity": op.severity,
            "summary": op.summary, "priced_in": risk_row["priced_in"], "gauges": values,
            "source_url": op.source_url, "retired": False,
        }
        writes.append(("upsert", risk_row, check_row))
        reply.applied.append({
            "index": op.index, "op": "upsert", "result": result, "risk_id": op.risk_id,
            "name": name, "severity": op.severity,
            "previous_severity": None if row is None else row["severity"],
            "gauges": values,
        })
        voice_fields += _field(f"{op.risk_id}_summary", f"{op.risk_id} summary",
                                     op.summary)
        voice_fields += _field(f"{op.risk_id}_priced_in", f"{op.risk_id} priced-in read",
                                     op.priced_in)
    reply.voice = _voice(voice_fields, "tail risks")

    record = {"payload": scrubbed(payload), "applied": reply.applied,
              "refusals": reply.refusals, "origin": origin}
    if dry_run:
        reply.batch_id = await store.archive("tail_risks", "submission", record, at=now,
                                             shadow=True)
        return reply

    run_id = await store.start_desk_run("tail_risks", origin, now)
    reply.run_id = run_id
    try:
        reply.batch_id = await store.archive("tail_risks", "submission", record, at=now,
                                             shadow=False)
        async with store.transaction() as connection:
            for action, row, check_row in writes:
                if action == "retire":
                    await store.retire_tail_risk(
                        connection, row["risk_id"], reason=row["reason"], on=today,
                        run_id=run_id, feed_source=ORIGIN,
                    )
                else:
                    await store.upsert_tail_risk(
                        connection, row | {"feed_source": ORIGIN, "run_id": run_id}
                    )
                await store.write_tail_risk_check(connection, check_row | {"run_id": run_id})
        await _finish(store, reply, run_id=run_id, rows=len(writes), now=now,
                      finding_key="desk_tail_risk_refused")
    except Exception as exc:
        await _failed(store, run_id, exc)
        raise
    return reply


# ---------------------------------------------------------------------------
# Positions
# ---------------------------------------------------------------------------


def norm_symbol(symbol: str) -> str:
    return " ".join(str(symbol).split()).upper()


def _asset_class(row: dict[str, Any]) -> str:
    kind = (row.get("kind") or "").lower()
    if kind == "etf":
        return "etf"
    if kind in prices.CRYPTO_KINDS:
        return "crypto"
    return str(row.get("asset_class") or kind or "other")


async def positions(store: DeskStore, settings: Settings, *, now: datetime) -> dict[str, Any]:
    """Every watched name with its price and 24h change, computed here.

    Accounting's open positions (never cash), then the operator's manual list,
    then the watch list. A failure to read Accounting is reported and the other
    two lists are still priced.
    """
    entries: list[dict[str, Any]] = []
    accounting: dict[str, Any] = {"ok": True, "error": None, "venues": [], "venues_error": None}
    try:
        held = await store.accounting_positions()
    except WriteRefused as exc:
        LOG.warning("Accounting's positions could not be read: %s", redact_accounts(exc.sentence))
        held = []
        accounting["ok"] = False
        accounting["error"] = (
            "Accounting's positions could not be read, so only the manual and watch lists are "
            "below. Say so; do not report the book as empty."
        )
    for one in held:
        entries.append({
            "symbol": norm_symbol(one["symbol"]),
            "name": one.get("display_name") or one.get("name"),
            "source": "accounting",
            "asset_class": _asset_class(one),
            "kind": one.get("kind"),
            "venues": list(one.get("venues") or ()),
            "direction": one.get("direction"),
            "quantity": one.get("quantity"),
            "exposure_usd": one.get("exposure_usd"),
            "currency": one.get("currency"),
            "underlying": one.get("underlying_symbol"),
            "expiry": one.get("expiry"),
            "accounting_mark": one.get("mark"),
            "accounting_mark_as_of": one.get("mark_as_of"),
        })
    try:
        accounting["venues"] = await store.accounting_venues()
    except WriteRefused as exc:
        LOG.warning("Accounting's venue status could not be read: %s",
                    redact_accounts(exc.sentence))
        accounting["venues_error"] = (
            "Accounting's venue status could not be read, so how fresh its book is is unknown"
            + ("; the positions are still Accounting's." if accounting["ok"] else ".")
        )
    for one in await store.manual_positions():
        entries.append({
            "symbol": norm_symbol(one["symbol"]), "name": one["name"], "source": "manual",
            "asset_class": one["asset_class"], "kind": None, "venues": [one["venue"]],
            "direction": None, "quantity": one["quantity"], "exposure_usd": None,
            "currency": None, "underlying": None, "expiry": None,
            "accounting_mark": None, "accounting_mark_as_of": None,
        })
    for one in await store.watch_tickers():
        entries.append({
            "symbol": norm_symbol(one["symbol"]), "name": one["name"], "source": "watch",
            "asset_class": one["asset_class"], "kind": None, "venues": [],
            "direction": None, "quantity": None, "exposure_usd": None, "currency": None,
            "underlying": None, "expiry": None, "accounting_mark": None,
            "accounting_mark_as_of": None,
        })

    symbols = prices.SymbolMap.load(settings.symbols_yaml)
    routes = [
        prices.route(
            one["symbol"], asset_class=one["asset_class"], kind=one["kind"],
            currency=one["currency"], underlying=one["underlying"], symbols=symbols,
        )
        for one in entries
    ]
    quotes = await prices.quotes(
        routes, http=bridge_http(settings), clock=bridge_clock(settings), now=now
    )
    rows = []
    for entry, found in zip(entries, routes, strict=True):
        quote = quotes[found.key]
        rows.append(entry | quote.as_json() | {"underlying_only": found.underlying_only})
    return {
        "as_of": singapore_day(now).isoformat(),
        "computed_at": now.isoformat(),
        "accounting": accounting,
        "rows": [{k: _iso(v) for k, v in one.items()} for one in rows],
    }


#: The fields `desk positions` shows the agent. Size (quantity, exposure) is
#: kept in the archive and on `holding_watch` for the page, not handed to a
#: model that only has to explain moves.
PUBLIC_FIELDS: tuple[str, ...] = (
    "symbol", "name", "source", "asset_class", "venues", "direction", "underlying",
    "expiry", "market_symbol", "price", "price_at", "currency", "change_24h_pct",
    "change_basis", "change_session", "price_source", "unavailable_reason",
    "underlying_only",
)


def public_positions(found: dict[str, Any], *, batch_id: int | None) -> dict[str, Any]:
    rows = [{key: one.get(key) for key in PUBLIC_FIELDS} for one in found["rows"]]
    return {
        "as_of": found["as_of"],
        "computed_at": found["computed_at"],
        "positions_batch_id": batch_id,
        "accounting": found["accounting"],
        "counts": {
            "accounting": sum(1 for one in rows if one["source"] == "accounting"),
            "manual": sum(1 for one in rows if one["source"] == "manual"),
            "watch": sum(1 for one in rows if one["source"] == "watch"),
            "change_unavailable": sum(1 for one in rows if one["change_24h_pct"] is None),
        },
        "positions": rows,
        "notes": [
            "change_24h_pct is computed from market data and is the number to report; "
            "close_to_close is the last completed session against the one before, "
            "rolling_24h is now against 24 hours ago",
            "a null change carries unavailable_reason; say the move is unavailable, never "
            "estimate it",
            "an option row carries its underlying's move (underlying_only)",
            "pass positions_batch_id to `desk holdings submit` so it records these same "
            "numbers",
        ],
    }


# ---------------------------------------------------------------------------
# Holdings
# ---------------------------------------------------------------------------


#: The largest id a bigint holds; a larger number is not a batch and is never sent.
_BIGINT_MAX = 2**63 - 1


def _batch_id(value: Any) -> int | None:
    """`positions_batch_id` as a batch id, or None when it cannot be one."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        number = value
    elif isinstance(value, str) and value.strip().isdigit() and len(value.strip()) <= 19:
        number = int(value.strip())
    else:
        return None
    return number if 1 <= number <= _BIGINT_MAX else None


async def _positions_for(
    store: DeskStore, settings: Settings, payload: Any, *, now: datetime, reply: Reply
) -> dict[str, Any]:
    wanted = payload.get("positions_batch_id") if isinstance(payload, dict) else None
    if wanted is not None:
        batch_id = _batch_id(wanted)
        batch = None if batch_id is None else await store.batch(batch_id)
        if (
            batch is not None
            and batch["source"] == "desk:positions"
            and batch["kind"] == "positions"
            and now - batch["fetched_at"] <= POSITIONS_REUSE
        ):
            reply.extra["positions_from"] = f"batch {batch['id']}"
            found: dict[str, Any] = batch["payload"]
            return found
        reply.checks.append(note(
            "desk_holding_positions_recomputed",
            f"positions_batch_id {str(wanted)[:40]!r} is not a `desk positions` archive from the "
            "last "
            f"{int(POSITIONS_REUSE.total_seconds() // 3600)} hours, so the prices were computed "
            "again for this submission."))
    found = await positions(store, settings, now=now)
    reply.extra["positions_from"] = "computed for this submission"
    return found


def _earnings_match(
    event: HoldingEvent,
    symbol: str,
    company: str | None,
    catalysts: list[dict[str, Any]],
    aliases: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """The earnings catalyst already on the calendar for this event, if any.

    Same name (or a former name) first. Otherwise a tracked earnings catalyst
    that names the ticker or the company and is dated within a week of it,
    which is how "Micron Q1 FY2027 earnings" and "MU earnings" are one event.
    """
    by_key = {one["external_key"]: one for one in catalysts}
    by_id = {one["id"]: one for one in catalysts}
    if event.key in by_key:
        return by_key[event.key]
    if event.key in aliases and aliases[event.key]["catalyst_id"] in by_id:
        return by_id[aliases[event.key]["catalyst_id"]]
    words = [re.escape(symbol.split()[0])]
    if company:
        first = next((w for w in re.split(r"\W+", company) if len(w) >= 4), None)
        if first:
            words.append(re.escape(first))
    pattern = re.compile(r"\b(" + "|".join(words) + r")\b", re.IGNORECASE)
    best: tuple[timedelta, dict[str, Any]] | None = None
    for one in catalysts:
        if one["status"] != "tracked" or one["category"] != "earnings":
            continue
        if not pattern.search(one["name"]):
            continue
        if one["starts_at"] is None or event.when.starts_at is None:
            gap = timedelta(days=0) if one["starts_at"] is None else timedelta(days=6)
        else:
            gap = abs(one["starts_at"] - event.when.starts_at)
        if gap > timedelta(days=7):
            continue
        if best is None or gap < best[0]:
            best = (gap, one)
    return None if best is None else best[1]


def _firmer(event: HoldingEvent, row: dict[str, Any]) -> bool:
    """Whether the event's date should replace the calendar's: a firm date beats a guess."""
    vague = row["date_precision"] in ("month", "unknown") or bool(row["date_estimated"])
    precise = event.when.precision in ("exact", "day", "window")
    return vague and precise and not event.estimated


async def holdings_submit(
    store: DeskStore,
    settings: Settings,
    payload: Any,
    *,
    now: datetime,
    dry_run: bool,
    origin: str = ORIGIN,
) -> Reply:
    vault = bridge_vault(settings)
    families = bridge_families(settings)
    reply = Reply(feed="holdings", dry_run=dry_run)
    items, checks = holding_items(payload, now=now, vault=vault)
    reply.checks.extend(checks)

    found = await _positions_for(store, settings, payload, now=now, reply=reply)
    rows = found["rows"]
    as_of = date.fromisoformat(found["as_of"])
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for one in rows:
        by_symbol.setdefault(norm_symbol(one["symbol"]), []).append(one)

    claims: dict[tuple[str, str], list[HoldingItem]] = {}
    for item in items:
        candidates = [
            one for one in by_symbol.get(norm_symbol(item.symbol), [])
            if item.source is None or one["source"] == item.source
        ]
        if not candidates:
            reply.checks.append(refuse(
                "desk_holding_unknown",
                f"items[{item.index}] ({item.symbol}) is not a watched name today; `desk "
                "positions` lists the names, and the dashboard edits the manual and watch "
                "lists."))
            continue
        for one in candidates:
            claims.setdefault((norm_symbol(one["symbol"]), one["source"]), []).append(item)
    # {GOOG} and {GOOG, source: accounting} both reach the Accounting row; which
    # driver is meant is a guess, so neither is written.
    doubled: dict[int, tuple[HoldingItem, tuple[str, str]]] = {}
    for position, claimants in claims.items():
        if len(claimants) > 1:
            for one in claimants:
                doubled.setdefault(one.index, (one, position))
    for index in sorted(doubled):
        item, (symbol, source) = doubled[index]
        reply.checks.append(refuse(
            "desk_holding_duplicate",
            f"items[{index}] ({item.symbol}): another item in this submission covers the same "
            f"{source} position ({symbol}), so neither was applied. Send one item per name, "
            "with `source` only when the name is on two lists."))
    matched: dict[tuple[str, str], HoldingItem] = {
        position: claimants[0]
        for position, claimants in claims.items()
        if not any(one.index in doubled for one in claimants)
    }

    catalysts = await store.catalysts()
    aliases = await store.catalyst_aliases()
    today = singapore_day(now)
    watch_rows: list[dict[str, Any]] = []
    for one in rows:
        symbol = norm_symbol(one["symbol"])
        said = matched.get((symbol, one["source"]))
        change = one.get("change_24h_pct")
        watch_rows.append({
            "as_of": as_of,
            "symbol": symbol,
            "source": one["source"],
            "name": one.get("name"),
            "asset_class": one.get("asset_class"),
            "market_symbol": one.get("market_symbol"),
            "price": None if one.get("price") is None else Decimal(str(one["price"])),
            "price_at": None if not one.get("price_at")
            else datetime.fromisoformat(one["price_at"]),
            "currency": one.get("currency"),
            "change_24h_pct": None if change is None else Decimal(str(change)),
            "change_basis": one.get("change_basis"),
            "change_session": None if not one.get("change_session")
            else date.fromisoformat(one["change_session"]),
            "price_source": one.get("price_source"),
            "unavailable_reason": one.get("unavailable_reason")
            or (None if change is not None else "no change was computed"),
            "underlying_only": bool(one.get("underlying_only")),
            "quantity": None if one.get("quantity") is None else Decimal(str(one["quantity"])),
            "exposure_usd": None if one.get("exposure_usd") is None
            else Decimal(str(one["exposure_usd"])),
            "material": bool(said and said.material),
            "driver_summary": said.driver_summary if said else None,
            "source_url": said.source_url if said else None,
            "no_clear_reason": bool(said and said.no_clear_reason),
            "recorded_at": now,
        })

    events: list[dict[str, Any]] = []
    catalyst_writes: list[dict[str, Any]] = []
    voice_fields: list[voice.Field] = []
    seen_items: set[int] = set()
    for (symbol, _source), item in matched.items():
        if item.index in seen_items:
            continue
        seen_items.add(item.index)
        company = next((one.get("name") for one in by_symbol.get(symbol, [])), None)
        voice_fields += _field(f"{symbol}_driver", f"the {symbol} driver",
                                     item.driver_summary)
        for event in item.events:
            entry: dict[str, Any] = {
                "symbol": symbol, "kind": event.kind, "name": event.name,
                "name_key": event.key, "starts_at": event.when.starts_at,
                "ends_at": event.when.ends_at, "date_precision": event.when.precision,
                "date_estimated": event.estimated, "date_text": event.date_text,
                "source_url": event.source_url, "catalyst_id": None, "seen_on": today,
                "calendar": None,
            }
            if event.kind == "earnings":
                existing = _earnings_match(event, symbol, company, catalysts, aliases)
                if existing is None:
                    name = event.name if symbol.split()[0] in event.name.upper() \
                        else f"{event.name} ({symbol.split()[0]})"
                    family, _category = families.classify(name)
                    catalyst_writes.append({
                        "action": "insert", "event": entry, "row": {
                            "external_key": vault.external_key(name), "name": name,
                            "starts_at": event.when.starts_at, "ends_at": event.when.ends_at,
                            "date_precision": event.when.precision,
                            "date_text": event.date_text, "date_estimated": event.estimated,
                            "family": family, "category": "earnings",
                            "source_url": event.source_url, "affected": (symbol.split()[0],),
                            "seen_on": today, "at": now,
                        }})
                    entry["calendar"] = f"added to the catalyst calendar as {name!r}"
                else:
                    entry["catalyst_id"] = existing["id"]
                    if existing["status"] != "tracked":
                        entry["calendar"] = (f"matches {existing['name']!r}, which is retired; "
                                             "not revived from the holdings watch")
                    elif _firmer(event, existing):
                        catalyst_writes.append({"action": "firm", "event": entry,
                                                "id": existing["id"], "row": {
                            "starts_at": event.when.starts_at, "ends_at": event.when.ends_at,
                            "date_precision": event.when.precision,
                            "date_text": event.date_text, "date_estimated": event.estimated,
                            "source_url": event.source_url, "seen_on": today, "at": now,
                        }})
                        entry["calendar"] = (f"firmed the date of {existing['name']!r} on the "
                                             "catalyst calendar")
                    else:
                        entry["calendar"] = (
                            f"already on the catalyst calendar as {existing['name']!r}"
                        )
                        if (
                            existing["starts_at"] is not None
                            and event.when.starts_at is not None
                            and abs(existing["starts_at"] - event.when.starts_at)
                            > timedelta(days=1)
                        ):
                            reply.checks.append(note(
                                "desk_holding_event_date",
                                f"{symbol}: {event.name!r} is dated "
                                f"{event.when.starts_at.date().isoformat()} here and "
                                f"{existing['starts_at'].date().isoformat()} on the catalyst "
                                f"calendar ({existing['name']!r}). The calendar was left as it "
                                "is; correct it with `desk catalysts submit` if it is wrong."))
            events.append(entry)

    # A merge per (as_of, symbol, source): a name the payload carries is written
    # (its driver, and the prices of this submission); a name it does not carry
    # keeps the row an earlier submission wrote for the day, and gets a quiet row
    # only if it has none. A resend of one fixed item erases nothing else.
    recorded = await store.holding_watch_keys(as_of)
    said_rows = [one for one in watch_rows if (one["symbol"], one["source"]) in matched]
    quiet_rows = [
        one for one in watch_rows
        if (one["symbol"], one["source"]) not in matched
        and (one["symbol"], one["source"]) not in recorded
    ]
    kept = [
        one["symbol"] for one in watch_rows
        if (one["symbol"], one["source"]) not in matched
        and (one["symbol"], one["source"]) in recorded
    ]
    reply.applied = [
        {k: _iso(v) for k, v in one.items()
         if k in ("symbol", "source", "change_24h_pct", "change_basis", "material",
                  "driver_summary", "no_clear_reason")}
        for one in said_rows + quiet_rows
    ]
    reply.extra["kept"] = kept
    reply.extra["events"] = [
        {k: _iso(v) for k, v in one.items() if k not in ("seen_on", "name_key")}
        for one in events
    ]
    reply.extra["as_of"] = as_of.isoformat()
    reply.voice = _voice(voice_fields, "holdings watch")

    record = {"payload": scrubbed(payload), "watch": reply.applied,
              "events": reply.extra["events"], "refusals": reply.refusals, "origin": origin}
    if dry_run:
        reply.batch_id = await store.archive("holdings", "submission", record, at=now,
                                             shadow=True)
        return reply

    run_id = await store.start_desk_run("holdings", origin, now)
    reply.run_id = run_id
    try:
        reply.batch_id = await store.archive("holdings", "submission", record, at=now,
                                             shadow=False)
        async with store.transaction() as connection:
            await store.upsert_holding_watch(
                connection, [one | {"run_id": run_id} for one in said_rows]
            )
            await store.upsert_holding_watch(
                connection, [one | {"run_id": run_id} for one in quiet_rows], keep_existing=True
            )
            for write in catalyst_writes:
                if write["action"] == "insert":
                    catalyst_id = await store.upsert_catalyst(
                        connection, write["row"] | {"feed_source": ORIGIN, "run_id": run_id}
                    )
                    write["event"]["catalyst_id"] = catalyst_id
                else:
                    await store.link_catalyst_dates(
                        connection, write["id"],
                        write["row"] | {"feed_source": ORIGIN, "run_id": run_id},
                    )
            for entry in events:
                await store.upsert_holding_event(connection, entry | {"run_id": run_id})
        written = len(said_rows) + len(quiet_rows) + len(events)
        await _finish(store, reply, run_id=run_id, rows=written, now=now,
                      finding_key="desk_holding_refused")
    except Exception as exc:
        await _failed(store, run_id, exc)
        raise
    return reply
