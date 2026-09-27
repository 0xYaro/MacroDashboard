"""Shared fixtures.

The golden payloads in `tests/golden/` are real captures from the live public
endpoints, taken on 2026-09-21. They are never edited by hand: an edited capture
tests the editor's idea of the vendor rather than the vendor.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from macro import series as series_module
from macro.expr import Context
from macro.observations import History, Reading

GOLDEN = Path(__file__).parent / "golden"
CONFIG = Path(__file__).resolve().parents[2] / "config" / "series.yaml"


def golden_text(name: str) -> str:
    return (GOLDEN / name).read_text(encoding="utf-8")


def golden_json(name: str):
    return json.loads(golden_text(name))


@pytest.fixture(scope="session")
def registry() -> series_module.Registry:
    """The repository's own `config/series.yaml`, not a copy of it.

    A test registry would be a second place for a unit to be wrong, which is the
    failure the one-canonical-unit rule exists to prevent.
    """
    return series_module.load(CONFIG)


def reading(
    key: str,
    asof: str,
    value: str | None,
    *,
    high: str | None = None,
    low: str | None = None,
    display: str = "",
    tier: int = 1,
) -> Reading:
    extra: dict[str, str] = {}
    if high is not None:
        extra["session_high"] = high
    if low is not None:
        extra["session_low"] = low
    return Reading(
        series_key=key,
        asof=date.fromisoformat(asof),
        value=None if value is None else Decimal(value),
        display=display,
        tier=tier,
        extra=extra,
    )


def history(*readings: Reading, gaps: tuple[tuple[str, str], ...] = ()) -> History:
    return History(
        readings,
        gaps=tuple((key, date.fromisoformat(day)) for key, day in gaps),
    )


def context(
    registry: series_module.Registry,
    hist: History,
    asof: str,
    *,
    not_before: str | None = None,
) -> Context:
    return Context(
        asof=date.fromisoformat(asof),
        history=hist,
        registry=registry,
        not_before=None if not_before is None else date.fromisoformat(not_before),
    )


def instant(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)
