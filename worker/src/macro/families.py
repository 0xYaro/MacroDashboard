"""Which recurring kind of event a catalyst is, read from its name.

The archive exists so that next year's review can ask "which kinds of catalyst
moved markets, and do they come round again". That question is asked of the
family, not of the name: "August Consumer Price Index" and "July CPI" are the
same family, `us_cpi`, and a year of them is twelve rows under one key.

A family is a recurring kind. A one-off event, a summit or a court ruling, has
no family and is `None`; it still carries a category. The table is ordered and
the first match wins, so a narrower rule sits above a broader one that would
also match: "FOMC minutes" above "FOMC", "ISM Services" above "PMI".

Nothing here is inferred by a model. The same name always gives the same
family, on the backfill and on every daily run after it.
"""

from __future__ import annotations

import re
from typing import Literal

Category = Literal[
    "us_macro",
    "fed",
    "global_macro",
    "treasury_liquidity",
    "index_options",
    "global_central_bank",
    "earnings",
    "ai_tech",
    "crypto",
    "trade_tariffs",
    "policy_geopolitics",
    "commodities_energy",
    "market_closure",
    "investor_forum",
    "market_structure",
    "other",
]

CATEGORIES: tuple[Category, ...] = (
    "us_macro",
    "fed",
    "global_macro",
    "treasury_liquidity",
    "index_options",
    "global_central_bank",
    "earnings",
    "ai_tech",
    "crypto",
    "trade_tariffs",
    "policy_geopolitics",
    "commodities_energy",
    "market_closure",
    "investor_forum",
    "market_structure",
    "other",
)

#: Economies whose prints the calendar carries, as (family prefix, name pattern).
_ECONOMIES: tuple[tuple[str, str], ...] = (
    ("japan", r"Japan(ese)?|Tokyo"),
    ("eurozone", r"Euro ?zone|Euro[- ]area|EU|HICP"),
    ("germany", r"German(y)?"),
    ("france", r"France|French"),
    ("uk", r"UK|U\.K\.|United Kingdom|Brit(ain|ish)"),
    ("canada", r"Canad(a|ian)"),
    ("australia", r"Australian?"),
    ("china", r"China|Chinese"),
)

#: The prints that exist under the same words in the U.S. calendar.
_INDICATORS: tuple[tuple[str, str], ...] = (
    ("cpi", r"CPI|HICP|Consumer Price|inflation"),
    ("ppi", r"PPI|Producer Price"),
    ("gdp", r"GDP"),
    ("retail_sales", r"Retail Sales|Retail Trade"),
    ("jobs", r"unemployment|jobs|employment|labou?r (force|market)"),
)

#: A central bank's report names inflation without being a print.
_NOT_A_PRINT = r"(?!.*\b(Bank of|BOJ|BOE|ECB|RBA|central bank)\b)"


def _foreign_prints() -> tuple[tuple[str, str | None, Category], ...]:
    """One rule per economy and print, the country named before or after the print."""
    out: list[tuple[str, str | None, Category]] = []
    for economy, where in _ECONOMIES:
        for indicator, what in _INDICATORS:
            pattern = (
                rf"^{_NOT_A_PRINT}.*(\b({where})\b.*\b({what})\b|\b({what})\b.*\b({where})\b)"
            )
            out.append((pattern, f"{economy}_{indicator}", "global_macro"))
    return tuple(out)


# (pattern, family, category). Case-insensitive, searched anywhere in the name.
_FAMILIES: tuple[tuple[str, str | None, Category], ...] = (
    # Fed, narrowest first.
    (r"Beige Book", "fed_beige_book", "fed"),
    (r"\bFOMC\b.*\bminutes\b|\bminutes\b.*\bFOMC\b", "fomc_minutes", "fed"),
    (r"\bFOMC\b", "fomc_decision", "fed"),
    (r"Jackson Hole", "jackson_hole", "fed"),
    (r"stress[- ]test", "fed_stress_test", "fed"),
    (r"\bSLOOS\b|Senior Loan Officer", "fed_sloos", "fed"),
    (r"\bSCOOS\b|Senior Credit Officer", "fed_scoos", "fed"),
    (
        r"(Semiannual|Humphrey|Monetary Policy Report).*testimony|"
        r"testimony.*Monetary Policy Report",
        "fed_chair_testimony",
        "fed",
    ),
    (r"Industrial Production", "us_industrial_production", "us_macro"),
    (
        r"\b(Fed|Federal Reserve)\b.*\b(speech(es)?|remarks|panel|fireside|speaks?)\b|"
        # A governor's surname, not a company or a CEO that shares it:
        # "Williams-Sonoma earnings call remarks", "Tim Cook remarks".
        r"(?<!Tim )\b(Waller|Jefferson|Cook|Bowman|Barr|Williams|Goolsbee|Logan|"
        r"Kashkari)\b(?!-).*\b(speech(es)?|remarks|panel)\b",
        "fed_speech",
        "fed",
    ),
    # Who runs the Fed, after the speech rule so a governor's speech stays a speech.
    (
        r"\b(nominat\w*|confirm\w*|removal|takes over)\b.*\b(Fed|Warsh|Powell|Cook)\b|"
        r"\b(Fed|Warsh|Powell|Cook)\b.*\b(nominat\w*|confirm\w*|removal|takes over)\b|"
        r"Powell (probe|term)",
        "fed_leadership",
        "fed",
    ),
    # U.S. macro prints, after every non-U.S. print whose words overlap them.
    (r"China .*(GDP|activity data)", "china_activity_data", "global_macro"),
    *_foreign_prints(),
    (r"\bCPI\b|Consumer Price Index", "us_cpi", "us_macro"),
    (r"\bPPI\b|Producer Price Index", "us_ppi", "us_macro"),
    (r"Import and Export Price", "us_import_export_prices", "us_macro"),
    (r"payroll benchmark|benchmark revision", "us_payroll_benchmark", "us_macro"),
    (r"\bNFP\b|Employment Situation|Nonfarm Payrolls", "us_nfp", "us_macro"),
    (r"\bADP\b", "us_adp", "us_macro"),
    (r"\bJOLTS\b", "us_jolts", "us_macro"),
    (r"Employment Cost Index|\bECI\b", "us_eci", "us_macro"),
    (r"\bPCE\b|Personal Income and Outlays", "us_pce", "us_macro"),
    (r"\bGDP\b", "us_gdp", "us_macro"),
    (r"Retail Sales|Retail Trade", "us_retail_sales", "us_macro"),
    (r"ISM Services", "us_ism_services", "us_macro"),
    (r"ISM Manufacturing", "us_ism_manufacturing", "us_macro"),
    (r"flash .*PMI", "global_flash_pmi", "us_macro"),
    (r"Michigan|consumer sentiment", "us_umich_sentiment", "us_macro"),
    (r"Conference Board.*Confidence", "us_conference_board_confidence", "us_macro"),
    (r"Durable Goods", "us_durable_goods", "us_macro"),
    (
        r"Residential Construction|housing starts|New Residential Sales|new home sales",
        "us_housing",
        "us_macro",
    ),
    (r"Trade in Goods and Services|Advance Economic Indicators", "us_trade_balance", "us_macro"),
    (
        r"Productivity and (Unit Labor )?Costs|productivity and unit labor",
        "us_productivity",
        "us_macro",
    ),
    (r"International Transactions", "us_international_transactions", "us_macro"),
    (r"Treasury International Capital|\bTIC\b", "us_tic", "us_macro"),
    # Treasury and liquidity.
    (r"quarterly refunding|\bQRA\b", "treasury_refunding", "treasury_liquidity"),
    (r"Monthly Treasury Statement", "treasury_monthly_statement", "treasury_liquidity"),
    (
        r"\bauction\b|\b(\d+Y|TIPS|bond|note)\b.*\breopening\b",
        "treasury_auction",
        "treasury_liquidity",
    ),
    (r"estimated-tax|tax payment|tax deadline", "us_tax_date", "treasury_liquidity"),
    (r"funding deadline|shutdown|debt[- ]ceiling", "us_funding_deadline", "treasury_liquidity"),
    # Index and options mechanics.
    (
        r"quad(ruple)? witching|triple[- ]witching|quarterly options",
        "quad_witching",
        "index_options",
    ),
    (r"Deribit|crypto options? expir", "crypto_options_expiry", "crypto"),
    (r"options?[- ]expiration|\bopex\b", "monthly_opex", "index_options"),
    (r"rebalance|reconstitution", "index_rebalance", "index_options"),
    # Global central banks.
    (r"\bECB\b.*Forum|Forum on Central Banking", "ecb_forum", "global_central_bank"),
    (r"\bECB\b.*(account|minutes)", "ecb_account", "global_central_bank"),
    (r"\bECB\b|European Central Bank", "ecb_decision", "global_central_bank"),
    (r"\bBOJ\b|Bank of Japan", "boj_decision", "global_central_bank"),
    (r"Bank of England.*Financial Stability", "boe_financial_stability", "global_central_bank"),
    (r"\bBOE\b|Bank of England", "boe_decision", "global_central_bank"),
    (r"\bPBoC\b|loan prime rate", "pboc_lpr", "global_central_bank"),
    # Earnings and company prints, one family per company so a year of them lines up.
    (r"TSMC .*(monthly (sales|revenue))|TSMC monthly", "tsmc_monthly_sales", "earnings"),
    (r"bank earnings", "earnings_us_banks", "earnings"),
    (r"\b(NVIDIA|Nvidia)\b.*(earnings|results)", "earnings_nvda", "earnings"),
    (r"\bBroadcom\b.*(earnings|results)", "earnings_avgo", "earnings"),
    (r"\bTSMC\b.*(earnings|results)", "earnings_tsm", "earnings"),
    (r"\bAMD\b.*(earnings|results)", "earnings_amd", "earnings"),
    (r"\bMarvell\b.*(earnings|results)", "earnings_mrvl", "earnings"),
    (r"\bMicron\b.*(earnings|results)", "earnings_mu", "earnings"),
    (r"\bMicrosoft\b.*(earnings|results)", "earnings_msft", "earnings"),
    (r"\bAlphabet\b.*(earnings|results)", "earnings_googl", "earnings"),
    (r"\bMeta\b.*(earnings|results)", "earnings_meta", "earnings"),
    (r"\bAmazon\b.*(earnings|results)", "earnings_amzn", "earnings"),
    (r"\bApple\b.*(earnings|results)", "earnings_aapl", "earnings"),
    (r"\bTesla\b.*(earnings|results)", "earnings_tsla", "earnings"),
    (r"\bOracle\b.*(earnings|results)", "earnings_orcl", "earnings"),
    (r"\bSK ?hynix\b.*(earnings|results)", "earnings_sk_hynix", "earnings"),
    (r"\bSamsung\b.*(earnings|results)", "earnings_samsung", "earnings"),
    (r"\bASML\b.*(earnings|results)", "earnings_asml", "earnings"),
    (r"\bCoreWeave\b.*(earnings|results)", "earnings_crwv", "earnings"),
    (r"\bPalantir\b.*(earnings|results)", "earnings_pltr", "earnings"),
    (r"\b(earnings|financial results)\b", "earnings_other", "earnings"),
    # Recurring tech and AI events.
    (r"\bWWDC\b", "apple_wwdc", "ai_tech"),
    (r"\bApple\b.*\bevent\b", "apple_event", "ai_tech"),
    (r"\bDevDay\b", "openai_devday", "ai_tech"),
    (r"\bGTC\b", "nvidia_gtc", "ai_tech"),
    (r"\bCOMPUTEX\b", "computex", "ai_tech"),
    (r"Meta Connect", "meta_connect", "ai_tech"),
    (r"Advancing AI", "amd_advancing_ai", "ai_tech"),
    (r"TSMC .*OIP|OIP Ecosystem", "tsmc_oip_forum", "ai_tech"),
    (r"Claude Founder House|Anthropic .*(livestream|event)", "anthropic_event", "ai_tech"),
    # Commodities and energy.
    (r"\bOPEC\b.*(Monthly Oil Market Report|MOMR)", "opec_momr", "commodities_energy"),
    (r"\bOPEC\+?", "opec_meeting", "commodities_energy"),
    (r"Short-Term Energy Outlook|\bSTEO\b", "eia_steo", "commodities_energy"),
    (r"\bIEA\b.*Oil Market Report", "iea_omr", "commodities_energy"),
    # Market closures.
    (r"\bclosures?\b|\bholiday\b", "market_closure", "market_closure"),
    # Investor forums.
    (r"\bSohn\b", "sohn_conference", "investor_forum"),
    (r"All-In (Summit|Liquidity)", "all_in_summit", "investor_forum"),
    # Recurring policy calendar items.
    (r"\bG7\b", "g7_summit", "policy_geopolitics"),
    (r"\bG20\b", "g20_meeting", "policy_geopolitics"),
    (r"\bNATO\b", "nato_summit", "policy_geopolitics"),
    (r"European Council", "european_council", "policy_geopolitics"),
    (r"daylight saving", "daylight_saving_shift", "market_closure"),
)

# One-offs: no family, but a category so the review can still slice by theme.
_CATEGORIES: tuple[tuple[str, Category], ...] = (
    (
        r"crypto|stablecoin|GENIUS|CLARITY|MiCA|\bCFTC\b|bitcoin|\bBTC\b|\bETH\b|Solana|"
        r"tokeni[sz]ed",
        "crypto",
    ),
    (
        r"tariff|Section (122|232|301|338)|USTR|USMCA|trade (deal|framework|talks)|"
        r"Board of Trade|IEEPA|\bbans?\b|embargo",
        "trade_tariffs",
    ),
    (
        r"\bSEC\b|24-hour|\bIPO\b|\bADR\b|lockup|unlock|trading debut|futures (launch|"
        r"listing)|contract listing|Cboe|\bCME\b",
        "market_structure",
    ),
    (r"\bAI\b|chip|semiconductor|Nasdaq", "ai_tech"),
    (
        r"Iran|Ukraine|Russia|China|Xi\b|election|summit|ceasefire|sanction|court|ruling|"
        r"peace|White House|Trump",
        "policy_geopolitics",
    ),
    (r"\boil\b|Hormuz|crude|copper|uranium|gold|\bLNG\b", "commodities_energy"),
    (r"ISM|Supply Chain", "us_macro"),
)


def classify(name: str) -> tuple[str | None, Category]:
    """The recurring family and the category of a catalyst, from its name alone."""
    for pattern, family, category in _FAMILIES:
        if re.search(pattern, name, re.IGNORECASE):
            return family, category
    for pattern, category in _CATEGORIES:
        if re.search(pattern, name, re.IGNORECASE):
            return None, category
    return None, "other"
