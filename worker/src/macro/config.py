"""Configuration, read from `os.environ` and nothing else.

`python-dotenv` loads a `.env` at process start inside `main.run`; nothing in
this package opens a file to find one.

A process whose whole job is to write refuses to start when it cannot write.
That refusal is a sentence naming the commonest cause, not a stack trace.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

DEFAULT_VAULT = Path("/Users/rayclaw/Obsidian/rayobs")

#: The three OpenClaw feeds, and the environment variable that says where each
#: comes from: `vault` (this worker parses the Obsidian note, the default) or
#: `desk` (`macro-agent desk` writes the tables, and the note is not read at all,
#: so it can never retire what the desk wrote). docs/DECISIONS.md 2026-09-24.
FEEDS: dict[str, str] = {
    "catalysts": "MACRO_FEED_SOURCE_CATALYSTS",
    "tail_risks": "MACRO_FEED_SOURCE_TAIL_RISKS",
    "holdings": "MACRO_FEED_SOURCE_HOLDINGS",
}
FeedSource = Literal["vault", "desk"]


class Misconfigured(Exception):
    """The environment cannot support a run. The message is the whole diagnosis."""


@dataclass(frozen=True, slots=True)
class Settings:
    """Everything the worker reads from the environment.

    `database_url` is the Supabase **Session pooler** URI on port 5432.

    `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` are read and carried for the
    weekly authoring agent, which does use the Data API. This worker cannot:
    `macro_raw` and `macro_core` are deliberately not exposed, so every
    PostgREST write to them fails with PGRST106 whatever key is used, and
    PostgREST has no transaction spanning requests, which a `macro_mart` flush
    needs.

    `fred_api_key` is unused. `fredgraph.csv?id=<SERIES>` is keyless for every
    series this system reads. It is still accepted so that an existing `.env`
    does not have to be edited, and so that setting it cannot be mistaken for a
    way to fix a FRED fetch that is actually a User-Agent problem.
    """

    database_url: str | None
    database_ssl: str
    supabase_url: str | None
    supabase_service_role_key: str | None
    fred_api_key: str | None
    vault_path: Path
    series_yaml: Path
    predicates_md: Path
    log_level: str
    #: `feed -> vault | desk`, for the three keys of `FEEDS`.
    feed_sources: dict[str, FeedSource] = field(default_factory=dict)

    def reads_vault(self, feed: str) -> bool:
        """Whether this worker still parses `feed`'s Obsidian note. True unless set to desk."""
        return self.feed_sources.get(feed, "vault") == "vault"

    @property
    def catalyst_file(self) -> Path:
        return self.vault_path / "3 - Work" / "35 - Catalyst" / "Catalyst Monitor.md"

    @property
    def watchlist_file(self) -> Path:
        return self.vault_path / "3 - Work" / "36 - Market Watchlist" / "Market Watchlist.md"

    @property
    def blackswan_file(self) -> Path:
        return self.vault_path / "3 - Work" / "36 - Market Watchlist" / "Blackswan Events.md"


def _repo_root() -> Path:
    """The repository root, derived from this file rather than from the cwd."""
    return Path(__file__).resolve().parents[3]


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

    vault = Path((env.get("VAULT_PATH") or "").strip() or DEFAULT_VAULT)
    series_yaml = Path((env.get("SERIES_YAML") or "").strip() or (root / "config" / "series.yaml"))
    predicates_md = Path(
        (env.get("PREDICATES_MD") or "").strip() or (root / "config" / "predicates.md")
    )

    feed_sources: dict[str, FeedSource] = {}
    for feed, variable in FEEDS.items():
        raw = (env.get(variable) or "vault").strip().lower()
        if raw not in ("vault", "desk"):
            raise Misconfigured(
                f"{variable} is {raw!r}; it is vault (the worker parses the Obsidian note) or "
                "desk (macro-agent desk writes the feed and the note is not read)."
            )
        feed_sources[feed] = "desk" if raw == "desk" else "vault"

    return Settings(
        database_url=database_url,
        database_ssl=(env.get("DATABASE_SSL") or "require").strip(),
        supabase_url=(env.get("SUPABASE_URL") or "").strip() or None,
        supabase_service_role_key=(env.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip() or None,
        fred_api_key=(env.get("FRED_API_KEY") or "").strip() or None,
        vault_path=vault,
        series_yaml=series_yaml,
        predicates_md=predicates_md,
        log_level=(env.get("LOG_LEVEL") or "INFO").strip().upper(),
        feed_sources=feed_sources,
    )


def require_database(settings: Settings) -> str:
    """The write URL, or a sentence saying exactly why there is none.

    `python-dotenv` lets a later key in the file override an earlier one, so a
    duplicate blank `DATABASE_URL=` line silently wins. That has happened, and
    it is why an empty value is fatal rather than absent-by-another-name.
    """
    if not settings.database_url:
        raise Misconfigured(
            "DATABASE_URL is absent or blank, so this run has nowhere to write. It must be "
            "the Supabase Session pooler URI on port 5432. Check for a second, blank "
            "DATABASE_URL= line in .env: python-dotenv lets the later one win."
        )
    return settings.database_url
