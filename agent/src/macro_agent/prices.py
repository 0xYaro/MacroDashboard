"""The price and 24h change of every held or watched name, from market data.

The operator's decision of 2026-09-24: the 24h change on the holdings watch is
computed here from real prices, and the model only writes the reason for the
move. One Micron session was reported as both +3.10% and +3.92% by two runs of
the old cron, because a model read two different pages. A number computed from
one stated source, on one stated basis, cannot disagree with itself like that.

## Two bases, never mixed

- **Stocks and ETFs: close to close.** The last completed regular session's
  close against the session before it, from Yahoo's chart API. For a US
  listing, "last completed" is the worker's own session calendar
  (`macro.clock.last_completed_us_session`), so a Tuesday morning in Singapore
  reports Monday's session and never a live quote. For any other exchange it is
  Yahoo's newest bar that is not still trading.
  Yahoo serves the daily bar of the session before a running one with every
  field null (every US name on 23 September 2026, mid-session). When a close
  the change needs is null, Yahoo's one-day chart is asked once: its
  `chartPreviousClose` is the official close of the session before its bar.
- **Crypto: rolling 24 hours.** The mark now against the price 24 hours ago,
  from Hyperliquid's info API (`prevDayPx`), which is where the book's tokens
  trade; CoinGecko's 24h change when Hyperliquid has no liquid market for the
  token.

## Which Hyperliquid market

Hyperliquid lists unrelated spot tokens under the names of its perps (on
2026-09-25 TRUMP, BERA, MON and PUMP each had a spot pair that had not traded
in a day beside a perp that traded millions), so a name is never priced from
the first book that knows it:

- a perp position (Accounting's kind `perp`) is priced on the perp only;
- a `market:` pin in `config/symbols.yaml` (`spot` or `perp`) prices on that
  book only;
- anything else takes the spot pair when it is liquid, then the perp when that
  is, then CoinGecko.

A market is liquid when it traded at least `MIN_DAY_VOLUME_USD` in 24 hours.
Below that a move is a tick, not a price: MAX, a spot token held on
Hyperliquid, trades about $34 a day at 0.0000002 to 0.0000004, so one tick
reads as -33% or -50%. An illiquid market with no fallback reports its price
and no change, with a sentence saying why.
- **An option** carries its underlying's close-to-close move and says so
  (`underlying_only`). Its own premium move is not a price the watch reports.

## Null, never zero, never guessed

A change that cannot be computed is `None` with a sentence saying why: Yahoo
served no close for the session, the session before it is missing, the token
did not trade in 24 hours, or `config/symbols.yaml` has no route for the name.
Nothing is carried forward, interpolated or taken from the model.

## The seam

HTTP goes through the worker's `macro.http.Client` over `worker_bridge`, so the
Yahoo user agent (`Mozilla/5.0` and nothing else), the per-host pacing and the
429 breaker are written in one place. The bar parsing is written here rather
than imported from `macro.fetchers.yahoo`, because importing that module runs
the fetcher package's `__init__`, which pulls every source's dependencies into
this package. The rule it applies is the same one: the newest bar is dropped
while its session is still trading.
"""

from __future__ import annotations

import urllib.parse
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

import yaml

Basis = Literal["close_to_close", "rolling_24h"]

YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1mo&interval=1d"
#: Yahoo's one-day chart: asked for only when the daily chart lost a close it needs.
YAHOO_LAST_DAY = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1d&interval=1d"
HYPERLIQUID_INFO = "https://api.hyperliquid.xyz/info"
COINGECKO_SIMPLE = (
    "https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd"
    "&include_24hr_change=true&include_last_updated_at=true"
)

NEW_YORK = "America/New_York"
HUNDRED = Decimal(100)
CENT = Decimal("0.01")

#: A Hyperliquid market that traded less than this in 24 hours reports no change:
#: its moves are ticks. See "Which Hyperliquid market" above.
MIN_DAY_VOLUME_USD = Decimal(10000)

Market = Literal["spot", "perp"]
MARKETS: tuple[Market, ...] = ("spot", "perp")

#: The asset classes priced on Yahoo. Everything else routes to crypto or nowhere.
YAHOO_CLASSES = frozenset({"equity", "etf", "option", "commodity", "fixed_income", "other"})


@dataclass(frozen=True, slots=True)
class Quote:
    """One name's price and 24h change, or the reason there is none."""

    market_symbol: str | None
    price: Decimal | None
    price_at: datetime | None
    currency: str | None
    change_pct: Decimal | None
    basis: Basis | None
    session: date | None
    source: str | None
    reason: str | None = None
    underlying_only: bool = False

    def as_json(self) -> dict[str, Any]:
        return {
            "market_symbol": self.market_symbol,
            # Fixed-point, never 2E-7: a sub-cent token is still read by a person.
            "price": None if self.price is None else f"{self.price:f}",
            "price_at": None if self.price_at is None else self.price_at.isoformat(),
            "currency": self.currency,
            "change_24h_pct": None if self.change_pct is None else str(self.change_pct),
            "change_basis": self.basis,
            "change_session": None if self.session is None else self.session.isoformat(),
            "price_source": self.source,
            "unavailable_reason": self.reason,
            "underlying_only": self.underlying_only,
        }


def unavailable(reason: str, *, market_symbol: str | None = None) -> Quote:
    return Quote(
        market_symbol=market_symbol,
        price=None,
        price_at=None,
        currency=None,
        change_pct=None,
        basis=None,
        session=None,
        source=None,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# Routing: which source prices which name
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Route:
    """Where one name is priced. `yahoo` for a listing, the two crypto ids otherwise.

    `market` says which Hyperliquid book prices the token: `perp` or `spot`
    only, or None for the liquid spot pair, then the liquid perp.
    """

    symbol: str
    yahoo: str | None = None
    hyperliquid: str | None = None
    coingecko: str | None = None
    underlying_only: bool = False
    reason: str | None = None
    market: Market | None = None

    @property
    def key(self) -> str:
        """One key per distinct quote. A name no source prices keeps its own key,
        so one name's reason is never shown on another."""
        found = f"{self.yahoo}|{self.hyperliquid}|{self.coingecko}|{self.market}"
        if self.yahoo is None and self.hyperliquid is None and self.coingecko is None:
            found = f"unrouted:{self.symbol}|{found}"
        return f"{found}|{self.underlying_only}"


@dataclass(frozen=True, slots=True)
class SymbolMap:
    """`config/symbols.yaml`: the names whose price source calls them something else."""

    equities: dict[str, dict[str, str]] = field(default_factory=dict)
    crypto: dict[str, dict[str, str]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> SymbolMap:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls(
            equities={
                str(k).upper(): {str(a): str(b) for a, b in (v or {}).items()}
                for k, v in (raw.get("equities") or {}).items()
            },
            crypto={
                str(k).upper(): {str(a): str(b) for a, b in (v or {}).items()}
                for k, v in (raw.get("crypto") or {}).items()
            },
        )


#: Accounting's instrument kinds that are crypto.
CRYPTO_KINDS = frozenset({"spot", "perp", "crypto"})


def route(
    symbol: str,
    *,
    asset_class: str | None,
    kind: str | None,
    currency: str | None,
    underlying: str | None,
    symbols: SymbolMap,
) -> Route:
    """Where a name is priced, or a sentence saying no route exists.

    A US listing needs no map entry. A non-US listing needs one unless the
    symbol is already in Yahoo's form (it carries a `.` suffix); a crypto token
    needs one unless Hyperliquid lists it under its own name, which is tried.
    """
    upper = symbol.upper().strip()
    is_crypto = (kind or "") in CRYPTO_KINDS or (asset_class or "") == "crypto"
    if is_crypto:
        entry = symbols.crypto.get(upper, {})
        # A perp position is a perp whatever the map says; otherwise the map's pin.
        pinned = "perp" if kind == "perp" else entry.get("market")
        market: Market | None = "perp" if pinned == "perp" else "spot" if pinned == "spot" else None
        return Route(
            symbol=upper,
            hyperliquid=entry.get("hyperliquid", upper),
            coingecko=entry.get("coingecko"),
            market=market,
        )
    if (kind or asset_class) == "option":
        if not underlying:
            return Route(
                symbol=upper,
                underlying_only=True,
                reason=(
                    f"{symbol} is an option and Accounting names no underlying for it, so there "
                    "is no underlying move to report"
                ),
            )
        base = route(
            underlying,
            asset_class="equity",
            kind="equity",
            currency=currency,
            underlying=None,
            symbols=symbols,
        )
        return Route(
            symbol=upper,
            yahoo=base.yahoo,
            underlying_only=True,
            reason=base.reason,
        )
    listing = symbols.equities.get(upper)
    if listing and listing.get("yahoo"):
        return Route(symbol=upper, yahoo=listing["yahoo"])
    if "." in upper or (currency or "USD").upper() == "USD":
        return Route(symbol=upper, yahoo=upper)
    return Route(
        symbol=upper,
        reason=(
            f"{symbol} is quoted in {currency} and config/symbols.yaml has no Yahoo symbol for "
            "it; add one under equities (for example 012330: yahoo: 012330.KS)"
        ),
    )


# ---------------------------------------------------------------------------
# Yahoo: close to close
# ---------------------------------------------------------------------------


def _dec(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        out = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return out if out.is_finite() else None


def pct(new: Decimal, old: Decimal) -> Decimal:
    """The move from `old` to `new` in percent, to the hundredth."""
    return ((new / old - 1) * HUNDRED).quantize(CENT)


@dataclass(frozen=True, slots=True)
class _Daily:
    """A daily chart's completed bars, dated in the exchange's own zone."""

    zone_name: str
    zone: ZoneInfo
    currency: str | None
    period: dict[str, Any]
    bars: list[tuple[date, Decimal | None]]


def _daily(payload: Any, *, now: datetime) -> _Daily | None:
    """The completed bars of a daily chart, or None if Yahoo served no chart.

    Yahoo stamps a daily bar at its session's open in exchange time, so the
    bar's date is the stamp's date in `exchangeTimezoneName`, never in UTC. The
    running session's bar is served last with a partial close; it is dropped
    while `currentTradingPeriod.regular` says the session has not ended.
    """
    try:
        result = payload["chart"]["result"][0]
        meta = result["meta"]
        stamps = result.get("timestamp") or []
        closes = result["indicators"]["quote"][0]["close"]
    except (KeyError, IndexError, TypeError):
        return None
    zone_name = str(meta.get("exchangeTimezoneName") or NEW_YORK)
    zone = ZoneInfo(zone_name)
    period = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
    running: datetime | None = None
    if (
        period.get("start") is not None
        and period.get("end") is not None
        and now < datetime.fromtimestamp(int(period["end"]), tz=UTC)
    ):
        running = datetime.fromtimestamp(int(period["start"]), tz=UTC)
    bars: list[tuple[date, Decimal | None]] = []
    for index, stamp in enumerate(stamps):
        moment = datetime.fromtimestamp(int(stamp), tz=UTC)
        if running is not None and index == len(stamps) - 1 and moment >= running:
            continue
        close = closes[index] if index < len(closes) else None
        bars.append((moment.astimezone(zone).date(), _dec(close)))
    return _Daily(
        zone_name=zone_name,
        zone=zone,
        currency=meta.get("currency"),
        period=period,
        bars=bars,
    )


def yahoo_needs_last_day(payload: Any, *, now: datetime, clock: Any) -> bool:
    """Whether a New York listing's daily chart is missing a close the change needs.

    Yahoo serves the bar of the session before a running one with every field
    null (seen for every US name on 23 September 2026, on query1 and query2,
    with or without pre- and post-market data). Its one-day chart still states
    that close, so `quotes` asks for it only when this is true.
    """
    daily = _daily(payload, now=now)
    if daily is None or daily.zone_name != NEW_YORK:
        return False
    session = clock.last_completed_us_session(now)
    by_session = dict(daily.bars)
    return (
        by_session.get(session) is None
        or by_session.get(clock.previous_us_session(session)) is None
    )


def _last_day_closes(
    payload: Any, *, zone: ZoneInfo, now: datetime, clock: Any
) -> dict[date, Decimal]:
    """The completed closes Yahoo's one-day chart states, by US session.

    Its one bar is the newest session (running or not), and `chartPreviousClose`
    is the official close of the session before that bar. The bar's own close
    counts only when its session has already closed: mid-session it is a
    partial close and never stands in for a completed one.
    """
    try:
        result = payload["chart"]["result"][0]
        meta = result["meta"]
        stamps = result.get("timestamp") or []
        closes = result["indicators"]["quote"][0].get("close") or []
    except (KeyError, IndexError, TypeError, AttributeError):
        return {}
    if not stamps:
        return {}
    day = datetime.fromtimestamp(int(stamps[-1]), tz=UTC).astimezone(zone).date()
    out: dict[date, Decimal] = {}
    previous = _dec(meta.get("chartPreviousClose"))
    if previous:
        out[clock.previous_us_session(day)] = previous
    own = _dec(closes[-1]) if closes else None
    if own and clock.is_us_session(day) and day <= clock.last_completed_us_session(now):
        out[day] = own
    return out


def yahoo_close_to_close(
    payload: Any, *, market_symbol: str, now: datetime, clock: Any, last_day: Any = None
) -> Quote:
    """The last completed session's close against the session before it.

    `clock` is the worker's `macro.clock`. For a New York listing the session is
    named by the US calendar and looked up in Yahoo's bars: mid-session and
    before the open that is the previous session, after the close it is today's.
    A bar Yahoo served empty is filled from `last_day` (Yahoo's one-day chart,
    see `yahoo_needs_last_day`) when that states the close, and otherwise
    reported as missing rather than skipped over: skipping it would compare
    across two sessions and still call it one.
    """
    source = f"Yahoo chart API ({market_symbol})"
    daily = _daily(payload, now=now)
    if daily is None:
        error = (payload or {}).get("chart", {}).get("error") if isinstance(payload, dict) else None
        detail = f": {error.get('description')}" if isinstance(error, dict) else ""
        return unavailable(
            f"Yahoo served no chart for {market_symbol}{detail}", market_symbol=market_symbol
        )
    zone, currency, period, bars = daily.zone, daily.currency, daily.period, daily.bars
    by_session = dict(bars)

    if daily.zone_name == NEW_YORK:
        session = clock.last_completed_us_session(now)
        before = clock.previous_us_session(session)
        last = by_session.get(session)
        prior = by_session.get(before)
        if (last is None or prior is None) and last_day is not None:
            stated = _last_day_closes(last_day, zone=zone, now=now, clock=clock)
            filled = []
            if last is None and session in stated:
                last = stated[session]
                filled.append(session)
            if prior is None and before in stated:
                prior = stated[before]
                filled.append(before)
            if filled:
                days = " and ".join(one.isoformat() for one in sorted(filled))
                source += f"; {days} close from Yahoo's one-day chart, the daily bar was empty"
        closed_at: datetime | None = clock.us_session_close_utc(session)
    else:
        if len(bars) < 2:
            return unavailable(
                f"Yahoo served fewer than two completed sessions for {market_symbol}",
                market_symbol=market_symbol,
            )
        (session, last), (before, prior) = bars[-1], bars[-2]
        closed_at = None
        end = period.get("end")
        if end is not None:
            candidate = datetime.fromtimestamp(int(end), tz=UTC)
            if candidate.astimezone(zone).date() == session:
                closed_at = candidate

    if last is None:
        return unavailable(
            f"Yahoo has no close for {market_symbol}'s {session.isoformat()} session yet, so "
            "there is no completed session to report",
            market_symbol=market_symbol,
        )
    if prior is None or prior == 0:
        return Quote(
            market_symbol=market_symbol,
            price=last,
            price_at=closed_at,
            currency=currency,
            change_pct=None,
            basis=None,
            session=session,
            source=source,
            reason=(
                f"Yahoo has no close for {market_symbol}'s {before.isoformat()} session, the one "
                f"before {session.isoformat()}, so there is no one-session change to report"
            ),
        )
    return Quote(
        market_symbol=market_symbol,
        price=last,
        price_at=closed_at,
        currency=currency,
        change_pct=pct(last, prior),
        basis="close_to_close",
        session=session,
        source=source,
    )


# ---------------------------------------------------------------------------
# Hyperliquid and CoinGecko: rolling 24 hours
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HyperliquidBook:
    """Hyperliquid's spot and perp contexts, fetched once per run."""

    #: token name -> (pair name, markPx, prevDayPx, dayNtlVlm), the most traded USDC pair.
    spot: dict[str, tuple[str, Decimal | None, Decimal | None, Decimal | None]]
    #: perp name -> (markPx, prevDayPx, dayNtlVlm)
    perp: dict[str, tuple[Decimal | None, Decimal | None, Decimal | None]]

    @classmethod
    def parse(cls, spot_payload: Any, perp_payload: Any) -> HyperliquidBook:
        spot: dict[str, tuple[str, Decimal | None, Decimal | None, Decimal | None]] = {}
        try:
            meta, contexts = spot_payload
            tokens = {int(t["index"]): str(t["name"]) for t in meta["tokens"]}
            by_coin = {str(c["coin"]): c for c in contexts}
            for pair in meta["universe"]:
                base, quote = pair["tokens"]
                if tokens.get(int(quote)) != "USDC":
                    continue
                name = tokens.get(int(base))
                ctx = by_coin.get(str(pair["name"]))
                if name is None or ctx is None:
                    continue
                volume = _dec(ctx.get("dayNtlVlm"))
                held = spot.get(name.upper())
                if held is not None and (held[3] or 0) >= (volume or 0):
                    continue
                spot[name.upper()] = (
                    str(pair["name"]),
                    _dec(ctx.get("markPx")),
                    _dec(ctx.get("prevDayPx")),
                    volume,
                )
        except (KeyError, TypeError, ValueError):
            spot = {}
        perp: dict[str, tuple[Decimal | None, Decimal | None, Decimal | None]] = {}
        try:
            meta, contexts = perp_payload
            for asset, ctx in zip(meta["universe"], contexts, strict=False):
                perp[str(asset["name"]).upper()] = (
                    _dec(ctx.get("markPx")),
                    _dec(ctx.get("prevDayPx")),
                    _dec(ctx.get("dayNtlVlm")),
                )
        except (KeyError, TypeError, ValueError):
            perp = {}
        return cls(spot=spot, perp=perp)


def _usd(volume: Decimal) -> str:
    return f"${volume:,.0f}"


def hyperliquid_quote(
    book: HyperliquidBook, token: str, *, now: datetime, market: Market | None = None
) -> Quote | None:
    """The token's rolling 24h move on Hyperliquid, or None if it has no market there.

    `market` restricts the books tried (see "Which Hyperliquid market"). With
    none, the spot pair is taken when it is liquid, then the perp when that is.
    When no market tried is liquid the quote carries the price and no change,
    with the reason; `quotes` then asks CoinGecko when it has an id.
    """
    upper = token.upper()
    found: list[tuple[str, Decimal | None, Decimal | None, Decimal | None]] = []
    for one in (market,) if market is not None else MARKETS:
        if one == "spot" and upper in book.spot:
            pair, mark, prev, volume = book.spot[upper]
            found.append((f"Hyperliquid spot ({upper}/USDC, {pair})", mark, prev, volume))
        elif one == "perp" and upper in book.perp:
            mark, prev, volume = book.perp[upper]
            found.append((f"Hyperliquid perp ({upper})", mark, prev, volume))
    priced = [one for one in found if one[1] is not None]
    if not priced:
        return None
    liquid = next((one for one in priced if (one[3] or 0) >= MIN_DAY_VOLUME_USD), None)
    label, mark, prev, volume = liquid or priced[0]
    assert mark is not None

    def without_change(reason: str) -> Quote:
        return Quote(
            market_symbol=upper,
            price=mark,
            price_at=now,
            currency="USD",
            change_pct=None,
            basis=None,
            session=None,
            source=label,
            reason=reason,
        )

    if not volume:
        return without_change(
            f"{upper} did not trade on Hyperliquid in the last 24 hours, so its mark has not "
            "moved and there is no change to report"
        )
    if liquid is None:
        return without_change(
            f"{upper} traded only {_usd(volume)} on Hyperliquid in the last 24 hours, under "
            f"the {_usd(MIN_DAY_VOLUME_USD)} a move needs to mean anything: at that volume one "
            "tick is the whole move, so no change is reported"
        )
    if prev is None or prev == 0:
        return without_change(f"Hyperliquid served no price from 24 hours ago for {upper}")
    return Quote(
        market_symbol=upper,
        price=mark,
        price_at=now,
        currency="USD",
        change_pct=pct(mark, prev),
        basis="rolling_24h",
        session=None,
        source=label,
    )


def coingecko_quote(payload: Any, coin_id: str) -> Quote | None:
    """CoinGecko's price and 24h change for one coin id, or None if it was not served."""
    try:
        row = payload[coin_id]
    except (KeyError, TypeError):
        return None
    price = _dec(row.get("usd"))
    change = _dec(row.get("usd_24h_change"))
    updated = row.get("last_updated_at")
    at = datetime.fromtimestamp(int(updated), tz=UTC) if updated else None
    if price is None:
        return None
    return Quote(
        market_symbol=coin_id,
        price=price,
        price_at=at,
        currency="USD",
        change_pct=None if change is None else change.quantize(CENT),
        basis=None if change is None else "rolling_24h",
        session=None,
        source=f"CoinGecko simple price ({coin_id})",
        reason=None if change is not None else f"CoinGecko served no 24h change for {coin_id}",
    )


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------


async def _yahoo_quote(client: Any, http: Any, symbol: str, *, now: datetime, clock: Any) -> Quote:
    """One Yahoo listing's close-to-close quote, asking for the one-day chart only if needed."""
    quoted = urllib.parse.quote(symbol, safe="")
    try:
        payload = await client.get_json(YAHOO_CHART.format(symbol=quoted))
    except http.FetchRefused as exc:
        return unavailable(f"Yahoo did not answer for {symbol}: {exc}", market_symbol=symbol)
    last_day: Any = None
    if yahoo_needs_last_day(payload, now=now, clock=clock):
        try:
            last_day = await client.get_json(YAHOO_LAST_DAY.format(symbol=quoted))
        except http.FetchRefused:
            last_day = None  # the daily chart's own reason stands
    return yahoo_close_to_close(
        payload, market_symbol=symbol, now=now, clock=clock, last_day=last_day
    )


async def quotes(
    routes: Iterable[Route], *, http: Any, clock: Any, now: datetime
) -> dict[str, Quote]:
    """One quote per distinct route, keyed by `Route.key`. Every fault is a reason, not a raise.

    `http` is the worker's `macro.http` module. Hyperliquid's two books are
    fetched once and only when a crypto name is in the list; CoinGecko is asked
    once for every coin Hyperliquid could not price.
    """
    distinct: dict[str, Route] = {}
    for one in routes:
        distinct.setdefault(one.key, one)
    out: dict[str, Quote] = {}
    async with http.Client(timeout=20.0) as client:
        book: HyperliquidBook | None = None
        book_fault: str | None = None
        needs_gecko: dict[str, Route] = {}
        #: Hyperliquid's quote without a change, kept for when CoinGecko has none either.
        fallback: dict[str, Quote] = {}
        #: One quote per Yahoo symbol: a stock and its option share one fetch.
        listed: dict[str, Quote] = {}
        for key, one in distinct.items():
            if one.reason is not None and one.yahoo is None:
                out[key] = unavailable(one.reason)
                continue
            if one.yahoo is not None:
                if one.yahoo not in listed:
                    listed[one.yahoo] = await _yahoo_quote(
                        client, http, one.yahoo, now=now, clock=clock
                    )
                found = listed[one.yahoo]
                if one.underlying_only:
                    found = Quote(
                        market_symbol=found.market_symbol,
                        price=found.price,
                        price_at=found.price_at,
                        currency=found.currency,
                        change_pct=found.change_pct,
                        basis=found.basis,
                        session=found.session,
                        source=None if found.source is None else f"{found.source}, underlying",
                        reason=found.reason,
                        underlying_only=True,
                    )
                out[key] = found
                continue
            if one.hyperliquid is not None:
                if book is None and book_fault is None:
                    try:
                        spot = await client.query_json(
                            HYPERLIQUID_INFO, {"type": "spotMetaAndAssetCtxs"}
                        )
                        perp = await client.query_json(
                            HYPERLIQUID_INFO, {"type": "metaAndAssetCtxs"}
                        )
                        book = HyperliquidBook.parse(spot, perp)
                    except http.FetchRefused as exc:
                        book_fault = str(exc)
                if book is not None:
                    found_hl = hyperliquid_quote(book, one.hyperliquid, now=now,
                                                 market=one.market)
                    if found_hl is not None and (
                        found_hl.change_pct is not None or one.coingecko is None
                    ):
                        out[key] = found_hl
                        continue
                    if found_hl is not None:
                        fallback[key] = found_hl
            if one.coingecko is not None:
                needs_gecko[key] = one
                continue
            out[key] = unavailable(
                (
                    f"Hyperliquid did not answer ({book_fault})"
                    if book_fault
                    else f"Hyperliquid has no {f'{one.market} ' if one.market else ''}market "
                    f"for {one.symbol}"
                )
                + " and config/symbols.yaml names no CoinGecko id for it",
                market_symbol=one.hyperliquid,
            )
        if needs_gecko:
            ids = sorted({str(one.coingecko) for one in needs_gecko.values()})
            gecko_fault: str | None
            try:
                payload = await client.get_json(
                    COINGECKO_SIMPLE.format(ids=urllib.parse.quote(",".join(ids), safe=","))
                )
            except http.FetchRefused as exc:
                payload = None
                gecko_fault = str(exc)
            else:
                gecko_fault = None
            for key, one in needs_gecko.items():
                found_cg = None if payload is None else coingecko_quote(payload, str(one.coingecko))
                if (found_cg is None or found_cg.change_pct is None) and key in fallback:
                    out[key] = fallback[key]
                    continue
                out[key] = found_cg or unavailable(
                    f"neither Hyperliquid nor CoinGecko priced {one.symbol}"
                    + (f" (CoinGecko: {gecko_fault})" if gecko_fault else ""),
                    market_symbol=one.coingecko,
                )
    return out
