"""Shared fixtures.

`tests/fixtures/w40_proposal.json` is a realistic week, not a minimal one. It is
W39's authored judgment carried forward: three scenarios, four setups, two
strategies, forty-one target rows and every trigger clause the journal actually
wrote, plus the change note on each phase. Its reader-facing prose is rewritten
in `config/voice.md`'s voice, because it is the example the Monday run copies;
every number and every expression is the journal's. The phase reads themselves are in
`tests/fixtures/w40_phase.json`, because on a real Monday they are written first,
by `author classify`, before last week's call is shown. It is the fixture because the question worth answering
is what fraction of a *real* week can be typed, and a fixture built to pass
would answer a different question.

Nothing here connects to the live Supabase project. The database tests build a
throwaway Postgres cluster in a temporary directory, apply `supabase/migrations`
to it, use it once and delete it.
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures"
CONFIG = ROOT / "config" / "series.yaml"
MIGRATIONS = ROOT / "supabase" / "migrations"
WORKER_SRC = ROOT / "worker" / "src"

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from macro_agent.config import Settings  # noqa: E402


@pytest.fixture(scope="session")
def settings() -> Settings:
    """The repository's own config, never a copy of it.

    A test registry would be a second place for a unit to be wrong, which is the
    failure the one-canonical-unit rule exists to prevent.
    """
    return Settings(
        database_url=None,
        database_ssl="disable",
        supabase_url=None,
        supabase_service_role_key=None,
        vault_path=Path("/nonexistent-vault"),
        series_yaml=CONFIG,
        predicates_md=ROOT / "config" / "predicates.md",
        worker_src=WORKER_SRC,
        log_level="WARNING",
    )


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def w40() -> dict:
    return fixture("w40_proposal.json")


def dec(value: str | int) -> Decimal:
    return Decimal(str(value))


@pytest.fixture
def reads() -> dict:
    """The W40 fresh reads, as `author classify` is given them."""
    return fixture("w40_phase.json")


#: What `author classify` would have filled in from the active W39 calls.
W39_PHASES = {"equities": 3, "crypto": 1}


def full(proposal_payload: dict, reads_payload: dict, *, prior: dict | None = None):
    """The whole W40 week as `author propose` sees it: the proposal with its reads attached.

    The reads were written before the reveal and carry last week's phase as the
    command filled it in; the proposal adds only the change notes.
    """
    from dataclasses import replace

    from macro_agent.validate import attach_reads, phase_reads, proposal

    before = W39_PHASES if prior is None else prior
    decoded = tuple(
        replace(one, prior_phase=before.get(one.asset), prior_week="2026-W39")
        for one in phase_reads(reads_payload)
    )
    return attach_reads(proposal(proposal_payload), decoded)
