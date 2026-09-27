"""Every column `docs/CONTRACT.md` declares for `macro_mart` exists in the DDL.

This is a drift detector, and it exists because the first two I wrote by hand
both gave the wrong answer and I believed the one that asked less of me.

A detector with a false negative is worse than none: it reports clean while the
thing it was built to catch is sitting there. So this one is a test, it runs on
every commit, and it fails loudly when it cannot parse rather than quietly
finding nothing.

## The three forms the migrations actually use

    -- A: one line
    alter table macro_mart.setup_state add column if not exists gate_text text null;

    -- B: two lines, one column
    alter table macro_mart.scenario_target_state
      add column if not exists comparator text null;

    -- C: one ALTER, several columns
    alter table macro_mart.phase_score
      add column if not exists blind_phase  int null,
      add column if not exists blind_agrees boolean null,
      add column if not exists stage        text null;

Twenty-five columns are added by ALTER across the migrations. A pattern anchored
on `alter table <table> add column` on one line reaches eleven of them, and one
anchored on a single `add column` per `alter table` truncates C to its first
column. Both of those were written, and between them they produced a false
negative on B and a false positive on C.

The fix is to parse by *statement*: take everything from `alter table` to the
next semicolon, then find every `add column` inside it. That is form-agnostic.

Five of the contract's `macro_mart` entries are views rather than tables, so
their columns come out of a select list instead of a column list, and a later
`create or replace view` supersedes an earlier one.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "supabase" / "migrations"
STORE = Path(__file__).resolve().parents[1] / "src" / "macro" / "store.py"
CONTRACT = ROOT / "docs" / "CONTRACT.md"

#: `- `macro_mart.x`: `a, b, c``  at the head of a contract bullet.
_DECLARATION = re.compile(
    r"^- `(macro_mart\.\w+)`:\s*`([^`]+)`", re.MULTILINE
)
#: A column name at the head of a create-table line, less the things that are
#: not columns.
_NOT_A_COLUMN = frozenset(
    {"primary", "unique", "constraint", "check", "foreign", "references", "create", "comment"}
)


def sql() -> str:
    files = sorted(MIGRATIONS.glob("*.sql"))
    assert files, f"no migrations found under {MIGRATIONS}"
    return "\n".join(one.read_text(encoding="utf-8") for one in files)


def created_columns(body: str) -> set[str]:
    out: set[str] = set()
    for line in body.splitlines():
        found = re.match(r"\s+(\w+)\s", line)
        if found and found.group(1).lower() not in _NOT_A_COLUMN:
            out.add(found.group(1))
    return out


def selected_columns(body: str) -> set[str]:
    """The output names of a view's select list.

    `p.id as phase_call_id` yields `phase_call_id`; `p.phase` yields `phase`.
    """
    inner = re.search(r"\bselect\b(.*?)\bfrom\b", body, re.S | re.I)
    if inner is None:
        return set()
    out: set[str] = set()
    depth = 0
    item = ""
    for char in inner.group(1):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            out.add(_output_name(item))
            item = ""
        else:
            item += char
    out.add(_output_name(item))
    return {one for one in out if one}


def _output_name(item: str) -> str:
    cleaned = " ".join(item.split())
    if not cleaned:
        return ""
    aliased = re.search(r"\bas\s+([\w\"]+)$", cleaned, re.I)
    if aliased:
        return aliased.group(1).strip('"')
    return cleaned.split(".")[-1].strip('"')


def migration_columns(table: str, text: str) -> set[str]:
    """Every column a table or view has after all the migrations, by any form.

    Statement-level: an `alter table` runs to its semicolon and may add several
    columns inside that span. Anchoring on one `add column` per `alter table`
    is what missed fourteen of twenty-five.

    A view is taken from its *last* definition, because `create or replace view`
    supersedes an earlier one and only the last is what the database holds.
    """
    out: set[str] = set()
    created = re.search(
        rf"create table if not exists {re.escape(table)}\s*\((.*?)\n\);", text, re.S
    )
    if created:
        out |= created_columns(created.group(1))
    # In file order, so a column a later migration drops is gone: `drop column`
    # is as much a form as `add column`, and a union of every add would report
    # a retired column as still there.
    for statement in re.findall(rf"alter table\s+{re.escape(table)}\b(.*?);", text, re.S):
        for verb, name in re.findall(
            r"(add|drop) column if (?:not )?exists\s+(\w+)", statement
        ):
            if verb == "add":
                out.add(name)
            else:
                out.discard(name)
    views = re.findall(
        rf"create (?:or replace )?view\s+{re.escape(table)}\b(.*?);", text, re.S
    )
    if views:
        out |= selected_columns(views[-1])
    return out


def contract_tables() -> dict[str, list[str]]:
    """Each `macro_mart` table the contract declares, and its column list."""
    text = CONTRACT.read_text(encoding="utf-8")
    out: dict[str, list[str]] = {}
    for table, columns in _DECLARATION.findall(text):
        # `freshness text check in ('fresh','due',...)` would otherwise split
        # into a column called `'due'`, so parenthesised spans go first.
        flattened = re.sub(r"\([^()]*\)", " ", columns)
        names = []
        for raw in flattened.split(","):
            word = raw.strip().split()[0] if raw.strip() else ""
            word = word.strip('"`')
            if word and word not in ("pk",) and word.isidentifier():
                names.append(word)
        if names:
            out[table] = names
    return out


def test_the_contract_declares_the_tables_this_test_is_about():
    """A parser that finds nothing must fail, not pass.

    This is the assertion the two hand-written checks were missing: both could
    have matched zero things and reported success.
    """
    tables = contract_tables()
    assert len(tables) >= 8, tables
    for expected in (
        "macro_mart.snapshot_row",
        "macro_mart.condition_state",
        "macro_mart.phase_score",
        "macro_mart.scenario_state",
        "macro_mart.scenario_target_state",
        "macro_mart.setup_state",
    ):
        assert expected in tables, f"{expected} is no longer parsed out of the contract"


def test_the_parser_sees_every_form_the_migrations_use():
    """Guards the fix itself.

    Each of these was added by a different syntactic form, and an earlier
    detector saw only the first.
    """
    text = sql()
    # Form A, one line.
    assert "gate_text" in migration_columns("macro_mart.setup_state", text)
    # Form B, two lines, one column.
    assert "comparator" in migration_columns("macro_mart.scenario_target_state", text)
    assert "series_label" in migration_columns("macro_mart.setup_state", text)
    # Form C, one ALTER, several columns: the second and third, not just the first.
    phase = migration_columns("macro_mart.phase_score", text)
    assert {"stage", "failed", "read_stale"} <= phase
    # And the create-table body, not only the ALTERs.
    assert {"asset", "met", "of"} <= phase
    # Form D, a later drop: 20260923000900 retired the upgrade and the blind read.
    assert not {"upgrade_eligible", "blind_phase", "blind_agrees"} & phase


#: Drift this worker has found, reported, and does not own the fix for. Each
#: entry is `xfail(strict=True)`, so the day the migration lands this test fails
#: for passing and the entry has to be deleted. A silently tolerated exception
#: is how a detector goes back to reporting clean.
KNOWN_DRIFT: dict[str, str] = {}


@pytest.mark.parametrize("table", sorted(contract_tables()))
def test_every_contract_column_exists_in_the_migrations(table: str):
    if table in KNOWN_DRIFT:
        pytest.xfail(KNOWN_DRIFT[table])
    declared = contract_tables()[table]
    actual = migration_columns(table, sql())
    assert actual, f"{table} was not found in any migration"
    missing = [one for one in declared if one not in actual]
    assert not missing, (
        f"{table} is declared in docs/CONTRACT.md with {missing}, which no migration "
        "creates. The worker must not write a column that does not exist: a MartFlush is "
        "one transaction, so the insert would roll back every other mart table with it."
    )


# ----------------------------------------------------------------- writes --


#: `["\s]*` spans the quotes of an insert written as concatenated single-line
#: strings. Requiring a newline before `values` skipped every one of those, which
#: is the same false negative this file was written about, one level up.
_INSERTS = re.compile(
    r'insert into (macro_\w+\.\w+)["\s]*\((.*?)\)["\s]*values["\s]*\((.*?)\)', re.S
)


def insert_columns() -> dict[str, set[str]]:
    """Each `macro_mart` table this worker inserts into, and the columns it names."""
    text = STORE.read_text(encoding="utf-8")
    out: dict[str, set[str]] = {}
    for table, columns, _ in _INSERTS.findall(text):
        if not table.startswith("macro_mart."):
            continue
        names = {one.strip() for one in columns.replace("\n", " ").split(",")}
        out[table] = {one for one in names if one.isidentifier()}
    return out


#: Columns that exist and that this worker deliberately does not write. Each one
#: is a decision with a reason, not a backlog: the default is that a column the
#: schema has is a column the worker fills, and anything here has to say why not.
NOT_WRITTEN: dict[str, dict[str, str]] = {}


def test_every_exception_is_still_needed():
    """An allowlist nobody prunes is how a detector goes quiet.

    Each entry has to name a column that the schema still has and the worker
    still does not write. A stale one would sit there excusing a column that
    came back.
    """
    for table, excused in NOT_WRITTEN.items():
        available = migration_columns(table, sql())
        written = insert_columns().get(table, set())
        for column, reason in excused.items():
            assert reason, f"{table}.{column} is excused without saying why"
            assert column in available, f"{table}.{column} no longer exists"
            assert column not in written, f"{table}.{column} is written now; drop the entry"


def test_the_insert_parser_sees_every_mart_write():
    """A parser that finds nothing must fail, not pass."""
    found = insert_columns()
    assert len(found) >= 8, f"only found inserts into {sorted(found)}"
    assert "comparator" in found["macro_mart.scenario_target_state"]
    assert "series_key" in found["macro_mart.setup_state"]


@pytest.mark.parametrize("table", sorted(insert_columns()))
def test_the_worker_writes_every_column_the_schema_gives_it(table: str):
    """The failure this file was born from, caught from the other side.

    `comparator`, `text`, `series_key` and `series_label` were in the migrations,
    in the live database and in the contract. The only place they were missing
    was the worker's insert, so every check that compared the contract to the
    DDL read clean while the four columns were null on every row.

    A column the schema has is a column the worker fills. An exception is a
    decision, and it lives in `NOT_WRITTEN` with its reason.
    """
    written = insert_columns()[table]
    available = migration_columns(table, sql())
    assert available, f"{table} was not found in any migration"
    excused = NOT_WRITTEN.get(table, {})
    unwritten = sorted(one for one in available - written if one not in excused)
    assert not unwritten, (
        f"{table} has {unwritten} in the schema and this worker never writes them, so "
        "those columns are null on every row and nothing says so. Either write them or "
        "add each one to NOT_WRITTEN with the reason it is deliberately blank."
    )


def test_every_insert_binds_exactly_as_many_placeholders_as_columns():
    """A miscount is caught here rather than at 09:30.

    A `MartFlush` is one transaction, so an insert that names twenty-two columns
    and binds twenty-one fails the whole evaluation and writes nothing, for a
    reason that has nothing to do with the data.
    """
    text = STORE.read_text(encoding="utf-8")
    statements = _INSERTS.findall(text)
    assert len(statements) >= 8, "the insert parser found almost nothing"
    for table, columns, placeholders in statements:
        named = [one for one in columns.replace("\n", " ").split(",") if one.strip()]
        # A value can be a literal rather than a parameter, as `status` is on
        # `ingest_run`, so the arity is the item count and not the `$n` count.
        values = [one for one in placeholders.replace("\n", " ").split(",") if one.strip()]
        assert len(named) == len(values), (
            f"{table} names {len(named)} columns and supplies {len(values)} values"
        )
        # A parameter may repeat, as `first_seen` and `last_seen` do on
        # `catalyst`, so this checks for a gap rather than for a sequence.
        bound = {int(one) for one in re.findall(r"\$(\d+)", placeholders)}
        assert bound == set(range(1, len(bound) + 1)), (
            f"{table} skips a placeholder: it binds {sorted(bound)}"
        )
