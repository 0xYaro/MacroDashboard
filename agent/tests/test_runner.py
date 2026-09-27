"""The run queue: its schedule, its activation plan, and one tick end to end.

The pure tests pin the decisions the runner makes. The database tests build a
throwaway Postgres with the Supabase shim applied first, so `auth.uid()` and the
three roles exist and the web's two functions can be called exactly as the
Desk calls them.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from macro_agent import runner
from macro_agent.runner import ActiveRow, ProposedRow, Recent
from macro_agent.store import Store

from .conftest import MIGRATIONS, ROOT

# Monday 28 September 2026, Singapore.
MON_0935 = datetime(2026, 9, 28, 1, 35, tzinfo=UTC)
MON_0945 = datetime(2026, 9, 28, 1, 45, tzinfo=UTC)
MON_1005 = datetime(2026, 9, 28, 2, 5, tzinfo=UTC)
MON_1030 = datetime(2026, 9, 28, 2, 30, tzinfo=UTC)
MON_1100 = datetime(2026, 9, 28, 3, 0, tzinfo=UTC)
TUE_0945 = datetime(2026, 9, 29, 1, 45, tzinfo=UTC)
TUE_1005 = datetime(2026, 9, 29, 2, 5, tzinfo=UTC)
WED_1005 = datetime(2026, 9, 30, 2, 5, tzinfo=UTC)
SAT_1000 = datetime(2026, 9, 26, 2, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def no_live_rescore(monkeypatch):
    """Every accept is followed by a worker rescore; in a test it must never be the real one."""
    monkeypatch.setenv("MACRO_UV", "true")
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody@127.0.0.1:1/none")


def recent(
    kind: str,
    at: datetime,
    status: str = "done",
    origin: str = "schedule",
    finished: datetime | None = None,
) -> Recent:
    return Recent(
        kind=kind, origin=origin, requested_at=at, status=status, finished_at=finished or at
    )


# ---------------------------------------------------------------------------
# The schedule
# ---------------------------------------------------------------------------


def test_the_narrative_waits_for_nine_forty_and_the_full_run():
    assert runner.scheduled_due(MON_0935, [], full_run_today=True) == []
    assert runner.scheduled_due(MON_0945, [], full_run_today=False) == []
    assert runner.scheduled_due(MON_0945, [], full_run_today=True) == ["narrative"]


def test_a_failed_narrative_is_retried_once_and_then_left():
    """One retry, fifteen minutes after the failure ended, and never a loop."""
    failed = [recent("narrative", MON_0945, status="failed")]
    assert runner.scheduled_due(MON_0945, failed, full_run_today=True) == []
    assert runner.scheduled_due(MON_1005, failed, full_run_today=True) == ["narrative"]
    twice = [*failed, recent("narrative", MON_1005, status="failed")]
    assert runner.scheduled_due(MON_1100, twice, full_run_today=True) == []
    done = [recent("narrative", MON_0945, status="done")]
    assert "narrative" not in runner.scheduled_due(MON_1100, done, full_run_today=True)


def test_an_operator_amend_does_not_count_as_the_days_narrative():
    theirs = [recent("amend", MON_0935, origin="operator")]
    assert runner.scheduled_due(MON_0945, theirs, full_run_today=True) == ["narrative"]


def test_the_monday_author_waits_for_a_finished_narrative():
    running = [recent("narrative", MON_0945, status="running")]
    assert runner.scheduled_due(MON_1005, running, full_run_today=True) == []
    done = [recent("narrative", MON_0945, status="done")]
    assert runner.scheduled_due(MON_1005, done, full_run_today=True) == ["author"]
    done_and_authored = [*done, recent("author", MON_1005, status="proposed")]
    assert runner.scheduled_due(MON_1005, done_and_authored, full_run_today=True) == []


def test_a_failed_author_is_retried_once_on_monday():
    done = [recent("narrative", MON_0945, status="done")]
    failed = [*done, recent("author", MON_1005, status="failed")]
    assert runner.scheduled_due(MON_1005, failed, full_run_today=True) == []
    assert runner.scheduled_due(MON_1030, failed, full_run_today=True) == ["author"]
    twice = [*failed, recent("author", MON_1030, status="failed")]
    assert runner.scheduled_due(MON_1100, twice, full_run_today=True) == []


def test_a_week_that_monday_never_authored_falls_back_to_tuesday():
    """The week is not lost: Tuesday gets the author, after Tuesday's narrative."""
    monday = [
        recent("narrative", MON_0945, status="done"),
        recent("author", MON_1005, status="failed"),
        recent("author", MON_1030, status="failed"),
    ]
    assert runner.scheduled_due(TUE_1005, monday, full_run_today=True) == ["narrative"]
    tuesday = [*monday, recent("narrative", TUE_0945, status="done")]
    assert runner.scheduled_due(TUE_1005, tuesday, full_run_today=True) == ["author"]
    # Wednesday is too late: two days of attempts is the budget.
    assert "author" not in runner.scheduled_due(WED_1005, tuesday, full_run_today=True)


def test_tuesday_does_not_wait_for_a_narrative_that_failed_twice():
    """Monday's narrative never landed, so Monday never authored."""
    monday = [
        recent("narrative", MON_0945, status="failed"),
        recent("narrative", MON_1005, status="failed"),
    ]
    assert "author" not in runner.scheduled_due(MON_1100, monday, full_run_today=True)
    tuesday = [
        *monday,
        recent("narrative", TUE_0945, status="failed"),
        recent("narrative", datetime(2026, 9, 29, 2, 1, tzinfo=UTC), status="failed"),
    ]
    assert runner.scheduled_due(TUE_1005, tuesday, full_run_today=True) == ["author"]


def test_a_proposed_or_rejected_week_is_never_re_run():
    done = [recent("narrative", MON_0945, status="done")]
    for status in ("proposed", "accepted", "rejected", "running"):
        week = [*done, recent("author", MON_1005, status=status)]
        assert runner.scheduled_due(MON_1100, week, full_run_today=True) == []
        tuesday = [*week, recent("narrative", TUE_0945, status="done")]
        assert runner.scheduled_due(TUE_1005, tuesday, full_run_today=True) == []


def test_nothing_is_scheduled_at_the_weekend():
    assert runner.scheduled_due(SAT_1000, [], full_run_today=True) == []


# ---------------------------------------------------------------------------
# The activation plan
# ---------------------------------------------------------------------------


def proposed(table: str, id: str, week: str = "2026-W40", **kw) -> ProposedRow:
    return ProposedRow(
        table=table,
        id=id,
        week=week,
        asset=kw.get("asset"),
        name=kw.get("name"),
        amends=kw.get("amends"),
    )


def active(table: str, id: str, week: str = "2026-W39", **kw) -> ActiveRow:
    return ActiveRow(table=table, id=id, week=week, asset=kw.get("asset"), name=kw.get("name"))


def test_a_weekly_author_supersedes_each_subject_and_retires_the_rest():
    plan = runner.plan_activation(
        "author",
        [
            proposed("strategy", "st-eq-40", asset="equities"),
            proposed("scenario_set", "set40"),
            proposed("phase_call", "pc-eq-40", asset="equities"),
            proposed("setup", "su-btc-40", asset="crypto", name="BTC $82K acceptance retest"),
        ],
        [
            active("scenario_set", "set39"),
            active("phase_call", "pc-eq-39", asset="equities"),
            active("phase_call", "pc-cr-39", asset="crypto"),
            active("strategy", "st-eq-39", asset="equities"),
            active("setup", "su-btc-39", asset="crypto", name="BTC $82K acceptance retest"),
            active("setup", "su-ndx-39", asset="equities", name="Nasdaq failed-push fade"),
        ],
    )
    assert [(one.table, one.id, one.supersede) for one in plan.activations] == [
        ("scenario_set", "set40", "set39"),
        ("phase_call", "pc-eq-40", "pc-eq-39"),
        ("setup", "su-btc-40", "su-btc-39"),
        ("strategy", "st-eq-40", "st-eq-39"),
    ]
    # Not re-proposed, so not re-affirmed: the crypto call and the Nasdaq fade go.
    assert sorted(plan.retire) == ["pc-cr-39", "su-ndx-39"]


def test_an_amendment_supersedes_only_the_row_it_names_and_retires_nothing():
    plan = runner.plan_activation(
        "amend",
        [proposed("strategy", "st-eq-39b", week="2026-W39", asset="equities", amends="st-eq-39")],
        [
            active("strategy", "st-eq-39", asset="equities"),
            active("setup", "su-ndx-39", asset="equities", name="Nasdaq failed-push fade"),
        ],
    )
    assert [(one.id, one.supersede) for one in plan.activations] == [("st-eq-39b", "st-eq-39")]
    assert plan.retire == ()


# ---------------------------------------------------------------------------
# What the run said
# ---------------------------------------------------------------------------


def test_the_last_summary_or_no_change_line_is_the_outcome():
    said = runner.read_outcome("working...\nSUMMARY: early draft\nmore\nSUMMARY: W40 proposed.\n")
    assert said.summary == "W40 proposed."
    assert said.no_change is False
    none = runner.read_outcome("checked drift\nNO_CHANGE: nothing material has moved.")
    assert none.no_change is True
    assert none.summary == "nothing material has moved."


def test_the_amend_prompt_carries_the_operators_words_and_the_rule():
    text = runner.prompt_for("amend", reason="oil broke $95", now=MON_1005)
    assert '"oil broke $95"' in text
    assert "material change" in text
    assert "Never run `macro-agent accept`" in text
    assert "author context --amend" in text
    assert "never carries a phase call" in text
    author = runner.prompt_for("author", reason=None, now=MON_1005)
    assert "2026-W40" in author
    assert "Begin with B1" in author and "author classify" in author


# ---------------------------------------------------------------------------
# One tick, against a real Postgres
# ---------------------------------------------------------------------------

PORT = 55437
OPERATOR = "00000000-0000-4000-8000-000000000001"
SHIM = ROOT / "supabase" / "tests" / "local_shim.sql"

needs_postgres = pytest.mark.skipif(
    not (shutil.which("initdb") and shutil.which("pg_ctl") and shutil.which("psql")),
    reason="no local Postgres",
)


def _psql(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["psql", "-h", "127.0.0.1", "-p", str(PORT), "-U", "postgres", "-v", "ON_ERROR_STOP=1",
         *args],
        capture_output=True,
        text=True,
    )


@pytest.fixture(scope="module")
def cluster():
    base = Path(tempfile.mkdtemp(prefix="runnerpg", dir="/tmp"))
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
        ["pg_ctl", "-D", str(data), "-o", f"-k {base} -p {PORT} -c listen_addresses=127.0.0.1",
         "-l", str(base / "log"), "start"],
        capture_output=True,
        check=True,
        env={**os.environ, "LC_ALL": "C"},
    )
    for _ in range(40):
        if _psql("-d", "postgres", "-tAc", "select 1").returncode == 0:
            break
        time.sleep(0.25)
    assert _psql("-d", "postgres", "-c", "create database macro").returncode == 0
    assert _psql("-d", "macro", "-q", "-f", str(SHIM)).returncode == 0
    for one in sorted(MIGRATIONS.glob("*.sql")):
        done = _psql("-d", "macro", "-q", "-f", str(one))
        assert done.returncode == 0, f"{one.name}: {done.stderr}"
    try:
        yield f"postgresql://postgres@127.0.0.1:{PORT}/macro"
    finally:
        subprocess.run(["pg_ctl", "-D", str(data), "-m", "immediate", "stop"], capture_output=True)
        shutil.rmtree(base, ignore_errors=True)


def as_operator(sql: str) -> subprocess.CompletedProcess[str]:
    """One statement as the signed-in operator, the way PostgREST runs the Desk's call."""
    return _psql(
        "-d",
        "macro",
        "-tA",
        "-c",
        f"set request.jwt.claim.sub = '{OPERATOR}'; set role authenticated; {sql}",
    )


@pytest.fixture
async def store(cluster):
    _psql("-d", "macro", "-c", "truncate macro_core.run_request; "
          "delete from macro_core.strategy; delete from macro_quality.ingest_run;")
    found = await Store.connect(cluster, ssl="disable")
    try:
        yield found
    finally:
        await found.aclose()


def fake_claude(tmp: Path, body: str) -> str:
    script = tmp / "claude"
    script.write_text("#!/bin/sh\n" + body)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


@needs_postgres
async def test_the_desks_refresh_needs_a_signed_in_operator_and_a_reason(store):
    anon = _psql("-d", "macro", "-tA", "-c",
                 "set role authenticated; select macro_mart.request_refresh('oil broke $95')")
    assert anon.returncode != 0
    assert "signed-in operator" in anon.stderr
    blank = as_operator("select macro_mart.request_refresh('  ')")
    assert "names what changed" in blank.stderr


@needs_postgres
async def test_a_double_click_queues_one_refresh(store):
    first = as_operator("select macro_mart.request_refresh('oil broke $95')")
    second = as_operator("select macro_mart.request_refresh('oil broke $95 again')")
    assert first.returncode == 0, first.stderr
    assert first.stdout.split()[-1] == second.stdout.split()[-1]
    seen = as_operator("select count(*), min(status) from macro_mart.run_request_state")
    assert seen.stdout.strip().splitlines()[-1] == "1|queued"


@needs_postgres
async def test_the_operator_cannot_touch_the_queue_table_directly(store):
    refused = as_operator("insert into macro_core.run_request (kind, origin) values ('amend', 'operator')")
    assert refused.returncode != 0


@needs_postgres
async def test_a_refresh_runs_proposes_and_is_accepted(store, tmp_path, monkeypatch):
    # The active W39 equities strategy the amendment will replace.
    _psql("-d", "macro", "-c",
          "insert into macro_core.strategy (id, week, asset, stance, posture, status, activated_at) "
          "values ('11111111-1111-4111-8111-111111111111', '2026-W39', 'equities', "
          "'Hold and upgrade quality', 'selective', 'active', now() - interval '2 days')")
    as_operator("select macro_mart.request_refresh('breadth broke 45')")

    # A stand-in for the Claude session: it writes one proposed amendment, as
    # SKILL.md's amending path would, and says so on its last line.
    claude = fake_claude(
        tmp_path,
        f"psql -h 127.0.0.1 -p {PORT} -U postgres -d macro -q -c \""
        "insert into macro_core.strategy (id, week, asset, stance, posture, status, amends, "
        "amend_reason) values ('22222222-2222-4222-8222-222222222222', '2026-W39', 'equities', "
        "'Reduce', 'defensive', 'proposed', '11111111-1111-4111-8111-111111111111', "
        "'a phase condition flipped: breadth under 45')\"\n"
        "echo 'SUMMARY: amended the equities strategy to Reduce.'\n",
    )
    monkeypatch.setenv("MACRO_CLAUDE", claude)

    report = await runner.tick(store, now=datetime.now(UTC))
    assert report["claimed"]["kind"] == "amend"
    assert report["result"] == "proposed"
    state = as_operator("select status, summary, proposed->0->>'table' from macro_mart.run_request_state")
    assert state.stdout.strip().splitlines()[-1] == (
        "proposed|amended the equities strategy to Reduce.|strategy"
    )

    request_id = as_operator("select id from macro_mart.run_request_state").stdout.split()[-1]
    decided = as_operator(f"select macro_mart.decide_proposal({request_id}, 'accept')")
    assert decided.returncode == 0, decided.stderr
    again = as_operator(f"select macro_mart.decide_proposal({request_id}, 'reject')")
    assert "not a proposal awaiting a decision" in again.stderr

    report = await runner.tick(store, now=datetime.now(UTC))
    assert report["decision"]["result"] == "accepted"
    rows = _psql("-d", "macro", "-tA", "-c",
                 "select stance, status, superseded_by is not null from macro_core.strategy "
                 "order by authored_at")
    assert rows.stdout.strip().splitlines() == ["Hold and upgrade quality|superseded|t",
                                                "Reduce|active|f"]


@needs_postgres
async def test_a_rejected_proposal_was_never_believed(store, tmp_path, monkeypatch):
    as_operator("select macro_mart.request_refresh('checking')")
    claude = fake_claude(
        tmp_path,
        f"psql -h 127.0.0.1 -p {PORT} -U postgres -d macro -q -c \""
        "insert into macro_core.strategy (week, asset, stance, posture, status) values "
        "('2026-W39', 'crypto', 'Add', 'constructive', 'proposed')\"\n"
        "echo 'SUMMARY: proposed a crypto strategy.'\n",
    )
    monkeypatch.setenv("MACRO_CLAUDE", claude)
    await runner.tick(store, now=datetime.now(UTC))
    request_id = as_operator("select id from macro_mart.run_request_state").stdout.split()[-1]
    as_operator(f"select macro_mart.decide_proposal({request_id}, 'reject')")
    report = await runner.tick(store, now=datetime.now(UTC))
    assert report["decision"]["result"] == "rejected"
    rows = _psql("-d", "macro", "-tA", "-c",
                 "select status, activated_at is null from macro_core.strategy")
    assert rows.stdout.strip() == "superseded|t"


@needs_postgres
async def test_a_run_that_says_no_change_is_done_and_one_that_says_nothing_failed(
    store, tmp_path, monkeypatch
):
    as_operator("select macro_mart.request_refresh('anything?')")
    monkeypatch.setenv("MACRO_CLAUDE", fake_claude(tmp_path, "echo 'NO_CHANGE: nothing material.'\n"))
    assert (await runner.tick(store, now=datetime.now(UTC)))["result"] == "no change"

    as_operator("select macro_mart.request_refresh('anything now?')")
    monkeypatch.setenv("MACRO_CLAUDE", fake_claude(tmp_path, "echo 'thinking'\n"))
    assert (await runner.tick(store, now=datetime.now(UTC)))["result"] == "failed"

    as_operator("select macro_mart.request_refresh('and now?')")
    monkeypatch.setenv("MACRO_CLAUDE", fake_claude(tmp_path, "echo boom; exit 3\n"))
    report = await runner.tick(store, now=datetime.now(UTC))
    assert report["result"] == "failed"
    error = as_operator("select error from macro_mart.run_request_state order by id desc limit 1")
    assert error.stdout.strip().splitlines()[-1].startswith("claude exited 3")


@needs_postgres
async def test_a_failed_author_leaves_nothing_proposed_for_its_retry(store, tmp_path, monkeypatch):
    """A half-written week is not reviewable, and its reads may have seen the prior.

    Whatever the failed run left `proposed` is superseded, so the retry writes
    a fresh read instead of being refused for one that was already revealed.
    """
    _psql("-d", "macro", "-c",
          "insert into macro_core.run_request (kind, origin) values ('author', 'schedule')")
    claude = fake_claude(
        tmp_path,
        f"psql -h 127.0.0.1 -p {PORT} -U postgres -d macro -q -c \""
        "insert into macro_core.strategy (week, asset, stance, posture, status) values "
        "('2026-W40', 'crypto', 'Add', 'constructive', 'proposed')\"\n"
        "echo 'halfway'; exit 3\n",
    )
    monkeypatch.setenv("MACRO_CLAUDE", claude)
    report = await runner.tick(store, now=datetime.now(UTC))
    assert report["claimed"]["kind"] == "author"
    assert report["result"] == "failed"
    assert report["discarded"] == 1
    rows = _psql("-d", "macro", "-tA", "-c",
                 "select status from macro_core.strategy where week = '2026-W40'")
    assert rows.stdout.strip() == "superseded"


@needs_postgres
async def test_a_stale_claim_clears_what_its_run_proposed(store):
    """A runner that died mid-run is a failed run like any other.

    Its proposed rows are superseded, so the retry's `author classify` is not
    refused for reads the dead run had already been shown last week's call for.
    """
    _psql("-d", "macro", "-c",
          "insert into macro_core.run_request (kind, origin, status, claimed_at) values "
          "('author', 'schedule', 'running', now() - interval '3 hours'); "
          "insert into macro_core.strategy (week, asset, stance, posture, status, authored_at) "
          "values ('2026-W40', 'crypto', 'Add', 'constructive', 'proposed', "
          "now() - interval '2 hours')")
    assert await runner.fail_stale_claims(store, datetime.now(UTC)) == 1
    rows = _psql("-d", "macro", "-tA", "-c",
                 "select status from macro_core.strategy where week = '2026-W40'")
    assert rows.stdout.strip() == "superseded"
    state = _psql("-d", "macro", "-tA", "-c", "select status from macro_core.run_request")
    assert state.stdout.strip() == "failed"


@needs_postgres
async def test_the_schedule_enqueues_the_narrative_once_the_full_run_landed(store, monkeypatch):
    monkeypatch.setenv("MACRO_CLAUDE", "echo 'SUMMARY: wrote 16 series.'")
    # 09:45 Singapore today, with no full run: nothing is scheduled.
    today_0945 = datetime.now(UTC).replace(hour=1, minute=45, second=0, microsecond=0)
    if runner.SINGAPORE and today_0945.astimezone(runner.SINGAPORE).weekday() >= 5:
        pytest.skip("the schedule is weekday-only and today is a weekend in Singapore")
    report = await runner.tick(store, now=today_0945)
    assert "scheduled" not in report
    _psql("-d", "macro", "-c",
          "insert into macro_quality.ingest_run (source, run_kind, started_at, finished_at, "
          f"status) values ('yahoo', 'full', '{today_0945.replace(minute=30).isoformat()}', "
          f"'{today_0945.replace(minute=33).isoformat()}', 'ok')")
    report = await runner.tick(store, now=today_0945)
    assert report["scheduled"][0]["kind"] == "narrative"
    assert report["result"] == "done"
    # The narrative's rows reach the page only through the worker's rescore.
    assert report["rescore_exit"] == 0
    report = await runner.tick(store, now=today_0945)
    assert "scheduled" not in report and "claimed" not in report


def test_every_session_is_pinned_to_one_model_and_named_by_the_runner(monkeypatch):
    monkeypatch.delenv("MACRO_CLAUDE_MODEL", raising=False)
    argv = runner.claude_command("go", session_id="0f0f0f0f-0000-4000-8000-000000000000")
    assert argv[argv.index("--model") + 1] == "claude-opus-5-5"
    assert argv[argv.index("--session-id") + 1] == "0f0f0f0f-0000-4000-8000-000000000000"
    monkeypatch.setenv("MACRO_CLAUDE_MODEL", "claude-fable-5-1")
    argv = runner.claude_command("go", session_id="0f0f0f0f-0000-4000-8000-000000000000")
    assert argv[argv.index("--model") + 1] == "claude-fable-5-1"


def _git_repo(root: Path) -> Path:
    for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", *args], cwd=root, check=True)
    (root / ".gitignore").write_text("ignored/\n")
    (root / "tracked.py").write_text("a = 1\n")
    (root / "dirty.py").write_text("b = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True)
    (root / "dirty.py").write_text("b = 2\n")  # already dirty before any run
    return root


def test_a_run_that_touches_nothing_changes_nothing(tmp_path):
    repo = _git_repo(tmp_path)
    assert runner.changed_paths(runner.repo_state(repo), runner.repo_state(repo)) == []


def test_every_kind_of_edit_is_seen_and_ignored_paths_are_not(tmp_path):
    """Including a second edit to a file that was already dirty: its porcelain line does not move."""
    repo = _git_repo(tmp_path)
    before = runner.repo_state(repo)
    (repo / "tracked.py").write_text("a = 2\n")
    (repo / "dirty.py").write_text("b = 3\n")
    (repo / "new.py").write_text("c = 1\n")
    (repo / "ignored").mkdir()
    (repo / "ignored" / "scratch.json").write_text("{}")
    after = runner.repo_state(repo)
    assert runner.changed_paths(before, after) == ["dirty.py", "new.py", "tracked.py"]
    (repo / "new.py").unlink()
    assert "new.py" not in runner.changed_paths(before, runner.repo_state(repo))


@needs_postgres
async def test_a_run_that_edits_the_repo_is_recorded_and_opens_a_finding(
    store, tmp_path, monkeypatch
):
    (tmp_path / "repo").mkdir()
    repo = _git_repo(tmp_path / "repo")
    monkeypatch.setattr(runner, "REPO_ROOT", repo)
    _psql("-d", "macro", "-c", "delete from macro_quality.finding where check_key = 'run_changed_repo'")
    as_operator("select macro_mart.request_refresh('anything?')")
    monkeypatch.setenv(
        "MACRO_CLAUDE",
        fake_claude(tmp_path, "echo 'x = 1' > prompts.py\necho 'NO_CHANGE: nothing material.'\n"),
    )
    report = await runner.tick(store, now=datetime.now(UTC))
    assert report["result"] == "no change", "a record, not a failure"
    assert report["repo_changed"] == ["prompts.py"]
    row = _psql("-d", "macro", "-tA", "-c",
                "select claude_session_id is not null, repo_changed, repo_before like '%dirty.py%' "
                "from macro_core.run_request order by id desc limit 1")
    assert row.stdout.strip() == "t|{prompts.py}|t"
    finding = _psql("-d", "macro", "-tA", "-c",
                    "select sentence from macro_quality.finding where check_key = 'run_changed_repo' "
                    "and closed_at is null")
    assert "changed 1 repository file: prompts.py" in finding.stdout
    assert "claude --resume" in finding.stdout


def test_the_rescore_after_an_accept_is_the_worker_and_can_be_stubbed():
    assert os.environ["MACRO_UV"] == "true"
    assert runner.rescore_command() == ["true", "run", "--project", "worker", "macro-worker",
                                        "--rescore"]
