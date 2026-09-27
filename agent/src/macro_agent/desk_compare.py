"""`macro-agent desk compare`: the trial's shadow batches against the vault feeds.

During the trial the OpenClaw `macro-desk` jobs run every submit as a dry run,
so what they would have written sits in `macro_raw.batch` with `shadow = true`,
while the live tables still come from the Obsidian notes the worker parses. This
module lines the two up per Singapore day so the cutover can be judged on
evidence (docs/DECISIONS.md, 2026-09-24, and openclaw/README.md, Phase 3):

- catalysts, matched by external key or a former name's key: what the desk found
  that the calendar lacks, what the vault added or retired that the desk did not,
  and dates that disagree;
- tail risks, by `risk_id`: new risks, severity disagreements, retirements, and
  live risks a weekly scan left unchecked;
- the holdings watch: which names the desk covered, with a computed move, a
  driver and events, beside the vault's holdings list and that day's changes.

It reads only, and it reads two tables the `macro_desk` role cannot
(`macro_core.holding`, `macro_core.watchlist_change`), so it connects with
`DATABASE_URL`. `compare` itself is a pure function over what `load` returns.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .desk import ORIGIN, singapore_day
from .desk_store import DeskStore

#: How close two catalysts of one family must start to be flagged as a likely duplicate.
NEAR = timedelta(days=3)

SGT = ZoneInfo("Asia/Singapore")


@dataclass(frozen=True, slots=True)
class Inputs:
    """Everything `compare` needs, as `load` reads it."""

    batches: list[dict[str, Any]]
    catalysts: list[dict[str, Any]]
    aliases: dict[str, dict[str, Any]]
    tail_risks: list[dict[str, Any]]
    holdings: list[dict[str, Any]]
    watchlist_changes: list[dict[str, Any]]


@dataclass(slots=True)
class CatalystDay:
    submissions: int = 0
    results: dict[str, int] = field(default_factory=dict)
    refused: int = 0
    new: list[str] = field(default_factory=list)
    dates_disagree: list[str] = field(default_factory=list)
    desk_retired_vault_tracks: list[str] = field(default_factory=list)
    vault_added_not_desk: list[str] = field(default_factory=list)
    vault_retired_not_desk: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RiskDay:
    submissions: list[str] = field(default_factory=list)
    checked: int = 0
    refused: int = 0
    new: list[str] = field(default_factory=list)
    severity_differs: list[str] = field(default_factory=list)
    desk_retired_vault_live: list[str] = field(default_factory=list)
    weekly_unchecked: list[str] = field(default_factory=list)
    checked_ids: set[str] = field(default_factory=set)


@dataclass(slots=True)
class HoldingsDay:
    submissions: int = 0
    names: list[str] = field(default_factory=list)
    with_move: int = 0
    material: list[str] = field(default_factory=list)
    with_driver: int = 0
    events: int = 0
    refused: int = 0
    vault_list: list[str] = field(default_factory=list)
    vault_changes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Day:
    day: date
    catalysts: CatalystDay | None = None
    tail_risks: RiskDay | None = None
    holdings: HoldingsDay | None = None


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


class CompareStore(DeskStore):
    """The desk's reads plus the vault-only tables and the shadow archive."""

    async def shadow_submissions(self, since: datetime) -> list[dict[str, Any]]:
        rows = await self._fetch(
            """
            select id, source, kind, fetched_at, payload
            from macro_raw.batch
            where shadow and kind = 'submission' and source like 'desk:%' and fetched_at >= $1
            order by fetched_at, id
            """,
            since,
        )
        return [self._decoded(dict(one)) for one in rows]

    async def vault_holdings(self) -> list[dict[str, Any]]:
        rows = await self._fetch(
            "select ticker, label, book, active from macro_core.holding order by ticker"
        )
        return [dict(one) for one in rows]

    async def watchlist_changes(self, since: datetime) -> list[dict[str, Any]]:
        rows = await self._fetch(
            "select ticker, observed_at, change_pct, summary, source_url "
            "from macro_core.watchlist_change where observed_at >= $1 order by observed_at",
            since,
        )
        return [dict(one) for one in rows]

    @staticmethod
    def _decoded(row: dict[str, Any]) -> dict[str, Any]:
        if isinstance(row.get("payload"), str):
            row["payload"] = json.loads(row["payload"])
        return row


async def load(store: CompareStore, since: date) -> Inputs:
    start = datetime.combine(since, datetime.min.time(), tzinfo=SGT)
    return Inputs(
        batches=await store.shadow_submissions(start),
        catalysts=await store.catalysts(),
        aliases=await store.catalyst_aliases(),
        tail_risks=await store.tail_risks(),
        holdings=await store.vault_holdings(),
        watchlist_changes=await store.watchlist_changes(start),
    )


# ---------------------------------------------------------------------------
# Comparing
# ---------------------------------------------------------------------------


def _when(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _day_of(value: Any) -> date | None:
    moment = _when(value)
    return None if moment is None else moment.date()


def _short(moment: Any, precision: Any = None) -> str:
    when = _when(moment)
    if when is None:
        return "no date"
    text = f"{when.day} {when:%b}"
    if precision == "exact":
        text += f" {when:%H:%M} UTC"
    return text


def _origin(batch: dict[str, Any]) -> str | None:
    payload = batch.get("payload")
    return payload.get("origin") if isinstance(payload, dict) else None


def compare(inputs: Inputs, *, since: date, until: date, origin: str = ORIGIN) -> list[Day]:
    """One `Day` per Singapore day from `since` to `until`, oldest first."""
    days = {since + timedelta(days=n): Day(since + timedelta(days=n))
            for n in range((until - since).days + 1)}
    by_id = {one["id"]: one for one in inputs.catalysts}
    by_key = {one["external_key"]: one for one in inputs.catalysts}
    risks = {one.get("risk_id"): one for one in inputs.tail_risks if one.get("risk_id")}

    touched_catalysts: dict[date, set[str]] = {}
    retired_catalysts: dict[date, set[str]] = {}
    holdings_state: dict[date, dict[str, Any]] = {}
    for batch in inputs.batches:
        if _origin(batch) != origin:
            continue
        day = singapore_day(batch["fetched_at"])
        if day not in days:
            continue
        record = batch["payload"]
        feed = str(batch["source"]).removeprefix("desk:")
        if feed == "catalysts":
            _catalysts(days[day], record, by_id, by_key, inputs.aliases,
                       touched_catalysts.setdefault(day, set()),
                       retired_catalysts.setdefault(day, set()))
        elif feed == "tail_risks":
            _risks(days[day], record, risks)
        elif feed == "holdings":
            _holdings(days[day], record, holdings_state.setdefault(day, {}))

    for day, entry in days.items():
        if entry.catalysts is not None:
            _vault_catalyst_moves(entry.catalysts, day, inputs.catalysts,
                                  touched_catalysts.get(day, set()),
                                  retired_catalysts.get(day, set()))
        if entry.tail_risks is not None and "weekly" in entry.tail_risks.submissions:
            entry.tail_risks.weekly_unchecked = sorted(
                str(one["risk_id"]) for one in inputs.tail_risks
                if one.get("retired_on") is None and one.get("risk_id")
                and str(one["risk_id"]) not in entry.tail_risks.checked_ids
            )
        if entry.holdings is not None:
            entry.holdings.vault_list = [
                str(one["ticker"]) for one in inputs.holdings if one.get("active")
            ]
            entry.holdings.vault_changes = [
                f"{one['ticker']} ({_pct(one.get('change_pct'))})"
                for one in inputs.watchlist_changes
                if singapore_day(one["observed_at"]) == day
            ]
    return [days[day] for day in sorted(days)]


def _catalysts(
    entry: Day,
    record: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
    by_key: dict[str, dict[str, Any]],
    aliases: dict[str, dict[str, Any]],
    touched: set[str],
    retired: set[str],
) -> None:
    day = entry.catalysts = entry.catalysts or CatalystDay()
    day.submissions += 1
    day.refused += len(record.get("refusals") or [])
    for one in record.get("applied") or []:
        result = str(one.get("result"))
        day.results[result] = day.results.get(result, 0) + 1
        live = _live_catalyst(one, by_id, by_key, aliases)
        if live is not None:
            touched.add(live["id"])
        name = str(one.get("name"))
        if one.get("op") == "retire":
            if live is not None:
                retired.add(live["id"])
                if live.get("status") == "tracked":
                    day.desk_retired_vault_tracks.append(name)
            continue
        if one.get("op") != "upsert":
            continue
        if live is None:
            text = f"{name} ({_short(one.get('starts_at'), one.get('date_precision'))})"
            twin = _near_twin(one, by_key.values())
            if twin is not None:
                text += f", maybe the calendar's '{twin['name']}' ({_short(twin.get('starts_at'))})"
            day.new.append(text)
            continue
        if _dates_differ(one, live):
            day.dates_disagree.append(
                f"{name}: desk {_short(one.get('starts_at'), one.get('date_precision'))} "
                f"({one.get('date_precision')}), vault "
                f"{_short(live.get('starts_at'), live.get('date_precision'))} "
                f"({live.get('date_precision')})"
            )


def _live_catalyst(
    one: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
    by_key: dict[str, dict[str, Any]],
    aliases: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    if one.get("id") and one["id"] in by_id:
        return by_id[one["id"]]
    key = one.get("external_key")
    if key in by_key:
        return by_key[key]
    alias = aliases.get(str(key)) if key else None
    if alias is not None:
        return by_id.get(alias["catalyst_id"])
    return None


def _near_twin(one: dict[str, Any], live: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    start = _when(one.get("starts_at"))
    family = one.get("family")
    if start is None or not family:
        return None
    for row in live:
        other = _when(row.get("starts_at"))
        if (row.get("status") == "tracked" and row.get("family") == family
                and other is not None and abs(other - start) <= NEAR):
            return row
    return None


def _dates_differ(one: dict[str, Any], live: dict[str, Any]) -> bool:
    desk_start, vault_start = _day_of(one.get("starts_at")), _day_of(live.get("starts_at"))
    if desk_start is None or vault_start is None:
        return (desk_start is None) != (vault_start is None)
    if desk_start != vault_start:
        return True
    desk_end, vault_end = _day_of(one.get("ends_at")), _day_of(live.get("ends_at"))
    return desk_end is not None and vault_end is not None and desk_end != vault_end


def _vault_catalyst_moves(
    day: CatalystDay,
    on: date,
    catalysts: list[dict[str, Any]],
    touched: set[str],
    retired: set[str],
) -> None:
    for row in catalysts:
        if row.get("feed_source") not in (None, "vault"):
            continue
        if row.get("first_seen") == on and row["id"] not in touched:
            day.vault_added_not_desk.append(
                f"{row['name']} ({_short(row.get('starts_at'), row.get('date_precision'))})"
            )
        if row.get("retired_at") == on and row["id"] not in retired:
            day.vault_retired_not_desk.append(str(row["name"]))


def _risks(entry: Day, record: dict[str, Any], risks: dict[str | None, dict[str, Any]]) -> None:
    day = entry.tail_risks = entry.tail_risks or RiskDay()
    sent = record.get("payload")
    payload: dict[str, Any] = sent if isinstance(sent, dict) else {}
    day.submissions.append(str(payload.get("kind", "unknown")))
    day.refused += len(record.get("refusals") or [])
    for one in record.get("applied") or []:
        risk_id = str(one.get("risk_id"))
        day.checked_ids.add(risk_id)
        live = risks.get(risk_id)
        if one.get("op") == "retire":
            if live is not None and live.get("retired_on") is None:
                day.desk_retired_vault_live.append(risk_id)
            continue
        day.checked += 1
        if live is None or one.get("result") in ("inserted", "revived"):
            day.new.append(f"{risk_id} ({one.get('severity')})")
        elif live.get("severity") != one.get("severity"):
            day.severity_differs.append(
                f"{risk_id}: desk {one.get('severity')}, vault {live.get('severity')}"
            )


def _holdings(entry: Day, record: dict[str, Any], merged: dict[str, Any]) -> None:
    """Fold one holdings submission into the day's watch.

    A resend carries only the items it fixes, so the day's watch is every name
    any submission covered, each as its latest submission wrote it. `merged`
    is the day's running state: rows by (symbol, source) and events by
    (symbol, name). Refusals are counted across the day's submissions.
    """
    watch: dict[tuple[str, str], dict[str, Any]] = merged.setdefault("watch", {})
    events: set[tuple[str, str]] = merged.setdefault("events", set())
    for one in record.get("watch") or []:
        # The latest row for a name wins; it keeps the place it was first seen in.
        watch[(str(one.get("symbol")), str(one.get("source")))] = one
    for one in record.get("events") or []:
        events.add((str(one.get("symbol")), str(one.get("name"))))
    rows = list(watch.values())
    day = entry.holdings = HoldingsDay(
        submissions=(entry.holdings.submissions + 1 if entry.holdings else 1),
        refused=(entry.holdings.refused if entry.holdings else 0)
        + len(record.get("refusals") or []),
    )
    day.names = list(dict.fromkeys(str(one.get("symbol")) for one in rows))
    day.with_move = sum(1 for one in rows if one.get("change_24h_pct") is not None)
    day.material = [str(one.get("symbol")) for one in rows if one.get("material")]
    day.with_driver = sum(1 for one in rows if one.get("driver_summary"))
    day.events = len(events)


def _pct(value: Any) -> str:
    if value is None:
        return "no move"
    number = float(value)
    return f"{number:+.1f}%"


# ---------------------------------------------------------------------------
# Writing it out
# ---------------------------------------------------------------------------


def _line(label: str, items: list[str]) -> list[str]:
    # The same finding from two runs in a day is one finding.
    unique = list(dict.fromkeys(items))
    return [f"    {label}: {'; '.join(unique)}."] if unique else []


def render(days: list[Day], *, since: date, until: date) -> str:
    out = [f"Desk trial against the vault feeds, {since} to {until} (Singapore days)."]
    for entry in days:
        out.append("")
        out.append(f"{entry.day:%a %d %b %Y}")
        cat = entry.catalysts
        if cat is None:
            out.append("  Catalysts: no desk run.")
        else:
            counts = ", ".join(f"{n} {r}" for r, n in sorted(cat.results.items())) or "nothing"
            out.append(f"  Catalysts: {cat.submissions} submission(s): {counts}; "
                       f"{cat.refused} refused.")
            out += _line("New on the desk, not on the calendar", cat.new)
            out += _line("Dates disagree", cat.dates_disagree)
            out += _line("Retired by the desk, still tracked by the vault",
                         cat.desk_retired_vault_tracks)
            out += _line("Added by the vault that day, not sent by the desk",
                         cat.vault_added_not_desk)
            out += _line("Retired by the vault that day, not by the desk",
                         cat.vault_retired_not_desk)
        risk = entry.tail_risks
        if risk is None:
            out.append("  Tail risks: no desk run.")
        else:
            kinds = ", ".join(f"{risk.submissions.count(k)} {k}"
                              for k in sorted(set(risk.submissions)))
            out.append(f"  Tail risks: {kinds} submission(s), {risk.checked} risks checked, "
                       f"{risk.refused} refused.")
            out += _line("New on the desk", risk.new)
            out += _line("Severity differs", risk.severity_differs)
            out += _line("Retired by the desk, live on the vault", risk.desk_retired_vault_live)
            out += _line("Live on the vault, not in the weekly scan", risk.weekly_unchecked)
        hold = entry.holdings
        if hold is None:
            out.append("  Holdings: no desk run.")
        else:
            out.append(f"  Holdings: {len(hold.names)} names, {hold.with_move} with a computed "
                       f"move, {len(hold.material)} material, {hold.with_driver} with a driver, "
                       f"{hold.events} events, {hold.refused} refused.")
            out += _line("Desk names", hold.names)
            out += _line("Material on the desk", hold.material)
            out += _line("Vault holdings list", hold.vault_list)
            shared = sorted(set(hold.names) & set(hold.vault_list))
            out.append(f"    Names on both lists: {', '.join(shared) if shared else 'none'}.")
            out += _line("Vault changes that day", hold.vault_changes)
    return "\n".join(out) + "\n"


def as_json(days: list[Day]) -> list[dict[str, Any]]:
    out = []
    for one in days:
        found = asdict(one)
        if found.get("tail_risks"):
            found["tail_risks"]["checked_ids"] = sorted(found["tail_risks"]["checked_ids"])
        out.append(found)
    return out
