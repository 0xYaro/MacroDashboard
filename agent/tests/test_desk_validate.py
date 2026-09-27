"""The desk's submission rules, with no database.

Every rule the vault parser applied to `Catalyst Monitor.md` and `Blackswan
Events.md` (worker/tests/test_vault.py) has a counterpart here, because the desk
writes the same rows the parser used to: the same identity, the same date for
the same words, the same estimate, the same category. The date cases are the
parser's own, sent both as the grammar's words (`date_text`) and as the
structured `when` the tool prefers.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from macro_agent import worker_bridge
from macro_agent.desk_validate import (
    CatalystRename,
    CatalystRetire,
    CatalystUpsert,
    RiskRetire,
    RiskUpsert,
    canonical_when,
    catalyst_ops,
    holding_items,
    resolve_when,
    risk_ops,
)

NOW = datetime(2026, 9, 21, 0, 45, tzinfo=UTC)  # 08:45 Singapore, the catalyst run


@pytest.fixture(scope="module")
def vault(settings):
    return worker_bridge.vault(settings)


@pytest.fixture(scope="module")
def families(settings):
    return worker_bridge.families(settings)


def item(**over):
    base = {
        "op": "upsert",
        "name": "U.S. August CPI",
        "when": {"date": "2026-10-14", "time": "08:30", "tz": "America/New_York"},
        "date_text": "2026-10-14 08:30 ET",
        "estimated": False,
        "source_url": "https://www.bls.gov/schedule/news_release/cpi.htm",
        "affected": ["rates", "USD", "equities"],
    }
    base.update(over)
    return base


def ops(vault, families, *items, now=NOW):
    return catalyst_ops({"items": list(items)}, now=now, vault=vault, families=families)


def refusals(checks):
    return [one.sentence for one in checks if not one.ok and one.severity == "refuse"]


# ---------------------------------------------------------------------------
# Dates: the vault parser's cases, through the desk
# ---------------------------------------------------------------------------


def test_a_structured_time_is_converted_through_singapore_and_parsed_by_the_vault(vault):
    when, problem = resolve_when(
        {"date": "2026-09-22", "time": "10:20", "tz": "America/New_York"}, None, vault=vault
    )
    assert problem is None
    assert when.canonical == "2026-09-22 22:20 SGT"
    assert when.precision == "exact"
    assert when.starts_at == datetime(2026, 9, 22, 14, 20, tzinfo=UTC)


def test_date_text_alone_reads_the_singapore_half_and_not_the_first_stamp(vault):
    """The parser's own rule: `<source local> / <Singapore>`, the Singapore half wins."""
    when, _ = resolve_when(None, "2026-09-22 10:20 ET / 2026-09-22 22:20 SGT", vault=vault)
    assert when.starts_at == datetime(2026, 9, 22, 14, 20, tzinfo=UTC)
    assert when.precision == "exact"


def test_a_window_takes_the_singapore_dates_not_the_source_local_ones(vault):
    text = "auctions 2026-09-22 to 2026-09-24 at 13:00 ET / 2026-09-23 to 2026-09-25 at 01:00 SGT"
    when, _ = resolve_when(None, text, vault=vault)
    assert when.precision == "window"
    assert when.starts_at.date() == date(2026, 9, 23)
    assert when.ends_at.date() == date(2026, 9, 25)


def test_a_structured_window_of_dates_is_a_window(vault):
    when, _ = resolve_when({"from": "2026-10-06", "to": "2026-10-08"}, None, vault=vault)
    assert (when.precision, when.starts_at.date(), when.ends_at.date()) == (
        "window", date(2026, 10, 6), date(2026, 10, 8))


def test_a_structured_timed_span_is_exact_with_an_end(vault):
    when, _ = resolve_when(
        {"from": "2026-10-06T13:00", "to": "2026-10-08T13:00", "tz": "America/New_York"},
        None, vault=vault,
    )
    assert when.precision == "exact"
    assert when.starts_at == datetime(2026, 10, 6, 17, 0, tzinfo=UTC)
    assert when.ends_at == datetime(2026, 10, 8, 17, 0, tzinfo=UTC)


def test_a_day_is_anchored_at_utc_midnight(vault):
    when, _ = resolve_when({"date": "2026-10-01"}, None, vault=vault)
    assert when.precision == "day"
    assert when.starts_at == datetime(2026, 10, 1, tzinfo=UTC)


def test_a_part_of_a_month_is_kept_as_a_span_and_not_filed_on_the_first(vault):
    when, _ = resolve_when({"month": "2026-10", "part": "late"}, None, vault=vault)
    assert when.canonical == "late October 2026"
    assert (when.starts_at.day, when.ends_at.day, when.precision) == (21, 31, "month")
    span, _ = resolve_when({"month": "2026-10", "part": "mid-to-late"}, None, vault=vault)
    assert (span.starts_at.day, span.ends_at.day) == (11, 31)
    whole, _ = resolve_when({"month": "2026-11"}, None, vault=vault)
    assert (whole.starts_at.day, whole.ends_at.day) == (1, 30)


def test_the_same_words_resolve_the_same_way_as_text_and_as_structure(vault):
    text, _ = resolve_when(None, "mid-to-late October 2026 expected", vault=vault)
    shaped, _ = resolve_when({"month": "2026-10", "part": "mid-to-late"}, None, vault=vault)
    assert (text.starts_at, text.ends_at, text.precision) == (
        shaped.starts_at, shaped.ends_at, shaped.precision)


def test_a_line_whose_singapore_times_carry_no_date_keeps_its_day(vault):
    when, _ = resolve_when(None, "2026-10-25 03:00 CEST / 10:00 SGT", vault=vault)
    assert (when.precision, when.starts_at) == ("day", datetime(2026, 10, 25, tzinfo=UTC))


def test_unknown_is_a_precision_not_a_refusal(vault):
    when, problem = resolve_when({"unknown": True}, None, vault=vault)
    assert problem is None and when.precision == "unknown" and when.starts_at is None


@pytest.mark.parametrize(
    ("raw", "fragment"),
    [
        ({"date": "2026-10-14", "time": "08:30"}, "needs `when.tz`"),
        ({"date": "14 Oct"}, "YYYY-MM-DD"),
        ({"date": "2026-10-14", "time": "8.30", "tz": "America/New_York"}, "HH:MM"),
        ({"from": "2026-10-08", "to": "2026-10-06"}, "must be after"),
        ({"month": "2026-13"}, "YYYY-MM"),
        ({"month": "2026-10", "part": "sometime"}, "early, mid, late"),
        ({"date": "2026-10-14", "tz": "Mars/Olympus"}, "without `when.time`"),
        ({"at": "tomorrow"}, "unrecognised shape"),
        ("2026-10-14", "is an object"),
    ],
)
def test_a_malformed_when_is_refused_with_what_to_send(raw, fragment):
    words, _, problem = canonical_when(raw)
    assert words is None and fragment in problem


# ---------------------------------------------------------------------------
# Catalysts
# ---------------------------------------------------------------------------


def test_a_well_formed_upsert_carries_the_vault_key_family_and_category(vault, families):
    found, checks = ops(vault, families, item())
    assert refusals(checks) == []
    (one,) = found
    assert isinstance(one, CatalystUpsert)
    assert one.key == vault.external_key("U.S. August CPI")
    assert (one.family, one.category) == families.classify("U.S. August CPI")
    assert one.category_given is False


def test_the_external_key_is_stable_across_a_reschedule_and_case_and_spacing(vault, families):
    first, _ = ops(vault, families, item())
    moved, _ = ops(vault, families, item(name="u.s.  august  CPI", when={"date": "2026-10-15"},
                                         date_text="2026-10-15"))
    assert first[0].key == moved[0].key


def test_a_given_category_must_be_one_of_the_sixteen(vault, families):
    found, checks = ops(vault, families, item(category="macro"))
    assert found == [] and "is not one of the sixteen" in refusals(checks)[0]
    found, checks = ops(vault, families, item(category="us_macro"))
    assert refusals(checks) == [] and found[0].category_given


def test_a_ban_is_a_trade_measure_when_no_category_is_given(vault, families):
    found, _ = ops(vault, families, item(name="U.S. ban on Chinese drone imports takes effect"))
    assert found[0].category == families.classify(
        "U.S. ban on Chinese drone imports takes effect")[1]


@pytest.mark.parametrize(
    "url",
    [None, "", "http://www.bls.gov/cpi", "www.bls.gov/cpi", "https://", "https://bls gov/x",
     "ftp://bls.gov/x"],
)
def test_every_catalyst_cites_an_https_source(vault, families, url):
    found, checks = ops(vault, families, item(source_url=url))
    assert found == [] and "`source_url` is required" in refusals(checks)[0]


def test_estimated_is_required_and_never_inferred(vault, families):
    raw = item()
    del raw["estimated"]
    found, checks = ops(vault, families, raw)
    assert found == [] and "`estimated` is required" in refusals(checks)[0]


def test_a_month_or_unknown_date_is_never_firm(vault, families):
    for when in ({"month": "2026-10", "part": "late"}, {"unknown": True}):
        found, checks = ops(vault, families, item(when=when, date_text=""))
        assert found == [] and "always an estimate" in refusals(checks)[0]
    found, checks = ops(vault, families, item(when={"month": "2026-10", "part": "late"},
                                              date_text="late October 2026", estimated=True))
    assert refusals(checks) == [] and found[0].estimated


def test_a_firm_flag_beside_hedged_words_is_a_contradiction(vault, families):
    """The parser reads "expected" and "around <date>" as an estimate; so must the desk."""
    for words in ("2026-10-20 expected", "around 2026-10-20", "2026-10-20; date TBD"):
        found, checks = ops(vault, families, item(when={"date": "2026-10-20"}, date_text=words))
        assert found == [] and "hedge it" in refusals(checks)[0], words


def test_a_firm_day_whose_time_is_not_out_yet_may_be_firm(vault, families):
    found, checks = ops(vault, families, item(when={"date": "2026-09-25"},
                                              date_text="2026-09-25; release time TBD"))
    assert refusals(checks) == [] and found[0].estimated is False


def test_dates_outside_minus_two_to_plus_120_days_are_refused(vault, families):
    past = (NOW - timedelta(days=3)).date().isoformat()
    far = (NOW + timedelta(days=121)).date().isoformat()
    for day in (past, far):
        found, checks = ops(vault, families, item(when={"date": day}, date_text=day))
        assert found == [], day
        assert "ago" in refusals(checks)[0] or "days ahead" in refusals(checks)[0]
    yesterday = (NOW - timedelta(days=1)).date().isoformat()
    found, _ = ops(vault, families, item(when={"date": yesterday}, date_text=yesterday))
    assert len(found) == 1


def test_two_items_naming_one_catalyst_are_both_refused(vault, families):
    """The parser kept the later of two same-named lines and filed a finding. The desk
    cannot know which the agent meant, so neither is written."""
    found, checks = ops(vault, families, item(), item(name="U.S. AUGUST cpi",
                                                      when={"date": "2026-10-15"}))
    assert found == []
    assert len([s for s in refusals(checks) if "names the same catalyst" in s]) == 2


def test_retire_needs_a_reason_and_a_target(vault, families):
    found, checks = ops(vault, families, {"op": "retire", "name": "U.S. August CPI"})
    assert found == [] and "says why in `reason`" in refusals(checks)[0]
    found, checks = ops(vault, families, {"op": "retire", "reason": "it has already happened"})
    assert found == [] and "by `name` or `id`" in refusals(checks)[0]
    found, checks = ops(vault, families, {"op": "retire", "name": "U.S. August CPI",
                                          "reason": "released on schedule"})
    assert refusals(checks) == [] and isinstance(found[0], CatalystRetire)


def test_rename_needs_a_new_name_that_is_actually_different(vault, families):
    found, checks = ops(vault, families, {"op": "rename", "name": "U.S. August CPI",
                                          "new_name": "u.s. august cpi"})
    assert found == [] and "differ only in case or spacing" in refusals(checks)[0]
    found, checks = ops(vault, families, {"op": "rename", "name": "U.S. August CPI",
                                          "new_name": "U.S. August Consumer Price Index"})
    assert refusals(checks) == [] and isinstance(found[0], CatalystRename)
    assert found[0].new_key == vault.external_key("U.S. August Consumer Price Index")


def test_an_unknown_op_and_a_non_list_payload_are_refused(vault, families):
    found, checks = ops(vault, families, {"op": "delete", "name": "U.S. August CPI"})
    assert found == [] and "upsert, retire or rename" in refusals(checks)[0]
    found, checks = catalyst_ops({"items": "nope"}, now=NOW, vault=vault, families=families)
    assert found == [] and "`items` list" in refusals(checks)[0]


def test_a_url_is_not_a_name(vault, families):
    found, checks = ops(vault, families, item(name="https://www.bls.gov/cpi"))
    assert found == [] and "is a URL" in refusals(checks)[0]


# ---------------------------------------------------------------------------
# Tail risks
# ---------------------------------------------------------------------------


def risk(**over):
    base = {
        "op": "upsert",
        "risk_id": "iran-oil-chokepoints",
        "name": "Iran-war tanker and oil-chokepoint escalation",
        "severity": "acute",
        "summary": "Tanker attacks near Hormuz raised shipping insurance again.",
        "priced_in": "Oil has priced some of it; stocks have not.",
        "gauges": ["vix", "brent"],
        "source_url": "https://www.reuters.com/world/middle-east/x",
        "assets": ["oil", "equities"],
    }
    base.update(over)
    return base


def test_a_tail_risk_upsert_decodes_with_gauges_as_keys_only():
    kind, found, checks = risk_ops({"kind": "weekly", "items": [risk()]})
    assert kind == "weekly" and refusals(checks) == []
    (one,) = found
    assert isinstance(one, RiskUpsert) and one.gauges == ("vix", "brent")


def test_gauges_carrying_numbers_are_refused():
    _, found, checks = risk_ops({"kind": "weekly", "items": [risk(gauges=[{"vix": 17}])]})
    assert found == [] and "no number is sent" in refusals(checks)[0]


@pytest.mark.parametrize("slug", ["Iran-Oil", "iran oil", "io", "iran--oil", "-iran", "x" * 49])
def test_a_risk_id_is_a_lower_case_slug(slug):
    _, found, checks = risk_ops({"kind": "weekly", "items": [risk(risk_id=slug)]})
    assert found == [] and "lower-case slug" in refusals(checks)[0]


def test_an_unknown_severity_is_refused_and_not_defaulted():
    _, found, checks = risk_ops({"kind": "weekly", "items": [risk(severity="high")]})
    assert found == [] and "acute, elevated or watch" in refusals(checks)[0]


def test_the_kind_is_weekly_or_daily_or_nothing_is_read():
    kind, found, checks = risk_ops({"kind": "monthly", "items": [risk()]})
    assert kind is None and found == [] and "weekly" in refusals(checks)[0]


def test_a_risk_named_twice_is_refused_once():
    _, found, checks = risk_ops({"kind": "weekly", "items": [risk(), risk(severity="watch")]})
    assert len(found) == 1 and "send one item per risk" in refusals(checks)[0]


def test_a_risk_retirement_says_why():
    _, found, checks = risk_ops({"kind": "weekly",
                                 "items": [{"op": "retire", "risk_id": "iran-oil-chokepoints"}]})
    assert found == [] and "says why" in refusals(checks)[0]
    _, found, _ = risk_ops({"kind": "weekly", "items": [
        {"op": "retire", "risk_id": "iran-oil-chokepoints", "reason": "ceasefire held two weeks"}]})
    assert isinstance(found[0], RiskRetire)


# ---------------------------------------------------------------------------
# Holdings
# ---------------------------------------------------------------------------


def holding(**over):
    base = {
        "symbol": "GOOG",
        "driver_summary": "Alphabet fell after a court ruling on search deals.",
        "material": True,
        "source_url": "https://www.reuters.com/technology/x",
    }
    base.update(over)
    return base


def test_price_fields_from_the_model_are_ignored_and_named(vault):
    found, checks = holding_items(
        {"items": [holding(change_24h_pct=-3.9, price=334.57)]}, now=NOW, vault=vault
    )
    assert refusals(checks) == []
    assert found[0].ignored == ("change_24h_pct", "price")
    assert any("computed by the desk" in one.sentence for one in checks)


def test_a_material_move_needs_its_reason_and_a_reason_needs_its_source(vault):
    found, checks = holding_items({"items": [holding(driver_summary=None)]}, now=NOW, vault=vault)
    assert found == [] and "says why in `driver_summary`" in refusals(checks)[0]
    found, checks = holding_items({"items": [holding(source_url=None)]}, now=NOW, vault=vault)
    assert found == [] and "cites its page" in refusals(checks)[0]


def test_an_event_must_fall_within_the_next_fourteen_days(vault):
    far = {"kind": "earnings", "name": "Alphabet Q3 2026 earnings",
           "when": {"date": (NOW + timedelta(days=20)).date().isoformat()}, "estimated": False,
           "source_url": "https://abc.xyz/investor/"}
    found, checks = holding_items({"items": [holding(events=[far])]}, now=NOW, vault=vault)
    assert found == [] and "days ahead" in refusals(checks)[0]
    near = far | {"when": {"date": (NOW + timedelta(days=10)).date().isoformat()}}
    found, checks = holding_items({"items": [holding(events=[near])]}, now=NOW, vault=vault)
    assert refusals(checks) == [] and found[0].events[0].kind == "earnings"


def test_an_event_needs_a_date_and_a_known_kind(vault):
    undated = {"kind": "earnings", "name": "Alphabet Q3 2026 earnings", "when": {"unknown": True},
               "estimated": True, "source_url": "https://abc.xyz/investor/"}
    found, checks = holding_items({"items": [holding(events=[undated])]}, now=NOW, vault=vault)
    assert found == [] and "needs one" in refusals(checks)[0]
    odd = undated | {"kind": "rumour", "when": {"date": "2026-09-25"}}
    found, checks = holding_items({"items": [holding(events=[odd])]}, now=NOW, vault=vault)
    assert found == [] and "`kind` is one of" in refusals(checks)[0]


# ---------------------------------------------------------------------------
# Review of 2026-09-25
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("where", ["name", "date_text", "affected"])
def test_a_control_character_is_refused_not_passed_to_the_database(vault, families, where):
    """Postgres text cannot hold U+0000; before this it failed the whole run."""
    sent = {"name": "U.S. August\u0000 CPI", "date_text": "2026-10-14\u0000 08:30 ET",
            "affected": ["rates\u0007"]}[where]
    found, checks = ops(vault, families, item(**{where: sent}), item(name="U.S. September CPI",
                                                                     when={"date": "2026-11-12"},
                                                                     date_text="2026-11-12"))
    assert [one.name for one in found] == ["U.S. September CPI"], "the clean item still goes"
    assert len(refusals(checks)) == 1 and "control character" in refusals(checks)[0]


def test_control_characters_are_refused_in_risks_and_holdings(vault):
    _, risks, checks = risk_ops({"kind": "weekly", "items": [risk(summary="Tankers\u0000.")]})
    assert risks == [] and "control character" in refusals(checks)[0]
    held, checks = holding_items({"items": [holding(driver_summary="Fell\u0000.")]},
                                 now=NOW, vault=vault)
    assert held == [] and "control character" in refusals(checks)[0]


def test_the_scrubbed_payload_can_be_archived():
    from macro_agent.desk_validate import scrubbed

    clean = scrubbed({"items": [{"na\u0000me": ["a\u0000b", 1, None, "tab\tok"]}]})
    assert clean == {"items": [{"na�me": ["a�b", 1, None, "tab\tok"]}]}


def test_date_text_must_agree_with_when(vault, families):
    found, checks = ops(vault, families, item(date_text="2026-10-20 08:30 ET"))
    assert found == []
    assert "`date_text` says 2026-10-20" in refusals(checks)[0]
    assert "`when` says 2026-10-14" in refusals(checks)[0]
    # A time in New York the evening before Singapore's day is the same event.
    evening = item(when={"date": "2026-10-14", "time": "20:30", "tz": "America/New_York"},
                   date_text="2026-10-14 20:30 ET")
    found, checks = ops(vault, families, evening)
    assert refusals(checks) == [] and len(found) == 1
    # A narrowed month and words with no date both stand.
    for words in ("late October 2026", "release time TBD"):
        found, checks = ops(vault, families, item(when={"date": "2026-10-28"}, date_text=words,
                                                  estimated=True))
        assert refusals(checks) == [], words


def test_an_unknown_when_beside_a_dated_date_text_is_a_contradiction(vault, families):
    found, checks = ops(vault, families, item(when={"unknown": True}, date_text="2026-10-20",
                                              estimated=True))
    assert found == [] and "`when` says the date is unknown" in refusals(checks)[0]


def test_an_event_date_text_must_agree_with_its_when(vault):
    event = {"kind": "earnings", "name": "Alphabet Q3 2026 earnings",
             "when": {"date": "2026-09-29"}, "date_text": "2026-10-05", "estimated": False,
             "source_url": "https://abc.xyz/investor/"}
    found, checks = holding_items({"items": [holding(events=[event])]}, now=NOW, vault=vault)
    assert found == [] and "`date_text` says 2026-10-05" in refusals(checks)[0]


def test_a_material_move_with_no_clear_reason_needs_no_source(vault):
    found, checks = holding_items({"items": [holding(
        driver_summary=None, source_url=None, no_clear_reason=True)]}, now=NOW, vault=vault)
    assert refusals(checks) == []
    assert found[0].material and found[0].no_clear_reason and found[0].source_url is None
    said, checks = holding_items({"items": [holding(
        driver_summary="No news explains the drop; the sector fell with it.", source_url=None,
        no_clear_reason=True)]}, now=NOW, vault=vault)
    assert refusals(checks) == [] and said[0].driver_summary.startswith("No news")
    found, checks = holding_items({"items": [holding(no_clear_reason="yes")]},
                                  now=NOW, vault=vault)
    assert found == [] and "`no_clear_reason` is true or false" in refusals(checks)[0]
