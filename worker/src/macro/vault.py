"""The three markdown files the OpenClaw crons write, parsed into `macro_core`.

Three OpenClaw crons write them, each an AI web search:
`daily-market-catalyst-monitor` writes `Catalyst Monitor.md` daily at 08:45
Singapore, `daily-holdings-watch` writes `Market Watchlist.md` daily at 09:15,
and the Blackswan cron writes `Blackswan Events.md` weekly, Mondays at 06:00.
They are not rewritten and keep their Telegram delivery. They write in a line
grammar their own prompts enforce, and the worker reads all three on every
09:30 run, so the weekly file is re-read six days out of seven unchanged: a
tail risk is dated by the file's own modification time, not by the run.

The vault is read-only to this system. Nothing here opens a file for writing.

## The grammar, as the files actually carry it

`Catalyst Monitor.md`, under `## Tracked Catalysts` and `## Newly Added`:

    - Catalyst: <name>. Date & Time: <when>. Source: <source> <url>. Affected: <a, b, c>.

`Market Watchlist.md`, under `## Holdings`:

    - MU
    - 012330 — Hyundai Mobis (KRX)

and under `## Active Changes` and `## Newly Flagged`:

    - Ticker: MU. Date & Time: 2026-09-21 09:15 SGT. Change: +3.92%.
      Summary: <text>. Source: <source> <url>

`Blackswan Events.md`, under `## Active Risks` and `## Newly Flagged`:

    - Risk: <name>. Date & Time: <when>. Severity: acute. Summary: <text>.
      Priced-in: <text>. Source: <source> <url>. Assets: <a, b, c>.

## A parse failure is a finding, never a silent zero

A line that does not match, a section that is missing, a severity word the
vocabulary does not carry: each one opens a `macro_quality.finding` naming the
file and the line. A file that parses to nothing is itself a finding, because a
Catalyst Monitor with no catalysts and a Catalyst Monitor that could not be read
are the same empty table and must not be the same report.
"""

from __future__ import annotations

import calendar
import hashlib
import re
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from .families import classify
from .model import (
    Catalyst,
    DatePrecision,
    Finding,
    Holding,
    TailRisk,
    VaultFeed,
    WatchlistChange,
)
from .units import maybe_dec

SINGAPORE = ZoneInfo("Asia/Singapore")

#: Which book a holding belongs to. The Watchlist does not say, and the
#: Strategy section that does is authored weekly, so the split is asserted here
#: from the tickers the journal has classified as crypto-policy instruments. A
#: ticker this list does not name is `equities`, and a ticker that turns out to
#: be crypto is a one-line change rather than a parse failure.
CRYPTO_TICKERS: frozenset[str] = frozenset({"RIOT", "GLXY", "MARA", "COIN", "MSTR", "HUT", "CLSK"})

SEVERITIES: frozenset[str] = frozenset({"acute", "elevated", "watch"})

#: Every check reading the three notes can raise. Only a run that read the
#: notes evaluated them, so only that run may close them.
CHECK_KEYS: tuple[str, ...] = (
    "vault_file_unreadable",
    "catalyst_name_collision",
    "catalyst_monitor_section_missing",
    "catalyst_line_unparsed",
    "catalyst_monitor_empty",
    "watchlist_section_missing",
    "holding_line_unparsed",
    "watchlist_empty",
    "change_line_unparsed",
    "change_date_unparsed",
    "blackswan_section_missing",
    "tail_risk_line_unparsed",
    "tail_risk_severity_unknown",
    "blackswan_empty",
)

_SECTION = re.compile(r"^##\s+(?P<title>.+?)\s*$")
_BULLET = re.compile(r"^\s*-\s+(?P<body>.+?)\s*$")
_URL = re.compile(r"https?://\S+")

#: `Catalyst: ... Date & Time: ... Source: ... Affected: ...`, each field ending
#: at the next field's label or at the end of the line. Written as one pattern
#: per field rather than one pattern per line, because a cron that adds a field
#: should cost the new field and not every catalyst.
_FIELD = r"(?:^|\.\s+){label}:\s*(?P<{name}>.+?)(?=\.\s+(?:{stops}):|\.?\s*$)"

_CATALYST_LABELS = ("Catalyst", "Date & Time", "Source", "Affected")
_CHANGE_LABELS = ("Ticker", "Date & Time", "Change", "Summary", "Source")
_RISK_LABELS = ("Risk", "Date & Time", "Severity", "Summary", "Priced-in", "Source", "Assets")

_ISO_DATE = re.compile(r"(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})")
_ISO_DATE_TIME = re.compile(
    r"(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})\s+(?P<hour>\d{1,2}):(?P<minute>\d{2})"
)
#: "October 2026", "late October 2026", "mid-to-late October 2026",
#: "end-September 2026". The part of the month is captured, not skipped: it is
#: the only thing the line says about the day, and dropping it filed every
#: "late October" earnings print on 1 October.
_MONTH_YEAR = re.compile(
    r"(?:\b(?P<first>early|mid|late|end)(?:[- ]to[- ](?P<last>early|mid|late|end))?"
    r"[- ](?:of[- ])?)?"
    r"\b(?P<month>January|February|March|April|May|June|July|"
    r"August|September|October|November|December)\s+(?P<year>\d{4})",
    re.IGNORECASE,
)

#: Which days a part of the month covers. "end" is read as "late": the cron
#: uses both for the last third of the month.
_PART_DAYS: dict[str, tuple[int, int]] = {
    "early": (1, 10),
    "mid": (11, 20),
    "late": (21, 31),
    "end": (21, 31),
}

#: Words that make the date itself an estimate, as opposed to a firm date whose
#: time is not yet known. "2026-09-25; release time TBD" is a firm day;
#: "around 2026-10-20 expected; exact date/time TBD" is not. "around" counts
#: only in front of a date, because "around 01:00 SGT" hedges the time.
_ESTIMATED = re.compile(
    r"\b(?:expected|estimated|tentative|unconfirmed|provisional|approx(?:imately)?)\b|"
    r"\baround\s+\d{4}-\d{2}-\d{2}|"
    r"\bdate\b[^;.]*?\bTBD\b",
    re.IGNORECASE,
)

_MONTHS = {
    name.lower(): index
    for index, name in enumerate(
        (
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ),
        start=1,
    )
}


def _fields(body: str, labels: tuple[str, ...]) -> dict[str, str] | None:
    """Split one bullet into its labelled fields, or `None` when a label is absent."""
    out: dict[str, str] = {}
    for label in labels:
        stops = "|".join(re.escape(other) for other in labels if other != label)
        pattern = _FIELD.format(label=re.escape(label), name="value", stops=stops)
        found = re.search(pattern, body)
        if found is None:
            return None
        out[label] = found.group("value").strip().rstrip(".").strip()
    return out


def _sections(text: str) -> dict[str, list[str]]:
    """Every `## ` section and the bullet bodies under it, in file order."""
    out: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        heading = _SECTION.match(line)
        if heading:
            current = heading.group("title").strip()
            out.setdefault(current, [])
            continue
        if current is None:
            continue
        bullet = _BULLET.match(line)
        if bullet:
            out[current].append(bullet.group("body"))
    return out


# ---------------------------------------------------------------------------
# Catalyst Monitor.md
# ---------------------------------------------------------------------------


def external_key(name: str) -> str:
    """A stable hash of the catalyst name, so a reschedule updates rather than duplicates.

    The name is the identity, because the date is the thing that moves. Case and
    inner whitespace are normalised so that a cron re-wording "Q3 2026 earnings"
    to "Q3  2026 earnings" does not fork the row.
    """
    normalised = re.sub(r"\s+", " ", name).strip().lower()
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()[:32]


def parse_catalysts(text: str, seen_on: date) -> tuple[list[Catalyst], list[Finding]]:
    """`Catalyst Monitor.md` into `macro_core.catalyst` rows."""
    sections = _sections(text)
    findings: list[Finding] = []
    bodies: list[str] = []
    for title in ("Tracked Catalysts", "Newly Added"):
        if title not in sections:
            findings.append(
                _missing_section("Catalyst Monitor.md", title, "catalyst_monitor_section_missing")
            )
            continue
        bodies.extend(sections[title])

    seen: dict[str, Catalyst] = {}
    for body in bodies:
        fields = _fields(body, _CATALYST_LABELS)
        if fields is None:
            findings.append(_unparsed("Catalyst Monitor.md", "catalyst_line_unparsed", body))
            continue
        name = fields["Catalyst"]
        when = fields["Date & Time"]
        starts_at, ends_at, precision = parse_when(when)
        key = external_key(name)
        previous = seen.get(key)
        if previous is not None and previous.starts_at != starts_at:
            # `external_key` is a hash of the name, by contract, so that a
            # reschedule updates rather than duplicates. Two live catalysts
            # sharing a name are therefore indistinguishable, and the later one
            # wins. That is a real loss and it is reported rather than hidden:
            # the file currently carries two "U.S. 2Y/5Y/7Y Treasury auction
            # cluster" rows a month apart.
            findings.append(
                Finding(
                    check_key="catalyst_name_collision",
                    subject=name,
                    sentence=(
                        f"Two tracked catalysts are both named {name!r}, dated "
                        f"{_stamp(previous.starts_at)} and {_stamp(starts_at)}. The external key "
                        "is a hash of the name so that a reschedule updates rather than "
                        "duplicates, so only the later row is stored and a trigger clause bound "
                        "to the earlier one now points at the later date."
                    ),
                    severity=2,
                )
            )
        family, category = classify(name)
        seen[key] = Catalyst(
            external_key=key,
            name=name,
            starts_at=starts_at,
            ends_at=ends_at,
            date_precision=precision,
            source_url=_first_url(fields["Source"]),
            affected=_split_list(fields["Affected"]),
            status="tracked",
            seen_on=seen_on,
            raw_date_text=when,
            date_estimated=is_estimated(when, precision),
            family=family,
            category=category,
        )

    if not seen and not findings:
        findings.append(_empty("Catalyst Monitor.md", "catalyst_monitor_empty", "catalysts"))
    return list(seen.values()), findings


def parse_when(text: str) -> tuple[datetime | None, datetime | None, DatePrecision]:
    """The catalyst's date, its end, and how precisely the line stated it.

    Five shapes appear in the live file and each gets its own precision,
    because "2026-09-22 10:20 ET / 2026-09-22 22:20 SGT" and "late October 2026
    expected" are not the same claim and a scenario clause bound to one should
    not be voided on the other's schedule.

    A line that states a time states it twice, in the source's own zone and in
    Singapore time, separated by a slash. The Singapore half is taken, because
    the cron has already applied the conversion and repeating it here would mean
    holding a table of exchange time zones. Taking the first stamp on the line
    instead reads the source's own local time as Singapore time, which for a
    10:20 New York speech files the event twelve hours early.

    A line that states only a date is anchored at UTC midnight, not at Singapore
    midnight. A date with no time has no time zone either, and putting it on the
    operator's clock would file a 1 October event under 30 September in UTC and
    make every horizon comparison a day out. `date_precision` carries the
    imprecision instead, which is what it is for.
    """
    segment, timed = _singapore_segment(text)

    stamps = list(_ISO_DATE_TIME.finditer(segment)) if timed else []
    if stamps:
        first = _sgt(stamps[0])
        last = _sgt(stamps[-1]) if len(stamps) > 1 else None
        return first, last, "exact"

    days = list(_ISO_DATE.finditer(segment))
    if days:
        first = _midnight(days[0])
        if len(days) > 1:
            return first, _midnight(days[-1]), "window"
        return first, None, "day"

    month = _MONTH_YEAR.search(segment)
    if month:
        year = int(month.group("year"))
        number = _MONTHS[month.group("month").lower()]
        last_day = calendar.monthrange(year, number)[1]
        opening = (month.group("first") or "").lower()
        closing = (month.group("last") or opening).lower()
        start_day = _PART_DAYS[opening][0] if opening else 1
        end_day = min(_PART_DAYS[closing][1], last_day) if closing else last_day
        return (
            datetime(year, number, start_day, tzinfo=UTC),
            datetime(year, number, end_day, tzinfo=UTC),
            "month",
        )

    return None, None, "unknown"


def is_estimated(text: str, precision: DatePrecision) -> bool:
    """Whether the line's date is a guess rather than a scheduled date.

    A month-only date always is. A day or a time is when the line hedges the
    date itself ("2026-11-03 expected", "around 2026-10-20"), and not when it
    only says the time is not yet published ("2026-09-25; release time TBD").
    """
    if precision == "month":
        return True
    return _ESTIMATED.search(text) is not None


def _singapore_segment(text: str) -> tuple[str, bool]:
    """The half of the line that is already on the operator's clock, and whether its times count.

    The cron writes `<source local> / <Singapore>`. When both halves are there,
    only the Singapore one is read. When there is no `SGT` marker at all the
    whole line is read for its dates only: its times are in a zone the line
    names in words this parser does not hold a table for.

    A line with two events names two Singapore times, and the second often
    carries no date because it shares the first one's: "statement 2026-09-16
    14:00 ET / 2026-09-17 02:00 SGT, press conference 14:30 ET / 02:30 SGT".
    The last part that says SGT *and* carries a date is the one read. Reading
    the last SGT part regardless left every FOMC of the year undated.

    When no Singapore part carries a date ("2026-10-25 03:00 CEST / 10:00
    SGT"), the whole line is read for its date only, with times ignored. Its
    only stamp is in the source's zone, and filing that as Singapore time is the
    twelve-hour error the split exists to prevent; the day is still right.

    The slash is the separator the cron uses and there is never a URL in this
    field, so splitting on it is safe here and would not be in the `Source` one.
    """
    if "SGT" not in text:
        # Every stamp is in the source's own zone ("08:30 ET"). Until 30 July the
        # monitor wrote lines this way, and reading them as Singapore time filed
        # every New York print twelve hours early. Keep the day, drop the time.
        return text, False
    if "/" not in text:
        return text, True
    parts = [part for part in text.split("/") if "SGT" in part]
    for part in reversed(parts):
        if _ISO_DATE.search(part):
            return part, True
    return text, False


def _sgt(match: re.Match[str]) -> datetime:
    return datetime(
        int(match.group("year")),
        int(match.group("month")),
        int(match.group("day")),
        int(match.group("hour")),
        int(match.group("minute")),
        tzinfo=SINGAPORE,
    ).astimezone(UTC)


def _midnight(match: re.Match[str]) -> datetime:
    return datetime.combine(
        date(int(match.group("year")), int(match.group("month")), int(match.group("day"))),
        time(0, 0),
        tzinfo=UTC,
    )


# ---------------------------------------------------------------------------
# Market Watchlist.md
# ---------------------------------------------------------------------------


def parse_watchlist(
    text: str, *, crypto: Iterable[str] = CRYPTO_TICKERS
) -> tuple[list[Holding], list[WatchlistChange], list[Finding]]:
    """`Market Watchlist.md` into holdings and dated changes."""
    sections = _sections(text)
    findings: list[Finding] = []
    crypto_set = {one.upper() for one in crypto}

    holdings: list[Holding] = []
    if "Holdings" not in sections:
        findings.append(
            _missing_section("Market Watchlist.md", "Holdings", "watchlist_section_missing")
        )
    else:
        for body in sections["Holdings"]:
            ticker, _, label = body.partition("—")
            ticker = ticker.strip()
            if not ticker:
                findings.append(_unparsed("Market Watchlist.md", "holding_line_unparsed", body))
                continue
            holdings.append(
                Holding(
                    ticker=ticker,
                    label=label.strip() or ticker,
                    book="crypto" if ticker.upper() in crypto_set else "equities",
                    active=True,
                )
            )
        if not holdings:
            findings.append(_empty("Market Watchlist.md", "watchlist_empty", "holdings"))

    changes: dict[tuple[str, datetime], WatchlistChange] = {}
    for title in ("Active Changes", "Newly Flagged"):
        for body in sections.get(title, []):
            fields = _fields(body, _CHANGE_LABELS)
            if fields is None:
                findings.append(_unparsed("Market Watchlist.md", "change_line_unparsed", body))
                continue
            observed = parse_when(fields["Date & Time"])[0]
            if observed is None:
                findings.append(
                    _unparsed(
                        "Market Watchlist.md",
                        "change_date_unparsed",
                        body,
                        why=f"the Date & Time field {fields['Date & Time']!r} carries no date",
                    )
                )
                continue
            changes[(fields["Ticker"], observed)] = WatchlistChange(
                ticker=fields["Ticker"],
                observed_at=observed,
                change_pct=_percent(fields["Change"]),
                summary=fields["Summary"],
                source_url=_first_url(fields["Source"]),
            )
    return holdings, list(changes.values()), findings


def _percent(text: str) -> Decimal | None:
    """A change like `+3.92%` or `-5.27%` into a number, or null with no guess."""
    cleaned = text.replace("−", "-").replace("%", "").strip()
    return maybe_dec(cleaned)


# ---------------------------------------------------------------------------
# Blackswan Events.md
# ---------------------------------------------------------------------------


def parse_tail_risks(text: str, seen_on: date) -> tuple[list[TailRisk], list[Finding]]:
    """`Blackswan Events.md` into `macro_core.tail_risk` rows.

    `seen_on` is the day this version of the note was written, not the day it
    was read: `read_all` passes the file's modification date.
    """
    sections = _sections(text)
    findings: list[Finding] = []
    bodies: list[str] = []
    for title in ("Active Risks", "Newly Flagged"):
        if title not in sections:
            findings.append(
                _missing_section("Blackswan Events.md", title, "blackswan_section_missing")
            )
            continue
        bodies.extend(sections[title])

    seen: dict[str, TailRisk] = {}
    for body in bodies:
        fields = _fields(body, _RISK_LABELS)
        if fields is None:
            findings.append(_unparsed("Blackswan Events.md", "tail_risk_line_unparsed", body))
            continue
        severity = fields["Severity"].strip().lower()
        if severity not in SEVERITIES:
            findings.append(
                _unparsed(
                    "Blackswan Events.md",
                    "tail_risk_severity_unknown",
                    body,
                    why=(
                        f"the Severity field reads {fields['Severity']!r}, which is not one of "
                        "acute, elevated or watch"
                    ),
                )
            )
            continue
        name = fields["Risk"]
        key = external_key(name)
        seen[key] = TailRisk(
            key=key,
            name=name,
            severity=severity,  # type: ignore[arg-type]
            summary=fields["Summary"],
            priced_in=fields["Priced-in"],
            assets=_split_list(fields["Assets"]),
            source_url=_first_url(fields["Source"]),
            seen_on=seen_on,
        )

    if not seen and not findings:
        findings.append(_empty("Blackswan Events.md", "blackswan_empty", "tail risks"))
    return list(seen.values()), findings


# ---------------------------------------------------------------------------
# The three files together
# ---------------------------------------------------------------------------


def read_all(
    *,
    catalyst_file: Path | None,
    watchlist_file: Path | None,
    blackswan_file: Path | None,
    seen_on: date,
) -> VaultFeed:
    """Parse all three cron files. A missing file is a finding, not an exception.

    Each file is judged on its own for `VaultFeed`'s `*_whole` flags: one file
    failing never makes another look empty.

    A `None` path is a feed the desk now writes (`MACRO_FEED_SOURCE_*=desk`):
    the note is not opened, yields nothing and is never whole, so it cannot
    retire or deactivate a row the desk wrote.
    """
    feed = VaultFeed()

    catalyst_text = None if catalyst_file is None else _read(catalyst_file, feed.findings)
    if catalyst_text is not None:
        catalysts, findings = parse_catalysts(catalyst_text, seen_on)
        feed.catalysts.extend(catalysts)
        feed.findings.extend(findings)
        feed.catalysts_whole = _whole(catalysts, findings, "catalyst_monitor_section_missing")

    watchlist_text = None if watchlist_file is None else _read(watchlist_file, feed.findings)
    if watchlist_text is not None:
        holdings, changes, findings = parse_watchlist(watchlist_text)
        feed.holdings.extend(holdings)
        feed.changes.extend(changes)
        feed.findings.extend(findings)
        feed.holdings_whole = _whole(holdings, findings, "watchlist_section_missing")

    blackswan_text = None if blackswan_file is None else _read(blackswan_file, feed.findings)
    if blackswan_text is not None and blackswan_file is not None:
        # Weekly, so the run date would claim a listing the cron never made.
        risks, findings = parse_tail_risks(
            blackswan_text, written_on(blackswan_file) or seen_on
        )
        feed.tail_risks.extend(risks)
        feed.findings.extend(findings)
        feed.tail_risks_whole = _whole(risks, findings, "blackswan_section_missing")

    return feed


def _whole(rows: Sequence[object], findings: Iterable[Finding], section_missing: str) -> bool:
    """True when a file yielded rows and carried every section that feeds them."""
    return bool(rows) and not any(one.check_key == section_missing for one in findings)


def retirements(
    stored: Iterable[tuple[str, str]],
    seen_keys: Iterable[str],
    *,
    seen_on: date,
) -> list[tuple[str, str]]:
    """Catalysts the monitor stopped listing, and the reason to record.

    A catalyst that leaves the file has passed or been dropped by the cron, and
    the premise-moved rule needs it marked `retired` rather than deleted: a
    trigger clause bound to it is voided and kept, never removed.
    """
    present = set(seen_keys)
    return [
        (
            key,
            f"the Catalyst Monitor stopped listing it on {seen_on.isoformat()}",
        )
        for key, status in stored
        if status == "tracked" and key not in present
    ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read(path: Path, findings: list[Finding]) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        findings.append(
            Finding(
                check_key="vault_file_unreadable",
                subject=str(path),
                sentence=(
                    f"{path} could not be read on this run ({exc}), so the rows it feeds kept "
                    "whatever they last held and nothing was reset to empty."
                ),
                severity=1,
            )
        )
        return None


def written_on(path: Path) -> date | None:
    """The Singapore day the cron last wrote `path`, from its modification time.

    `Blackswan Events.md` carries no date of its own, and it is written once a
    week while the worker reads it every day. The file's own clock is the only
    honest answer to "when did the note last list this".
    """
    try:
        modified = path.stat().st_mtime
    except OSError:
        return None
    return datetime.fromtimestamp(modified, tz=SINGAPORE).date()


def _stamp(moment: datetime | None) -> str:
    return "an unstated date" if moment is None else moment.date().isoformat()


def _split_list(text: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in text.split(",") if part.strip())


def _first_url(text: str) -> str:
    found = _URL.search(text)
    return found.group(0).rstrip(".,;") if found else ""


def _missing_section(file_name: str, title: str, check_key: str) -> Finding:
    return Finding(
        check_key=check_key,
        subject=f"{file_name} ## {title}",
        sentence=(
            f"{file_name} has no `## {title}` section, so the rows that section feeds were not "
            "read on this run; they are missing rather than empty."
        ),
        severity=1,
    )


def _unparsed(file_name: str, check_key: str, body: str, *, why: str | None = None) -> Finding:
    excerpt = body if len(body) <= 120 else body[:117] + "..."
    reason = why or "it does not match the line grammar the cron's own prompt enforces"
    return Finding(
        check_key=check_key,
        subject=f"{file_name}: {excerpt}",
        sentence=(
            f"A line in {file_name} was not parsed because {reason}, so it reached no table; "
            "the row is absent rather than zero."
        ),
        severity=2,
    )


def _empty(file_name: str, check_key: str, noun: str) -> Finding:
    return Finding(
        check_key=check_key,
        subject=file_name,
        sentence=(
            f"{file_name} parsed cleanly and yielded no {noun}, which is either a cron that "
            "wrote an empty file or a grammar that has drifted; it is reported rather than "
            "stored as zero."
        ),
        severity=1,
    )


Book = Literal["equities", "crypto"]
