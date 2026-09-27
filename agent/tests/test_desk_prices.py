"""The holdings watch's prices: computed from market data, null with a reason otherwise.

The payloads are the shapes Yahoo's chart API and Hyperliquid's info API served
on 2026-09-23, cut down to the fields read. Nothing here touches the network:
`quotes` is driven through a stand-in for the worker's `macro.http`.
"""

from __future__ import annotations

import copy
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from macro_agent import prices, worker_bridge
from macro_agent.prices import HyperliquidBook, Route, SymbolMap, pct

# Thursday 24 September, 01:05 UTC: 09:05 in Singapore, the holdings run. The US
# session of Wednesday the 23rd closed at 20:00 UTC.
NOW = datetime(2026, 9, 24, 1, 5, tzinfo=UTC)


@pytest.fixture(scope="module")
def clock(settings):
    return worker_bridge.clock(settings)


def stamp(day: date, hour: int = 13, minute: int = 30) -> int:
    return int(datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC).timestamp())


def chart(closes: list[tuple[date, float | None]], *, zone="America/New_York", period=None,
          currency="USD"):
    return {
        "chart": {
            "result": [
                {
                    "meta": {
                        "exchangeTimezoneName": zone,
                        "currency": currency,
                        "currentTradingPeriod": {"regular": period or {}},
                    },
                    "timestamp": [stamp(day) for day, _ in closes],
                    "indicators": {"quote": [{"close": [c for _, c in closes]}]},
                }
            ],
            "error": None,
        }
    }


D21, D22, D23, D24 = (date(2026, 9, d) for d in (21, 22, 23, 24))


def test_close_to_close_is_the_last_completed_session_against_the_one_before(clock):
    quote = prices.yahoo_close_to_close(
        chart([(D21, 350.87), (D22, 339.36), (D23, 334.57)]),
        market_symbol="GOOG", now=NOW, clock=clock,
    )
    assert quote.basis == "close_to_close"
    assert quote.session == D23
    assert quote.price == Decimal("334.57")
    assert quote.change_pct == Decimal("-1.41")
    assert quote.price_at == datetime(2026, 9, 23, 20, 0, tzinfo=UTC)
    assert quote.reason is None


def test_a_session_still_trading_is_never_the_one_reported(clock):
    """At 17:28 UTC on the 23rd the 23rd is running: the change ends on the 22nd."""
    during = datetime(2026, 9, 23, 17, 28, tzinfo=UTC)
    period = {"start": stamp(D23), "end": stamp(D23, 20, 0)}
    quote = prices.yahoo_close_to_close(
        chart([(D21, 350.87), (D22, 339.36), (D23, 334.57)], period=period),
        market_symbol="GOOG", now=during, clock=clock,
    )
    assert quote.session == D22
    assert quote.change_pct == Decimal("-3.28")


def test_an_empty_bar_for_the_session_is_reported_and_not_skipped_over(clock):
    """Yahoo served every US name's 22 September bar empty on the 23rd. Comparing the
    23rd with the 21st would still be called a one-session change, and it is not."""
    quote = prices.yahoo_close_to_close(
        chart([(D21, 350.87), (D22, None), (D23, 334.57)]),
        market_symbol="GOOG", now=NOW, clock=clock,
    )
    assert quote.change_pct is None
    assert quote.price == Decimal("334.57")
    assert "2026-09-22" in quote.reason and "no one-session change" in quote.reason


def test_no_close_for_the_last_completed_session_is_a_reason_not_a_zero(clock):
    quote = prices.yahoo_close_to_close(
        chart([(D21, 350.87), (D22, 339.36)]), market_symbol="GOOG", now=NOW, clock=clock
    )
    assert quote.change_pct is None and quote.price is None
    assert "2026-09-23 session yet" in quote.reason


def test_a_non_us_listing_uses_its_own_exchange_bars(clock):
    seoul = {"start": stamp(D24, 0, 0), "end": stamp(D24, 6, 30)}
    quote = prices.yahoo_close_to_close(
        chart([(D22, 373000.0), (D23, 367500.0), (D24, 370000.0)], zone="Asia/Seoul",
              period=seoul, currency="KRW"),
        market_symbol="012330.KS", now=NOW, clock=clock,
    )
    assert quote.session == D23
    assert quote.change_pct == Decimal("-1.47")
    assert quote.currency == "KRW"


def test_a_yahoo_error_payload_is_a_reason(clock):
    quote = prices.yahoo_close_to_close(
        {"chart": {"result": None, "error": {"description": "No data found"}}},
        market_symbol="NOPE", now=NOW, clock=clock,
    )
    assert quote.change_pct is None and "No data found" in quote.reason


# ---------------------------------------------------------------------------
# Hyperliquid
# ---------------------------------------------------------------------------

SPOT = [
    {
        "tokens": [
            {"index": 0, "name": "USDC"},
            {"index": 150, "name": "HYPE"},
            {"index": 200, "name": "NEKO"},
            {"index": 300, "name": "HYPE"},
        ],
        "universe": [
            {"name": "@107", "tokens": [150, 0]},
            {"name": "@144", "tokens": [200, 0]},
            {"name": "@999", "tokens": [300, 0]},
        ],
    },
    [
        {"coin": "@144", "markPx": "0.000105", "prevDayPx": "0.000105", "dayNtlVlm": "0.0"},
        {"coin": "@107", "markPx": "93.171", "prevDayPx": "96.591", "dayNtlVlm": "119230654.44"},
        {"coin": "@999", "markPx": "1.0", "prevDayPx": "0.5", "dayNtlVlm": "12.0"},
    ],
]
PERP = [{"universe": [{"name": "BTC"}]}, [{"markPx": "84037.0", "prevDayPx": "86632.0",
                                             "dayNtlVlm": "1000000"}]]


def test_spot_contexts_are_matched_by_coin_and_the_most_traded_pair_wins():
    book = HyperliquidBook.parse(SPOT, PERP)
    quote = prices.hyperliquid_quote(book, "HYPE", now=NOW)
    assert quote.price == Decimal("93.171")
    assert quote.change_pct == Decimal("-3.54")
    assert quote.basis == "rolling_24h"
    assert "@107" in quote.source


def test_a_token_that_did_not_trade_has_no_change_rather_than_zero():
    book = HyperliquidBook.parse(SPOT, PERP)
    quote = prices.hyperliquid_quote(book, "NEKO", now=NOW)
    assert quote.change_pct is None and "did not trade" in quote.reason
    assert quote.price == Decimal("0.000105")


def test_a_perp_prices_a_token_with_no_spot_pair():
    book = HyperliquidBook.parse(SPOT, PERP)
    quote = prices.hyperliquid_quote(book, "BTC", now=NOW)
    assert quote.change_pct == Decimal("-3.00") and "perp" in quote.source
    assert prices.hyperliquid_quote(book, "DOGE", now=NOW) is None


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

SYMBOLS = SymbolMap(
    equities={"012330": {"yahoo": "012330.KS"}},
    crypto={"HYPE": {"hyperliquid": "HYPE", "coingecko": "hyperliquid"}},
)


def test_routes():
    us = prices.route("GOOG", asset_class="equity", kind="equity", currency="USD",
                      underlying=None, symbols=SYMBOLS)
    assert us.yahoo == "GOOG"
    krx = prices.route("012330", asset_class="equity", kind=None, currency=None,
                       underlying=None, symbols=SYMBOLS)
    assert krx.yahoo == "012330.KS"
    suffixed = prices.route("D05.SI", asset_class="equity", kind=None, currency="SGD",
                            underlying=None, symbols=SYMBOLS)
    assert suffixed.yahoo == "D05.SI"
    unmapped = prices.route("D05", asset_class="equity", kind=None, currency="SGD",
                            underlying=None, symbols=SYMBOLS)
    assert unmapped.yahoo is None and "config/symbols.yaml" in unmapped.reason
    option = prices.route("META 260925C00737500", asset_class="option", kind="option",
                          currency="USD", underlying="META", symbols=SYMBOLS)
    assert option.yahoo == "META" and option.underlying_only
    hype = prices.route("HYPE", asset_class="crypto", kind="spot", currency="USDC",
                        underlying=None, symbols=SYMBOLS)
    assert (hype.hyperliquid, hype.coingecko) == ("HYPE", "hyperliquid")


def test_the_repository_symbol_map_loads(settings):
    loaded = SymbolMap.load(settings.symbols_yaml)
    assert loaded.equities["012330"]["yahoo"] == "012330.KS"
    assert loaded.equities["D05"]["yahoo"] == "D05.SI"


# ---------------------------------------------------------------------------
# quotes(): one request per distinct route, every fault a reason
# ---------------------------------------------------------------------------


class FakeHttp:
    """A stand-in for `macro.http`: `Client` and `FetchRefused`, answering from a table."""

    class FetchRefused(Exception):
        pass

    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.calls: list[str] = []
        outer = self

        class Client:
            def __init__(self, *, timeout: float) -> None:
                pass

            async def __aenter__(self) -> Client:
                return self

            async def __aexit__(self, *_: object) -> None:
                return None

            async def get_json(self, url: str) -> Any:
                outer.calls.append(url)
                for fragment, answer in outer.answers.items():
                    if fragment in url:
                        if isinstance(answer, Exception):
                            raise answer
                        return answer
                raise outer.FetchRefused(f"{url} answered HTTP 404")

            async def query_json(self, url: str, body: dict[str, Any]) -> Any:
                key = body["type"]
                outer.calls.append(key)
                return outer.answers[key]

        self.Client = Client


async def test_quotes_price_each_route_once_and_explain_every_gap(clock):
    fake = FakeHttp({
        "/GOOG?": chart([(D21, 350.87), (D22, 339.36), (D23, 334.57)]),
        "/META?": chart([(D22, 746.0), (D23, 750.0)]),
        "spotMetaAndAssetCtxs": SPOT,
        "metaAndAssetCtxs": PERP,
        "simple/price": {"obscure-coin": {"usd": 2.0, "usd_24h_change": 5.123,
                                          "last_updated_at": 1790197000}},
    })
    routes = [
        Route("GOOG", yahoo="GOOG"),
        Route("GOOG", yahoo="GOOG"),
        Route("META 260925C00737500", yahoo="META", underlying_only=True),
        Route("HYPE", hyperliquid="HYPE"),
        Route("OBSC", hyperliquid="OBSC", coingecko="obscure-coin"),
        Route("NOPE", hyperliquid="NOPE"),
        Route("D05", reason="no route"),
    ]
    got = await prices.quotes(routes, http=fake, clock=clock, now=NOW)
    assert fake.calls.count(next(c for c in fake.calls if "/GOOG?" in c)) == 1
    assert got[routes[0].key].change_pct == Decimal("-1.41")
    option = got[routes[2].key]
    assert option.underlying_only and option.change_pct == Decimal("0.54")
    assert option.source.endswith(", underlying")
    assert got[routes[3].key].basis == "rolling_24h"
    assert got[routes[4].key].change_pct == Decimal("5.12")
    assert "CoinGecko" in got[routes[4].key].source
    assert got[routes[5].key].change_pct is None
    assert "no market for NOPE" in got[routes[5].key].reason
    assert got[routes[6].key].reason == "no route"


async def test_a_source_that_does_not_answer_is_a_reason_not_a_raise(clock):
    fake = FakeHttp({"/GOOG?": FakeHttp.FetchRefused("HTTP 429")})
    got = await prices.quotes([Route("GOOG", yahoo="GOOG")], http=fake, clock=clock, now=NOW)
    (quote,) = got.values()
    assert quote.change_pct is None and "did not answer" in quote.reason


def test_a_sub_cent_price_is_written_in_fixed_point():
    quote = prices.Quote(market_symbol="MAX", price=Decimal("2E-7"), price_at=None,
                         currency="USD", change_pct=Decimal("0.00"), basis="rolling_24h",
                         session=None, source="Hyperliquid spot (MAX/USDC, @591)")
    assert quote.as_json()["price"] == "0.0000002"


# ---------------------------------------------------------------------------
# Yahoo's empty bar for the session before a running one, at three clocks
# ---------------------------------------------------------------------------
#
# Captured from query1.finance.yahoo.com with the `Mozilla/5.0` user agent at
# 18:50 UTC on 23 September 2026 (02:50 in Singapore on the 24th), with the
# 23rd's regular session running. `range_1mo` is the daily chart `quotes`
# always asks for: its 22 September bar is served with every field null, on
# query1 and query2, with or without includePrePost, for APP, GOOG, META, TLT
# and SPY alike. `range_1d` is Yahoo's one-day chart of the same moment: its
# one bar is the running 23rd, and `chartPreviousClose` is the 22nd's official
# close (328.73), the number the daily chart has lost.

CAPTURED = json.loads((Path(__file__).parent / "fixtures" /
                       "yahoo_app_2026-09-23_mid_session.json").read_text(encoding="utf-8"))
MID_SESSION = datetime(2026, 9, 23, 18, 46, tzinfo=UTC)  # 02:46 SGT on the 24th
PRE_MARKET = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)    # 08:00 New York, before the open
AFTER_CLOSE = datetime(2026, 9, 23, 21, 0, tzinfo=UTC)   # 17:00 New York, after the close
APP_21, APP_22, APP_23 = Decimal("330.17"), Decimal("328.73"), Decimal("314.59")


def captured(key: str, *, closes: dict[date, float | None] | None = None,
             drop_after: date | None = None, previous: float | None = None,
             bar_close: float | None = None) -> dict[str, Any]:
    """A copy of one captured payload, with the bars a different clock would show."""
    payload = copy.deepcopy(CAPTURED[key])
    result = payload["chart"]["result"][0]
    quote = result["indicators"]["quote"][0]
    zone = ZoneInfo("America/New_York")
    days = [datetime.fromtimestamp(s, tz=UTC).astimezone(zone).date() for s in result["timestamp"]]
    keep = [i for i, d in enumerate(days) if drop_after is None or d <= drop_after]
    result["timestamp"] = [result["timestamp"][i] for i in keep]
    for field_name in quote:
        quote[field_name] = [quote[field_name][i] for i in keep]
    for day, value in (closes or {}).items():
        quote["close"][[days[i] for i in keep].index(day)] = value
    if previous is not None:
        result["meta"]["chartPreviousClose"] = previous
    if bar_close is not None:
        quote["close"][-1] = bar_close
    return payload


def test_mid_session_the_captured_daily_chart_alone_has_no_close_for_the_22nd(clock):
    """What `desk positions` reported for every US name at 02:46 SGT on the 24th."""
    quote = prices.yahoo_close_to_close(
        CAPTURED["range_1mo"], market_symbol="APP", now=MID_SESSION, clock=clock)
    assert quote.change_pct is None
    assert "2026-09-22 session" in quote.reason
    assert prices.yahoo_needs_last_day(CAPTURED["range_1mo"], now=MID_SESSION, clock=clock)


def test_mid_session_the_running_bar_is_ignored_and_the_22nd_comes_from_the_one_day_chart(clock):
    quote = prices.yahoo_close_to_close(
        CAPTURED["range_1mo"], market_symbol="APP", now=MID_SESSION, clock=clock,
        last_day=CAPTURED["range_1d"])
    assert quote.session == D22
    assert quote.price == APP_22, "never the running 23rd's partial close"
    assert quote.change_pct == pct(APP_22, APP_21) == Decimal("-0.44")
    assert quote.basis == "close_to_close"
    assert quote.price_at == datetime(2026, 9, 22, 20, 0, tzinfo=UTC)
    assert "2026-09-22 close from Yahoo's one-day chart" in quote.source


def test_before_the_open_the_last_completed_session_is_yesterdays(clock):
    """08:00 New York on the 23rd: the daily chart ends on the 22nd with its close."""
    daily = captured("range_1mo", drop_after=D22, closes={D22: 328.73})
    assert not prices.yahoo_needs_last_day(daily, now=PRE_MARKET, clock=clock)
    quote = prices.yahoo_close_to_close(daily, market_symbol="APP", now=PRE_MARKET, clock=clock)
    assert quote.session == D22
    assert quote.change_pct == Decimal("-0.44")


def test_before_the_open_an_empty_22nd_is_also_filled(clock):
    daily = captured("range_1mo", drop_after=D22)
    assert prices.yahoo_needs_last_day(daily, now=PRE_MARKET, clock=clock)
    quote = prices.yahoo_close_to_close(daily, market_symbol="APP", now=PRE_MARKET, clock=clock,
                                        last_day=CAPTURED["range_1d"])
    assert quote.session == D22
    assert quote.change_pct == Decimal("-0.44")


def test_after_the_close_the_23rd_is_reported_against_the_filled_22nd(clock):
    """17:00 New York on the 23rd: the 23rd is complete, the 22nd may still be empty."""
    daily = captured("range_1mo", closes={D23: 314.59})
    quote = prices.yahoo_close_to_close(daily, market_symbol="APP", now=AFTER_CLOSE, clock=clock)
    assert quote.change_pct is None and "2026-09-22" in quote.reason
    assert prices.yahoo_needs_last_day(daily, now=AFTER_CLOSE, clock=clock)
    quote = prices.yahoo_close_to_close(daily, market_symbol="APP", now=AFTER_CLOSE, clock=clock,
                                        last_day=captured("range_1d", bar_close=314.59))
    assert quote.session == D23
    assert quote.price == APP_23
    assert quote.change_pct == pct(APP_23, APP_22) == Decimal("-4.30")


def test_the_one_day_chart_never_supplies_the_running_sessions_close(clock):
    """Mid-session the one-day bar is the 23rd's partial close; it may not stand in for it."""
    daily = captured("range_1mo", closes={D22: None, D23: None})
    quote = prices.yahoo_close_to_close(daily, market_symbol="APP", now=MID_SESSION, clock=clock,
                                        last_day=CAPTURED["range_1d"])
    assert quote.session == D22 and quote.price == APP_22


async def test_quotes_ask_for_the_one_day_chart_only_when_a_needed_bar_is_empty(clock):
    fake = FakeHttp({
        "/APP?range=1mo": CAPTURED["range_1mo"],
        "/APP?range=1d&": CAPTURED["range_1d"],
        "/GOOG?": chart([(D21, 350.87), (D22, 339.36), (D23, 334.57)]),
    })
    option = Route("META 260925C00737500", yahoo="APP", underlying_only=True)
    got = await prices.quotes([Route("APP", yahoo="APP"), option, Route("GOOG", yahoo="GOOG")],
                              http=fake, clock=clock, now=MID_SESSION)
    app = got[Route("APP", yahoo="APP").key]
    assert (app.session, app.change_pct, app.reason) == (D22, Decimal("-0.44"), None)
    assert got[option.key].change_pct == Decimal("-0.44") and got[option.key].underlying_only
    assert sum("/APP?range=1d&" in c for c in fake.calls) == 1
    assert not any("/GOOG?range=1d&" in c for c in fake.calls)


# ---------------------------------------------------------------------------
# Which Hyperliquid market prices a token (review of 2026-09-25)
# ---------------------------------------------------------------------------
#
# Hyperliquid lists unrelated spot tokens under the names of its perps: on
# 2026-09-25 TRUMP, BERA, MON and PUMP each had a spot pair that had not traded
# in 24 hours beside a perp that traded millions. MAX is the other case: a spot
# token held on Hyperliquid, no perp, and about $34 of volume a day, so a move
# of one tick (0.0000003 to 0.0000002) read as -33% and then -50%.

NAMESAKES_SPOT = [
    {
        "tokens": [
            {"index": 0, "name": "USDC"},
            {"index": 9, "name": "TRUMP"},
            {"index": 591, "name": "MAX"},
            {"index": 150, "name": "HYPE"},
        ],
        "universe": [
            {"name": "@9", "tokens": [9, 0]},
            {"name": "@591", "tokens": [591, 0]},
            {"name": "@107", "tokens": [150, 0]},
        ],
    },
    [
        {"coin": "@9", "markPx": "0.00174", "prevDayPx": "0.001795", "dayNtlVlm": "0.0"},
        {"coin": "@591", "markPx": "0.0000002", "prevDayPx": "0.0000004",
         "dayNtlVlm": "33.95"},
        {"coin": "@107", "markPx": "93.334", "prevDayPx": "92.801", "dayNtlVlm": "122390673.4"},
    ],
]
NAMESAKES_PERP = [
    {"universe": [{"name": "TRUMP"}, {"name": "HYPE"}]},
    [
        {"markPx": "2.082", "prevDayPx": "1.9726", "dayNtlVlm": "8161841.84"},
        {"markPx": "93.349", "prevDayPx": "92.838", "dayNtlVlm": "856438843.26"},
    ],
]


def test_an_unrelated_spot_namesake_with_no_volume_falls_through_to_the_perp():
    book = HyperliquidBook.parse(NAMESAKES_SPOT, NAMESAKES_PERP)
    quote = prices.hyperliquid_quote(book, "TRUMP", now=NOW)
    assert quote.price == Decimal("2.082") and "perp" in quote.source
    assert quote.change_pct == Decimal("5.55")


def test_a_perp_position_is_priced_on_the_perp_even_when_a_spot_pair_trades():
    book = HyperliquidBook.parse(NAMESAKES_SPOT, NAMESAKES_PERP)
    held = prices.route("HYPE", asset_class="crypto", kind="perp", currency="USDC",
                        underlying=None, symbols=SymbolMap())
    assert held.market == "perp"
    quote = prices.hyperliquid_quote(book, "HYPE", now=NOW, market=held.market)
    assert quote.price == Decimal("93.349") and "perp" in quote.source
    spot = prices.route("HYPE", asset_class="crypto", kind="spot", currency="USDC",
                        underlying=None, symbols=SymbolMap())
    assert spot.key != held.key, "a spot and a perp of one name are two quotes"
    assert "spot" in prices.hyperliquid_quote(book, "HYPE", now=NOW, market=spot.market).source


def test_an_illiquid_spot_token_with_no_other_market_reports_no_move():
    """MAX: the market is right, but a one-tick move on $34 of volume is not a -50% day."""
    book = HyperliquidBook.parse(NAMESAKES_SPOT, NAMESAKES_PERP)
    quote = prices.hyperliquid_quote(book, "MAX", now=NOW)
    assert quote.price == Decimal("0.0000002") and "@591" in quote.source
    assert quote.change_pct is None and quote.basis is None
    assert "$34" in quote.reason and "one tick" in quote.reason


def test_symbols_yaml_may_pin_the_market():
    pinned = SymbolMap(crypto={"TRUMP": {"hyperliquid": "TRUMP", "market": "spot"}})
    found = prices.route("TRUMP", asset_class="crypto", kind=None, currency=None,
                         underlying=None, symbols=pinned)
    assert found.market == "spot"
    book = HyperliquidBook.parse(NAMESAKES_SPOT, NAMESAKES_PERP)
    quote = prices.hyperliquid_quote(book, "TRUMP", now=NOW, market=found.market)
    assert "spot" in quote.source and quote.change_pct is None, "pinned: no fall-through"


def test_the_repository_pins_the_hyperliquid_native_spot_tokens(settings):
    loaded = SymbolMap.load(settings.symbols_yaml)
    assert loaded.crypto["MAX"]["market"] == "spot"


async def test_an_illiquid_hyperliquid_quote_falls_back_to_coingecko(clock):
    fake = FakeHttp({
        "spotMetaAndAssetCtxs": NAMESAKES_SPOT,
        "metaAndAssetCtxs": NAMESAKES_PERP,
        "simple/price": {"max-token": {"usd": 0.0000002, "usd_24h_change": -1.5,
                                       "last_updated_at": 1790197000}},
    })
    routes = [Route("MAX", hyperliquid="MAX", coingecko="max-token"),
              Route("MAX", hyperliquid="MAX")]
    got = await prices.quotes(routes, http=fake, clock=clock, now=NOW)
    assert got[routes[0].key].change_pct == Decimal("-1.50")
    assert "CoinGecko" in got[routes[0].key].source
    assert got[routes[1].key].change_pct is None and "one tick" in got[routes[1].key].reason


async def test_two_names_with_no_route_keep_their_own_reasons(clock):
    first = prices.route("D05", asset_class="equity", kind=None, currency="SGD",
                         underlying=None, symbols=SYMBOLS)
    second = prices.route("7203", asset_class="equity", kind=None, currency="JPY",
                          underlying=None, symbols=SYMBOLS)
    assert first.key != second.key
    got = await prices.quotes([first, second], http=FakeHttp({}), clock=clock, now=NOW)
    assert got[first.key].reason.startswith("D05 is quoted in SGD")
    assert got[second.key].reason.startswith("7203 is quoted in JPY")
