"""Paths, week scope and the asserted series map.

Nothing here is discovered at runtime: the migration is a one-shot import of a
fixed, known set of files, and every path and every week is named so that a
reviewer can see the whole scope in one screen.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
MIGRATION = REPO / "migration"
OUT = MIGRATION / "out"

VAULT = Path(
    os.environ.get("VAULT_PATH", "/Users/rayclaw/Obsidian/rayobs")
).resolve()
MACRO = VAULT / "3 - Work" / "31 - Macro"
DATA = MACRO / "data"

SCORECARD_LEDGER = MACRO / "Scorecard Ledger.md"
SETUP_LEDGER = MACRO / "Setup Ledger.md"
SCENARIO_RULES = MACRO / "scenario-rules.md"

SERIES_YAML = REPO / "config" / "series.yaml"

# Weeks whose sidecar JSON is imported directly.
SIDECAR_WEEKS = ("2026-W36", "2026-W37", "2026-W38", "2026-W39")

# Weeks bootstrapped by running the vendored legacy parser over the journal.
# 2026-W33 has no journal: the W32 scenario set was graded over two weeks.
LEGACY_WEEKS = ("2026-W32", "2026-W34", "2026-W35")

# Weeks whose judgment tables this migration produces, in order.
JUDGMENT_WEEKS = tuple(sorted(LEGACY_WEEKS + SIDECAR_WEEKS))

# The ledger is migrated in full from here.
LEDGER_FIRST_WEEK = "2026-W23"
# The Setup Ledger is migrated in full from here.
SETUP_FIRST_WEEK = "2026-W35"
# Deliberately not imported.
ARCHIVE_EXCLUDED = "2026-W03 .. 2026-W31"

# Which journal graded which week. The row for week N is written by journal N+1.
# 2026-W32 was graded in the 2026-W34 journal because no W33 journal exists.
GRADED_BY = {
    "2026-W32": "2026-W34",
    "2026-W34": "2026-W35",
    "2026-W35": "2026-W36",
    "2026-W36": "2026-W37",
    "2026-W37": "2026-W38",
    "2026-W38": "2026-W39",
}

# The as-of date each journal was authored on, read from its own header line.
# Used for authored_at / activated_at / graded_at. Weeks graded by an archive
# journal this migration does not read carry a null graded_at instead.
AUTHORED_ON = {
    "2026-W32": "2026-08-03",
    "2026-W34": "2026-08-17",
    "2026-W35": "2026-08-24",
    "2026-W36": "2026-08-31",
    "2026-W37": "2026-09-09",
    "2026-W38": "2026-09-14",
    "2026-W39": "2026-09-21",
}

# The observation date the whole snapshot of a legacy week describes, taken from
# that journal's own "Authored as-of" header. The legacy sidecar shape carries no
# per-row asof, so one date stands for the table and every such row is flagged.
LEGACY_SNAPSHOT_ASOF = {
    "2026-W32": "2026-07-31",
    "2026-W34": "2026-08-14",
    "2026-W35": "2026-08-21",
    "2026-W36": "2026-08-28",
}

# Series map. label as written in the journal -> (key, unit, value_kind).
# `key` is the stable identifier CONTRACT.md requires every condition to be
# written against. Until config/series.yaml exists these keys are *proposed*:
# out/series_keys_proposed.json carries them for reconciliation.
#
# Unit is the canonical unit the contract pins to the series, not the unit the
# journal printed. HY and IG option-adjusted spreads are basis points here even
# though the journal writes "2.70% (270 bps)".
NUM = "numeric"
TXT = "text"

# Loaded from config/series.yaml at import time when it exists, so a key can never
# drift from the file the database is seeded from. The tuple is (key, unit,
# value_kind); unit and value_kind come from series.yaml, never from here.
SERIES_LABEL_TO_KEY: dict[str, str] = {
    # Price & Levels
    "S&P 500 (ES=F)": "spx_front",
    "Dow Jones (YM=F)": "dow_front",
    "Russell 2000 (RTY=F)": "rut_front",
    "Nasdaq Composite (cash)": "nasdaq_cash",
    "BTC": "btc",
    "ETH": "eth",
    "SOL": "sol",
    "Gold (GC=F)": "gold",
    "Silver (SI=F)": "silver",
    "WTI Crude (CL=F)": "wti",
    "Brent Crude (BZ=F)": "brent",
    "US Dollar (DXY)": "dxy",
    "US Dollar (DXY proxy)": "dxy",
    "VIX": "vix",
    "30Y Treasury Yield": "ust_30y",
    "10Y Treasury Yield": "ust_10y",
    "2Y Treasury Yield": "ust_2y",
    # Sentiment & Positioning
    "CNN Equities F&G": "cnn_fear_greed",
    "Crypto F&G (Alt.me)": "crypto_fear_greed",
    "State Street institutional equity allocation": "state_street_equity_allocation",
    "CFTC Asset Managers, ES net futures": "cftc_asset_managers_es",
    "CFTC Leveraged Funds, ES net futures": "cftc_leveraged_funds_es",
    "% S&P above 200dma": "breadth_spx_above_200dma",
    "HY credit spread (OAS)": "hy_oas_bps",
    "IG credit spread (OAS)": "ig_oas_bps",
    "MOVE index": "move",
    "BTC Dominance": "btc_dominance",
    "Funding skew": "funding_skew",
    # Flows & Structure
    "Net liquidity (Fed BS \u2212 TGA \u2212 RRP)": "net_liquidity",
    "BTC ETF weekly flow": "btc_etf_weekly_flow",
    "BTC ETF inflow streak": "btc_etf_inflow_streak",
    "ETH ETF weekly flow": "eth_etf_weekly_flow",
    "ETH ETF inflow streak": "eth_etf_inflow_streak",
    "Stablecoin Market Cap": "stablecoin_mcap",
    "Total DeFi TVL": "defi_tvl",
    "Total Crypto OI": "crypto_open_interest",
    "Funding extremes": "funding_extremes",
    "24h Liquidations": "liquidations_24h",
    "Alt season index": "alt_season_index",
    # Macro & Fiscal State
    "Fed funds": "fed_funds",
    "Next FOMC": "next_fomc",
    "CME FedWatch (next FOMC)": "fedwatch_next_fomc",
    "CME FedWatch (Sep 16)": "fedwatch_next_fomc",
    "CME FedWatch": "fedwatch_next_fomc",
    "Tariff posture": "tariff_state",
    "Active tariff policy": "tariff_state",
    "War / geopolitical state": "geopolitical_state",
}

# Snapshot rows that config/series.yaml deliberately does not carry. They are not
# imported, and VERIFICATION.md names each one and the weeks it appeared in.
UNMAPPED_LABELS = {
    "NAAIM Exposure":
        "config/series.yaml carries no NAAIM series; the journal dropped the row "
        "after 2026-W32 and the ledger records that NAAIM went dark mid-arc.",
    "Coinbase Premium / CME basis":
        "config/series.yaml carries no key for it; the journal dropped the row "
        "after 2026-W35.",
    "Alt OI (excl. BTC/ETH)":
        "config/series.yaml carries crypto_open_interest only; the journal dropped "
        "the row after 2026-W36.",
    "7-day Liquidations":
        "config/series.yaml carries liquidations_24h only; the journal dropped the "
        "row after 2026-W36.",
}

# Cash indices are their own series, carried in the sidecar row's `extra`.
CASH_OF = {
    "spx_front": "spx_cash",
    "dow_front": "dow_cash",
    "rut_front": "rut_cash",
}


def load_series_yaml() -> dict[str, dict]:
    """config/series.yaml, parsed without a YAML dependency.

    The file is a flat list of twelve scalar keys per series, which is a small
    enough grammar to read directly; a dependency would be a second thing to
    keep in step with the schema agent's file.
    """
    if not SERIES_YAML.exists():
        return {}
    out: dict[str, dict] = {}
    cur: dict | None = None
    for raw in SERIES_YAML.read_text(encoding="utf-8").splitlines():
        line = raw.split("#")[0].rstrip() if not raw.strip().startswith("#") else ""
        if not line.strip():
            continue
        m = re.match(r"^  - key:\s*(\S+)\s*$", line)
        if m:
            cur = {"key": m.group(1)}
            out[cur["key"]] = cur
            continue
        if cur is None:
            continue
        m = re.match(r"^    (\w+):\s*(.*)$", line)
        if not m:
            continue
        k, v = m.group(1), m.group(2).strip()
        if v in ("null", ""):
            v = None
        elif v in ("true", "false"):
            v = v == "true"
        elif re.fullmatch(r"-?\d+", v):
            v = int(v)
        else:
            v = v.strip("'\"")
        cur[k] = v
    return out


SERIES_YAML_ROWS = load_series_yaml()

# Subjects named inside a scenario target band, mapped to config/series.yaml keys.
TARGET_SUBJECTS: tuple[tuple[str, str], ...] = (
    (r"s&p", "spx_cash"),
    (r"nasdaq composite", "nasdaq_cash"),
    (r"nasdaq", "nasdaq_cash"),
    (r"russell", "rut_cash"),
    (r"\bdow\b", "dow_cash"),
    (r"breadth", "breadth_spx_above_200dma"),
    (r"hy oas", "hy_oas_bps"),
    (r"\bhy\b", "hy_oas_bps"),
    (r"\big\b", "ig_oas_bps"),
    (r"\bvix\b", "vix"),
    (r"\bmove\b", "move"),
    (r"btc etf", "btc_etf_weekly_flow"),
    (r"eth etf", "eth_etf_weekly_flow"),
    (r"\bbtc\b", "btc"),
    (r"bitcoin", "btc"),
    (r"\beth\b", "eth"),
    (r"\bsol\b", "sol"),
    (r"\b30y\b", "ust_30y"),
    (r"\b10y\b", "ust_10y"),
    (r"\b2y\b", "ust_2y"),
    (r"\bgold\b", "gold"),
    (r"\bsilver\b", "silver"),
    (r"\bbrent\b", "brent"),
    (r"\bwti\b", "wti"),
    (r"\bcrude\b", "brent"),
    (r"\bdxy\b", "dxy"),
)
