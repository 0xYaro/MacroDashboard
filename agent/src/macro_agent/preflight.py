"""R1 to R12, walked against a proposal before the operator sees it.

`scenario-rules.md` says how these are applied: *walk R1 to R12 against the
draft scenario table; write one line under the table only when a rule was
consciously waived, naming the rule.* That walk was a reading exercise. Here it
is a function, and `docs/DECISIONS.md` makes it the pre-flight the app runs and
displays before the operator accepts.

## What a mechanised rule can and cannot be

Each rule below is mechanised to the strongest check the stored shape supports,
and every check states in its own sentence what it actually looked at. That
matters more than the verdict: R2 says the set must span both directions of the
dominant swing variable, and what this file can check is that the three S&P
bands are not all on one side of today's close. A set that passes that is not
thereby two-sided in the sense R2 means, and the sentence says so.

Nothing here narrows a rule to fit what is easy. Where a rule is not fully
checkable the check is raised as `warn` or `note` rather than quietly weakened
into something it is not, and the untested half is named.

## Waiving

`macro_core.scenario_set.rules_waived` carries the rule numbers the authoring
run consciously waived. A waived rule that fails is reported as a `note` naming
the waiver, never suppressed: the whole value of the ledger is that a waiver
which turns out badly is findable afterwards.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from .model import Check, Proposal, ScenarioProposal, ScenarioSetProposal

#: Series a confirming-regime clause may be written on, per R4.
CONFIRMING = frozenset({"vix", "move", "hy_oas_bps", "ig_oas_bps", "breadth_spx_above_200dma"})
#: The dominant equity series every scenario is expected to band.
INDEX = "spx_cash"
#: The oil series R3 and R11 are about.
OIL = "brent"
#: The two crypto series R7 and R12 are about.
CRYPTO = frozenset({"btc", "eth", "sol"})
#: The flow series R10 is about.
FLOW = frozenset({"btc_etf_weekly_flow", "eth_etf_weekly_flow"})
STREAK = frozenset({"btc_etf_inflow_streak", "eth_etf_inflow_streak"})

LEADERSHIP_WORDS = ("leadership", "equal-weight", "equal weight", "breadth of leadership")


@dataclass(frozen=True, slots=True)
class Holding:
    """How a read's four conditions stand on the evaluation date, three ways.

    Unevaluable is its own count and never folded into failed: a condition
    over a series with no reading that day says nothing about the read, and
    counting it as not holding turns a missing observation into a warning
    about the phase.
    """

    met: int
    failed: int
    unevaluable: int
    asof: date | None = None

    @property
    def total(self) -> int:
        return self.met + self.failed + self.unevaluable


@dataclass(frozen=True, slots=True)
class Tape:
    """The few readings the preflight needs, so it does not go to the database itself.

    `brent_8w_lo` and `brent_8w_hi` are the last eight weeks of Brent, which is
    what R3 says to size an oil band from. They are passed in rather than
    derived here because a pure function that takes its numbers is testable
    against a hand-computed week, and the eight-week window is the rule's, not
    this file's.
    """

    index_close: Decimal | None = None
    brent_8w_lo: Decimal | None = None
    brent_8w_hi: Decimal | None = None
    btc_streak: Decimal | None = None
    eth_streak: Decimal | None = None
    #: `asset -> how the read's four conditions stand on the evaluation date`,
    #: or the asset absent when no history was available to score them
    #: against. A read whose own conditions are already failing on the day it
    #: is written is resting on something that is not true.
    holding: Mapping[str, Holding] = field(default_factory=dict)


def walk(found: Proposal, tape: Tape) -> list[Check]:
    """R1 to R12 against one proposal, in order."""
    scenario_set = found.scenario_set
    waived = set(scenario_set.rules_waived)
    checks = [
        _r1(found),
        _r2(scenario_set, tape),
        _r3(scenario_set, tape),
        _r4(scenario_set, tape),
        _r5(scenario_set),
        _r6(scenario_set),
        _r7(scenario_set),
        _r8(found),
        _r9(scenario_set),
        _r10(scenario_set, tape),
        _r11(scenario_set, tape),
        _r12(scenario_set),
    ]
    return [_apply_waiver(one, waived) for one in checks]


def _apply_waiver(check: Check, waived: set[int]) -> Check:
    number = _rule_number(check.key)
    if check.ok or number is None or number not in waived:
        return check
    return Check(
        key=check.key,
        ok=check.ok,
        sentence=(
            f"{check.sentence} R{number} is listed in rules_waived, so this is recorded rather "
            "than refused. The ledger keeps the waiver so a rule waived badly is findable."
        ),
        severity="note",
    )


def _rule_number(key: str) -> int | None:
    head = key.split("_", 1)[0]
    return int(head[1:]) if head.startswith("r") and head[1:].isdigit() else None


# ---------------------------------------------------------------------------
# Helpers over the authored shape
# ---------------------------------------------------------------------------


def _bands(scenario: ScenarioProposal, series_key: str) -> list[Any]:
    return [
        one
        for one in scenario.targets
        if one.kind == "band" and one.series_key == series_key
    ]


Span = tuple[Decimal | None, Decimal | None]


def _band_span(scenario: ScenarioProposal, series_key: str) -> Span:
    """The low and high of a scenario's band on one series, whatever its comparator."""
    found = _bands(scenario, series_key)
    if not found:
        return None, None
    lows = [one.lo for one in found if one.lo is not None]
    highs = [one.hi for one in found if one.hi is not None]
    return (min(lows) if lows else None, max(highs) if highs else None)


def _series_named(expression: Any, found: set[str]) -> None:
    if isinstance(expression, dict):
        if "series" in expression and isinstance(expression["series"], str):
            found.add(expression["series"])
        for value in expression.values():
            _series_named(value, found)
        args = expression.get("args")
        if isinstance(args, dict):
            _series_named(args, found)
    elif isinstance(expression, list):
        for one in expression:
            _series_named(one, found)


def clause_series(scenario: ScenarioProposal) -> set[str]:
    found: set[str] = set()
    for clause in scenario.clauses:
        _series_named(clause.expr, found)
    return found


def risk_off(scenario_set: ScenarioSetProposal) -> ScenarioProposal | None:
    """The branch whose index band sits lowest. R4 and R11 are about that one."""
    banded = [
        (one, _band_span(one, INDEX)[0])
        for one in scenario_set.scenarios
        if _band_span(one, INDEX)[0] is not None
    ]
    if not banded:
        return None
    return min(banded, key=lambda pair: pair[1])[0]  # type: ignore[arg-type,return-value]


def _has_leadership_target(scenario: ScenarioProposal) -> bool:
    return any(
        any(word in (one.label or "").lower() for word in LEADERSHIP_WORDS)
        or any(word in (one.text or "").lower() for word in LEADERSHIP_WORDS)
        for one in scenario.targets
    )


# ---------------------------------------------------------------------------
# The twelve
# ---------------------------------------------------------------------------


def _and(parts: list[str]) -> str:
    """`a`, `a and b`, `a, b and c`. A sentence the operator reads has to read."""
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def _r1(found: Proposal) -> Check:
    """Compute a futures-quoted index's weekly change from the cash index.

    What is checkable here: no setup and no target may be written against a
    front-month future while a cash series for the same index exists. The
    arithmetic half of R1 lives in the worker's `delta_week_open`; the authoring
    half is which series a level names, and that is this.
    """
    futures = {"spx_front": "spx_cash", "dow_front": "dow_cash", "rut_front": "rut_cash"}
    offenders = [one.name for one in found.setups if one.series_key in futures]
    offenders += [
        f"S{s.ordinal} {t.label}"
        for s in found.scenario_set.scenarios
        for t in s.targets
        if t.series_key in futures
    ]
    return Check(
        "r1_levels_are_cash_levels",
        not offenders,
        "R1: "
        + (
            "every level is quoted on a cash index."
            if not offenders
            else f"{', '.join(offenders)} are quoted on a front-month future while the cash series "
            "exists. Contract rolls contaminate the week-over-week arithmetic, which is why R1 was "
            "written after W27."
        ),
    )


def _r2(scenario_set: ScenarioSetProposal, tape: Tape) -> Check:
    """The set spans both directions of the dominant swing variable.

    Checked as: the three index bands are not all on one side of the last close.
    That is weaker than R2, which is about the swing *variable* and not the
    index, and the sentence says so.
    """
    if tape.index_close is None:
        return Check(
            "r2_two_sided",
            True,
            "R2: not checked. No close for `spx_cash` was available, so which side of today each "
            "band sits on cannot be decided. The rule still applies and was not tested.",
            severity="note",
        )
    spans = [_band_span(one, INDEX) for one in scenario_set.scenarios]
    above = any(lo is not None and lo > tape.index_close for lo, _ in spans)
    below = any(hi is not None and hi < tape.index_close for _, hi in spans)
    return Check(
        "r2_two_sided",
        above and below,
        "R2: "
        + (
            f"at least one index band sits above {tape.index_close} and at least one below it."
            if above and below
            else f"every index band sits on one side of {tape.index_close}. Two-sided is a "
            "requirement, not a stylistic preference. Note that this check reads the index band "
            "and not the dominant swing variable itself, so passing it is necessary and not "
            "sufficient."
        ),
    )


def _r3(scenario_set: ScenarioSetProposal, tape: Tape) -> Check:
    """Oil bands wide enough to survive a chokepoint headline in either direction.

    Checked as: the union of the three Brent bands covers the last eight weeks
    of Brent. R3 says to size the band from that range, and the weakest thing
    that means is that the range is inside the set somewhere. It does not check
    that any single band is wide enough, because R3 states no width.
    """
    if tape.brent_8w_lo is None or tape.brent_8w_hi is None:
        return Check(
            "r3_oil_bands_sized_from_the_range",
            True,
            "R3: not checked. The last eight weeks of Brent were not available, so no range could "
            "be compared against. The rule still applies and was not tested.",
            severity="note",
        )
    lows = [lo for lo, _ in (_band_span(one, OIL) for one in scenario_set.scenarios) if lo]
    highs = [hi for _, hi in (_band_span(one, OIL) for one in scenario_set.scenarios) if hi]
    if not lows or not highs:
        return Check(
            "r3_oil_bands_sized_from_the_range",
            False,
            "R3: no scenario carries a Brent band. Oil broke a band that every branch shared in "
            "W26, W31, W34 and W36.",
        )
    covered = min(lows) <= tape.brent_8w_lo and max(highs) >= tape.brent_8w_hi
    return Check(
        "r3_oil_bands_sized_from_the_range",
        covered,
        f"R3: the three Brent bands together span {min(lows)}–{max(highs)} against a trailing "
        f"eight-week range of {tape.brent_8w_lo}–{tape.brent_8w_hi}"
        + (
            "."
            if covered
            else ", so a move no larger than the last eight weeks already delivered would land "
            "outside every branch. R3 says to size the band from that range, not from the "
            "current narrative."
        ),
        severity="warn",
    )


def _r4(scenario_set: ScenarioSetProposal, tape: Tape) -> Check:
    """Every risk-off trigger needs a confirming-regime clause.

    Checked as: the lowest-index-band scenario carries at least one typed clause
    naming VIX, MOVE, HY, IG or breadth. A price level alone is not a trigger,
    and 7,450 firing at VIX 18.6 and breadth 65.8% in W30 is why.
    """
    del tape
    scenario = risk_off(scenario_set)
    if scenario is None:
        return Check(
            "r4_risk_off_has_a_confirming_leg",
            False,
            "R4: no scenario carries an index band, so the risk-off branch cannot be identified "
            "and its confirming leg cannot be checked.",
        )
    named = clause_series(scenario) & CONFIRMING
    return Check(
        "r4_risk_off_has_a_confirming_leg",
        bool(named),
        f"R4: S{scenario.ordinal} is the risk-off branch and "
        + (
            f"its trigger names {', '.join(sorted(named))} as a confirming regime."
            if named
            else "not one of its trigger clauses names VIX, MOVE, HY, IG or breadth as a typed "
            "expression. A price level alone is not a trigger; the oil leg fired inside the tail "
            "for six weeks running while nothing confirmed it."
        ),
    )


def _r5(scenario_set: ScenarioSetProposal) -> Check:
    """Leadership gets its own two-sided axis, separate from the macro trigger."""
    missing = [
        f"S{one.ordinal}" for one in scenario_set.scenarios if not _has_leadership_target(one)
    ]
    return Check(
        "r5_leadership_has_its_own_axis",
        not missing,
        "R5: "
        + (
            "every branch carries a leadership target."
            if not missing
            else f"{', '.join(missing)} carries no leadership target. A correct macro branch and a "
            "correct tape have landed in different scenarios three times, and a directional-only "
            "table cannot express the relative trade."
        ),
    )


def _r6(scenario_set: ScenarioSetProposal) -> Check:
    """Reserve explicit probability for a dateless structural shock.

    Checked as: at least one clause somewhere is not bound to a catalyst. A
    clause bound to a dated catalyst is by construction not the dateless shock
    R6 reserves room for.
    """
    dateless = [
        (one.ordinal, clause.ordinal)
        for one in scenario_set.scenarios
        for clause in one.clauses
        if clause.catalyst_id is None
    ]
    return Check(
        "r6_dateless_shock_is_priced",
        bool(dateless),
        "R6: "
        + (
            f"{len(dateless)} clauses are not bound to a dated catalyst, so the set has room for a "
            "shock no calendar carried."
            if dateless
            else "every clause in the set is bound to a dated catalyst, so nothing in it can fire "
            "on an event no calendar carries. Two of the last ten weeks were driven by exactly "
            "that."
        ),
        severity="warn",
    )


def _r7(scenario_set: ScenarioSetProposal) -> Check:
    """Equities and crypto get separate target rows, not a decoupling footnote."""
    missing = []
    for one in scenario_set.scenarios:
        keys = {t.series_key for t in one.targets if t.series_key}
        if not (keys & CRYPTO) or INDEX not in keys:
            missing.append(f"S{one.ordinal}")
    return Check(
        "r7_the_two_books_get_their_own_rows",
        not missing,
        "R7: "
        + (
            "every branch carries both an equity band and a crypto band."
            if not missing
            else f"{', '.join(missing)} does not carry both an equity and a crypto band. When the "
            "two books sit on opposite sides of the swing variable, a decoupling footnote is not "
            "a target row."
        ),
    )


def _r8(found: Proposal) -> Check:
    """When a phase falsifier is a conjunction, price the case where both arrive at once."""
    conjunctions = [
        one
        for one in found.phase_calls
        if " and " in one.falsifier.lower() or " alongside " in one.falsifier.lower()
    ]
    if not conjunctions:
        return Check(
            "r8_conjunction_falsifier_is_priced",
            True,
            "R8: no phase falsifier is written as a conjunction, so there is no both-at-once case "
            "to price.",
        )
    keys = {
        t.series_key
        for s in found.scenario_set.scenarios
        for t in s.targets
        if t.series_key
    }
    priced = bool(keys & CRYPTO) and bool(keys & FLOW)
    return Check(
        "r8_conjunction_falsifier_is_priced",
        priced,
        "R8: "
        + _and([f"the {one.asset} falsifier" for one in conjunctions])
        + (" reads" if len(conjunctions) == 1 else " read")
        + (
            " as a conjunction, and the set carries both a price band and a flow band, so the "
            "week where both arrive is priced."
            if priced
            else " as a conjunction (reclaim AND flow) and the set does not carry both a "
            "price band and a flow band. A Lull call with no mechanism for a +20% week is not a "
            "call."
        ),
        severity="warn",
    )


def _r9(scenario_set: ScenarioSetProposal) -> Check:
    """Do not couple an index direction to a specific leadership regime continuing.

    Checked as: no trigger clause is written about leadership. Leadership
    belongs in the target rows, where R5 puts it, because it flips faster than a
    phase call and a trigger gated on it gates the whole branch.
    """
    offenders = [
        f"S{one.ordinal} clause {clause.ordinal}"
        for one in scenario_set.scenarios
        for clause in one.clauses
        if any(word in clause.text.lower() for word in LEADERSHIP_WORDS)
    ]
    return Check(
        "r9_leadership_is_not_in_the_trigger",
        not offenders,
        "R9: "
        + (
            "no trigger clause is gated on a leadership regime continuing."
            if not offenders
            else f"{', '.join(offenders)} gates a branch on leadership. Leadership flips faster "
            "than phase calls, so it belongs in the target rows per R5 and not in the trigger."
        ),
    )


def _r10(scenario_set: ScenarioSetProposal, tape: Tape) -> Check:
    """Do not extrapolate a three-week flow streak into a regime."""
    streaks = {"btc": tape.btc_streak, "eth": tape.eth_streak}
    live = {name: value for name, value in streaks.items() if value is not None and abs(value) >= 3}
    if not live:
        return Check(
            "r10_no_streak_extrapolated",
            True,
            "R10: no ETF streak of three sessions or more is running, so there is nothing to "
            "extrapolate.",
            severity="warn",
        )
    base = max(scenario_set.scenarios, key=lambda one: (one.probability, -one.ordinal))
    offenders = []
    for name, value in live.items():
        key = f"{name}_etf_weekly_flow"
        lo, hi = _band_span(base, key)
        if value > 0 and lo is not None and lo >= 0:
            offenders.append(f"{key} banded above zero into a +{value} session inflow streak")
        if value < 0 and hi is not None and hi <= 0:
            offenders.append(f"{key} banded below zero into a {value} session outflow streak")
    return Check(
        "r10_no_streak_extrapolated",
        not offenders,
        "R10: "
        + (
            f"the base case S{base.ordinal} does not price a running streak straight through."
            if not offenders
            else f"the base case S{base.ordinal} has {'; '.join(offenders)}. Streaks inflect "
            "exactly when they look most established."
        ),
        severity="warn",
    )


def _r11(scenario_set: ScenarioSetProposal, tape: Tape) -> Check:
    """Oil gets its own two-sided axis, separate from the risk-off tail."""
    del tape
    spans = {one.ordinal: _band_span(one, OIL) for one in scenario_set.scenarios}
    banded = [ordinal for ordinal, span in spans.items() if span != (None, None)]
    distinct = len({span for span in spans.values() if span != (None, None)})
    ok = len(banded) == len(scenario_set.scenarios) and distinct > 1
    return Check(
        "r11_oil_has_its_own_axis",
        ok,
        "R11: "
        + (
            f"all {len(banded)} branches carry a Brent band and {distinct} of them differ, so oil "
            "is an axis rather than a leg of the tail."
            if ok
            else f"{len(banded)} of {len(scenario_set.scenarios)} branches carry a Brent band and "
            f"{distinct} distinct band(s) were written. The oil trigger fired inside the tail six "
            "weeks running while that tail's equity, credit and vol legs never fired."
        ),
    )


def _r12(scenario_set: ScenarioSetProposal) -> Check:
    """Name the dominant driver per asset and say which channel its band is priced off.

    Checked as: every scenario states its positioning, and the set's swing
    variables name a crypto driver as well as an equity one. That is the
    structural half. Whether the channel named is the one that moved the asset
    last is judgment and is not checked here.
    """
    silent = [f"S{one.ordinal}" for one in scenario_set.scenarios if not one.positioning]
    text = " ".join(str(one) for one in scenario_set.swing_variables).lower()
    crypto_named = any(word in text for word in ("btc", "bitcoin", "eth", "crypto", "sec", "etf"))
    ok = not silent and crypto_named
    return Check(
        "r12_driver_named_per_asset",
        ok,
        "R12: "
        + (
            "every branch states its positioning and the swing variables name a crypto driver of "
            "its own. Whether the channel named is the one that moved the asset last is judgment "
            "and is not checked here."
            if ok
            else (
                (f"{', '.join(silent)} states no positioning. " if silent else "")
                + (
                    "" if crypto_named
                    else "No swing variable names a crypto driver, so BTC's band is priced off the "
                    "equity channel by default. That is the W38 miss: BTC was capped at $79K "
                    "because the Fed was hiking, when the SEC's agency track was the driver."
                )
            )
        ),
        severity="warn",
    )


# ---------------------------------------------------------------------------
# The phase read, which is not one of the twelve
# ---------------------------------------------------------------------------


def phase_read(found: Proposal, tape: Tape) -> list[Check]:
    """Each fresh read, its move since last week, and whether it holds today.

    This is not a scenario rule and it is deliberately not part of `walk`.
    R1 to R12 are a checklist against the scenario table; these are questions
    about a phase read, and folding them into the numbered walk would put a
    rule number on something `scenario-rules.md` never wrote.

    None of these refuses. Any phase may follow any phase, so a move is
    reported, not judged; a jump of two or more steps is surfaced as a warning
    for the operator to read the change note against (the validator has
    already required that note to argue it). A read whose own conditions do
    not all hold on the day it was written is a warning too: a condition that
    is already failing is not evidence for the phase.
    """
    out: list[Check] = []
    for call in sorted(found.phase_calls, key=lambda one: one.asset):
        out.append(_transition(call))
        out.append(_holds_today(call, tape.holding.get(call.asset)))
    return out


def _transition(call: Any) -> Check:
    stage = f", {call.stage}" if call.stage else ""
    head = f"{call.asset}: read fresh as Phase {call.phase} ({call.phase_name}{stage})."
    if call.prior_phase is None:
        return Check(
            f"{call.asset}_transition",
            True,
            f"{head} There was no call before it.",
            severity="note",
        )
    since = f" in {call.prior_week}" if call.prior_week else ""
    if call.prior_phase == call.phase:
        return Check(
            f"{call.asset}_transition",
            True,
            f"{head} Same as last week{since}. {call.change_note or ''}".strip(),
            severity="note",
        )
    large = (call.steps or 0) >= 2
    return Check(
        f"{call.asset}_transition",
        not large,
        f"{head} Last week{since} was Phase {call.prior_phase}: {call.move}, "
        f"{call.steps} step{'s' if call.steps != 1 else ''} around the cycle. "
        + (
            "A jump this size is allowed and has to be argued; read the note against the "
            "evidence. "
            if large
            else ""
        )
        + f"What changed: {call.change_note or '(no note)'}",
        severity="warn" if large else "note",
    )


def _holds_today(call: Any, holding: Holding | None) -> Check:
    total = len(call.conditions)
    if holding is None:
        return Check(
            f"{call.asset}_holds_today",
            True,
            f"{call.asset}: not checked against the tape, because no history was available "
            "to score the four conditions against.",
            severity="note",
        )
    day = f"on {holding.asof.isoformat()}" if holding.asof else "on the evaluation date"
    sentence = (
        f"{call.asset}: {holding.met} of the {total} things Phase {call.phase} is held on "
        f"hold {day}."
    )
    if holding.failed:
        sentence += (
            f" {holding.failed} failed. A condition that is not true on the day of the read is "
            "not evidence for it; either the read or the condition is wrong."
        )
    if holding.unevaluable:
        sentence += (
            f" {holding.unevaluable} could not be evaluated, because its series has no reading "
            "that day or a manual condition carries no assertion; that is not a failure."
        )
    return Check(
        f"{call.asset}_holds_today",
        holding.failed == 0,
        sentence,
        severity="warn" if holding.failed else "note",
    )


def report(checks: list[Check]) -> str:
    """The preflight as the operator reads it: one line per rule, in order."""
    width = max((len(one.marker) for one in checks), default=4)
    return "\n".join(f"{one.marker:<{width}}  {one.sentence}" for one in checks)
