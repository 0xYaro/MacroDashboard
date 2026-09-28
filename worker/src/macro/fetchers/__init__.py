"""The live set of deterministic sources.

One module per source. Adding one here is the whole of adding a source: the run
loop iterates this tuple, and `macro_quality.ingest_run` gets a row per entry
whether it worked or not.

The search-agent series in `config/series.yaml` have no module here on purpose.
They are fetched on the 09:30 run only, they cannot be made deterministic, and
`docs/FUTURE.md` records what it would take to replace each one with a scraper.
"""

from __future__ import annotations

from .alternative_me import AlternativeMe
from .base import FetchContext, Fetcher, run_one
from .binance import Binance
from .breadth import Breadth
from .cftc import Cftc
from .cnn import Cnn
from .coingecko import CoinGecko
from .coinmarketcap import CoinMarketCap
from .defillama import DefiLlama
from .etf_flows import EtfFlows
from .fed_funds_futures import FedFundsFutures
from .fred import Fred
from .treasury import Treasury
from .treasury_auctions import TreasuryAuctions
from .yahoo import Yahoo


def deterministic() -> tuple[Fetcher, ...]:
    """Every source both runs are responsible for, in the order they are fetched.

    Yahoo goes first because the crypto open-interest proxy needs a BTC spot
    price and CoinGecko is paced, so putting the paced source early gives the
    rest of the run something to do while it waits.
    """
    out: tuple[Fetcher, ...] = (
        Yahoo(),
        Treasury(),
        TreasuryAuctions(),
        Fred(),
        Breadth(),
        CoinGecko(),
        AlternativeMe(),
        Cnn(),
        Cftc(),
        Binance(),
        DefiLlama(),
        CoinMarketCap(),
        EtfFlows(),
        FedFundsFutures(),
    )
    return out


__all__ = [
    "AlternativeMe",
    "Binance",
    "Breadth",
    "Cftc",
    "Cnn",
    "CoinGecko",
    "CoinMarketCap",
    "DefiLlama",
    "EtfFlows",
    "FedFundsFutures",
    "FetchContext",
    "Fetcher",
    "Fred",
    "Treasury",
    "TreasuryAuctions",
    "Yahoo",
    "deterministic",
    "run_one",
]
