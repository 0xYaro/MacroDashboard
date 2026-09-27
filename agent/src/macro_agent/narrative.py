"""Mode A: the series the worker cannot fetch, and the dated event log.

The worker holds no LLM client by decision, so without this module the series
whose `fetch_path` is `agent_0930` would never be written and every one of them
would report `not_connected` for ever. This module is the other side of that seam.

It does not call a model. The model is the Claude run that `SKILL.md` drives:
this module renders the prompts it dispatches, and then takes what came back,
decides whether to believe it, and writes what survives. That split is the
point. A search agent's return is untrusted input, and the code that decides
whether a figure may enter `macro_core` has to be readable on its own, without
a model in the loop.

## The tier discipline, carried from `gather.workflow.js`

Two stages. Gather each category in parallel, then re-fetch only the Tier 3 and
Tier 4 points against a named fallback source order, keeping a recovered point
only when its tier is *strictly* better than the one it replaces. A metric that
still cannot be sourced after the retry is Tier 4: an empty value, a note naming
what to check, and no number. Never a plausible figure, never a zero, never
yesterday's reading stamped onto today.

## What a missed reading costs

Every series on this path is `replayable: false`. A source that serves only the
present moment makes a missed session a permanent gap however often it prints,
so a Tier 4 point opens a `macro_quality.gap` marked permanent, and every
condition over that series reports `unevaluable` for as long as the gap sits
inside its window. Nothing is interpolated and no value is carried into a day it
did not describe.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from . import voice
from .config import Settings
from .model import Check, Event, NarrativeObservation
from .prompts import Category, categories, retry
from .store import Store
from .validate import events as decode_events
from .validate import narrative_points
from .worker_bridge import registry as load_registry

LOG = logging.getLogger(__name__)


def agent_series(settings: Settings) -> list[str]:
    """The keys this mode owns, read from `config/series.yaml` and never listed here.

    `Registry.agent_only()` is the declaration. A list written into this module
    would be a second one, and the day a series moves to a scraper the two would
    disagree about which run owns it.
    """
    return [one.key for one in load_registry(settings).agent_only()]


def dispatch(context: dict[str, Any]) -> list[dict[str, Any]]:
    """The four search agents to run, with their prompts already rendered.

    Returned rather than executed, because the model that answers them is the
    run driving `SKILL.md` and this package holds no LLM client either. What it
    holds is the prompt text, so that the prompt proven over twelve weeks is
    versioned beside the code that consumes its output rather than retyped into
    a chat each Monday.
    """
    cats = categories(
        asof=context["asof"],
        previous={
            key: str(one.get("display") or one.get("value") or "")
            for key, one in (context.get("previous") or {}).items()
        },
        prev_date=context.get("prev_week") or "the previous week",
        prev_catalysts="; ".join(context.get("catalysts") or ()),
        prev_expectation=context.get("prev_expectation") or "",
        watchlist_deltas="\n".join(context.get("watchlist_deltas") or ()),
        blackswan="\n".join(context.get("blackswan") or ()),
        catalysts="\n".join(context.get("catalysts") or ()),
    )
    return [
        {
            "key": one.key,
            "label": one.label,
            "schema": one.schema,
            "prompt": one.prompt,
            "series": list(one.series),
        }
        for one in cats
    ]


def retry_prompt(
    category_key: str, points: list[dict[str, Any]], *, asof: str
) -> str | None:
    """The Retry-stage prompt for one category, or None when nothing was flagged.

    `gather.workflow.js` skips this stage for the events category, which carries
    no data points to recover, and so does this.
    """
    if category_key == "events" or not points:
        return None
    found = _category(category_key)
    if found is None:
        return None
    return retry(
        found,
        [
            (str(one["series"]), int(one.get("tier") or 0), str(one.get("note") or ""))
            for one in points
        ],
        asof=asof,
    )


def _category(key: str) -> Category | None:
    for one in categories(
        asof="",
        previous={},
        prev_date="",
        prev_catalysts="",
        prev_expectation="",
        watchlist_deltas="",
        blackswan="",
        catalysts="",
    ):
        if one.key == key:
            return one
    return None


def merge_retry(
    first: list[dict[str, Any]], recovered: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Keep a recovered point only when its tier is strictly better.

    `gather.workflow.js`'s merge, unchanged in behaviour: a retry that came back
    at the same tier is not an improvement, and letting it win would swap one
    unverified source for another and call the swap progress.
    """
    by_key = {str(one.get("series") or "").strip(): one for one in recovered}
    out: list[dict[str, Any]] = []
    notes: list[str] = []
    for original in first:
        key = str(original.get("series") or "").strip()
        replacement = by_key.get(key)
        if replacement and int(replacement.get("tier") or 99) < int(original.get("tier") or 99):
            out.append(replacement)
            notes.append(
                f"{key}: recovered from Tier {original.get('tier')} to "
                f"Tier {replacement.get('tier')} on a fallback source"
            )
        else:
            out.append(original)
            if int(original.get("tier") or 0) >= 3:
                notes.append(f"{key}: still Tier {original.get('tier')} after the retry")
    return out, notes


def flagged(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The Tier 3 and Tier 4 points, which are the only ones the retry stage touches."""
    return [one for one in points if int(one.get("tier") or 0) >= 3]


# ---------------------------------------------------------------------------
# The plain rewrite of the prose rows
# ---------------------------------------------------------------------------

#: The field a rewritten point carries the search agent's own sentence in.
#: `display` holds the plain rewrite and is what the dashboard shows; this holds
#: the sentence the agent returned, which the batch archives in its place.
ORIGINAL = "display_original"

# A number as the check compares it: its sign, its digits, its unit and its order
# of magnitude. Thousands commas are dropped, so `$4,150` and `4150` are the same
# figure, but `4.00` and `4` are not: `config/voice.md` says 2.40 stays 2.40. A
# minus counts only where it cannot be a hyphen or a range dash (`12-0`,
# `3.75%-4.00%`, `2026-09-25`), and a plus is no sign at all. `25bp` and `25%`
# differ, as do `$54 billion` and `$54 million`; `$1.8tn` and `$1.8 trillion` agree.
_FIGURE = re.compile(
    r"(?P<sign>(?<![\w.%)\]])[-\u2212+])?"
    r"[$\u20ac\u00a3]?"
    r"(?P<digits>\d+(?:,\d{3})*(?:\.\d+)?)"
    r"(?:\s?(?P<unit>%|percent\b|per\s?cent\b|bps?\b|basis\s+points?\b|pp\b"
    r"|percentage\s+points?\b))?"
    r"(?:\s?(?P<order>k|thousand|mn?|million|bn?|billion|tn?|trillion)\b)?",
    re.IGNORECASE,
)
_UNITS = {"%": "%", "percent": "%", "bp": "bp", "bps": "bp", "pp": "pp"}
_ORDERS = {"thousand": "k", "mn": "m", "million": "m", "bn": "b", "billion": "b",
           "tn": "t", "trillion": "t"}


def _unit(raw: str | None) -> str:
    if not raw:
        return ""
    word = " ".join(raw.lower().split())
    if word.startswith("basis"):
        return "bp"
    if word.startswith("percentage"):
        return "pp"
    if word.startswith("per"):
        return "%"
    return _UNITS.get(word, word)


def figures(text: str) -> Counter[str]:
    """Every number in a sentence, counted: `-140.6m`, `25bp`, `3.75%`, `2026`, `12`."""
    out: Counter[str] = Counter()
    for match in _FIGURE.finditer(text):
        sign = "-" if match["sign"] in ("-", "\u2212") else ""
        order = (match["order"] or "").lower()
        out[
            sign + match["digits"].replace(",", "") + _unit(match["unit"])
            + _ORDERS.get(order, order)
        ] += 1
    return out


def _listed(counted: Counter[str]) -> str:
    return ", ".join(sorted(counted.elements()))


@dataclass(frozen=True, slots=True)
class Plained:
    """One run's returns, split into what is written and what the agents said."""

    #: `category -> points`, each carrying the sentence that goes into `display`.
    points: dict[str, list[dict[str, Any]]]
    #: `category -> points` exactly as the search agent returned them, for the batch.
    raw: dict[str, list[dict[str, Any]]]
    #: `category -> series -> the plain sentence`, for every rewrite that was kept.
    plain: dict[str, dict[str, str]] = field(default_factory=dict)
    checks: list[Check] = field(default_factory=list)


def plain_prose(
    returns: dict[str, list[dict[str, Any]]], *, text_keys: frozenset[str]
) -> Plained:
    """Keep the plain rewrite of a prose row only when it kept every number.

    `SKILL.md` A3b rewrites the `display` of the text series into the voice of
    `config/voice.md` and moves the agent's sentence to `display_original`. The
    rewrite is presentation; the agent's sentence is the evidence. So the batch
    gets the agent's sentence back under `display`, and the rewrite is stored
    beside it rather than over it.

    A rewrite whose numbers differ from the original's is refused and the
    agent's sentence is written instead: one that lost a number, lost a sign or
    a unit or an order of magnitude from one, or added a number the original
    does not have. The numbers are counted, not collected, so two ones are two
    numbers. A dropped rate or date is a factual regression, and a correct
    sentence in trader shorthand is better than a plain one that is wrong.
    """
    points: dict[str, list[dict[str, Any]]] = {}
    raw: dict[str, list[dict[str, Any]]] = {}
    plain: dict[str, dict[str, str]] = {}
    checks: list[Check] = []
    for category, rows in returns.items():
        points[category] = []
        raw[category] = []
        for one in rows:
            original = one.get(ORIGINAL)
            if not isinstance(original, str) or not original.strip():
                points[category].append(one)
                raw[category].append(one)
                continue
            said = {key: value for key, value in one.items() if key != ORIGINAL}
            said["display"] = original
            raw[category].append(said)
            key = str(one.get("series") or "").strip()
            rewritten = str(one.get("display") or "").strip()
            said_figures, plain_figures = figures(original), figures(rewritten)
            lost = said_figures - plain_figures
            added = plain_figures - said_figures
            if key not in text_keys:
                checks.append(
                    Check(
                        "plain_rewrite_out_of_scope",
                        False,
                        f"{key} carries a {ORIGINAL} but is not a text series. Only the prose "
                        "rows are rewritten, so the search agent's sentence was written instead.",
                    )
                )
                points[category].append(said)
            elif not rewritten or lost or added:
                if not rewritten:
                    what = "dropped the whole sentence"
                else:
                    what = " and ".join(
                        part for part in (
                            f"dropped {_listed(lost)}" if lost else "",
                            f"added {_listed(added)}" if added else "",
                        ) if part
                    )
                checks.append(
                    Check(
                        "plain_rewrite_lost_a_number" if lost or not rewritten
                        else "plain_rewrite_added_a_number",
                        False,
                        f"{key}: the plain rewrite {what}. The search agent's sentence was "
                        f"written instead: {original!r}. Put every number back exactly as the "
                        "original writes it, sign, unit and all, add none, and run `narrative "
                        "write` again to land the plain one.",
                    )
                )
                points[category].append(said)
            else:
                points[category].append({**said, "display": rewritten})
                plain.setdefault(category, {})[key] = rewritten
    return Plained(points=points, raw=raw, plain=plain, checks=checks)


def prose_voice(
    returns: dict[str, list[dict[str, Any]]], *, text_keys: frozenset[str]
) -> list[Check]:
    """`Voice:` warnings on the sentence each prose row will show. Never a refusal."""
    fields = [
        voice.Field(f"{key}_display", f"the {key} row", str(one.get("display") or ""))
        for rows in returns.values()
        for one in rows
        if (key := str(one.get("series") or "").strip()) in text_keys
        and str(one.get("display") or "").strip()
    ]
    if not fields:
        return []
    return [one for one in voice.checks(fields, scope="Policy state rows") if not one.ok]


async def apply(
    store: Store,
    settings: Settings,
    *,
    returns: dict[str, list[dict[str, Any]]],
    event_rows: list[dict[str, Any]],
    asof: date,
    now: datetime,
) -> dict[str, Any]:
    """Write what the search agents returned, and say what was refused.

    `returns` is `category key -> points`, after the retry merge and the plain
    rewrite. `event_rows` is the events agent's return.
    """
    keys = frozenset(agent_series(settings))
    kinds = await store.series_kinds()
    ids = await store.series_ids()
    text_keys = frozenset(key for key in keys if kinds.get(key, ("",))[0] == "text")

    plained = plain_prose(returns, text_keys=text_keys)
    observations: list[NarrativeObservation] = []
    checks: list[Check] = list(plained.checks)
    for category, points in plained.points.items():
        found, category_checks = narrative_points(
            points,
            kinds=kinds,
            allowed=keys,
            observed_at=now,
            source_prefix=f"search agent ({category})",
        )
        observations.extend(found)
        checks.extend(category_checks)

    decoded, event_checks = decode_events(event_rows)
    checks.extend(event_checks)

    run_id = await store.start_run("agent_0930", now)
    try:
        await store.write_search_batches(
            [
                (
                    "agent_0930:" + key,
                    now,
                    {"points": value}
                    | ({"plain": plained.plain[key]} if key in plained.plain else {}),
                )
                for key, value in plained.raw.items()
            ]
            + [("agent_0930:events", now, {"events": event_rows})]
        )
        written = await store.write_observations(observations, ids, received_at=now)
        events_written = await store.write_events(decoded)

        # Every series on this path is replayable: false, so one this run did
        # not land is a reading that is gone rather than one a later run
        # recovers. A Tier 4 point is the same fact said out loud.
        #
        # Landing is decided on the tier, not on the value. The five prose rows
        # carry `value` null by contract and their whole content is the
        # sentence, so a value test would report every one of them missing on a
        # run that fetched all five perfectly.
        landed = {one.series_key for one in observations if one.tier < 4}
        missing = sorted(keys - landed)
        await store.write_gaps(
            [
                (
                    key,
                    asof,
                    "the 09:30 search agent returned no usable reading; this source serves only "
                    "the present moment, so the session cannot be recovered",
                    True,
                )
                for key in missing
            ]
        )
        await store.write_findings(
            [
                ("narrative_point_refused", one.key, one.sentence, 2)
                for one in checks
                if not one.ok
            ]
            + [
                (
                    "narrative_series_missing",
                    key,
                    f"{key} carries no reading for {asof.isoformat()} after the retry stage. The "
                    "source is point-in-time, so the gap is permanent and every condition over "
                    "the series reports unevaluable rather than standing on an older number.",
                    2,
                )
                for key in missing
            ],
            now=now,
        )
        await store.finish_run(
            run_id,
            finished_at=datetime.now(now.tzinfo),
            rows=written + events_written,
            status="ok",
            error=None,
        )
    except Exception as exc:
        await store.finish_run(
            run_id,
            finished_at=datetime.now(now.tzinfo),
            rows=0,
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
        )
        raise

    return {
        "observations_written": written,
        "events_written": events_written,
        "series_missing": missing,
        "refusals": [one.sentence for one in checks if not one.ok],
        "tier_4": sorted(one.series_key for one in observations if one.tier >= 4),
        # Warn-only, and not findings: advice to the run that wrote the prose.
        "voice": [one.sentence for one in prose_voice(plained.points, text_keys=text_keys)],
    }


def summarise(observations: list[NarrativeObservation], decoded: list[Event]) -> str:
    """One line per series, for the run log and for the operator's read."""
    lines = [
        f"{one.series_key:<32} tier {one.tier}  {one.asof}  "
        f"{'—' if one.value is None else one.value}  {one.display[:60]}"
        for one in sorted(observations, key=lambda x: x.series_key)
    ]
    lines.append(f"{len(decoded)} events")
    return "\n".join(lines)
