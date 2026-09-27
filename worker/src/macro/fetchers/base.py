"""What every fetcher is, and the one loop that runs one.

A fetcher is two halves kept deliberately apart.

`fetch` talks to the network and returns raw payloads. It is the only half that
can fail for a reason outside this repository.

It returns a `Fetched`, not a bare list, because a source that reads fourteen
symbols can fail at four of them and still return ten. Those four faults used to
be collected into a local list and thrown away whenever one symbol survived, and
that is how a Yahoo run that delivered one series of fourteen recorded itself as
`ok` with no error and no finding. A `Fetched` carries the faults out.

`normalize` is pure. It takes the payload and the instant, and returns
`Observation` records and nothing else. No vendor field name escapes it, it
opens no socket, and it reads no clock. That is what makes the golden tests
possible: a captured payload in, only `model` records out.

`run_one` wraps the pair with everything the contract requires of a source:
a cursor read, the catch-up list computed from each series' declared release
rule, a gap for every due reading the source did not produce, and one
`macro_quality.ingest_run` row whatever happened.

It also decides whether the source finished. A source declares the series it
owes; one that delivers fewer is `partial`, not `ok`. `ingest_run` is how a
quietly failing job is found, and a status that says `ok` on five percent of a
universe is worse than no status at all.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Protocol, runtime_checkable

from .. import release
from ..http import Client, FetchRefused
from ..model import Finding, Gap, Observation, RawBatch, SourceResult
from ..series import Registry, Series, unknown_key_finding

LOG = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FetchContext:
    """Everything a fetch needs that is not the network.

    `now` is the instant the run is pinned to. Every fetcher takes it rather
    than reading the clock, so a replay of an old run produces the old answer.

    `cursor` is the newest `asof` already stored for each of the source's
    series. It is read from `macro_core.observation` rather than from a side
    table: the data is the cursor, so the two cannot drift apart.
    """

    now: datetime
    registry: Registry
    cursor: dict[str, date] = field(default_factory=dict)
    fred_api_key: str | None = None

    def last_asof(self, series_key: str) -> date | None:
        return self.cursor.get(series_key)


@dataclass(slots=True)
class Fetched:
    """What one `fetch` returned: the payloads, and what went wrong anyway.

    `faults` is for a source that partly worked. A fetcher that reads many
    things raises only when it reads none of them, because one delisted symbol
    must not cost the other thirteen; everything short of that belongs here, so
    that the run can say which of them failed and why.
    """

    batches: list[RawBatch] = field(default_factory=list)
    faults: list[str] = field(default_factory=list)


def one(payload: RawBatch) -> Fetched:
    """A `Fetched` for a source that reads exactly one thing."""
    return Fetched(batches=[payload])


class Fetcher(Protocol):
    """One source. The registry in `__init__.py` holds the live set.

    `source` and `series` are read-only here on purpose. Every implementation
    declares them as plain class attributes, and a mutable protocol member would
    make each of those an invariant mismatch: nothing ever assigns to them, so
    the protocol should not ask to be able to.
    """

    @property
    def source(self) -> str:
        """The name that lands in `macro_quality.ingest_run.source`."""
        ...

    @property
    def series(self) -> tuple[str, ...]:
        """Every `config/series.yaml` key this source can produce."""
        ...

    async def fetch(self, client: Client, ctx: FetchContext) -> Fetched:
        """Talk to the network.

        Raise `FetchRefused` only when nothing at all could be read. Anything
        less goes in `Fetched.faults`, so the run can report an incomplete pass
        rather than a successful one.
        """
        ...

    def normalize(self, batches: Sequence[RawBatch], ctx: FetchContext) -> list[Observation]:
        """Pure. Payloads in, `Observation` records out."""
        ...


@runtime_checkable
class Reviews(Protocol):
    """A source with something to say about what it delivered.

    Optional. Breadth uses it to open a finding when its own computation and
    the vendor's published figure disagree for the same session, which is a
    statement about the rows and not a reason the source did not finish, so it
    never touches `status`.
    """

    def review(self, observations: Sequence[Observation], ctx: FetchContext) -> list[Finding]:
        """Pure. The delivered rows in, findings out."""
        ...


async def run_one(fetcher: Fetcher, client: Client, ctx: FetchContext) -> SourceResult:
    """Run one source end to end and return everything it had to say.

    A fault never propagates. A source that raised still returns a result with
    `error` set and whatever it normalised before the fault, because partial is
    better than nothing and the `ingest_run` row records which it was.
    """
    result = SourceResult(source=fetcher.source, series=tuple(fetcher.series))
    batches: list[RawBatch] = []
    try:
        fetched = await fetcher.fetch(client, ctx)
        batches = fetched.batches
        result.batches = batches
        result.faults = list(fetched.faults)
    except FetchRefused as exc:
        result.error = str(exc)
    except Exception as exc:
        result.error = f"{type(exc).__name__}: {exc}"
        LOG.exception("%s: fetch raised", fetcher.source)

    if batches:
        try:
            produced = fetcher.normalize(batches, ctx)
        except Exception as exc:
            result.error = result.error or f"normalize refused the payload: {exc}"
            LOG.exception("%s: normalize raised", fetcher.source)
            produced = []
        result.observations = _keep_known(produced, fetcher.source, ctx.registry, result.findings)
        if isinstance(fetcher, Reviews):
            try:
                result.findings.extend(fetcher.review(result.observations, ctx))
            except Exception:
                LOG.exception("%s: review raised", fetcher.source)

    result.gaps.extend(_missing(fetcher, ctx, result.observations, result.error))
    finding = _completeness_finding(result)
    if finding is not None:
        result.findings.append(finding)
    return result


#: Every check the run loop itself raises for a source. Each run evaluates them
#: for every source, so each run may close them.
CHECK_KEYS: tuple[str, ...] = ("source_unreadable", "source_incomplete", "series_key_unknown")


def _completeness_finding(result: SourceResult) -> Finding | None:
    """The one finding that says a source did not finish.

    `partial` is load-bearing rather than decorative. A replayable source that
    misses a session writes no permanent gap, so on a cold start this finding is
    the *only* record that anything went wrong: the screen still tells the truth
    because those rows read `overdue`, but nothing in `macro_quality` would.
    """
    if result.status == "ok":
        return None
    sentence = result.sentence or f"{result.source} did not finish"
    if result.status == "failed":
        return Finding(
            check_key="source_unreadable",
            subject=result.source,
            sentence=(
                f"{sentence}. Every series it owns keeps its previous observation with that "
                "observation's own older as-of, and nothing was carried into a day it did not "
                "describe."
            ),
            severity=1,
        )
    return Finding(
        check_key="source_incomplete",
        subject=result.source,
        sentence=(
            f"{sentence}. Missing: {', '.join(result.missing)}. Each of those keeps its "
            "previous observation with that observation's own older as-of, so the snapshot "
            "reports them as overdue rather than fresh."
        ),
        severity=2,
    )


def _keep_known(
    produced: Sequence[Observation],
    source: str,
    registry: Registry,
    findings: list[Finding],
) -> list[Observation]:
    """Drop any observation whose key `config/series.yaml` does not declare.

    Storing it would put a reading under a key no condition can resolve, which
    reads as a working series and behaves as a missing one.
    """
    kept: list[Observation] = []
    seen_unknown: set[str] = set()
    for observation in produced:
        if observation.series_key in registry:
            kept.append(observation)
        elif observation.series_key not in seen_unknown:
            seen_unknown.add(observation.series_key)
            findings.append(unknown_key_finding(observation.series_key, source))
    return kept


def _missing(
    fetcher: Fetcher,
    ctx: FetchContext,
    produced: Sequence[Observation],
    error: str | None,
) -> list[Gap]:
    """A gap for every reading that was due and did not arrive.

    Due work is computed from each series' declared release rule, never guessed
    from how long the worker was down. A gap on a `replayable: false` series is
    permanent: the source serves only the present moment, so that reading is
    gone and every condition over it reports unevaluable rather than standing on
    yesterday's number.
    """
    have: dict[str, set[date]] = {}
    for observation in produced:
        have.setdefault(observation.series_key, set()).add(observation.asof)

    gaps: list[Gap] = []
    for key in fetcher.series:
        series = ctx.registry.get(key)
        if series is None:
            continue
        cursor = ctx.last_asof(key)
        if cursor is None:
            # No history at all. A cold start owes nothing: there is no window
            # between a cursor that does not exist and today, and inventing one
            # would open a gap for every session since the series began.
            continue
        for due in release.due_asofs(series.release_rule, cursor, ctx.now):
            if due in have.get(key, set()):
                continue
            gaps.append(
                Gap(
                    series_key=key,
                    asof=due,
                    reason=_gap_reason(fetcher.source, series, error),
                    permanent=not series.replayable,
                )
            )
    return gaps


def _gap_reason(source: str, series: Series, error: str | None) -> str:
    if error:
        return f"{source} was unreadable on the run that owed this reading: {error}"
    if series.replayable:
        return f"{source} served no reading for this date; it is replayable and may arrive later"
    return (
        f"{source} serves only the present moment, so the reading for this date was missed "
        "and can never be recovered"
    )


#: A fetcher's `fetch` half, when the module wants to keep it a plain function.
FetchFn = Callable[[Client, FetchContext], Awaitable[Fetched]]


def batch(source: str, kind: str, ctx: FetchContext, payload: Any) -> RawBatch:
    """One `macro_raw.batch` row, stamped with the run's own instant."""
    return RawBatch(source=source, kind=kind, fetched_at=ctx.now, payload=payload)
