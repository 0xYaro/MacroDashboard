"""The series registry, read from `config/series.yaml`.

`config/series.yaml` is asserted reference data and the authority on every
series' key, canonical unit, cadence, release rule and fetch path. Nothing in a
fetch ever writes it, and this worker keeps no second copy of it: a second copy
is a second place for a unit to be wrong, which is the exact failure the "one
canonical unit per series" rule exists to prevent.

This module owns two things and no more: reading that file into typed records,
and refusing clearly when it does not describe a series a fetcher produces. A
fetcher emitting a key the registry has never heard of is a configuration fault
that must be visible, because the alternative is a value landing in no table at
all and every condition over it reporting `unevaluable` forever.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from . import release
from .model import Finding

Cadence = Literal["session", "daily", "weekly", "monthly", "event"]
FetchPath = Literal["deterministic", "agent_0930"]

CADENCES: frozenset[str] = frozenset({"session", "daily", "weekly", "monthly", "event"})
FETCH_PATHS: frozenset[str] = frozenset({"deterministic", "agent_0930"})

TABLE_GROUPS: tuple[str, ...] = (
    "Price & Levels",
    "Sentiment & Positioning",
    "Flows & Structure",
    "Macro & Fiscal State",
)

#: The units `config/series.yaml` declares. Listed here so a typo in the config
#: is a refusal at load rather than a comparison against a unit nothing shares.
#: A text series declares no unit at all and stores its content in `display`.
KNOWN_UNITS: frozenset[str] = frozenset(
    {
        "index_points",
        "index_0_100",
        "usd",
        "usd_per_oz",
        "usd_per_bbl",
        "percent",
        "bps",
        "pct_of_oi",
        "pct_per_8h",
        "sessions",
        # A macro print is stored in the unit the clause quotes it in. Payrolls
        # are written "+150K" and JOLTS "7.2M", so the two counts do not share a
        # unit even though FRED serves both in thousands.
        "thousands",
        "millions",
        # A bid-to-cover is total tendered over total accepted: a pure ratio,
        # quoted "2.57x" and never as a percentage.
        "ratio",
    }
)


class RegistryRefused(Exception):
    """The registry cannot be built. The message is the whole diagnosis."""


@dataclass(frozen=True, slots=True)
class Series:
    """One `macro_core.series` row.

    `key` is the stable identifier every condition expression resolves against.
    `label` is the display string and may change without breaking a condition.

    `unit` is the one canonical unit this series is stored and compared in, and
    is `None` only for a `text` series. High yield sits in `bps` because every
    condition referencing it is written in basis points, even though FRED serves
    percent.

    `replayable` is false for a source that only serves the present moment. A
    missed day on a non-replayable series is a permanent gap and can never be
    recovered, which is why the distinction is structural rather than a comment.

    `fetch_path` is what the 21:00 rescore selects on.
    """

    key: str
    label: str
    table_group: str
    ordinal: int
    value_kind: Literal["numeric", "text"]
    unit: str | None
    decimals: int | None
    cadence: Cadence
    release_rule: release.ReleaseRule
    replayable: bool
    source_name: str
    fetch_path: FetchPath
    tier_floor: int

    @property
    def is_deterministic(self) -> bool:
        """True when the 21:00 rescore owns this series as well as the 09:30 run."""
        return self.fetch_path == "deterministic"

    @property
    def delta_native_label(self) -> str | None:
        """What the series' own-cadence delta is called on the snapshot row.

        A weekly series never shows a one-day change. An event series shows no
        cadence delta at all, because it has no cadence to measure one over.
        """
        return {
            "session": "1d",
            "daily": "1d",
            "weekly": "WoW",
            "monthly": "MoM",
            "event": None,
        }[self.cadence]


class Registry:
    """Every series, by key, in the order the snapshot renders them."""

    def __init__(self, series: list[Series]) -> None:
        self._by_key: dict[str, Series] = {}
        for one in series:
            if one.key in self._by_key:
                raise RegistryRefused(f"config/series.yaml declares {one.key!r} more than once")
            self._by_key[one.key] = one
        positions: dict[tuple[str, int], str] = {}
        for one in series:
            slot = (one.table_group, one.ordinal)
            if slot in positions:
                raise RegistryRefused(
                    f"config/series.yaml gives {one.key!r} and {positions[slot]!r} the same "
                    f"ordinal {one.ordinal} in {one.table_group!r}, and ordinal is unique "
                    "within a table group"
                )
            positions[slot] = one.key

    def __contains__(self, key: object) -> bool:
        return key in self._by_key

    def __len__(self) -> int:
        return len(self._by_key)

    def get(self, key: str) -> Series | None:
        return self._by_key.get(key)

    def require(self, key: str) -> Series:
        found = self._by_key.get(key)
        if found is None:
            raise RegistryRefused(
                f"no series {key!r} in config/series.yaml, so an observation for it has "
                "nowhere to land"
            )
        return found

    def keys(self) -> frozenset[str]:
        return frozenset(self._by_key)

    def ordered(self) -> tuple[Series, ...]:
        """Every series in table order, then in the ordinal its group declares."""
        order = {group: index for index, group in enumerate(TABLE_GROUPS)}
        return tuple(
            sorted(
                self._by_key.values(),
                key=lambda s: (order.get(s.table_group, len(order)), s.ordinal),
            )
        )

    def deterministic(self) -> tuple[Series, ...]:
        """The series the 21:00 rescore is responsible for."""
        return tuple(s for s in self.ordered() if s.is_deterministic)

    def agent_only(self) -> tuple[Series, ...]:
        """The series only the 09:30 run can refresh."""
        return tuple(s for s in self.ordered() if not s.is_deterministic)


def load(path: Path) -> Registry:
    """Read `config/series.yaml`. There is no fallback and no default registry."""
    if not path.exists():
        raise RegistryRefused(
            f"{path} is missing. It is the one source for macro_core.series, so without it "
            "no observation has a key, a unit or a release rule and nothing can be stored."
        )
    return parse(path.read_text(encoding="utf-8"), str(path))


def parse(text: str, source: str = "config/series.yaml") -> Registry:
    """Parse the YAML body of a series registry."""
    raw = yaml.safe_load(text)
    if not isinstance(raw, dict) or "series" not in raw:
        raise RegistryRefused(f"{source} has no top-level `series:` mapping")
    entries = raw["series"]
    if not isinstance(entries, list):
        raise RegistryRefused(f"{source}: `series:` must be a list of mappings")
    out: list[Series] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise RegistryRefused(f"{source}: series entry {index} is not a mapping")
        try:
            out.append(_one(entry))
        except (KeyError, ValueError, release.RuleRefused) as exc:
            key = entry.get("key", f"#{index}")
            raise RegistryRefused(f"{source}: series {key!r}: {exc}") from exc
    return Registry(out)


def _one(entry: dict[str, Any]) -> Series:
    key = str(entry["key"])
    value_kind = str(entry.get("value_kind", "numeric"))
    if value_kind not in ("numeric", "text"):
        raise ValueError(f"value_kind {value_kind!r} must be numeric or text")

    unit_raw = entry.get("unit")
    unit = None if unit_raw is None else str(unit_raw)
    if value_kind == "text":
        if unit is not None:
            raise ValueError("a text series declares no unit; its content lives in display")
    elif unit is None or unit not in KNOWN_UNITS:
        raise ValueError(f"unit {unit!r} is not one this worker stores or compares in")

    decimals_raw = entry.get("decimals")
    decimals = None if decimals_raw is None else int(decimals_raw)

    cadence = str(entry["cadence"])
    if cadence not in CADENCES:
        raise ValueError(f"cadence {cadence!r} is not one of {sorted(CADENCES)}")

    table_group = str(entry["table_group"])
    if table_group not in TABLE_GROUPS:
        raise ValueError(f"table_group {table_group!r} is not one of {list(TABLE_GROUPS)}")

    fetch_path = str(entry["fetch_path"])
    if fetch_path not in FETCH_PATHS:
        raise ValueError(f"fetch_path {fetch_path!r} must be deterministic or agent_0930")

    tier_floor = int(entry.get("tier_floor", 1))
    if not 1 <= tier_floor <= 4:
        raise ValueError(f"tier_floor {tier_floor} is outside 1..4")

    return Series(
        key=key,
        label=str(entry["label"]),
        table_group=table_group,
        ordinal=int(entry["ordinal"]),
        value_kind=value_kind,  # type: ignore[arg-type]
        unit=unit,
        decimals=decimals,
        cadence=cadence,  # type: ignore[arg-type]
        release_rule=release.parse(str(entry["release_rule"])),
        replayable=bool(entry["replayable"]),
        source_name=str(entry["source_name"]),
        fetch_path=fetch_path,  # type: ignore[arg-type]
        tier_floor=tier_floor,
    )


def unknown_key_finding(key: str, source: str) -> Finding:
    """The finding a fetcher opens when it produces a key the registry lacks."""
    return Finding(
        check_key="series_key_unknown",
        subject=key,
        sentence=(
            f"{source} produced an observation for series {key!r}, which config/series.yaml "
            "does not declare, so the reading was discarded rather than stored under a key no "
            "condition can resolve."
        ),
        severity=1,
    )
