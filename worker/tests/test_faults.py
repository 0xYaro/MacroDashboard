"""One reason, said once, however many items it refused.

The defect these tests pin: Yahoo owns fourteen symbols behind one host, the
rate-limit breaker in `http.py` refuses every one of them with the same
sentence, and the old join repeated that sentence fourteen times inside the
`ingest_run` error and fourteen more inside the finding built from it.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from macro.faults import group_faults
from macro.fetchers import yahoo
from macro.fetchers.base import FetchContext
from macro.http import FetchRefused
from macro.model import SourceResult
from macro.series import load as load_registry

from .conftest import CONFIG, instant

#: The sentence `http.Client.get` raises once the breaker is open, verbatim.
BREAKER = (
    "query1.finance.yahoo.com is rate limiting this run (HTTP 429); after 2 "
    "consecutive 429s the breaker is open and no further request to it waits"
)


def test_identical_faults_are_one_explanation_and_every_subject_survives():
    """Fourteen symbols, one reason: the reason is stated once, the symbols all."""
    symbols = [spec.symbol for spec in yahoo.SPECS]
    assert len(symbols) == 14

    sentence = group_faults(f"{symbol}: {BREAKER}" for symbol in symbols)

    assert sentence.count(BREAKER) == 1
    assert "(14 in all)" in sentence
    for symbol in symbols:
        assert symbol in sentence


def test_distinct_reasons_stay_distinct():
    """Grouping collapses repetition, never two different facts into one."""
    sentence = group_faults(
        [
            "ES=F: did not answer after 5 attempts: HTTP 429",
            *(f"{symbol}: {BREAKER}" for symbol in ("^GSPC", "YM=F", "^DJI")),
        ]
    )
    assert sentence.count(BREAKER) == 1
    assert sentence.startswith("ES=F: did not answer after 5 attempts: HTTP 429; ")
    assert "^GSPC, YM=F, ^DJI (3 in all): " in sentence


def test_a_fault_with_no_subject_is_kept_whole():
    """A source-level error has no colon of its own and is not given one."""
    assert group_faults(["yahoo was unreachable"]) == "yahoo was unreachable"


def test_one_fault_reads_exactly_as_it_was_written():
    assert group_faults([f"ES=F: {BREAKER}"]) == f"ES=F: {BREAKER}"


class _Refusing:
    """A client that answers every symbol with the breaker's own sentence."""

    async def get_json(self, url: str, **_: Any) -> Any:
        raise FetchRefused(BREAKER)


def test_yahoo_refuses_the_whole_run_in_one_sentence():
    """End to end through the real fetcher, not through a rebuilt fault list."""
    ctx = FetchContext(now=instant("2026-09-21T13:00:00"), registry=load_registry(CONFIG))
    with pytest.raises(FetchRefused) as refused:
        asyncio.run(yahoo.Yahoo().fetch(_Refusing(), ctx))  # type: ignore[arg-type]

    sentence = str(refused.value)
    assert sentence.count(BREAKER) == 1
    assert sentence.startswith("Yahoo served no symbol: ES=F, ^GSPC, YM=F, ")
    for spec in yahoo.SPECS:
        assert spec.symbol in sentence


def test_the_run_sentence_does_not_repeat_the_error_it_is_built_from():
    """`SourceResult.sentence` is what the finding and the UI both render."""
    result = SourceResult(
        source="yahoo",
        series=tuple(spec.series_key for spec in yahoo.SPECS),
        error="Yahoo served no symbol: "
        + group_faults(f"{spec.symbol}: {BREAKER}" for spec in yahoo.SPECS),
    )
    assert result.status == "failed"
    assert result.sentence is not None
    assert result.sentence.count(BREAKER) == 1
