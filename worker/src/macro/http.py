"""The one place this worker makes an outbound request.

Every public endpoint here is read-only and keyless. There is no order path
anywhere in this system and no request in this module carries a credential
beyond FRED's optional API key.

Retries are deliberately asymmetric. A 429 or a 5xx is the server asking for
time and is retried with backoff. A 4xx that is not 429 is not retried, because
repeating a malformed request will not make it right, and a fetcher that fails
fast becomes a finding in this run rather than a timeout in the next one.

## The rate-limit breaker

A host that is rate limiting is rate limiting the whole run, not one request.
Yahoo answers 429 to every symbol once it has decided to, and fourteen symbols
each retried five times with backoff is eight minutes of a run spent learning
one fact. After `BREAKER_AFTER` consecutive 429s from one host, every further
request to it in the same run gets one attempt and fails immediately.

The breaker changes how long the fault takes to discover and nothing else. The
source still reports `failed` with its sentence, every series it owns still
keeps its own older as-of, and the next run starts with the breaker closed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from dataclasses import dataclass
from typing import Any

import httpx

LOG = logging.getLogger(__name__)

#: A browser user agent, sent only to the hosts that need one.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)

#: The bare product token, and the only string Yahoo's chart API will answer.
#:
#: Measured here on 2026-09-23 against `v8/finance/chart/ES=F`: `Mozilla/5.0`
#: answers 200 for all fourteen symbols at the run's pacing, and the full Chrome
#: string above (in its 130 and 140 versions), `curl/8.7.1` and `python-httpx`
#: each answer 429 on the first request. A full browser string arriving without
#: a browser's other headers reads as a scraper, and Yahoo rate limits it as one.
#: The 429s from 2026-09-22 were this, not an IP block: the same machine, the
#: same minute, answered 200 to the bare token and 429 to the Chrome string.
YAHOO_USER_AGENT = "Mozilla/5.0"

#: The hosts that serve a different body, or nothing at all, to a plain client,
#: and the agent each one is sent. Every other host is sent `httpx`'s own
#: default, and that is not laziness: it is the only string FRED will answer.
#:
#: Measured here, repeatedly, against `fredgraph.csv?id=WALCL`. `curl/8.7.1` and
#: `python-httpx/0.28.1` answer in under a tenth of a second. The Chrome string
#: above, and every variant of `macro-worker/0.1` tried, accept the connection
#: and then never send a byte, so the request dies on the read timeout forty
#: seconds later. It is an allowlist on the product token, and a name this
#: worker invents for itself is not on it. Setting a polite custom agent here
#: would cost five series a day and look like an outage.
_USER_AGENTS: dict[str, str] = {
    "query1.finance.yahoo.com": YAHOO_USER_AGENT,
    "query2.finance.yahoo.com": YAHOO_USER_AGENT,
    "www.tradingview.com": BROWSER_USER_AGENT,
    "api.coinmarketcap.com": BROWSER_USER_AGENT,
    # CNN's chart endpoint answers 418 to anything but a full browser string,
    # and 418 to that too unless a cnn.com `Referer` comes with it. The referer
    # is sent by `fetchers/cnn.py`, because it is a header and not an agent.
    "production.dataviz.cnn.io": BROWSER_USER_AGENT,
}


def user_agent_for(host: str) -> str | None:
    """The user agent a host is sent, or `None` to leave the library's own.

    Per host, because one of them insists on a full browser string, another on
    the bare product token, and a third refuses anything it does not recognise.
    """
    return _USER_AGENTS.get(host)

DEFAULT_TIMEOUT = 40.0
MAX_ATTEMPTS = 5

#: Consecutive 429s from one host before this run stops waiting on it.
BREAKER_AFTER = 2

#: Hosts that rate limit a public endpoint hard enough to need pacing between
#: calls, measured against the live services rather than read off a document.
_PACE_SECONDS: dict[str, float] = {
    "api.coingecko.com": 3.0,
    # Yahoo rate limits a burst of a dozen symbols hard, and stays cross once it
    # has. Measured on this machine: fourteen symbols at 1.5 s apart trips it,
    # and every request for the following hour answers 429.
    "query1.finance.yahoo.com": 2.5,
    "query2.finance.yahoo.com": 2.5,
    "fred.stlouisfed.org": 1.0,
}


class FetchRefused(Exception):
    """The source could not be read. The message is a sentence, not a trace."""


@dataclass(slots=True)
class Response:
    """What one successful request returned, with the text kept for `macro_raw`."""

    url: str
    status: int
    text: str

    def json(self) -> Any:
        try:
            return json.loads(self.text)
        except json.JSONDecodeError as exc:
            raise FetchRefused(
                f"{self.url.split('?')[0]} answered {self.status} with a body that is not JSON"
            ) from exc


class Client:
    """A small async HTTP client with retries, pacing and one shared connection pool.

    It is deliberately not a `httpx.AsyncClient` subclass: a fetcher should not
    be able to reach `post`, and nothing in this system has any business sending
    one. `query_json` is the one POST, for an endpoint that reads through a
    request body and writes nothing.
    """

    def __init__(self, *, timeout: float = DEFAULT_TIMEOUT) -> None:
        # HTTP/1.1 only. FRED's HTTP/2 endpoint resets a stream under repeated
        # requests from one connection, and the resulting `INTERNAL_ERROR` looks
        # like an outage rather than a protocol problem.
        self._client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            http2=False,
            headers={"Accept": "*/*"},
        )
        self._last_call: dict[str, float] = {}
        self._throttled: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> Client:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _pace(self, host: str) -> None:
        gap = _PACE_SECONDS.get(host)
        if gap is None:
            return
        async with self._lock:
            loop = asyncio.get_running_loop()
            last = self._last_call.get(host)
            now = loop.time()
            if last is not None and now - last < gap:
                await asyncio.sleep(gap - (now - last))
            self._last_call[host] = asyncio.get_running_loop().time()

    def is_throttled(self, host: str) -> bool:
        """True once this run has stopped waiting on a host that keeps saying 429."""
        return self._throttled.get(host, 0) >= BREAKER_AFTER

    async def get(self, url: str, *, headers: dict[str, str] | None = None) -> Response:
        """GET `url`, retrying what is worth retrying and nothing else."""
        return await self._send("GET", url, headers=headers)

    async def query_json(
        self, url: str, body: dict[str, Any], *, headers: dict[str, str] | None = None
    ) -> Any:
        """POST a read-only query and decode the JSON answer.

        For an endpoint whose only interface is a query in a request body:
        TradingView's scanner takes its symbol list and columns that way and has
        no GET form. It reads and changes nothing, so it is a query, not a
        write, and the class still exposes no general `post`.
        """
        reply = await self._send("POST", url, headers=headers, body=body)
        return reply.json()

    async def _send(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Response:
        host = httpx.URL(url).host
        last_fault = "no attempt was made"
        attempts = 1 if self.is_throttled(host) else MAX_ATTEMPTS
        for attempt in range(1, attempts + 1):
            await self._pace(host)
            try:
                agent = user_agent_for(host)
                reply = await self._client.request(
                    method,
                    url,
                    json=body,
                    headers={
                        **({"User-Agent": agent} if agent else {}),
                        **(headers or {}),
                    },
                )
            except httpx.HTTPError as exc:
                last_fault = f"{type(exc).__name__}: {exc}"
            else:
                if reply.status_code < 400:
                    self._throttled.pop(host, None)
                    return Response(url=url, status=reply.status_code, text=reply.text)
                last_fault = f"HTTP {reply.status_code}"
                if reply.status_code == 429:
                    self._throttled[host] = self._throttled.get(host, 0) + 1
                retryable = reply.status_code == 429 or reply.status_code >= 500
                if not retryable:
                    raise FetchRefused(f"{url.split('?')[0]} answered {last_fault}")
            if attempt < attempts:
                delay = min(60.0, 2.0**attempt) + random.uniform(0, 1.5)
                LOG.warning("%s: %s, retrying in %.1fs", url.split("?")[0], last_fault, delay)
                await asyncio.sleep(delay)
        if self.is_throttled(host) and attempts == 1:
            raise FetchRefused(
                f"{host} is rate limiting this run ({last_fault}); after {BREAKER_AFTER} "
                "consecutive 429s the breaker is open and no further request to it waits"
            )
        raise FetchRefused(
            f"{url.split('?')[0]} did not answer after {attempts} attempts: {last_fault}"
        )

    async def get_json(self, url: str, *, headers: dict[str, str] | None = None) -> Any:
        return (await self.get(url, headers=headers)).json()

    async def get_as_browser(self, url: str) -> Response:
        """GET `url` through `curl_cffi`, presenting Chrome's TLS and HTTP/2 fingerprint.

        For a host behind a Cloudflare challenge that no header can pass.
        Farside answers 403 to `httpx` and to `curl` whatever the User-Agent,
        and 200 to the same request with a browser's handshake, measured on
        2026-09-24. `curl_cffi` is imported here rather than at the top so that a
        machine without it loses the one source that needs it and nothing else.

        Retried like `get`: a 5xx or a dropped connection is worth another try,
        a 403 is the challenge refusing this handshake and is not.
        """
        try:
            from curl_cffi.requests import AsyncSession
        except ImportError as exc:
            raise FetchRefused(
                f"{url} needs curl_cffi to pass its Cloudflare check, and it is not installed"
            ) from exc
        host = httpx.URL(url).host
        last_fault = "no attempt was made"
        attempts = 3
        async with AsyncSession(impersonate="chrome", timeout=DEFAULT_TIMEOUT) as session:
            for attempt in range(1, attempts + 1):
                await self._pace(host)
                try:
                    reply = await session.get(url)
                except Exception as exc:  # curl_cffi's own error tree, not httpx's
                    last_fault = f"{type(exc).__name__}: {exc}"
                else:
                    if reply.status_code < 400:
                        return Response(url=url, status=reply.status_code, text=reply.text)
                    last_fault = f"HTTP {reply.status_code}"
                    if reply.status_code != 429 and reply.status_code < 500:
                        raise FetchRefused(f"{url} answered {last_fault}")
                if attempt < attempts:
                    delay = min(30.0, 2.0**attempt) + random.uniform(0, 1.5)
                    LOG.warning("%s: %s, retrying in %.1fs", url, last_fault, delay)
                    await asyncio.sleep(delay)
        raise FetchRefused(f"{url} did not answer after {attempts} attempts: {last_fault}")
