"""Decimal arithmetic and the one canonical unit per series.

Every number that comes off a vendor is built with `Decimal(str(v))`, so no
value ever round-trips through a binary float. `float` appears nowhere in this
package outside a vendor payload's own decoding.

A condition comparing `2.70` against `300` is silently false and looks correct.
That is why the unit is a property of the series, declared once in
`config/series.yaml`, and why every fetcher converts into it before the value
reaches an `Observation`.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

#: What one unit is when it is written in another. Only pairs this worker
#: actually converts are listed: a missing pair is a refusal, not a fallback.
_CONVERSIONS: dict[tuple[str, str], Decimal] = {
    ("percent", "bps"): Decimal(100),
    ("bps", "percent"): Decimal(1) / Decimal(100),
    ("usd_billions", "usd"): Decimal(10) ** 9,
    ("usd_trillions", "usd"): Decimal(10) ** 12,
    ("usd", "usd_billions"): Decimal(1) / Decimal(10) ** 9,
    ("usd", "usd_trillions"): Decimal(1) / Decimal(10) ** 12,
    ("usd_millions", "usd"): Decimal(10) ** 6,
    ("usd_millions", "usd_billions"): Decimal(1) / Decimal(1000),
    ("usd_billions", "usd_trillions"): Decimal(1) / Decimal(1000),
}


class UnitMismatch(Exception):
    """Two units that this worker will not convert between. Never guessed at."""


def dec(value: Any) -> Decimal:
    """`Decimal(str(v))` with the vendor's own text preserved exactly.

    A value that is not a number at all raises, because a fetcher that hands a
    string like `"."` to this function has found a holiday row and must decide
    what that means rather than letting it become a zero.
    """
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise UnitMismatch(f"{value!r} is not a number") from exc


def maybe_dec(value: Any) -> Decimal | None:
    """`dec`, but a value that is not a number is null rather than an exception.

    Used only where the vendor documents a missing reading, such as FRED's `.`
    for a market holiday. Null, never zero.
    """
    if value is None:
        return None
    text = str(value).strip()
    if text in ("", ".", "null", "None", "NaN", "n/a", "-"):
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def convert(value: Decimal, frm: str, to: str) -> Decimal:
    """`value` expressed in `to`, or a refusal.

    There is no identity fallback for an unknown pair. A silent pass-through is
    how a percent reaches a basis-point condition.
    """
    if frm == to:
        return value
    factor = _CONVERSIONS.get((frm, to))
    if factor is None:
        raise UnitMismatch(f"no conversion from {frm!r} to {to!r}")
    return value * factor


def quantize(value: Decimal, decimals: int) -> Decimal:
    """`value` rounded for display only. The stored value is never quantized."""
    return value.quantize(Decimal(1).scaleb(-decimals))


def pct_change(new: Decimal | None, old: Decimal | None) -> Decimal | None:
    """The percent change from `old` to `new`, or null.

    Null when either side is missing and null when `old` is zero: a change from
    nothing is not a large change, it is an unmeasurable one.
    """
    if new is None or old is None or old == 0:
        return None
    return (new / old - Decimal(1)) * Decimal(100)


def difference(new: Decimal | None, old: Decimal | None) -> Decimal | None:
    """`new - old`, or null when either side is missing."""
    if new is None or old is None:
        return None
    return new - old
