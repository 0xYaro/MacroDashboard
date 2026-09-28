"""Against a real Postgres with the real schema.

The checks that only a database can make: the deferred cardinality triggers that
fire at `COMMIT`, the partial unique index that cannot be deferred, the shape
constraint on a target band, and the foreign key from a rule's teaching week
onto a graded week.

Skipped when no local Postgres is installed. The cluster is created in a
temporary directory, used once and thrown away. Nothing here touches the live
Supabase project, which `docs/DECISIONS.md` pins to one shared instance the
sibling repositories also write.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from macro_agent import authoring, grade
from macro_agent.config import Settings
from macro_agent.model import (
    Event,
    NarrativeObservation,
    ProposedGrade,
    Refused,
    ScenarioGrade,
)
from macro_agent.preflight import Tape
from macro_agent.store import Store, WriteRefused
from macro_agent.validate import attach_reads, phase_reads, proposal
from macro_agent.worker_bridge import registry as load_registry

from .conftest import MIGRATIONS, WORKER_SRC

PORT = 55434
NOW = datetime(2026, 9, 28, 1, 30, tzinfo=UTC)  # Monday 09:30 in Singapore
ASOF = date(2026, 9, 25)

pytestmark = pytest.mark.skipif(
    not (
        shutil.which("initdb")
        and shutil.which("pg_ctl")
        and shutil.which("psql")
        and MIGRATIONS.is_dir()
    ),
    reason="no local Postgres, or supabase/migrations is absent",
)


@pytest.fixture(scope="module")
def cluster():
    # A unix socket path is capped at about 103 bytes, which a pytest tmp path
    # can exceed, so the cluster lives directly under the system temp root and
    # listens on loopback: asyncpg connects over TCP.
    base = Path(tempfile.mkdtemp(prefix="agentpg", dir="/tmp"))
    data = base / "data"
    subprocess.run(
        ["initdb", "-D", str(data), "-U", "postgres", "--encoding=UTF8", "--locale=C"],
        capture_output=True,
        check=True,
    )
    # macOS Postgres refuses to start without a locale in the environment
    # ("postmaster became multithreaded during startup"), and the unattended
    # runner's shell sets none.
    subprocess.run(
        [
            "pg_ctl", "-D", str(data),
            "-o", f"-k {base} -p {PORT} -c listen_addresses=127.0.0.1",
            "-l", str(base / "log"), "start",
        ],
        capture_output=True,
        check=True,
        env={**os.environ, "LC_ALL": "C"},
    )
    for _ in range(40):
        probe = subprocess.run(
            ["psql", "-h", "127.0.0.1", "-p", str(PORT), "-U", "postgres", "-d", "postgres",
             "-tAc", "select 1"],
            capture_output=True,
        )
        if probe.returncode == 0:
            break
        time.sleep(0.25)
    subprocess.run(
        ["psql", "-h", "127.0.0.1", "-p", str(PORT), "-U", "postgres", "-d", "postgres",
         "-c", "create database macro"],
        capture_output=True,
        check=True,
    )

    # Whatever is in the directory, in name order, however many there are.
    # A count pinned here is a count that goes stale the next time a migration
    # lands, and the one that landed while this package was being written is
    # the one that would have been missed.
    files = sorted(MIGRATIONS.glob("*.sql"))
    assert files, f"no migrations found under {MIGRATIONS}"
    # The Supabase roles and auth stubs first, so every migration applies whole
    # and a failing one fails the suite rather than being skipped.
    shim = subprocess.run(
        ["psql", "-h", "127.0.0.1", "-p", str(PORT), "-U", "postgres", "-d", "macro",
         "-v", "ON_ERROR_STOP=1", "-q", "-f",
         str(MIGRATIONS.parent / "tests" / "local_shim.sql")],
        capture_output=True,
        text=True,
    )
    assert shim.returncode == 0, shim.stderr
    for one in files:
        done = subprocess.run(
            ["psql", "-h", "127.0.0.1", "-p", str(PORT), "-U", "postgres", "-d", "macro",
             "-v", "ON_ERROR_STOP=1", "-q", "-f", str(one)],
            capture_output=True,
            text=True,
        )
        assert done.returncode == 0, f"{one.name} failed: {done.stderr}"
    try:
        yield f"postgresql://postgres@127.0.0.1:{PORT}/macro"
    finally:
        subprocess.run(["pg_ctl", "-D", str(data), "-m", "immediate", "stop"], capture_output=True)
        shutil.rmtree(base, ignore_errors=True)


@pytest.fixture(scope="module")
def pg_settings(cluster) -> Settings:
    from .conftest import CONFIG, ROOT

    return Settings(
        database_url=cluster,
        database_ssl="disable",
        supabase_url=None,
        supabase_service_role_key=None,
        vault_path=Path("/nonexistent-vault"),
        series_yaml=CONFIG,
        predicates_md=ROOT / "config" / "predicates.md",
        worker_src=WORKER_SRC,
        log_level="WARNING",
    )


@pytest.fixture
async def store(cluster, pg_settings):
    """A store over a clean `macro_core`, with the 47 series seeded.

    `macro_core.series` is asserted reference data the worker applies from
    `config/series.yaml`; this package never writes it in production, so the
    seed is the test standing in for that run.
    """
    found = await Store.connect(cluster, ssl="disable")
    async with found.transaction() as connection:
        await connection.execute(
            "truncate macro_core.scenario_rule_week, macro_core.scenario_rule, "
            "macro_core.setup_grade, macro_core.week_grade, "
            "macro_core.condition_state_asserted, macro_core.scenario_target, "
            "macro_core.trigger_clause, macro_core.scenario, macro_core.scenario_set, "
            "macro_core.phase_condition, macro_core.setup, macro_core.holdings_check, "
            "macro_core.sector_focus, macro_core.strategy, macro_core.phase_call, "
            "macro_core.observation, macro_core.event, macro_core.catalyst, "
            "macro_quality.gap, macro_quality.finding, macro_quality.ingest_run, "
            "macro_raw.batch, macro_core.run_request cascade"
        )
        for one in load_registry(pg_settings).ordered():
            await connection.execute(
                "insert into macro_core.series (key, label, table_group, ordinal, value_kind, "
                "unit, decimals, cadence, release_rule, replayable, source_name, fetch_path, "
                "tier_floor) values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13) "
                "on conflict (key) do nothing",
                one.key, one.label, one.table_group, one.ordinal, one.value_kind, one.unit,
                one.decimals, one.cadence, one.release_rule.raw, one.replayable, one.source_name,
                one.fetch_path, one.tier_floor,
            )
    try:
        yield found
    finally:
        await found.aclose()


async def _catalysts(store: Store) -> None:
    """The catalysts the W40 fixture's clauses name, as the Monitor would carry them."""
    names = [
        "Oct 6-8 auction cluster",
        "Q2 GDP third estimate and August PCE",
        "Micron Q4 FY2026",
        "September Employment Situation",
    ]
    async with store.transaction() as connection:
        for index, name in enumerate(names):
            await connection.execute(
                "insert into macro_core.catalyst (external_key, name, starts_at, "
                "date_precision, affected, status, first_seen, last_seen) "
                "values ($1,$2,$3,'day','{}','tracked',$4,$4) "
                "on conflict (external_key) do nothing",
                f"key-{index}",
                name,
                datetime(2026, 10, 6, tzinfo=UTC),
                ASOF,
            )


async def _week(store, pg_settings, w40, reads, *, week: str = "2026-W40", now=NOW):
    """Monday in its order: classify the reads first, then propose the week around them."""
    await authoring.classify(
        store,
        pg_settings,
        phase_reads({**reads, "week": week}, classified_at=now),
        week=week,
        asof=ASOF,
        now=now,
    )
    found = attach_reads(proposal({**w40, "week": week}), tuple(await store.proposed_reads(week)))
    return await authoring.propose(store, pg_settings, found, asof=ASOF, tape=Tape(), now=now)


# ---------------------------------------------------------------------------
# A judgment is written whole, or not at all
# ---------------------------------------------------------------------------


async def test_a_scenario_set_written_whole_commits(store, pg_settings, w40, reads):
    await _catalysts(store)
    written = await _week(store, pg_settings, w40, reads)
    rows = await store.scenario_set_for_week("2026-W40")
    assert rows is not None
    assert rows["status"] == "proposed", "nothing is activated by the run"
    assert [one["probability"] for one in rows["scenarios"]] == [45, 32, 23]
    assert len(written.phase_call_ids) == 2
    assert len(written.setup_ids) == 4
    assert len(written.strategy_ids) == 2


async def test_a_partial_scenario_set_is_refused_and_leaves_nothing_behind(store, pg_settings):
    """The deferred trigger fires at COMMIT, so a two-scenario set never lands.

    Written through the store directly, bypassing this package's own check, so
    that what is being tested is the database's refusal and not ours.
    """
    from macro_agent.model import ScenarioProposal, ScenarioSetProposal, TargetProposal

    partial = ScenarioSetProposal(
        week="2026-W41",
        synthesis="two branches",
        probability_check="",
        swing_variables=[],
        rules_waived=(),
        authored_at=NOW,
        scenarios=(
            ScenarioProposal(
                ordinal=1, title="one", probability=60, positioning=None, clauses=(),
                targets=(TargetProposal(label="VIX", kind="band", series_key="vix",
                                        comparator="between", lo=Decimal(13), hi=Decimal(19)),),
            ),
            ScenarioProposal(
                ordinal=2, title="two", probability=40, positioning=None, clauses=(),
                targets=(TargetProposal(label="VIX", kind="band", series_key="vix",
                                        comparator="between", lo=Decimal(20), hi=Decimal(30)),),
            ),
        ),
    )
    with pytest.raises(WriteRefused) as raised:
        await store.write_scenario_set(partial)
    assert "exactly 3" in str(raised.value)
    assert "written whole in one transaction" in raised.value.sentence

    # The head, the two scenarios and their targets all rolled back together.
    assert await store.scenario_set_for_week("2026-W41") is None
    assert await store.target_rows_for_week("2026-W41") == []


async def test_probabilities_that_do_not_sum_to_100_are_refused_by_the_database(store):
    from macro_agent.model import ScenarioProposal, ScenarioSetProposal

    def scenario(ordinal: int, probability: int) -> ScenarioProposal:
        return ScenarioProposal(
            ordinal=ordinal, title=f"s{ordinal}", probability=probability,
            positioning=None, clauses=(), targets=(),
        )

    with pytest.raises(WriteRefused, match="sum to 98"):
        await store.write_scenario_set(
            ScenarioSetProposal(
                week="2026-W41", synthesis="", probability_check="", swing_variables=[],
                rules_waived=(), authored_at=NOW,
                scenarios=(scenario(1, 45), scenario(2, 30), scenario(3, 23)),
            )
        )
    assert await store.scenario_set_for_week("2026-W41") is None


async def test_a_phase_call_with_three_conditions_is_refused(store, reads):
    """Exactly four things a read holds on, enforced by the database as well."""
    reads["phase_calls"][0]["conditions"] = reads["phase_calls"][0]["conditions"][:3]
    call = phase_reads(reads)[0]
    with pytest.raises(WriteRefused, match="exactly 4 are required"):
        await store.write_phase_call(call)
    assert await store.active_phase_calls() == []


async def test_a_band_target_that_names_no_series_is_refused_by_the_schema(store):
    """Belt and braces: `validate` refuses it first, and so does the CHECK."""
    from macro_agent.model import ScenarioProposal, ScenarioSetProposal, TargetProposal

    bad = object.__new__(TargetProposal)
    object.__setattr__(bad, "label", "S&P 500")
    object.__setattr__(bad, "kind", "band")
    object.__setattr__(bad, "series_key", None)
    object.__setattr__(bad, "comparator", "between")
    object.__setattr__(bad, "lo", Decimal(7500))
    object.__setattr__(bad, "hi", Decimal(7800))
    object.__setattr__(bad, "text", None)

    def scenario(ordinal: int, probability: int, targets=()) -> ScenarioProposal:
        return ScenarioProposal(
            ordinal=ordinal, title=f"s{ordinal}", probability=probability,
            positioning=None, clauses=(), targets=targets,
        )

    with pytest.raises(WriteRefused):
        await store.write_scenario_set(
            ScenarioSetProposal(
                week="2026-W41", synthesis="", probability_check="", swing_variables=[],
                rules_waived=(), authored_at=NOW,
                scenarios=(scenario(1, 40, (bad,)), scenario(2, 35), scenario(3, 25)),
            )
        )


# ---------------------------------------------------------------------------
# targets_n
# ---------------------------------------------------------------------------


async def test_targets_n_counts_both_band_and_manual_rows(store, pg_settings, w40):
    """`targets hit x of n`: n is the row count, full stop.

    W39's S1 carried thirteen numeric bands plus one written leg and the grading
    counted eleven across all of them. In W37's grading block the same grader
    counted one scenario's targets including its manual legs and the next
    scenario's excluding them, which is two conventions in one paragraph.
    """
    await _catalysts(store)
    await authoring.propose(store, pg_settings, proposal(w40), asof=ASOF, tape=Tape(), now=NOW)

    rows = await store.target_rows_for_week("2026-W40")
    assert len(rows) == 41

    per_scenario: dict[int, list[str]] = {}
    for one in rows:
        per_scenario.setdefault(one["scenario_ordinal"], []).append(one["kind"])

    assert {ordinal: len(kinds) for ordinal, kinds in per_scenario.items()} == {1: 14, 2: 13, 3: 14}
    bands = {
        ordinal: sum(1 for kind in kinds if kind == "band")
        for ordinal, kinds in per_scenario.items()
    }
    assert bands == {1: 13, 2: 10, 3: 13}
    # Two denominators, and they differ. Conflating them is the W36 defect.
    assert bands[2] != len(per_scenario[2])


async def test_the_grading_denominator_is_the_row_count(store, pg_settings, w40):
    """End to end: the number the ledger records comes from the row count."""
    await _catalysts(store)
    await authoring.propose(store, pg_settings, proposal(w40), asof=ASOF, tape=Tape(), now=NOW)
    stored = await store.scenario_set_for_week("2026-W40")
    assert stored is not None

    row, findings = grade.close_the_week(
        settings=pg_settings,
        week="2026-W40",
        stored_set=stored,
        grades=[
            ScenarioGrade(scenario_id=one["scenario_id"], grade=g, note=note)
            for one, g, note in zip(
                stored["scenarios"],
                ("Partial", "Not", "Not"),
                (
                    "",
                    "Not one trigger clause fired, yet the four bands S1 missed all landed here.",
                    "The 20Y tailed at a record, but the trigger was a conjunction and no "
                    "confirming leg fired for a seventh week.",
                ),
                strict=True,
            )
        ],
        history_rows=[],
        open_gaps=[],
        asof=ASOF,
        eq_call="valid",
        crypto_call="valid",
        key_miss="the mechanism was right and the map split in two",
        graded_at=NOW,
    )
    assert not findings
    assert row.base_scenario_label.startswith('S1 "Few stocks rise')
    # S1 is the base at 45%: fourteen targets, thirteen bands and one written leg.
    assert row.targets_n == 14
    # Nothing was measured, because no observations were loaded.
    assert row.targets_hit == 0
    # Brier from the worker's own arithmetic: p = (0.45, 0.32, 0.23),
    # weights (0.5, 0, 0) normalised to (1, 0, 0).
    assert row.brier == (
        (Decimal("0.45") - 1) ** 2 + Decimal("0.32") ** 2 + Decimal("0.23") ** 2
    )

    # other_grades is an array of {"id","prob","grade","note"}, in scenario
    # order, excluding the base case. An object here parses as a malformed row
    # in the web app and empties the calibration route entirely.
    assert isinstance(row.other_grades, list)
    assert [one["id"] for one in row.other_grades] == ["S2", "S3"]
    assert [one["prob"] for one in row.other_grades] == [32, 23]
    assert [one["grade"] for one in row.other_grades] == ["Not", "Not"]
    assert row.other_grades[0]["note"].startswith("Not one trigger clause fired")
    # The base case is excluded on purpose: its grading reasoning is key_miss.
    assert not any(one["id"] == "S1" for one in row.other_grades)

    await store.write_week_grade(row)
    ledger = await store.ledger()
    assert [one.week for one in ledger] == ["2026-W40"]
    assert ledger[0].targets_n == 14
    # The array survives the round trip through jsonb, still an array.
    assert isinstance(ledger[0].other_grades, list)
    assert [one["id"] for one in ledger[0].other_grades] == ["S2", "S3"]
    assert grade.calibration_read(pg_settings, ledger)["graded_weeks"] == 1


async def test_a_blank_grading_note_is_reported_rather_than_invented(
    store, pg_settings, w40
):
    """A generated sentence that reads like a human one is worse than a blank.

    The ledger's value is that its sentences were written by someone who thought
    about the week, so nothing here writes one. The blank is surfaced instead.
    """
    await _catalysts(store)
    await authoring.propose(store, pg_settings, proposal(w40), asof=ASOF, tape=Tape(), now=NOW)
    stored = await store.scenario_set_for_week("2026-W40")
    assert stored is not None

    row, findings = grade.close_the_week(
        settings=pg_settings,
        week="2026-W40",
        stored_set=stored,
        grades=[
            ScenarioGrade(scenario_id=one["scenario_id"], grade="Not", note="")
            for one in stored["scenarios"]
        ],
        history_rows=[],
        open_gaps=[],
        asof=ASOF,
        eq_call="valid",
        crypto_call="valid",
        key_miss="",
        graded_at=NOW,
    )
    assert [one["note"] for one in row.other_grades] == ["", ""]
    assert any("S2, S3 graded without a reason" in one for one in findings)


async def test_a_note_on_the_base_case_is_flagged_because_key_miss_is_where_it_goes(
    store, pg_settings, w40
):
    """The base case is excluded from `other_grades` on purpose, not by oversight.

    Its grading reasoning is what `key_miss` is, so a sentence supplied on the
    base judgment and absent from `key_miss` is stored nowhere at all.
    """
    await _catalysts(store)
    await authoring.propose(store, pg_settings, proposal(w40), asof=ASOF, tape=Tape(), now=NOW)
    stored = await store.scenario_set_for_week("2026-W40")
    assert stored is not None
    base = stored["scenarios"][0]["scenario_id"]

    _row, findings = grade.close_the_week(
        settings=pg_settings,
        week="2026-W40",
        stored_set=stored,
        grades=[
            ScenarioGrade(
                scenario_id=one["scenario_id"],
                grade="Partial",
                note="The mechanism landed and the map split in two."
                if one["scenario_id"] == base
                else "a reason",
            )
            for one in stored["scenarios"]
        ],
        history_rows=[],
        open_gaps=[],
        asof=ASOF,
        eq_call="valid",
        crypto_call="valid",
        key_miss="Something else entirely.",
        graded_at=NOW,
    )
    assert any("its reasoning is what key_miss is" in one for one in findings)

    # Folded into key_miss, there is nothing to say.
    _row, quiet = grade.close_the_week(
        settings=pg_settings,
        week="2026-W40",
        stored_set=stored,
        grades=[
            ScenarioGrade(
                scenario_id=one["scenario_id"],
                grade="Partial",
                note="The mechanism landed and the map split in two."
                if one["scenario_id"] == base
                else "a reason",
            )
            for one in stored["scenarios"]
        ],
        history_rows=[],
        open_gaps=[],
        asof=ASOF,
        eq_call="valid",
        crypto_call="valid",
        key_miss="The mechanism landed and the map split in two. R5 again.",
        graded_at=NOW,
    )
    assert not quiet


# ---------------------------------------------------------------------------
# Activation
# ---------------------------------------------------------------------------


async def test_activation_supersedes_the_outgoing_row_first(store, pg_settings, w40, reads):
    """One transaction, fixed order. The partial unique index cannot be deferred."""
    await _catalysts(store)
    first = await _week(store, pg_settings, w40, reads)
    await store.activate(
        "phase_call",
        incoming_id=first.phase_call_ids["equities"],
        outgoing_id=None,
        activated_at=NOW,
    )
    assert await store.active_row_id("phase_call", asset="equities") == first.phase_call_ids[
        "equities"
    ]

    second = await _week(store, pg_settings, w40, reads, week="2026-W41")
    outgoing = await store.active_row_id("phase_call", asset="equities")
    await store.activate(
        "phase_call",
        incoming_id=second.phase_call_ids["equities"],
        outgoing_id=outgoing,
        activated_at=NOW,
    )

    calls = await store.active_phase_calls()
    equities = [one for one in calls if one["asset"] == "equities"]
    assert len(equities) == 1, "at most one active per subject"
    assert equities[0]["week"] == "2026-W41"


async def test_two_active_rows_for_one_subject_are_impossible(store, pg_settings, w40, reads):
    """Activating without superseding is the order the index rejects."""
    await _catalysts(store)
    first = await _week(store, pg_settings, w40, reads)
    second = await _week(store, pg_settings, w40, reads, week="2026-W41")
    await store.activate(
        "phase_call", incoming_id=first.phase_call_ids["crypto"], outgoing_id=None,
        activated_at=NOW,
    )
    with pytest.raises(WriteRefused):
        await store.activate(
            "phase_call", incoming_id=second.phase_call_ids["crypto"], outgoing_id=None,
            activated_at=NOW,
        )
    assert len(await store.active_phase_calls()) == 1


async def test_the_run_does_not_activate_anything(store, pg_settings, w40, reads):
    """The single human review gate. A run that stood up its own proposal has no review in it."""
    await _catalysts(store)
    await _week(store, pg_settings, w40, reads)
    assert await store.active_phase_calls() == []
    stored = await store.scenario_set_for_week("2026-W40")
    assert stored is not None and stored["activated_at"] is None


async def test_only_a_judgment_table_can_be_activated(store):
    with pytest.raises(WriteRefused, match="not a judgment table"):
        await store.activate(
            "week_grade", incoming_id="x", outgoing_id=None, activated_at=NOW
        )


# ---------------------------------------------------------------------------
# Catalyst binding
# ---------------------------------------------------------------------------


async def test_a_clause_that_names_a_catalyst_is_bound_to_its_id(store, pg_settings, w40):
    """This is what makes premise-moved detection work."""
    await _catalysts(store)
    await authoring.propose(store, pg_settings, proposal(w40), asof=ASOF, tape=Tape(), now=NOW)
    stored = await store.scenario_set_for_week("2026-W40")
    assert stored is not None
    bound = [
        clause
        for scenario in stored["scenarios"]
        for clause in scenario["clauses"]
        if clause["catalyst_id"] is not None
    ]
    # Derived from the fixture, not pinned: retyping a clause can move it off a
    # catalyst, and it did when the FedWatch odds stopped needing one.
    wanted = sum(
        1
        for scenario in w40["scenario_set"]["scenarios"]
        for clause in scenario["clauses"]
        if str(clause.get("catalyst_id") or "").startswith("CATALYST:")
    )
    assert wanted
    assert len(bound) == wanted
    by_name = await store.catalysts_by_name()
    assert {one["catalyst_id"] for one in bound} <= set(by_name.values())


async def test_a_clause_naming_a_catalyst_nobody_tracks_is_refused(store, pg_settings, w40):
    await _catalysts(store)
    w40["scenario_set"]["scenarios"][0]["clauses"][0]["catalyst_id"] = "CATALYST:A summit nobody called"
    with pytest.raises(Refused, match="A summit nobody called"):
        await authoring.propose(store, pg_settings, proposal(w40), asof=ASOF, tape=Tape(), now=NOW)
    assert await store.scenario_set_for_week("2026-W40") is None


# ---------------------------------------------------------------------------
# Mode A against the real table
# ---------------------------------------------------------------------------


async def test_the_thirteen_agent_series_are_written_and_replay_is_safe(store):
    ids = await store.series_ids()
    rows = [
        NarrativeObservation(
            series_key="cnn_fear_greed", asof=ASOF, value=Decimal(29), display="29 (Fear)",
            tier=1, source="search agent (equities): MacroMicro", observed_at=NOW,
        ),
        NarrativeObservation(
            series_key="btc_etf_inflow_streak", asof=ASOF, value=Decimal(-4),
            display="a fourth consecutive outflow session", tier=2,
            source="search agent (crypto-flows): SoSoValue", observed_at=NOW,
        ),
        NarrativeObservation(
            series_key="fed_funds", asof=ASOF, value=None,
            display="3.75%-4.00% after a unanimous 12-0 hike on Sep 16", tier=1,
            source="search agent (macro): FOMC statement", observed_at=NOW, value_kind="text",
        ),
        NarrativeObservation(
            series_key="liquidations_24h", asof=ASOF, value=None, display="—", tier=4,
            source="search agent (crypto-flows): no source found", observed_at=NOW,
            note="check Coinglass coverage for the 24h total",
        ),
    ]
    assert await store.write_observations(rows, ids, received_at=NOW) == 4
    assert await store.write_observations(rows, ids, received_at=NOW) == 4

    latest = await store.latest_agent_observations(
        ["cnn_fear_greed", "btc_etf_inflow_streak", "fed_funds", "liquidations_24h"]
    )
    assert latest["btc_etf_inflow_streak"]["value"] == "-4"
    assert latest["fed_funds"]["value"] is None
    assert latest["fed_funds"]["display"].startswith("3.75%")
    assert latest["liquidations_24h"]["value"] is None
    assert latest["liquidations_24h"]["note"]

    history = await store.history_rows(since=date(2026, 9, 1))
    assert len(history) == 4, "replaying the run upserts rather than duplicating"


async def test_the_event_log_restates_rather_than_grows_on_a_replay(store):
    """The one table a replay would otherwise grow. `macro_core.event` has no declared key."""
    events = [
        Event(
            occurred_on=date(2026, 9, 16),
            kind="macro",
            text="The FOMC hiked 25 bp unanimously. It removes the hike-now-cut-later escape "
            "hatch equities had been leaning on.",
            assets=("rates", "equities"),
            surprise=False,
            source="Fed statement",
        ),
        Event(
            occurred_on=date(2026, 9, 18),
            kind="structure",
            text="BTC broke $80,000 to $81,911 on a $433M Friday ETF session. The move cannot be "
            "read as a rates trade.",
            assets=("crypto",),
            surprise=True,
        ),
    ]
    assert await store.write_events(events) == 2
    assert await store.write_events(events) == 2
    count = await store.history_rows(since=date(2026, 9, 1))
    del count
    async with store.transaction() as connection:
        total = await connection.fetchval("select count(*) from macro_core.event")
    assert total == 2


async def test_a_permanent_gap_is_opened_once_and_never_re_dated(store):
    await store.write_gaps(
        [("liquidations_24h", ASOF, "the 09:30 search agent returned no usable reading", True)]
    )
    await store.write_gaps([("liquidations_24h", ASOF, "a different reason", True)])
    gaps = await store.open_gaps(since=date(2026, 9, 1))
    assert gaps == [("liquidations_24h", ASOF)]


# ---------------------------------------------------------------------------
# The ledger's own foreign key
# ---------------------------------------------------------------------------


async def test_a_rule_cannot_cite_a_week_nobody_graded(store):
    await store.add_rule(13, "A new pattern seen twice.", [])
    with pytest.raises(WriteRefused):
        await store.add_rule_week(13, "2026-W40")

    await store.write_week_grade(
        ProposedGrade(
            week="2026-W40",
            base_scenario_id=None,
            base_scenario_label='S1 "Narrow and high-yielding" (45%)',
            base_grade="Partial",
            other_grades={},
            eq_call="valid",
            crypto_call="valid",
            key_miss="",
            targets_hit=7,
            targets_n=14,
            brier=Decimal("0.4"),
            premise_moved=False,
            graded_at=NOW,
        )
    )
    await store.add_rule_week(13, "2026-W40")
    rules = await store.rules()
    assert rules[-1]["number"] == 13
    assert rules[-1]["weeks"] == ["2026-W40"]
    assert rules[-1]["fire_count"] == 1


async def test_the_next_rule_number_never_reuses_one(store):
    await store.add_rule(12, "R12 as migrated.", [])
    assert await store.next_rule_number() == 13


# ---------------------------------------------------------------------------
# Mode A, end to end
# ---------------------------------------------------------------------------


async def test_the_narrative_run_writes_the_board_and_says_what_it_could_not(
    store, pg_settings
):
    """One pass of mode A over a return that is partly good and partly missing.

    Three of the owned series come back: two numerics and a prose row. The
    rest do not, and every one of them is a permanent gap with a sentence
    beside it, because every agent-owned series is `replayable: false`.
    """
    from macro_agent import narrative

    returns = {
        "crypto-flows": [
            {
                "series": "liquidations_24h",
                "value": "",
                "display": "",
                "tier": 4,
                "asof": "2026-09-25",
                "released": "",
                "note": "check CoinDesk coverage quoting Coinglass",
            },
        ],
        "macro": [
            {
                "series": "ism_services",
                "value": "55.4",
                "display": "55.4, above the 54.3 consensus",
                "tier": 2,
                "asof": "2026-08-31",
                "released": "2026-09-03",
                "source": "ISM Report On Business",
            },
            {
                "series": "ism_manufacturing",
                "value": "49.1",
                "display": "49.1, a seventh month of contraction",
                "tier": 1,
                "asof": "2026-08-31",
                "released": "2026-09-01",
                "source": "ISM Report On Business",
            },
            {
                "series": "fed_funds",
                "value": "",
                "display": "3.75%-4.00% after a unanimous 12-0 hike on Sep 16",
                "tier": 1,
                "asof": "2026-09-25",
                "released": "",
                "source": "FOMC statement",
            }
        ],
    }
    events = [
        {
            "date": "2026-09-24",
            "kind": "macro",
            "whatHappened": "The Senior Credit Officer survey showed dealer financing terms "
            "tightening.",
            "whyItMatters": "It is the only scheduled read on whether private-credit leverage "
            "is tightening.",
            "assets": ["rates", "credit"],
            "surprise": True,
        }
    ]

    result = await narrative.apply(
        store, pg_settings, returns=returns, event_rows=events, asof=ASOF, now=NOW
    )

    owned = set(narrative.agent_series(pg_settings))
    # `fed_funds` is prose: it carries `value` null by contract, and it landed.
    # Landing is decided on the tier, not on the value.
    landed = {"ism_services", "ism_manufacturing", "fed_funds"}

    assert result["observations_written"] == 4
    assert result["events_written"] == 1
    assert not result["refusals"]
    assert result["tier_4"] == ["liquidations_24h"]
    # Everything the registry owns that did not land a value is gone, and
    # liquidations_24h counts among them although it wrote a row: a Tier 4 null
    # is the same fact said out loud. The expected set is derived rather than
    # counted, because the owned set grew from thirteen to sixteen once already.
    assert set(result["series_missing"]) == owned - landed
    assert "liquidations_24h" in result["series_missing"]
    assert "ism_services" not in result["series_missing"]
    assert "fed_funds" not in result["series_missing"]

    gaps = dict(await store.open_gaps(since=date(2026, 9, 1)))
    assert "liquidations_24h" in gaps
    assert "ism_services" not in gaps

    async with store.transaction() as connection:
        runs = await connection.fetch(
            "select source, run_kind, status, rows from macro_quality.ingest_run"
        )
        batches = await connection.fetchval(
            "select count(*) from macro_raw.batch where kind = 'search'"
        )
        findings = await connection.fetchval(
            "select count(*) from macro_quality.finding where check_key = "
            "'narrative_series_missing'"
        )
    assert [(one["source"], one["run_kind"], one["status"], one["rows"]) for one in runs] == [
        ("agent_0930", "full", "ok", 5)
    ]
    # One per category that returned (macro and crypto-flows) plus the events
    # return, so a narrative point can be traced back to the text it came from.
    assert batches == 3
    assert findings == len(owned - landed)


async def test_replaying_the_narrative_run_restates_rather_than_grows(store, pg_settings):
    from macro_agent import narrative

    returns = {
        "macro": [
            {
                "series": "ism_services",
                "value": "55.4",
                "display": "55.4, above the 54.3 consensus",
                "tier": 2,
                "asof": "2026-08-31",
                "released": "2026-09-03",
                "source": "ISM Report On Business",
            }
        ]
    }
    events = [
        {
            "date": "2026-09-24",
            "kind": "structure",
            "whatHappened": "MU rose 5.50% on the Intel capacity warning.",
            "whyItMatters": "The buyer side is confirming the shortage.",
            "assets": ["semis"],
        }
    ]
    for _ in range(2):
        await narrative.apply(
            store, pg_settings, returns=returns, event_rows=events, asof=ASOF, now=NOW
        )
    async with store.transaction() as connection:
        assert await connection.fetchval("select count(*) from macro_core.observation") == 1
        assert await connection.fetchval("select count(*) from macro_core.event") == 1
        # One per owned series that did not land, opened once and never re-dated.
        owned = set(narrative.agent_series(pg_settings))
        assert await connection.fetchval(
            "select count(*) from macro_quality.gap"
        ) == len(owned) - 1


async def test_a_plained_prose_row_shows_the_rewrite_and_archives_the_original(
    store, pg_settings
):
    """A3b: `display` carries the plain sentence, the batch the search agent's own."""
    import json

    from macro_agent import narrative

    agent = "3.75%-4.00% after a hawkish 12-0 hold on Sep 16"
    plain = "The Fed kept its rate at 3.75%-4.00% on Sep 16. All 12 voted for it, 0 against."
    lossy = "Tariffs stay where they were."
    returns = {
        "macro": [
            {
                "series": "fed_funds",
                "value": "",
                "display": plain,
                "display_original": agent,
                "tier": 1,
                "asof": "2026-09-25",
                "source": "FOMC statement",
            },
            {
                "series": "tariff_state",
                "value": "",
                "display": lossy,
                "display_original": "Section 232 at 25% on steel, unchanged since Jun 4",
                "tier": 2,
                "asof": "2026-09-25",
                "source": "Tariff tracker",
            },
        ]
    }
    result = await narrative.apply(
        store, pg_settings, returns=returns, event_rows=[], asof=ASOF, now=NOW
    )

    # The lossy rewrite is refused by name, and the agent's sentence lands instead.
    assert len(result["refusals"]) == 1
    assert "tariff_state: the plain rewrite dropped 232, 25%, 4" in result["refusals"][0]
    async with store.transaction() as connection:
        shown = dict(
            await connection.fetch(
                "select s.key, o.display from macro_core.observation o "
                "join macro_core.series s on s.id = o.series_id"
            )
        )
        payload = await connection.fetchval(
            "select payload from macro_raw.batch where source = 'agent_0930:macro'"
        )
    assert shown["fed_funds"] == plain
    assert shown["tariff_state"] == "Section 232 at 25% on steel, unchanged since Jun 4"

    archived = json.loads(payload) if isinstance(payload, str) else payload
    by_key = {one["series"]: one for one in archived["points"]}
    assert by_key["fed_funds"]["display"] == agent
    assert "display_original" not in by_key["fed_funds"]
    assert archived["plain"] == {"fed_funds": plain}


# ---------------------------------------------------------------------------
# The command line, end to end
# ---------------------------------------------------------------------------


async def test_the_cli_classifies_then_reveals_then_proposes_then_preflights(
    store, pg_settings, cluster, w40, reads, tmp_path, capsys
):
    """The commands `SKILL.md` runs on a Monday, in order, against a real database.

    Driven through `cli` rather than through the modules, because the runbook
    tells the operator to run commands and a module that works behind a command
    that does not is a runbook nobody can follow.

    The order is the point. The full context is refused until the fresh reads
    are written, serving it stamps the reveal, and after the reveal the reads
    cannot be rewritten.
    """
    import argparse
    import json

    from macro_agent import cli

    await _catalysts(store)
    # Last week's calls, which the reads must not see and the reveal shows.
    last = await _week(store, pg_settings, w40, reads, week="2026-W39")
    for asset in ("equities", "crypto"):
        await store.activate(
            "phase_call", incoming_id=last.phase_call_ids[asset], outgoing_id=None,
            activated_at=NOW,
        )
    capsys.readouterr()

    context_args = argparse.Namespace(week="2026-W40", graded_week="2026-W39", amend=False)
    assert await cli.author_context_cmd(context_args, pg_settings) == 1
    refused = json.loads(capsys.readouterr().out)
    assert "author classify" in refused["refusals"][0]

    # B1: the blind context, then the reads.
    assert await cli.author_phase_context_cmd(
        argparse.Namespace(week="2026-W40"), pg_settings
    ) == 0
    blind = json.loads(capsys.readouterr().out)
    assert "phase_calls" not in blind and "ledger" not in blind
    text = json.dumps(blind)
    assert "Internals deteriorated" not in text
    assert last.week == "2026-W39"

    reads_file = tmp_path / "phase.json"
    reads_file.write_text(json.dumps(reads), encoding="utf-8")
    classify_args = argparse.Namespace(week="2026-W40", input=str(reads_file), asof="2026-09-25")
    assert await cli.author_classify_cmd(classify_args, pg_settings) == 0
    classified = json.loads(capsys.readouterr().out)
    assert set(classified["phase_call_ids"]) == {"equities", "crypto"}
    assert "last_week_phase" not in json.dumps(classified), "the prior is not shown here"
    # Before the reveal a read may be written again, and replaces itself.
    assert await cli.author_classify_cmd(classify_args, pg_settings) == 0
    assert json.loads(capsys.readouterr().out)["replaced_unrevealed_reads"] == 2

    # B2: the reveal.
    assert await cli.author_context_cmd(context_args, pg_settings) == 0
    full_context = json.loads(capsys.readouterr().out)
    moves = {one["asset"]: one["move"] for one in full_context["phase_reads"]}
    assert moves == {"equities": "same as last week", "crypto": "same as last week"}
    assert {one["week"] for one in full_context["phase_calls"]} == {"2026-W39"}
    async with store.transaction() as connection:
        order = await connection.fetch(
            "select classified_at <= revealed_at as in_order from macro_core.phase_call "
            "where week = '2026-W40' and status = 'proposed'"
        )
    assert [one["in_order"] for one in order] == [True, True]
    # After the reveal the reads are fixed.
    assert await cli.author_classify_cmd(classify_args, pg_settings) == 1
    assert "would not be fresh" in json.loads(capsys.readouterr().out)["refusals"][0]

    # B4: the rest of the week, with the change notes.
    proposal_file = tmp_path / "proposal.json"
    proposal_file.write_text(json.dumps(w40), encoding="utf-8")
    code = await cli.author_propose_cmd(
        argparse.Namespace(input=str(proposal_file), asof="2026-09-25"), pg_settings
    )
    assert code == 0
    written = json.loads(capsys.readouterr().out)
    assert written["status"] == "proposed"
    assert set(written["phase_call_ids"]) == {"equities", "crypto"}
    rules = [one for one in written["preflight"] if one["key"].startswith("r")]
    assert len(rules) == 12
    assert all(one["marker"] == "pass" for one in rules)

    # The phase read rides in the same report and is not one of the twelve.
    phase = [
        one for one in written["preflight"] if not one["key"].startswith(("r", "voice_"))
    ]
    # The voice lines ride along too, warn-only; the fixture reads in the voice.
    assert [one["marker"] for one in written["preflight"] if one["key"].startswith("voice_")] == [
        "pass"
    ]
    assert {one["key"] for one in phase} == {
        "equities_transition", "equities_holds_today", "crypto_transition", "crypto_holds_today"
    }
    same = next(one for one in phase if one["key"] == "equities_transition")
    assert "Same as last week in 2026-W39" in same["sentence"]
    async with store.transaction() as connection:
        notes = await connection.fetch(
            "select asset, change_note from macro_core.phase_call "
            "where week = '2026-W40' and status = 'proposed' order by asset"
        )
    assert all(one["change_note"].startswith("Same Phase") for one in notes)

    # B2, against the set just written.
    stored = await store.scenario_set_for_week("2026-W40")
    assert stored is not None
    grades_file = tmp_path / "grades.json"
    grades_file.write_text(
        json.dumps(
            {
                "scenario_grades": [
                    {"scenario_id": one["scenario_id"], "grade": g, "note": note}
                    for one, g, note in zip(
                        stored["scenarios"],
                        ("Materialized", "Not", "Not"),
                        ("", "Not one clause fired and every band missed.", ""),
                        strict=True,
                    )
                ],
                "eq_call": "valid",
                "crypto_call": "valid",
                "key_miss": "the map split in two",
                "new_rules": [{"text": "A new pattern seen twice.", "weeks": []}],
            }
        ),
        encoding="utf-8",
    )
    code = await cli.author_grade_cmd(
        argparse.Namespace(
            graded_week="2026-W40", input=str(grades_file), asof="2026-09-25"
        ),
        pg_settings,
    )
    assert code == 0
    graded = json.loads(capsys.readouterr().out)
    assert graded["base_grade"] == "Materialized"
    assert graded["targets_n"] == 14
    assert graded["calibration"]["graded_weeks"] == 1
    assert graded["ledger_changes"] == ["R1 added"]
    assert isinstance(graded["voice"], list), "the grading prose is voice-checked, warn-only"

    # B5 on its own.
    code = await cli.preflight_cmd(
        argparse.Namespace(input=str(proposal_file), asof="2026-09-25", text=True, phase=None),
        pg_settings,
    )
    assert code == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 17, "twelve rules, two lines per book and the voice line"
    assert lines[-1].startswith("pass  Voice: all 63 reader-facing fields")


async def test_the_cli_refuses_a_proposal_with_no_fresh_read(
    store, pg_settings, w40, tmp_path, capsys
):
    """Monday's first step is the read. A week proposed without one is refused."""
    import argparse
    import json

    from macro_agent import cli

    await _catalysts(store)
    path = tmp_path / "proposal.json"
    path.write_text(json.dumps(w40), encoding="utf-8")
    code = await cli.author_propose_cmd(
        argparse.Namespace(input=str(path), asof="2026-09-25"), pg_settings
    )
    assert code == 1
    assert "no fresh phase read" in json.loads(capsys.readouterr().out)["refusals"][0]
    assert await store.scenario_set_for_week("2026-W40") is None


async def test_the_cli_refuses_a_bad_proposal_without_writing_anything(
    store, pg_settings, w40, reads, tmp_path, capsys
):
    import argparse
    import json

    from macro_agent import cli

    await _catalysts(store)
    await authoring.classify(
        store, pg_settings, phase_reads(reads), week="2026-W40", asof=ASOF, now=NOW
    )
    # `author context` has run: propose is refused before the reveal.
    await store.reveal_reads("2026-W40", NOW)
    w40["scenario_set"]["scenarios"][1]["probability"] = 30
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(w40), encoding="utf-8")

    code = await cli.author_propose_cmd(
        argparse.Namespace(input=str(bad), asof="2026-09-25"), pg_settings
    )
    assert code == 1
    out = json.loads(capsys.readouterr().out)
    assert any("= 98" in one for one in out["refusals"])
    assert await store.scenario_set_for_week("2026-W40") is None


# ---------------------------------------------------------------------------
# A fresh read survives the round trip
# ---------------------------------------------------------------------------


async def test_a_fresh_read_is_stored_with_last_weeks_phase_filled_in(
    store, pg_settings, w40, reads
):
    """The prior comes from the active call, never from the read."""
    await _catalysts(store)
    last = await _week(store, pg_settings, w40, reads, week="2026-W39")
    await store.activate(
        "phase_call", incoming_id=last.phase_call_ids["crypto"], outgoing_id=None,
        activated_at=NOW,
    )
    # A two-step move: the market can go from 1 to 3 in a week, and the read
    # that says so is written like any other.
    reads["phase_calls"][1].update(phase=3, phase_name="Wealth Distribution")
    w40["phase_calls"][1]["change_note"] = (
        "Phase 1 to Phase 3 in one week: majors stalled on RSI divergence while alts rotated "
        "fast, which skips Wealth Creation on this data."
    )
    written = await _week(store, pg_settings, w40, reads)
    async with store.transaction() as connection:
        row = await connection.fetchrow(
            "select phase, prior_phase, direction, classification, classified_at, "
            "change_note, stage from macro_core.phase_call where id = $1::uuid",
            written.phase_call_ids["crypto"],
        )
    assert (row["phase"], row["prior_phase"], row["direction"]) == (3, 1, "up")
    assert row["classification"] == "fresh"
    assert row["classified_at"] == NOW
    assert row["change_note"].startswith("Phase 1 to Phase 3")
    assert row["stage"] == "late"


async def test_a_stage_outside_the_vocabulary_is_refused_by_the_schema(store, reads):
    """Belt and braces: `validate` refuses it first, and so does the CHECK."""
    from dataclasses import replace as dc_replace

    call = dc_replace(phase_reads(reads)[0], stage="mature")
    with pytest.raises(WriteRefused):
        await store.write_phase_call(call)
    assert await store.active_phase_calls() == []


async def test_the_schema_refuses_a_reveal_before_the_read(store, reads):
    """A fresh row whose prior was shown before it was written is not fresh."""
    call_id = await store.write_phase_call(phase_reads(reads, classified_at=NOW)[0])
    with pytest.raises(WriteRefused, match="ck_macro_phase_call_fresh_order"):
        await store._execute(
            "update macro_core.phase_call set revealed_at = classified_at - interval '1 minute' "
            "where id = $1::uuid",
            call_id,
        )


async def _carried(connection, week: str, phase: int, status: str) -> None:
    """One migrated-style call, with the four manual conditions the journals wrote."""
    call_id = await connection.fetchval(
        "insert into macro_core.phase_call (asset, week, phase, phase_name, prior_phase, "
        "direction, reason, falsifier, status, activated_at) values "
        "('crypto', $1, $2, 'x', null, 'unchanged', 'r', 'f', $3, $4) returning id",
        week, phase, status, NOW,
    )
    for ordinal in range(1, 5):
        await connection.execute(
            "insert into macro_core.phase_condition (phase_call_id, ordinal, text, kind) "
            "values ($1, $2, 'c', 'manual')",
            call_id, ordinal,
        )


async def test_the_detail_view_counts_the_weeks_a_phase_has_run(store):
    """`Same as last week, 5 weeks running` is counted in calendar weeks, W33 included."""
    async with store.transaction() as connection:
        await _carried(connection, "2026-W32", 5, "superseded")
        await _carried(connection, "2026-W35", 1, "superseded")
        await _carried(connection, "2026-W37", 1, "superseded")
        await _carried(connection, "2026-W38", 1, "superseded")
        await _carried(connection, "2026-W39", 1, "active")
    async with store.transaction() as connection:
        row = await connection.fetchrow(
            "select classification, prior_week, since_week, weeks_running "
            "from macro_mart.phase_call_detail where asset = 'crypto'"
        )
    assert row["classification"] == "carried"
    assert row["prior_week"] == "2026-W38"
    assert row["since_week"] == "2026-W35"
    assert row["weeks_running"] == 5


# ---------------------------------------------------------------------------
# Monday against a realistic tape
#
# Every test above runs with no observations, so `history_rows` came back
# empty and nothing ever decoded an `extra`. On the live database 2,248 of
# 3,030 observations carry one, and the first of them crashed `classify`,
# `grade`, `propose` and `preflight` alike.
# ---------------------------------------------------------------------------


def _last_session() -> date:
    """A weekday at least one day before today, as the newest close the tape carries.

    Relative to the real date, because the commands default to it: a Monday
    run sees Friday's close, never today's.
    """
    from datetime import timedelta

    day = date.today() - timedelta(days=3)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


async def _tape(store: Store, *, skip: tuple[str, ...] = ()) -> date:
    """A week of sessions for every series the W40 reads name, `extra` and all.

    Written the way the worker writes them, `extra` as `jsonb`, so a reader
    that forgot to decode it fails here the way it failed against the live rows.
    """
    from datetime import timedelta

    last = _last_session()
    days = [last - timedelta(days=offset) for offset in range(0, 8)]
    days = [one for one in days if one.weekday() < 5][:5]
    readings: dict[str, tuple[str, dict]] = {
        "spx_cash": ("7650.50", {"dma200_pct": 6.1, "dma200_slope": "rising"}),
        "breadth_spx_above_200dma": ("49.5", {}),
        "hy_oas_bps": ("285", {}),
        "vix": ("16.2", {}),
        "move": ("78", {}),
        "btc": ("80250", {"dma200_pct": 11.4}),
        "crypto_fear_greed": ("58", {}),
        "btc_etf_weekly_flow": ("6200000", {}),
        "brent": ("91.4", {}),
        "ust_10y": ("4.62", {"spread_2s10s_bps": 38}),
    }
    async with store.transaction() as connection:
        for key, (value, extra) in readings.items():
            if key in skip:
                continue
            for day in days:
                await connection.execute(
                    "insert into macro_core.observation (series_id, asof, value, display, tier, "
                    "source, extra, observed_at, received_at) values ("
                    "(select id from macro_core.series where key = $1), $2, $3::numeric, $3, 1, "
                    "'test', $4::jsonb, $5, $5)",
                    key, day, value, json.dumps(extra), NOW,
                )
    return last


async def _last_week_standing(store, pg_settings, w40, reads) -> None:
    """W39 written and accepted: its reads, its set, as the operator left them."""
    last = await _week(store, pg_settings, w40, reads, week="2026-W39")
    async with store.transaction() as connection:
        await connection.execute(
            "update macro_core.phase_call set revealed_at = classified_at "
            "where week = '2026-W39'"
        )
    for asset in ("equities", "crypto"):
        await store.activate(
            "phase_call", incoming_id=last.phase_call_ids[asset], outgoing_id=None,
            activated_at=NOW,
        )
    await store.activate(
        "scenario_set", incoming_id=last.scenario_set_id, outgoing_id=None, activated_at=NOW
    )


async def test_history_rows_come_back_decoded(store, pg_settings):
    """`extra` is a dict at the store boundary, for every caller of `readings_from`."""
    from macro_agent.worker_bridge import evaluator

    await _tape(store)
    rows = await store.history_rows(since=date(2000, 1, 1))
    spx = [one for one in rows if one["key"] == "spx_cash"]
    assert spx and spx[-1]["extra"] == {"dma200_pct": 6.1, "dma200_slope": "rising"}
    _expr, _predicates, observations = evaluator(pg_settings)
    readings = observations.readings_from(rows)
    assert any(one.extra.get("dma200_slope") == "rising" for one in readings)


async def test_monday_runs_against_a_realistic_tape(
    store, pg_settings, w40, reads, tmp_path, capsys
):
    """Classify, reveal, grade, propose and preflight, with `extra` set and no `--asof`.

    `move` is left off the tape, so one equities condition cannot be evaluated:
    that is reported as unevaluable, apart from failed, and it is not a finding.
    """
    import argparse

    from macro_agent import cli

    await _catalysts(store)
    await _last_week_standing(store, pg_settings, w40, reads)
    last_session = await _tape(store, skip=("move",))
    capsys.readouterr()

    assert await cli.author_phase_context_cmd(
        argparse.Namespace(week="2026-W40"), pg_settings
    ) == 0
    blind = json.loads(capsys.readouterr().out)
    assert blind["weekly"]["spx_cash"][-1]["dma200_slope"] == "rising"

    reads_file = tmp_path / "phase.json"
    reads_file.write_text(json.dumps(reads), encoding="utf-8")
    code = await cli.author_classify_cmd(
        argparse.Namespace(week="2026-W40", input=str(reads_file), asof=None), pg_settings
    )
    classified = json.loads(capsys.readouterr().out)
    assert code == 0, classified
    assert classified["evaluated_at"] == last_session.isoformat()
    held = [one for one in classified["warnings"] if "conditions hold on" in one]
    assert held == [
        f"equities: 3 of 4 conditions hold on {last_session.isoformat()}. 1 could not be "
        "evaluated on that day (no reading, or a manual condition with no assertion). That is "
        "not a failure; check the series names if it was not expected."
    ]

    assert await cli.author_context_cmd(
        argparse.Namespace(week="2026-W40", graded_week="2026-W39", amend=False), pg_settings
    ) == 0
    capsys.readouterr()

    stored = await store.scenario_set_for_week("2026-W39")
    grades_file = tmp_path / "grades.json"
    grades_file.write_text(
        json.dumps(
            {
                "scenario_grades": [
                    {"scenario_id": one["scenario_id"], "grade": g, "note": ""}
                    for one, g in zip(
                        stored["scenarios"], ("Partial", "Not", "Not"), strict=True
                    )
                ],
                "eq_call": "valid", "crypto_call": "valid", "key_miss": "",
            }
        ),
        encoding="utf-8",
    )
    code = await cli.author_grade_cmd(
        argparse.Namespace(graded_week="2026-W39", input=str(grades_file), asof=None),
        pg_settings,
    )
    graded = json.loads(capsys.readouterr().out)
    assert code == 0, graded
    assert graded["targets_n"] == 14

    proposal_file = tmp_path / "proposal.json"
    proposal_file.write_text(json.dumps(w40), encoding="utf-8")
    code = await cli.author_propose_cmd(
        argparse.Namespace(input=str(proposal_file), asof=None), pg_settings
    )
    written = json.loads(capsys.readouterr().out)
    assert code == 0, written
    assert written["evaluated_at"] == last_session.isoformat()
    holds = {one["key"]: one for one in written["preflight"] if one["key"].endswith("_holds_today")}
    assert holds["equities_holds_today"]["marker"] == "pass"
    assert holds["crypto_holds_today"]["marker"] == "pass"
    async with store.transaction() as connection:
        filed = await connection.fetch(
            "select check_key from macro_quality.finding where check_key like '%holds_today'"
        )
    assert filed == [], "an unevaluable condition is not a finding"

    code = await cli.preflight_cmd(
        argparse.Namespace(input=str(proposal_file), asof=None, text=False, phase=None),
        pg_settings,
    )
    assert code == 0, capsys.readouterr().out


async def test_a_second_propose_replaces_the_first(store, pg_settings, w40, reads):
    """Twice-run `propose` left two proposed W40 sets, and Accept tripped on the second."""
    await _catalysts(store)
    first = await _week(store, pg_settings, w40, reads)
    found = attach_reads(proposal(w40), tuple(await store.proposed_reads("2026-W40")))
    second = await authoring.propose(store, pg_settings, found, asof=ASOF, tape=Tape(), now=NOW)
    assert second.replaced == 1 + 2 + 4, "the set, both strategies and every setup"
    async with store.transaction() as connection:
        rows = await connection.fetch(
            "select 'set' as t, id::text as id, status from macro_core.scenario_set "
            "where week = '2026-W40' union all "
            "select 'strategy', id::text, status from macro_core.strategy where week = '2026-W40' "
            "union all select 'setup', id::text, status from macro_core.setup "
            "where week = '2026-W40'"
        )
    proposed = {one["id"] for one in rows if one["status"] == "proposed"}
    assert second.scenario_set_id in proposed and first.scenario_set_id not in proposed
    assert len(proposed) == 1 + 2 + 4
    assert len(rows) == 2 * (1 + 2 + 4)


async def test_an_accept_that_cannot_finish_changes_nothing(store, pg_settings, w40, reads):
    """One transaction for the whole decision: W39 stays standing if any step is refused."""
    from macro_agent import runner

    await _catalysts(store)
    await _last_week_standing(store, pg_settings, w40, reads)
    await _week(store, pg_settings, w40, reads)
    # Two proposed sets for W40, as the old `propose` left them after a second run.
    bound, _ = authoring.resolve_catalysts(proposal(w40), await store.catalysts_by_name())
    await store.write_scenario_set(bound.scenario_set)
    async with store.transaction() as connection:
        request_id = await connection.fetchval(
            "insert into macro_core.run_request (kind, origin, status, decision, decided_at) "
            "values ('author', 'schedule', 'proposed', 'accept', now()) returning id"
        )
    wrote = await runner.proposed_since(store, datetime(2000, 1, 1, tzinfo=UTC))
    wrote = [one for one in wrote if one.week == "2026-W40"]
    decided = runner.Decided(id=request_id, kind="author", decision="accept", proposed=tuple(wrote))

    before = set(await runner.active_rows(store))
    assert {one.week for one in before} == {"2026-W39"}
    with pytest.raises(WriteRefused, match="two proposed rows for scenario set"):
        await runner.apply_decision(store, decided, NOW)
    assert set(await runner.active_rows(store)) == before

    # And with the doubled-subject guard out of the way, the index refuses half
    # way through, and the transaction still leaves everything as it was.
    original = runner._doubled_subjects
    def no_doubles(rows: Sequence[runner.ProposedRow]) -> list[str]:
        del rows
        return []

    runner._doubled_subjects = no_doubles
    try:
        with pytest.raises(WriteRefused, match="uq_macro_scenario_set_active"):
            await runner.apply_decision(store, decided, NOW)
    finally:
        runner._doubled_subjects = original
    assert set(await runner.active_rows(store)) == before
    async with store.transaction() as connection:
        status = await connection.fetchval(
            "select status from macro_core.run_request where id = $1", request_id
        )
    assert status == "proposed"


async def test_propose_is_refused_before_the_reveal_and_preflight_stamps_it(
    store, pg_settings, w40, reads, tmp_path, capsys
):
    """Both print last week's phase. Propose waits for the reveal; preflight is one."""
    import argparse

    from macro_agent import cli

    await _catalysts(store)
    await _last_week_standing(store, pg_settings, w40, reads)
    # Classified in the past: the preflight stamps the reveal at the real clock.
    earlier = datetime(2026, 1, 5, 1, 30, tzinfo=UTC)
    await authoring.classify(
        store, pg_settings, phase_reads(reads, classified_at=earlier), week="2026-W40",
        asof=ASOF, now=earlier,
    )
    capsys.readouterr()
    path = tmp_path / "proposal.json"
    path.write_text(json.dumps(w40), encoding="utf-8")

    code = await cli.author_propose_cmd(
        argparse.Namespace(input=str(path), asof="2026-09-25"), pg_settings
    )
    refused = json.loads(capsys.readouterr().out)
    assert code == 1 and "Run `author context` first" in refused["refusals"][0]
    assert await store.scenario_set_for_week("2026-W40") is None

    code = await cli.preflight_cmd(
        argparse.Namespace(input=str(path), asof="2026-09-25", text=True, phase=None), pg_settings
    )
    assert "Same as last week in 2026-W39" in capsys.readouterr().out
    assert all(one["revealed_at"] is not None for one in await store.fresh_reads("2026-W40"))


async def test_an_amendment_context_waits_for_the_weeks_reads(
    store, pg_settings, w40, reads, capsys
):
    """`--amend` carries the standing calls; before Monday's read it would be the reveal."""
    import argparse

    from macro_agent import cli

    await _catalysts(store)
    await _last_week_standing(store, pg_settings, w40, reads)
    capsys.readouterr()
    args = argparse.Namespace(week="2026-W40", graded_week="2026-W39", amend=True)
    assert await cli.author_context_cmd(args, pg_settings) == 1
    assert "no phase read" in json.loads(capsys.readouterr().out)["refusals"][0]

    await authoring.classify(
        store, pg_settings, phase_reads(reads, classified_at=NOW), week="2026-W40",
        asof=ASOF, now=NOW,
    )
    assert await cli.author_context_cmd(args, pg_settings) == 0


async def test_an_amendment_context_serves_a_week_carried_in_by_the_migration(
    store, pg_settings, w40, reads, capsys
):
    """A carried week has its call and no author run owed, so Refresh amends it."""
    import argparse

    from macro_agent import cli

    await _catalysts(store)
    await _last_week_standing(store, pg_settings, w40, reads)
    async with store.transaction() as connection:
        await connection.execute(
            "update macro_core.phase_call set classification = 'carried', "
            "classified_at = null, revealed_at = null where week = '2026-W39'"
        )
    capsys.readouterr()
    args = argparse.Namespace(week="2026-W39", graded_week="2026-W38", amend=True)
    assert await cli.author_context_cmd(args, pg_settings) == 0
    assert "refusals" not in json.loads(capsys.readouterr().out)

    # The week after, with no call of its own yet, is still refused.
    args = argparse.Namespace(week="2026-W40", graded_week="2026-W39", amend=True)
    assert await cli.author_context_cmd(args, pg_settings) == 1


async def test_a_catalyst_named_after_a_phase_does_not_refuse_the_blind_context(
    store, pg_settings, w40, reads
):
    """"US-China Phase 2 trade deal" is cleaned, and the context still prints."""
    from macro_agent import context as context_module

    await _catalysts(store)
    await _last_week_standing(store, pg_settings, w40, reads)
    async with store.transaction() as connection:
        await connection.execute(
            "insert into macro_core.catalyst (external_key, name, starts_at, date_precision, "
            "affected, status, first_seen, last_seen) values ('trade', "
            "'US-China Phase 2 trade deal', now() + interval '5 days', 'day', '{}', 'tracked', "
            "$1, $1)",
            ASOF,
        )
    ctx = await context_module.phase_context(store, pg_settings, week="2026-W40", now=NOW)
    text = json.dumps(ctx["catalysts"])
    assert "US-China [phase redacted] trade deal" in text and "Phase 2" not in text
    assert ctx["redacted"] >= 1


async def test_the_blind_context_is_refused_when_a_file_read_first_quotes_the_call(
    store, pg_settings, w40, reads, monkeypatch
):
    from macro_agent import context as context_module

    await _catalysts(store)
    await _last_week_standing(store, pg_settings, w40, reads)
    reason = (await store.active_phase_calls())[0]["reason"]
    monkeypatch.setattr(
        context_module, "pre_classify_files", lambda _root=None: {"config/voice.md": reason}
    )
    with pytest.raises(Refused, match=r"config/voice\.md quotes the active"):
        await context_module.phase_context(store, pg_settings, week="2026-W40", now=NOW)


async def test_a_retried_grade_does_not_add_its_new_rule_twice(
    store, pg_settings, w40, reads, tmp_path, capsys
):
    import argparse

    from macro_agent import cli

    await _catalysts(store)
    await _last_week_standing(store, pg_settings, w40, reads)
    stored = await store.scenario_set_for_week("2026-W39")
    path = tmp_path / "grades.json"
    path.write_text(
        json.dumps(
            {
                "scenario_grades": [
                    {"scenario_id": one["scenario_id"], "grade": "Not", "note": ""}
                    for one in stored["scenarios"]
                ],
                "new_rules": [{"text": "A pattern seen twice.", "weeks": []}],
            }
        ),
        encoding="utf-8",
    )
    args = argparse.Namespace(graded_week="2026-W39", input=str(path), asof="2026-09-25")
    assert await cli.author_grade_cmd(args, pg_settings) == 0
    assert json.loads(capsys.readouterr().out)["ledger_changes"] == ["R1 added"]
    assert await cli.author_grade_cmd(args, pg_settings) == 0
    assert json.loads(capsys.readouterr().out)["ledger_changes"] == ["R1 already stands"]
    assert [one["number"] for one in await store.rules()] == [1]


async def test_a_rejected_read_is_not_history(store, pg_settings, w40, reads):
    """A proposed call stood down without standing is not the prior week, nor a week run."""
    async with store.transaction() as connection:
        await _carried(connection, "2026-W37", 1, "superseded")
        await _carried(connection, "2026-W38", 1, "superseded")
        # W39's first read was rejected on the Desk: superseded, never activated.
        rejected = await connection.fetchval(
            "insert into macro_core.phase_call (asset, week, phase, phase_name, prior_phase, "
            "direction, reason, falsifier, status) values ('crypto', '2026-W39', 4, 'x', 1, "
            "'down', 'r', 'f', 'superseded') returning id"
        )
        for ordinal in range(1, 5):
            await connection.execute(
                "insert into macro_core.phase_condition (phase_call_id, ordinal, text, kind) "
                "values ($1, $2, 'c', 'manual')",
                rejected, ordinal,
            )
        await _carried(connection, "2026-W40", 1, "active")
    async with store.transaction() as connection:
        row = await connection.fetchrow(
            "select prior_week, since_week, weeks_running "
            "from macro_mart.phase_call_detail where asset = 'crypto'"
        )
    assert (row["prior_week"], row["since_week"], row["weeks_running"]) == (
        "2026-W38", "2026-W37", 4
    )

    # The same rule in the store's own prior-week lookup for a proposed read.
    await authoring.classify(
        store, pg_settings, phase_reads({**reads, "week": "2026-W41"}, classified_at=NOW),
        week="2026-W41", asof=ASOF, now=NOW,
    )
    async with store.transaction() as connection:
        await connection.execute(
            "delete from macro_core.phase_call where week = '2026-W40' and asset = 'crypto'"
        )
    crypto = [one for one in await store.proposed_reads("2026-W41") if one.asset == "crypto"]
    assert crypto[0].prior_week == "2026-W38"
