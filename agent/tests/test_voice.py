"""The voice checker: plain text passes, trader shorthand is flagged, nothing is refused.

The W39 sentences below are the live week's own text, before `config/voice.md`
existed. The W40 fixture is the same week rewritten in that voice, and it is the
example the Monday run copies, so it has to pass.
"""

from __future__ import annotations

import re

import pytest
import yaml

from macro_agent import voice
from macro_agent.preflight import Tape, phase_read, walk
from macro_agent.validate import phase_reads, refusals

from .conftest import ROOT, full

VOICE_MD = ROOT / "config" / "voice.md"
JARGON_YAML = ROOT / "config" / "jargon.yaml"

#: Reader-facing text from the live W39 week, verbatim.
W39 = {
    "summary": (
        "The Fed hiked into a 5% ten-year and a closed Hormuz, and the risk complex answered "
        "by sending the VIX to a run low and tightening investment-grade spreads, which is the "
        "week's real information: nothing that prices risk is treating the macro as a risk."
    ),
    "title": "Narrow and high-yielding: memory carries a thinning tape",
    "positioning": (
        "Cut high-beta, not quality, and only once a confirming leg prints; hold cash for a "
        "retest of the rising 200dma; do not use gold or crypto as the equity hedge"
    ),
    "clause": "The Sep 22–24 cluster clears without a second record tail",
    "reason": "the internals deteriorated again while every contraction condition moved further away",
}


def render_table() -> str:
    """The jargon table as `config/voice.md` prints it, rendered from the YAML."""
    raw = yaml.safe_load(JARGON_YAML.read_text(encoding="utf-8"))
    rows = ["| Instead of | Say |", "| --- | --- |"]
    for one in raw["terms"]:
        plain = one["plain"]
        if one.get("note"):
            plain += ". " + one["note"][0].upper() + one["note"][1:]
        rows.append(f"| {one['term']} | {plain} |")
    return "\n".join(rows)


def test_voice_md_prints_the_same_jargon_list_the_checker_reads():
    """One list, two readers: a term added to one file and not the other fails here."""
    assert render_table() in VOICE_MD.read_text(encoding="utf-8")


def test_every_jargon_pattern_matches_its_own_term_or_its_example():
    for term in voice.terms():
        examples = [term.term, *re.findall(r'"([^"]+)"', term.note or "")]
        assert any(term.pattern.search(one) for one in examples), term.term


def test_neither_config_file_carries_an_em_dash():
    for path in (VOICE_MD, JARGON_YAML):
        assert voice.EM_DASH not in path.read_text(encoding="utf-8"), path


@pytest.mark.parametrize("name", sorted(W39))
def test_the_w39_text_is_flagged(name):
    assert voice.problems(W39[name]), name


def test_the_w39_positioning_names_each_term_and_its_replacement():
    found = " ".join(voice.problems(W39["positioning"]))
    for term, plain in (
        ("high-beta", "the most volatile stocks"),
        ("leg", "part"),
        ("prints", "comes out"),
        ("200dma", "200-day average"),
    ):
        assert f'"{term}"' in found
        assert plain in found


def test_a_phrase_wins_over_the_word_inside_it():
    names = [one.term for one in voice.jargon("the risk complex held at the floor of the band")]
    assert names == ["risk complex", "floor of the band"]


@pytest.mark.parametrize(
    "text",
    [
        "Core PCE inflation, out Sep 30, rises 0.2-0.3% from the month before",
        "The damage spreads to stocks and shipping.",
        "The page lists each tail risk and what is priced in.",
        "Add the two numbers together.",
    ],
)
def test_ordinary_english_is_not_jargon(text):
    """`Sep` is a month, not the SEP; `spreads to` is a verb; a tail risk is not an auction tail."""
    assert voice.jargon(text) == []


def test_numbers_are_ignored():
    """Levels, ranges and dates neither raise the grade nor split a sentence."""
    text = (
        "BTC held $76,000 on 2026-09-25, the 10-year sat at 4.90-5.12%, and ETH ETFs saw "
        "-$140.6M."
    )
    assert voice.problems(text) == []
    assert len(voice.sentences(text)) == 1
    assert voice.grade(text) == voice.grade(re.sub(r"\S*\d\S*", "Ten", text))


def test_a_long_sentence_and_an_em_dash_are_flagged():
    long = " ".join(["the cat sat"] * 9) + "."
    assert any("27-word sentence" in one for one in voice.problems(long))
    assert any("em dash" in one for one in voice.problems(f"Stocks rose {voice.EM_DASH} then fell."))


def test_a_short_field_is_not_graded():
    """Under eight words the formula is noise: a hard word in a title is not a grade 14 title."""
    assert voice.problems("Institutional capitulation materialises") == []


def test_the_rewritten_fixture_passes(w40, reads):
    found = full(w40, reads)
    lines = voice.proposal_checks(found) + voice.read_checks(phase_reads(reads))
    assert [one.sentence for one in lines if not one.ok] == []
    assert [one.key for one in lines] == ["voice_proposal", "voice_phase_reads"]


def test_a_voice_warning_never_refuses(w40, reads):
    """Put the W39 text back into the fixture: every line warns and none refuses."""
    scenario = w40["scenario_set"]["scenarios"][1]
    scenario["positioning"] = W39["positioning"]
    scenario["title"] = W39["title"]
    w40["scenario_set"]["synthesis"] = W39["summary"]
    lines = voice.proposal_checks(full(w40, reads))
    assert {one.key for one in lines if not one.ok} == {
        "voice_s2_positioning",
        "voice_s2_title",
        "voice_synthesis",
    }
    assert all(one.severity == "warn" for one in lines)
    assert refusals(lines) == []
    assert all(voice.is_voice(one) for one in lines)
    # And the walk the preflight prints beside it is untouched by the prose.
    assert len(walk(full(w40, reads), Tape())) == 12
    assert phase_read(full(w40, reads), Tape())


def test_a_migrated_swing_variable_written_as_one_sentence_is_checked(w40, reads):
    """Weeks carried over from the journals store each swing variable as a string."""
    w40["scenario_set"]["swing_variables"][0] = (
        "The Sep 22–24 cluster: cool = the cluster clears near where-issued / hot = a second tail"
    )
    lines = voice.proposal_checks(full(w40, reads))
    assert [one.key for one in lines if not one.ok] == ["voice_swing_1"]


# ---------------------------------------------------------------------------
# Review of 2026-09-25: false positives on the desk's short fields
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Micron beat estimates on memory demand. Shares rose after hours.",
        "Regulators cleared the merger on Friday.",
        "The company published a guide to its new chips.",
        "Buyers returned after a failed takeover bid.",
        "A cluster of lawmakers opposed the bill.",
        "The tail end of the rally faded on Friday.",
        "The ruling clears the way for the deal.",
    ],
)
def test_plain_uses_of_the_narrowed_terms_are_not_jargon(text):
    assert voice.jargon(text) == [], [one.term for one in voice.jargon(text)]


@pytest.mark.parametrize(
    ("text", "term"),
    [
        ("Nvidia raised guide for the quarter.", "guide"),
        ("The stock gapped higher as management guided up.", "guide"),
        ("There is a bid under long bonds again.", "bid"),
        ("The 10Y auction tailed by 2bp.", "tail"),
        ("A second record tail would change the call.", "tail"),
        ("The 3Y/10Y/30Y cluster sets the tone for yields.", "cluster"),
        ("Bitcoin clears 76,000 for the first time.", "clears"),
        ("The index cleared resistance at the 200-day line.", "clears"),
    ],
)
def test_trader_uses_of_the_narrowed_terms_are_still_jargon(text, term):
    assert term in [one.term for one in voice.jargon(text)]


def test_a_field_of_one_or_two_short_sentences_is_not_graded():
    """The desk's drivers are one or two short sentences; the formula is noise there."""
    short = "Shares slumped after disappointing semiconductor inventory commentary."
    assert voice.grade(short) > voice.MAX_GRADE
    assert voice.problems(short) == []
    two = short + " Competitors followed immediately afterwards."
    assert voice.problems(two) == []
    long = " ".join([short] * 3)
    assert any("reads at grade" in one for one in voice.problems(long))
