"""The read model every evaluation stands on.

One object, `History`, holding the observations the run loaded and answering the
three questions the evaluator, the predicates and the snapshot all ask:

- what did this series read on this exact day
- which sessions does this series carry, in order
- is there a recorded gap here

It answers `None` for a day a series did not describe, and it never reaches for
the nearest earlier value to fill one in. That single refusal is what makes
`unevaluable` mean something: every `None` that comes out of here becomes an
`unevaluable` upstream rather than a comparison against yesterday's number.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from .model import Observation


@dataclass(frozen=True, slots=True)
class Reading:
    """One stored observation, as everything downstream sees it."""

    series_key: str
    asof: date
    value: Decimal | None
    display: str
    tier: int
    extra: dict[str, Any]
    #: The moment the reading described: the close for a session series, the run
    #: time for a 24-hour one. What says which of the day's two runs wrote it.
    observed_at: datetime | None = None
    #: When the reading became readable, where the source states it.
    released: date | None = None

    @property
    def session_high(self) -> Decimal | None:
        return _decimal(self.extra.get("session_high"))

    @property
    def session_low(self) -> Decimal | None:
        return _decimal(self.extra.get("session_low"))


class History:
    """Every observation this run can see, indexed by series and date."""

    def __init__(
        self,
        readings: Iterable[Reading] = (),
        gaps: Iterable[tuple[str, date]] = (),
    ) -> None:
        self._by_series: dict[str, dict[date, Reading]] = {}
        for reading in readings:
            self._by_series.setdefault(reading.series_key, {})[reading.asof] = reading
        self._sessions: dict[str, tuple[date, ...]] = {
            key: tuple(sorted(rows)) for key, rows in self._by_series.items()
        }
        self._gaps: set[tuple[str, date]] = set(gaps)

    @classmethod
    def from_observations(
        cls,
        observations: Iterable[Observation],
        gaps: Iterable[tuple[str, date]] = (),
    ) -> History:
        return cls(
            (
                Reading(
                    series_key=one.series_key,
                    asof=one.asof,
                    value=one.value,
                    display=one.display,
                    tier=one.tier,
                    extra=dict(one.extra),
                    observed_at=one.observed_at,
                    released=one.released,
                )
                for one in observations
            ),
            gaps,
        )

    def at(self, series_key: str, asof: date) -> Reading | None:
        """The reading for exactly this day, or `None`.

        Exactly, never on-or-before. On-or-before is how a Friday close gets
        read as a Monday one.
        """
        return self._by_series.get(series_key, {}).get(asof)

    def value_at(self, series_key: str, asof: date) -> Decimal | None:
        reading = self.at(series_key, asof)
        return None if reading is None else reading.value

    def latest(self, series_key: str, on_or_before: date | None = None) -> Reading | None:
        """The newest reading at or before a day, used for `display`, never for a
        comparison that names a date."""
        sessions = self._sessions.get(series_key, ())
        for asof in reversed(sessions):
            if on_or_before is None or asof <= on_or_before:
                return self._by_series[series_key][asof]
        return None

    def sessions(self, series_key: str) -> tuple[date, ...]:
        """Every date this series carries an observation for, oldest first."""
        return self._sessions.get(series_key, ())

    def window(
        self,
        series_key: str,
        *,
        ending: date,
        count: int,
        not_before: date | None = None,
    ) -> tuple[date, ...]:
        """The last `count` sessions of a series, ending at `ending` inclusive.

        A window is counted in sessions of the named series, not in calendar
        days: five sessions of `spx_cash` is five US trading days and a weekend
        does not consume the window, while five sessions of `btc` is five
        calendar days because `btc` advances on `calendar_day`.

        `not_before` is the clip. A window never reaches back past the moment
        the judgment that owns it was authored, so a setup written on Monday
        cannot grade as entered on the prior Thursday's tape.
        """
        eligible = [
            asof
            for asof in self._sessions.get(series_key, ())
            if asof <= ending and (not_before is None or asof >= not_before)
        ]
        return tuple(eligible[-count:]) if count > 0 else ()

    def has_gap(self, series_key: str, asof: date) -> bool:
        return (series_key, asof) in self._gaps

    def series_keys(self) -> frozenset[str]:
        return frozenset(self._by_series)

    def __len__(self) -> int:
        return sum(len(rows) for rows in self._by_series.values())


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except ArithmeticError:
        return None


def readings_from(rows: Sequence[dict[str, Any]]) -> list[Reading]:
    """Database rows into readings. The one place a row shape is decoded."""
    return [
        Reading(
            series_key=str(row["key"]),
            asof=row["asof"],
            value=_decimal(row.get("value")),
            display=str(row.get("display") or ""),
            tier=int(row.get("tier") or 4),
            extra=dict(row.get("extra") or {}),
            observed_at=row.get("observed_at"),
            released=row.get("released"),
        )
        for row in rows
    ]
