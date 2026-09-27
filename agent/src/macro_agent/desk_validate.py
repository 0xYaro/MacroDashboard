"""What a desk submission may say, decided without a database.

The OpenClaw `macro-desk` agent sends JSON through a typed tool; `macro-agent
desk ... submit` decodes it here. Everything that can be refused from the
payload alone is refused here, one sentence per problem, so the agent can fix
the item and send it again. What needs the stored rows (does this catalyst
exist, is this risk elevated today) is decided in `desk.py`.

## The rules are the vault parser's, not a copy of them

A catalyst's identity is `macro.vault.external_key(name)`, its date comes out of
`macro.vault.parse_when`, whether that date is a guess out of
`macro.vault.is_estimated`, and its family and category out of
`macro.families.classify`. A structured `when` is rendered into the grammar the
Catalyst Monitor wrote ("2026-10-14 20:30 SGT", "late October 2026") and parsed
by the same function, so a desk row and a vault row for the same words are the
same row, down to which days "late October" covers.

## What the agent must state

- A `source_url`, https, on every catalyst, risk, driver and event. The one
  exception is a holding whose move has no sourced story: `no_clear_reason: true`
  stands in for the driver's source.
- Whether a date is `estimated`. A firm date is a claim the calendar treats as
  settled, so it is never inferred: an item without `estimated` is refused, a
  month or an unknown date can never be firm, and `estimated: false` beside
  words that hedge the date ("expected", "around 2026-10-20", "date TBD") is a
  contradiction and is refused.
- A date inside the window the feed covers: catalysts from two days ago to 120
  days ahead, holding events from yesterday to 14 days ahead.
- A `date_text` that agrees with `when`. Words that name a date must name the
  same one, give or take the day a time zone moves it.
- No control characters. PostgreSQL cannot store U+0000 in text or jsonb, and
  a lone surrogate cannot be encoded, so an item carrying either is refused
  and the archived copy of the submission has them replaced (`scrubbed`).
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .model import Check

SINGAPORE = ZoneInfo("Asia/Singapore")

CATALYST_PAST = timedelta(days=2)
CATALYST_AHEAD = timedelta(days=120)
EVENT_PAST = timedelta(days=1)
EVENT_AHEAD = timedelta(days=14)

SEVERITIES: tuple[str, ...] = ("watch", "elevated", "acute")
EVENT_KINDS: frozenset[str] = frozenset({"earnings", "product", "regulatory", "unlock", "other"})
RISK_ID = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_MINUTE = re.compile(r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})$")
_MONTH = re.compile(r"^(\d{4})-(\d{2})$")
_PARTS = ("early", "mid", "late", "end")
_MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June", "July", "August",
    "September", "October", "November", "December",
)

#: Fields a model might send that the desk computes itself. Sent anyway, they
#: are ignored and the reply says so; they are never written.
PRICE_FIELDS: frozenset[str] = frozenset(
    {"price", "change", "change_pct", "change_24h", "change_24h_pct", "pct", "move"}
)

Precision = Literal["exact", "day", "window", "month", "unknown"]


# ---------------------------------------------------------------------------
# Shared pieces
# ---------------------------------------------------------------------------


#: What PostgreSQL cannot store or no reader wants: C0 controls other than tab,
#: line feed and carriage return, DEL, and a lone UTF-16 surrogate.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff]")


def has_control(value: Any) -> bool:
    """Whether any string in `value`, a key included, carries a control character."""
    if isinstance(value, str):
        return _CONTROL.search(value) is not None
    if isinstance(value, dict):
        return any(has_control(k) or has_control(v) for k, v in value.items())
    if isinstance(value, list):
        return any(has_control(one) for one in value)
    return False


def scrubbed(value: Any) -> Any:
    """`value` with every control character replaced by U+FFFD, so it can be archived."""
    if isinstance(value, str):
        return _CONTROL.sub("\ufffd", value)
    if isinstance(value, dict):
        return {scrubbed(k): scrubbed(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrubbed(one) for one in value]
    return value


def control_refusal(key: str, index: int) -> Check:
    return refuse(key, f"items[{index}] was not applied: a text field carries a control "
                  "character (such as U+0000), which the desk cannot store. Remove it and "
                  "send the item again.")


def refuse(key: str, sentence: str) -> Check:
    return Check(key, False, sentence)


def note(key: str, sentence: str) -> Check:
    return Check(key, False, sentence, severity="warn")


def https_url(value: Any) -> str | None:
    """The URL if it is a well-formed https link, else None."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > 600 or any(ch.isspace() for ch in text):
        return None
    parts = urllib.parse.urlsplit(text)
    if parts.scheme != "https" or not parts.netloc or "." not in parts.netloc:
        return None
    return text


def one_line(value: Any, *, low: int, high: int) -> str | None:
    """A trimmed single-line string within the length bounds, else None."""
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not (low <= len(text) <= high) or "\n" in value.strip():
        return None
    return text


def prose(value: Any, *, high: int) -> str | None:
    """Reader-facing text: trimmed, whitespace collapsed, at most `high` characters."""
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not text or len(text) > high:
        return None
    return text


def str_list(value: Any, *, most: int, longest: int) -> tuple[str, ...] | None:
    if value is None:
        return ()
    if not isinstance(value, list) or len(value) > most:
        return None
    out: list[str] = []
    for one in value:
        text = one_line(one, low=1, high=longest)
        if text is None:
            return None
        if text not in out:
            out.append(text)
    return tuple(out)


def items_of(payload: Any, *, label: str) -> tuple[list[Any], list[Check]]:
    """The `items` list of a submission, or one refusal saying what was sent instead."""
    if not isinstance(payload, dict):
        return [], [refuse(f"{label}_payload", f"A {label} submission is a JSON object with an "
                           "`items` list, and this was not an object.")]
    items = payload.get("items")
    if not isinstance(items, list):
        return [], [refuse(f"{label}_payload", f"A {label} submission carries an `items` list, "
                           "and it is missing or not a list.")]
    if len(items) > 200:
        return [], [refuse(f"{label}_payload", f"A {label} submission carries at most 200 "
                           f"items; this one carries {len(items)}. Split it.")]
    return items, []


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class When:
    """A date, resolved exactly as the vault parser resolves the same words."""

    starts_at: datetime | None
    ends_at: datetime | None
    precision: Precision
    #: The words `vault.parse_when` was given: the canonical rendering of a
    #: structured `when`, or the agent's own `date_text`.
    canonical: str

    def as_json(self) -> dict[str, Any]:
        return {
            "starts_at": None if self.starts_at is None else self.starts_at.isoformat(),
            "ends_at": None if self.ends_at is None else self.ends_at.isoformat(),
            "date_precision": self.precision,
            "parsed_from": self.canonical,
        }


def _zone(raw: Any) -> ZoneInfo | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return ZoneInfo(raw.strip())
    except (ZoneInfoNotFoundError, ValueError):
        return None


def _day(raw: Any) -> date | None:
    if not isinstance(raw, str) or not _ISO_DAY.match(raw.strip()):
        return None
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        return None


def _sgt(day: date, clock: str, zone: ZoneInfo) -> str | None:
    try:
        hour, minute = (int(x) for x in clock.split(":"))
        local = datetime.combine(day, time(hour, minute), tzinfo=zone)
    except ValueError:
        return None
    return local.astimezone(SINGAPORE).strftime("%Y-%m-%d %H:%M") + " SGT"


def canonical_when(raw: Any) -> tuple[str | None, Precision | None, str | None]:
    """A structured `when` rendered in the Catalyst Monitor's grammar.

    Returns `(words, the precision they must parse to, a refusal)`. The shapes:

        {"date": "2026-10-14", "time": "08:30", "tz": "America/New_York"}   exact
        {"date": "2026-10-14"}                                              day
        {"from": "2026-10-06", "to": "2026-10-08"}                          window
        {"from": "2026-10-06T09:00", "to": "2026-10-08T17:00", "tz": "..."} exact span
        {"month": "2026-10", "part": "late"}                                month
        {"unknown": true}                                                   unknown

    A time is converted to Singapore time here, because the grammar's parser
    reads only the Singapore half of a line; the zone is the source's own and
    is required, so a New York 08:30 is never read as Singapore's.
    """
    if not isinstance(raw, dict):
        return None, None, (
            "`when` is an object: date, date with time and tz, from/to, month, or unknown"
        )
    keys = set(raw)
    if raw.get("unknown") is True and keys <= {"unknown"}:
        return "", "unknown", None
    if "date" in keys and keys <= {"date", "time", "tz"}:
        day = _day(raw.get("date"))
        if day is None:
            return None, None, f"`when.date` must be YYYY-MM-DD, not {raw.get('date')!r}"
        if raw.get("time") in (None, ""):
            if raw.get("tz"):
                return None, None, "`when.tz` is given without `when.time`; drop tz or add the time"
            return day.isoformat(), "day", None
        zone = _zone(raw.get("tz"))
        if zone is None:
            return None, None, (
                "`when.time` needs `when.tz`, an IANA zone such as America/New_York, so the "
                f"time is not misread; got tz {raw.get('tz')!r}"
            )
        clock = str(raw.get("time")).strip()
        if not re.match(r"^\d{2}:\d{2}$", clock):
            return None, None, f"`when.time` must be HH:MM, not {raw.get('time')!r}"
        words = _sgt(day, clock, zone)
        if words is None:
            return None, None, f"`when.time` {clock!r} is not a time of day"
        return words, "exact", None
    if keys and keys <= {"from", "to", "tz"} and {"from", "to"} <= keys:
        first, last = str(raw.get("from") or "").strip(), str(raw.get("to") or "").strip()
        a, b = _day(first), _day(last)
        if a is not None and b is not None:
            if raw.get("tz"):
                return None, None, "`when.tz` is only for times; a from/to of dates takes none"
            if b <= a:
                return None, None, f"`when.to` {last} must be after `when.from` {first}"
            return f"{a.isoformat()} to {b.isoformat()}", "window", None
        ma, mb = _ISO_MINUTE.match(first), _ISO_MINUTE.match(last)
        zone = _zone(raw.get("tz"))
        if ma and mb and zone is not None:
            da, db = _day(ma.group(1)), _day(mb.group(1))
            if da is None or db is None:
                return None, None, "`when.from` and `when.to` carry a date that does not exist"
            sa, sb = _sgt(da, ma.group(2), zone), _sgt(db, mb.group(2), zone)
            if sa is None or sb is None:
                return None, None, "`when.from` or `when.to` carries a time that does not exist"
            if sb <= sa:
                return None, None, f"`when.to` {last} must be after `when.from` {first}"
            return f"{sa} to {sb}", "exact", None
        return None, None, (
            "`when.from` and `when.to` are both YYYY-MM-DD, or both YYYY-MM-DDTHH:MM with "
            f"`when.tz`; got {first!r}, {last!r}, tz {raw.get('tz')!r}"
        )
    if "month" in keys and keys <= {"month", "part"}:
        found = _MONTH.match(str(raw.get("month") or "").strip())
        if not found or not 1 <= int(found.group(2)) <= 12:
            return None, None, f"`when.month` must be YYYY-MM, not {raw.get('month')!r}"
        name = f"{_MONTH_NAMES[int(found.group(2)) - 1]} {found.group(1)}"
        part = raw.get("part")
        if part in (None, ""):
            return name, "month", None
        pieces = str(part).strip().lower().split("-to-")
        if len(pieces) > 2 or any(one not in _PARTS for one in pieces):
            return None, None, (
                "`when.part` is early, mid, late, or a range such as mid-to-late; "
                f"not {part!r}"
            )
        return f"{'-to-'.join(pieces)} {name}", "month", None
    return None, None, (
        "`when` has an unrecognised shape. Use one of: {date}, {date, time, tz}, {from, to}, "
        "{from, to, tz} with times, {month, part}, {unknown: true}"
    )


def resolve_when(
    raw_when: Any, date_text: str | None, *, vault: Any
) -> tuple[When | None, str | None]:
    """The item's date, parsed by `vault.parse_when`, or a refusal sentence.

    A structured `when` wins when both are given; `date_text` is then kept only
    as the words the page shows. With no `when`, `date_text` is parsed exactly
    as a Catalyst Monitor line's "Date & Time" field was.
    """
    if raw_when is None:
        if not date_text:
            return None, "give `when` (preferred) or `date_text` in the Catalyst Monitor's grammar"
        starts, ends, precision = vault.parse_when(date_text)
        return When(starts, ends, precision, date_text), None
    words, expected, problem = canonical_when(raw_when)
    if problem is not None or words is None or expected is None:
        return None, problem or "`when` could not be read"
    starts, ends, precision = vault.parse_when(words)
    if precision != expected:  # pragma: no cover - a rendering bug, not an input fault
        return None, f"`when` rendered as {words!r} parsed as {precision}, not {expected}"
    when = When(starts, ends, precision, words)
    if date_text:
        disagreement = _disagreement(when, date_text, vault=vault)
        if disagreement:
            return None, disagreement
    return when, None


def _days(starts: datetime, ends: datetime | None) -> str:
    first, last = starts.date(), (ends or starts).date()
    return first.isoformat() if first == last else f"{first.isoformat()} to {last.isoformat()}"


def _disagreement(when: When, date_text: str, *, vault: Any) -> str | None:
    """Why `date_text` and `when` cannot both stand, or None.

    Words with no date ("release time TBD") say nothing to disagree with. Words
    that name one must overlap `when`'s days, give or take one day: a New York
    evening is the next morning in Singapore, and `date_text` is usually
    written in the source's own zone.
    """
    starts, ends, precision = vault.parse_when(date_text)
    if starts is None or precision == "unknown":
        return None
    said = _days(starts, ends)
    if when.starts_at is None:
        return (
            f"`when` says the date is unknown but `date_text` says {said}; send the date in "
            "`when`, or drop the date from `date_text`"
        )
    slack = timedelta(days=1)
    text_first, text_last = starts.date(), (ends or starts).date()
    when_first, when_last = when.starts_at.date(), (when.ends_at or when.starts_at).date()
    if text_first <= when_last + slack and when_first <= text_last + slack:
        return None
    return (
        f"`date_text` says {said} but `when` says {_days(when.starts_at, when.ends_at)}; make "
        "them name the same date, or drop `date_text`"
    )


def estimated_problem(
    estimated: Any, when: When, date_text: str, *, vault: Any
) -> str | None:
    """Why the item's `estimated` flag cannot stand, or None."""
    if not isinstance(estimated, bool):
        return (
            "`estimated` is required, true or false. A firm date is never inferred: say false only "
            "when the source publishes the date, true when it is expected or approximate"
        )
    if estimated:
        return None
    if when.precision in ("month", "unknown"):
        return (
            f"a {when.precision} date is always an estimate; set `estimated` true, or give the "
            "published day"
        )
    words = " ".join(part for part in (date_text, when.canonical) if part)
    if vault.is_estimated(words, when.precision):
        return (
            f"`estimated` is false but the date words hedge it ({date_text!r}). Set it true, or "
            "cite the page that publishes the date and drop the hedge"
        )
    return None


def window_problem(
    when: When, *, now: datetime, past: timedelta, ahead: timedelta, noun: str
) -> str | None:
    if when.starts_at is None:
        return None
    last = when.ends_at or when.starts_at
    if last < now - past:
        return (
            f"the {noun} ended on {last.date().isoformat()}, more than {past.days} day(s) ago; "
            "a passed event is retired, not submitted"
        )
    if when.starts_at > now + ahead:
        return (
            f"the {noun} starts on {when.starts_at.date().isoformat()}, more than {ahead.days} "
            "days ahead, outside what this feed covers"
        )
    return None


# ---------------------------------------------------------------------------
# Catalysts
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CatalystUpsert:
    index: int
    name: str
    key: str
    category: str
    family: str | None
    category_given: bool
    when: When
    date_text: str
    estimated: bool
    source_url: str
    affected: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CatalystRetire:
    index: int
    name: str | None
    catalyst_id: str | None
    reason: str


@dataclass(frozen=True, slots=True)
class CatalystRename:
    index: int
    name: str | None
    catalyst_id: str | None
    new_name: str
    new_key: str
    reason: str | None


CatalystOp = CatalystUpsert | CatalystRetire | CatalystRename


def _label(index: int, item: Any) -> str:
    name = item.get("name") if isinstance(item, dict) else None
    return f"items[{index}]" + (f" ({name!r})" if isinstance(name, str) and name else "")


def _reason(value: Any) -> str | None:
    text = prose(value, high=300)
    if text is None or len(text.split()) < 3:
        return None
    return text


def _id(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    return text if _UUID.match(text) else None


def catalyst_ops(
    payload: Any, *, now: datetime, vault: Any, families: Any
) -> tuple[list[CatalystOp], list[Check]]:
    """Decode a `desk catalysts submit` payload. Returns the ops and every refusal and note."""
    items, checks = items_of(payload, label="catalysts")
    ops: list[CatalystOp] = []
    categories = frozenset(families.CATEGORIES)
    for index, item in enumerate(items):
        label = _label(index, item)
        if not isinstance(item, dict):
            checks.append(refuse("desk_catalyst_item", f"{label} is not an object."))
            continue
        if has_control(item):
            checks.append(control_refusal("desk_catalyst_item", index))
            continue
        op = item.get("op", "upsert")
        if op == "upsert":
            found = _catalyst_upsert(index, item, label, now=now, vault=vault,
                                     families=families, categories=categories)
            if isinstance(found, CatalystUpsert):
                ops.append(found)
            else:
                checks.extend(found)
        elif op == "retire":
            reason = _reason(item.get("reason"))
            name = one_line(item.get("name"), low=4, high=200)
            ident = _id(item.get("id"))
            problems = []
            if name is None and ident is None:
                problems.append("names the catalyst by `name` or `id`")
            if reason is None:
                problems.append("says why in `reason`, at least three words (passed, cancelled, "
                                "moved outside the horizon, duplicate of ...)")
            if problems:
                checks.append(refuse("desk_catalyst_retire",
                                     f"{label}: a retirement {' and '.join(problems)}."))
                continue
            assert reason is not None
            ops.append(CatalystRetire(index, name, ident, reason))
        elif op == "rename":
            name = one_line(item.get("name"), low=4, high=200)
            ident = _id(item.get("id"))
            new_name = one_line(item.get("new_name"), low=4, high=200)
            if (name is None and ident is None) or new_name is None:
                checks.append(refuse(
                    "desk_catalyst_rename",
                    f"{label}: a rename names the catalyst by `name` or `id` and gives "
                    "`new_name`, 4 to 200 characters on one line."))
                continue
            new_key = vault.external_key(new_name)
            if name is not None and vault.external_key(name) == new_key:
                checks.append(refuse(
                    "desk_catalyst_rename",
                    f"{label}: {name!r} and {new_name!r} differ only in case or spacing, which "
                    "is the same catalyst; there is nothing to rename."))
                continue
            ops.append(CatalystRename(index, name, ident, new_name, new_key,
                                      prose(item.get("reason"), high=300)))
        else:
            checks.append(refuse("desk_catalyst_op",
                                 f"{label}: `op` is upsert, retire or rename, not {op!r}."))

    # Two items about the same catalyst in one submission: which one is meant is
    # a guess, so neither is written.
    seen: dict[str, int] = {}
    clash: set[int] = set()
    for one in ops:
        key = one.key if isinstance(one, CatalystUpsert) else (
            one.catalyst_id or vault.external_key(one.name or ""))
        if key in seen:
            clash |= {seen[key], one.index}
        else:
            seen[key] = one.index
    if clash:
        for one in ops:
            if one.index in clash:
                checks.append(refuse(
                    "desk_catalyst_duplicate",
                    f"items[{one.index}]: another item in this submission names the same catalyst "
                    "(names match ignoring case and spacing), so neither was applied. Send one "
                    "item per catalyst."))
        ops = [one for one in ops if one.index not in clash]
    return ops, checks


def _catalyst_upsert(
    index: int,
    item: dict[str, Any],
    label: str,
    *,
    now: datetime,
    vault: Any,
    families: Any,
    categories: frozenset[str],
) -> CatalystUpsert | list[Check]:
    problems: list[str] = []
    name = one_line(item.get("name"), low=4, high=200)
    if name is None:
        problems.append("`name` is 4 to 200 characters on one line")
    elif https_url(name) is not None:
        problems.append("`name` is a URL; the link goes in `source_url`")
    url = https_url(item.get("source_url"))
    if url is None:
        problems.append("`source_url` is required and must be an https link to the page that "
                        "states the date")
    raw_text = item.get("date_text")
    date_text = one_line(raw_text, low=1, high=200) if raw_text not in (None, "") else ""
    if date_text is None:
        problems.append("`date_text` is one line of at most 200 characters")
        date_text = ""
    when, when_problem = resolve_when(item.get("when"), date_text or None, vault=vault)
    if when_problem:
        problems.append(when_problem)
    affected = str_list(item.get("affected"), most=12, longest=60)
    if affected is None:
        problems.append("`affected` is a list of at most 12 short names (assets, sectors)")
    category_raw = item.get("category")
    if category_raw not in (None, "") and category_raw not in categories:
        problems.append(f"`category` {category_raw!r} is not one of the sixteen: "
                        + ", ".join(sorted(categories)))
    if when is not None:
        estimate = estimated_problem(item.get("estimated"), when, date_text, vault=vault)
        if estimate:
            problems.append(estimate)
        span = window_problem(when, now=now, past=CATALYST_PAST, ahead=CATALYST_AHEAD,
                              noun="catalyst")
        if span:
            problems.append(span)
    if problems or name is None or url is None or when is None or affected is None:
        return [refuse("desk_catalyst_refused", f"{label} was not applied: {'; '.join(problems)}.")]
    family, derived = families.classify(name)
    category = str(category_raw) if category_raw not in (None, "") else str(derived)
    return CatalystUpsert(
        index=index,
        name=name,
        key=vault.external_key(name),
        category=category,
        family=family,
        category_given=category_raw not in (None, ""),
        when=when,
        date_text=date_text or when.canonical,
        estimated=bool(item.get("estimated")),
        source_url=url,
        affected=affected,
    )


# ---------------------------------------------------------------------------
# Tail risks
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RiskUpsert:
    index: int
    risk_id: str
    name: str | None
    severity: str
    summary: str
    priced_in: str | None
    gauges: tuple[str, ...]
    source_url: str
    assets: tuple[str, ...] | None


@dataclass(frozen=True, slots=True)
class RiskRetire:
    index: int
    risk_id: str
    reason: str


RiskOp = RiskUpsert | RiskRetire


def severity_rank(severity: str) -> int:
    return SEVERITIES.index(severity)


def risk_ops(payload: Any) -> tuple[str | None, list[RiskOp], list[Check]]:
    """Decode a `desk tail-risks submit` payload: its kind, its ops, and every refusal."""
    items, checks = items_of(payload, label="tail_risks")
    kind = payload.get("kind") if isinstance(payload, dict) else None
    if kind not in ("weekly", "daily"):
        checks.append(refuse(
            "desk_tail_risk_kind",
            "`kind` is weekly (the Monday full scan) or daily (the recheck of the elevated and "
            f"acute risks), not {kind!r}. Nothing was applied."))
        return None, [], checks
    ops: list[RiskOp] = []
    seen: dict[str, int] = {}
    for index, item in enumerate(items):
        label = f"items[{index}]"
        if not isinstance(item, dict):
            checks.append(refuse("desk_tail_risk_item", f"{label} is not an object."))
            continue
        if has_control(item):
            checks.append(control_refusal("desk_tail_risk_item", index))
            continue
        risk_id = item.get("risk_id")
        if not isinstance(risk_id, str) or not (
            RISK_ID.match(risk_id) and 3 <= len(risk_id) <= 48
        ):
            checks.append(refuse(
                "desk_tail_risk_id",
                f"{label}: `risk_id` is a lower-case slug of 3 to 48 characters, words joined by "
                f"hyphens (iran-oil-chokepoints), not {risk_id!r}."))
            continue
        label = f"items[{index}] ({risk_id})"
        if risk_id in seen:
            checks.append(refuse(
                "desk_tail_risk_duplicate",
                f"{label}: items[{seen[risk_id]}] already names this risk; send one item per "
                "risk. This one was not applied."))
            continue
        seen[risk_id] = index
        op = item.get("op", "upsert")
        if op == "retire":
            reason = _reason(item.get("reason"))
            if reason is None:
                checks.append(refuse(
                    "desk_tail_risk_retire",
                    f"{label}: a retirement says why in `reason`, at least three words."))
                continue
            ops.append(RiskRetire(index, risk_id, reason))
            continue
        if op != "upsert":
            checks.append(refuse("desk_tail_risk_op",
                                 f"{label}: `op` is upsert or retire, not {op!r}."))
            continue
        problems: list[str] = []
        severity = item.get("severity")
        if severity not in SEVERITIES:
            problems.append(f"`severity` is acute, elevated or watch, not {severity!r}")
        summary = prose(item.get("summary"), high=700)
        if summary is None:
            problems.append("`summary` is required, at most 700 characters")
        priced_in = prose(item.get("priced_in"), high=500) if item.get("priced_in") else None
        if item.get("priced_in") and priced_in is None:
            problems.append("`priced_in` is at most 500 characters")
        url = https_url(item.get("source_url"))
        if url is None:
            problems.append("`source_url` is required and must be an https link")
        name = one_line(item.get("name"), low=4, high=160) if item.get("name") else None
        if item.get("name") and name is None:
            problems.append("`name` is 4 to 160 characters on one line")
        assets = str_list(item.get("assets"), most=12, longest=60) if "assets" in item else None
        if "assets" in item and assets is None:
            problems.append("`assets` is a list of at most 12 short names")
        gauges_raw = item.get("gauges") or []
        if isinstance(gauges_raw, dict):
            gauges_raw = list(gauges_raw)
        if not isinstance(gauges_raw, list) or not all(isinstance(g, str) for g in gauges_raw):
            problems.append(
                "`gauges` is a list of series keys (vix, move, hy_oas_bps); the desk reads their "
                "values from the dashboard, so no number is sent")
            gauges_raw = []
        if problems or summary is None or url is None:
            checks.append(refuse("desk_tail_risk_refused",
                                 f"{label} was not applied: {'; '.join(problems)}."))
            continue
        ops.append(RiskUpsert(
            index=index,
            risk_id=risk_id,
            name=name,
            severity=str(severity),
            summary=summary,
            priced_in=priced_in,
            gauges=tuple(dict.fromkeys(g.strip() for g in gauges_raw if g.strip())),
            source_url=url,
            assets=assets,
        ))
    return kind, ops, checks


# ---------------------------------------------------------------------------
# Holdings
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HoldingEvent:
    kind: str
    name: str
    key: str
    when: When
    date_text: str
    estimated: bool
    source_url: str


@dataclass(frozen=True, slots=True)
class HoldingItem:
    index: int
    symbol: str
    source: str | None
    driver_summary: str | None
    material: bool
    source_url: str | None
    events: tuple[HoldingEvent, ...] = field(default_factory=tuple)
    #: Price fields the model sent anyway. Ignored; named in the reply.
    ignored: tuple[str, ...] = ()
    #: The agent looked and found no sourced story for the move. A driver is then
    #: optional and needs no `source_url`.
    no_clear_reason: bool = False


def holding_items(
    payload: Any, *, now: datetime, vault: Any
) -> tuple[list[HoldingItem], list[Check]]:
    """Decode a `desk holdings submit` payload. Prices are never read from it."""
    items, checks = items_of(payload, label="holdings")
    out: list[HoldingItem] = []
    seen: dict[tuple[str, str | None], int] = {}
    for index, item in enumerate(items):
        label = f"items[{index}]"
        if not isinstance(item, dict):
            checks.append(refuse("desk_holding_item", f"{label} is not an object."))
            continue
        if has_control(item):
            checks.append(control_refusal("desk_holding_item", index))
            continue
        symbol = one_line(item.get("symbol"), low=1, high=24)
        if symbol is None:
            checks.append(refuse("desk_holding_symbol", f"{label}: `symbol` is required."))
            continue
        symbol = symbol.upper()
        label = f"items[{index}] ({symbol})"
        source = item.get("source")
        if source not in (None, "accounting", "manual", "watch"):
            checks.append(refuse("desk_holding_source",
                                 f"{label}: `source` is accounting, manual or watch, or omitted."))
            continue
        if (symbol, source) in seen:
            checks.append(refuse(
                "desk_holding_duplicate",
                f"{label}: items[{seen[(symbol, source)]}] already covers this name; send one "
                "item per name. This one was not applied."))
            continue
        seen[(symbol, source)] = index
        problems: list[str] = []
        driver = prose(item.get("driver_summary"), high=700) if item.get("driver_summary") else None
        if item.get("driver_summary") and driver is None:
            problems.append("`driver_summary` is at most 700 characters")
        url = https_url(item.get("source_url")) if item.get("source_url") else None
        if item.get("source_url") and url is None:
            problems.append("`source_url` must be an https link")
        no_clear = item.get("no_clear_reason", False)
        if not isinstance(no_clear, bool):
            problems.append("`no_clear_reason` is true or false")
            no_clear = False
        if driver and url is None and not no_clear:
            problems.append("a `driver_summary` cites its page in `source_url`, or sets "
                            "`no_clear_reason: true` when no source explains the move")
        material = item.get("material", False)
        if not isinstance(material, bool):
            problems.append("`material` is true or false")
            material = False
        if material and not driver and not no_clear:
            problems.append("a material move says why in `driver_summary`, or sets "
                            "`no_clear_reason: true` when no sourced story explains it")
        events: list[HoldingEvent] = []
        raw_events = item.get("events") or []
        if not isinstance(raw_events, list):
            problems.append("`events` is a list")
            raw_events = []
        for e_index, raw in enumerate(raw_events):
            found = _event(raw, now=now, vault=vault)
            if isinstance(found, str):
                problems.append(f"events[{e_index}]: {found}")
            else:
                events.append(found)
        if problems:
            checks.append(refuse("desk_holding_refused",
                                 f"{label} was not applied: {'; '.join(problems)}."))
            continue
        ignored = tuple(sorted(PRICE_FIELDS & set(item)))
        if ignored:
            checks.append(note(
                "desk_holding_price_ignored",
                f"{label}: {', '.join(ignored)} ignored. The price and the 24h change are "
                "computed by the desk from market data; send only the reason for the move."))
        out.append(HoldingItem(index, symbol, source, driver, bool(material), url,
                               tuple(events), ignored, no_clear))
    return out, checks


def _event(raw: Any, *, now: datetime, vault: Any) -> HoldingEvent | str:
    if not isinstance(raw, dict):
        return "an event is an object"
    kind = raw.get("kind")
    if kind not in EVENT_KINDS:
        return f"`kind` is one of {', '.join(sorted(EVENT_KINDS))}, not {kind!r}"
    name = one_line(raw.get("name"), low=4, high=200)
    if name is None:
        return "`name` is 4 to 200 characters on one line"
    url = https_url(raw.get("source_url"))
    if url is None:
        return f"{name!r} needs an https `source_url`"
    raw_text = raw.get("date_text")
    date_text = one_line(raw_text, low=1, high=200) if raw_text not in (None, "") else ""
    if date_text is None:
        return "`date_text` is one line of at most 200 characters"
    when, problem = resolve_when(raw.get("when"), date_text or None, vault=vault)
    if problem or when is None:
        return f"{name!r}: {problem}"
    if when.starts_at is None:
        return f"{name!r} has no date, and an upcoming event needs one"
    estimate = estimated_problem(raw.get("estimated"), when, date_text, vault=vault)
    if estimate:
        return f"{name!r}: {estimate}"
    span = window_problem(when, now=now, past=EVENT_PAST, ahead=EVENT_AHEAD, noun="event")
    if span:
        return f"{name!r}: {span}"
    return HoldingEvent(
        kind=str(kind),
        name=name,
        key=vault.external_key(name),
        when=when,
        date_text=date_text or when.canonical,
        estimated=bool(raw.get("estimated")),
        source_url=url,
    )


def utc(moment: datetime) -> datetime:
    return moment.astimezone(UTC)
