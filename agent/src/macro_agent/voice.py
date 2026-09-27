"""The reader-facing text, checked against `config/voice.md`.

The dashboard's sentences are written for a smart reader who is not in
finance. `config/voice.md` says how, and `config/jargon.yaml` lists the trader
shorthand to replace. This module walks every field a reader sees and warns
on four things a machine can see:

- a Flesch-Kincaid grade over 8, on fields long enough for the formula to mean
  something (under eight words it is noise, and so it is on one or two short
  sentences, which is what a desk driver or a tail risk's priced-in read is);
- any sentence over 25 words;
- any term from the jargon list, with its plain replacement;
- any em dash.

**Warnings only, never refusals.** A readability score is a proxy, and a
sentence that scores grade 9 may still be the clearest way to say it. The
lines go to the run that wrote the text, which fixes what it can before the
proposal is written; they are not findings for the operator.

Numbers are replaced with a one-syllable word before scoring, so `$4,150`,
`0.2-0.3%` and `2026-09-25` neither raise the grade nor split a sentence.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import textstat
import yaml

from .config import repo_root
from .model import Check, PhaseCallProposal, Proposal, ScenarioGrade

#: Above this Flesch-Kincaid grade a field is flagged.
MAX_GRADE = 8.0
#: Fields shorter than this are not graded: the formula is noise on a title.
MIN_WORDS_FOR_GRADE = 8
#: Nor is a field of at most this many sentences, none longer than
#: `SHORT_SENTENCE_WORDS`: one long word in a two-line driver reads as grade 12.
SHORT_FIELD_SENTENCES = 2
SHORT_SENTENCE_WORDS = 15
#: No sentence runs longer than this.
MAX_SENTENCE_WORDS = 25

#: Written as a code point so this file does not itself carry one.
EM_DASH = chr(0x2014)

# A number as the text writes it: an optional sign or currency, digits with
# commas and decimals, an optional unit, and ranges joined by a dash or "to".
_NUMBER = re.compile(
    r"[+\-−]?[$€£]?\d[\d,]*(?:\.\d+)?(?:%|[kKmMbB]\b|bps\b|bp\b|x\b)?"
    r"(?:\s?[-–]\s?[+\-−]?[$€£]?\d[\d,]*(?:\.\d+)?(?:%|[kKmMbB]\b)?)*"
)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[\"'(\[]?[A-Z0-9$])")
_WORD = re.compile(r"[A-Za-z0-9$%][\w'$%&./-]*")


@dataclass(frozen=True, slots=True)
class Term:
    term: str
    plain: str
    pattern: re.Pattern[str]
    note: str | None = None


@dataclass(frozen=True, slots=True)
class Field:
    """One reader-facing field: where it is, and what it says."""

    path: str
    label: str
    text: str


def jargon_path() -> Path:
    return repo_root() / "config" / "jargon.yaml"


@cache
def terms(path: Path | None = None) -> tuple[Term, ...]:
    """The jargon list, longest term first so a phrase wins over a word inside it."""
    raw = yaml.safe_load((path or jargon_path()).read_text(encoding="utf-8")) or {}
    out: list[Term] = []
    for one in raw.get("terms") or ():
        term = str(one["term"])
        source = one.get("match") or rf"\b{re.escape(term)}\b"
        flags = 0 if one.get("case_sensitive") else re.IGNORECASE
        out.append(
            Term(
                term=term,
                plain=str(one["plain"]),
                pattern=re.compile(source, flags),
                note=one.get("note"),
            )
        )
    return tuple(sorted(out, key=lambda one: -len(one.term)))


def _without_numbers(text: str) -> str:
    return _NUMBER.sub("Ten", text)


def sentences(text: str) -> list[str]:
    """Split on sentence ends. Numbers are masked first, so a decimal is not an end."""
    masked = _without_numbers(text.strip())
    return [one for one in _SENTENCE_END.split(masked) if one.strip()]


def words(text: str) -> int:
    return len(_WORD.findall(text))


def grade(text: str) -> float:
    """Flesch-Kincaid grade, with numbers masked so they do not count as long words."""
    return float(textstat.flesch_kincaid_grade(_without_numbers(text)))


def jargon(text: str, vocabulary: Iterable[Term] | None = None) -> list[Term]:
    """Every listed term in the text, once each, a phrase beating a word inside it."""
    taken: list[tuple[int, int]] = []
    found: list[tuple[int, Term]] = []
    for term in vocabulary if vocabulary is not None else terms():
        for match in term.pattern.finditer(text):
            span = match.span()
            if any(span[0] < end and start < span[1] for start, end in taken):
                continue
            taken.append(span)
            if all(term is not seen for _, seen in found):
                found.append((span[0], term))
    return [term for _, term in sorted(found, key=lambda pair: pair[0])]


def problems(text: str) -> list[str]:
    """What is wrong with one field, one short phrase per problem. Empty when clean."""
    out: list[str] = []
    count = words(_without_numbers(text))
    said = sentences(text)
    short = len(said) <= SHORT_FIELD_SENTENCES and all(
        words(one) <= SHORT_SENTENCE_WORDS for one in said
    )
    if count >= MIN_WORDS_FOR_GRADE and not short:
        # Rounded first, so a warning never reads "grade 8.0 (aim for 8 or under)".
        score = round(grade(text), 1)
        if score > MAX_GRADE:
            out.append(f"reads at grade {score:.1f} (aim for {MAX_GRADE:.0f} or under)")
    longest = max((words(one) for one in said), default=0)
    if longest > MAX_SENTENCE_WORDS:
        out.append(f"has a {longest}-word sentence (limit {MAX_SENTENCE_WORDS})")
    found = jargon(text)
    if found:
        out.append(
            "uses jargon: " + "; ".join(f'"{one.term}" (say: {one.plain})' for one in found)
        )
    if EM_DASH in text:
        out.append("has an em dash (use a comma, a colon or a new sentence)")
    return out


# ---------------------------------------------------------------------------
# Which fields a reader sees
# ---------------------------------------------------------------------------


def _field(path: str, label: str, text: str | None) -> list[Field]:
    return [Field(path, label, text)] if text and text.strip() else []


def proposal_fields(found: Proposal) -> list[Field]:
    """Every field of a Monday proposal that the dashboard shows a reader.

    Band target labels are left out: they name a series, and the page's glossary
    explains the series. Setup names and styles are the operator's own
    shorthand for a trade, and the rationale is not shown on the page.
    """
    s = found.scenario_set
    out = _field("synthesis", "the weekly summary", s.synthesis)
    out += _field("probability_check", "the probability check", s.probability_check)
    out += _field("amend_reason", "the amendment reason", s.amend_reason)
    for index, swing in enumerate(s.swing_variables, start=1):
        if isinstance(swing, str):
            # The migrated weeks carry each swing variable as one sentence.
            out += _field(f"swing_{index}", f"swing variable {index}", swing)
            continue
        if not isinstance(swing, dict):
            continue
        for key in ("name", "cool", "hot"):
            value = swing.get(key)
            out += _field(
                f"swing_{index}_{key}",
                f"swing variable {index} {key}",
                value if isinstance(value, str) else None,
            )
    for scenario in s.scenarios:
        head = f"s{scenario.ordinal}"
        name = f"S{scenario.ordinal}"
        out += _field(f"{head}_title", f"{name} title", scenario.title)
        out += _field(f"{head}_positioning", f"{name} positioning", scenario.positioning)
        for clause in scenario.clauses:
            out += _field(
                f"{head}_clause_{clause.ordinal}", f"{name} clause {clause.ordinal}", clause.text
            )
        for index, target in enumerate(scenario.targets, start=1):
            if target.kind == "manual":
                out += _field(
                    f"{head}_target_{index}", f"{name} target {target.label!r}", target.text
                )
    for strategy in found.strategies:
        head = strategy.asset
        out += _field(f"{head}_stance", f"the {head} stance", strategy.stance)
        out += _field(f"{head}_posture", f"the {head} posture", strategy.posture)
        out += _field(f"{head}_amend_reason", f"the {head} amendment reason", strategy.amend_reason)
        for focus in strategy.sector_focus:
            out += _field(
                f"{head}_focus_{focus.band.lower()}", f"the {head} {focus.band} row", focus.text
            )
        for holding in strategy.holdings_check:
            out += _field(
                f"{head}_holding_{holding.ticker.lower()}",
                f"the {head} {holding.ticker} holdings line",
                holding.text,
            )
    for setup in found.setups:
        out += _field(
            f"setup_{_slug(setup.name)}_gate", f"setup {setup.name!r} gate", setup.gate_text
        )
    for call in found.phase_calls:
        out += _field(
            f"{call.asset}_change_note", f"the {call.asset} change note", call.change_note
        )
    return out


def read_fields(reads: Iterable[PhaseCallProposal]) -> list[Field]:
    """Every field of a fresh phase read that the dashboard shows a reader."""
    out: list[Field] = []
    for call in reads:
        head = call.asset
        out += _field(f"{head}_reason", f"the {head} reason", call.reason)
        out += _field(f"{head}_stage_reason", f"the {head} stage reason", call.stage_reason)
        out += _field(f"{head}_falsifier", f"the {head} falsifier", call.falsifier)
        for index, line in enumerate(call.evidence, start=1):
            out += _field(f"{head}_evidence_{index}", f"the {head} evidence {index}", line)
        for condition in call.conditions:
            out += _field(
                f"{head}_condition_{condition.ordinal}",
                f"the {head} condition {condition.ordinal}",
                condition.text,
            )
    return out


def grade_fields(
    grades: Iterable[ScenarioGrade], *, eq_call: str, crypto_call: str, key_miss: str
) -> list[Field]:
    """The grading prose the Calibration panel shows: the notes, the key miss, the two calls."""
    out: list[Field] = []
    for index, one in enumerate(grades, start=1):
        out += _field(f"grade_{index}_note", f"grade note {index}", one.note)
    out += _field("key_miss", "the key miss", key_miss)
    out += _field("eq_call", "the equities call line", eq_call)
    out += _field("crypto_call", "the crypto call line", crypto_call)
    return out


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def checks(fields: list[Field], *, scope: str) -> list[Check]:
    """One warning per field that breaks the voice, or one pass line when none does.

    Every line is `warn` at most. A voice problem never refuses a write.
    """
    out = [
        Check(
            f"voice_{one.path}",
            False,
            f"Voice: {one.label} {', '.join(found)}. It reads: {one.text}",
            severity="warn",
        )
        for one in fields
        if (found := problems(one.text))
    ]
    if out:
        return out
    return [
        Check(
            f"voice_{scope.replace(' ', '_')}",
            True,
            f"Voice: all {len(fields)} reader-facing fields in the {scope} read at grade "
            f"{MAX_GRADE:.0f} or under, with no sentence over {MAX_SENTENCE_WORDS} words, no "
            "listed jargon and no em dash.",
            severity="warn",
        )
    ]


def proposal_checks(found: Proposal) -> list[Check]:
    return checks(proposal_fields(found), scope="proposal")


def read_checks(reads: Iterable[PhaseCallProposal]) -> list[Check]:
    return checks(read_fields(reads), scope="phase reads")


def grade_checks(
    grades: Iterable[ScenarioGrade], *, eq_call: str, crypto_call: str, key_miss: str
) -> list[Check]:
    return checks(
        grade_fields(grades, eq_call=eq_call, crypto_call=crypto_call, key_miss=key_miss),
        scope="grading",
    )


def is_voice(check: Check) -> bool:
    return check.key.startswith("voice_")
