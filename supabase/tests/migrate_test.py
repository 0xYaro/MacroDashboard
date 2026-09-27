#!/usr/bin/env python3
"""Tests for supabase/migrate.py against a throwaway local PostgreSQL cluster.

Nothing here touches a hosted project. The cluster is created with initdb under
a short path in /tmp (a unix socket path longer than 103 bytes is rejected by
libpq), used, and removed.

    python3 supabase/tests/migrate_test.py

Standard library only. Requires initdb, pg_ctl and psql on PATH.

The scenario that matters most is the last one. A database that already contains
Accounting's four schemas and its rows in supabase_migrations.schema_migrations
must come through a full migration run with that table not merely unchanged but
never written: the assertion is on PostgreSQL's own insert, update and delete
counters for the relation, with a positive control on our own ledger table so
that a silent statistics failure cannot make the check pass by accident.
"""

from __future__ import annotations

import sys

# Loading migrate.py through importlib in scenario 9 would otherwise leave a
# __pycache__ directory inside supabase/. Nothing in this repository imports it
# as a module in normal use, so there is nothing to cache.
sys.dont_write_bytecode = True

import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUPABASE = HERE.parent
REPO = SUPABASE.parent
MIGRATE = SUPABASE / "migrate.py"
MIGRATIONS = SUPABASE / "migrations"
SHIM = HERE / "local_shim.sql"
SMOKE = HERE / "smoke.sql"

PORT = "55470"

FAILURES: list[str] = []
PASSES = 0


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


class Cluster:
    def __init__(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="mdmig", dir="/tmp"))
        self.data = self.dir / "d"

    def start(self) -> None:
        run(["initdb", "-D", str(self.data), "-U", "postgres",
             "--encoding=UTF8", "--locale=C"], check=True)
        run(["pg_ctl", "-D", str(self.data),
             "-o", f"-k {self.dir} -p {PORT} -c listen_addresses=''",
             "-l", str(self.dir / "server.log"), "start"], check=True)
        for _ in range(50):
            if run(["psql", "-h", str(self.dir), "-p", PORT, "-U", "postgres",
                    "-d", "postgres", "-tAc", "select 1"]).returncode == 0:
                return
            time.sleep(0.2)
        raise SystemExit("cluster did not come up:\n"
                         + (self.dir / "server.log").read_text())

    def stop(self) -> None:
        run(["pg_ctl", "-D", str(self.data), "stop", "-m", "fast"])
        shutil.rmtree(self.dir, ignore_errors=True)

    def url(self, db: str) -> str:
        return f"postgresql://postgres@/{db}?host={self.dir}&port={PORT}"

    def createdb(self, db: str) -> str:
        self.sql("postgres", f'drop database if exists "{db}"')
        self.sql("postgres", f'create database "{db}"')
        self.file(db, SHIM)
        return self.url(db)

    def sql(self, db: str, statement: str) -> str:
        r = run(["psql", "-h", str(self.dir), "-p", PORT, "-U", "postgres", "-d", db,
                 "-v", "ON_ERROR_STOP=1", "-tAqc", statement])
        if r.returncode != 0:
            raise SystemExit(f"setup SQL failed on {db}:\n{statement}\n{r.stderr}")
        return r.stdout.strip()

    def try_sql(self, db: str, statement: str) -> subprocess.CompletedProcess:
        return run(["psql", "-h", str(self.dir), "-p", PORT, "-U", "postgres", "-d", db,
                    "-v", "ON_ERROR_STOP=1", "-tAqc", statement])

    def file(self, db: str, path: Path) -> subprocess.CompletedProcess:
        return run(["psql", "-h", str(self.dir), "-p", PORT, "-U", "postgres", "-d", db,
                    "-v", "ON_ERROR_STOP=1", "-q", "-f", str(path)])


def run(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise SystemExit(f"{cmd[0]} failed:\n{r.stdout}\n{r.stderr}")
    return r


def migrate(url: str, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ, DATABASE_URL=url)
    return subprocess.run(
        [sys.executable, str(MIGRATE), *args],
        capture_output=True, text=True, env=env, cwd=str(cwd or REPO),
    )


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASSES
    if condition:
        PASSES += 1
        print(f"  PASS  {label}")
    else:
        FAILURES.append(label)
        print(f"  FAIL  {label}")
        if detail:
            for line in detail.strip().splitlines():
                print(f"        {line}")


def copy_migrations(into: Path) -> Path:
    target = into / "migrations"
    shutil.copytree(MIGRATIONS, target)
    return target


N_MIGRATIONS = len([p for p in MIGRATIONS.iterdir() if p.suffix == ".sql"])


# ---------------------------------------------------------------------------
# Accounting, simulated
# ---------------------------------------------------------------------------

ACCOUNTING_SQL = """
create schema raw;
create schema core;
create schema mart;
create schema quality;

create table core.risk_limit (key text primary key, value numeric not null, source text not null);
insert into core.risk_limit values ('gross_ceiling', 2.0, 'config');

create table mart.book_summary (id int primary key check (id = 1), nav_usd numeric);
insert into mart.book_summary values (1, 1000);
alter table mart.book_summary enable row level security;
create policy authenticated_select on mart.book_summary for select to authenticated using (true);

create table quality.finding (id bigserial primary key, sentence text);
alter table quality.finding enable row level security;
create policy authenticated_select on quality.finding for select to authenticated using (true);

create view mart.risk_limit with (security_invoker = false) as
  select key, value, source from core.risk_limit;

grant usage on schema mart, quality to authenticated;
grant select on all tables in schema mart, quality to authenticated;

-- The shared ledger. One table per DATABASE, not per schema, which is the whole
-- reason migrate.py exists. Thirteen rows, exactly as Accounting names them.
create schema supabase_migrations;
create table supabase_migrations.schema_migrations (
  version     text primary key,
  statements  text[],
  name        text
);
insert into supabase_migrations.schema_migrations (version, name)
select lpad(n::text, 4, '0'), 'accounting_' || n
from generate_series(1, 13) as n;
"""

FOREIGN_LEDGER_FINGERPRINT = (
    "select md5(coalesce(string_agg(version || '|' || coalesce(name, ''), ',' "
    "order by version), '')) || ':' || count(*)::text "
    "from supabase_migrations.schema_migrations"
)


def relation_writes(cluster: Cluster, db: str, relation: str) -> tuple[int, int, int]:
    """(inserted, updated, deleted) for `relation` since the last stats reset."""
    for _ in range(25):
        row = cluster.sql(
            db,
            "select coalesce(pg_stat_get_tuples_inserted(c.oid), 0)"
            " || ',' || coalesce(pg_stat_get_tuples_updated(c.oid), 0)"
            " || ',' || coalesce(pg_stat_get_tuples_deleted(c.oid), 0)"
            f" from pg_class c where c.oid = '{relation}'::regclass",
        )
        if row:
            i, u, d = (int(x) for x in row.split(","))
            return i, u, d
        time.sleep(0.1)
    raise SystemExit(f"could not read write statistics for {relation}")


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


def scenario_clean(c: Cluster) -> None:
    print("\n1. A clean database takes all migrations, and the result passes smoke.sql")
    url = c.createdb("mig_clean")

    r = migrate(url, "--status")
    check("status on a clean database reports every file pending",
          r.returncode == 0 and f"0 applied, {N_MIGRATIONS} pending" in r.stdout,
          r.stdout + r.stderr)

    r = migrate(url, "--dry-run")
    check("dry-run lists every file and exits clean",
          r.returncode == 0 and f"would apply {N_MIGRATIONS} migration" in r.stdout,
          r.stdout + r.stderr)
    check("dry-run created no ledger table",
          c.sql("mig_clean", "select to_regclass('macro_quality.schema_migration') is null") == "t")

    r = migrate(url)
    check(f"apply runs all {N_MIGRATIONS} files",
          r.returncode == 0 and f"applied {N_MIGRATIONS}" in r.stdout,
          r.stdout + r.stderr)

    check("the ledger holds one row per file",
          c.sql("mig_clean", "select count(*) from macro_quality.schema_migration") == str(N_MIGRATIONS))
    check("every ledger row carries a sha256 hash and a duration",
          c.sql("mig_clean",
                "select count(*) from macro_quality.schema_migration "
                "where content_hash like 'sha256:%' and duration_ms >= 0") == str(N_MIGRATIONS))
    check("the ledger's versions are the files' timestamps, in order",
          c.sql("mig_clean",
                "select string_agg(version, ',' order by version) "
                "from macro_quality.schema_migration")
          == ",".join(sorted(p.name[:14] for p in MIGRATIONS.iterdir() if p.suffix == ".sql")))

    check("the four macro schemas exist",
          c.sql("mig_clean",
                "select count(*) from information_schema.schemata "
                "where schema_name like 'macro%'") == "4")

    r = c.file("mig_clean", SMOKE)
    check("smoke.sql passes against the migrator's output",
          r.returncode == 0 and "SMOKE OK" in r.stdout,
          r.stdout + r.stderr)

    r = migrate(url, "--status")
    check("status now reports everything applied",
          r.returncode == 0 and f"{N_MIGRATIONS} applied, 0 pending" in r.stdout,
          r.stdout + r.stderr)


def scenario_idempotent(c: Cluster) -> None:
    print("\n2. A second run applies nothing")
    url = c.url("mig_clean")
    before = c.sql("mig_clean",
                   "select md5(string_agg(version || content_hash || applied_at::text, "
                   "',' order by version)) from macro_quality.schema_migration")

    r = migrate(url)
    check("a second apply reports nothing to do",
          r.returncode == 0 and "nothing to apply" in r.stdout, r.stdout + r.stderr)

    r = migrate(url, "--dry-run")
    check("a second dry-run reports nothing to do",
          r.returncode == 0 and "nothing to apply" in r.stdout, r.stdout + r.stderr)

    after = c.sql("mig_clean",
                  "select md5(string_agg(version || content_hash || applied_at::text, "
                  "',' order by version)) from macro_quality.schema_migration")
    check("no ledger row was rewritten", before == after and before != "")


def scenario_failure(c: Cluster, tmp: Path) -> None:
    print("\n3. A failing file rolls back whole and stops the run")
    work = tmp / "failing"
    work.mkdir()
    mdir = copy_migrations(work)
    (mdir / "29990101000000_broken.sql").write_text(
        "-- Deliberately broken, for supabase/tests/migrate_test.py.\n"
        "create table macro_quality.rollback_probe (id int primary key);\n"
        "select 1 / 0;\n",
        encoding="utf-8",
    )
    url = c.createdb("mig_fail")

    r = migrate(url, "--migrations-dir", str(mdir))
    check("the run exits non-zero", r.returncode == 1, r.stdout + r.stderr)
    check("the failure names the file that failed",
          "29990101000000_broken.sql" in (r.stdout + r.stderr), r.stdout + r.stderr)
    check("the message says where the run resumes",
          "the run resumes at 29990101000000_broken.sql" in r.stderr, r.stderr)

    check("every file before the broken one is applied and recorded",
          c.sql("mig_fail", "select count(*) from macro_quality.schema_migration") == str(N_MIGRATIONS))
    check("the broken file is not recorded",
          c.sql("mig_fail", "select count(*) from macro_quality.schema_migration "
                            "where version = '29990101000000'") == "0")
    check("the broken file's first statement rolled back with the rest of it",
          c.sql("mig_fail", "select to_regclass('macro_quality.rollback_probe') is null") == "t")

    r = migrate(url, "--migrations-dir", str(mdir), "--status")
    check("status shows the broken file as the only pending one",
          f"{N_MIGRATIONS} applied, 1 pending" in r.stdout, r.stdout)

    (mdir / "29990101000000_broken.sql").write_text(
        "create table macro_quality.rollback_probe (id int primary key);\n",
        encoding="utf-8",
    )
    r = migrate(url, "--migrations-dir", str(mdir))
    check("fixing the file and re-running resumes at it",
          r.returncode == 0 and "applied 1" in r.stdout, r.stdout + r.stderr)


def scenario_drift(c: Cluster, tmp: Path) -> None:
    print("\n4. An applied file that has since been edited is refused")
    work = tmp / "drift"
    work.mkdir()
    mdir = copy_migrations(work)
    url = c.createdb("mig_drift")

    r = migrate(url, "--migrations-dir", str(mdir))
    check("the first run applies everything", r.returncode == 0, r.stdout + r.stderr)

    victim = mdir / "20260921000800_quality_tables.sql"
    victim.write_text(victim.read_text(encoding="utf-8") + "\n-- edited after applying\n",
                      encoding="utf-8")

    before = c.sql("mig_drift",
                   "select md5(string_agg(version || content_hash, ',' order by version)) "
                   "from macro_quality.schema_migration")

    r = migrate(url, "--migrations-dir", str(mdir))
    check("apply refuses", r.returncode == 1, r.stdout + r.stderr)
    check("the refusal names the edited file and both hashes",
          "20260921000800_quality_tables.sql has changed" in r.stderr
          and "recorded sha256:" in r.stderr and "on disk  sha256:" in r.stderr,
          r.stderr)

    r = migrate(url, "--migrations-dir", str(mdir), "--dry-run")
    check("dry-run refuses the same way", r.returncode == 1, r.stdout + r.stderr)

    r = migrate(url, "--migrations-dir", str(mdir), "--status")
    check("status flags it rather than refusing, so the drift can be read",
          r.returncode == 0 and "CHANGED ON DISK" in r.stdout, r.stdout + r.stderr)

    after = c.sql("mig_drift",
                  "select md5(string_agg(version || content_hash, ',' order by version)) "
                  "from macro_quality.schema_migration")
    check("the ledger was not rewritten by the refusal", before == after)

    os.unlink(victim)
    r = migrate(url, "--migrations-dir", str(mdir))
    check("a recorded file deleted from disk is refused too",
          r.returncode == 1 and "no file with that version exists any more" in r.stderr,
          r.stderr)


def scenario_accounting(c: Cluster) -> None:
    print("\n5. Accounting's database is unaffected, and its ledger is never written")
    url = c.createdb("mig_shared")
    c.sql("mig_shared", ACCOUNTING_SQL)

    before_ledger = c.sql("mig_shared", FOREIGN_LEDGER_FINGERPRINT)
    before_rows = c.sql("mig_shared",
                        "select count(*) from supabase_migrations.schema_migrations")

    # Reset statistics so that the write counters below describe the migration
    # run and nothing that set this database up.
    c.sql("mig_shared", "select pg_stat_reset()")

    r = migrate(url)
    check(f"the run applies all {N_MIGRATIONS} files alongside Accounting",
          r.returncode == 0 and f"applied {N_MIGRATIONS}" in r.stdout, r.stdout + r.stderr)

    # Positive control: statistics are live, because our own ledger shows writes.
    ours = relation_writes(c, "mig_shared", "macro_quality.schema_migration")
    check("statistics are live: our own ledger recorded inserts",
          ours[0] == N_MIGRATIONS, f"inserted/updated/deleted = {ours}")

    theirs = relation_writes(c, "mig_shared", "supabase_migrations.schema_migrations")
    check("supabase_migrations.schema_migrations was never inserted into, "
          "updated or deleted from",
          theirs == (0, 0, 0), f"inserted/updated/deleted = {theirs}")

    check("its contents are byte-for-byte what they were",
          c.sql("mig_shared", FOREIGN_LEDGER_FINGERPRINT) == before_ledger)
    check("it still holds Accounting's thirteen rows",
          c.sql("mig_shared",
                "select count(*) from supabase_migrations.schema_migrations") == before_rows == "13")

    check("Accounting's four schemas are still there",
          c.sql("mig_shared",
                "select count(*) from information_schema.schemata "
                "where schema_name in ('raw','core','mart','quality')") == "4")
    check("Accounting's rows are still there",
          c.sql("mig_shared", "select count(*) from core.risk_limit") == "1"
          and c.sql("mig_shared", "select nav_usd::text from mart.book_summary") == "1000")
    check("Accounting's policies are still there",
          c.sql("mig_shared",
                "select count(*) from pg_policies "
                "where schemaname in ('raw','core','mart','quality')") == "2")
    check("Accounting's grants to authenticated are still there",
          c.sql("mig_shared",
                "select has_table_privilege('authenticated','mart.book_summary','select')::text "
                "|| ',' || has_table_privilege('authenticated','mart.risk_limit','select')::text "
                "|| ',' || has_table_privilege('authenticated','quality.finding','select')::text")
          == "true,true,true")
    check("Accounting's core is still closed to authenticated",
          c.sql("mig_shared", "select has_schema_privilege('authenticated','core','usage')::text") == "false")

    check("our four schemas landed beside them",
          c.sql("mig_shared",
                "select count(*) from information_schema.schemata "
                "where schema_name like 'macro%'") == "4")

    r = c.file("mig_shared", SMOKE)
    check("smoke.sql passes in the shared database",
          r.returncode == 0 and "SMOKE OK" in r.stdout, r.stdout + r.stderr)


ACCOUNTING_POSITIONS_SQL = """
create table core.instrument (id uuid primary key default gen_random_uuid(), kind text not null,
  symbol text not null, currency text not null, underlying_id uuid null references core.instrument (id),
  expiry date null, strike numeric null, put_call text null);
create table mart.book_position (instrument_id uuid primary key references core.instrument (id),
  symbol text not null, kind text not null, name text null, display_name text null,
  asset_class text null, quantity numeric not null, direction text null, mark numeric null,
  mark_as_of timestamptz null, currency text not null, exposure_usd numeric null,
  venues text[] not null default '{}', accounts text[] not null default '{}',
  updated_at timestamptz not null default now());
create table mart.venue_status (account_id uuid primary key default gen_random_uuid(),
  venue text not null, state text not null, reason text null, venue_as_of timestamptz null,
  received_at timestamptz null, position_count int not null default 0);
alter table core.instrument enable row level security;
alter table mart.book_position enable row level security;
alter table mart.venue_status enable row level security;
create policy authenticated_select on mart.book_position for select to authenticated using (true);
create policy authenticated_select on mart.venue_status for select to authenticated using (true);
grant select on mart.book_position, mart.venue_status to authenticated;
with i as (insert into core.instrument (kind, symbol, currency) values
  ('equity', 'GOOG', 'USD'), ('cash', 'USD', 'USD'), ('spot', 'USDE', 'USDC')
  returning id, kind, symbol, currency)
insert into mart.book_position (instrument_id, symbol, kind, quantity, currency, accounts)
select id, symbol, kind, case symbol when 'USDE' then 0 else 5 end, currency, array['U123']
  from i;
insert into mart.venue_status (venue, state, position_count) values ('tiger', 'fresh', 1);
"""

ACCOUNTING_FINGERPRINT = (
    "select md5(coalesce(string_agg(x, '|' order by x), '')) from ("
    " select schemaname || '.' || tablename || ':' || policyname || ':' || array_to_string(roles, ',') as x"
    "   from pg_policies where schemaname in ('raw', 'core', 'mart', 'quality')"
    " union all"
    " select n.nspname || '.' || c.relname || ':' || coalesce(array_to_string(c.relacl, ','), '')"
    "   from pg_class c join pg_namespace n on n.oid = c.relnamespace"
    "  where n.nspname in ('raw', 'core', 'mart', 'quality')"
    " union all"
    " select nspname || ':' || coalesce(array_to_string(nspacl, ','), '')"
    "   from pg_namespace where nspname in ('raw', 'core', 'mart', 'quality')"
    ") t"
)


def scenario_desk_reads_accounting(c: Cluster) -> None:
    print("\n10. The desk reads Accounting's positions without a grant or policy on Accounting")
    url = c.createdb("mig_desk")
    c.sql("mig_desk", ACCOUNTING_SQL)
    c.sql("mig_desk", ACCOUNTING_POSITIONS_SQL)
    before = c.sql("mig_desk", ACCOUNTING_FINGERPRINT)

    r = migrate(url)
    check("the run applies every file beside an Accounting that holds positions",
          r.returncode == 0, r.stdout + r.stderr)
    check("Accounting's policies, table grants and schema grants are byte-for-byte unchanged",
          c.sql("mig_desk", ACCOUNTING_FINGERPRINT) == before)
    check("macro_desk is created without a login; supabase/desk_role.py sets it",
          c.sql("mig_desk", "select rolcanlogin::text from pg_roles where rolname = 'macro_desk'")
          == "false")
    check("macro_desk has no usage on Accounting's mart or core",
          c.sql("mig_desk",
                "select has_schema_privilege('macro_desk', 'mart', 'usage')::text || ',' || "
                "has_schema_privilege('macro_desk', 'core', 'usage')::text") == "false,false")

    denied = c.try_sql("mig_desk", "set role macro_desk; select count(*) from mart.book_position")
    check("macro_desk cannot select mart.book_position directly",
          denied.returncode != 0 and "permission denied" in denied.stderr, denied.stderr)

    rows = c.sql("mig_desk",
                 "set role macro_desk; "
                 "select string_agg(symbol, ',' order by symbol) "
                 "from macro_core.accounting_open_positions()")
    check("through the definer function it reads open non-cash positions only",
          rows.splitlines()[-1] == "GOOG", rows)
    columns = c.sql("mig_desk",
                    "select string_agg(parameter_name, ',') from information_schema.parameters "
                    "where specific_name like 'accounting_open_positions%' and parameter_mode = 'OUT'")
    check("the function never returns the accounts column", "accounts" not in columns.split(","),
          columns)
    c.sql("mig_desk",
          "insert into mart.venue_status (venue, state, reason, position_count) values "
          "('tiger', 'fresh', 'Tiger account 50400376 holds cash only; wallet "
          "0x8c1f2e3d4b5a6978 is idle; ref 1234567', 0)")
    said = c.sql("mig_desk",
                 "set role macro_desk; select coalesce(string_agg(concat_ws(' | ', venue, state, "
                 "reason), ' || '), '') from macro_core.accounting_venue_status()").splitlines()[-1]
    check("no string accounting_venue_status() returns carries an account number or address",
          not re.search(r"\d{5,}|0x[0-9a-fA-F]{6,}", said)
          and "Tiger account holds cash only" in said, said)
    refused = c.try_sql("mig_desk",
                        "set role authenticated; select * from macro_core.accounting_open_positions()")
    check("authenticated cannot call it", refused.returncode != 0, refused.stderr)

    # Accounting's next migration must never trip over an object of ours.
    r2 = c.try_sql("mig_desk", "alter table mart.book_position drop column display_name")
    check("Accounting can still drop a column the desk reads: no dependency is recorded",
          r2.returncode == 0, r2.stderr)
    after = c.try_sql("mig_desk",
                      "set role macro_desk; select * from macro_core.accounting_open_positions()")
    check("the desk's read then fails loudly rather than returning something else",
          after.returncode != 0 and "display_name" in after.stderr, after.stderr)

    r = c.file("mig_desk", SMOKE)
    check("smoke.sql passes with the desk seam in place",
          r.returncode == 0 and "SMOKE OK" in r.stdout, r.stdout + r.stderr)


def scenario_desk_role_script(c: Cluster, tmp: Path) -> None:
    print("\n11. desk_role.py's password works, and its URL keeps the pooler's shape")
    import importlib.util

    spec = importlib.util.spec_from_file_location("desk_role_under_test",
                                                  SUPABASE / "desk_role.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)

    pooler = ("postgresql://postgres.abcdefghijklmnopqrst:secret@"
              "aws-0-ap-south-1.pooler.supabase.com:5432/postgres?sslmode=require")
    built = mod.desk_url(pooler, "pa$$ word")
    parts = mod.urlsplit(built)
    check("the pooler user becomes macro_desk.<project-ref>",
          parts.username == "macro_desk.abcdefghijklmnopqrst", built.split("@")[-1])
    check("host, port, database and options are kept",
          (parts.hostname, parts.port, parts.path, parts.query)
          == ("aws-0-ap-south-1.pooler.supabase.com", 5432, "/postgres", "sslmode=require"))
    check("the password is URL-encoded and survives the round trip",
          mod.unquote(parts.password or "") == "pa$$ word")
    check("a direct connection's bare user becomes the bare role",
          mod.urlsplit(mod.desk_url("postgresql://postgres:x@db.example.co:5432/postgres",
                                    "p")).username == "macro_desk")

    env_file = tmp / "desk.env"
    env_file.write_text("DATABASE_URL=keep\nMACRO_DESK_DATABASE_URL=old\n", encoding="utf-8")
    os.chmod(env_file, 0o600)
    how = mod.write_env(env_file, "new")
    check("an existing line is replaced, the rest kept, the mode kept",
          how == "replaced"
          and env_file.read_text(encoding="utf-8") == "DATABASE_URL=keep\nMACRO_DESK_DATABASE_URL=new\n"
          and (env_file.stat().st_mode & 0o777) == 0o600)

    # The .env content holds DATABASE_URL's password. It must never sit in a file any
    # other user can read, even for the moment before a chmod, and a failed write must
    # leave no copy behind.
    home = tmp / "desk-env"
    home.mkdir()
    target = home / ".env"
    previous = os.umask(0)
    try:
        mod.write_env(target, "first")
        check("a new .env is created owner-only whatever the umask",
              (target.stat().st_mode & 0o777) == 0o600, oct(target.stat().st_mode))
        os.chmod(target, 0o640)
        mod.write_env(target, "again")
        check("an existing .env keeps its own mode",
              (target.stat().st_mode & 0o777) == 0o640, oct(target.stat().st_mode))
        os.chmod(target, 0o600)
        before = target.read_text(encoding="utf-8")

        def refuse(*_args: object, **_kw: object) -> None:
            raise OSError("disk full")

        saved = (mod.os.chmod, getattr(mod.os, "fchmod", None), mod.os.replace)
        mod.os.chmod = refuse
        mod.os.fchmod = refuse
        mod.os.replace = refuse
        try:
            try:
                mod.write_env(target, "never")
                failed = False
            except OSError:
                failed = True
        finally:
            mod.os.chmod, fchmod, mod.os.replace = saved
            if fchmod is not None:
                mod.os.fchmod = fchmod
        leftovers = sorted(one.name for one in home.iterdir() if one.name != target.name)
        check("a write that fails raises, keeps .env as it was and leaves no copy behind",
              failed and target.read_text(encoding="utf-8") == before and leftovers == [],
              f"failed={failed} leftovers={leftovers}")
    finally:
        os.umask(previous)

    # The verifier is computed here and only it is sent; PostgreSQL must accept the
    # plaintext against it. The socket is switched to scram for macro_desk alone.
    c.createdb("mig_role")
    c.file("mig_role", SHIM)
    r = migrate(c.url("mig_role"))
    check("migrations apply", r.returncode == 0, r.stderr)
    password = "correct horse battery staple"
    c.sql("mig_role", f"alter role macro_desk login password '{mod.scram_verifier(password)}'")
    hba = c.data / "pg_hba.conf"
    original = hba.read_text(encoding="utf-8")
    hba.write_text("local all macro_desk scram-sha-256\n" + original, encoding="utf-8")
    run(["pg_ctl", "-D", str(c.data), "reload"], check=True)
    time.sleep(0.5)
    try:
        def login(secret: str) -> subprocess.CompletedProcess:
            return subprocess.run(
                ["psql", "-h", str(c.dir), "-p", PORT, "-U", "macro_desk", "-d", "mig_role",
                 "-tAc", "select current_user"],
                capture_output=True, text=True,
                env={**os.environ, "PGPASSWORD": secret},
            )
        good = login(password)
        check("the plaintext logs in against the verifier the script computed",
              good.returncode == 0 and good.stdout.strip() == "macro_desk", good.stderr)
        bad = login("wrong")
        check("a wrong password does not", bad.returncode != 0, bad.stdout)
    finally:
        hba.write_text(original, encoding="utf-8")
        run(["pg_ctl", "-D", str(c.data), "reload"], check=True)
        c.sql("mig_role", "alter role macro_desk nologin password null")


def scenario_preflight(c: Cluster, tmp: Path) -> None:
    print("\n6. Files that cannot honour one-file-one-transaction are refused")
    cases = [
        ("29990102000000_own_transaction.sql",
         "begin;\ncreate table macro_quality.probe (id int);\ncommit;\n",
         "contains BEGIN"),
        ("29990103000000_meta_command.sql",
         "\\c postgres\ncreate table macro_quality.probe (id int);\n",
         "starts with a psql meta-command"),
        ("29990104000000_foreign_ledger.sql",
         "insert into supabase_migrations.schema_migrations (version) values ('9999');\n",
         "mentions supabase_migrations"),
        ("29990105000000_concurrently.sql",
         "create index concurrently probe_idx on macro_quality.finding (check_key);\n",
         "contains CREATE INDEX CONCURRENTLY"),
    ]
    for i, (name, body, expected) in enumerate(cases):
        work = tmp / f"preflight{i}"
        work.mkdir()
        mdir = copy_migrations(work)
        (mdir / name).write_text(body, encoding="utf-8")
        url = c.createdb(f"mig_pre{i}")
        r = migrate(url, "--migrations-dir", str(mdir))
        check(f"{name} is refused ({expected})",
              r.returncode == 1 and expected in r.stderr, r.stderr)
        check(f"{name} left nothing applied",
              c.sql(f"mig_pre{i}", "select to_regclass('macro_core.series') is null") == "t")


def scenario_discovery(c: Cluster, tmp: Path) -> None:
    print("\n7. Discovery is by extension and name order, never by prefix or count")
    url = c.createdb("mig_discover")

    r = migrate(url, "--migrations-dir", str(tmp / "does_not_exist"))
    check("a missing directory is an error",
          r.returncode == 1 and "migrations directory not found" in r.stderr, r.stderr)

    empty = tmp / "empty"
    empty.mkdir()
    r = migrate(url, "--migrations-dir", str(empty))
    check("an empty directory is an error rather than a silent no-op",
          r.returncode == 1 and "no migrations found" in r.stderr, r.stderr)

    work = tmp / "badname"
    work.mkdir()
    mdir = copy_migrations(work)
    (mdir / "0001_legacy.sql").write_text("select 1;\n", encoding="utf-8")
    r = migrate(url, "--migrations-dir", str(mdir))
    check("a file that is not <14-digit timestamp>_<name>.sql is refused",
          r.returncode == 1 and "0001_legacy.sql" in r.stderr
          and "does not match" in r.stderr, r.stderr)

    work = tmp / "notsql"
    work.mkdir()
    mdir = copy_migrations(work)
    (mdir / "notes.md").write_text("notes\n", encoding="utf-8")
    (mdir / ".DS_Store").write_bytes(b"\x00")
    r = migrate(url, "--migrations-dir", str(mdir), "--dry-run")
    check("a stray non-sql file is refused loudly rather than skipped",
          r.returncode == 1 and "notes.md" in r.stderr, r.stderr)

    work = tmp / "hidden"
    work.mkdir()
    mdir = copy_migrations(work)
    (mdir / ".DS_Store").write_bytes(b"\x00")
    r = migrate(url, "--migrations-dir", str(mdir), "--dry-run")
    check("a dotfile is ignored and the real files are still all found",
          r.returncode == 0 and f"would apply {N_MIGRATIONS} migration" in r.stdout,
          r.stdout + r.stderr)

    r = migrate(url, "--dry-run")
    check("the real directory yields every file although none starts with 0",
          r.returncode == 0 and f"would apply {N_MIGRATIONS} migration" in r.stdout,
          r.stdout + r.stderr)


def scenario_out_of_order(c: Cluster, tmp: Path) -> None:
    print("\n8. A migration inserted into the past is refused")
    work = tmp / "ooo"
    work.mkdir()
    mdir = copy_migrations(work)
    url = c.createdb("mig_ooo")

    r = migrate(url, "--migrations-dir", str(mdir))
    check("the first run applies everything", r.returncode == 0, r.stdout + r.stderr)

    (mdir / "20260921000050_too_early.sql").write_text(
        "create table macro_quality.too_early (id int);\n", encoding="utf-8")
    r = migrate(url, "--migrations-dir", str(mdir))
    check("a pending file that sorts before an applied one is refused",
          r.returncode == 1 and "20260921000050_too_early.sql" in r.stderr
          and "sort before one that is already applied" in r.stderr, r.stderr)
    check("nothing was applied",
          c.sql("mig_ooo", "select to_regclass('macro_quality.too_early') is null") == "t")


def scenario_single_transaction(c: Cluster) -> None:
    print("\n9. The one-transaction assertion itself fires when the marker is lost")
    # PostgreSQL will not let a migration commit inside the explicit transaction
    # block migrate.py opens, and preflight refuses a literal COMMIT anyway, so
    # the runtime assertion is a second line of defence. It is exercised here
    # directly: the same assertion block, run after a commit has discarded the
    # transaction-local marker, must raise.
    import importlib.util

    spec = importlib.util.spec_from_file_location("macro_migrate_under_test", MIGRATE)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    # Dataclass resolution reads sys.modules[cls.__module__], so the module has to
    # be registered before it is executed.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)

    probe = """
begin;
select set_config('macro_migrate.xid', pg_current_xact_id()::text, true);
commit;
begin;
do $probe$
begin
  if coalesce(current_setting('macro_migrate.xid', true), '') <> pg_current_xact_id()::text then
    raise exception 'ASSERTION FIRED' using errcode = 'invalid_transaction_state';
  end if;
end
$probe$;
commit;
"""
    r = c.try_sql("mig_clean", probe)
    check("the assertion raises once a commit has discarded the marker",
          r.returncode != 0 and "ASSERTION FIRED" in r.stderr, r.stdout + r.stderr)

    sample = next(p for p in sorted(MIGRATIONS.iterdir()) if p.suffix == ".sql")
    m = mod.Migration(version=sample.name[:14], filename=sample.name,
                      path=sample, content_hash="sha256:x")
    wrapper = mod.wrapper_sql(m)
    check("the wrapper opens one transaction, asserts, records, and commits once",
          wrapper.count("begin;") == 1 and wrapper.count("commit;") == 1
          and "pg_current_xact_id()" in wrapper
          and wrapper.index("insert into macro_quality.schema_migration")
              < wrapper.rindex("commit;"),
          wrapper)

    # 20260921001200 recreates nine views; the guarantee it needs is exactly the
    # one above, so it is named rather than left implicit.
    views = MIGRATIONS / "20260921001200_mart_judgment_views.sql"
    text = views.read_text(encoding="utf-8")
    code = mod.strip_sql(text).lower()
    check("the view migration opens no transaction of its own",
          not any(p.search(code) for p, _ in mod.FORBIDDEN_STATEMENTS), views.name)
    check("the view migration does drop and recreate, so the guarantee matters",
          "drop view if exists" in code and code.count("create view") >= 9, views.name)


# ---------------------------------------------------------------------------


def main() -> int:
    for tool in ("initdb", "pg_ctl", "psql"):
        if not shutil.which(tool):
            print(f"{tool} not found on PATH", file=sys.stderr)
            return 2

    print(f"migrate.py tests: {N_MIGRATIONS} migrations in {MIGRATIONS}")
    cluster = Cluster()
    tmp = Path(tempfile.mkdtemp(prefix="mdmigwork"))
    try:
        cluster.start()
        scenario_clean(cluster)
        scenario_idempotent(cluster)
        scenario_failure(cluster, tmp)
        scenario_drift(cluster, tmp)
        scenario_accounting(cluster)
        scenario_preflight(cluster, tmp)
        scenario_discovery(cluster, tmp)
        scenario_out_of_order(cluster, tmp)
        scenario_single_transaction(cluster)
        scenario_desk_reads_accounting(cluster)
        scenario_desk_role_script(cluster, tmp)
    finally:
        cluster.stop()
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED, {PASSES} passed")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print(f"MIGRATE OK: {PASSES} passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
