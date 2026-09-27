"""The single most important check in the migration.

The 2026-W39 journal's section 0 prints a calibration sentence produced by the
vault's calibration.py over Scorecard Ledger.md. The migrated week_grade rows,
run through the same arithmetic over the database columns, must reproduce it.

If this fails the migration is wrong, whatever else passes.
"""
from macro_migrate import config, sources
from macro_migrate.verify import W39_CALIBRATION_SENTENCE, calibration_from_rows

# Quoted verbatim from 2026-W39.md section 0, with the parenthetical gloss the
# journal elides.
W39_JOURNAL_QUOTE = (
    "base case scored 4.5 of the last 8 graded weeks (Partial = half credit), "
    "fully materialized in 1 of 8 (12%); mean Brier 0.27 over 3 scored weeks; "
    "base was S2 in 6 of 8."
)


def test_the_sentence_under_test_is_what_the_journal_prints():
    assert W39_CALIBRATION_SENTENCE == W39_JOURNAL_QUOTE


def test_migrated_week_grades_reproduce_the_w39_calibration_sentence(build):
    got = calibration_from_rows(build.rows["week_grade"])
    assert got["line"] == W39_JOURNAL_QUOTE


def test_every_component_of_the_read(build):
    got = calibration_from_rows(build.rows["week_grade"])
    assert got["graded_weeks"] == 8
    assert got["first_week"] == "2026-W30"
    assert got["last_week"] == "2026-W38"
    assert got["base_hit_score"] == 4.5
    assert got["base_materialized"] == 1
    assert got["mean_brier"] == 0.268
    assert got["brier_weeks"] == 3
    assert got["base_was_s2"] == 6


def test_parity_with_the_vault_script_over_the_live_ledger(build):
    """Not only self-consistent: the same numbers the system prints today."""
    live = sources.calibration.read(str(config.SCORECARD_LEDGER), 8)
    got = calibration_from_rows(build.rows["week_grade"])
    assert live["graded_weeks"] == got["graded_weeks"]
    assert round(live["base_hit_rate"] * live["graded_weeks"], 1) == got["base_hit_score"]
    assert round(live["base_full_rate"] * live["graded_weeks"]) == got["base_materialized"]
    assert live["mean_brier"] == got["mean_brier"]
    assert live["brier_weeks"] == got["brier_weeks"]
    assert live["base_was_s2"] == got["base_was_s2"]


def test_the_brier_column_matches_the_vault_arithmetic(build):
    """Each migrated brier is what calibration.py would compute for that week."""
    spec = {
        "2026-W36": "S1:45:Partial,S2:30:Not,S3:25:Partial",
        "2026-W37": "S1:35:Not,S2:40:Materialized,S3:25:Not",
        "2026-W38": "S1:45:Partial,S2:30:Partial,S3:25:Not",
    }
    by_week = {r["week"]: r for r in build.rows["week_grade"]}
    for week, s in spec.items():
        assert round(sources.calibration.brier(s), 3) == float(by_week[week]["brier"]), week


def test_no_oracle_failed(checks):
    all_checks, _ = checks
    failed = [(c.key, c.subject, c.detail) for c in all_checks if not c.passed]
    assert failed == []
