"""How much of a real week the daily evaluation can actually score.

Every condition and trigger clause migrated from the journals is `kind='manual'`
with a null expression, because the journals were prose. That is 0% typed, and a
week authored here that carried the same proportion would mean the twice-daily
evaluation had nothing to do.

So this is the number that says whether the system works, and it is computed
from the fixture rather than asserted in a report. The fixture is W39's authored
judgment carried into W40, with W40's two fresh phase reads beside it: eight
phase conditions, the same three
scenarios, the same four setups, the same forty-one target rows. Nothing was
dropped to make the number look better, and the things that genuinely cannot be
typed are named below with the reason.

The number moved once already, and it is worth recording how. The first pass
typed 60% of what the daily run evaluates and only 38% of trigger clauses,
because almost every clause named a macro print, the implied FOMC odds, a
moving average or an auction, and none of those was a series. All four gaps were
then closed in the registry, and the same fixture retyped to 85% overall and
79% of clauses without a single sentence being weakened.

What did not move is the discipline. Three of the new typings only became
possible once the clause text was narrowed first, which is the rule this package
enforces on itself: a tail became a bid-to-cover floor and a high-yield ceiling,
"clears cleanly" was given numbers, and one clause that bundled an auction with
a Senior Credit Officer survey was split in two, because the survey half still
has no series and typing the pair would have made the clause report `met` on
half its evidence.

The thresholds are floors, not targets. They exist so that a later change which
quietly pushes conditions back into `manual` fails here rather than showing up
as a dashboard that stops moving.
"""

from __future__ import annotations

import re

from .conftest import full


def coverage(found) -> dict[str, tuple[int, int]]:
    """`(typed, total)` per kind of thing the daily run evaluates."""
    conditions = [c for call in found.phase_calls for c in call.conditions]
    clauses = [c for s in found.scenario_set.scenarios for c in s.clauses]
    gates = [s for s in found.setups if s.gate_expr is not None]
    targets = [t for s in found.scenario_set.scenarios for t in s.targets]
    return {
        "phase conditions": (
            sum(1 for one in conditions if one.kind != "manual"),
            len(conditions),
        ),
        "trigger clauses": (sum(1 for one in clauses if one.kind != "manual"), len(clauses)),
        "setup gates": (len(gates), len(found.setups)),
        "scenario targets": (sum(1 for one in targets if one.kind == "band"), len(targets)),
    }


def test_the_typed_fraction_of_a_realistic_week(w40, capsys, reads):
    found = full(w40, reads)
    rows = coverage(found)

    conditions = rows["phase conditions"]
    clauses = rows["trigger clauses"]
    gates = rows["setup gates"]
    targets = rows["scenario targets"]

    evaluated_typed = conditions[0] + clauses[0] + gates[0]
    evaluated_total = conditions[1] + clauses[1] + gates[1]

    with capsys.disabled():
        print("\n  what the daily run evaluates, for one realistic week (2026-W40)")
        for name, (typed, total) in rows.items():
            print(f"    {name:<18} {typed:>3} of {total:>3}   {typed / total:.0%}")
        print(
            f"    {'conditions total':<18} {evaluated_typed:>3} of {evaluated_total:>3}   "
            f"{evaluated_typed / evaluated_total:.0%}"
        )
        print("    migrated weeks       0 of every one   0%")

    # Phase conditions are what a stale read is flagged on between Mondays, so a
    # manual one is a condition that can never raise the flag. Every one in both
    # books is typed: the 200dma through the `field` accessor, and BTC against
    # its 50-week average as a named level read off `btc_50w_ma`.
    assert conditions == (8, 8)

    # Setup gates are the thing three consecutive weeks got wrong, and every one
    # of them is a threshold on a series the worker fetches deterministically.
    assert gates[0] == 3
    assert gates[0] == len([one for one in found.setups if one.gate_text])

    # Target bands are what `targets hit x of n` counts.
    assert targets[0] / targets[1] > 0.85

    # Trigger clauses were the weak half at 38% and are the part that moved most.
    # What is left manual is named in the test below.
    assert clauses[0] / clauses[1] >= 0.75

    assert evaluated_typed / evaluated_total >= 0.80


def test_every_manual_condition_says_why_it_is_manual(w40, reads):
    """`manual` is reserved for what genuinely needs judgment, and it has to say so.

    A `manual` condition with no asserted text is indistinguishable from one
    nobody bothered to type, and the whole discipline rests on the difference.
    """
    found = full(w40, reads)
    manual: list = [c for call in found.phase_calls for c in call.conditions if c.kind == "manual"]
    manual += [c for s in found.scenario_set.scenarios for c in s.clauses if c.kind == "manual"]
    assert manual
    for one in manual:
        assert one.asserted_text, f"{one.text!r} is manual and does not say why"


def test_what_is_left_manual_is_manual_for_one_of_three_reasons(w40, reads):
    """The manual clauses, named, so the list can be argued with.

    The four causes the first pass listed were all gaps in `config/series.yaml`
    and all four have been closed: the macro prints exist, the implied FOMC odds
    exist as a numeric series beside the prose one, `btc_50w_ma` exists, and the
    auctions exist as bid-to-cover and high yield per tenor.

    Three causes are left, and none of them is a gap anyone can close by adding
    a series:

    1. **A company print.** No series carries a guide, and none should: a
       company's numbers are not a market measurement.
    2. **A survey nobody publishes as a series.** The Senior Credit Officer
       survey is the live case.
    3. **A dateless shock.** R6 reserves explicit probability for an event no
       calendar carries, so by construction it can never be a threshold.

    A fourth thing is deliberately absent and must stay absent: **an auction
    tail.** A tail is the high yield less the when-issued yield at the bidding
    deadline, Treasury does not publish when-issued, and `averageMedianYield`
    describes the auction's own distribution rather than the gap to a market
    quoted outside it. A clause naming a tail is narrowed to bid-to-cover and
    high yield, which is what the fixture does three times.
    """
    found = full(w40, reads)
    manual = [
        (f"S{s.ordinal} clause {c.ordinal}", c.text)
        for s in found.scenario_set.scenarios
        for c in s.clauses
        if c.kind == "manual"
    ]
    assert manual, "the fixture has stopped exercising the manual path at all"

    company = ("micron", "guide", "earnings")
    survey = ("survey", "senior credit officer")
    dateless = ("shock", "dateless", "release", "markdown", "capex trim")

    unexplained = [
        label
        for label, text in manual
        for lowered in [text.lower()]
        if not any(word in lowered for word in company + survey + dateless)
    ]
    assert not unexplained, (
        "these manual clauses fit none of the three remaining causes, so either they "
        f"can be typed or a fourth cause needs writing down: {unexplained}"
    )


def test_no_clause_names_an_auction_tail(w40, reads):
    """There is no tail series, and there must not be one.

    Treasury does not publish when-issued, so a tail cannot be computed from
    anything this system holds, and `averageMedianYield` is in the same feed and
    is not a substitute: subtracting one from the other gives a number in basis
    points that would look exactly like a tail and move roughly with one.

    A future reader with a news scrape is exactly who will try to add one, which
    is why the absence is asserted here as well as stated in `CONTRACT.md`,
    `config/series.yaml`, the worker's fetcher and `SKILL.md`.
    """
    found = full(w40, reads)
    texts = [c.text.lower() for s in found.scenario_set.scenarios for c in s.clauses]
    texts += [c.text.lower() for call in found.phase_calls for c in call.conditions]
    # On a word boundary: "retail sales" is not an auction tail.
    offenders = [one for one in texts if re.search(r"\btail", one)]
    assert not offenders, (
        "a clause still asserts a tail. Narrow it to what its expression checks: "
        f"bid-to-cover and high yield per tenor. {offenders}"
    )


def test_the_registry_declares_no_tail_series(settings):
    """The same absence, asserted against `config/series.yaml` itself."""
    from macro_agent.worker_bridge import registry

    tails = [
        one.key for one in registry(settings).ordered() if re.search(r"(^|_)tail(_|$)", one.key)
    ]
    assert not tails, (
        "a tail series has been added. It cannot be computed: Treasury does not publish "
        f"when-issued, and averageMedianYield is not a substitute. {tails}"
    )
