"""The three cron files, parsed against the grammar their prompts enforce.

The bodies below are copied verbatim from the live files on 2026-09-21. A
parse failure is a `macro_quality` finding, never a silent zero, and half of
this file is about that half of the contract.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from macro import vault

SEEN_ON = date(2026, 9, 21)

CATALYST_FILE = """# Catalyst Monitor

## Tracked Catalysts

- Catalyst: Federal Reserve Vice Chair Philip Jefferson speech on discount-window modernization \
and Treasury-market functioning. Date & Time: 2026-09-22 10:20 ET / 2026-09-22 22:20 SGT. \
Source: Federal Reserve September 2026 calendar \
https://www.federalreserve.gov/newsevents/2026-september.htm. Affected: Treasury-market \
liquidity, bank funding, discount-window policy, rates, USD, equities.
- Catalyst: September PBoC loan prime rate fixing. Date & Time: 2026-09-21; exact time TBD. \
Source: MNI 2026 global central-bank calendar https://media.marketnews.com/MNI.pdf. \
Affected: CNY, China and Hong Kong equities, Asian rates.
- Catalyst: Tesla Q3 2026 earnings. Date & Time: mid-to-late October 2026 expected; exact \
date/time TBD. Source: Tesla Investor Relations https://ir.tesla.com/. Affected: megacap tech, \
Nasdaq, EV demand.
- Catalyst: U.S. 2Y/5Y/7Y Treasury auction cluster. Date & Time: auctions 2026-09-22 to \
2026-09-24 at 13:00 ET / 2026-09-23 to 2026-09-25 at 01:00 SGT. Source: U.S. Treasury \
https://home.treasury.gov/Tentative-Auction-Schedule.pdf. Affected: front-end rates, USD.

## Newly Added

- Catalyst: Federal Reserve Senior Credit Officer Opinion Survey on Dealer Financing Terms. \
Date & Time: 2026-09-24 14:00 ET / 2026-09-25 02:00 SGT. Source: Federal Reserve September 2026 \
calendar https://www.federalreserve.gov/newsevents/2026-september.htm. Affected: dealer \
financing, repo, rates.
"""

WATCHLIST_FILE = """# Market Watchlist

## Holdings
- MU
- RIOT
- 012330 — Hyundai Mobis (KRX)

## Watching

## Active Changes
- Ticker: MU. Date & Time: 2026-09-21 09:15 SGT. Change: +3.92%. Summary: UBS estimated AI capex \
at nearly $1 trillion in 2026. Source: Investing.com https://www.investing.com/news/x-4908051
- Ticker: SKHY. Date & Time: 2026-09-15 09:15 SGT. Change: -7.60%. Summary: SK hynix fell 7.60% \
as fresh industry reporting showed its DRAM revenue share fell. Source: ChosunBiz \
https://biz.chosun.com/en/en-it/2026/09/14/BQ7VDE267JCWNOZOWXB77B7TIY/

## Newly Flagged
- Ticker: MU. Date & Time: 2026-09-21 09:15 SGT. Change: +3.92%. Summary: UBS estimated AI capex \
at nearly $1 trillion in 2026. Source: Investing.com https://www.investing.com/news/x-4908051
"""

BLACKSWAN_FILE = """# Blackswan Events

## Watchlist
- AI capex sustainability and data-center concentration

## Active Risks

- Risk: Iran-war tanker and oil-chokepoint escalation. Date & Time: 2026-09-09 onward. \
Severity: acute. Summary: U.S.-Iran tanker strikes intensified into the conflict's largest \
shipping attack as crude moved above $100. Priced-in: Cboe VIX fell to 14.81 on Sep 18, still \
consistent with a contained-conflict assumption. Source: Reuters https://www.reuters.com. \
Assets: oil, shipping, energy equities, U.S. Treasuries, USD.
- Risk: Opaque private-credit leverage and insurer feedback loop. Date & Time: 2026-09-14 \
onward. Severity: watch. Summary: Recent reporting flagged opaque leverage in private credit. \
Priced-in: Cboe VIX at 14.81 on Sep 18 implies markets still assume losses remain idiosyncratic. \
Source: WSJ https://www.wsj.com. Assets: private credit, life insurers, leveraged loans.

## Newly Flagged
- Risk: Opaque private-credit leverage and insurer feedback loop. Date & Time: 2026-09-14 \
onward. Severity: watch. Summary: Recent reporting flagged opaque leverage in private credit. \
Priced-in: Cboe VIX at 14.81 on Sep 18 implies markets still assume losses remain idiosyncratic. \
Source: WSJ https://www.wsj.com. Assets: private credit, life insurers, leveraged loans.
"""


# ---------------------------------------------------------------------------
# Catalyst Monitor.md
# ---------------------------------------------------------------------------


def test_catalysts_parse_with_no_findings():
    catalysts, findings = vault.parse_catalysts(CATALYST_FILE, SEEN_ON)
    assert findings == []
    assert len(catalysts) == 5
    assert all(one.status == "tracked" for one in catalysts)


def test_a_timed_catalyst_reads_the_singapore_half_and_not_the_first_stamp():
    """The line states the time twice: `10:20 ET / 22:20 SGT`.

    22:20 Singapore is 14:20 UTC, which is 10:20 New York, so the two halves
    agree. Taking the first stamp on the line instead reads 10:20 as Singapore
    time and files the speech at 02:20 UTC, twelve hours early. That was the
    bug this test was written for.
    """
    catalysts, _ = vault.parse_catalysts(CATALYST_FILE, SEEN_ON)
    speech = next(one for one in catalysts if "Jefferson" in one.name)
    assert speech.date_precision == "exact"
    assert speech.starts_at is not None
    assert speech.starts_at.isoformat() == "2026-09-22T14:20:00+00:00"
    assert speech.source_url == "https://www.federalreserve.gov/newsevents/2026-september.htm"
    assert "Treasury-market liquidity" in speech.affected


def test_a_window_takes_the_singapore_dates_not_the_source_local_ones():
    """`auctions 2026-09-22 to 2026-09-24 ET / 2026-09-23 to 2026-09-25 SGT`.

    The two halves carry different dates, because a 13:00 New York auction is
    01:00 the next morning in Singapore. The Singapore window is the one stored.
    """
    catalysts, _ = vault.parse_catalysts(CATALYST_FILE, SEEN_ON)
    cluster = next(one for one in catalysts if "auction cluster" in one.name)
    assert cluster.starts_at is not None
    assert cluster.ends_at is not None
    assert cluster.starts_at.date() == date(2026, 9, 23)
    assert cluster.ends_at.date() == date(2026, 9, 25)


def test_the_four_date_precisions_are_told_apart():
    """A clause bound to a dated event and one bound to "late October" are not
    the same claim, and the premise-moved rule has to be able to tell."""
    catalysts, _ = vault.parse_catalysts(CATALYST_FILE, SEEN_ON)
    by_precision = {one.date_precision for one in catalysts}
    assert by_precision == {"exact", "day", "window", "month"}

    tesla = next(one for one in catalysts if "Tesla" in one.name)
    assert tesla.date_precision == "month"
    assert tesla.starts_at is not None
    assert tesla.starts_at.date().month == 10

    cluster = next(one for one in catalysts if "auction cluster" in one.name)
    assert cluster.date_precision == "window"
    assert cluster.ends_at is not None
    assert cluster.ends_at > cluster.starts_at


def test_the_external_key_is_stable_across_a_reschedule():
    """A hash of the name, so a reschedule updates rather than duplicates."""
    assert vault.external_key("September FOMC minutes") == vault.external_key(
        "  september   FOMC   Minutes "
    )
    assert vault.external_key("September FOMC minutes") != vault.external_key("October CPI")


def test_two_catalysts_sharing_a_name_open_a_finding():
    """The live file carries two "U.S. 2Y/5Y/7Y Treasury auction cluster" rows.

    The external key is the name, so they collapse into one row and only the
    later survives. That is a real loss and it is reported, not hidden.
    """
    doubled = CATALYST_FILE + (
        "- Catalyst: U.S. 2Y/5Y/7Y Treasury auction cluster. Date & Time: auctions 2026-10-26 "
        "to 2026-10-29. Source: U.S. Treasury https://home.treasury.gov/x.pdf. Affected: "
        "front-end rates, USD.\n"
    )
    catalysts, findings = vault.parse_catalysts(doubled, SEEN_ON)
    collision = next(one for one in findings if one.check_key == "catalyst_name_collision")
    assert "2026-09-23" in collision.sentence
    assert "2026-10-26" in collision.sentence
    assert len(catalysts) == 5  # still five: the later row won


def test_an_unparsed_catalyst_line_is_a_finding_and_not_a_dropped_row():
    broken = (
        "# Catalyst Monitor\n\n## Tracked Catalysts\n\n"
        "- Something with no labels at all.\n\n## Newly Added\n"
    )
    catalysts, findings = vault.parse_catalysts(broken, SEEN_ON)
    assert catalysts == []
    assert [one.check_key for one in findings] == ["catalyst_line_unparsed"]
    assert "reached no table" in findings[0].sentence


def test_a_missing_section_is_a_finding():
    text = "# Catalyst Monitor\n\n## Newly Added\n"
    _, findings = vault.parse_catalysts(text, SEEN_ON)
    assert any(one.check_key == "catalyst_monitor_section_missing" for one in findings)


def test_a_file_that_parses_to_nothing_is_a_finding():
    """An empty Catalyst Monitor and an unreadable one must not look the same."""
    text = "# Catalyst Monitor\n\n## Tracked Catalysts\n\n## Newly Added\n"
    catalysts, findings = vault.parse_catalysts(text, SEEN_ON)
    assert catalysts == []
    assert [one.check_key for one in findings] == ["catalyst_monitor_empty"]


# ---------------------------------------------------------------------------
# Market Watchlist.md
# ---------------------------------------------------------------------------


def test_holdings_and_their_books():
    holdings, changes, findings = vault.parse_watchlist(WATCHLIST_FILE)
    assert findings == []
    by_ticker = {one.ticker: one for one in holdings}
    assert set(by_ticker) == {"MU", "RIOT", "012330"}
    assert by_ticker["RIOT"].book == "crypto"
    assert by_ticker["MU"].book == "equities"
    assert by_ticker["012330"].label == "Hyundai Mobis (KRX)"
    assert all(one.active for one in holdings)
    assert len(changes) == 2  # the duplicate in `Newly Flagged` is the same key


def test_a_change_carries_a_signed_percent_and_a_dated_instant():
    _, changes, _ = vault.parse_watchlist(WATCHLIST_FILE)
    by_ticker = {one.ticker: one for one in changes}
    assert by_ticker["MU"].change_pct == Decimal("3.92")
    assert by_ticker["SKHY"].change_pct == Decimal("-7.60")
    assert by_ticker["MU"].observed_at.isoformat() == "2026-09-21T01:15:00+00:00"
    assert by_ticker["SKHY"].source_url.startswith("https://biz.chosun.com/")


def test_an_unreadable_change_percent_is_null_and_never_zero():
    text = WATCHLIST_FILE.replace("Change: +3.92%.", "Change: n/a.")
    _, changes, _ = vault.parse_watchlist(text)
    micron = next(one for one in changes if one.ticker == "MU")
    assert micron.change_pct is None


def test_an_unparsed_change_line_is_a_finding():
    text = WATCHLIST_FILE.replace("- Ticker: SKHY.", "- SKHY fell a lot.")
    _, changes, findings = vault.parse_watchlist(text)
    assert {one.ticker for one in changes} == {"MU"}
    assert any(one.check_key == "change_line_unparsed" for one in findings)


# ---------------------------------------------------------------------------
# Blackswan Events.md
# ---------------------------------------------------------------------------


def test_tail_risks_parse_with_no_findings():
    risks, findings = vault.parse_tail_risks(BLACKSWAN_FILE, SEEN_ON)
    assert findings == []
    assert len(risks) == 2
    acute = next(one for one in risks if one.severity == "acute")
    assert "tanker" in acute.name
    assert "14.81" in acute.priced_in
    assert acute.assets[0] == "oil"
    assert acute.source_url == "https://www.reuters.com"


def test_an_unknown_severity_is_a_finding_and_not_a_default():
    """Three words is the whole vocabulary. A fourth is a grammar that drifted."""
    text = BLACKSWAN_FILE.replace("Severity: acute.", "Severity: severe.")
    risks, findings = vault.parse_tail_risks(text, SEEN_ON)
    assert len(risks) == 1
    assert any(one.check_key == "tail_risk_severity_unknown" for one in findings)


# ---------------------------------------------------------------------------
# Retirement
# ---------------------------------------------------------------------------


def test_a_catalyst_that_left_the_file_is_retired_rather_than_deleted():
    """A trigger clause bound to it is voided and kept, so the row has to stay."""
    stored = [("aaa", "tracked"), ("bbb", "tracked"), ("ccc", "retired")]
    out = vault.retirements(stored, ["aaa"], seen_on=SEEN_ON)
    assert [key for key, _ in out] == ["bbb"]
    assert "2026-09-21" in out[0][1]


def test_a_line_with_two_singapore_times_reads_the_one_that_carries_a_date():
    """The September FOMC, verbatim from the monitor on 2026-09-16.

    The press conference's Singapore time, the last SGT on the line, has no date
    because it shares the statement's. Reading that part left the row undated,
    and every FOMC of the year with it.
    """
    starts_at, ends_at, precision = vault.parse_when(
        "2026-09-15 to 2026-09-16; statement 2026-09-16 14:00 ET / 2026-09-17 02:00 SGT, "
        "press conference 14:30 ET / 02:30 SGT"
    )
    assert precision == "exact"
    assert starts_at is not None
    assert starts_at.isoformat() == "2026-09-16T18:00:00+00:00"
    assert ends_at is None


def test_a_line_whose_singapore_times_carry_no_date_keeps_its_day():
    """The July ECB decision: three zones per time, a date only at the front.

    Its one stamp is Frankfurt time, and filing that as Singapore time is the
    error the split exists to prevent, so the day is kept and the time dropped.
    """
    starts_at, _, precision = vault.parse_when(
        "2026-07-23 decision 14:15 CEST / 12:15 UTC / 20:15 SGT; "
        "press conference 14:45 CEST / 12:45 UTC / 20:45 SGT"
    )
    assert precision == "day"
    assert starts_at is not None
    assert starts_at.isoformat() == "2026-07-23T00:00:00+00:00"


def test_a_time_with_no_singapore_half_keeps_its_day_and_not_its_hour():
    """May CPI as the monitor wrote it in June: "2026-06-10 08:30 ET", no SGT.

    Read as Singapore time it lands at 00:30 UTC, twelve hours before the print.
    """
    starts_at, _, precision = vault.parse_when("2026-06-10 08:30 ET")
    assert precision == "day"
    assert starts_at is not None
    assert starts_at.isoformat() == "2026-06-10T00:00:00+00:00"


# ---------------------------------------------------------------------------
# Estimated dates, verbatim from the live Catalyst Monitor.md on 2026-09-23
# ---------------------------------------------------------------------------


def test_a_part_of_a_month_is_kept_as_a_span_and_not_filed_on_the_first():
    """"late October 2026 expected" used to be stored as 1 October.

    Eight earnings prints then sat on Thursday 1 October as if scheduled. The
    part of the month is the only thing the line says about the day, so it is
    stored as the span it names.
    """
    cases = {
        "late October 2026 expected; exact date/time TBD": ("2026-10-21", "2026-10-31"),
        "mid-October 2026 expected; exact date/time TBD": ("2026-10-11", "2026-10-20"),
        "mid-to-late October 2026 expected; exact date/time TBD": ("2026-10-11", "2026-10-31"),
        "October 2026 expected; exact date, time and UAE venue TBD": ("2026-10-01", "2026-10-31"),
        "early November 2026 expected": ("2026-11-01", "2026-11-10"),
        "end-September 2026 window": ("2026-09-21", "2026-09-30"),
    }
    for text, (first, last) in cases.items():
        starts_at, ends_at, precision = vault.parse_when(text)
        assert precision == "month", text
        assert starts_at is not None and ends_at is not None, text
        assert (starts_at.date().isoformat(), ends_at.date().isoformat()) == (first, last), text


def test_an_estimated_date_is_told_apart_from_a_firm_day_with_no_time_yet():
    """A time that is TBD does not make the day a guess; "expected" does."""
    estimated = [
        "late October 2026 expected; exact date/time TBD",
        "around 2026-10-20 expected; exact date/time TBD",
        "2026-11-03 expected; polling and result timing TBD",
    ]
    firm = [
        "2026-09-25; release time TBD",
        "2026-10-04; exact time TBD",
        "2026-09-30 to 2026-10-01; exact session times TBD",
        # "around" hedges the time here, not the date.
        "2026-11-10 around 12:00 ET / 2026-11-11 around 01:00 SGT",
    ]
    for text in estimated:
        assert vault.is_estimated(text, vault.parse_when(text)[2]), text
    for text in firm:
        assert not vault.is_estimated(text, vault.parse_when(text)[2]), text


def test_a_catalyst_carries_its_words_its_estimate_and_its_category():
    catalysts, _ = vault.parse_catalysts(CATALYST_FILE, SEEN_ON)
    tesla = next(one for one in catalysts if "Tesla" in one.name)
    assert tesla.raw_date_text.startswith("mid-to-late October 2026 expected")
    assert tesla.date_estimated
    assert (tesla.family, tesla.category) == ("earnings_tsla", "earnings")

    lpr = next(one for one in catalysts if "PBoC" in one.name)
    assert not lpr.date_estimated  # "2026-09-21; exact time TBD" is a firm day
    assert lpr.category == "global_central_bank"


def test_a_ban_is_a_trade_measure():
    """The live row that fell through to `other` before the rule existed."""
    from macro.families import classify

    name = (
        "U.S. ban on selected Canadian dairy products, most alcohol, and motorcycles "
        "takes effect"
    )
    assert classify(name) == (None, "trade_tariffs")
    assert classify("EU embargo on Russian LNG")[1] == "trade_tariffs"


# ---------------------------------------------------------------------------
# Blackswan Events.md is weekly
# ---------------------------------------------------------------------------


def test_a_tail_risk_is_dated_by_the_note_and_not_by_the_run(tmp_path):
    """The note written Monday 21 September was read again on the 23rd.

    Dating the listing by the run made the page say "last listed 23 Sept" for
    a note the weekly cron wrote two days earlier.
    """
    import os
    from datetime import datetime

    blackswan = tmp_path / "Blackswan Events.md"
    blackswan.write_text(BLACKSWAN_FILE, encoding="utf-8")
    written = datetime(2026, 9, 21, 6, 3, tzinfo=vault.SINGAPORE).timestamp()
    os.utime(blackswan, (written, written))

    feed = vault.read_all(
        catalyst_file=tmp_path / "absent.md",
        watchlist_file=tmp_path / "absent.md",
        blackswan_file=blackswan,
        seen_on=date(2026, 9, 23),
    )
    assert {one.seen_on for one in feed.tail_risks} == {date(2026, 9, 21)}
