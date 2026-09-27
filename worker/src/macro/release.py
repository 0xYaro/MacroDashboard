"""The release-rule grammar `config/series.yaml` declares, parsed and evaluated.

    release_rule := <base> [ "; " <key> "=" <value> ]*

Six bases, each naming the calendar the series advances on:

    us_session       one observation per US equity trading session (NYSE calendar)
    us_business_day  one per US federal business day (SIFMA / Treasury calendar)
    calendar_day     one per calendar day, UTC; a 24-hour market has no closed days
    weekly           one per week
    monthly          one per calendar month
    event            no schedule; `next_expected` is null and the row is never overdue,
                     unless the 09:30 agents re-read it daily (see `freshness`)

Two keys:

    asof=     which day the observation describes: `mon`..`fri` for weekly, `eom`
              for monthly, omitted for the three daily bases
    release=  when that observation becomes readable; omitted means same day.
              A weekday name, `+Nb` (N US business days), `+Nd` (N calendar
              days), or `next_month_day:N`

`next_expected` advances the base's calendar one step past the latest
observation's `asof`, then applies `release`. That is why the rule is declared
rather than guessed from the gap between the last two rows: a weekly series four
days from its next print is `due`, and a rule inferred from row spacing cannot
tell that apart from a series that has stopped.

This module is pure. Nothing here reads the wall clock.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from . import clock

BASES: frozenset[str] = frozenset(
    {"us_session", "us_business_day", "calendar_day", "weekly", "monthly", "event"}
)

_WEEKDAYS: dict[str, int] = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}

_OFFSET = re.compile(r"^\+(\d+)([bd])$")
_NEXT_MONTH_DAY = re.compile(r"^next_month_day:(\d{1,2})$")


class RuleRefused(Exception):
    """A release rule this worker will not guess at. The message is the diagnosis."""


@dataclass(frozen=True, slots=True)
class ReleaseRule:
    """One parsed `release_rule`."""

    raw: str
    base: str
    asof_key: str | None
    release_key: str | None

    @property
    def is_scheduled(self) -> bool:
        """False for `event`, which promises nothing and is never overdue."""
        return self.base != "event"


def parse(rule: str) -> ReleaseRule:
    """Read one `release_rule` string, refusing anything the grammar does not cover."""
    parts = [p.strip() for p in rule.split(";") if p.strip()]
    if not parts:
        raise RuleRefused(f"release_rule {rule!r} is empty")
    base = parts[0]
    if base not in BASES:
        raise RuleRefused(f"release_rule {rule!r}: base {base!r} is not one of {sorted(BASES)}")
    asof_key: str | None = None
    release_key: str | None = None
    for part in parts[1:]:
        if "=" not in part:
            raise RuleRefused(f"release_rule {rule!r}: {part!r} is not a key=value pair")
        key, _, value = part.partition("=")
        key, value = key.strip(), value.strip()
        if key == "asof":
            asof_key = value
        elif key == "release":
            release_key = value
        else:
            raise RuleRefused(f"release_rule {rule!r}: unknown key {key!r}")

    if base == "weekly":
        if asof_key not in _WEEKDAYS:
            raise RuleRefused(f"release_rule {rule!r}: weekly needs asof=mon..fri")
    elif base == "monthly":
        if asof_key != "eom":
            raise RuleRefused(f"release_rule {rule!r}: monthly needs asof=eom")
    elif asof_key is not None:
        raise RuleRefused(f"release_rule {rule!r}: base {base!r} takes no asof key")

    if release_key is not None:
        _validate_release(rule, release_key)
    return ReleaseRule(raw=rule, base=base, asof_key=asof_key, release_key=release_key)


def _validate_release(rule: str, value: str) -> None:
    if value in _WEEKDAYS or _OFFSET.match(value) or _NEXT_MONTH_DAY.match(value):
        return
    raise RuleRefused(
        f"release_rule {rule!r}: release={value!r} is not a weekday, +Nb, +Nd or next_month_day:N"
    )


# ---------------------------------------------------------------------------
# Advancing the calendar
# ---------------------------------------------------------------------------


def next_asof(rule: ReleaseRule, asof: date) -> date | None:
    """The day the observation after `asof` will describe. `None` for `event`."""
    if rule.base == "event":
        return None
    if rule.base == "us_session":
        return clock.next_us_session(asof)
    if rule.base == "us_business_day":
        return clock.next_us_business_day(asof)
    if rule.base == "calendar_day":
        return asof + timedelta(days=1)
    if rule.base == "weekly":
        return asof + timedelta(days=7)
    if rule.base == "monthly":
        return _end_of_next_month(asof)
    raise RuleRefused(f"cannot advance base {rule.base!r}")


def released_on(rule: ReleaseRule, asof: date) -> date:
    """The day the observation for `asof` becomes readable.

    With no `release=` key the reading is public the moment it exists, which is
    true of every price and of every 24-hour series.
    """
    value = rule.release_key
    if value is None:
        return asof
    if value in _WEEKDAYS:
        # A weekday name means that weekday in the same ISO week as `asof`.
        return asof + timedelta(days=_WEEKDAYS[value] - asof.weekday())
    offset = _OFFSET.match(value)
    if offset:
        count, kind = int(offset.group(1)), offset.group(2)
        if kind == "d":
            return asof + timedelta(days=count)
        cursor = asof
        for _ in range(count):
            cursor = clock.next_us_business_day(cursor)
        return cursor
    month_day = _NEXT_MONTH_DAY.match(value)
    if month_day:
        day = int(month_day.group(1))
        first_next = _first_of_next_month(asof)
        return first_next.replace(day=min(day, _days_in_month(first_next)))
    raise RuleRefused(f"cannot apply release={value!r}")


def next_expected(rule: ReleaseRule, asof: date) -> date | None:
    """When the observation after `asof` becomes readable. `None` for `event`."""
    following = next_asof(rule, asof)
    if following is None:
        return None
    return released_on(rule, following)


#: When a publication is public by, on its release day, if its own calendar does
#: not say. 17:00 New York clears every producer this system reads: the par
#: curve posts around 15:30, the CFTC at 15:30 and the H.4.1 at 16:30.
_PUBLISHED_BY = time(17, 0)


def published_by(day: date) -> datetime:
    """The instant a figure dated `day` is public by, when nothing finer is known.

    17:00 New York, the same stand-in `readable_at` uses. It is what lets a
    series whose schedule comes from a producer rather than from a rule be
    compared at an instant like every other series: a Treasury auction is struck
    in the New York afternoon, so at 21:00 Singapore on its own day it has not
    happened yet.
    """
    return datetime.combine(day, _PUBLISHED_BY, tzinfo=clock.NEW_YORK).astimezone(UTC)


def readable_at(rule: ReleaseRule, asof: date) -> datetime | None:
    """The instant the observation for `asof` can first be read.

    A date is not enough. The next US session's close is at 16:00 New York, so
    on Monday morning in Singapore that session has not happened yet and a
    series still holding Friday's reading is current, not late. Comparing dates
    instead would call it `due` from midnight, and with forty-seven series on
    six calendars a large minority of the board would sit permanently amber,
    which is the same as the board saying nothing at all.
    """
    day = released_on(rule, asof)
    if rule.base == "calendar_day":
        # A 24-hour market's reading for a day exists from the start of that day:
        # it is a sample taken at the run instant, not a close.
        return datetime.combine(day, time(0, 0), tzinfo=UTC)
    if rule.base == "us_session" and rule.release_key is None:
        return clock.us_session_close_utc(day)
    return datetime.combine(day, _PUBLISHED_BY, tzinfo=clock.NEW_YORK).astimezone(UTC)


def due_asofs(rule: ReleaseRule, after: date, now: datetime) -> tuple[date, ...]:
    """Every observation date strictly after `after` and already readable at `now`.

    This is the catch-up list: what a source owes, computed from its declared
    release rule rather than guessed from how long the worker was down.

    Readable at an instant, not on a date. Monday's session closes at 16:00 New
    York, so at 09:30 Singapore on Monday it is not owed yet, and a catch-up
    that counted it would open a gap for a session that has not happened.
    """
    if rule.base == "event":
        return ()
    out: list[date] = []
    cursor = after
    for _ in range(4000):  # a hard stop, so a malformed rule cannot spin
        following = next_asof(rule, cursor)
        if following is None:
            break
        when = readable_at(rule, following)
        if when is None or when > now:
            break
        out.append(following)
        cursor = following
    return tuple(out)


def reconfirm_due_at(asof: date) -> datetime:
    """When an agent-read `event` row dated `asof` should have been read again.

    The first 09:30 Singapore full run that starts after the `asof` day has
    ended in New York.

    Why New York and not Singapore. `asof` is meant to be a New York date, but
    the 09:30 agent run defaults it to the machine's own date, which is
    Singapore's, and a model filling the field may choose either. A row written
    by the 09:30 run on Singapore Wednesday can therefore carry Tuesday (New
    York) or Wednesday (Singapore). Counting from the end of the New York day
    rates both correctly: for either one, Wednesday's own run is never counted
    against it, and the first run that can find it missing is Friday's for a
    Wednesday date or Thursday's for a Tuesday one. Counting from the end of the
    Singapore day instead would rate a New York-dated row `due` at the very
    instant it was written, and the row would sit amber every morning. The cost
    of the choice is that a Singapore-dated row gets one run's slack before it
    goes `due`, which is a lag, not a false alarm.
    """
    ended = datetime.combine(asof + timedelta(days=1), time(0, 0), tzinfo=clock.NEW_YORK)
    local = ended.astimezone(clock.SINGAPORE)
    run = datetime.combine(local.date(), clock.FULL_RUN_LOCAL, tzinfo=clock.SINGAPORE)
    if run < local:
        run += timedelta(days=1)
    return run.astimezone(UTC)


def freshness(
    rule: ReleaseRule,
    asof: date | None,
    now: datetime,
    *,
    grace: timedelta = timedelta(days=1),
    fetch_path: str | None = None,
) -> str:
    """`fresh`, `due` or `overdue` for an observation dated `asof`.

    `fresh` means the next observation is not readable yet, whatever the gap to
    the last one: a weekly series four days from its next print is current.

    `due` means the next observation became readable and has not arrived. It is
    a source that is a little behind, not a source that is broken, and it has
    `grace` to catch up.

    `overdue` means it missed a release it was owed. The two are kept apart
    because the web app takes this rating as given and never re-rates it, so a
    rating that conflated them would hide a real failure among forty false ones.

    `fetch_path` matters only for an `event` rule. An `event` series has no
    schedule, so by default it is always `fresh`, and that is right for a
    Treasury auction: nothing is owed until the producer holds one. It is wrong
    for a series the 09:30 agents own, such as the fed funds range or the next
    FOMC date. The event is unscheduled, but the reading is not: every 09:30
    full run re-reads the current state and writes it under a new `asof`, so a
    row still holding an old `asof` after a 09:30 run has passed is a run that
    did not confirm it, and "always fresh" is how a week-old statement of the
    policy rate sat green beside a board of live prices. With
    `fetch_path="agent_0930"` such a row goes `due` at `reconfirm_due_at` and
    `overdue` after `grace` more, the same convention as every scheduled rule.
    Omitted, the rating is what it always was.
    """
    if asof is None:
        return "overdue"
    if not rule.is_scheduled:
        if fetch_path != "agent_0930":
            return "fresh"
        reconfirm_at = reconfirm_due_at(asof)
        if now < reconfirm_at:
            return "fresh"
        return "due" if now <= reconfirm_at + grace else "overdue"
    following = next_asof(rule, asof)
    if following is None:
        return "fresh"
    due_at = readable_at(rule, following)
    if due_at is None or now < due_at:
        return "fresh"
    if now <= due_at + grace:
        return "due"
    return "overdue"


def _days_in_month(day: date) -> int:
    first_next = _first_of_next_month(day)
    return (first_next - timedelta(days=1)).day


def _first_of_next_month(day: date) -> date:
    year = day.year + day.month // 12
    month = day.month % 12 + 1
    return date(year, month, 1)


def _end_of_next_month(day: date) -> date:
    """The last day of the month after `day`'s month."""
    first_next = _first_of_next_month(day)
    first_after = _first_of_next_month(first_next)
    return first_after - timedelta(days=1)
