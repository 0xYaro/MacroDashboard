"""Calendars and run times.

Pure: every function takes the instant it reasons about. Nothing here reads the
wall clock, so a test can place the process anywhere in the year.

Three calendars are kept apart on purpose.

`Asia/Singapore` is the operator's clock and decides only *when a run happens*.
No observation date is ever derived from it.

`America/New_York` is the US market calendar and decides the `asof` of every
session and daily series. A Friday close observed at 05:00 Saturday Singapore
time has a Friday `asof`.

UTC is the crypto day and the clock every stored timestamp is in.

The NYSE calendar and the US federal business calendar are not the same
calendar, and `config/series.yaml` declares which one each series advances on.
The NYSE is shut on Good Friday and open on Columbus Day and Veterans Day; the
Treasury par curve is the other way round. A series scheduled on the wrong one
reports `overdue` on a day it was never going to print.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

SINGAPORE = ZoneInfo("Asia/Singapore")
NEW_YORK = ZoneInfo("America/New_York")

FULL_RUN_LOCAL = time(9, 30)
RESCORE_RUN_LOCAL = time(21, 0)

REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)


def _nth_weekday(year: int, month: int, weekday: int, nth: int) -> date:
    """The `nth` `weekday` of a month. Monday is 0."""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (nth - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """The last `weekday` of a month."""
    next_month = date(year + month // 12, month % 12 + 1, 1)
    last = next_month - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _easter(year: int) -> date:
    """Anonymous Gregorian computus. Good Friday is Easter minus two days."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ll = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ll) // 451
    month, day = divmod(h + ll - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _observed(holiday: date) -> date:
    """A Saturday holiday is observed on the Friday, a Sunday on the Monday."""
    if holiday.weekday() == 5:
        return holiday - timedelta(days=1)
    if holiday.weekday() == 6:
        return holiday + timedelta(days=1)
    return holiday


def us_federal_holidays(year: int) -> frozenset[date]:
    """The eleven US federal holidays, observed. The Treasury calendar."""
    fixed = [
        date(year, 1, 1),  # New Year's Day
        date(year, 6, 19),  # Juneteenth
        date(year, 7, 4),  # Independence Day
        date(year, 11, 11),  # Veterans Day
        date(year, 12, 25),  # Christmas Day
    ]
    holidays = {_observed(d) for d in fixed}
    holidays.add(_nth_weekday(year, 1, 0, 3))  # Martin Luther King Jr Day
    holidays.add(_nth_weekday(year, 2, 0, 3))  # Washington's Birthday
    holidays.add(_last_weekday(year, 5, 0))  # Memorial Day
    holidays.add(_nth_weekday(year, 9, 0, 1))  # Labor Day
    holidays.add(_nth_weekday(year, 10, 0, 2))  # Columbus Day
    holidays.add(_nth_weekday(year, 11, 3, 4))  # Thanksgiving
    return frozenset(holidays)


def us_market_holidays(year: int) -> frozenset[date]:
    """NYSE full closures. The federal list less Columbus Day and Veterans Day,
    plus Good Friday."""
    federal = set(us_federal_holidays(year))
    federal.discard(_nth_weekday(year, 10, 0, 2))  # Columbus Day: the NYSE trades
    federal.discard(_observed(date(year, 11, 11)))  # Veterans Day: the NYSE trades
    federal.add(_easter(year) - timedelta(days=2))  # Good Friday: the NYSE is shut
    return frozenset(federal)


def us_early_closes(year: int) -> frozenset[date]:
    """Sessions that end at 13:00 New York."""
    early = {_nth_weekday(year, 11, 3, 4) + timedelta(days=1)}
    for candidate in (date(year, 12, 24), date(year, 7, 3)):
        if candidate.weekday() < 5:
            early.add(candidate)
    holidays = us_market_holidays(year)
    return frozenset(d for d in early if d not in holidays)


def is_us_session(day: date) -> bool:
    """True when the US equity market trades at all on `day`."""
    return day.weekday() < 5 and day not in us_market_holidays(day.year)


def is_us_business_day(day: date) -> bool:
    """True when the US federal government and the Treasury are open."""
    return day.weekday() < 5 and day not in us_federal_holidays(day.year)


def session_close_time(day: date) -> time:
    """The New York local close for a trading session."""
    return EARLY_CLOSE if day in us_early_closes(day.year) else REGULAR_CLOSE


def us_session_close_utc(day: date) -> datetime:
    """The instant a US session closes, in UTC. 16:00 New York, 13:00 on a half day."""
    return datetime.combine(day, session_close_time(day), tzinfo=NEW_YORK).astimezone(UTC)


def next_us_session(day: date) -> date:
    """The trading session strictly after `day`."""
    cursor = day + timedelta(days=1)
    while not is_us_session(cursor):
        cursor += timedelta(days=1)
    return cursor


def previous_us_session(day: date) -> date:
    """The trading session strictly before `day`."""
    cursor = day - timedelta(days=1)
    while not is_us_session(cursor):
        cursor -= timedelta(days=1)
    return cursor


def next_us_business_day(day: date) -> date:
    """The federal business day strictly after `day`."""
    cursor = day + timedelta(days=1)
    while not is_us_business_day(cursor):
        cursor += timedelta(days=1)
    return cursor


def us_session_on_or_before(day: date) -> date:
    """`day` itself when it is a session, otherwise the session before it."""
    cursor = day
    while not is_us_session(cursor):
        cursor -= timedelta(days=1)
    return cursor


def last_completed_us_session(moment: datetime) -> date:
    """The newest US session whose close is already behind `moment`.

    This is the only honest way to name the session a settled figure describes.
    It is never the calendar date of the run: at 09:30 Singapore on a Tuesday
    the settled session is Monday's, and the worker must say Monday.
    """
    cursor = us_session_on_or_before(moment.astimezone(NEW_YORK).date())
    while us_session_close_utc(cursor) > moment:
        cursor = previous_us_session(cursor)
    return cursor


def us_sessions_between(first: date, last: date) -> tuple[date, ...]:
    """Every US session in `[first, last]`, oldest first."""
    out: list[date] = []
    cursor = first
    while cursor <= last:
        if is_us_session(cursor):
            out.append(cursor)
        cursor += timedelta(days=1)
    return tuple(out)


def singapore_local(moment: datetime) -> datetime:
    """`moment` rendered on the operator's clock. Used for scheduling only."""
    return moment.astimezone(SINGAPORE)


def is_monday_in_singapore(moment: datetime) -> bool:
    """True when the run falls on a Singapore Monday, which closes the week."""
    return singapore_local(moment).weekday() == 0


def iso_week(day: date) -> str:
    """The `2026-W39` label the journal, both ledgers and every week key use."""
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def week_start(week: str) -> date:
    """The Monday of an ISO week label such as `2026-W39`."""
    year, _, number = week.partition("-W")
    return date.fromisocalendar(int(year), int(number), 1)


def previous_week(week: str) -> str:
    """The ISO week label before `week`."""
    return iso_week(week_start(week) - timedelta(days=7))


def crypto_day(moment: datetime) -> date:
    """The UTC calendar date a 24-hour market's reading belongs to."""
    return moment.astimezone(UTC).date()


def utcnow() -> datetime:
    """The one place this package reads the wall clock.

    Everything downstream takes the instant as an argument, which is what makes
    the evaluator and the predicates testable at any date in the record.
    """
    return datetime.now(UTC)
