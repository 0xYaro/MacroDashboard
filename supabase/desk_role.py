#!/usr/bin/env python3
"""Give the `macro_desk` role a login, and write its URL into the repository's `.env`.

    python3 supabase/desk_role.py             # first time
    python3 supabase/desk_role.py --rotate    # replace an existing password
    python3 supabase/desk_role.py --dry-run   # say what would happen, change nothing

`20260924000200_desk_role.sql` creates `macro_desk` without a login, because a
migration is committed and a password must never be. This script is the other
half, run once on the operator's machine:

1. It generates a random password (256 bits, URL-safe) in memory.
2. It turns that into a SCRAM-SHA-256 verifier here and sends only the verifier
   (`ALTER ROLE macro_desk LOGIN PASSWORD 'SCRAM-SHA-256$...'`), so the
   plaintext never reaches the server, its statement log or `pg_stat_statements`.
3. It writes `MACRO_DESK_DATABASE_URL` into `.env`: `DATABASE_URL`'s pooler host,
   port, database and options, with the user `macro_desk.<project-ref>`, which
   is how the Supabase session pooler (Supavisor) names a role of one project.
4. It connects with the new URL and checks the session is `macro_desk`.

It never prints the password or either URL, and it passes every credential to
`psql` through the environment, never on the command line where `ps` would
show it. `DATABASE_URL` is read from the environment, or from `.env` when the
environment has none. Standard library only, like `migrate.py`; needs `psql`.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit

REPO = Path(__file__).resolve().parents[1]
ENV_FILE = REPO / ".env"
ROLE = "macro_desk"
KEY = "MACRO_DESK_DATABASE_URL"
SCRAM_ITERATIONS = 4096


class Refused(Exception):
    """The script cannot proceed. The message is the whole diagnosis, with no secret in it."""


def read_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line)
        if not match:
            continue
        value = match.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        # python-dotenv lets a later line win, and so does this.
        out[match.group(1)] = value
    return out


def database_url() -> str:
    url = (os.environ.get("DATABASE_URL") or "").strip() or read_env_file(ENV_FILE).get(
        "DATABASE_URL", ""
    ).strip()
    if not url:
        raise Refused(
            "DATABASE_URL is not in the environment or in .env. It is the Supabase session "
            "pooler URI the worker and migrate.py use."
        )
    return url


def desk_url(admin_url: str, password: str) -> str:
    """The admin URL with the user and password swapped for macro_desk's.

    The session pooler's user is `<role>.<project-ref>`; a direct connection's is
    the bare role. Host, port, database and query string are kept as they are.
    """
    parts = urlsplit(admin_url)
    if parts.scheme not in ("postgres", "postgresql") or not parts.hostname:
        raise Refused("DATABASE_URL is not a postgres:// URL with a host.")
    user = unquote(parts.username or "")
    _, dot, ref = user.partition(".")
    login = f"{ROLE}.{ref}" if dot and ref else ROLE
    host = parts.hostname if ":" not in parts.hostname else f"[{parts.hostname}]"
    netloc = f"{quote(login, safe='')}:{quote(password, safe='')}@{host}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def libpq_env(url: str) -> dict[str, str]:
    """The URL as PG* variables, so no credential appears in psql's argv."""
    parts = urlsplit(url)
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("PG") and key != "DATABASE_URL"
    }
    env["PGHOST"] = parts.hostname or ""
    env["PGPORT"] = str(parts.port or 5432)
    env["PGUSER"] = unquote(parts.username or "")
    env["PGPASSWORD"] = unquote(parts.password or "")
    env["PGDATABASE"] = (parts.path or "/postgres").lstrip("/") or "postgres"
    query = dict(
        pair.split("=", 1) for pair in (parts.query or "").split("&") if "=" in pair
    )
    env["PGSSLMODE"] = query.get("sslmode", "require")
    env["PGCONNECT_TIMEOUT"] = "15"
    return env


def psql(url: str, sql: str) -> subprocess.CompletedProcess[str]:
    """Run SQL from stdin, never from argv."""
    return subprocess.run(
        ["psql", "-X", "-v", "ON_ERROR_STOP=1", "-tA"],
        input=sql,
        capture_output=True,
        text=True,
        env=libpq_env(url),
    )


def scram_verifier(password: str, *, salt: bytes | None = None) -> str:
    """A SCRAM-SHA-256 verifier in the form PostgreSQL stores in pg_authid (RFC 7677)."""
    salt = salt or secrets.token_bytes(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, SCRAM_ITERATIONS)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()

    def b64(raw: bytes) -> str:
        return base64.b64encode(raw).decode("ascii")

    return (
        f"SCRAM-SHA-256${SCRAM_ITERATIONS}:{b64(salt)}${b64(stored_key)}:{b64(server_key)}"
    )


def write_env(path: Path, value: str) -> str:
    """Set KEY=value in .env, replacing an existing line, keeping the file's mode.

    The file holds DATABASE_URL's password, so the new content is written to a
    temporary file that `mkstemp` creates owner-only (0600) in the same
    directory, given the old file's mode, and renamed over it. It is never
    readable by another user, not even before a chmod, and a write that fails
    part way leaves no copy behind.
    """
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    pattern = re.compile(rf"^\s*(?:export\s+)?{KEY}\s*=")
    kept = [line for line in lines if not pattern.match(line)]
    replaced = len(kept) != len(lines)
    kept.append(f"{KEY}={value}")
    mode = path.stat().st_mode & 0o777 if path.is_file() else 0o600
    fd, temp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".desk-tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("\n".join(kept) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), mode)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return "replaced" if replaced else "appended"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--rotate", action="store_true",
                        help=f"replace the password even though .env already has {KEY}")
    parser.add_argument("--dry-run", action="store_true", help="check and describe; change nothing")
    args = parser.parse_args(argv)

    if not shutil.which("psql"):
        print("psql is not on PATH.", file=sys.stderr)
        return 2
    try:
        admin = database_url()
        existing = read_env_file(ENV_FILE).get(KEY)
        if existing and not args.rotate and not args.dry_run:
            raise Refused(
                f"{ENV_FILE.name} already has {KEY}. Re-run with --rotate to replace the "
                "password; the old one stops working the moment the new one is set."
            )
        probe = psql(admin, f"select count(*) from pg_roles where rolname = '{ROLE}';")
        if probe.returncode != 0:
            raise Refused(
                "could not connect with DATABASE_URL: "
                + (probe.stderr.strip().splitlines() or ["no error text"])[-1]
            )
        if probe.stdout.strip() != "1":
            raise Refused(
                f"the {ROLE} role does not exist yet. Apply the migrations first: "
                "python3 supabase/migrate.py"
            )
        login = urlsplit(desk_url(admin, "x")).username
        host = urlsplit(admin).hostname
        if args.dry_run:
            print(f"would set LOGIN and a new password on {ROLE}, and write {KEY} to "
                  f"{ENV_FILE} as user {unquote(login or '')} on {host}. Nothing was changed.")
            return 0

        password = secrets.token_urlsafe(32)
        done = psql(
            admin,
            f"alter role {ROLE} login password '{scram_verifier(password)}';\n",
        )
        if done.returncode != 0:
            raise Refused(
                f"ALTER ROLE {ROLE} was refused: "
                + (done.stderr.strip().splitlines() or ["no error text"])[-1]
            )
        url = desk_url(admin, password)
        how = write_env(ENV_FILE, url)
        check = psql(url, "select current_user;")
        if check.returncode != 0:
            raise Refused(
                f"the password is set and {KEY} was {how} in {ENV_FILE.name}, but connecting "
                "with it failed: " + (check.stderr.strip().splitlines() or ["no error text"])[-1]
            )
        if check.stdout.strip() != ROLE:
            raise Refused(f"connected, but as {check.stdout.strip()!r} rather than {ROLE}")
    except Refused as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"{ROLE} can log in. {KEY} was {how} in {ENV_FILE.name} and verified: the session "
          f"pooler at {host} answered as {ROLE}. The password was not printed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
