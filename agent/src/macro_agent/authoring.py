"""Mode B: writing the week's judgment, as `proposed`, and stopping.

Two writes, in a fixed order, because the order is what makes the phase fresh.

**`classify` writes the phase reads first**, before the run has seen last
week's call. `docs/DECISIONS.md` 2026-09-23: every week the phase is
classified from the data and the frameworks alone, and any phase may follow
any phase. The command fills in last week's phase itself and does not print
it. `author context` is the reveal, stamped on the rows as `revealed_at`; from
then on the read is fixed, and the only thing the run adds is one sentence on
what changed.

**`propose` writes the rest of the week**: the scenario set, the strategies and
the setups, and the change notes on the reads. One transaction per subject,
because the cardinality rules are per subject: a scenario set is three
scenarios summing to 100 and a phase call is exactly four conditions, and
neither says anything about the other. Each subject is written whole inside its
own transaction, so a refused set leaves nothing half-built behind it.

Nothing here activates anything. `docs/DECISIONS.md` puts the single human
review gate exactly at the move from `proposed` to `active`, and a run that
stood up its own proposal would be a system with no review in it. `Store.activate`
exists, is tested, and is the operator's command.

## The amendment path

A material change permits a re-price: a trigger firing in full, a phase
condition flipping, a tail risk going acute, or a premise moving. Ordinary daily
noise does not. An amendment never carries a phase: the phase is read fresh
once a week, and a read made mid-week after the run has seen the standing call
would not be fresh. An amendment is not an edit: it is a new row carrying `amends`
and `amend_reason`, written `proposed` like any other, and the row it amends
keeps standing until the operator accepts the replacement. Brier can then be
scored against the first statement or the amended one, because both are rows.

`amend` is deliberately the same code path as `propose`. An amendment that took
a shortcut would be the one write in the system that skipped validation, and it
is the write most likely to be made in a hurry.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Any

from . import voice
from .config import Settings
from .model import Check, PhaseCallProposal, Proposal, Refused
from .preflight import Tape, phase_read, walk
from .store import Store
from .validate import check as validate_check
from .validate import read_checks, refusals

LOG = logging.getLogger(__name__)

#: What may permit a re-price, per `docs/DECISIONS.md`. An `amend_reason` that
#: names none of these is surfaced, not refused: the list is the four causes
#: recorded so far and the operator may have a fifth, but they have to see that
#: the row they are about to accept does not name one of the four.
MATERIAL_CHANGES = (
    "a trigger firing in full",
    "a phase condition flipping",
    "a tail risk going acute",
    "a scenario's premise moving",
)


@dataclass(frozen=True, slots=True)
class Written:
    """What one authoring pass put on the table, all of it `proposed`."""

    week: str
    scenario_set_id: str
    phase_call_ids: dict[str, str]
    strategy_ids: dict[str, str]
    setup_ids: dict[str, str]
    checks: list[Check]
    preflight: list[Check]
    #: Rows of an earlier, never-accepted proposal for the same week that this
    #: one stood down, so an Accept sees one proposal and not two.
    replaced: int = 0

    @property
    def refused(self) -> list[Check]:
        return refusals(self.checks)


#: How the authoring run names a catalyst it has not got the id of. The run is
#: handed `catalyst_ids` in its context and may substitute directly, but naming
#: is what it actually does, so the name is resolved here.
CATALYST_PREFIX = "CATALYST:"


def resolve_catalysts(found: Proposal, by_name: dict[str, str]) -> tuple[Proposal, list[Check]]:
    """Turn `CATALYST:<name>` references into real ids, and refuse a name nobody tracks.

    Binding a clause to a catalyst is what makes premise-moved detection work.
    A clause that names a dated event and carries no `catalyst_id` is a clause
    the Catalyst Monitor can never void, so the scenario keeps standing on an
    event that has been retired and nothing in the system says so.

    A reference the Monitor does not carry is refused rather than dropped.
    Dropping it would produce exactly the unbindable clause the reference was
    written to avoid, and silently.
    """
    checks: list[Check] = []
    lowered = {name.strip().lower(): value for name, value in by_name.items()}
    scenarios = []
    for scenario in found.scenario_set.scenarios:
        clauses = []
        for clause in scenario.clauses:
            reference = clause.catalyst_id
            if reference and reference.startswith(CATALYST_PREFIX):
                wanted = reference[len(CATALYST_PREFIX) :].strip()
                resolved = by_name.get(wanted) or lowered.get(wanted.lower())
                if resolved is None:
                    checks.append(
                        Check(
                            "catalyst_not_tracked",
                            False,
                            f"S{scenario.ordinal} clause {clause.ordinal} names the catalyst "
                            f"{wanted!r}, which the Catalyst Monitor does not carry. A clause "
                            "bound to nothing can never be voided, so the scenario would keep "
                            "standing on an event that had been retired and nothing would say so.",
                        )
                    )
                clause = replace(clause, catalyst_id=resolved)
            clauses.append(clause)
        scenarios.append(replace(scenario, clauses=tuple(clauses)))
    return (
        replace(found, scenario_set=replace(found.scenario_set, scenarios=tuple(scenarios))),
        checks,
    )


async def propose(
    store: Store,
    settings: Settings,
    found: Proposal,
    *,
    asof: date,
    tape: Tape,
    now: datetime,
) -> Written:
    """Validate a whole week's judgment, then write every part of it as `proposed`.

    Validation runs first and over the whole proposal, not per subject. A
    scenario set whose probabilities sum to 97 and whose setups are perfect is
    one refusal, and writing the setups first would leave the operator four rows
    to reject by hand.
    """
    found, bound = resolve_catalysts(found, await store.catalysts_by_name())
    checks = validate_check(found, settings, asof=asof) + bound
    refused = refusals(checks)
    if refused:
        raise Refused(
            f"{len(refused)} check(s) refused this proposal, so nothing was written:\n"
            + "\n".join(f"  - {one.sentence}" for one in refused)
        )

    # R1 to R12, then the phase read, which is not one of the twelve, then the
    # voice lines, which are advice to the writer and never refuse.
    preflight = walk(found, tape) + phase_read(found, tape) + voice.proposal_checks(found)

    # A second `propose` for the same week replaces the first rather than
    # standing beside it. Two proposed sets for one week cannot both be
    # accepted, and the operator would be reviewing whichever the Desk showed.
    amending = bool(
        found.scenario_set.amends
        or any(one.amends for one in found.strategies)
        or any(one.amends for one in found.setups)
    )
    replaced = 0 if amending else await store.replace_unaccepted_week(found.week)

    set_id = await store.write_scenario_set(found.scenario_set)
    await _assert_manual_clauses(store, found, set_id, now=now)

    # The reads were written by `classify` before the reveal. All the proposal
    # adds to them is the change note, and only to a read still proposed.
    phase_ids: dict[str, str] = {}
    for call in found.phase_calls:
        if call.call_id is None:
            continue
        phase_ids[call.asset] = call.call_id
        if call.change_note:
            await store.set_change_note(call.call_id, call.change_note)

    strategy_ids: dict[str, str] = {}
    bands_by_asset: dict[str, dict[str, str]] = {}
    for strategy in found.strategies:
        strategy_id, bands = await store.write_strategy(strategy)
        strategy_ids[strategy.asset] = strategy_id
        bands_by_asset[strategy.asset] = bands

    setup_ids: dict[str, str] = {}
    for setup in found.setups:
        focus = bands_by_asset.get(setup.asset, {}).get(setup.band or "")
        setup_ids[f"{setup.asset}:{setup.name}"] = await store.write_setup(
            setup, sector_focus_id=focus
        )

    # Every check that did not refuse and did not pass lands as a
    # `macro_quality` finding, not merely a line in a report the operator may
    # close: a large phase jump, a read whose conditions fail on its own day.
    # Each carries a stable `check_key` and is rendered verbatim by the web app.
    # The voice lines are not findings: they are for the run that wrote the
    # text, which reads them in `preflight` before proposing.
    await store.write_findings(
        [
            (one.key, f"{found.week} {one.key}", one.sentence, 2 if one.severity == "warn" else 3)
            for one in checks + preflight
            if not one.ok and not voice.is_voice(one)
        ],
        now=now,
    )

    return Written(
        week=found.week,
        scenario_set_id=set_id,
        phase_call_ids=phase_ids,
        strategy_ids=strategy_ids,
        setup_ids=setup_ids,
        checks=checks,
        preflight=preflight,
        replaced=replaced,
    )


@dataclass(frozen=True, slots=True)
class Classified:
    """What `classify` wrote. Last week's phase is on the rows, and not shown yet."""

    week: str
    phase_call_ids: dict[str, str]
    reads: tuple[PhaseCallProposal, ...]
    checks: list[Check]
    replaced: int
    #: Warn-only lines on how the reads' prose reads. Not written as findings.
    voice: list[Check]


async def classify(
    store: Store,
    settings: Settings,
    reads: tuple[PhaseCallProposal, ...],
    *,
    week: str,
    asof: date,
    now: datetime,
) -> Classified:
    """Write the week's fresh reads as `proposed`, then fill in last week's phase.

    Refused as one unit when a check refuses. Refused as well when a read for
    this week already exists that the run has been shown the prior for: a read
    rewritten after the reveal is not fresh. The runner clears a failed run's
    reads when it records the failure, and a proposal awaiting review is
    cleared by rejecting it on the Desk. A read written and not yet revealed
    may be replaced, so a typo found before the reveal costs nothing.
    """
    checks = read_checks(reads, settings, asof=asof)
    refused = refusals(checks)
    if refused:
        raise Refused(
            f"{len(refused)} check(s) refused these reads, so nothing was written:\n"
            + "\n".join(f"  - {one.sentence}" for one in refused)
        )
    existing = await store.fresh_reads(week)
    settled = [one for one in existing if one["status"] == "active"]
    if settled:
        raise Refused(
            f"{week} already has an accepted fresh read for "
            f"{', '.join(sorted(one['asset'] for one in settled))}. The phase is read once a "
            "week, and this week's read is on the Desk."
        )
    revealed = [one for one in existing if one["revealed_at"] is not None]
    if revealed:
        raise Refused(
            f"{week}'s reads for {', '.join(sorted(one['asset'] for one in revealed))} were "
            "already shown last week's call, so a read written now would not be fresh, and the "
            "reads already written stand. Stop here and report it. If an earlier run's proposal "
            "for this week is awaiting review, the operator can reject it on the Desk, which "
            "clears its reads; a run that failed has its reads cleared by the runner when it "
            "records the failure."
        )
    replaced = await store.discard_unrevealed_reads(week)

    prior = {one["asset"]: one for one in await store.active_phase_calls()}
    written: list[PhaseCallProposal] = []
    ids: dict[str, str] = {}
    for read in reads:
        before = prior.get(read.asset)
        stamped = replace(
            read,
            prior_phase=None if before is None else before["phase"],
            prior_week=None if before is None else before["week"],
            classified_at=now,
            authored_at=now,
        )
        call_id = await store.write_phase_call(stamped)
        await _assert_manual_conditions(store, stamped, call_id, now=now)
        ids[read.asset] = call_id
        written.append(replace(stamped, call_id=call_id))

    await store.write_findings(
        [
            (one.key, f"{week} {one.key}", one.sentence, 2 if one.severity == "warn" else 3)
            for one in checks
            if not one.ok
        ],
        now=now,
    )
    return Classified(
        week=week,
        phase_call_ids=ids,
        reads=tuple(written),
        checks=checks,
        replaced=replaced,
        voice=voice.read_checks(reads),
    )


async def _assert_manual_conditions(
    store: Store, call: Any, call_id: str, *, now: datetime
) -> None:
    """Record the as-written status of each `manual` phase condition.

    A manual condition can never be computed, so without this it reports
    `unevaluable` for the whole week. The status lives in `macro_core` and not
    in `macro_mart` because `macro_mart` is rebuilt on every run and a judgment
    a rebuild destroys is not a record.
    """
    by_ordinal = await store.phase_condition_ids(call_id)
    for condition in call.conditions:
        if condition.kind != "manual" or not condition.asserted_status:
            continue
        await store.assert_condition_state(
            by_ordinal[condition.ordinal],
            owner_kind="phase",
            status=condition.asserted_status,
            current_text=condition.asserted_text,
            asserted_at=now,
        )


async def _assert_manual_clauses(
    store: Store, found: Proposal, set_id: str, *, now: datetime
) -> None:
    by_pair = await store.trigger_clause_ids(set_id)
    for scenario in found.scenario_set.scenarios:
        for clause in scenario.clauses:
            if clause.kind != "manual" or not clause.asserted_status:
                continue
            await store.assert_condition_state(
                by_pair[(scenario.ordinal, clause.ordinal)],
                owner_kind="trigger",
                status=clause.asserted_status,
                current_text=clause.asserted_text,
                asserted_at=now,
            )


def amendment_checks(found: Proposal) -> list[Check]:
    """Whether each amendment names a change that permits a re-price.

    Reported, never refused. `docs/DECISIONS.md` names four material changes and
    a fifth is possible; what is not possible is an amendment whose reason the
    operator cannot read, and that is what this surfaces.
    """
    out: list[Check] = []
    pairs: list[tuple[str, str | None, str | None]] = [
        ("the scenario set", found.scenario_set.amends, found.scenario_set.amend_reason)
    ]
    pairs += [(f"setup {one.name!r}", one.amends, one.amend_reason) for one in found.setups]
    pairs += [
        (f"the {one.asset} strategy", one.amends, one.amend_reason) for one in found.strategies
    ]
    for label, amends, reason in pairs:
        if not amends:
            continue
        text = (reason or "").lower()
        named = any(
            word in text
            for word in ("trigger", "condition", "tail risk", "acute", "premise", "catalyst")
        )
        out.append(
            Check(
                "amendment_names_a_material_change",
                named,
                f"{label} amends an earlier row. "
                + (
                    f"Its reason reads: {reason}"
                    if named
                    else f"Its reason is {reason!r}, which names none of the four material "
                    "changes a re-price is permitted on: "
                    + ", ".join(MATERIAL_CHANGES)
                    + ". Ordinary daily noise does not permit a re-price; nudging a probability "
                    "on noise manufactures exactly the over-anchoring R2, R4, R5, R6, R10, R11 "
                    "and R12 were written to stop."
                ),
                severity="warn",
            )
        )
    return out
