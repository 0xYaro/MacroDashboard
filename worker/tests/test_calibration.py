"""Brier and the rolling read, against the twelve weeks already in the ledger.

Every expected value here is a number the Scorecard Ledger or the W39 journal
already carries. These are not fixtures this repository invented: if the port
had changed the arithmetic, the Brier series would stop being comparable with
the eleven weeks scored before it, and this file is what notices.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from macro.calibration import GradedWeek, ScenarioOutcome, brier, read


def outcomes(*rows: tuple[str, int, str]) -> list[ScenarioOutcome]:
    return [ScenarioOutcome(label, probability, grade) for label, probability, grade in rows]


# ---------------------------------------------------------------------------
# Brier
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("week", "rows", "expected"),
    [
        # 2026-W36: S1 45% Partial, S2 30% Not, S3 25% Partial. Ledger: 0.155.
        ("2026-W36", (("S1", 45, "Partial"), ("S2", 30, "Not"), ("S3", 25, "Partial")), "0.155"),
        # 2026-W37: S2 40% Materialized was the base. Ledger: 0.545.
        (
            "2026-W37",
            (("S1", 35, "Not"), ("S2", 40, "Materialized"), ("S3", 25, "Not")),
            "0.545",
        ),
        # 2026-W38: S1 45% Partial, S2 30% Partial, S3 25% Not. Ledger: 0.105.
        ("2026-W38", (("S1", 45, "Partial"), ("S2", 30, "Partial"), ("S3", 25, "Not")), "0.105"),
    ],
)
def test_brier_reproduces_every_score_in_the_ledger(week, rows, expected):
    assert round(brier(outcomes(*rows)), 3) == Decimal(expected)


def test_brier_worked_by_hand_for_w38():
    """The whole calculation, written out, for the week the ledger scored 0.105.

    Outcome weights: Partial 0.5, Partial 0.5, Not 0, which sum to 1.0, so the
    normalised outcomes are 0.5, 0.5 and 0.

      (0.45 - 0.5)^2 = 0.0025
      (0.30 - 0.5)^2 = 0.0400
      (0.25 - 0.0)^2 = 0.0625
                       ------
                       0.1050
    """
    assert brier(
        outcomes(("S1", 45, "Partial"), ("S2", 30, "Partial"), ("S3", 25, "Not"))
    ) == Decimal("0.1050")


def test_brier_is_the_worst_case_when_nothing_materialized():
    """The truth lay outside the set, so the score is `sum(p^2) + 1`.

    Hand-computed for 30 / 40 / 30, all graded Not:
      0.09 + 0.16 + 0.09 = 0.34, plus 1 = 1.34.
    """
    assert brier(outcomes(("S1", 30, "Not"), ("S2", 40, "Not"), ("S3", 30, "Not"))) == Decimal(
        "1.34"
    )


def test_a_perfect_forecast_scores_zero():
    """100 on the one scenario that materialized, 0 on the two that did not."""
    assert brier(
        outcomes(("S1", 0, "Not"), ("S2", 100, "Materialized"), ("S3", 0, "Not"))
    ) == Decimal(0)


def test_brier_is_not_forgiven_for_anything():
    """There is no discount, no exclusion and no argument taking one.

    A scenario whose premise moved is scored normally and the ledger row carries
    the tag. R6 exists because two weeks in ten were driven by an event no
    calendar carried, so over-anchoring on a dated catalyst is a forecasting
    error rather than bad luck.
    """
    import inspect

    signature = inspect.signature(brier)
    assert list(signature.parameters) == ["outcomes"]


def test_brier_refuses_an_empty_set():
    with pytest.raises(ValueError, match="needs a scenario set"):
        brier([])


# ---------------------------------------------------------------------------
# The rolling read
# ---------------------------------------------------------------------------


LEDGER = [
    ("2026-W30", 'S2 "Distribution grinds; index chops" (40%)', "Partial", None),
    ("2026-W31", 'S2 "Hold with hawkish framing" (40%)', "Partial", None),
    ("2026-W32", 'S2 "Framework wobbles" (40%)', "Partial", None),
    ("2026-W34", 'S2 "Records hold" (40%)', "Partial", None),
    ("2026-W35", 'S2 "The turf fight stays unresolved" (40%)', "Partial", None),
    ("2026-W36", 'S1 "Hawkish resilience" (45%)', "Partial", "0.155"),
    ("2026-W37", 'S2 "Hot-enough CPI" (40%)', "Materialized", "0.545"),
    ("2026-W38", 'S1 "The Fed hikes with a path" (45%)', "Partial", "0.105"),
]


def ledger():
    return [
        GradedWeek(week, label, grade, None if score is None else Decimal(score))
        for week, label, grade, score in LEDGER
    ]


def test_the_rolling_read_reproduces_the_w39_journals_own_line():
    """W39 section 4 states it in prose, and every figure here is from that line.

    "The ledger's base case scores 4.5 of the last 8 graded weeks with only 1
    full materialization and a mean Brier of 0.27 across three scored weeks."

    Hand-computed: seven Partials at a half plus one Materialized at one is 4.5;
    one of the eight fully materialized; the three recorded Briers average
    (0.155 + 0.545 + 0.105) / 3 = 0.2683, which rounds to 0.27.
    """
    out = read(ledger())
    assert out.graded_weeks == 8
    assert out.base_hit_score == Decimal("4.5")
    assert out.base_materialized == 1
    assert out.brier_weeks == 3
    assert round(out.mean_brier, 2) == Decimal("0.27")
    assert out.window == 8


def test_the_habit_check_counts_how_often_the_base_was_s2():
    """Six of the last eight, which is the shape the habit takes."""
    assert read(ledger()).base_was_s2 == 6


def test_the_sentence_is_the_one_section_zero_pastes():
    assert read(ledger()).sentence == (
        "**Calibration:** base case scored 4.5 of the last 8 graded weeks "
        "(Partial = half credit), fully materialized in 1 of 8 (12%); "
        "mean Brier 0.27 over 3 scored weeks (0 = perfect, 2 = worst); "
        "base was S2 in 6 of 8."
    )


def test_the_window_is_the_last_eight_and_older_weeks_do_not_count():
    """A ninth week, graded Not, changes nothing: it is outside the window."""
    older = [GradedWeek("2026-W29", 'S2 "..." (40%)', "Not", None), *ledger()]
    assert read(older).graded_weeks == 8
    assert read(older).base_hit_score == Decimal("4.5")


def test_a_shorter_record_reports_its_own_length():
    """Starting empty would silence the check, so it reports what it has."""
    out = read(ledger()[:3])
    assert out.graded_weeks == 3
    assert out.base_hit_score == Decimal("1.5")
    assert out.mean_brier is None
    assert out.brier_weeks == 0


def test_an_empty_ledger_says_so_rather_than_reporting_zero():
    out = read([])
    assert out.graded_weeks == 0
    assert out.base_hit_score is None
    assert out.mean_brier is None
    assert "no graded weeks yet" in out.sentence


def test_a_migrated_week_with_no_brier_is_counted_in_the_hit_rate():
    """`base_scenario_label` always carries the title, so migrated rows still read.

    Five of the eight weeks above have no Brier at all: they predate the column.
    They count toward the hit rate and not toward the mean.
    """
    out = read(ledger())
    assert out.graded_weeks == 8
    assert out.brier_weeks == 3


# ---------------------------------------------------------------------------
# The shape of `other_grades`
# ---------------------------------------------------------------------------
#
# An array, never an object keyed by scenario id. This is the one thing three
# separate parts each got right on their own terms and wrong together: the
# column defaults to '[]'::jsonb, the web app reads an array, and an object
# keyed `S1` parses as a malformed row, rejects every ledger row it appears in,
# and empties the calibration route entirely.


from datetime import UTC, date, datetime  # noqa: E402

from macro.grading import ScenarioJudgment, other_grades, week_grade  # noqa: E402
from macro.model import Scenario, ScenarioSet  # noqa: E402


def w39_set() -> ScenarioSet:
    """W39's three scenarios, with the titles and probabilities as written."""
    titles = (
        (1, 45, "Narrow and high-yielding: memory carries a thinning tape", "Hold quality"),
        (2, 32, "Duration fails and the 5% ten-year finally transmits", "Cut high-beta"),
        (3, 23, "Oil breaks the inflation leg and breadth repairs", "Add toward the middle"),
    )
    return ScenarioSet(
        set_id="set1",
        week="2026-W39",
        authored_asof=date(2026, 9, 21),
        activated_at=datetime(2026, 9, 21, 1, 30, tzinfo=UTC),
        scenarios=tuple(
            Scenario(
                scenario_id=f"sc{ordinal}",
                set_id="set1",
                ordinal=ordinal,
                title=title,
                probability=probability,
                positioning=positioning,
                clauses=(),
                targets=(),
            )
            for ordinal, probability, title, positioning in titles
        ),
    )


#: W39's grading of the W38 set, with the notes as section 0 actually writes
#: them. Every one of these sentences is backward-looking, written at grading
#: time about what happened, and none of them is the scenario's positioning.
S2_NOTE = (
    "Not one trigger clause fired, yet the four bands S1 missed — VIX 13–16, BTC $79K–$85K, "
    "ETH $2,550–$2,850, gold $4,380–$4,600 — all landed here, plus HY at the top of its range."
)
S3_NOTE = (
    "The 20Y tailed 2 bp at a record 5.42% and the 10Y touched 5.04%, but the trigger was "
    "written as a conjunction requiring the 30Y above 5.60%, which never came, and no VIX, HY "
    "or breadth leg confirmed for a sixth straight week."
)

JUDGMENTS = [
    ScenarioJudgment("sc1", "Partial", "The trigger fired in full and the equity bands held."),
    ScenarioJudgment("sc2", "Not", S2_NOTE),
    ScenarioJudgment("sc3", "Partial", S3_NOTE),
]


def judged() -> dict[str, ScenarioJudgment]:
    return {one.scenario_id: one for one in JUDGMENTS}


def test_other_grades_is_an_array_and_never_an_object():
    """The bug, asserted directly. A mapping here empties `/calibration`."""
    out = other_grades(w39_set(), judged(), base_scenario_id="sc1")
    assert isinstance(out, list)
    assert not isinstance(out, dict)


def test_every_entry_carries_the_four_named_keys():
    """`id`, `prob`, `grade` and `note`, on every entry, with the right types."""
    out = other_grades(w39_set(), judged(), base_scenario_id="sc1")
    assert len(out) == 2  # the base case is excluded
    for entry in out:
        assert {"id", "prob", "grade", "note"} <= set(entry)
        assert isinstance(entry["id"], str)
        assert isinstance(entry["prob"], int)
        assert entry["grade"] in ("Materialized", "Partial", "Not")
        assert isinstance(entry["note"], str)


def test_the_entries_are_in_scenario_order_and_name_the_right_scenarios():
    out = other_grades(w39_set(), judged(), base_scenario_id="sc1")
    assert [entry["id"] for entry in out] == ["S2", "S3"]
    assert [entry["prob"] for entry in out] == [32, 23]
    assert [entry["grade"] for entry in out] == ["Not", "Partial"]


def test_the_note_is_the_grading_reason_and_never_the_positioning():
    """The two are opposite things and one reads plausibly in the other's column.

    `note` is written at grading time, looking backward at what happened.
    `positioning` is forward-looking posture, written a week earlier when the
    scenario was authored. Substituting it would put last week's intentions in
    the column that is supposed to explain last week's outcome, and nothing
    downstream would catch it.
    """
    out = other_grades(w39_set(), judged(), base_scenario_id="sc1")
    by_id = {entry["id"]: entry for entry in out}

    assert by_id["S2"]["note"] == S2_NOTE
    assert by_id["S3"]["note"] == S3_NOTE

    positionings = {one.positioning for one in w39_set().scenarios}
    assert not any(entry["note"] in positionings for entry in out)


def test_a_scenario_graded_without_a_reason_gets_a_blank_and_not_a_sentence():
    """The key is always present, and a blank is visibly a blank.

    Nothing here synthesises a reason from the band counts. A generated
    sentence that reads like a human one is worse than an empty string,
    because the ledger's whole value is that its prose was written by someone
    who thought about it.
    """
    out = other_grades(
        w39_set(),
        {one.scenario_id: ScenarioJudgment(one.scenario_id, "Not") for one in w39_set().scenarios},
        base_scenario_id="sc1",
    )
    assert [entry["note"] for entry in out] == ["", ""]
    assert all("note" in entry for entry in out)


def test_the_week_grade_row_carries_the_same_array():
    """End to end, through the function that builds the ledger row."""
    grade, findings = week_grade(
        week="2026-W39",
        scenario_set=w39_set(),
        judgments=JUDGMENTS,
        target_states_by_scenario={},
        eq_call="valid",
        crypto_call="valid",
        key_miss="...",
        graded_at=datetime(2026, 9, 28, 1, 30, tzinfo=UTC),
    )
    assert findings == []
    assert isinstance(grade.other_grades, list)
    assert [entry["id"] for entry in grade.other_grades] == ["S2", "S3"]
    assert grade.base_scenario_id == "sc1"  # 45% is the highest
    assert grade.base_scenario_label.startswith('S1 "Narrow and high-yielding')
    assert grade.base_grade == "Partial"
    # The base case's own reasoning lives in `key_miss`, which is where the
    # ledger puts it, so it is absent from `other_grades` by design.
    assert all(entry["id"] != "S1" for entry in grade.other_grades)
    assert [entry["note"] for entry in grade.other_grades] == [S2_NOTE, S3_NOTE]


def test_the_row_survives_a_round_trip_through_json():
    """What the column actually stores, and what the web app actually reads."""
    import json

    grade, _ = week_grade(
        week="2026-W39",
        scenario_set=w39_set(),
        judgments=JUDGMENTS,
        target_states_by_scenario={},
        eq_call="valid",
        crypto_call="valid",
        key_miss="...",
        graded_at=datetime(2026, 9, 28, 1, 30, tzinfo=UTC),
    )
    decoded = json.loads(json.dumps(grade.other_grades))
    assert isinstance(decoded, list)
    assert all({"id", "prob", "grade", "note"} <= set(one) for one in decoded)
