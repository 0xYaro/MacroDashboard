"""Golden-file tests: a captured real payload in, expected normalised rows out.

Every fixture in `tests/golden/` is a real capture from the live public endpoint
taken on 2026-09-21, byte for byte as the vendor served it. None of them is
edited by hand: an edited capture tests the editor's idea of the vendor rather
than the vendor.

The `normalize` half of every fetcher is pure. It opens no socket and reads no
clock, which is what makes these tests possible at all.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from decimal import Decimal

import pytest

from macro import clock
from macro.fetchers import (
    alternative_me,
    binance,
    cftc,
    cnn,
    coingecko,
    coinmarketcap,
    defillama,
    fred,
    treasury,
    treasury_auctions,
    yahoo,
)
from macro.fetchers.base import FetchContext
from macro.http import FetchRefused
from macro.model import RawBatch
from macro.series import load as load_registry
from macro.units import dec

from .conftest import CONFIG, golden_json, golden_text, instant

NOW = instant("2026-09-21T13:00:00")


def batch(kind: str, payload: object) -> RawBatch:
    return RawBatch(source="test", kind=kind, fetched_at=NOW, payload=payload)


# ---------------------------------------------------------------------------
# FRED
# ---------------------------------------------------------------------------


def test_fred_stores_the_spread_in_basis_points():
    """FRED serves percent. Every condition is written in basis points.

    `2.70` in the CSV is `270` in `macro_core.observation.value`, and the
    display carries both the way the journal prints it. A condition comparing
    2.70 against 300 is silently false and looks correct, which is the whole
    reason the conversion happens here and not in the reader.
    """
    points = fred.read_csv(
        golden_text("fred_BAMLH0A0HYM2.csv"), "BAMLH0A0HYM2", Decimal(100)
    )
    assert points[-1][0] == date(2026, 9, 17)
    assert points[-1][1] == Decimal("270.00")

    rows = fred.Fred().normalize(
        [batch("BAMLH0A0HYM2", golden_text("fred_BAMLH0A0HYM2.csv"))],
        _ctx(),
    )
    newest = max(rows, key=lambda one: one.asof)
    assert newest.series_key == "hy_oas_bps"
    assert newest.asof == date(2026, 9, 17)
    assert newest.value == Decimal("270.00")
    assert newest.display == "2.70% (270 bps)"
    assert newest.tier == 1


def test_fred_restates_the_replay_window_not_only_the_newest_point():
    """HY held scattered points because only the newest one was ever stored.

    The CSV already carries the whole history, so every run re-states the last
    `REPLAY_DAYS` of it and a day a run missed fills itself on the next one.
    From the captured CSV the window ending 2026-09-17 opens on 2026-08-03.
    """
    rows = fred.Fred().normalize(
        [batch("BAMLH0A0HYM2", golden_text("fred_BAMLH0A0HYM2.csv"))],
        _ctx(),
    )
    days = [one.asof for one in rows]
    assert len(days) == len(set(days))
    assert min(days) >= date(2026, 9, 17) - timedelta(days=fred.REPLAY_DAYS)
    assert date(2026, 9, 16) in days
    assert min(days) == date(2026, 8, 3)
    assert all(one.value is not None and one.tier == 1 for one in rows)


def test_fred_skips_a_holiday_rather_than_reading_it_as_zero():
    """FRED writes `.` for a day the series has no reading. It is not a zero."""
    text = "observation_date,BAMLH0A0HYM2\n2026-09-16,.\n2026-09-17,2.70\n"
    points = fred.read_csv(text, "BAMLH0A0HYM2", Decimal(100))
    assert [one[0] for one in points] == [date(2026, 9, 17)]


def test_net_liquidity_is_one_source_with_one_asof():
    """`WALCL - WTREGEN - RRPONTSYD`, all three struck on WALCL's own Wednesday.

    Hand-computed from the captured CSVs, all three rows dated 2026-09-16:
      WALCL     6,746,548 million  = 6,746,548,000,000
      WTREGEN     877,028 million  =   877,028,000,000
      RRPONTSYD     5.375 billion  =     5,375,000,000
      net liquidity                = 5,864,145,000,000

    The RRP term is the one worth checking by hand. It is served in billions
    while its two neighbours are served in millions, so reading it as millions
    would put net liquidity out by 5.37 billion and the figure would still look
    entirely plausible.
    """
    rows = fred.Fred().normalize(
        [
            batch("WALCL", golden_text("fred_WALCL.csv")),
            batch("WTREGEN", golden_text("fred_WTREGEN.csv")),
            batch("RRPONTSYD", golden_text("fred_RRPONTSYD.csv")),
        ],
        _ctx(),
    )
    liquidity = max(
        (one for one in rows if one.series_key == "net_liquidity"), key=lambda one: one.asof
    )
    assert liquidity.asof == date(2026, 9, 16)
    assert liquidity.value == Decimal("5864145000000.000")
    assert liquidity.display == "$5.864T"
    assert liquidity.extra["RRPONTSYD"] == "5375000000.000"
    assert liquidity.extra["WALCL_asof"] == "2026-09-16"


def test_net_liquidity_is_withheld_when_a_component_is_missing():
    """A total missing a term is not a smaller total."""
    walcl = [(date(2026, 9, 16), Decimal("6746548000000"))]
    tga = [(date(2026, 9, 9), Decimal("883335000000"))]  # the wrong Wednesday
    rrp = [(date(2026, 9, 16), Decimal("5375000000"))]
    row = fred.net_liquidity(walcl, tga, rrp, NOW)
    assert row is not None
    assert row.value is None
    assert row.tier == 4
    assert "WTREGEN" in (row.note or "")


def test_rrp_is_billions_and_the_others_are_millions():
    """The unit that would quietly shrink net liquidity if it were read wrong."""
    assert fred.BY_ID["RRPONTSYD"].scale == "billions_to_usd"
    assert fred.BY_ID["WALCL"].scale == "millions_to_usd"
    assert fred.BY_ID["WTREGEN"].scale == "millions_to_usd"


# ---------------------------------------------------------------------------
# The Treasury par curve
# ---------------------------------------------------------------------------


def test_treasury_reads_three_tenors_and_the_2s10s_spread():
    """The three par yields and the spread the journal hangs on the ten-year.

    From the captured 2026 CSV, the newest row is 2026-09-18: the 10Y at 5.01
    and the 2Y at 4.76, so the spread is 25 basis points, which is the figure
    `2026-W39.points.json` carries.
    """
    rows = treasury.Treasury().normalize(
        [batch("csv:2026", golden_text("treasury_yield_2026.csv"))], _ctx()
    )
    by_key = {one.series_key: one for one in rows}
    assert set(by_key) == {"ust_30y", "ust_10y", "ust_2y"}
    assert by_key["ust_10y"].asof == date(2026, 9, 18)
    assert by_key["ust_10y"].value == Decimal("5.01")
    assert by_key["ust_2y"].value == Decimal("4.76")
    assert by_key["ust_30y"].value == Decimal("5.34")
    assert by_key["ust_10y"].extra["spread_2s10s_bps"] == "25.00"
    assert by_key["ust_10y"].display == "5.01%"


# ---------------------------------------------------------------------------
# CFTC
# ---------------------------------------------------------------------------


def test_treasury_restates_the_replay_window_it_already_serves():
    """The CSV is the whole year. The last `REPLAY_DAYS` of it are stored each run."""
    rows = treasury.Treasury().normalize(
        [batch("csv:2026", golden_text("treasury_yield_2026.csv"))], _ctx()
    )
    tens = [one for one in rows if one.series_key == "ust_10y"]
    days = [one.asof for one in tens]
    assert days == sorted(set(days))
    assert days[-1] == date(2026, 9, 18)
    assert days[0] >= date(2026, 9, 18) - timedelta(days=treasury.REPLAY_DAYS)
    assert len(days) > 20
    assert all("spread_2s10s_bps" in one.extra for one in tens)


def test_cftc_net_position_and_regime_band():
    """Net as a percent of open interest, and the band it sits in.

    Hand-computed from the captured report for 2026-09-15:
      asset managers   (1,142,075 - 237,391) / 2,446,519 = +36.98%
      leveraged funds  (  161,176 - 454,319) / 2,446,519 = -11.98%
    Both match `2026-W39.points.json`, and both fall in the `neutral` band.
    """
    rows = cftc.Cftc().normalize([batch("tff", golden_json("cftc_tff_es.json"))], _ctx())
    by_key = {one.series_key: one for one in rows}
    managers = by_key["cftc_asset_managers_es"]
    funds = by_key["cftc_leveraged_funds_es"]

    assert managers.asof == date(2026, 9, 15)
    assert managers.released == date(2026, 9, 18)  # Tuesday positions, Friday release
    assert managers.value.quantize(Decimal("0.01")) == Decimal("36.98")
    assert managers.display == "+36.98% of OI"
    assert managers.extra["band"] == "neutral"

    assert funds.value.quantize(Decimal("0.01")) == Decimal("-11.98")
    assert funds.display == "-11.98% of OI"
    assert funds.extra["band"] == "neutral"


def test_cftc_bands_are_the_asserted_reference_thresholds():
    """The two cohorts have different tables and the same number means opposite things."""
    assert cftc.band("asset_manager", Decimal("26.6")) == "sidelined"
    assert cftc.band("asset_manager", Decimal("40")) == "neutral"
    assert cftc.band("asset_manager", Decimal("47.8")) == "heavy"
    assert cftc.band("asset_manager", Decimal("48.8")) == "extreme"
    assert cftc.band("leveraged_fund", Decimal("-20.0")) == "sidelined"
    assert cftc.band("leveraged_fund", Decimal("-12")) == "neutral"
    assert cftc.band("leveraged_fund", Decimal("-9.9")) == "heavy"
    assert cftc.band("leveraged_fund", Decimal("-6.7")) == "extreme"


# ---------------------------------------------------------------------------
# Alternative.me
# ---------------------------------------------------------------------------


def test_crypto_fear_and_greed_takes_the_vendor_date():
    rows = alternative_me.normalize_fng(golden_json("alternative_fng.json"), NOW)
    newest = rows[-1]
    assert newest.series_key == "crypto_fear_greed"
    assert newest.value == Decimal("70")
    assert newest.display == "70 (Greed)"
    assert newest.asof == date(2026, 9, 21)


def test_crypto_fear_and_greed_restates_the_thirty_days_it_already_serves():
    """The response carries thirty dated readings, and all thirty are stored.

    Only the newest one used to be, so a day a run missed stayed missing.
    """
    rows = alternative_me.normalize_fng(golden_json("alternative_fng.json"), NOW)
    days = [one.asof for one in rows]
    assert len(rows) == 30
    assert days == sorted(set(days))
    assert days[0] == date(2026, 8, 23)
    assert rows[-2].asof == date(2026, 9, 20)
    assert rows[-2].value == Decimal("71")


# ---------------------------------------------------------------------------
# CoinGecko
# ---------------------------------------------------------------------------


def test_coingecko_price_carries_the_points_own_day():
    rows = coingecko.normalize_chart(
        coingecko.BY_ID["bitcoin"], golden_json("coingecko_bitcoin_chart.json"), NOW
    )
    assert len(rows) == 1
    assert rows[0].series_key == "btc"
    assert rows[0].value > Decimal(1000)
    assert rows[0].asof == date(2026, 9, 21)
    assert rows[0].display.startswith("$")


def test_coingecko_fills_only_the_days_the_store_has_not_reached():
    """A missed day fills from the chart; a stored day is never re-stated.

    The cursor is 2026-09-18, so 19 and 20 September have no row and take the
    chart's own daily points; 21 September is the running day and takes the
    live sample, as it always did. With no cursor only the live sample lands.
    """
    payload = golden_json("coingecko_bitcoin_chart.json")
    coin = coingecko.BY_ID["bitcoin"]
    rows = coingecko.normalize_chart(coin, payload, NOW, after=date(2026, 9, 18))
    assert [one.asof for one in rows] == [
        date(2026, 9, 19),
        date(2026, 9, 20),
        date(2026, 9, 21),
    ]
    assert rows[1].value == dec("81236.41383660372")
    assert rows[-1].value == dec("83652.92987064083")  # the live sample, not 00:00
    assert len(coingecko.normalize_chart(coin, payload, NOW)) == 1


def test_btc_dominance_is_a_percent():
    rows = coingecko.normalize_global(golden_json("coingecko_global.json"), NOW)
    assert rows[0].series_key == "btc_dominance"
    assert Decimal(40) < rows[0].value < Decimal(80)
    assert rows[0].display.endswith("%")


def test_open_interest_is_a_proxy_and_says_so():
    """Tier 2 with the method on the row. It can never claim to be a measurement."""
    rows = coingecko.normalize_open_interest(
        golden_json("coingecko_derivatives.json"), Decimal("81492"), NOW
    )
    assert rows[0].series_key == "crypto_open_interest"
    assert rows[0].tier == 2
    assert "proxy" in (rows[0].note or "")
    assert len(rows[0].extra["exchanges"]) == 10


def test_open_interest_is_withheld_with_no_spot_price():
    """Null with a reason, never a BTC-denominated number pretending to be dollars."""
    rows = coingecko.normalize_open_interest(golden_json("coingecko_derivatives.json"), None, NOW)
    assert rows[0].value is None
    assert rows[0].tier == 4
    assert "no BTC spot price" in (rows[0].note or "")


# ---------------------------------------------------------------------------
# Binance
# ---------------------------------------------------------------------------


def test_binance_funding_skew_and_extremes():
    """The median funding rate, and the single most negative one beside it.

    `funding_extremes` is numeric here rather than prose, because
    `config/series.yaml` reserves `value_kind: text` for the five Macro and
    Fiscal State rows so that a Tier 4 failure is never shaped like a reading.
    """
    rows = binance.normalize_premium_index(golden_json("binance_premium_index.json"), NOW)
    by_key = {one.series_key: one for one in rows}
    skew = by_key["funding_skew"]
    extremes = by_key["funding_extremes"]

    assert skew.display.startswith("Median ")
    assert skew.extra["n_perps"] > 500
    assert set(binance.MAJORS) <= set(skew.extra)

    assert extremes.value == Decimal(extremes.extra["most_negative"][0][1])
    assert extremes.value <= Decimal(0)
    assert len(extremes.extra["most_negative"]) == 2
    assert len(extremes.extra["most_positive"]) == 2
    assert " | " in extremes.display


def test_binance_ignores_a_perp_with_no_funding_rate():
    payload = [
        {"symbol": "BTCUSDT", "lastFundingRate": "0.0001"},
        {"symbol": "ETHUSDT", "lastFundingRate": ""},
        {"symbol": "BTCUSDC", "lastFundingRate": "0.0009"},
    ]
    rows = binance.normalize_premium_index(payload, NOW)
    skew = next(one for one in rows if one.series_key == "funding_skew")
    assert skew.extra["n_perps"] == 1  # only the USDT perp with a rate
    assert skew.value == Decimal("0.01")


# ---------------------------------------------------------------------------
# DefiLlama
# ---------------------------------------------------------------------------


def test_stablecoin_cap_totals_the_pegged_supply():
    rows = defillama.normalize_stablecoins(golden_json("llama_stablecoins.json"), NOW)
    row = rows[0]
    assert row.series_key == "stablecoin_mcap"
    assert row.value > Decimal(10) ** 11
    assert set(row.extra) == {"USDT", "USDC"}
    assert row.asof == NOW.date()  # the endpoint has no date of its own


def test_defi_tvl_takes_the_newest_dated_point():
    rows = defillama.normalize_tvl(golden_json("llama_tvl.json"), NOW)
    assert rows[-1].series_key == "defi_tvl"
    assert rows[-1].asof == date(2026, 9, 21)
    assert rows[-1].value == Decimal(93922434887)


def test_defi_tvl_restates_the_replay_window_it_already_serves():
    rows = defillama.normalize_tvl(golden_json("llama_tvl.json"), NOW)
    days = [one.asof for one in rows]
    assert days == sorted(set(days))
    assert days[0] == date(2026, 9, 21) - timedelta(days=defillama.REPLAY_DAYS)
    assert rows[-2].asof == date(2026, 9, 20)
    assert rows[-2].value == Decimal(93119786589)


# ---------------------------------------------------------------------------
# CoinMarketCap
# ---------------------------------------------------------------------------


def test_alt_season_index_and_its_label():
    rows = coinmarketcap.normalize_chart(golden_json("cmc_altseason.json"), NOW)
    assert rows[0].series_key == "alt_season_index"
    assert rows[0].tier == 2
    assert rows[0].display.endswith(("(Neutral)", "(Altcoin Season)", "(Bitcoin Season)"))


def test_alt_season_fills_only_the_days_the_store_has_not_reached():
    payload = golden_json("cmc_altseason.json")
    rows = coinmarketcap.normalize_chart(payload, NOW, after=date(2026, 9, 19))
    assert [one.asof for one in rows] == [date(2026, 9, 20), date(2026, 9, 21)]
    assert rows[0].value == Decimal(48)
    assert rows[-1].value == Decimal(51)  # the live sample, not the 00:00 point
    assert len(coinmarketcap.normalize_chart(payload, NOW)) == 1


def test_alt_season_labels_are_the_vendors_own_thresholds():
    assert coinmarketcap.season(Decimal(75)) == "Altcoin Season"
    assert coinmarketcap.season(Decimal(74)) == "Neutral"
    assert coinmarketcap.season(Decimal(25)) == "Bitcoin Season"
    assert coinmarketcap.season(Decimal(26)) == "Neutral"


# ---------------------------------------------------------------------------
# Yahoo
# ---------------------------------------------------------------------------


def test_yahoo_stores_the_session_high_and_low():
    """Without them the band-touch flag cannot exist.

    A setup whose entry band was reached and refused intraday is
    indistinguishable from one price never approached, which is exactly the
    failure `macro_mart.setup_state.band_touched_today` exists to surface.
    """
    payload = _chart(
        timestamps=[1789651800, 1789738200],
        closes=[7637.759765625, 7650.5],
        highs=[7646.85986328125, 7657.169921875],
        lows=[7611.81005859375, 7610.52001953125],
        opens=[7631.43994140625, 7657.169921875],
        regular=(1789738200, 1789761600),
    )
    rows = yahoo.normalize_chart(yahoo.BY_KEY["spx_cash"], payload, NOW)
    assert [row.asof for row in rows] == [date(2026, 9, 17), date(2026, 9, 18)]
    assert rows[-1].value == Decimal("7650.5")
    assert rows[-1].extra["session_high"] == "7657.169921875"
    assert rows[-1].extra["session_low"] == "7610.52001953125"
    assert rows[-1].extra["exchange_timezone"] == "America/New_York"


def test_yahoo_never_stores_an_unfinished_session():
    """The running session is a bar like any other in the payload.

    Storing it would put an unfinished day's close into a day that has not
    finished, which is the carry-forward rule pointed the other way.
    """
    payload = _chart(
        timestamps=[1789651800, 1789738200],
        closes=[7637.76, 7650.5],
        highs=[7646.86, 7657.17],
        lows=[7611.81, 7610.52],
        opens=[7631.44, 7657.17],
        # The session starting 1789738200 has not closed yet at `NOW`.
        regular=(1789738200, 1799999999),
    )
    rows = yahoo.normalize_chart(yahoo.BY_KEY["spx_cash"], payload, NOW)
    assert len(rows) == 1
    assert rows[0].asof == date(2026, 9, 17)


def test_yahoo_skips_a_bar_padded_with_nulls():
    payload = _chart(
        timestamps=[1789565400, 1789651800],
        closes=[None, 7637.76],
        highs=[None, 7646.86],
        lows=[None, 7611.81],
        opens=[None, 7631.44],
        regular=(1789651800, 1789675200),
    )
    rows = yahoo.normalize_chart(yahoo.BY_KEY["spx_cash"], payload, NOW)
    assert len(rows) == 1
    assert rows[0].asof == date(2026, 9, 17)


def test_dma200_is_null_below_two_hundred_closes():
    """A 200-day average over 180 days is a different statistic wearing the name."""
    bars = [
        yahoo.Bar(date(2026, 1, 1), Decimal(1), Decimal(1), Decimal(1), Decimal(1))
        for _ in range(199)
    ]
    assert yahoo.dma200(bars) == (None, None)


def test_dma200_distance_and_slope():
    """Hand-computed. 210 closes: 200 at 100, then 10 at 110.

    The newest 200-day average is (190 * 100 + 10 * 110) / 200 = 100.5, and the
    last close is 110, so the distance is 110 / 100.5 - 1 = +9.4527%.

    The average ten sessions ago is over closes 1..200, all of them 100, so it
    is 100.0 exactly. The change is +0.5%, which is above the 0.1% threshold, so
    the slope is `rising`.
    """
    closes = [Decimal(100)] * 200 + [Decimal(110)] * 10
    bars = [
        yahoo.Bar(date(2026, 1, 1), close, close, close, close) for close in closes
    ]
    distance, slope = yahoo.dma200(bars)
    assert distance is not None
    assert distance.quantize(Decimal("0.0001")) == Decimal("9.4527")
    assert slope == "rising"


def test_yahoo_refuses_a_payload_of_another_shape():
    with pytest.raises(FetchRefused, match="not the shape"):
        yahoo.normalize_chart(yahoo.BY_KEY["vix"], {"chart": {"result": []}}, NOW)


def _chart(
    *,
    timestamps: list[int],
    closes: list[float | None],
    highs: list[float | None],
    lows: list[float | None],
    opens: list[float | None],
    regular: tuple[int, int],
) -> dict:
    """A chart payload in Yahoo's own shape.

    Built here rather than captured, because Yahoo rate limited this machine for
    the whole of the capture window. The field names and nesting are taken from
    a live `^GSPC` response read on 2026-09-21, and the two closes and the two
    timestamps in the tests above are that response's real values.
    """
    return {
        "chart": {
            "error": None,
            "result": [
                {
                    "meta": {
                        "exchangeTimezoneName": "America/New_York",
                        "currentTradingPeriod": {
                            "regular": {"start": regular[0], "end": regular[1]}
                        },
                    },
                    "timestamp": timestamps,
                    "indicators": {
                        "quote": [
                            {"close": closes, "high": highs, "low": lows, "open": opens}
                        ]
                    },
                }
            ],
        }
    }


def _ctx() -> FetchContext:
    return FetchContext(now=NOW, registry=load_registry(CONFIG))


# ---------------------------------------------------------------------------
# The macro prints
# ---------------------------------------------------------------------------
#
# Every expected value below is worked by hand from the two index levels in the
# captured CSV, because the whole point of these series is that the stored
# figure is not the figure FRED serves.


def fred_batches(*series_ids: str):
    return [batch(one, golden_text(f"fred_{one}.csv")) for one in series_ids]


def print_rows(*series_ids: str):
    rows = fred.Fred().normalize(list(fred_batches(*series_ids)), _ctx())
    return {one.series_key: one for one in rows}


def test_core_cpi_is_stored_as_the_month_over_month_percent():
    """FRED serves an index level. The clause says "core CPI 0.3%".

    Hand-computed from the captured CSV:
      July 2026  336.789
      August     337.765
      337.765 / 336.789 - 1 = 0.0028979... = +0.2898%

    A condition written against 337.765 would be false for ever and would look
    entirely correct, which is why the change is computed here.
    """
    row = print_rows("CPILFESL")["cpi_core_mom"]
    assert row.value is not None
    assert row.value.quantize(Decimal("0.0001")) == Decimal("0.2898")
    assert row.display == "+0.29%"
    assert row.tier == 1
    assert row.extra["measure"] == "mom_percent"


def test_the_asof_is_the_end_of_the_data_month_not_its_first():
    """FRED dates a monthly observation at the first of the month it describes.

    `config/series.yaml` declares `monthly; asof=eom`, so the first is mapped to
    the month end. Without it every monthly release rule computes its next print
    a month early, and the row reads overdue for a fortnight every month.
    """
    row = print_rows("CPILFESL")["cpi_core_mom"]
    assert row.asof == date(2026, 8, 31)
    assert row.extra["data_month"] == "2026-08-31"


def test_core_pce_is_the_figure_the_trigger_names():
    """Hand-computed: 130.658 / 130.338 - 1 = +0.2455%.

    W40's S1 clause reads "core PCE on Sep 30 lands 0.2-0.3%", so the series it
    needs is this one and not the chain-type index.
    """
    row = print_rows("PCEPILFE")["pce_core_mom"]
    assert row.value is not None
    assert row.value.quantize(Decimal("0.0001")) == Decimal("0.2455")
    assert row.asof == date(2026, 7, 31)  # PCE runs a month behind CPI


def test_payrolls_is_the_level_change_in_thousands():
    """159,075 - 158,913 = 162, which is the "+162K" a clause quotes.

    Never the level: no clause has ever said "total nonfarm employment under
    159,075".
    """
    row = print_rows("PAYEMS")["payrolls_change"]
    assert row.value == Decimal(162)
    assert row.display == "+162K"
    assert row.extra["measure"] == "mom_change"


def test_unemployment_and_jolts_are_levels_in_the_unit_they_are_quoted_in():
    """JOLTS is served in thousands and quoted in millions.

    7,271 thousand openings is "7.271M", and a clause saying "JOLTS under 7.0M"
    against a stored 7,271 would be true for ever.
    """
    rows = print_rows("UNRATE", "JTSJOL")
    assert rows["unemployment_rate"].value == Decimal("4.1")
    assert rows["unemployment_rate"].display == "4.1%"
    assert rows["jolts_openings"].value == Decimal("7.271")
    assert rows["jolts_openings"].display == "7.271M"


def test_the_retail_control_group_is_the_total_less_its_four_exclusions():
    """The Census definition, computed because FRED carries no aggregate.

    Hand-computed from the captured CSVs:
      July   764,462 - 141,565 - 60,455 - 42,318 - 103,824 = 416,300
      August 773,947 - 142,379 - 62,303 - 42,227 - 105,070 = 421,968
      421,968 / 416,300 - 1 = +1.3615%

    Total retail over the same months is +1.2407%, so the two are different
    numbers and a condition naming the control group means the control group.
    """
    rows = print_rows("RSAFS", "RSMVPD", "RSGASS", "RSBMGESD", "RSFSDP")
    control = rows["retail_control_mom"]
    total = rows["retail_sales_mom"]
    assert control.value is not None
    assert control.value.quantize(Decimal("0.0001")) == Decimal("1.3615")
    assert total.value is not None
    assert total.value.quantize(Decimal("0.0001")) == Decimal("1.2407")
    assert control.value != total.value
    assert set(control.extra["components"]) == {
        "RSAFS", "RSMVPD", "RSGASS", "RSBMGESD", "RSFSDP"
    }


def test_the_control_group_is_withheld_when_a_component_is_missing():
    """A total missing a term is not a smaller total.

    Dropping gasoline stations would raise the control group by about 60bn and
    the figure would look entirely plausible.
    """
    rows = print_rows("RSAFS", "RSMVPD", "RSBMGESD", "RSFSDP")  # no RSGASS
    control = rows["retail_control_mom"]
    assert control.value is None
    assert control.tier == 4
    assert "RSGASS" in (control.note or "")


def test_a_print_with_only_one_month_is_withheld_rather_than_guessed():
    """A month-over-month figure needs two months."""
    one_month = "observation_date,CPILFESL\n2026-08-01,337.765\n"
    rows = fred.Fred().normalize([batch("CPILFESL", one_month)], _ctx())
    row = next(one for one in rows if one.series_key == "cpi_core_mom")
    assert row.value is None
    assert row.tier == 4
    assert "only one month" in (row.note or "")


def test_ism_has_no_fetcher_because_fred_does_not_serve_it(registry):
    """NAPM and NAPMNOI are discontinued and both answer 404, verified live."""
    assert "ism_manufacturing" not in fred.Fred().series
    assert registry.require("ism_manufacturing").fetch_path == "agent_0930"
    assert registry.require("ism_services").fetch_path == "agent_0930"


def test_the_fedwatch_probability_is_declared_but_not_fetched(registry):
    """The registry is complete so a condition naming it validates.

    It is the authoring run's to write: CME's FedWatch page is a client-rendered
    application with no keyless endpoint behind it.
    """
    numeric = registry.require("fedwatch_hike_prob_next")
    assert numeric.fetch_path == "agent_0930"
    assert numeric.value_kind == "numeric"
    assert registry.require("fedwatch_next_fomc").value_kind == "text"
    assert not any("fedwatch" in key for key in fred.Fred().series)


# ---------------------------------------------------------------------------
# The BTC 50-week moving average
# ---------------------------------------------------------------------------


def test_the_50w_average_is_fifty_weekly_closes():
    """Not a 350-day mean of daily closes, and not forty-nine weeks.

    The captured year gives 52 complete ISO weeks ending Sunday, so the average
    is over the last 50 of them and the window starts on 2025-10-12.
    """
    payload = golden_json("coingecko_bitcoin_365d.json")
    weeks = coingecko.weekly_closes(payload)
    assert len(weeks) == 52
    assert weeks[-1][0] == date(2026, 9, 20)  # a Sunday
    assert all(one.weekday() == 6 for one, _ in weeks)

    rows = coingecko.normalize_50w_ma(payload, NOW)
    assert len(rows) == 1
    row = rows[0]
    assert row.series_key == "btc_50w_ma"
    assert row.asof == date(2026, 9, 20)
    assert row.extra["weeks"] == 50
    assert row.extra["oldest_week"] == "2025-10-12"

    # The average, recomputed here from the same fifty closes.
    expected = sum((close for _, close in weeks[-50:]), Decimal(0)) / Decimal(50)
    assert row.value == expected
    assert row.tier == 1


def test_an_incomplete_week_is_not_averaged_in():
    """A week that has not reached its Sunday has no close.

    Averaging a partial week in would move the figure by whatever the last
    three days did, which is not what "50-week moving average" means.
    """
    payload = golden_json("coingecko_bitcoin_365d.json")
    weeks = coingecko.weekly_closes(payload)
    assert all(ending.weekday() == 6 for ending, _ in weeks)
    # The payload runs to Monday 2026-09-21, and that week is absent.
    assert date(2026, 9, 27) not in [ending for ending, _ in weeks]


def test_fewer_than_fifty_weeks_is_withheld_and_never_shortened():
    """A forty-three-week average wearing the name of a fifty-week one is worse
    than an absent figure."""
    payload = golden_json("coingecko_bitcoin_365d.json")
    short = {"prices": payload["prices"][-200:]}  # under thirty weeks
    rows = coingecko.normalize_50w_ma(short, NOW)
    assert rows[0].value is None
    assert rows[0].tier == 4
    assert "it needs 50" in (rows[0].note or "")
    assert rows[0].extra["weeks_available"] < 50


# ---------------------------------------------------------------------------
# Treasury auction results
# ---------------------------------------------------------------------------
#
# The captured payload is a real 120-day window and carries all four kinds the
# endpoint serves: 20 nominal notes, 8 nominal bonds, 4 TIPS, 71 bills and 4
# floating-rate notes. That mix is the point of the fixture, because two of
# those kinds would corrupt a nominal-yield series silently.


def auctions():
    return golden_json("treasurydirect_auctioned.json")


def upcoming():
    return golden_json("treasurydirect_upcoming.json")


def auction_rows():
    rows = treasury_auctions.normalize_auctions(auctions(), upcoming(), NOW)
    return {one.series_key: one for one in rows}


def test_the_captured_payload_carries_all_four_kinds():
    """A filter tested against a payload of one kind tests nothing."""
    kinds = Counter(one.get("type") for one in auctions())
    assert kinds["Note"] > 0
    assert kinds["Bond"] > 0
    assert kinds["TIPS"] > 0
    assert kinds["Bill"] > 0
    assert kinds["FRN"] > 0


def test_the_twenty_year_matches_the_journal():
    """W39: "the 20Y tailed 2 bp at a record 5.42%".

    The 2026-09-15 auction is a reopening whose `securityTerm` reads 19-Year
    11-Month, so it is keyed on `originalSecurityTerm` and lands on the 20-Year.
    """
    rows = auction_rows()
    high = rows["auction_high_yield_20y"]
    cover = rows["auction_bid_to_cover_20y"]
    assert high.asof == date(2026, 9, 15)
    assert high.value == Decimal("5.4200")
    assert high.display == "5.420%"
    assert cover.value == Decimal("2.570000")
    assert cover.display == "2.57x"
    assert high.extra["security_term"] == "19-Year 11-Month"
    assert high.extra["reopening"] == "Yes"


def test_a_tips_reopening_never_reaches_the_nominal_series():
    """The trap, asserted directly.

    A TIPS reopening on 2026-09-17 carries `originalSecurityTerm: 10-Year` and
    `highYield: 2.6530`, which is a real yield. The nominal ten-year auctioned
    on 2026-09-09 at 4.8340. Unfiltered, the ten-year series would take 2.653:
    plausible alone, wrong in every condition, and invisible on a row that says
    only "10-Year auction, 2.65%".
    """
    trap = [
        one
        for one in auctions()
        if one.get("tips") == "Yes" and one.get("originalSecurityTerm") == "10-Year"
    ]
    assert trap, "the fixture must contain the TIPS trap"
    assert any(one["highYield"] == "2.6530" for one in trap)

    ten_year = auction_rows()["auction_high_yield_10y"]
    assert ten_year.value == Decimal("4.8340")
    assert ten_year.value != Decimal("2.6530")
    assert ten_year.asof == date(2026, 9, 9)


def test_bills_and_floating_rate_notes_are_excluded():
    """A bill has no `highYield` at all and its tenor names the bill it reopens.

    The 4-week bill auctioned on 2026-09-17 reads `originalSecurityTerm:
    17-Week`, which is not a tenor any clause means.
    """
    for one in auctions():
        if one.get("securityType") == "Bill":
            assert not treasury_auctions.is_nominal_coupon(one)
        if one.get("floatingRate") == "Yes":
            assert not treasury_auctions.is_nominal_coupon(one)
        if one.get("tips") == "Yes":
            assert not treasury_auctions.is_nominal_coupon(one)


def test_the_filter_reads_stated_fields_and_infers_nothing():
    """Each exclusion is a field the feed states, not a heuristic."""
    nominal = {
        "tips": "No",
        "floatingRate": "No",
        "securityType": "Note",
        "originalSecurityTerm": "10-Year",
    }
    assert treasury_auctions.is_nominal_coupon(nominal)
    assert not treasury_auctions.is_nominal_coupon({**nominal, "tips": "Yes"})
    assert not treasury_auctions.is_nominal_coupon({**nominal, "floatingRate": "Yes"})
    assert not treasury_auctions.is_nominal_coupon({**nominal, "securityType": "Bill"})
    assert not treasury_auctions.is_nominal_coupon(
        {**nominal, "originalSecurityTerm": "17-Week"}
    )


def test_every_tenor_gets_two_series_and_no_more():
    rows = auction_rows()
    assert len(rows) == 14
    for suffix in ("2y", "3y", "5y", "7y", "10y", "20y", "30y"):
        assert f"auction_high_yield_{suffix}" in rows
        assert f"auction_bid_to_cover_{suffix}" in rows


def test_the_newest_auction_of_each_tenor_wins():
    """The fixture holds four auctions of each tenor; a clause names the latest."""
    nominal = [one for one in auctions() if treasury_auctions.is_nominal_coupon(one)]
    by_tenor = Counter(one["originalSecurityTerm"] for one in nominal)
    assert set(by_tenor.values()) == {4}

    rows = auction_rows()
    for tenor, suffix in treasury_auctions.TENORS.items():
        latest = max(
            (one for one in nominal if one["originalSecurityTerm"] == tenor),
            key=lambda one: one["auctionDate"],
        )
        assert rows[f"auction_high_yield_{suffix}"].asof.isoformat() == latest["auctionDate"][:10]


def test_there_is_no_tail_series_and_extra_says_why():
    """A tail is the high yield less the when-issued, and Treasury publishes
    no when-issued. A series called `tail` carrying anything else would be
    wrong in the way that is hardest to catch."""
    rows = auction_rows()
    assert not any("tail" in key for key in rows)

    extra = rows["auction_high_yield_20y"].extra
    assert extra["tail"] is None
    assert "when-issued" in extra["tail_note"]
    # The figures a tail could be faked from are carried, and never combined.
    assert extra["average_median_yield"] is not None
    assert extra["low_yield"] is not None


def test_the_next_auction_date_comes_from_the_schedule_and_not_from_a_rule(registry):
    """An auction has no cadence, so its rule is `event` and says nothing.

    TreasuryDirect publishes the schedule, so the row states it anyway.
    """
    assert registry.require("auction_high_yield_2y").release_rule.raw == "event"
    assert registry.require("auction_high_yield_2y").cadence == "event"

    rows = auction_rows()
    assert rows["auction_high_yield_2y"].extra["next_expected"] == "2026-09-22"
    assert rows["auction_bid_to_cover_5y"].extra["next_expected"] == "2026-09-23"
    # Nothing is invented for a tenor the schedule does not yet cover.
    assert "next_expected" not in rows["auction_high_yield_20y"].extra


def test_the_schedule_filter_is_the_same_one():
    """An upcoming TIPS reopening must not set a nominal tenor's next date."""
    poisoned = [
        *upcoming(),
        {
            "auctionDate": "2026-09-19T00:00:00",
            "originalSecurityTerm": "2-Year",
            "securityType": "Note",
            "tips": "Yes",
            "floatingRate": "No",
        },
    ]
    dates = treasury_auctions.next_auction_dates(poisoned)
    assert dates["2-Year"] == date(2026, 9, 22)  # not the 19th


def test_an_auction_with_no_high_yield_is_withheld_rather_than_skipped():
    """Null with a reason, so the row is visibly absent rather than stale."""
    broken = [
        {
            "auctionDate": "2026-09-15T00:00:00",
            "originalSecurityTerm": "20-Year",
            "securityTerm": "19-Year 11-Month",
            "securityType": "Bond",
            "type": "Bond",
            "tips": "No",
            "floatingRate": "No",
            "highYield": "",
            "bidToCoverRatio": "2.570000",
        }
    ]
    rows = {
        one.series_key: one
        for one in treasury_auctions.normalize_auctions(broken, [], NOW)
    }
    assert rows["auction_high_yield_20y"].value is None
    assert rows["auction_high_yield_20y"].tier == 4
    assert "no highYield" in (rows["auction_high_yield_20y"].note or "")
    assert rows["auction_bid_to_cover_20y"].value == Decimal("2.570000")


def test_a_payload_of_nothing_but_bills_refuses_rather_than_reporting_nothing():
    """An empty result after filtering is a fault, not a quiet day."""
    bills = [one for one in auctions() if one.get("securityType") == "Bill"]
    with pytest.raises(FetchRefused, match="no nominal note or bond"):
        treasury_auctions.normalize_auctions(bills, [], NOW)


# Both captured from the live chart API at 2026-09-23 03:18 UTC, which is 11:18
# Singapore, inside the window between the US close and the next New York day.
YAHOO_CAPTURED = instant("2026-09-23T03:18:43")


def test_a_future_keeps_yesterdays_settled_bar_while_todays_is_running():
    """ES=F must not fall a day behind the cash index on the 09:30 run.

    Yahoo gives a 24-hour future a "regular" period of the whole New York day,
    04:00 to 03:59 UTC, so the settled 22 September bar (stamped 04:00) sits
    inside it. The running bar is stamped at its last trade, 03:08 UTC, which in
    New York is still 22 September. The settled bar is the one to keep.
    """
    rows = yahoo.normalize_chart(
        yahoo.BY_KEY["spx_front"], golden_json("yahoo_es_f_5d.json"), YAHOO_CAPTURED
    )
    assert rows[-1].asof == date(2026, 9, 22)
    assert rows[-1].value == Decimal("7831.75")
    # The running bar, stamped 22 September in New York, did not overwrite it.
    assert [row.asof for row in rows].count(date(2026, 9, 22)) == 1


def test_a_cash_index_after_its_close_keeps_the_closed_session():
    rows = yahoo.normalize_chart(
        yahoo.BY_KEY["spx_cash"], golden_json("yahoo_gspc_5d.json"), YAHOO_CAPTURED
    )
    assert rows[-1].asof == date(2026, 9, 22)
    assert "dma200_pct" in rows[-1].extra


def test_every_restated_bar_carries_its_own_200_day_read():
    """The replay re-states 45 days, and the upsert replaces `extra` wholesale.

    A row re-stated without its 200-day read erases the one stored for it on an
    earlier run. Every re-stated bar therefore carries the read computed from
    the closes up to and including that bar, which is what a run on that day
    would have stored.
    """
    day = 86_400
    count = 260
    last = 1789738200
    stamps = [last - day * (count - 1 - i) for i in range(count)]
    closes = [100.0 + i for i in range(count)]
    payload = _chart(
        timestamps=stamps,
        closes=closes,
        highs=closes,
        lows=closes,
        opens=closes,
        regular=(last, last + 23_400),
    )
    rows = yahoo.normalize_chart(yahoo.BY_KEY["spx_cash"], payload, NOW)
    bars, _ = yahoo._bars(payload, NOW)
    assert len(rows) > 30
    by_session = {bar.session: i for i, bar in enumerate(bars)}
    for row in rows:
        i = by_session[row.asof]
        distance, slope = yahoo.dma200(bars[: i + 1])
        assert distance is not None
        assert row.extra["dma200_pct"] == str(distance)
        assert row.extra["dma200_slope"] == slope
    # The oldest re-stated read differs from the newest: it is its own day's.
    assert rows[0].extra["dma200_pct"] != rows[-1].extra["dma200_pct"]


def test_every_recent_bar_is_restated_so_a_null_bar_filled_later_lands():
    """A replayable gap is only replayable if some run asks for it again.

    Yahoo served a null 22 September bar for YM=F, RTY=F, SI=F, BZ=F and the
    dollar on 23 September. When it fills that bar in, the next run must store
    it even though a newer session is already stored.
    """
    payload = _chart(
        timestamps=[1789565400, 1789651800, 1789738200],
        closes=[7551.81, 7637.76, 7650.5],
        highs=[7626.79, 7646.86, 7657.17],
        lows=[7507.77, 7611.81, 7610.52],
        opens=[7601.25, 7631.44, 7657.17],
        regular=(1789738200, 1789761600),
    )
    rows = yahoo.normalize_chart(yahoo.BY_KEY["spx_cash"], payload, NOW)
    assert [row.asof for row in rows] == [
        date(2026, 9, 16),
        date(2026, 9, 17),
        date(2026, 9, 18),
    ]


def test_bars_older_than_the_replay_window_are_not_restated():
    day = 86_400
    stamps = [1789738200 - day * back for back in (60, 50, 44, 0)]
    payload = _chart(
        timestamps=stamps,
        closes=[1.0, 2.0, 3.0, 4.0],
        highs=[1.0, 2.0, 3.0, 4.0],
        lows=[1.0, 2.0, 3.0, 4.0],
        opens=[1.0, 2.0, 3.0, 4.0],
        regular=(1789738200, 1789761600),
    )
    rows = yahoo.normalize_chart(yahoo.BY_KEY["spx_cash"], payload, NOW)
    assert [row.value for row in rows] == [Decimal(3), Decimal(4)]


# ---------------------------------------------------------------------------
# CNN Fear and Greed
# ---------------------------------------------------------------------------
#
# Captured from `graphdata/2026-08-08` at 2026-09-23 04:55:57 UTC, which is
# 00:55 Wednesday in New York: Tuesday's session had closed and Wednesday's had
# not opened. The payload's historical points run from Monday 10 August to a
# Wednesday 23 September point served twice, both carrying Tuesday's score.

CNN_CAPTURED = instant("2026-09-23T04:55:57")


def cnn_rows(now=CNN_CAPTURED):
    return cnn.normalize_graphdata(golden_json("cnn_fear_greed_graphdata.json"), now)


def test_cnn_newest_row_is_the_last_closed_session_as_the_dial_prints_it():
    """35.2285714285714 is `35 (Fear)`, the same shape as the migrated `29 (Fear)`.

    The unrounded score and CNN's own rating word travel in `extra`.
    """
    rows = cnn_rows()
    newest = rows[-1]
    assert newest.series_key == "cnn_fear_greed"
    assert newest.asof == date(2026, 9, 22)
    assert newest.value == Decimal(35)
    assert newest.display == "35 (Fear)"
    assert newest.tier == 1
    assert newest.extra == {"score": "35.2285714285714", "rating": "fear"}
    # The close of the session it describes: 16:00 New York, EDT.
    assert newest.observed_at == instant("2026-09-22T20:00:00")


def test_cnn_never_stores_the_session_that_has_not_happened():
    """The Wednesday point exists before Wednesday opens, carrying Tuesday's score.

    Storing it would date Tuesday's reading Wednesday. It is not stored, and the
    duplicated point does not become a second row either.
    """
    rows = cnn_rows()
    assert date(2026, 9, 23) not in {row.asof for row in rows}
    assert len({row.asof for row in rows}) == len(rows)


def test_cnn_drops_the_running_session_during_the_new_york_day():
    """At 14:00 New York on Tuesday the Tuesday point is still moving."""
    rows = cnn_rows(instant("2026-09-22T18:00:00"))
    assert rows[-1].asof == date(2026, 9, 21)
    assert rows[-1].display == "34 (Fear)"  # 34.1714285714286


def test_cnn_restates_every_session_of_the_replay_window():
    """Every NYSE session from 10 August to 22 September, and nothing else.

    Labor Day, 7 September, has no point and no row: the payload skips it, and
    a session series has nothing to say about a day the market was shut.
    """
    rows = cnn_rows()
    asofs = [row.asof for row in rows]
    assert asofs == list(clock.us_sessions_between(date(2026, 8, 10), date(2026, 9, 22)))
    assert date(2026, 9, 7) not in asofs
    by_day = {row.asof: row for row in rows}
    assert by_day[date(2026, 9, 18)].display == "30 (Fear)"  # 30.43
    assert by_day[date(2026, 8, 13)].display == "66 (Greed)"  # 66.11
    assert by_day[date(2026, 8, 18)].display == "55 (Neutral)"  # 54.63


def test_cnn_points_older_than_the_replay_window_are_not_restated():
    day = 86_400_000
    tuesday = 1790035200000  # 2026-09-22 00:00 UTC
    payload = {
        "fear_and_greed_historical": {
            "data": [
                {"x": tuesday - day * back, "y": float(back), "rating": "fear"}
                for back in (48, 45, 42, 0)
            ]
        }
    }
    rows = cnn.normalize_graphdata(payload, CNN_CAPTURED)
    # 48 days back is 5 August, outside; 45 back is 8 August, a Saturday, so it
    # is not a session at all; 42 back is Tuesday 11 August, inside.
    assert [row.asof for row in rows] == [date(2026, 8, 11), date(2026, 9, 22)]


def test_cnn_rating_words_are_title_cased_and_the_value_rounds_half_up():
    tuesday = 1790035200000
    payload = {
        "fear_and_greed_historical": {
            "data": [{"x": tuesday, "y": 74.5, "rating": "extreme greed"}]
        }
    }
    (row,) = cnn.normalize_graphdata(payload, CNN_CAPTURED)
    assert row.value == Decimal(75)
    assert row.display == "75 (Extreme Greed)"
    assert row.extra["rating"] == "extreme greed"


def test_cnn_refuses_a_payload_of_another_shape():
    with pytest.raises(FetchRefused, match="not the shape"):
        cnn.normalize_graphdata({"fear_and_greed": {"score": 35}}, CNN_CAPTURED)
    with pytest.raises(FetchRefused, match="no Fear and Greed readings"):
        cnn.normalize_graphdata({"fear_and_greed_historical": {"data": []}}, CNN_CAPTURED)


def test_cnn_window_is_opened_on_the_new_york_calendar():
    """At 00:55 Wednesday New York the request starts 45 days before that Wednesday."""
    assert cnn.url(CNN_CAPTURED).endswith("/graphdata/2026-08-09")


def test_cnn_is_a_deterministic_replayable_session_series(registry):
    """The registry and the fetcher agree on what this series now is."""
    from macro.fetchers import deterministic

    series = registry.require("cnn_fear_greed")
    assert series.fetch_path == "deterministic"
    assert series.replayable is True
    assert series.release_rule.raw == "us_session"
    assert series.unit == "index_0_100"
    assert series.decimals == 0
    assert any("cnn_fear_greed" in one.series for one in deterministic())

