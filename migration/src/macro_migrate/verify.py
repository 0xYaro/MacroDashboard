"""Oracles, and the hand-verification report.

The contract requires this history be "verified by hand", so the report names,
for every migrated week, what was read, what was produced, and every field the
source did not carry.

The oracles are the machine half of that. The load-bearing one is
`calibration_check`: the migrated week_grade rows, run through the same
arithmetic as the vault's calibration.py, must reproduce the sentence the W39
journal prints in its section 0. If they do not, the migration is wrong.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from . import config, sources
from .model import ORDINAL_OF, Build, score_met
from .textutil import strip_md

# One recorded denominator this migration deliberately does not match, with the
# reason. It is a defect in the source, not in the parse: the 2026-W37 journal
# counted 2026-W36's S1 as 9 targets *including* its three non-numeric legs
# ("Nasdaq leads", "Russell lags", "SOL leads") and, in the same grading block,
# counted W36's S2 as 6 *excluding* its two equivalent non-numeric legs ("ETH
# catches up", "curve steepens"). Both cannot be right. The parse keeps all 8
# rows, because dropping a stated forecast leg would make the scenario look more
# checkable than it was. W36's S2 was not the base case, so the Scorecard
# Ledger's "6 of 9" for W36 is unaffected and week_grade is correct.
KNOWN_SOURCE_DEFECTS = {
    ("2026-W36", 2): (
        "the 2026-W37 journal recorded n = 6 for W36's S2 while recording n = 9 "
        "for W36's S1 on the opposite convention; the parse keeps all 8 stated "
        "target legs"),
}

WEIGHT = {"Materialized": 1.0, "Partial": 0.5, "Not": 0.0}

# The sentence 2026-W39.md section 0 prints, which this migration must be able
# to reproduce from macro_core.week_grade alone.
W39_CALIBRATION_SENTENCE = (
    "base case scored 4.5 of the last 8 graded weeks (Partial = half credit), "
    "fully materialized in 1 of 8 (12%); mean Brier 0.27 over 3 scored weeks; "
    "base was S2 in 6 of 8."
)


# Places where docs/CONTRACT.md, supabase/migrations or the sources disagree with
# each other or with what the history actually contains. The migration does not
# edit the contract; it works to the deployed schema and names every one here and
# in the final report.
#
# Four earlier entries have been removed because the schema was corrected in
# response to them and this migration now works to the corrected shape:
# macro_core.setup carries sector_focus_id with a foreign key onto sector_focus
# and the seed writes it directly; week_grade.graded_at is nullable, so the nine
# archive-graded weeks carry null rather than a derived Monday;
# macro_core.condition_state_asserted exists and this seed fills it; and
# CONTRACT.md's W39 worked example is corrected, eleven being 2026-W38's S1 while
# 2026-W39's S1 is thirteen bands plus one manual target. Two more are gone
# because CONTRACT.md now states the conventions this migration adopted: the
# clause split must be lossless, and `required` takes false when the sentence's
# shape does not decide it.
CONTRACT_ISSUES: list[tuple[str, str]] = [
    ("macro_core.setup NOT NULL against the null-never-zero doctrine",
     "entry_lo, entry_hi, invalidation, invalidation_dir and target are NOT NULL. "
     "The Setup Ledger contains one setup whose entry is a condition rather than "
     "a band and three whose invalidation names a level with no side, so those "
     "fields have to be derived to load at all. Each derivation is named in this "
     "report, and the rule that derives a side is checked against every row where "
     "the journal did write one."),
    ("macro_core.setup_grade.graded_week NOT NULL leaves an ungraded setup "
     "nowhere to sit",
     "The Setup Ledger's four 2026-W39 rows read `open (grade in W40)`, which is "
     "a placeholder rather than a grading. Writing 2026-W40 into graded_week "
     "would assert a grading that has not happened, so those four setups carry "
     "no setup_grade row and the 2026-W40 Monday run writes the first one. If "
     "the app needs an ungraded setup to be visible as `open`, that belongs in "
     "macro_mart.setup_state, which already has an entry_state of `not "
     "triggered`."),
    ("No journal records whether an individual trigger clause fired",
     "macro_core.condition_state_asserted now holds the status a manual "
     "condition was written with, and a phase tracker row supplies one "
     "directly. A trigger clause does not: the grading journals judge a trigger "
     "as a whole: `the trigger fired in full`, `not one trigger clause "
     "fired`. Every migrated clause is therefore asserted `unevaluable` and "
     "carries that whole-trigger verdict as its current_text, rather than "
     "having a per-clause verdict invented for it."),
    ("CONTRACT.md's cadence vocabulary does not include `continuous`",
     "The 'Sources and their cadence' summary table calls BTC, ETH and SOL "
     "continuous, which is not one of the five values the same document defines "
     "for macro_core.series.cadence. config/series.yaml has already resolved "
     "this as daily on a calendar_day release rule; the summary table still "
     "says continuous."),
    ("The 2026-W37 journal records two different target-counting conventions",
     "It counts 2026-W36's S1 as 9 including three non-numeric legs and, in the "
     "same block, counts W36's S2 as 6 excluding two equivalent ones. This is a "
     "source defect rather than a contract issue, but it is the one recorded "
     "denominator this migration does not reproduce, so it is listed here too."),
]


@dataclass
class Check:
    key: str
    subject: str
    passed: bool
    detail: str


def calibration_from_rows(rows: list[dict], n: int = 8) -> dict:
    """The calibration read, computed over migrated week_grade rows.

    Identical arithmetic to the vault's calibration.py `read`, expressed over
    the database columns instead of the markdown cells:
      Base grade      -> week_grade.base_grade
      Base case (prob)-> week_grade.base_scenario_label
      Brier           -> week_grade.brier
    """
    last = sorted(rows, key=lambda r: r["week"])[-n:]
    hits = sum(WEIGHT.get(r["base_grade"], 0.0) for r in last)
    full = sum(1 for r in last if r["base_grade"] == "Materialized")
    s2 = sum(1 for r in last
             if re.match(r"\s*S2\b", r["base_scenario_label"] or ""))
    briers = [float(r["brier"]) for r in last if r["brier"] is not None]
    k = len(last)
    out = {
        "graded_weeks": k,
        "base_hit_score": hits,
        "base_materialized": full,
        "mean_brier": round(sum(briers) / len(briers), 3) if briers else None,
        "brier_weeks": len(briers),
        "base_was_s2": s2,
        "window": n,
        "first_week": last[0]["week"] if last else None,
        "last_week": last[-1]["week"] if last else None,
    }
    line = ("base case scored %.1f of the last %d graded weeks "
            "(Partial = half credit), fully materialized in %d of %d (%.0f%%)"
            % (hits, k, full, k, 100.0 * full / k if k else 0))
    if briers:
        line += "; mean Brier %.2f over %d scored weeks" % (
            out["mean_brier"], len(briers))
    line += "; base was S2 in %d of %d." % (s2, k)
    out["line"] = line
    return out


def calibration_check(b: Build) -> tuple[Check, dict]:
    got = calibration_from_rows(b.rows["week_grade"])
    ok = got["line"] == W39_CALIBRATION_SENTENCE
    return (Check(
        "calibration.reproduces_w39_section_0",
        "macro_core.week_grade over the last 8 graded weeks",
        ok,
        "expected %r\n     got %r" % (W39_CALIBRATION_SENTENCE, got["line"])
        if not ok else got["line"]), got)


def calibration_parity(b: Build) -> Check:
    """The same read, taken from the live ledger file by the vault's own script.

    Proves the migrated rows and the markdown the system prints from today agree
    number for number, not only that the migrated rows are self-consistent.
    """
    live = sources.calibration.read(str(config.SCORECARD_LEDGER), 8)
    got = calibration_from_rows(b.rows["week_grade"])
    # base_hit_rate is rounded to three decimals by the vault script, so the
    # comparison is against the score it implies, to within half a tenth.
    same = (abs(live["base_hit_rate"] * live["graded_weeks"]
                - got["base_hit_score"]) < 0.05
            and live["graded_weeks"] == got["graded_weeks"]
            and round(live["base_full_rate"] * live["graded_weeks"]) == got["base_materialized"]
            and live["mean_brier"] == got["mean_brier"]
            and live["brier_weeks"] == got["brier_weeks"]
            and live["base_was_s2"] == got["base_was_s2"])
    return Check("calibration.parity_with_vault_script",
                 "calibration.py read over Scorecard Ledger.md",
                 same,
                 "vault %s | migrated %s" % (
                     {k: live[k] for k in ("graded_weeks", "base_hit_rate",
                                           "base_full_rate", "mean_brier",
                                           "brier_weeks", "base_was_s2")},
                     {k: got[k] for k in ("graded_weeks", "base_hit_score",
                                          "base_materialized", "mean_brier",
                                          "brier_weeks", "base_was_s2")}))


def target_oracles(b: Build) -> list[Check]:
    """Recorded `targets hit x of n` denominators, per scenario, per week.

    CONTRACT.md: n is the scenario_target row count. The denominator is recorded
    by the *following* week's journal, which is the week that graded the set.
    """
    recorded: dict[tuple[str, int], int] = {}
    for graded, grader in config.GRADED_BY.items():
        sc = b.sidecars.get(grader)
        for g in ((sc or {}).get("scorecard") or {}).get("scenario_grades", []):
            if g.get("targets_n") is not None and g.get("id") in ORDINAL_OF:
                recorded[(graded, ORDINAL_OF[g["id"]])] = g["targets_n"]

    by_scenario: dict[str, int] = defaultdict(int)
    for t in b.rows["scenario_target"]:
        by_scenario[t["scenario_id"]] += 1

    from .textutil import uid
    checks: list[Check] = []
    for (week, ordinal), n in sorted(recorded.items()):
        got = by_scenario[uid("scenario", week, str(ordinal))]
        known = KNOWN_SOURCE_DEFECTS.get((week, ordinal))
        checks.append(Check(
            "targets.row_count_equals_recorded_denominator",
            "%s S%d" % (week, ordinal),
            got == n or known is not None,
            ("recorded n = %d, scenario_target rows = %d" % (n, got)
             + ("" if got == n else
                "; KNOWN SOURCE DEFECT: " + known))))
    return checks


def base_target_oracle(b: Build) -> list[Check]:
    """week_grade.targets_n must equal the base scenario's target row count."""
    counts: dict[str, int] = defaultdict(int)
    for t in b.rows["scenario_target"]:
        counts[t["scenario_id"]] += 1
    out: list[Check] = []
    for r in b.rows["week_grade"]:
        if r["targets_n"] is None or not r["base_scenario_id"]:
            continue
        got = counts[r["base_scenario_id"]]
        out.append(Check("week_grade.targets_n_matches_base_scenario",
                         r["week"], got == r["targets_n"],
                         "ledger says %d, base scenario has %d target rows"
                         % (r["targets_n"], got)))
    return out


def tracker_oracles(b: Build) -> list[Check]:
    """Four conditions per phase call, and the Score line's met-count must equal
    the tracker's met rows."""
    state: dict[tuple[str, str], list[str]] = defaultdict(list)
    for r in b.rows["condition_state_asserted"]:
        if r["owner_kind"] == "phase":
            state[(r["_week"], r["_subject"].split()[0])].append(r["status"])
    out: list[Check] = []
    for week in config.JUDGMENT_WEEKS:
        sc = b.sidecars[week]
        for asset in ("equities", "crypto"):
            p = (sc.get("phase") or {}).get(asset) or {}
            rows = state[(week, asset)]
            out.append(Check("phase.four_conditions", "%s %s" % (week, asset),
                             len(rows) == 4, "%d tracker rows" % len(rows)))
            score = p.get("score") or {}
            met_written = score_met(score)
            met_rows = sum(1 for s in rows if s == "met")
            if met_written is None:
                out.append(Check("phase.score_met_equals_tracker",
                                 "%s %s" % (week, asset), False,
                                 "the journal's Score line did not parse into a "
                                 "met count, so the tracker cannot be checked "
                                 "against it (raw: %r)"
                                 % strip_md(score.get("raw", ""))[:70]))
                continue
            out.append(Check("phase.score_met_equals_tracker",
                             "%s %s" % (week, asset), met_written == met_rows,
                             "Score line says %d met, tracker has %d met rows"
                             % (met_written, met_rows)))
    return out


def structural_oracles(b: Build) -> list[Check]:
    out: list[Check] = []

    # Probabilities sum to 100 per set.
    by_set: dict[str, int] = defaultdict(int)
    week_of = {r["id"]: r["week"] for r in b.rows["scenario_set"]}
    for s in b.rows["scenario"]:
        by_set[s["set_id"]] += int(s["probability"] or 0)
    for sid, total in sorted(by_set.items(), key=lambda kv: week_of[kv[0]]):
        out.append(Check("scenario_set.probabilities_sum_to_100",
                         week_of[sid], total == 100, "sum = %d" % total))

    # Three scenarios per set.
    counts: dict[str, int] = defaultdict(int)
    for s in b.rows["scenario"]:
        counts[s["set_id"]] += 1
    for sid, n in sorted(counts.items(), key=lambda kv: week_of[kv[0]]):
        out.append(Check("scenario_set.three_scenarios", week_of[sid], n == 3,
                         "%d scenarios" % n))

    # Teaching weeks are a real foreign key onto graded weeks.
    graded = {r["week"] for r in b.rows["week_grade"]}
    missing = sorted({r["week"] for r in b.rows["scenario_rule_week"]} - graded)
    out.append(Check("scenario_rule_week.fk_onto_week_grade",
                     "R1..R12 teaching weeks", not missing,
                     "weeks with no week_grade row: %s" % (missing or "none")))

    # Rule numbers are 1..12 and never reused.
    nums = sorted(r["number"] for r in b.rows["scenario_rule"])
    out.append(Check("scenario_rule.numbers", "R1..R12",
                     nums == list(range(1, 13)), "numbers = %s" % nums))

    # Every Setup Ledger row produced exactly one setup and one setup_grade.
    ledger_rows = len(sources.read_setup_ledger())
    from_ledger = [s for s in b.rows["setup"] if s["_source"] == "Setup Ledger.md"]
    open_rows = sum(1 for r in sources.read_setup_ledger()
                    if strip_md(r["Outcome (graded)"]).lower().startswith("open"))
    out.append(Check("setup.ledger_row_per_setup", "Setup Ledger.md",
                     len(from_ledger) == ledger_rows
                     and len(b.rows["setup_grade"]) == ledger_rows - open_rows,
                     "ledger rows %d, setup rows from the ledger %d, "
                     "setup_grade rows %d (%d ledger rows read `open (grade in "
                     "W40)`, a placeholder rather than a grading, so they carry "
                     "no grade row)"
                     % (ledger_rows, len(from_ledger), len(b.rows["setup_grade"]),
                        open_rows)))

    # Every Scorecard Ledger row produced a week_grade row.
    lrows = len(sources.read_scorecard_ledger())
    out.append(Check("week_grade.row_per_ledger_row", "Scorecard Ledger.md",
                     lrows == len(b.rows["week_grade"]),
                     "ledger rows %d, week_grade rows %d"
                     % (lrows, len(b.rows["week_grade"]))))

    # The archive is not imported.
    early = sorted(w for w in {r["week"] for r in b.rows["scenario_set"]}
                   if w < "2026-W32")
    out.append(Check("scope.archive_not_imported", "2026-W03 .. 2026-W31",
                     not early, "scenario sets before 2026-W32: %s"
                     % (early or "none")))

    # Observations are unique on (series, asof) and HY OAS is in basis points.
    seen: dict[tuple[str, str], dict] = {}
    dupes = []
    for o in b.rows["observation"]:
        k = (o["series_key"], o["asof"])
        if k in seen and seen[k]["value"] != o["value"]:
            dupes.append("%s @ %s: %s vs %s" % (k[0], k[1], seen[k]["value"],
                                                o["value"]))
        seen[k] = o
    out.append(Check("observation.unique_series_asof", "macro_core.observation",
                     not dupes, "conflicting duplicates: %s" % (dupes or "none")))

    # Every plausible OAS in basis points is at least 10; every percent form the
    # journal prints ("2.70%", "0.81%") is below 10. That is the whole test.
    bad = [(o["series_key"], o["asof"], o["value"]) for o in b.rows["observation"]
           if o["series_key"] in ("hy_oas_bps", "ig_oas_bps")
           and o["value"] is not None and o["value"] < 10]
    vals = sorted({(o["series_key"], str(o["value"])) for o in b.rows["observation"]
                   if o["series_key"] in ("hy_oas_bps", "ig_oas_bps")
                   and o["value"] is not None})
    out.append(Check("observation.oas_stored_in_basis_points",
                     "hy_oas_bps, ig_oas_bps", not bad,
                     "values stored: %s" % vals if not bad
                     else "rows stored as percent: %s" % bad))

    # Every scenario_target satisfies the schema's shape check.
    shape_bad = []
    for t in b.rows["scenario_target"]:
        ok = ((t["kind"] == "manual" and t["series_key"] is None
               and t["comparator"] is None and t["lo"] is None and t["hi"] is None)
              or (t["kind"] == "band" and t["series_key"] and
                  ((t["comparator"] == "between" and t["lo"] is not None
                    and t["hi"] is not None)
                   or (t["comparator"] == "gt" and t["lo"] is not None
                       and t["hi"] is None)
                   or (t["comparator"] == "lt" and t["hi"] is not None
                       and t["lo"] is None))))
        if not ok:
            shape_bad.append(t["label"][:60])
    out.append(Check("scenario_target.satisfies_schema_shape_check",
                     "macro_core.scenario_target", not shape_bad,
                     "rows the ck_macro_scenario_target_shape check would "
                     "reject: %s" % (shape_bad or "none")))

    # Setups satisfy every NOT NULL and the entry_lo <= entry_hi check.
    setup_bad = []
    for r in b.rows["setup"]:
        for col in ("entry_lo", "entry_hi", "invalidation", "invalidation_dir",
                    "target", "name", "week", "asset"):
            if r[col] is None:
                setup_bad.append("%s %s: %s is null" % (r["week"], r["name"][:30], col))
        if (r["entry_lo"] is not None and r["entry_hi"] is not None
                and r["entry_lo"] > r["entry_hi"]):
            setup_bad.append("%s %s: entry_lo > entry_hi" % (r["week"], r["name"][:30]))
    out.append(Check("setup.satisfies_not_null_and_entry_order",
                     "macro_core.setup", not setup_bad,
                     "violations: %s" % (setup_bad or "none")))

    # The rule that derives a missing invalidation side must agree with every
    # side the journals did write.
    from .model import infer_invalidation_dir
    dis = [(r["week"], r["name"][:30], r["invalidation_dir_written"],
            infer_invalidation_dir(r["invalidation"], r["target"]))
           for r in b.rows["setup"] if r["invalidation_dir_written"]
           and infer_invalidation_dir(r["invalidation"], r["target"])
           != r["invalidation_dir_written"]]
    written = sum(1 for r in b.rows["setup"] if r["invalidation_dir_written"])
    out.append(Check("setup.invalidation_side_rule_agrees_with_source",
                     "target above invalidation means long", not dis,
                     "agrees on all %d rows where the journal wrote the side"
                     % written if not dis else "disagreements: %s" % dis))

    # A prior-week cash close carried in `extra` must equal that week's own close.
    by_key: dict[tuple[str, str], set] = defaultdict(set)
    for o in b.rows["observation"]:
        if o["value"] is not None:
            by_key[(o["series_key"], o["asof"])].add(str(o["value"]))
    clash = ["%s @ %s: %s" % (k[0], k[1], sorted(v))
             for k, v in sorted(by_key.items()) if len(v) > 1]
    out.append(Check("observation.week_over_week_cash_closes_agree",
                     "spx_cash, dow_cash, rut_cash and every other series",
                     not clash,
                     "a value carried by two journals for the same date "
                     "disagrees: %s" % (clash or "none")))

    # Every sector_focus_id a setup carries must point at a row this seed
    # writes, because 0003 puts a foreign key on it.
    sf_ids = {r["id"] for r in b.rows["sector_focus"]}
    dangling = ["%s %s" % (r["week"], r["name"][:30]) for r in b.rows["setup"]
                if r["sector_focus_id"] and r["sector_focus_id"] not in sf_ids]
    resolved = sum(1 for r in b.rows["setup"] if r["sector_focus_id"])
    out.append(Check("setup.sector_focus_id_resolves", "macro_core.setup",
                     not dangling,
                     "%d of %d setups name a sector-focus row and every one "
                     "resolves" % (resolved, len(b.rows["setup"]))
                     if not dangling else "dangling: %s" % dangling))

    # graded_at is null exactly for the weeks graded by an archive journal.
    derived = sorted(r["week"] for r in b.rows["week_grade"]
                     if r["graded_at"] is None)
    expected = sorted(r["week"] for r in b.rows["week_grade"]
                      if r["week"] not in config.GRADED_BY)
    out.append(Check("week_grade.graded_at_is_null_where_unknown",
                     "2026-W23 to 2026-W31", derived == expected,
                     "null for %s, which is exactly the set whose grading "
                     "journal is in the archive" % ", ".join(derived)))

    # Every manual condition carries an asserted status, and nothing else does.
    asserted = {r["condition_id"] for r in b.rows["condition_state_asserted"]}
    manual = ({r["id"] for r in b.rows["phase_condition"] if r["kind"] == "manual"}
              | {r["id"] for r in b.rows["trigger_clause"] if r["kind"] == "manual"})
    out.append(Check("condition_state_asserted.covers_every_manual_condition",
                     "macro_core.condition_state_asserted",
                     asserted == manual,
                     "%d rows covering %d phase conditions and %d trigger "
                     "clauses" % (len(asserted),
                                  len(b.rows["phase_condition"]),
                                  len(b.rows["trigger_clause"]))
                     if asserted == manual else
                     "uncovered: %d, extra: %d" % (len(manual - asserted),
                                                   len(asserted - manual))))

    # A phase tracker's asserted status must still agree with the Score line.
    from .model import score_met
    mismatch = []
    by_call = defaultdict(list)
    for r in b.rows["condition_state_asserted"]:
        if r["owner_kind"] == "phase":
            by_call[r["_week"], r["_subject"].split()[0]].append(r["status"])
    for week in config.JUDGMENT_WEEKS:
        for asset in ("equities", "crypto"):
            p = (b.sidecars[week].get("phase") or {}).get(asset) or {}
            met = score_met(p.get("score"))
            got = by_call[(week, asset)].count("met")
            if met is not None and met != got:
                mismatch.append("%s %s: %d vs %d" % (week, asset, met, got))
    out.append(Check("condition_state_asserted.met_count_matches_score_line",
                     "every phase call", not mismatch,
                     "the asserted statuses reproduce every Score line"
                     if not mismatch else "mismatches: %s" % mismatch))

    # Cash indices exist as their own series.
    keys = {o["series_key"] for o in b.rows["observation"]}
    need = {"spx_cash", "dow_cash", "rut_cash"}
    out.append(Check("observation.cash_indices_are_their_own_series",
                     "spx_cash, dow_cash, rut_cash", need <= keys,
                     "missing: %s" % (sorted(need - keys) or "none")))
    return out


def trigger_roundtrip(b: Build) -> list[Check]:
    """Joining a scenario's clauses with '; ' must reproduce its trigger."""
    from .model import split_clauses
    out: list[Check] = []
    for week in config.JUDGMENT_WEEKS:
        for s in (b.sidecars[week].get("house_view") or {}).get("scenarios", []):
            raw = s.get("trigger") or ""
            joined = "; ".join(split_clauses(raw))
            out.append(Check("trigger_clause.split_is_lossless",
                             "%s %s" % (week, s.get("id")),
                             joined == raw.strip(),
                             "clauses rejoin to the trigger verbatim"
                             if joined == raw.strip() else
                             "rejoined %r != %r" % (joined[:80], raw[:80])))
    return out


def all_checks(b: Build) -> tuple[list[Check], dict]:
    cal, got = calibration_check(b)
    checks = [cal, calibration_parity(b)]
    checks += target_oracles(b)
    checks += base_target_oracle(b)
    checks += tracker_oracles(b)
    checks += structural_oracles(b)
    checks += trigger_roundtrip(b)
    return checks, got
