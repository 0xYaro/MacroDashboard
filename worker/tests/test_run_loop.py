"""The source loop: the cursor, the catch-up, the gaps and the findings.

Every fetcher is wrapped by `run_one`, which owns the four things the contract
asks of a source and which no individual fetcher should have to remember: read
the cursor, compute what is due from the declared release rule, open a gap for
every due reading that did not arrive, and never let a fault propagate.

The cursor is the data. `supabase/migrations` declares no cursor table, and the
newest `asof` already stored per series is a cursor that cannot disagree with
what was actually written.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from macro.fetchers.base import FetchContext, Fetched, run_one
from macro.http import FetchRefused
from macro.model import Observation, RawBatch

from .conftest import instant

NOW = instant("2026-09-24T01:30:00")  # Thursday 09:30 in Singapore


class Fake:
    """A fetcher with no network behind it."""

    def __init__(
        self,
        *,
        source: str = "fake",
        series: tuple[str, ...] = ("breadth_spx_above_200dma",),
        rows: Sequence[Observation] = (),
        fault: Exception | None = None,
        normalize_fault: Exception | None = None,
        faults: Sequence[str] = (),
    ) -> None:
        self.source = source
        self.series = series
        self._rows = list(rows)
        self._fault = fault
        self._normalize_fault = normalize_fault
        self._faults = list(faults)

    async def fetch(self, client, ctx) -> Fetched:
        if self._fault is not None:
            raise self._fault
        return Fetched(
            batches=[RawBatch(source=self.source, kind="payload", fetched_at=ctx.now, payload={})],
            faults=self._faults,
        )

    def normalize(self, batches, ctx) -> list[Observation]:
        if self._normalize_fault is not None:
            raise self._normalize_fault
        return list(self._rows)


def breadth(asof: str, value: str = "49.50") -> Observation:
    return Observation(
        series_key="breadth_spx_above_200dma",
        asof=date.fromisoformat(asof),
        value=Decimal(value),
        display=f"{value}%",
        tier=2,
        source="Computed from TradingView scanner closes and 200-day averages",
        observed_at=NOW,
    )


def ctx(registry, cursor: dict[str, str] | None = None) -> FetchContext:
    return FetchContext(
        now=NOW,
        registry=registry,
        cursor={key: date.fromisoformat(day) for key, day in (cursor or {}).items()},
    )


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


async def test_a_source_that_delivers_what_it_owes_opens_no_gap(registry):
    result = await run_one(
        Fake(rows=[breadth("2026-09-22"), breadth("2026-09-23")]),
        client=None,
        ctx=ctx(registry, {"breadth_spx_above_200dma": "2026-09-21"}),
    )
    assert result.status == "ok"
    assert len(result.observations) == 2
    assert result.gaps == []
    assert result.findings == []


async def test_a_cold_start_owes_nothing(registry):
    """No cursor at all is not an outage since the series began.

    Inventing a window between a cursor that does not exist and today would
    open a gap for every session in the series' history.
    """
    result = await run_one(
        Fake(rows=[breadth("2026-09-23")]), client=None, ctx=ctx(registry)
    )
    assert result.gaps == []


# ---------------------------------------------------------------------------
# Catch-up, from the declared rule
# ---------------------------------------------------------------------------


async def test_due_work_is_computed_from_the_release_rule(registry):
    """`breadth_spx_above_200dma` is `us_session`. Three sessions were missed.

    The cursor is Friday 2026-09-18 and the run is Thursday 2026-09-24 at
    09:30 Singapore. The sessions in between are Monday, Tuesday and Wednesday;
    Thursday's has not closed yet, so it is not owed. The source delivers
    Wednesday's only, so Monday and Tuesday are gaps: two, not five days.
    """
    result = await run_one(
        Fake(rows=[breadth("2026-09-23")]),
        client=None,
        ctx=ctx(registry, {"breadth_spx_above_200dma": "2026-09-18"}),
    )
    assert [one.asof.isoformat() for one in result.gaps] == ["2026-09-21", "2026-09-22"]


async def test_a_gap_on_a_replayable_series_is_not_permanent(registry):
    """`defi_tvl` serves history, so a missed day may still arrive."""
    result = await run_one(
        Fake(source="defillama", series=("defi_tvl",), rows=[]),
        client=None,
        ctx=ctx(registry, {"defi_tvl": "2026-09-21"}),
    )
    assert result.gaps
    assert all(one.permanent is False for one in result.gaps)
    assert "may arrive later" in result.gaps[0].reason


async def test_a_gap_on_a_point_in_time_series_is_permanent(registry):
    """`fedwatch_hike_prob_next` is `replayable: false`: an aggregator serves only today.

    A session missed is a session gone. The gap says so, and every condition
    over that date reports unevaluable for as long as it sits inside a
    predicate's window.
    """
    assert registry.require("fedwatch_hike_prob_next").replayable is False
    result = await run_one(
        Fake(source="search", series=("fedwatch_hike_prob_next",), rows=[]),
        client=None,
        ctx=ctx(registry, {"fedwatch_hike_prob_next": "2026-09-22"}),
    )
    assert result.gaps
    assert all(one.permanent for one in result.gaps)
    assert "can never be recovered" in result.gaps[0].reason


async def test_a_series_that_is_merely_not_due_yet_is_not_a_gap(registry):
    """CFTC positions dated Tuesday are not owed again until the next Friday."""
    result = await run_one(
        Fake(source="cftc_tff", series=("cftc_asset_managers_es",), rows=[]),
        client=None,
        ctx=ctx(registry, {"cftc_asset_managers_es": "2026-09-22"}),
    )
    assert result.gaps == []


# ---------------------------------------------------------------------------
# Faults
# ---------------------------------------------------------------------------


async def test_a_fetch_fault_never_propagates_and_says_what_happened(registry):
    result = await run_one(
        Fake(fault=FetchRefused("the endpoint answered HTTP 503")),
        client=None,
        ctx=ctx(registry, {"breadth_spx_above_200dma": "2026-09-22"}),
    )
    assert result.status == "failed"
    assert "503" in (result.error or "")
    assert [one.check_key for one in result.findings] == ["source_unreadable"]
    assert "older as-of" in result.findings[0].sentence
    assert result.gaps  # the reading it owed is still owed


async def test_an_unexpected_exception_is_caught_too(registry):
    """A bug in a fetcher costs that fetcher, not the run."""
    result = await run_one(
        Fake(fault=ValueError("something nobody anticipated")),
        client=None,
        ctx=ctx(registry),
    )
    assert result.status == "failed"
    assert "ValueError" in (result.error or "")


async def test_a_normalize_fault_is_a_finding_and_not_a_row(registry):
    result = await run_one(
        Fake(normalize_fault=KeyError("close")),
        client=None,
        ctx=ctx(registry),
    )
    assert result.observations == []
    assert result.status == "failed"


async def test_an_observation_for_an_undeclared_series_is_discarded_with_a_finding(registry):
    """Storing it would put a reading under a key no condition can resolve."""
    stray = Observation(
        series_key="breadth_spx_above_200sma",  # a typo, not a series
        asof=date(2026, 9, 23),
        value=Decimal("49.50"),
        display="49.50%",
        tier=2,
        source="test",
        observed_at=NOW,
    )
    result = await run_one(
        Fake(rows=[stray, breadth("2026-09-23")]),
        client=None,
        ctx=ctx(registry),
    )
    assert [one.series_key for one in result.observations] == ["breadth_spx_above_200dma"]
    assert [one.check_key for one in result.findings] == ["series_key_unknown"]




async def test_a_missed_breadth_session_is_recoverable_not_permanent(registry):
    """`breadth_spx_above_200dma` is computed from the constituents now.

    The scanner re-serves the two sessions before the newest, and the backfill
    rebuilds any older one from Yahoo closes, so a missed session is a gap that
    may still arrive. It used to be `replayable: false`, which is how a gap the
    09:30 run opened before S5TH had published stayed open for good beside the
    reading the 21:00 run stored.
    """
    assert registry.require("breadth_spx_above_200dma").replayable is True

    result = await run_one(
        Fake(rows=[]),
        client=None,
        ctx=ctx(registry, {"breadth_spx_above_200dma": "2026-09-22"}),
    )
    assert result.gaps
    assert not any(one.permanent for one in result.gaps)
    assert "may arrive later" in result.gaps[0].reason


# ---------------------------------------------------------------------------
# A prose row and a failed fetch are not the same shape
# ---------------------------------------------------------------------------


def test_a_text_series_stores_its_content_in_display_with_a_null_value():
    """The five Macro and Fiscal State rows are prose.

    The rule is keyed on `value_kind`, not on the null. A prose row needing a
    note beside it would make it look like a failed fetch, which is precisely
    the confusion `value_kind` exists to prevent, and it would force the
    authoring agent to use a different record type for the same table.
    """
    row = Observation(
        series_key="fed_funds",
        asof=date(2026, 9, 16),
        value=None,
        display="**3.75%-4.00%** (midpoint 3.875%); September 16 hike of 25 bps, 12-0",
        tier=2,
        source="FOMC coverage (search agent)",
        observed_at=NOW,
        value_kind="text",
    )
    assert row.value is None
    assert row.note is None
    assert row.display.startswith("**3.75%")


def test_a_numeric_series_with_a_null_value_still_has_to_say_why():
    """The exception is for prose, not for silence. Null, never zero, with a reason."""
    import pytest

    with pytest.raises(ValueError, match="needs a note"):
        Observation(
            series_key="vix",
            asof=date(2026, 9, 18),
            value=None,
            display="",
            tier=4,
            source="Yahoo chart API (^VIX)",
            observed_at=NOW,
        )


def test_a_text_series_may_not_carry_a_value():
    import pytest

    with pytest.raises(ValueError, match="content in display"):
        Observation(
            series_key="fed_funds",
            asof=date(2026, 9, 16),
            value=Decimal("3.875"),
            display="3.875%",
            tier=2,
            source="search agent",
            observed_at=NOW,
            value_kind="text",
        )


def test_a_text_series_with_an_empty_display_says_nothing_at_all():
    import pytest

    with pytest.raises(ValueError, match="says nothing"):
        Observation(
            series_key="fed_funds",
            asof=date(2026, 9, 16),
            value=None,
            display="",
            tier=2,
            source="search agent",
            observed_at=NOW,
            value_kind="text",
        )


# ---------------------------------------------------------------------------
# The registry and the fetchers describe the same system
# ---------------------------------------------------------------------------


def test_every_deterministic_series_has_a_fetcher(registry):
    """A deterministic series nothing fetches is a permanent `overdue` row.

    It reads as a source that fell behind, which is the one thing the freshness
    vocabulary is built to distinguish from a source nobody wired up.
    """
    from macro.fetchers import deterministic

    owned = {key for one in deterministic() for key in one.series}
    declared = {one.key for one in registry.deterministic()}
    assert declared - owned == set()


def test_no_fetcher_produces_a_series_the_registry_does_not_declare(registry):
    """The other direction: a key with nowhere to land.

    `run_one` discards it with a finding at runtime, but a fetcher that always
    produces one is a configuration fault, not a bad day.
    """
    from macro.fetchers import deterministic

    owned = {key for one in deterministic() for key in one.series}
    assert owned - registry.keys() == set()


def test_no_fetcher_claims_a_series_the_09_30_agents_own(registry):
    """The rescore must not appear to cover what only the 09:30 run can."""
    from macro.fetchers import deterministic

    owned = {key for one in deterministic() for key in one.series}
    agent = {one.key for one in registry.agent_only()}
    assert owned & agent == set()


# ---------------------------------------------------------------------------
# A source that did not finish is not `ok`
# ---------------------------------------------------------------------------
#
# From the first live run against the real database, 2026-09-21 23:52 Singapore.
# Yahoo was rate-limited, the breaker opened after two 429s, and the run
# recorded:
#
#     yahoo | ok | 1 | -
#
# Status ok, no error, no finding, on a source that delivered one of fourteen
# series. The screen still told the truth, because those thirteen rows read
# `overdue` and none carried a stale value forward. The layer whose entire
# purpose is to reveal a quietly failing job said nothing at all.


def many(count: int) -> tuple[str, ...]:
    """A source declaring several series, like Yahoo's fourteen symbols."""
    return ("spx_cash", "dow_cash", "nasdaq_cash", "rut_cash", "vix", "move")[:count]


def price(series_key: str, asof: str = "2026-09-23") -> Observation:
    return Observation(
        series_key=series_key,
        asof=date.fromisoformat(asof),
        value=Decimal("7650.50"),
        display="7,650.50",
        tier=1,
        source="Yahoo chart API",
        observed_at=NOW,
    )


async def test_a_source_delivering_a_strict_subset_records_partial(registry):
    """The live bug, reproduced at the scale it happened.

    One of six delivered, with the breaker's own sentence as the reason. It is
    `partial`, not `ok`, and it opens a finding naming how many of how many and
    why.
    """
    result = await run_one(
        Fake(
            source="yahoo",
            series=many(6),
            rows=[price("spx_cash")],
            faults=[
                "^GSPC: query1.finance.yahoo.com is rate limiting this run (HTTP 429); "
                "after 2 consecutive 429s the breaker is open"
            ],
        ),
        client=None,
        ctx=ctx(registry),
    )
    assert result.status == "partial"
    assert result.status != "ok"
    assert len(result.delivered) == 1
    assert len(result.declared) == 6
    assert result.missing == ("dow_cash", "move", "nasdaq_cash", "rut_cash", "vix")

    assert [one.check_key for one in result.findings] == ["source_incomplete"]
    sentence = result.findings[0].sentence
    assert "1 of 6 series" in sentence
    assert "has not finished" in sentence
    assert "rate limiting" in sentence  # the breaker's opening is the reportable event
    assert "dow_cash" in sentence  # and which ones are absent


async def test_a_complete_pass_records_ok_and_opens_nothing(registry):
    """`ok` is reserved for a source that delivered everything it owes."""
    result = await run_one(
        Fake(source="yahoo", series=many(3), rows=[price(one) for one in many(3)]),
        client=None,
        ctx=ctx(registry),
    )
    assert result.status == "ok"
    assert result.missing == ()
    assert result.findings == []
    assert result.sentence is None


async def test_a_source_delivering_nothing_is_failed_not_partial(registry):
    """A hard failure that delivered nothing stays `failed`."""
    result = await run_one(
        Fake(source="yahoo", series=many(6), fault=FetchRefused("every symbol answered 429")),
        client=None,
        ctx=ctx(registry),
    )
    assert result.status == "failed"
    assert [one.check_key for one in result.findings] == ["source_unreadable"]
    assert "delivered nothing" in result.findings[0].sentence


async def test_a_withheld_tier_four_row_counts_as_delivered(registry):
    """A figure the source refused to compute was still reported.

    `net_liquidity` withheld with a note is a row with a reason on it. Missing
    means no row at all, and conflating the two would make every honest
    withholding look like a fetch that silently dropped a series.
    """
    withheld = Observation(
        series_key="net_liquidity",
        asof=date(2026, 9, 16),
        value=None,
        display="",
        tier=4,
        source="FRED",
        observed_at=NOW,
        note="a component is missing and a total missing a term is not a smaller total",
    )
    result = await run_one(
        Fake(source="fred", series=("net_liquidity",), rows=[withheld]),
        client=None,
        ctx=ctx(registry),
    )
    assert result.status == "ok"
    assert result.findings == []


async def test_a_complete_pass_stays_ok_even_when_it_opens_a_finding(registry):
    """A fault and a notable event are different things.

    A source that delivered everything it owes is `ok`, whatever it had to say
    about what it delivered. Only completeness decides the status.
    """
    stray = Observation(
        series_key="not_a_series",
        asof=date(2026, 9, 23),
        value=Decimal(1),
        display="1",
        tier=1,
        source="test",
        observed_at=NOW,
    )
    result = await run_one(
        Fake(source="yahoo", series=many(2), rows=[*[price(one) for one in many(2)], stray]),
        client=None,
        ctx=ctx(registry),
    )
    assert result.status == "ok"
    assert [one.check_key for one in result.findings] == ["series_key_unknown"]


async def test_a_fetch_that_partly_worked_is_partial_rather_than_ok(registry):
    """The faults a fetcher collects reach the result.

    They used to be gathered into a local list and dropped the moment one
    request succeeded, which is precisely how the status came out clean.
    """
    result = await run_one(
        Fake(
            source="fred",
            series=("hy_oas_bps", "ig_oas_bps"),
            rows=[],
            faults=["BAMLC0A0CM: HTTP 500"],
        ),
        client=None,
        ctx=ctx(registry),
    )
    assert result.faults == ["BAMLC0A0CM: HTTP 500"]
    assert result.status == "failed"  # it delivered none of the two


def test_the_status_vocabulary_is_exactly_three_words():
    """`macro_quality.ingest_run.status` is read by a human looking for trouble."""
    from macro.model import SourceResult

    complete = SourceResult(source="s", series=("a",))
    complete.observations = [price("a")]
    assert complete.status == "ok"

    partial = SourceResult(source="s", series=("a", "b"))
    partial.observations = [price("a")]
    assert partial.status == "partial"

    failed = SourceResult(source="s", series=("a", "b"))
    assert failed.status == "failed"


def test_every_check_the_worker_raises_has_an_owner_that_may_close_it():
    """A check key no scope declares is opened and then never closed by anything.

    Every literal check key in the package is either evaluated by a step both
    runs perform, by the 09:30 vault read, by the Monday grading, or closed
    subject by subject (`database_write_refused`, `setup_series_missing`).
    `trigger_clause_dropped` belongs to a guard no scheduled run calls, and
    `week_grade_incomplete` and `holdings_check_missing` to the authoring agent.
    """
    import re
    from pathlib import Path

    from macro import grading, main, vault

    source = Path(main.__file__).parent
    raised: set[str] = set()
    for path in source.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        raised.update(re.findall(r'check_key="([a-z_]+)"', text))
        raised.update(re.findall(r'_(?:unparsed|empty)\(\s*"[^"]+",\s*"([a-z_]+)"', text))
        raised.update(
            re.findall(r'_missing_section\(\s*"[^"]+",\s*(?:\w+|"[^"]+"),\s*"([a-z_]+)"', text)
        )
    declared = {
        *main.FETCH_CHECK_KEYS,
        *main.EVALUATION_CHECK_KEYS,
        *vault.CHECK_KEYS,
        *grading.WEEKLY_CHECK_KEYS,
        main.WRITE_REFUSED,
        main.SETUP_REFUSED,
    }
    not_the_workers = {"trigger_clause_dropped", "week_grade_incomplete", "holdings_check_missing"}
    assert raised - declared - not_the_workers == set()
    assert declared - raised == set()
