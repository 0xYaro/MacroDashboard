"""Configuration, read from `os.environ` and nothing else.

Deliberately the same shape as `worker/src/macro/config.py`, because this
package writes the same database and a second convention for reading the same
four variables is a second place for one of them to be wrong.

One variable is this package's own: `WORKER_SRC`, the path to the worker's
`src` directory. `worker_bridge` puts it on `sys.path` so the Brier arithmetic
and the condition evaluator are called rather than reimplemented.

`MACRO_DESK_DATABASE_URL` is the `macro_desk` login that `macro-agent desk` uses,
written into `.env` by `supabase/desk_role.py`. It can write the three feed
tables and read what the feeds need, and nothing else. The model-facing desk
commands refuse to run without it; only an operator's explicit
`desk --use-database-url` falls back to `DATABASE_URL`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_VAULT = Path("/Users/rayclaw/Obsidian/rayobs")


class Misconfigured(Exception):
    """The environment cannot support a run. The message is the whole diagnosis."""


@dataclass(frozen=True, slots=True)
class Settings:
    """Everything this package reads from the environment.

    `database_url` is the Supabase **Session pooler** URI on port 5432, the same
    connection the worker uses and for the same reason: `macro_raw` and
    `macro_core` are not exposed through the Data API, so PostgREST returns
    PGRST106 for every table this package writes, and a scenario set has to be
    written whole in one transaction, which PostgREST cannot span.

    `supabase_url` and `supabase_service_role_key` are read and carried but not
    used to write `macro_core`. See `docs/CONTRACT.md` and this package's
    README: the contract says both that the authoring agent writes `macro_core`
    with the service-role key *not* through the Data API, and that it *does* use
    the Data API. Only the first is reachable, so that is what is implemented.
    """

    database_url: str | None
    database_ssl: str
    supabase_url: str | None
    supabase_service_role_key: str | None
    vault_path: Path
    series_yaml: Path
    predicates_md: Path
    worker_src: Path
    log_level: str
    #: The least-privilege `macro_desk` login, for `macro-agent desk` only.
    desk_database_url: str | None = None
    #: How a held or watched name is priced: `config/symbols.yaml`.
    symbols_yaml: Path = field(default_factory=lambda: repo_root() / "config" / "symbols.yaml")

    @property
    def frameworks_dir(self) -> Path:
        return self.vault_path / "3 - Work" / "Market Principles"

    @property
    def macro_dir(self) -> Path:
        return self.vault_path / "3 - Work" / "31 - Macro"


def repo_root() -> Path:
    """The repository root, derived from this file rather than from the cwd."""
    return Path(__file__).resolve().parents[3]


_repo_root = repo_root


def load(environ: dict[str, str] | None = None) -> Settings:
    """Build `Settings` from an environment mapping, defaulting to `os.environ`."""
    env = dict(os.environ if environ is None else environ)
    root = _repo_root()

    database_url = (env.get("DATABASE_URL") or "").strip() or None
    if database_url and ":6543" in database_url:
        raise Misconfigured(
            "DATABASE_URL points at the transaction pooler on port 6543, which has no "
            "prepared statements and which asyncpg needs. Use the Session pooler URI on "
            "port 5432, shown in Supabase under Connect, Session pooler."
        )

    desk_url = (env.get("MACRO_DESK_DATABASE_URL") or "").strip() or None
    if desk_url and ":6543" in desk_url:
        raise Misconfigured(
            "MACRO_DESK_DATABASE_URL points at the transaction pooler on port 6543. It must be "
            "the Session pooler on port 5432, like DATABASE_URL; supabase/desk_role.py writes it."
        )

    return Settings(
        database_url=database_url,
        database_ssl=(env.get("DATABASE_SSL") or "require").strip(),
        supabase_url=(env.get("SUPABASE_URL") or "").strip() or None,
        supabase_service_role_key=(env.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip() or None,
        vault_path=Path((env.get("VAULT_PATH") or "").strip() or DEFAULT_VAULT),
        series_yaml=Path(
            (env.get("SERIES_YAML") or "").strip() or (root / "config" / "series.yaml")
        ),
        predicates_md=Path(
            (env.get("PREDICATES_MD") or "").strip() or (root / "config" / "predicates.md")
        ),
        worker_src=Path((env.get("WORKER_SRC") or "").strip() or (root / "worker" / "src")),
        log_level=(env.get("LOG_LEVEL") or "INFO").strip().upper(),
        desk_database_url=desk_url,
        symbols_yaml=Path(
            (env.get("SYMBOLS_YAML") or "").strip() or (root / "config" / "symbols.yaml")
        ),
    )


def require_database(settings: Settings) -> str:
    """The write URL, or a sentence saying exactly why there is none."""
    if not settings.database_url:
        raise Misconfigured(
            "DATABASE_URL is absent or blank, so this run has nowhere to write. It must be "
            "the Supabase Session pooler URI on port 5432. Check for a second, blank "
            "DATABASE_URL= line in .env: python-dotenv lets the later one win."
        )
    return settings.database_url


def require_desk_database(settings: Settings, *, allow_fallback: bool = False) -> str:
    """The desk's URL: the least-privilege `macro_desk` login, or a refusal.

    The desk commands are called by a model's tools, so they never quietly run
    as `DATABASE_URL`, which can write far more than the three feeds. Only an
    operator's explicit `allow_fallback` (`desk --use-database-url`) does.
    """
    if settings.desk_database_url:
        return settings.desk_database_url
    if allow_fallback:
        return require_database(settings)
    raise Misconfigured(
        "MACRO_DESK_DATABASE_URL is not set, so the desk will not connect. Its commands run "
        "as the least-privilege macro_desk login and never as DATABASE_URL, which can write "
        "far more than the three feeds. The operator creates the login once with the desk "
        "role script (docs/DESK.md, Setup)."
    )
