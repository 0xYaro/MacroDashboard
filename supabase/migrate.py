#!/usr/bin/env python3
"""Apply this repository's migrations to the shared GreenfieldFund database.

Why this exists rather than `supabase db push`
----------------------------------------------
Supabase records applied migrations in `supabase_migrations.schema_migrations`,
which is one table per DATABASE, not per schema. Accounting lives in the same
project and owns rows in it. The CLI cannot manage a database whose migration
history it does not wholly own: `db push` refuses, `--include-all` refuses
identically, and the only remedy it offers is `migration repair`, which rewrites
the other product's history. See the last two entries in docs/DECISIONS.md.

So this repository applies its own migrations and keeps its own ledger, in a
table it owns, in a schema it owns. `supabase_migrations.schema_migrations` is
Accounting's and is never read for a decision and never written at all.

What it guarantees
------------------
* Every `*.sql` file in supabase/migrations is discovered by extension, in name
  order. Never by prefix glob and never against a pinned count: a `0*.sql`
  pattern silently matched nothing after the timestamp rename, and that class of
  bug has now appeared twice in this build.
* Each pending file is applied in its own transaction, together with its ledger
  row. A file that fails rolls back whole and the run stops there, leaving every
  earlier file applied and recorded.
* A file already recorded whose content has since changed is refused, loudly,
  before anything is applied. Drift is the failure a hand-rolled migrator risks,
  so it is the thing this refuses hardest.
* Running it twice applies nothing the second time.

Usage
-----
    export DATABASE_URL='postgresql://...'   # the Supabase session pooler
    python3 supabase/migrate.py --status
    python3 supabase/migrate.py --dry-run
    python3 supabase/migrate.py

DATABASE_URL is read from the environment and nowhere else. This script does not
read .env, .env.local or any dotenv file, and it has no default connection.

Requires `psql` on PATH and PostgreSQL 13 or newer on the server.
Standard library only: no uv project, no virtualenv, no third-party driver.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

LEDGER_SCHEMA = "macro_quality"
LEDGER_TABLE = "schema_migration"
LEDGER = f"{LEDGER_SCHEMA}.{LEDGER_TABLE}"

# Supabase's own convention: a fourteen-digit UTC timestamp, an underscore, a
# snake_case name. Enforced so that name order is always timestamp order.
FILENAME_RE = re.compile(r"^(\d{14})_[a-z0-9]+(?:_[a-z0-9]+)*\.sql$")

HASH_ALGO = "sha256"

# Field and record separators for psql's unaligned tuple output. Chosen as ASCII
# control characters so that no SQL value can contain one.
FS = "\x1f"
RS = "\x1e"

# Statements that would break the "one file, one transaction" guarantee, or that
# cannot run inside a transaction block at all. Checked against the file with
# comments and quoted strings removed, so `begin` inside a plpgsql body does not
# trip it.
FORBIDDEN_STATEMENTS = [
    (re.compile(r"\bbegin\b"), "BEGIN"),
    (re.compile(r"\bcommit\b"), "COMMIT"),
    (re.compile(r"\brollback\b"), "ROLLBACK"),
    (re.compile(r"\bsavepoint\b"), "SAVEPOINT"),
    (re.compile(r"\bstart\s+transaction\b"), "START TRANSACTION"),
    (re.compile(r"\bcreate\s+index\s+concurrently\b"), "CREATE INDEX CONCURRENTLY"),
    (re.compile(r"\bdrop\s+index\s+concurrently\b"), "DROP INDEX CONCURRENTLY"),
    (re.compile(r"\balter\s+system\b"), "ALTER SYSTEM"),
    (re.compile(r"\bvacuum\b"), "VACUUM"),
    (re.compile(r"\bcreate\s+database\b"), "CREATE DATABASE"),
    (re.compile(r"\bdrop\s+database\b"), "DROP DATABASE"),
]

# Accounting's ledger. Naming it at all from a migration of ours is a mistake we
# would rather catch here than in production.
FOREIGN_LEDGER_RE = re.compile(r"supabase_migrations", re.IGNORECASE)


class MigrateError(Exception):
    """A refusal. Printed as the reason and the run stops; nothing is applied."""


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Migration:
    version: str
    filename: str
    path: Path
    content_hash: str


@dataclass(frozen=True)
class Applied:
    version: str
    filename: str
    content_hash: str
    applied_at: str
    duration_ms: str


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def discover(directory: Path) -> list[Migration]:
    """Every .sql file in `directory`, in name order.

    By extension, never by prefix. A pattern that encodes today's naming scheme
    stops matching the day the naming scheme changes, and does it silently.
    """
    if not directory.is_dir():
        raise MigrateError(f"migrations directory not found: {directory}")

    found: list[Migration] = []
    offenders: list[str] = []
    for entry in sorted(directory.iterdir(), key=lambda p: p.name):
        if entry.name.startswith("."):
            continue
        if not entry.is_file():
            continue
        if entry.suffix != ".sql":
            offenders.append(f"{entry.name}: not a .sql file")
            continue
        match = FILENAME_RE.match(entry.name)
        if not match:
            offenders.append(
                f"{entry.name}: does not match <14-digit timestamp>_<snake_case>.sql"
            )
            continue
        found.append(
            Migration(
                version=match.group(1),
                filename=entry.name,
                path=entry,
                content_hash=hash_file(entry),
            )
        )

    if offenders:
        raise MigrateError(
            "migrations directory holds files this migrator will not order:\n  "
            + "\n  ".join(offenders)
            + "\nRename or remove them. Ordering is by filename, so a file that "
            "does not carry a timestamp has no defined position."
        )

    if not found:
        raise MigrateError(
            f"no migrations found in {directory}. "
            "That is almost certainly a path or naming bug rather than an empty "
            "repository, so it is treated as an error rather than as nothing to do."
        )

    versions: dict[str, str] = {}
    for m in found:
        if m.version in versions:
            raise MigrateError(
                f"two migrations share the version {m.version}: "
                f"{versions[m.version]} and {m.filename}"
            )
        versions[m.version] = m.filename

    return found


def hash_file(path: Path) -> str:
    digest = hashlib.new(HASH_ALGO)
    digest.update(path.read_bytes())
    return f"{HASH_ALGO}:{digest.hexdigest()}"


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------


def strip_sql(text: str) -> str:
    """Return `text` with comments and quoted literals blanked out.

    Dollar-quoted bodies are removed whole, which is what lets the forbidden
    statement scan below run without flagging the `begin` of every plpgsql
    function in 20260921000500_core_constraints.sql.
    """
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]

        if ch == "-" and text.startswith("--", i):
            j = text.find("\n", i)
            i = n if j == -1 else j
            continue

        if ch == "/" and text.startswith("/*", i):
            depth = 1
            i += 2
            while i < n and depth:
                if text.startswith("/*", i):
                    depth += 1
                    i += 2
                elif text.startswith("*/", i):
                    depth -= 1
                    i += 2
                else:
                    i += 1
            continue

        if ch == "'":
            i += 1
            while i < n:
                if text[i] == "'":
                    if text.startswith("''", i):
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            out.append(" ")
            continue

        if ch == '"':
            i += 1
            while i < n and text[i] != '"':
                i += 1
            i += 1
            out.append(" ")
            continue

        if ch == "$":
            tag = re.match(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$", text[i:])
            if tag:
                marker = tag.group(0)
                end = text.find(marker, i + len(marker))
                i = n if end == -1 else end + len(marker)
                out.append(" ")
                continue

        out.append(ch)
        i += 1

    return "".join(out)


def preflight(m: Migration) -> None:
    """Refuse a file that cannot honour "one file, one transaction".

    20260921001200_mart_judgment_views.sql drops and recreates nine views, so a
    reader must never see the window between the drop and the create. The
    per-file transaction gives that, but only for a file that does not open or
    close a transaction of its own, so it is checked rather than assumed.
    """
    text = m.path.read_text(encoding="utf-8")

    for line_no, line in enumerate(text.splitlines(), start=1):
        if line.startswith("\\"):
            raise MigrateError(
                f"{m.filename}:{line_no} starts with a psql meta-command "
                f"({line.split()[0]}). Migrations are plain SQL: a meta-command can "
                "leave the transaction, change connection or quit the session, none "
                "of which this migrator can roll back."
            )

    if FOREIGN_LEDGER_RE.search(text):
        raise MigrateError(
            f"{m.filename} mentions supabase_migrations. That schema is "
            "Accounting's migration history, shared with this system only because "
            "the two products share one database. It is never read for a decision "
            "here and never written."
        )

    code = strip_sql(text).lower()
    for pattern, name in FORBIDDEN_STATEMENTS:
        if pattern.search(code):
            raise MigrateError(
                f"{m.filename} contains {name}. Every migration is applied inside a "
                "transaction this migrator opens and closes, together with its "
                "ledger row. A file that manages its own transaction, or that "
                "cannot run inside one, would break the guarantee that a failed "
                "migration leaves nothing behind."
            )


# ---------------------------------------------------------------------------
# psql
# ---------------------------------------------------------------------------


def psql_path() -> str:
    found = os.environ.get("PSQL") or shutil.which("psql")
    if not found:
        raise MigrateError(
            "psql not found on PATH. It ships with the Supabase CLI and with "
            "libpq; set PSQL to its full path if it is installed elsewhere."
        )
    return found


def database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise MigrateError(
            "DATABASE_URL is not set. Point it at the Supabase session pooler and "
            "export it in the shell you run this from. This script reads the "
            "environment and nothing else: it does not load .env."
        )
    return url


def query(sql: str) -> list[list[str]]:
    """Run a read-only statement and return its rows as lists of strings."""
    result = subprocess.run(
        [
            psql_path(),
            "--no-psqlrc",
            "--quiet",
            "--no-align",
            "--tuples-only",
            "--field-separator", FS,
            "--record-separator", RS,
            "--variable", "ON_ERROR_STOP=1",
            "--command", sql,
            database_url(),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise MigrateError(
            "could not query the database:\n" + (result.stderr.strip() or "psql failed")
        )
    body = result.stdout.strip(RS + "\n")
    if not body:
        return []
    return [row.split(FS) for row in body.split(RS) if row.strip()]


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------

BOOTSTRAP_SQL = f"""
create schema if not exists {LEDGER_SCHEMA};

create table if not exists {LEDGER} (
  version       text primary key,
  filename      text not null unique,
  content_hash  text not null,
  applied_at    timestamptz not null default now(),
  duration_ms   int not null
);

comment on table {LEDGER} is
  'This repository''s own migration ledger. supabase_migrations.schema_migrations is scoped to the database, not the schema, and belongs to Accounting; it is never read for a decision here and never written. See docs/DECISIONS.md and supabase/migrate.py.';
comment on column {LEDGER}.content_hash is
  'sha256 of the file as applied. A recorded migration whose file has since changed is refused before anything is applied.';
"""


def ledger_exists() -> bool:
    rows = query(f"select to_regclass({sql_literal(LEDGER)}) is not null")
    return bool(rows) and rows[0][0] == "t"


def bootstrap_ledger() -> None:
    run_script(BOOTSTRAP_SQL, label="ledger bootstrap")


def read_ledger() -> dict[str, Applied]:
    if not ledger_exists():
        return {}
    rows = query(
        "select version, filename, content_hash, "
        "to_char(applied_at at time zone 'UTC', 'YYYY-MM-DD HH24:MI:SS'), "
        f"duration_ms::text from {LEDGER} order by version"
    )
    return {r[0]: Applied(*r) for r in rows}


def run_script(sql: str, label: str) -> None:
    with tempfile.NamedTemporaryFile(
        "w", suffix=".sql", delete=False, encoding="utf-8"
    ) as fh:
        fh.write(sql)
        temp = fh.name
    try:
        result = subprocess.run(
            [
                psql_path(),
                "--no-psqlrc",
                "--quiet",
                "--variable", "ON_ERROR_STOP=1",
                "--file", temp,
                database_url(),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise MigrateError(
                f"{label} failed:\n"
                + (result.stderr.strip() or "psql failed with no message")
            )
    finally:
        os.unlink(temp)


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def wrapper_sql(m: Migration) -> str:
    """The transaction that carries one migration and its ledger row.

    The assertion in the middle is the one the contract asked to be checked
    rather than assumed: if the file managed to commit or roll back on its own,
    the transaction-local marker is gone and the transaction id has moved, and
    the run stops before the ledger records a half-applied file.
    """
    if "'" in str(m.path):
        raise MigrateError(
            f"migration path contains a single quote and cannot be included "
            f"safely: {m.path}"
        )
    return f"""\\set QUIET on
begin;

select set_config('macro_migrate.started_at', clock_timestamp()::text, true),
       set_config('macro_migrate.xid', pg_current_xact_id()::text, true);

\\i '{m.path}'

do $macro_migrate_assert$
begin
  if coalesce(current_setting('macro_migrate.xid', true), '') <> pg_current_xact_id()::text then
    raise exception
      'migration {m.filename} did not run as one transaction: it opened or closed a transaction of its own'
      using errcode = 'invalid_transaction_state';
  end if;
end
$macro_migrate_assert$;

insert into {LEDGER} (version, filename, content_hash, applied_at, duration_ms)
values (
  {sql_literal(m.version)},
  {sql_literal(m.filename)},
  {sql_literal(m.content_hash)},
  clock_timestamp(),
  greatest(0, (extract(epoch from (
    clock_timestamp() - current_setting('macro_migrate.started_at')::timestamptz
  )) * 1000)::int)
);

commit;
"""


def apply(m: Migration) -> None:
    run_script(wrapper_sql(m), label=f"migration {m.filename}")


# ---------------------------------------------------------------------------
# Checks that run before anything is applied
# ---------------------------------------------------------------------------


def check_drift(migrations: list[Migration], applied: dict[str, Applied]) -> None:
    by_version = {m.version: m for m in migrations}
    problems: list[str] = []

    for version, record in sorted(applied.items()):
        m = by_version.get(version)
        if m is None:
            problems.append(
                f"{version} ({record.filename}) is recorded as applied but no file "
                "with that version exists any more"
            )
            continue
        if m.filename != record.filename:
            problems.append(
                f"{version} was applied as {record.filename} and is now called "
                f"{m.filename}"
            )
            continue
        if m.content_hash != record.content_hash:
            problems.append(
                f"{m.filename} has changed since it was applied on "
                f"{record.applied_at} UTC\n"
                f"      recorded {record.content_hash}\n"
                f"      on disk  {m.content_hash}"
            )

    if problems:
        raise MigrateError(
            "the migrations on disk disagree with the ledger:\n  - "
            + "\n  - ".join(problems)
            + "\n\nNothing has been applied. An applied migration is a statement "
            "about what the database already is, so editing one makes the ledger a "
            "record of something that never happened. Add a new migration that "
            "makes the change, or, if the edit is known to be cosmetic and the "
            "database is known to match, update content_hash in "
            f"{LEDGER} by hand and say so."
        )


def check_order(migrations: list[Migration], applied: dict[str, Applied]) -> None:
    if not applied:
        return
    highest = max(applied)
    late: list[str] = [
        m.filename for m in migrations if m.version not in applied and m.version < highest
    ]
    if late:
        raise MigrateError(
            "these migrations are pending but sort before one that is already "
            "applied:\n  - "
            + "\n  - ".join(late)
            + f"\n\nThe highest applied version is {highest}. Nothing has been "
            "applied. Files run in name order and each one assumes every earlier "
            "one has run, so a file inserted into the past would run against a "
            "schema it was not written for. Rename it with a timestamp after "
            f"{highest}."
        )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_status(migrations: list[Migration], applied: dict[str, Applied]) -> int:
    width = max(len(m.filename) for m in migrations)
    print(f"{'FILE'.ljust(width)}  STATUS      APPLIED (UTC)        TOOK")
    print(f"{'-' * width}  ----------  -------------------  --------")
    pending = 0
    for m in migrations:
        record = applied.get(m.version)
        if record is None:
            pending += 1
            print(f"{m.filename.ljust(width)}  pending")
        else:
            drift = "" if record.content_hash == m.content_hash else "  <- CHANGED ON DISK"
            print(
                f"{m.filename.ljust(width)}  applied     {record.applied_at}  "
                f"{record.duration_ms + ' ms':>8}{drift}"
            )
    print()
    print(f"{len(applied)} applied, {pending} pending, {len(migrations)} total")
    orphans = sorted(set(applied) - {m.version for m in migrations})
    if orphans:
        print()
        print("recorded but missing from disk: " + ", ".join(orphans))
    return 0


def cmd_dry_run(migrations: list[Migration], applied: dict[str, Applied]) -> int:
    pending = [m for m in migrations if m.version not in applied]
    check_drift(migrations, applied)
    check_order(migrations, applied)
    for m in pending:
        preflight(m)

    if not pending:
        print(f"nothing to apply: all {len(migrations)} migrations are recorded.")
        return 0

    print(f"would apply {len(pending)} migration(s), each in its own transaction:")
    for m in pending:
        print(f"  {m.filename}  {m.content_hash}")
    print()
    print("nothing was written. Re-run without --dry-run to apply.")
    return 0


def cmd_apply(migrations: list[Migration]) -> int:
    bootstrap_ledger()
    applied = read_ledger()

    check_drift(migrations, applied)
    check_order(migrations, applied)

    pending = [m for m in migrations if m.version not in applied]
    for m in pending:
        preflight(m)

    if not pending:
        print(f"nothing to apply: all {len(migrations)} migrations are recorded.")
        return 0

    print(f"applying {len(pending)} migration(s) to {redact(database_url())}")
    for m in pending:
        print(f"  {m.filename} ... ", end="")
        sys.stdout.flush()
        try:
            apply(m)
        except MigrateError as exc:
            # Flush first: the progress line above is on stdout and the reason goes to
            # stderr, and a reader looking at a terminal must see them in that order.
            print("FAILED")
            sys.stdout.flush()
            print(f"\n{exc}", file=sys.stderr)
            print(
                f"\nThis file rolled back whole and was not recorded. "
                f"Every file before it is applied and recorded. "
                f"Fix it and run again: the run resumes at {m.filename}.",
                file=sys.stderr,
            )
            sys.stderr.flush()
            return 1
        print("ok")

    print()
    print(f"applied {len(pending)}, {len(migrations)} total recorded.")
    return 0


def redact(url: str) -> str:
    return re.sub(r"//([^:/@]+):[^@]*@", r"//\1:***@", url)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="migrate.py",
        description=(
            "Apply supabase/migrations to the database named by DATABASE_URL, and "
            "record each one in macro_quality.schema_migration. This repository "
            "never runs supabase db push, db pull or migration repair; see "
            "docs/DECISIONS.md."
        ),
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--status",
        action="store_true",
        help="print applied versus pending and exit, writing nothing",
    )
    group.add_argument(
        "--dry-run",
        action="store_true",
        help="run every check and list what would be applied, writing nothing",
    )
    parser.add_argument(
        "--migrations-dir",
        type=Path,
        default=MIGRATIONS_DIR,
        help=f"default: {MIGRATIONS_DIR}",
    )
    args = parser.parse_args(argv)

    try:
        migrations = discover(args.migrations_dir)
        if args.status:
            return cmd_status(migrations, read_ledger())
        if args.dry_run:
            return cmd_dry_run(migrations, read_ledger())
        return cmd_apply(migrations)
    except MigrateError as exc:
        print(f"\nmigrate.py: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
