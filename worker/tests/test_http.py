"""What each host is sent, and why a wrong user agent is an outage.

From 2026-09-21 to 2026-09-23 every Yahoo series sat on its 18 September close
because every chart request answered 429. It read as an IP block. It was the
user agent: the same machine, in the same minute, got 200 for the bare
`Mozilla/5.0` token and 429 for the full Chrome string this worker sent.

The fake server below answers the way Yahoo was measured to answer, so the test
fails if the agent ever drifts back to a string Yahoo refuses.
"""

from __future__ import annotations

import os
import urllib.parse

import httpx
import pytest

from macro import http
from macro.fetchers import yahoo
from macro.fetchers.base import FetchContext
from macro.series import load as load_registry

from .conftest import CONFIG, instant

NOW = instant("2026-09-23T03:00:00")

YAHOO_HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")


def test_yahoo_is_sent_the_bare_product_token():
    for host in YAHOO_HOSTS:
        assert http.user_agent_for(host) == "Mozilla/5.0"


def test_the_hosts_that_need_a_full_browser_string_still_get_one():
    assert http.user_agent_for("www.tradingview.com") == http.BROWSER_USER_AGENT
    assert http.user_agent_for("api.coinmarketcap.com") == http.BROWSER_USER_AGENT


def test_fred_is_left_the_library_default():
    """FRED hangs on any agent it does not recognise, including a browser's."""
    assert http.user_agent_for("fred.stlouisfed.org") is None


def _chart(symbol: str) -> dict[str, object]:
    # Two completed sessions, 17 and 18 September, well before NOW.
    return {
        "chart": {
            "result": [
                {
                    "meta": {
                        "symbol": symbol,
                        "exchangeTimezoneName": "America/New_York",
                        "currentTradingPeriod": {
                            "regular": {"start": 1790083800, "end": 1790107200}
                        },
                    },
                    "timestamp": [1789651800, 1789738200],
                    "indicators": {
                        "quote": [
                            {
                                "open": [100.0, 101.0],
                                "high": [102.0, 103.0],
                                "low": [99.0, 100.0],
                                "close": [101.0, 102.0],
                            }
                        ]
                    },
                }
            ],
            "error": None,
        }
    }


def _yahoo_as_measured(request: httpx.Request) -> httpx.Response:
    """429 for every agent except the bare token, as measured on 2026-09-23."""
    if request.headers.get("user-agent") != "Mozilla/5.0":
        return httpx.Response(429, text="Too Many Requests")
    symbol = urllib.parse.unquote(request.url.path.rsplit("/", 1)[-1])
    return httpx.Response(200, json=_chart(symbol))


async def test_every_yahoo_symbol_is_answered_by_a_server_that_refuses_browsers(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(http, "_PACE_SECONDS", {})
    client = http.Client()
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(_yahoo_as_measured))
    try:
        fetched = await yahoo.Yahoo().fetch(
            client, FetchContext(now=NOW, registry=load_registry(CONFIG))
        )
    finally:
        await client.aclose()
    assert fetched.faults == []
    assert {one.kind for one in fetched.batches} == set(yahoo.BY_KEY)


@pytest.mark.skipif(
    os.environ.get("MACRO_LIVE") != "1",
    reason="touches the live Yahoo endpoint; set MACRO_LIVE=1 to run",
)
async def test_live_yahoo_answers_the_agent_this_worker_sends():
    async with http.Client() as client:
        reply = await client.get_json(
            yahoo.CHART_URL.format(symbol=urllib.parse.quote("^GSPC", safe=""), range="5d")
        )
    assert reply["chart"]["result"][0]["meta"]["symbol"] == "^GSPC"
