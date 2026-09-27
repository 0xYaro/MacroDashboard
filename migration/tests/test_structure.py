"""Structural facts about the migrated history that a reviewer should not have
to re-derive from the SQL."""
from macro_migrate import config, sources
from macro_migrate.model import (infer_invalidation_dir, norm_direction,
                                 parse_exposure, parse_setup_series,
                                 parse_streak, split_clauses,
                                 split_risk_appetite)


def test_scope_is_exactly_what_decisions_md_says(build):
    weeks = sorted({r["week"] for r in build.rows["scenario_set"]})
    assert weeks == ["2026-W32", "2026-W34", "2026-W35", "2026-W36",
                     "2026-W37", "2026-W38", "2026-W39"]
    graded = sorted(r["week"] for r in build.rows["week_grade"])
    assert graded[0] == "2026-W23"
    assert graded[-1] == "2026-W38"
    assert len(graded) == 15
    assert "2026-W33" not in graded


def test_the_archive_is_not_imported(build):
    for table in ("scenario_set", "phase_call", "strategy", "setup"):
        assert all(r["week"] >= "2026-W32" for r in build.rows[table])


def test_only_the_most_recent_week_is_active(build):
    for table in ("phase_call", "scenario_set", "strategy", "setup"):
        active = {r["week"] for r in build.rows[table] if r["status"] == "active"}
        assert active == {"2026-W39"}, table


def test_the_supersession_chain_is_complete(build):
    for table, key in (("phase_call", "asset"), ("strategy", "asset"),
                       ("scenario_set", None)):
        by_subject = {}
        for r in sorted(build.rows[table], key=lambda r: r["week"]):
            by_subject.setdefault(r.get(key) if key else "", []).append(r)
        for rows in by_subject.values():
            for r in rows[:-1]:
                assert r["superseded_by"] is not None, (table, r["week"])
            assert rows[-1]["superseded_by"] is None


def test_every_teaching_week_is_a_graded_week(build):
    graded = {r["week"] for r in build.rows["week_grade"]}
    for r in build.rows["scenario_rule_week"]:
        assert r["week"] in graded, r


def test_twelve_rules_none_retired(build):
    nums = sorted(r["number"] for r in build.rows["scenario_rule"])
    assert nums == list(range(1, 13))
    assert all(r["retired_week"] is None for r in build.rows["scenario_rule"])


def test_every_setup_ledger_row_became_a_setup(build):
    ledger = sources.read_setup_ledger()
    from_ledger = [r for r in build.rows["setup"] if r["_source"] == "Setup Ledger.md"]
    assert len(ledger) == len(from_ledger) == 18
    # Four ledger rows are still ungraded placeholders, so 14 carry a grade.
    assert len(build.rows["setup_grade"]) == 14


def test_graded_at_is_null_where_the_grading_journal_is_not_read(build):
    nulls = sorted(r["week"] for r in build.rows["week_grade"]
                   if r["graded_at"] is None)
    assert nulls == ["2026-W%d" % n for n in range(23, 32)]
    for r in build.rows["week_grade"]:
        if r["week"] in config.GRADED_BY:
            assert r["graded_at"] == config.AUTHORED_ON[config.GRADED_BY[r["week"]]]


def test_every_manual_condition_has_an_asserted_status(build):
    asserted = {r["condition_id"]: r for r in build.rows["condition_state_asserted"]}
    manual = ({r["id"] for r in build.rows["phase_condition"] if r["kind"] == "manual"}
              | {r["id"] for r in build.rows["trigger_clause"] if r["kind"] == "manual"})
    assert set(asserted) == manual
    assert len(asserted) == 116
    assert all(r["asserted_by"] == "migration" for r in asserted.values())
    triggers = [r for r in asserted.values() if r["owner_kind"] == "trigger"]
    assert len(triggers) == 60
    assert {r["status"] for r in triggers} == {"unevaluable"}


def test_setup_sector_focus_id_points_at_a_row_this_seed_writes(build):
    ids = {r["id"] for r in build.rows["sector_focus"]}
    named = [r for r in build.rows["setup"] if r["sector_focus_id"]]
    assert named
    for r in named:
        assert r["sector_focus_id"] in ids, r["name"]


def test_w36_s2_is_eight_targets_six_of_them_bands(build):
    """The one recorded denominator the parse does not match, and why.

    The 2026-W37 journal counted W36's S2 as 6, which is its band count, and
    W36's S1 as 9, which counts that scenario's three manual legs. `kind` is
    what lets both numbers be right at once.
    """
    from macro_migrate.textutil import uid
    rows = [t for t in build.rows["scenario_target"]
            if t["scenario_id"] == uid("scenario", "2026-W36", "2")]
    assert len(rows) == 8
    assert sum(1 for t in rows if t["kind"] == "band") == 6
    assert sum(1 for t in rows if t["kind"] == "manual") == 2


def test_four_conditions_per_phase_call(build):
    by_call = {}
    for c in build.rows["phase_condition"]:
        by_call[c["phase_call_id"]] = by_call.get(c["phase_call_id"], 0) + 1
    assert set(by_call.values()) == {4}
    assert len(by_call) == len(build.rows["phase_call"]) == 14


def test_three_scenarios_summing_to_100(build):
    by_set = {}
    for s in build.rows["scenario"]:
        by_set.setdefault(s["set_id"], []).append(s)
    assert len(by_set) == 7
    for rows in by_set.values():
        assert len(rows) == 3
        assert sum(r["probability"] for r in rows) == 100


def test_hy_oas_is_basis_points(build):
    hy = {o["asof"]: o["value"] for o in build.rows["observation"]
          if o["series_key"] == "hy_oas_bps"}
    # The 2026-W39 journal writes "2.70% (270 bps)" for the Sep 17 reading.
    assert hy["2026-09-17"] == 270
    assert all(v >= 100 for v in hy.values())


def test_cash_indices_are_their_own_series(build):
    keys = {o["series_key"] for o in build.rows["observation"]}
    assert {"spx_cash", "dow_cash", "rut_cash"} <= keys
    spx = {o["asof"]: o["value"] for o in build.rows["observation"]
           if o["series_key"] == "spx_cash"}
    # 2026-W39's snapshot: ES=F 7,657.35 with cash 7,650.50.
    assert spx["2026-09-18"] == 7650.50


def test_macro_state_rows_are_text(build):
    for o in build.rows["observation"]:
        spec = config.SERIES_YAML_ROWS[o["series_key"]]
        if spec["value_kind"] == "text":
            assert o["value"] is None
            assert o["display"]


def test_every_series_key_is_one_config_series_yaml_declares(build):
    known = set(config.SERIES_YAML_ROWS)
    assert {o["series_key"] for o in build.rows["observation"]} <= known
    assert {t["series_key"] for t in build.rows["scenario_target"]
            if t["series_key"]} <= known


def test_the_fedwatch_defect_is_fixed(build):
    """CONTRACT.md: W39's points file holds two rows both labelled Next FOMC."""
    w39 = [o for o in build.rows["observation"] if o["_week"] == "2026-W39"]
    fomc = [o for o in w39 if o["series_key"] == "next_fomc"]
    watch = [o for o in w39 if o["series_key"] == "fedwatch_next_fomc"]
    assert len(fomc) == 1 and len(watch) == 1
    assert "October 27" in fomc[0]["display"]
    assert "49.1" in watch[0]["display"]


def test_streaks_are_signed_session_counts():
    assert parse_streak("**Two consecutive inflow sessions** (Sep 17-18)") == 2
    assert parse_streak("Four consecutive outflow sessions (Sep 8-11)") == -4
    assert parse_streak("**One inflow session** (Sep 18), after three "
                        "consecutive outflow sessions (Sep 15-17)") == 1
    assert parse_streak("**10 consecutive inflow sessions** (+5)") == 10
    # Prose that names no session count stays null rather than becoming a zero.
    assert parse_streak("**Nine-day run ended Friday**") is None


def test_clause_split_is_lossless(build):
    for week in config.JUDGMENT_WEEKS:
        sc = sources.read_sidecar(week)
        for s in sc["house_view"]["scenarios"]:
            raw = (s.get("trigger") or "").strip()
            assert "; ".join(split_clauses(raw)) == raw, (week, s["id"])


def test_invalidation_side_rule_agrees_with_every_written_side(build):
    written = [r for r in build.rows["setup"] if r["invalidation_dir_written"]]
    assert len(written) >= 15
    for r in written:
        assert infer_invalidation_dir(r["invalidation"], r["target"]) == \
            r["invalidation_dir_written"], r["name"]


def test_exposure_band_and_operated_range():
    assert parse_exposure("**25–50% net**, run at the middle of the band") == \
        (25, 50, None, None)
    lo, hi, olo, ohi = parse_exposure(
        "**50–110% net** per X and Y, operated at **60–70%** now")
    assert (lo, hi, olo, ohi) == (50, 110, 60, 70)


def test_direction_vocabulary():
    assert norm_direction("moved up") == "up"
    assert norm_direction("unchanged") == "unchanged"
    assert norm_direction("valid but weakening") is None


def test_every_active_setup_names_the_series_its_levels_are_on(build):
    """The four 2026-W39 setups used to load with a null series_id, and the
    worker refuses to score a setup that does not say what it is measured on."""
    active = {(r["asset"], r["name"]): r["series_key"]
              for r in build.rows["setup"] if r["status"] == "active"}
    assert active == {
        ("crypto", "BTC $82K acceptance retest (FTR, HTF)"): "btc",
        ("crypto", "ETH breakout-shelf FTR (FTR, HTF)"): "eth",
        ("equities", "Nasdaq failed-push fade (O/U, HTF)"): "nasdaq_cash",
        ("equities", "S&P FOMC-shelf FTR long (FTR, HTF)"): "spx_cash",
    }


def test_a_setup_series_is_read_from_the_title_and_never_guessed():
    """A title that names no instrument, or two, stays null with a sentence."""
    assert parse_setup_series("Nasdaq failed-push fade (O/U, HTF)",
                              "equities")[0] == "nasdaq_cash"
    assert parse_setup_series("S&P FOMC-shelf FTR long (FTR, HTF)",
                              "equities")[0] == "spx_cash"
    assert parse_setup_series("Record-shelf fade (O/U, HTF)", "equities")[0] is None
    # The asset column scopes the table, so a crypto book can never resolve to
    # an equity index however the setup happens to be titled.
    assert parse_setup_series("BTC vs Nasdaq spread", "crypto")[0] == "btc"
    key, why = parse_setup_series("BTC / ETH rotation", "crypto")
    assert key is None and "more than one instrument" in why


def test_every_migrated_setup_series_is_a_config_series_yaml_key(build):
    keys = {r["series_key"] for r in build.rows["setup"] if r["series_key"]}
    assert keys and keys <= set(config.SERIES_YAML_ROWS)


def test_the_state_street_cell_is_cut_into_two_displays():
    """One journal cell, two series, and neither one states the other's figure."""
    allocation, appetite = split_risk_appetite(
        "57.4% (Aug) (−0.13 pp MoM); Risk Appetite Index +0.36 (Jul +0.18)")
    assert allocation == "57.4% (Aug) (−0.13 pp MoM)"
    assert appetite == "+0.36 (Jul +0.18)"

    # The semicolon inside the bracket belongs to the allocation's own aside,
    # and the component split after the index clause stays with the allocation.
    allocation, appetite = split_risk_appetite(
        "58.0% (Jul) (no new release; +1.42 pp MoM); "
        "Risk Appetite Index 0.18 (vs 0.45 Jun); FI 24.9%, cash 17.6%")
    assert allocation == "58.0% (Jul) (no new release; +1.42 pp MoM); FI 24.9%, cash 17.6%"
    assert appetite == "0.18 (vs 0.45 Jun)"

    assert split_risk_appetite("58.0% (Jul) (no new monthly release)") == (
        "58.0% (Jul) (no new monthly release)", None)


def test_neither_state_street_series_displays_the_other(build):
    rows = [r for r in build.rows["observation"]
            if r["series_key"].startswith("state_street_")]
    assert rows
    for r in rows:
        display = r["display"] or ""
        if r["series_key"] == "state_street_risk_appetite":
            assert "%" not in display, display
            assert "Risk Appetite" not in display, display
        else:
            assert "Risk Appetite" not in display, display
