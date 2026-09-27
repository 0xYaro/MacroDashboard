"""Mode A's two stages, and the prompts they carry.

The prompts are a port, not a rewrite, so what is asserted here is that the
parts twelve weeks of behaviour depended on are still in them: the tier ladder,
the never-blank `asof`, the fallback source order, and the instruction that a
metric which cannot be sourced is Tier 4 with an empty value.
"""

from __future__ import annotations

import pytest

from macro_agent.narrative import (
    agent_series,
    dispatch,
    flagged,
    merge_retry,
    plain_prose,
    prose_voice,
    retry_prompt,
)
from macro_agent.prompts import categories

CONTEXT = {
    "asof": "2026-09-25",
    "prev_week": "2026-W39",
    "prev_expectation": "The base case wanted memory to carry a thinning tape.",
    "previous": {"cnn_fear_greed": {"display": "29 (Fear)"}},
    "watchlist_deltas": ["[2026-09-24] MU +5.50% — on the Intel capacity warning"],
    "blackswan": ["[elevated] Crowded AI unwind — the memory trade and the AI trade have merged"],
    "catalysts": ["2026-09-30 Micron Q4 FY2026 — HBM, DRAM, semis, Nasdaq"],
}


# ---------------------------------------------------------------------------
# Which series this mode owns
# ---------------------------------------------------------------------------


def test_the_owned_set_is_the_registry_and_nothing_else(settings):
    """Derived from `config/series.yaml`, never counted.

    The set began at thirteen and is sixteen as of the run that added the
    FedWatch hike probability and the two ISM prints. A test that pinned the
    number would have failed on that change and taught nothing, and the three
    new series would have gone unfetched until somebody noticed the count.
    What is worth asserting is a property that holds whatever the count becomes.
    """
    from macro_agent.worker_bridge import registry

    reg = registry(settings)
    keys = agent_series(settings)
    assert keys, "the registry declares no agent_0930 series at all"
    assert sorted(keys) == sorted(one.key for one in reg.agent_only())
    assert not set(keys) & {one.key for one in reg.deterministic()}


def test_every_owned_series_is_covered_by_exactly_one_category(settings):
    """A series in no category is never fetched; one in two is fetched twice.

    This is the test that would have caught the three new series. It reads the
    registry and compares it against what the categories claim, so adding a
    series to `config/series.yaml` fails here until a prompt asks for it.
    """
    owned = set(agent_series(settings))
    claimed: list[str] = []
    for one in dispatch(CONTEXT):
        claimed.extend(one["series"])

    assert sorted(claimed) == sorted(set(claimed)), (
        "a series is claimed by two categories, so it would be fetched twice and "
        f"the later return would win: {sorted({k for k in claimed if claimed.count(k) > 1})}"
    )
    assert set(claimed) == owned, (
        f"uncovered: {sorted(owned - set(claimed))}; claimed but not owned: "
        f"{sorted(set(claimed) - owned)}"
    )


def test_no_category_claims_a_series_the_worker_fetches(settings):
    """A search agent on a deterministic path is a second, worse source.

    Breadth, HY and IG OAS and the net-liquidity components used to be search
    targets and are not any more, and the macro prints were added as fetches
    rather than as searches. A prompt that still asked for one would overwrite a
    Tier 1 reading with an aggregator's.
    """
    from macro_agent.worker_bridge import registry

    deterministic = {one.key for one in registry(settings).deterministic()}
    for one in dispatch(CONTEXT):
        overlap = deterministic & set(one["series"])
        assert not overlap, f"category {one['key']!r} claims deterministic series {sorted(overlap)}"


# ---------------------------------------------------------------------------
# The prompts
# ---------------------------------------------------------------------------


def test_the_tier_ladder_survived_the_port():
    prompt = dispatch(CONTEXT)[0]["prompt"]
    assert "prices within 1%, flows within 5%" in prompt
    assert "NEVER leave asof blank" in prompt
    assert "leave value empty" in prompt
    assert "never zero" in prompt
    assert "Weekly and monthly series are fresh until their next scheduled release" in prompt


def test_the_prompt_pastes_the_previous_observations_in_for_sanity():
    prompt = dispatch(CONTEXT)[0]["prompt"]
    assert "29 (Fear)" in prompt


def test_fred_and_breadth_are_no_longer_asked_for():
    """Both moved to a deterministic fetch; a search agent on that path is a worse source."""
    every = " ".join(one["prompt"] for one in dispatch(CONTEXT))
    assert "fredgraph" not in every
    assert "BAMLH0A0HYM2" not in every
    assert "200dma" not in every


def test_fedwatch_is_told_it_is_its_own_series():
    """The defect: `2026-W39.points.json` holds two rows both labelled Next FOMC."""
    prompt = next(one for one in dispatch(CONTEXT) if one["key"] == "macro")["prompt"]
    assert "never returned under the `next_fomc` key" in prompt


def test_no_search_agent_is_asked_for_etf_flows():
    """The worker computes all six ETF series from SoSoValue and Farside since 2026-09-24."""
    for one in dispatch(CONTEXT):
        assert not any("_etf_" in key for key in one["series"]), one["key"]
        assert "_etf_" not in one["prompt"], one["key"]


def test_the_five_prose_rows_are_asked_for_as_prose():
    prompt = next(one for one in dispatch(CONTEXT) if one["key"] == "macro")["prompt"]
    assert "The first five are PROSE: value empty, the sentence in display." in prompt


def test_every_series_a_category_owns_is_asked_for_in_its_prompt():
    """The defect: the macro prompt owned eight series and asked for five."""
    for one in dispatch(CONTEXT):
        for key in one["series"]:
            assert f"`{key}`" in one["prompt"], (one["key"], key)


def test_the_events_agent_is_told_not_to_re_search_what_the_crons_found():
    prompt = next(one for one in dispatch(CONTEXT) if one["key"] == "events")["prompt"]
    assert "WITHOUT re-searching them" in prompt
    assert "MU +5.50%" in prompt
    assert "Crowded AI unwind" in prompt
    assert "Micron Q4 FY2026" in prompt


def test_every_category_names_its_fallback_sources():
    for one in categories(
        asof="2026-09-25",
        previous={},
        prev_date="",
        prev_catalysts="",
        prev_expectation="",
        watchlist_deltas="",
        blackswan="",
        catalysts="",
    ):
        if one.key == "events":
            continue
        assert one.fallbacks and "fallback" not in one.fallbacks.lower()


# ---------------------------------------------------------------------------
# The retry stage
# ---------------------------------------------------------------------------


def test_only_tier_3_and_4_points_are_retried():
    points = [
        {"series": "cnn_fear_greed", "tier": 1},
        {"series": "liquidations_24h", "tier": 3, "note": "sources disagree by 12%"},
        {"series": "btc_etf_weekly_flow", "tier": 4, "note": "check SoSoValue"},
    ]
    assert [one["series"] for one in flagged(points)] == [
        "liquidations_24h",
        "btc_etf_weekly_flow",
    ]


def test_the_retry_prompt_names_the_flagged_metrics_and_the_fallback_order():
    prompt = retry_prompt(
        "crypto-flows",
        [{"series": "liquidations_24h", "tier": 4, "note": "check CoinDesk"}],
        asof="2026-09-25",
    )
    assert prompt is not None
    assert "liquidations_24h (currently Tier 4; note: check CoinDesk)" in prompt
    assert "news quoting Coinglass (CoinDesk / The Block)" in prompt
    assert "character for character" in prompt


def test_the_events_category_has_no_retry_stage():
    """It carries no data points to recover, exactly as `gather.workflow.js` had it."""
    assert retry_prompt("events", [{"series": "x", "tier": 4}], asof="2026-09-25") is None


def test_a_recovered_point_wins_only_when_its_tier_is_strictly_better():
    first = [
        {"series": "liquidations_24h", "tier": 3, "value": "250000000"},
        {"series": "cnn_fear_greed", "tier": 4, "value": ""},
    ]
    recovered = [
        # Same tier: not an improvement, so the original stands. Letting it win
        # would swap one unverified source for another and call it progress.
        {"series": "liquidations_24h", "tier": 3, "value": "310000000"},
        {"series": "cnn_fear_greed", "tier": 2, "value": "29"},
    ]
    merged, notes = merge_retry(first, recovered)
    by_key = {one["series"]: one for one in merged}
    assert by_key["liquidations_24h"]["value"] == "250000000"
    assert by_key["cnn_fear_greed"]["value"] == "29"
    assert any("still Tier 3" in one for one in notes)
    assert any("recovered from Tier 4 to Tier 2" in one for one in notes)


def test_a_point_the_retry_never_returned_keeps_its_original():
    first = [{"series": "liquidations_24h", "tier": 4, "value": ""}]
    merged, notes = merge_retry(first, [])
    assert merged == first
    assert any("still Tier 4" in one for one in notes)


# ---------------------------------------------------------------------------
# The plain rewrite of the prose rows
# ---------------------------------------------------------------------------

TEXT = frozenset({"fed_funds", "next_fomc", "fedwatch_next_fomc", "tariff_state"})
AGENT = "FOMC held 3.75%-4.00% on Sep 16, 12-0; hawkish hold, dots see 1 cut in 2026"


def _fed(display: str, original: str | None = AGENT) -> dict[str, object]:
    point: dict[str, object] = {
        "series": "fed_funds",
        "value": "",
        "display": display,
        "tier": 1,
        "asof": "2026-09-25",
        "source": "FOMC statement",
    }
    if original is not None:
        point["display_original"] = original
    return point


def test_a_rewrite_that_keeps_every_number_is_written():
    plain = (
        "The Fed held its rate at 3.75%-4.00% on Sep 16, in a 12-0 vote. It sounded firm, "
        "and its members expect 1 cut in 2026."
    )
    done = plain_prose({"macro": [_fed(plain)]}, text_keys=TEXT)
    assert not done.checks
    assert done.points["macro"][0]["display"] == plain
    assert "display_original" not in done.points["macro"][0]
    assert done.plain == {"macro": {"fed_funds": plain}}


def test_the_agents_sentence_is_what_the_batch_keeps():
    """The raw return is the evidence; the rewrite sits beside it, never over it."""
    plain = "The Fed held at 3.75%-4.00% on Sep 16, 12-0, and expects 1 cut in 2026."
    done = plain_prose({"macro": [_fed(plain)]}, text_keys=TEXT)
    said = done.raw["macro"][0]
    assert said["display"] == AGENT
    assert "display_original" not in said
    assert said == {k: v for k, v in _fed(AGENT).items() if k != "display_original"}


def test_a_rewrite_that_lost_a_number_is_refused_and_the_original_written():
    plain = "The Fed held its rate at 3.75%-4.00% in a unanimous vote, and expects one cut."
    done = plain_prose({"macro": [_fed(plain)]}, text_keys=TEXT)
    assert [one.key for one in done.checks] == ["plain_rewrite_lost_a_number"]
    refusal = done.checks[0]
    assert not refusal.ok and refusal.severity == "refuse"
    # It names what to restore: Sep 16, the 12-0 vote, the 1 cut and 2026.
    assert "dropped 0, 1, 12, 16, 2026" in refusal.sentence
    assert "run `narrative write` again" in refusal.sentence
    assert done.points["macro"][0]["display"] == AGENT
    assert not done.plain


def test_a_changed_decimal_counts_as_a_lost_number():
    """`config/voice.md`: 2.40 stays 2.40. `4.00%` rewritten as `4%` is a different figure."""
    plain = "The Fed held at 3.75%-4% on Sep 16, 12-0, and expects 1 cut in 2026."
    done = plain_prose({"macro": [_fed(plain)]}, text_keys=TEXT)
    assert "dropped 4.00" in done.checks[0].sentence


def test_a_point_with_no_rewrite_passes_through_untouched():
    point = _fed(AGENT, original=None)
    done = plain_prose({"macro": [point]}, text_keys=TEXT)
    assert done.points["macro"] == [point] == done.raw["macro"]
    assert not done.checks and not done.plain


def test_only_the_prose_rows_may_be_rewritten():
    point = {
        "series": "fedwatch_hike_prob_next",
        "value": "18.5",
        "display": "18.5% chance of a rise",
        "display_original": "18.5% odds of a hike on Oct 28",
        "tier": 2,
        "asof": "2026-09-25",
    }
    done = plain_prose({"macro": [point]}, text_keys=TEXT)
    assert [one.key for one in done.checks] == ["plain_rewrite_out_of_scope"]
    assert done.points["macro"][0]["display"] == "18.5% odds of a hike on Oct 28"


def test_the_voice_checker_warns_on_a_prose_row_in_shorthand():
    """Warn-only: a jargon term is named with its plain replacement, and nothing is refused."""
    shorthand = _fed("Hawkish hold at 3.75%-4.00%; the tape prints dovish dots", original=None)
    warnings = prose_voice({"macro": [shorthand]}, text_keys=TEXT)
    assert warnings and all(one.severity == "warn" for one in warnings)
    assert warnings[0].sentence.startswith("Voice: the fed_funds row")
    assert '"tape"' in warnings[0].sentence


def test_the_voice_checker_is_quiet_on_a_plain_row_and_skips_numeric_series():
    plain = _fed("The Fed held its rate at 3.75%-4.00% on Sep 16.", original=None)
    numeric = {"series": "fedwatch_hike_prob_next", "display": "tape prints 18.5%"}
    assert prose_voice({"macro": [plain, numeric]}, text_keys=TEXT) == []


# ---------------------------------------------------------------------------
# The number check counts, and reads sign, unit and order of magnitude
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("original", "rewrite", "fragment"),
    [
        # A lost minus sign turns an outflow into an inflow.
        ("ETH ETFs saw -$140.6M of outflows on Sep 24.",
         "ETH funds saw $140.6M leave on Sep 24.", "dropped -140.6m"),
        # Basis points are not percent.
        ("The 10-year rose 25bp to 4.90%.", "The 10-year yield rose 25% to 4.90%.",
         "dropped 25bp"),
        # A billion is not a million.
        ("Nvidia guided to $54 billion of sales.", "Nvidia expects $54 million of sales.",
         "dropped 54b"),
        # Two ones are two numbers: a set compare saw one.
        ("Officials see 1 cut in 2026 and 1 hike in 2027.",
         "Officials see 1 cut in 2026 and a hike in 2027.", "dropped 1"),
        # A number the original never had is refused too.
        ("The Fed held at 3.75%-4.00% on Sep 16.",
         "The Fed held at 3.75%-4.00% on Sep 16 and signalled 3 cuts.", "added 3"),
    ],
)
def test_a_rewrite_that_changes_a_number_is_refused(original, rewrite, fragment):
    done = plain_prose({"macro": [_fed(rewrite, original=original)]}, text_keys=TEXT)
    assert len(done.checks) == 1 and done.checks[0].severity == "refuse"
    assert fragment in done.checks[0].sentence, done.checks[0].sentence
    assert done.points["macro"][0]["display"] == original, "the original is written instead"


@pytest.mark.parametrize(
    ("original", "rewrite"),
    [
        ("Inflows were $4,150 million.", "Money coming in was $4150 million."),
        ("It rose +25bp on the day.", "It rose 25 basis points on the day."),
        ("The deficit is $1.8tn.", "The deficit is $1.8 trillion."),
        ("Core PCE rose 0.3 percent.", "Core PCE rose 0.3%."),
        ("The Fed held at 3.75%-4.00% on Sep 16, 12-0.",
         "On Sep 16 the Fed held at 3.75%-4.00%, in a 12-0 vote."),
    ],
)
def test_the_same_numbers_written_another_way_pass(original, rewrite):
    done = plain_prose({"macro": [_fed(rewrite, original=original)]}, text_keys=TEXT)
    assert not done.checks, done.checks
