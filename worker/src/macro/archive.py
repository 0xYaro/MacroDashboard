"""Catalysts that happened, archived one row per occurrence.

`macro_core.catalyst` is the live calendar and forgets: a passed catalyst is
retired, and a recurring name's row moves on to its next date. The archive,
`macro_core.catalyst_occurrence`, keeps every occurrence so a later review can
ask which kinds of catalyst came round and which of them mattered.

## When a live catalyst counts as having happened

Its start has passed *and* the Catalyst Monitor was still listing it within
`LISTING_GRACE` of the day it started. Retirement alone is not the signal: the
monitor also drops a catalyst that was cancelled, rescheduled out of range, or
judged irrelevant, and those did not happen.

The grace exists because the evidence is `last_seen`, the last 09:30 run that
read the catalyst in the file, and a run can be missed: the Mac asleep, a
failed run. The monitor prunes a passed event the next morning, so a missed run
on the event's day would otherwise lose it for good. The replay of the
monitor's history lost Jackson Hole exactly that way, to three days with no
snapshot. The cost is that an event cancelled within the grace of its date is
archived as held; the monitor rarely drops one that late without renaming it.

A catalyst whose date is only a month ("late October expected") is never
archived from that date; the monitor firms dates up before they pass, and the
archive waits for a day.

The Catalyst Monitor removes a passed event on its next morning run, before the
worker reads the file, so the archive is fed from the table and not the file.

## The natural key

`(name_key, occurred_on)`. `name_key` is the live table's own `external_key`,
and `occurred_on` is the UTC date of `starts_at`, the same rule the live table
uses for a date with no time. The backfill applies the same rule, so an event
that reached the archive both ways is one row.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from .families import classify
from .model import CatalystOccurrence, DatePrecision, OccurrenceConfidence, OccurrenceProvenance
from .vault import external_key

ARCHIVABLE_PRECISION: tuple[DatePrecision, ...] = ("exact", "day", "window")

#: How long before its start a catalyst may last have been listed and still count as held.
LISTING_GRACE = timedelta(days=3)

# The monitor's own words for a date it has not confirmed. Its prompt requires one of
# these on any date that came from memory rather than from a fetched source.
_UNCONFIRMED = re.compile(r"\b(expected|TBD|tentative|around|estimated|unconfirmed)\b", re.I)


def occurred_on(starts_at: datetime) -> date:
    """The archive's date for an event: the UTC date of its start."""
    return starts_at.astimezone(UTC).date()


def confidence_of(date_text: str | None, precision: DatePrecision) -> OccurrenceConfidence:
    """Confirmed only when the monitor stated a firm day and did not hedge it."""
    if precision == "window" or date_text is None or _UNCONFIRMED.search(date_text):
        return "approximate"
    return "confirmed"


def from_live(rows: Iterable[Mapping[str, Any]], *, now: datetime) -> list[CatalystOccurrence]:
    """Live `macro_core.catalyst` rows that have happened, as archive rows.

    `rows` carry the table's columns. Rows that have not started, whose date is
    no better than a month, or that the monitor had stopped listing more than
    `LISTING_GRACE` before they started are left out.
    """
    archived: list[CatalystOccurrence] = []
    for row in rows:
        starts_at: datetime | None = row["starts_at"]
        precision = cast(DatePrecision, row["date_precision"])
        if starts_at is None or precision not in ARCHIVABLE_PRECISION:
            continue
        # A day with no time sits at UTC midnight, 08:00 in Singapore, so it has
        # not "started" at 09:30 that morning in any sense that matters. It waits
        # for the day to end; an exact time counts from that time.
        if (starts_at if precision == "exact" else starts_at + timedelta(days=1)) > now:
            continue
        on = occurred_on(starts_at)
        last_seen: date = row["last_seen"]
        if last_seen < on - LISTING_GRACE:
            continue  # dropped before it started: it did not happen
        family, category = classify(row["name"])
        archived.append(
            CatalystOccurrence(
                name=row["name"],
                name_key=row["external_key"],
                occurred_on=on,
                starts_at=starts_at,
                ends_at=row["ends_at"],
                date_precision=precision,
                family=family,
                category=category,
                source_url=row["source_url"],
                affected=tuple(row["affected"] or ()),
                provenance="catalyst_monitor",
                provenance_ref=f"{row['first_seen'].isoformat()}..{last_seen.isoformat()}",
                confidence=confidence_of(row["date_text"], precision),
                catalyst_id=str(row["id"]),
                first_tracked=row["first_seen"],
                last_tracked=last_seen,
            )
        )
    return archived


def from_backfill(path: Path) -> list[CatalystOccurrence]:
    """A reviewed backfill file, as archive rows.

    The file is a JSON array of objects with the archive's columns, less the
    ones derived here: `name_key` and `family`/`category` come from the same
    functions the daily run uses, and `occurred_on` is recomputed from
    `starts_at` when there is one, so both routes key an event identically.
    """
    records = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(records, list) or not records:
        raise ValueError(f"{path} holds no rows: expected a non-empty JSON array")
    rows = [_backfill_row(one, index) for index, one in enumerate(records)]
    seen: set[tuple[str, date]] = set()
    twice: set[str] = set()
    for one in rows:
        key = (one.name_key, one.occurred_on)
        if key in seen:
            twice.add(one.name)
        seen.add(key)
    if twice:
        raise ValueError(f"{path} names the same event on the same day twice: {sorted(twice)}")
    return rows


def _backfill_row(record: Mapping[str, Any], index: int) -> CatalystOccurrence:
    try:
        name = str(record["name"]).strip()
        # A date with no time sits at UTC midnight, as the live parser files it.
        starts_at = _instant(record.get("starts_at") or record.get("occurred_on"))
        ends_at = _instant(record.get("ends_at"))
        if starts_at is None:
            raise ValueError("neither starts_at nor occurred_on is set")
        on = occurred_on(starts_at)
        precision = cast(DatePrecision, record["date_precision"])
        provenance = cast(OccurrenceProvenance, record["provenance"])
        confidence = cast(OccurrenceConfidence, record["confidence"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"backfill row {index} is malformed: {exc!r}") from exc
    if not name:
        raise ValueError(f"backfill row {index} has no name")
    if precision not in ARCHIVABLE_PRECISION:
        raise ValueError(f"backfill row {index} ({name}) has precision {precision!r}")
    if provenance not in ("catalyst_monitor", "weekly_journal"):
        raise ValueError(f"backfill row {index} ({name}) has provenance {provenance!r}")
    if confidence not in ("confirmed", "approximate"):
        raise ValueError(f"backfill row {index} ({name}) has confidence {confidence!r}")
    family, category = classify(name)
    return CatalystOccurrence(
        name=name,
        name_key=external_key(name),
        occurred_on=on,
        starts_at=starts_at,
        ends_at=ends_at,
        date_precision=precision,
        family=family,
        category=category,
        source_url=record.get("source_url") or None,
        affected=tuple(record.get("affected") or ()),
        provenance=provenance,
        provenance_ref=record.get("provenance_ref") or None,
        confidence=confidence,
        moved_markets=record.get("moved_markets"),
        market_note=record.get("market_note") or None,
        note=record.get("note") or None,
        first_tracked=_day(record.get("first_tracked")),
        last_tracked=_day(record.get("last_tracked")),
    )


def _instant(value: str | None) -> datetime | None:
    """An ISO instant, or a bare date read as UTC midnight like the live parser does."""
    if not value:
        return None
    if len(value) == 10:
        return datetime.combine(date.fromisoformat(value), datetime.min.time(), tzinfo=UTC)
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(f"{value!r} has no UTC offset")
    return parsed.astimezone(UTC)


def _day(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None
