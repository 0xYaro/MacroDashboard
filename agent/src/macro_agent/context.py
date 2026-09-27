"""What each mode hands the model before it is asked for anything.

`docs/CONTRACT.md` lists Monday's fourth obligation as "hand the authoring agent
its context", and nothing did. This is it.

The context is assembled from the database and from `config/series.yaml`, and
from the vault only for the five framework files, which are reference material
the worker has no reason to hold. It is deliberately not assembled from the
markdown journals: the whole point of this repository is that the structured
state is canonical and the prose is a rendering of it, and a context read back
out of last week's journal would put the renderer back in front of the model.

Three properties the assembly has to have, and each one is a mistake it prevents:

**It carries the ledger and the rules, not a summary of them.** The authoring
judgment is informed by twelve graded weeks and twelve standing rules, and a
summary is a place for the twelve to quietly become eight.

**It carries the target rows with their kinds.** A grading pass that sees only
the band targets counts a different `n` than the one the ledger recorded, which
is the W36 defect exactly.

**It carries what the worker already decided.** The setup grades are read, not
recomputed. A setup's outcome is a fact about the tape that the worker computed
deterministically on this morning's run, and a second grader on one ledger is
two ledgers.

## The phase step gets its own context, and it is blind

The phase is classified fresh every Monday (`docs/DECISIONS.md` 2026-09-23),
so the first thing the run reads is `phase_context`: this week's board, the
last twelve weeks of every numeric series, the catalysts and tail risks, and
the two cycle frameworks' definitions. It holds no phase call, no ledger (whose
`eq_call` reads "Phase 3 held"), no rule text and no graded set, and before it
is printed it is searched for any of the active calls' own sentences and for
any "Phase N" outside the frameworks. A context that fails the search is
refused, not trimmed: a read taken over a leaked prior is not fresh, and
nothing downstream could tell.

Two things keep that refusal from firing on nothing. A "Phase N" inside a
catalyst or tail-risk name ("US-China Phase 2 trade deal") is blanked, and
only the count of blankings is kept: the matches themselves stay out of the
context, so the search never finds the text the redaction just cleaned. And
the files `SKILL.md` has the run read before `author classify` (the runbook
itself and `config/voice.md`) are searched for the active calls' sentences
too, because a voice example copied from last week's reason is a leak that
never passes through this context at all.

The frameworks go in without their `## Phase Transition Tracker` sections. Those
score the step to the next phase and carry the old 3-of-4 upgrade rule, which
is exactly the sequential read the operator retired.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .config import Settings, repo_root
from .grade import calibration_read
from .model import PHASE_NAMES, Refused
from .store import Store

#: The frameworks the authoring run reads, resolved by filename because folders
#: move. `12 - Complete Market Approach` and `15 - Ray Trading Blueprint` are
#: read in part; the other three in full.
FRAMEWORKS: tuple[tuple[str, str | None], ...] = (
    ("13 - Crypto Market Cycle Framework", None),
    ("14 - Equities Market Cycle Framework", None),
    ("12 - Complete Market Approach", "### Net Exposure Heuristics"),
    ("15 - Ray Trading Blueprint", "## Trading Setups"),
    ("cftc-fund-positioning-and-naaim-reference", None),
)

#: How far back the evaluator needs to see to resolve a predicate window.
HISTORY_DAYS = 120

#: The two frameworks the phase step reads, and the section it reads without.
PHASE_FRAMEWORKS = ("13 - Crypto Market Cycle Framework", "14 - Equities Market Cycle Framework")
TRACKER_HEADING = "## Phase Transition Tracker"

#: How many weeks of each numeric series the phase step sees, one reading per
#: ISO week (the last in it). Enough to see a trend turn, a breadth decay run
#: its course, a 200dma slope flip.
TREND_WEEKS = 12

#: The fields of an observation's `extra` a phase read can use: the same ones
#: a condition may name through the `field` accessor.
TREND_FIELDS = ("dma200_pct", "dma200_slope", "spread_2s10s_bps", "band")

#: The files `SKILL.md` has the run read before `author classify`, relative to
#: the repository root. They reach the model without passing through the phase
#: context, so they are searched for the active calls' sentences separately.
PRE_CLASSIFY_FILES = ("agent/SKILL.md", "config/voice.md")

#: A phase named by number. Outside the frameworks this is a prior call leaking
#: in through prose, a narrative row that quoted last week's journal, say.
PHASE_MENTION = re.compile(r"\b(?:phase|stage)\s*[1-5]\b|\bP[1-5]\b", re.IGNORECASE)


def resolve_framework(vault: Path, name: str) -> Path | None:
    """Find a framework by filename, never by a remembered path.

    The vault skill's own rule, kept: folders move, and a path that used to work
    fails silently as an empty section rather than loudly as a missing file.
    """
    for candidate in vault.rglob(f"{name}.md"):
        if ".git" not in candidate.parts:
            return candidate
    return None


def framework_section(text: str, heading: str | None) -> str:
    """One `## ` or `### ` section of a framework, or the whole file.

    The section runs to the next heading at the same level or shallower, which
    is how the vault skill reads `### Net Exposure Heuristics` without dragging
    in the rest of `12 - Complete Market Approach`.
    """
    if heading is None:
        return text
    depth = len(heading) - len(heading.lstrip("#"))
    lines = text.splitlines()
    out: list[str] = []
    inside = False
    for line in lines:
        if line.strip() == heading:
            inside = True
            out.append(line)
            continue
        if inside:
            stripped = line.lstrip("#")
            level = len(line) - len(stripped)
            if line.startswith("#") and 0 < level <= depth:
                break
            out.append(line)
    return "\n".join(out).strip()


def frameworks(settings: Settings) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for name, heading in FRAMEWORKS:
        path = resolve_framework(settings.vault_path, name)
        if path is None:
            out.append(
                {
                    "name": name,
                    "path": None,
                    "heading": heading,
                    "text": "",
                    "note": (
                        f"{name}.md was not found under {settings.vault_path}. The authoring run "
                        "proceeds without it and says so rather than inventing the convention it "
                        "would have carried."
                    ),
                }
            )
            continue
        text = path.read_text(encoding="utf-8")
        out.append(
            {
                "name": name,
                "path": str(path),
                "heading": heading,
                "text": framework_section(text, heading),
                "note": None,
            }
        )
    return out


def without_section(text: str, heading: str) -> str:
    """A framework with one `## ` section cut out, to the next heading at its level."""
    depth = len(heading) - len(heading.lstrip("#"))
    out: list[str] = []
    skipping = False
    for line in text.splitlines():
        if line.strip() == heading:
            skipping = True
            continue
        if skipping:
            stripped = line.lstrip("#")
            level = len(line) - len(stripped)
            if line.startswith("#") and 0 < level <= depth:
                skipping = False
            else:
                continue
        out.append(line)
    return "\n".join(out).strip()


def phase_frameworks(settings: Settings) -> list[dict[str, Any]]:
    """The two cycle frameworks, whole except for the transition tracker."""
    out = []
    for one in frameworks(settings):
        if one["name"] not in PHASE_FRAMEWORKS:
            continue
        out.append({**one, "text": without_section(one["text"], TRACKER_HEADING)})
    return out


def redact(value: Any, found: list[str]) -> Any:
    """Every string with a numbered phase blanked, and each blanking recorded."""
    if isinstance(value, str):
        def blank(match: re.Match[str]) -> str:
            found.append(match.group(0))
            return "[phase redacted]"

        return PHASE_MENTION.sub(blank, value)
    if isinstance(value, list):
        return [redact(one, found) for one in value]
    if isinstance(value, dict):
        return {key: redact(one, found) for key, one in value.items()}
    return value


def weekly_trend(rows: list[dict[str, Any]], numeric: set[str]) -> dict[str, list[dict[str, Any]]]:
    """The last reading of each ISO week, per numeric series, oldest first."""
    by_key: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        key = row["key"]
        if key not in numeric or row.get("value") is None:
            continue
        asof = row["asof"]
        year, week, _ = asof.isocalendar()
        label = f"{year}-W{week:02d}"
        held = by_key.setdefault(key, {}).get(label)
        if held is not None and held["asof"] >= asof.isoformat():
            continue
        extra = row.get("extra") or {}
        if isinstance(extra, str):
            extra = json.loads(extra)
        point: dict[str, Any] = {"asof": asof.isoformat(), "value": str(row["value"])}
        point.update({name: extra[name] for name in TREND_FIELDS if extra.get(name) is not None})
        by_key[key][label] = {"week": label, **point}
    return {
        key: [weeks[label] for label in sorted(weeks)][-TREND_WEEKS:]
        for key, weeks in sorted(by_key.items())
    }


def _call_sentences(call: dict[str, Any]) -> list[str]:
    """The sentences an active call carries that are long enough to be its own."""
    sentences = [
        call.get("reason"),
        call.get("falsifier"),
        call.get("stage_reason"),
        call.get("change_note"),
        *(call.get("evidence") or ()),
    ]
    return [text for text in ((one or "").strip() for one in sentences) if len(text) >= 24]


def leaks(
    context: dict[str, Any],
    prior_calls: list[dict[str, Any]],
    *,
    files: dict[str, str] | None = None,
) -> list[str]:
    """What in a phase context, or in a file read beside it, would tell the reader last week's call.

    Three searches. Every sentence the active calls themselves carry (the
    reason, the evidence, the falsifier, the stage reason, the change note),
    anywhere in the context. The same sentences in each of `files`, the text of
    what the run reads before classifying, keyed by path. And any numbered
    phase outside the framework text, which should already have been
    redacted, so a hit here means redaction missed a field. The redaction's
    own count is not searched: it records that something was cleaned, not
    what. An empty list is the only answer that lets the context be printed.
    """
    found: list[str] = []
    outside = {
        key: value for key, value in context.items() if key not in ("frameworks", "redacted")
    }
    blob = json.dumps(outside, default=str, ensure_ascii=False)
    for call in prior_calls:
        for text in _call_sentences(call):
            if text in blob:
                found.append(f"the active {call.get('asset')} call's own sentence: {text[:80]}")
            for path, body in (files or {}).items():
                if text.lower() in body.lower():
                    found.append(
                        f"{path} quotes the active {call.get('asset')} call's own sentence: "
                        f"{text[:80]}. Replace that text in {path} with an invented one"
                    )
    found += [
        f"a numbered phase outside the frameworks: {match.group(0)!r}"
        for match in PHASE_MENTION.finditer(blob)
        if match.group(0) != "[phase redacted]"
    ]
    for key in ("phase_calls", "ledger", "rules", "graded_scenario_set", "phase_reads"):
        if key in context:
            found.append(f"the {key!r} section, which only the full context carries")
    return found


async def phase_context(
    store: Store, settings: Settings, *, week: str, now: datetime
) -> dict[str, Any]:
    """Everything the fresh phase read needs, and nothing that says what last week's was.

    Refuses rather than prints when `leaks` finds anything, so the only way to
    see this context is to see it clean.
    """
    from .worker_bridge import registry as load_registry

    reg = load_registry(settings)
    numeric = {one.key for one in reg.ordered() if one.value_kind == "numeric"}
    since = now.date() - timedelta(weeks=TREND_WEEKS + 1)
    redacted: list[str] = []
    context: dict[str, Any] = {
        "week": week,
        "now": now.isoformat(),
        "task": (
            "Classify equities and crypto into one of the five phases each, from this data and "
            "the two frameworks alone. Any phase may follow any phase. Last week's call is not "
            "here on purpose; it is shown after `author classify` has written this read."
        ),
        "phase_names": {
            asset: {str(number): name for number, name in names.items()}
            for asset, names in PHASE_NAMES.items()
        },
        "snapshot": redact([_stringify(one) for one in await store.snapshot()], redacted),
        "weekly": weekly_trend(await store.history_rows(since=since), numeric),
        "catalysts": redact(await store.catalyst_lines(), redacted),
        "tail_risks": redact(await store.tail_risk_lines(), redacted),
        "frameworks": phase_frameworks(settings),
        "frameworks_note": (
            "The frameworks' Phase Transition Tracker sections are left out on purpose. They "
            "score the step to the next phase under a 3-of-4 upgrade rule that docs/DECISIONS.md "
            "2026-09-23 retired: the phase is classified fresh, and the four conditions a read "
            "carries are what must stay true for its own phase to hold."
        ),
        "series": _series_catalogue(settings),
        "predicates": settings.predicates_md.read_text(encoding="utf-8")
        if settings.predicates_md.is_file()
        else "",
    }
    # The count only. The matches are exactly the numbered phases the context
    # must not carry, so keeping them would put back what was just cleaned.
    context["redacted"] = len(redacted)
    found = leaks(context, await store.active_phase_calls(), files=pre_classify_files())
    if found:
        raise Refused(
            "the phase context would show last week's call, so it was not printed: "
            + "; ".join(found)
            + ". A read taken over it would not be fresh."
        )
    return context


def pre_classify_files(root: Path | None = None) -> dict[str, str]:
    """The text of every file `SKILL.md` has the run read before `author classify`."""
    root = root or repo_root()
    out: dict[str, str] = {}
    for name in PRE_CLASSIFY_FILES:
        path = root / name
        if path.is_file():
            out[name] = path.read_text(encoding="utf-8")
    return out


async def narrative_context(
    store: Store, *, keys: list[str], asof: date, prev_week: str, prev_synthesis: str | None
) -> dict[str, Any]:
    """Everything the four search agents need, and nothing they should re-search.

    The watchlist deltas, the tail risks and the catalysts all come out of
    `macro_core`, where the worker parsed them from the three files the OpenClaw
    crons wrote at 08:45 and 09:15. The events agent is told they are already
    logged so it searches only what they do not cover, which is the same
    instruction `gather.workflow.js` carried and the reason the 09:30 run is
    scheduled after those two crons rather than before them.
    """
    return {
        "asof": asof.isoformat(),
        "prev_week": prev_week,
        "prev_expectation": prev_synthesis or "",
        "previous": await store.latest_agent_observations(keys),
        "watchlist_deltas": await store.recent_watchlist_changes(since=asof - timedelta(days=9)),
        "blackswan": await store.tail_risk_lines(),
        "catalysts": await store.catalyst_lines(),
        "series": keys,
    }


async def authoring_context(
    store: Store,
    settings: Settings,
    *,
    week: str,
    graded_week: str,
    now: datetime,
) -> dict[str, Any]:
    """Everything the Monday judgment reads, in one object."""
    ledger = await store.ledger()
    graded_set = await store.scenario_set_for_week(graded_week)
    return {
        "week": week,
        "graded_week": graded_week,
        "now": now.isoformat(),
        # Section 0: what is being graded, and against what record.
        "graded_scenario_set": graded_set,
        "graded_targets": [
            {
                "scenario_ordinal": one["scenario_ordinal"],
                "target_id": str(one["id"]),
                "label": one["label"],
                "kind": one["kind"],
                "series_key": one["series_key"],
                "comparator": one["comparator"],
                "lo": None if one["lo"] is None else str(one["lo"]),
                "hi": None if one["hi"] is None else str(one["hi"]),
                "text": one["text"],
            }
            for one in await store.target_rows_for_week(graded_week)
        ],
        # Read, never recomputed. The worker graded these deterministically on
        # this morning's run and a second grader on one ledger is two ledgers.
        "setup_grades": [_stringify(one) for one in await store.setup_grades_for_week(graded_week)],
        "ledger": [
            {
                "week": one.week,
                "base_scenario_label": one.base_scenario_label,
                "base_grade": one.base_grade,
                "other_grades": one.other_grades,
                "eq_call": one.eq_call,
                "crypto_call": one.crypto_call,
                "key_miss": one.key_miss,
                "targets_hit": one.targets_hit,
                "targets_n": one.targets_n,
                "brier": None if one.brier is None else str(one.brier),
                "premise_moved": one.premise_moved,
            }
            for one in ledger
        ],
        "calibration": calibration_read(settings, ledger),
        "rules": await store.rules(),
        # Sections 1 to 5: the board, the standing judgment, the feed.
        "snapshot": [_stringify(one) for one in await store.snapshot()],
        # Last week's calls, and this week's fresh reads beside them. The reads
        # were written by `author classify` before this context was served,
        # which is the only order in which they are fresh.
        "phase_calls": [_stringify(one) for one in await store.active_phase_calls()],
        "phase_reads": [
            {
                "phase_call_id": one.call_id,
                "asset": one.asset,
                "phase": one.phase,
                "phase_name": one.phase_name,
                "stage": one.stage,
                "prior_phase": one.prior_phase,
                "prior_week": one.prior_week,
                "move": one.move,
                "steps": one.steps,
                "change_note": one.change_note,
            }
            for one in await store.proposed_reads(week)
        ],
        "catalysts": await store.catalyst_lines(),
        "catalyst_ids": await store.catalysts_by_name(),
        "tail_risks": await store.tail_risk_lines(),
        "holdings": [{"ticker": t, "book": b} for t, b in await store.holdings()],
        "frameworks": frameworks(settings),
        # The vocabulary a typed condition may use.
        "series": _series_catalogue(settings),
        "predicates": settings.predicates_md.read_text(encoding="utf-8")
        if settings.predicates_md.is_file()
        else "",
    }


def _series_catalogue(settings: Settings) -> list[dict[str, Any]]:
    """Every series a condition may name, with its canonical unit.

    The unit is carried because every condition is written in it and nothing
    converts downstream: a condition comparing `2.70` against `300` is silently
    false and looks correct, which is why `hy_oas_bps` is basis points and says
    so here.
    """
    from .worker_bridge import registry as load_registry

    reg = load_registry(settings)
    return [
        {
            "key": one.key,
            "label": one.label,
            "table_group": one.table_group,
            "value_kind": one.value_kind,
            "unit": one.unit,
            "cadence": one.cadence,
            "fetch_path": one.fetch_path,
            "typeable": one.value_kind == "numeric",
        }
        for one in reg.ordered()
    ]


def _stringify(row: dict[str, Any]) -> dict[str, Any]:
    """Decimals and dates to strings, so the context is JSON without a custom encoder."""
    out: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, (datetime, date)):
            out[key] = value.isoformat()
        elif hasattr(value, "quantize"):
            out[key] = str(value)
        elif isinstance(value, list):
            out[key] = [_stringify(one) if isinstance(one, dict) else one for one in value]
        elif isinstance(value, dict):
            out[key] = _stringify(value)
        else:
            out[key] = value
    return out
