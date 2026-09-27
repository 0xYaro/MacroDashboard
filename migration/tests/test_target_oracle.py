"""The scenario-target row count must equal the denominator the journals recorded.

CONTRACT.md: "These are what `targets hit x of n` counts, and `n` is the row
count." The denominator for week N's scenarios is written by the journal of week
N+1, which is the journal that graded them.
"""
import pytest

from macro_migrate.targets import parse_targets
from macro_migrate.textutil import uid
from macro_migrate.verify import KNOWN_SOURCE_DEFECTS

# Transcribed from each grading journal's section 0.
RECORDED = {
    ("2026-W36", 1): 9, ("2026-W36", 2): 6, ("2026-W36", 3): 7,
    ("2026-W37", 1): 10, ("2026-W37", 2): 11, ("2026-W37", 3): 8,
    ("2026-W38", 1): 11, ("2026-W38", 2): 11, ("2026-W38", 3): 9,
}


@pytest.mark.parametrize("week,ordinal,n", [(w, o, n) for (w, o), n in RECORDED.items()])
def test_row_count_equals_recorded_denominator(build, week, ordinal, n):
    rows = [t for t in build.rows["scenario_target"]
            if t["scenario_id"] == uid("scenario", week, str(ordinal))]
    if (week, ordinal) in KNOWN_SOURCE_DEFECTS:
        pytest.xfail(KNOWN_SOURCE_DEFECTS[(week, ordinal)])
    assert len(rows) == n


def test_the_two_counts_the_brief_names_explicitly(build):
    """W39's grading says W38's S1 hit 7 of 11 and S3 0 of 9."""
    def n(ordinal):
        return len([t for t in build.rows["scenario_target"]
                    if t["scenario_id"] == uid("scenario", "2026-W38", str(ordinal))])
    assert n(1) == 11
    assert n(3) == 9


def test_week_grade_targets_n_matches_the_base_scenario(build):
    counts = {}
    for t in build.rows["scenario_target"]:
        counts[t["scenario_id"]] = counts.get(t["scenario_id"], 0) + 1
    checked = 0
    for r in build.rows["week_grade"]:
        if r["targets_n"] is None or not r["base_scenario_id"]:
            continue
        assert counts[r["base_scenario_id"]] == r["targets_n"], r["week"]
        checked += 1
    assert checked == 3


def test_a_manual_target_still_counts():
    raw = ("**Eq:** S&P 7,520-7,800, breadth 44-55%. "
           "**Leadership:** Nasdaq beats the Dow again and memory leads the Nasdaq. "
           "**Oil:** Brent $92-112")
    targets, _ = parse_targets(raw)
    assert len(targets) == 4
    leadership = [t for t in targets if t.group == "Leadership"]
    assert len(leadership) == 1
    assert leadership[0].kind == "manual"
    assert leadership[0].series_key is None


def test_an_ambiguity_statement_is_not_a_target():
    raw = ("**Rates:** 30Y 5.55-5.80%; the 2Y is ambiguous because a growth "
           "scare and inflation pull opposite ways")
    targets, dropped = parse_targets(raw)
    assert len(targets) == 1
    assert len(dropped) == 1


def test_a_two_sided_band_is_one_target():
    raw = "**Metals:** gold < $4,400 hot, >$4,750 cold"
    targets, _ = parse_targets(raw)
    assert len(targets) == 1


def test_two_series_in_one_group_are_two_targets():
    raw = "**Rates:** 2Y 4.60-4.90%, 30Y 5.30-5.60%"
    targets, _ = parse_targets(raw)
    assert [t.series_key for t in targets] == ["ust_2y", "ust_30y"]
