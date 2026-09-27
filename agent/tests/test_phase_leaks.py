"""What the blind phase step may and may not be shown, without a database.

Two ways the leak search used to go wrong, both of which would have killed a
Monday: it refused a clean context because the redaction's own record of what
it cleaned was searched, and it never looked at the files the runbook has the
run read before classifying, one of which quoted last week's reason.
"""

from __future__ import annotations

from macro_agent.context import PRE_CLASSIFY_FILES, leaks, pre_classify_files, redact

from .conftest import ROOT

#: The W39 equities call's reason, as the live row carries it. `config/voice.md`
#: used it verbatim as a "before" example, and SKILL.md has the run read that
#: file before `author classify`.
W39_EQUITIES_REASON = (
    "the internals deteriorated again while every contraction condition moved further away"
)

ACTIVE = [{"asset": "equities", "reason": W39_EQUITIES_REASON, "evidence": []}]


def test_a_catalyst_named_after_a_phase_is_cleaned_not_refused():
    found: list[str] = []
    catalysts = redact(["2026-10-02 US-China Phase 2 trade deal"], found)
    assert catalysts == ["2026-10-02 US-China [phase redacted] trade deal"]
    context = {"catalysts": catalysts, "redacted": len(found)}
    assert leaks(context, ACTIVE) == []


def test_the_redaction_record_is_never_searched():
    """Even a list of raw matches under `redacted` is the record, not a leak."""
    assert leaks({"catalysts": [], "redacted": ["Phase 2"]}, ACTIVE) == []


def test_a_file_read_before_classify_that_quotes_the_call_is_a_leak():
    files = {"config/voice.md": f'Before: "{W39_EQUITIES_REASON.capitalize()}"'}
    found = leaks({}, ACTIVE, files=files)
    assert len(found) == 1
    assert found[0].startswith("config/voice.md quotes the active equities call")


def test_the_runbook_and_the_voice_are_what_is_searched_and_quote_no_standing_call():
    assert PRE_CLASSIFY_FILES == ("agent/SKILL.md", "config/voice.md")
    files = pre_classify_files(ROOT)
    assert set(files) == set(PRE_CLASSIFY_FILES)
    assert leaks({}, ACTIVE, files=files) == []
