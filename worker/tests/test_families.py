"""The recurring family of a catalyst, from its name alone.

A non-U.S. print is its own family. Filing Japan's CPI under `us_cpi` puts it
in the U.S. inflation series of the review, and next year's question "did CPI
move markets" would be answered with Tokyo's prints mixed in.
"""

from __future__ import annotations

import pytest

from macro.families import classify


@pytest.mark.parametrize(
    ("name", "family"),
    [
        ("Japan national CPI (August)", "japan_cpi"),
        ("Tokyo CPI", "japan_cpi"),
        ("Eurozone flash CPI (September)", "eurozone_cpi"),
        ("Euro area HICP flash estimate", "eurozone_cpi"),
        ("UK GDP (Q2 final)", "uk_gdp"),
        ("Canada retail sales (July)", "canada_retail_sales"),
        ("CPI: Canada, August", "canada_cpi"),
        ("China Q3 GDP and September activity data", "china_activity_data"),
    ],
)
def test_a_non_us_print_is_its_own_family(name, family):
    assert classify(name) == (family, "global_macro")


@pytest.mark.parametrize(
    ("name", "family"),
    [
        ("August Consumer Price Index", "us_cpi"),
        ("U.S. CPI (September)", "us_cpi"),
        ("Q2 GDP third estimate", "us_gdp"),
        ("Advance Monthly Retail Sales", "us_retail_sales"),
    ],
)
def test_a_us_print_keeps_its_family(name, family):
    assert classify(name) == (family, "us_macro")


def test_a_central_banks_inflation_report_is_not_a_print():
    assert classify("Bank of Japan Outlook Report on inflation")[0] == "boj_decision"


def test_a_company_named_like_a_governor_is_not_a_fed_speech():
    assert classify("Williams-Sonoma earnings call remarks") == ("earnings_other", "earnings")
    assert classify("Apple CEO Tim Cook remarks at conference")[0] != "fed_speech"
    assert classify("Williams speech on the economic outlook") == ("fed_speech", "fed")
    assert classify("Governor Cook remarks on financial stability") == ("fed_speech", "fed")
