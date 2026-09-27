"""Load the emitted seed into a real Postgres with the real schema.

The oracles in verify.py check the numbers; this checks that the SQL actually
loads against every NOT NULL, CHECK, foreign key, partial unique index and
deferred constraint trigger supabase/migrations declares, and that replaying it
is safe.

Skipped when no local Postgres is installed. The cluster is created in a
temporary directory, used once and thrown away; nothing here touches the live
Supabase project.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

from macro_migrate import config, emit, model
from macro_migrate.textutil import sql_bool, sql_int, sql_str

MIGRATIONS = config.REPO / "supabase" / "migrations"
pytestmark = pytest.mark.skipif(
    not (shutil.which("initdb") and shutil.which("pg_ctl")
         and shutil.which("psql") and MIGRATIONS.is_dir()),
    reason="no local Postgres, or supabase/migrations is absent")


def _series_seed() -> str:
    """macro_core.series from config/series.yaml.

    The worker owns this seed in production; the test needs the 47 rows present
    so that every `(SELECT id FROM macro_core.series WHERE key = ...)` in the
    emitted SQL resolves.
    """
    out = []
    for r in config.SERIES_YAML_ROWS.values():
        out.append(
            "INSERT INTO macro_core.series (key,label,table_group,ordinal,"
            "value_kind,unit,decimals,cadence,release_rule,replayable,"
            "source_name,fetch_path,tier_floor) VALUES "
            "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (key) DO NOTHING;" % (
                sql_str(r["key"]), sql_str(r["label"]), sql_str(r["table_group"]),
                sql_int(r["ordinal"]), sql_str(r["value_kind"]),
                sql_str(r.get("unit")), sql_int(r.get("decimals")),
                sql_str(r["cadence"]), sql_str(r["release_rule"]),
                sql_bool(r["replayable"]), sql_str(r["source_name"]),
                sql_str(r["fetch_path"]), sql_int(r["tier_floor"])))
    return "\n".join(out)


class Cluster:
    def __init__(self, base: Path):
        self.base = base
        self.data = base / "data"

    def psql(self, *args: str, db: str = "macro") -> str:
        return subprocess.run(
            ["psql", "-h", str(self.base), "-p", "55433", "-U", "postgres",
             "-d", db, "-v", "ON_ERROR_STOP=1", "-q", *args],
            capture_output=True, text=True, check=True).stdout

    def query(self, sql: str) -> str:
        return subprocess.run(
            ["psql", "-h", str(self.base), "-p", "55433", "-U", "postgres",
             "-d", "macro", "-tAc", sql],
            capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture(scope="module")
def pg():
    # A Unix socket path is capped at ~103 bytes, which the pytest tmp path can
    # exceed, so the cluster lives directly under the system temp root.
    base = Path(tempfile.mkdtemp(prefix="mmpg", dir="/tmp"))
    c = Cluster(base)
    subprocess.run(["initdb", "-D", str(c.data), "-U", "postgres",
                    "--encoding=UTF8", "--locale=C"],
                   capture_output=True, check=True)
    subprocess.run(
        ["pg_ctl", "-D", str(c.data), "-o",
         "-k %s -p 55433 -c listen_addresses=''" % base,
         "-l", str(base / "log"), "start"], capture_output=True, check=True)
    for _ in range(40):
        try:
            subprocess.run(["psql", "-h", str(base), "-p", "55433", "-U",
                            "postgres", "-d", "postgres", "-tAc", "select 1"],
                           capture_output=True, check=True)
            break
        except subprocess.CalledProcessError:
            time.sleep(0.25)
    subprocess.run(["psql", "-h", str(base), "-p", "55433", "-U", "postgres",
                    "-d", "postgres", "-c", "create database macro"],
                   capture_output=True, check=True)
    try:
        yield c
    finally:
        subprocess.run(["pg_ctl", "-D", str(c.data), "-m", "immediate", "stop"],
                       capture_output=True)
        shutil.rmtree(base, ignore_errors=True)


@pytest.fixture(scope="module")
def loaded(pg, tmp_path_factory):
    # The schema. 0007 and 0009 create Supabase roles that do not exist in a
    # bare cluster; macro_core, which is all this seed writes, is unaffected.
    # Every .sql in the directory, in name order, never a prefix pattern and never a
    # pinned count. The files were renamed from 0001.. to Supabase's timestamp
    # convention because the sequential numbers collided with Accounting's in the
    # shared project, and a "0*.sql" glob silently matched nothing afterwards.
    found = sorted(MIGRATIONS.glob("*.sql"))
    assert found, f"no migrations found in {MIGRATIONS}"
    for f in found:
        try:
            pg.psql("-f", str(f))
        except subprocess.CalledProcessError as e:
            if "does not exist" not in e.stderr or "role" not in e.stderr:
                raise AssertionError("%s failed: %s" % (f.name, e.stderr))

    seed = tmp_path_factory.mktemp("seed") / "series.sql"
    seed.write_text(_series_seed(), encoding="utf-8")
    pg.psql("-f", str(seed))

    out = tmp_path_factory.mktemp("out")
    import macro_migrate.config as cfg
    old, cfg.OUT = cfg.OUT, out
    try:
        emit.emit(model.build_all())
    finally:
        cfg.OUT = old
    order = ["10_judgment.sql", "20_week_grade.sql", "30_scenario_rule.sql",
             "40_setup.sql", "50_observation.sql", "60_condition_state.sql"]
    for name in order:
        try:
            pg.psql("-f", str(out / name))
        except subprocess.CalledProcessError as e:
            raise AssertionError("%s failed to load: %s" % (name, e.stderr))
    return pg, out, order


def test_the_seed_loads_against_the_real_schema(loaded):
    pg, _, _ = loaded
    counts = dict(
        line.split("|") for line in pg.query(
            "select 'phase_call'||'|'||count(*) from macro_core.phase_call "
            "union all select 'week_grade'||'|'||count(*) from macro_core.week_grade "
            "union all select 'scenario_target'||'|'||count(*) from macro_core.scenario_target "
            "union all select 'setup'||'|'||count(*) from macro_core.setup "
            "union all select 'observation'||'|'||count(*) from macro_core.observation"
        ).splitlines())
    assert counts["phase_call"] == "14"
    assert counts["week_grade"] == "15"
    assert counts["scenario_target"] == "210"
    assert counts["setup"] == "21"
    assert int(counts["observation"]) > 250


def test_replaying_the_seed_is_safe(loaded):
    """CONTRACT.md doctrine: every write is an idempotent upsert."""
    pg, out, order = loaded
    before = pg.query("select count(*) from macro_core.observation")
    for name in order:
        pg.psql("-f", str(out / name))
    assert pg.query("select count(*) from macro_core.observation") == before
    assert pg.query("select count(*) from macro_core.scenario_target") == "210"


def test_calibration_computed_in_sql_matches_the_journal(loaded):
    pg, _, _ = loaded
    row = pg.query("""
        with last8 as (select * from macro_core.week_grade order by week desc limit 8)
        select count(*)
            || '|' || sum(case base_grade when 'Materialized' then 1
                                          when 'Partial' then 0.5 else 0 end)
            || '|' || count(*) filter (where base_grade = 'Materialized')
            || '|' || round(avg(brier), 3)
            || '|' || count(brier)
            || '|' || count(*) filter (where base_scenario_label like 'S2%')
        from last8""")
    assert row == "8|4.5|1|0.268|3|6"


def test_the_recorded_target_denominators_hold_in_sql(loaded):
    pg, _, _ = loaded
    got = pg.query(
        "select ss.week || ' S' || s.ordinal || ' ' || count(t.id) "
        "from macro_core.scenario_set ss "
        "join macro_core.scenario s on s.set_id = ss.id "
        "left join macro_core.scenario_target t on t.scenario_id = s.id "
        "where ss.week = '2026-W38' "
        "group by ss.week, s.ordinal order by ss.week, s.ordinal")
    assert got.splitlines() == ["2026-W38 S1 11", "2026-W38 S2 11", "2026-W38 S3 9"]


def test_only_one_active_row_per_subject_survives_the_unique_indexes(loaded):
    pg, _, _ = loaded
    assert pg.query(
        "select count(*) from macro_core.phase_call where status = 'active'") == "2"
    assert pg.query(
        "select count(*) from macro_core.scenario_set where status = 'active'") == "1"
    assert pg.query(
        "select count(*) from macro_core.strategy where status = 'active'") == "2"


def test_every_setup_sector_focus_id_satisfies_its_foreign_key(loaded):
    """0003 puts a foreign key on macro_core.setup.sector_focus_id."""
    pg, _, _ = loaded
    assert pg.query(
        "select count(*) from macro_core.setup where sector_focus_id is not null") == "15"
    assert pg.query(
        "select count(*) from macro_core.setup s "
        "left join macro_core.sector_focus f on f.id = s.sector_focus_id "
        "where s.sector_focus_id is not null and f.id is null") == "0"


def test_graded_at_is_null_for_the_archive_graded_weeks(loaded):
    pg, _, _ = loaded
    assert pg.query(
        "select string_agg(week, ',' order by week) from macro_core.week_grade "
        "where graded_at is null") == (
        "2026-W23,2026-W24,2026-W25,2026-W26,2026-W27,2026-W28,2026-W29,"
        "2026-W30,2026-W31")


def test_asserted_condition_state_loads_and_covers_both_owner_kinds(loaded):
    pg, _, _ = loaded
    assert pg.query(
        "select owner_kind || '=' || count(*) from macro_core.condition_state_asserted "
        "group by owner_kind order by owner_kind").splitlines() == [
        "phase=56", "trigger=60"]
    assert pg.query(
        "select count(*) from macro_core.condition_state_asserted "
        "where asserted_by <> 'migration'") == "0"
    # Every trigger clause is unevaluable: no journal scores a clause on its own.
    assert pg.query(
        "select count(*) from macro_core.condition_state_asserted "
        "where owner_kind = 'trigger' and status <> 'unevaluable'") == "0"
    # Every asserted phase row points at a real phase_condition.
    assert pg.query(
        "select count(*) from macro_core.condition_state_asserted a "
        "left join macro_core.phase_condition c on c.id = a.condition_id "
        "where a.owner_kind = 'phase' and c.id is null") == "0"


def test_hy_oas_is_stored_in_basis_points(loaded):
    pg, _, _ = loaded
    assert pg.query(
        "select value::int from macro_core.observation o "
        "join macro_core.series s on s.id = o.series_id "
        "where s.key = 'hy_oas_bps' and o.asof = '2026-09-17'") == "270"
