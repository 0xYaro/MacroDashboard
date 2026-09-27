"""The only module in this package that talks to the database.

`asyncpg` over a direct Postgres connection, not PostgREST.

`docs/CONTRACT.md`'s Environment section lists `SUPABASE_URL` and
`SUPABASE_SERVICE_ROLE_KEY` for the worker. Those cannot do this job, for two
reasons and the second is the one that decides it:

- `macro_raw` and `macro_core` are deliberately not exposed through the Data
  API, so every PostgREST write to them fails with PGRST106 whatever key is
  used.
- A `macro_mart` flush has to be one transaction, or the web app reads a
  snapshot from one run beside a condition state from the next and renders a
  reading that never existed. PostgREST has no transaction spanning requests.
  Postgres does, and `flush_mart` takes exactly one.

Both variables are still read by `config` and still documented, because the
weekly authoring agent uses them.

## Every write is an idempotent upsert on a natural key

Replaying a run is always safe. `on conflict ... do update` everywhere, and
`observation` is keyed on `(series_id, asof)` so the two runs of one day upsert
the same row and the later one wins.

## Activation is two statements in one transaction

A partial unique index enforces at most one `active` row per subject and cannot
be deferred, so activation always supersedes the outgoing row first and stands
the incoming one up second. Doing it the other way round leaves two active rows
for the length of one statement and is rejected outright.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Any

import asyncpg

from .calibration import GradedWeek
from .drift import Drift, TailRiskSeen
from .model import (
    AssertedCondition,
    Catalyst,
    CatalystOccurrence,
    CatalystWindowRow,
    ConditionState,
    Finding,
    FindingScope,
    Gap,
    Holding,
    Observation,
    PhaseCall,
    PhaseCondition,
    PhaseScore,
    RawBatch,
    Scenario,
    ScenarioSet,
    ScenarioState,
    ScenarioTarget,
    ScenarioTargetState,
    Setup,
    SetupGrade,
    SetupState,
    Status,
    TailRisk,
    TriggerClause,
    WatchlistChange,
    WeekGrade,
)
from .observations import Reading
from .series import Registry

LOG = logging.getLogger(__name__)


class WriteRefused(Exception):
    """The database declined a write. Expected and handled, not a programming error."""

    @property
    def sentence(self) -> str:
        text = str(self)
        if "does not exist" in text or "UndefinedTable" in text:
            return (
                f"{text}. A migration in supabase/migrations is probably not applied; the run "
                "resumes on the next pass with no restart."
            )
        if "gaierror" in text or "Name or service not known" in text:
            return (
                f"{text}. `db.<ref>.supabase.co` is IPv6 only and does not resolve from an IPv4 "
                "network: DATABASE_URL must be the Session pooler URI on port 5432."
            )
        return text


class Store:
    """A pool of two to four connections, and every statement this worker sends."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        #: Findings raised while decoding rows, drained by `main` after a read.
        self.refused_setups: list[Finding] = []

    @classmethod
    async def connect(cls, dsn: str, *, ssl: str = "require") -> Store:
        try:
            # `disable` exists for a local TCP cluster; every real deployment is
            # TLS, which is what the Session pooler requires.
            pool = await asyncpg.create_pool(
                dsn, min_size=2, max_size=4, ssl=False if ssl == "disable" else "require"
            )
        except (OSError, asyncpg.PostgresError) as exc:
            raise WriteRefused(f"{type(exc).__name__}: {exc}") from exc
        if pool is None:  # pragma: no cover - asyncpg only returns None on a bad dsn
            raise WriteRefused("asyncpg returned no pool for the given DATABASE_URL")
        return cls(pool)

    async def aclose(self) -> None:
        await self._pool.close()

    # ------------------------------------------------------------------
    # Every statement goes through one of these four, so that an asyncpg fault
    # becomes a `WriteRefused` carrying a sentence rather than a driver
    # exception reaching a run loop that has no idea what to say about it.
    # ------------------------------------------------------------------

    def _acquire(self) -> Any:
        return self._pool.acquire()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[Any]:
        """One connection inside one transaction, faults wrapped as `WriteRefused`."""
        try:
            async with self._pool.acquire() as connection, connection.transaction():
                yield connection
        except (OSError, asyncpg.PostgresError) as exc:
            raise WriteRefused(f"{type(exc).__name__}: {exc}") from exc

    async def _execute(self, sql: str, *args: Any) -> str:
        try:
            async with self._pool.acquire() as connection:
                return str(await connection.execute(sql, *args))
        except (OSError, asyncpg.PostgresError) as exc:
            raise WriteRefused(f"{type(exc).__name__}: {exc}") from exc

    async def _executemany(self, sql: str, rows: Sequence[tuple[Any, ...]]) -> None:
        if not rows:
            return
        try:
            async with self._pool.acquire() as connection, connection.transaction():
                await connection.executemany(sql, rows)
        except (OSError, asyncpg.PostgresError) as exc:
            raise WriteRefused(f"{type(exc).__name__}: {exc}") from exc

    async def _fetch(self, sql: str, *args: Any) -> list[asyncpg.Record]:
        try:
            async with self._pool.acquire() as connection:
                return list(await connection.fetch(sql, *args))
        except (OSError, asyncpg.PostgresError) as exc:
            raise WriteRefused(f"{type(exc).__name__}: {exc}") from exc

    async def _fetchrow(self, sql: str, *args: Any) -> asyncpg.Record:
        try:
            async with self._pool.acquire() as connection:
                record = await connection.fetchrow(sql, *args)
        except (OSError, asyncpg.PostgresError) as exc:
            raise WriteRefused(f"{type(exc).__name__}: {exc}") from exc
        if record is None:
            raise WriteRefused(
                f"a statement that must return a row returned none: {sql.strip()[:80]}"
            )
        return record

    # ------------------------------------------------------------------
    # Reference data
    # ------------------------------------------------------------------

    def drain_refusals(self) -> list[Finding]:
        """Take the findings raised while decoding rows, and clear them."""
        out = list(self.refused_setups)
        self.refused_setups.clear()
        return out

    def _setups(self, rows: Sequence[Any]) -> list[Setup]:
        """Decode setup rows, recording every one that named no series.

        The refusals are kept on the store rather than raised, because one
        unscoreable setup must not cost the other three their state. `main`
        drains them into the run's findings after each read.
        """
        out: list[Setup] = []
        for record in rows:
            found = _setup_from(record)
            if found.series_key is None:
                self.refused_setups.append(
                    setup_refused_finding(str(record["id"]), record["name"], record["week"])
                )
            out.append(found)
        return out

    async def sync_series(self, registry: Registry) -> dict[str, str]:
        """Apply `config/series.yaml` to `macro_core.series` and return key to id.

        The registry is asserted reference data, so this is an upsert on `key`
        and never a delete: a series removed from the YAML keeps its row and its
        observations rather than taking them with it.
        """
        rows = [
            (
                s.key,
                s.label,
                s.table_group,
                s.ordinal,
                s.value_kind,
                s.unit,
                s.decimals,
                s.cadence,
                s.release_rule.raw,
                s.replayable,
                s.source_name,
                s.fetch_path,
                s.tier_floor,
            )
            for s in registry.ordered()
        ]
        async with self._acquire() as connection, connection.transaction():
            # `(table_group, ordinal)` is unique and checked row by row, so a
            # series moving into a slot another one is leaving would be refused
            # halfway through the upsert. Every row that is about to move is
            # parked on a negative ordinal first, which no declared slot uses,
            # and so is every row whose key the registry no longer names: it
            # keeps its observations, and must not keep a slot another series
            # is now declared in.
            #
            # Each parked row goes below the lowest ordinal its group already
            # holds, one apart, so parking never collides with a row parked on
            # an earlier run. A retired row already parked is left where it is;
            # a registry row is moved back onto its slot by the upsert below.
            await connection.execute(
                """
                with want as (
                  select * from unnest($1::text[], $2::text[], $3::int[])
                    as w(key, table_group, ordinal)
                ),
                park as (
                  select s.id, s.table_group,
                         row_number() over (
                           partition by s.table_group order by s.ordinal, s.key
                         ) as n
                  from macro_core.series s
                  left join want on want.key = s.key
                  where (want.key is not null
                         and (s.table_group, s.ordinal)
                             is distinct from (want.table_group, want.ordinal))
                     or (want.key is null and s.ordinal >= 0)
                ),
                lowest as (
                  select table_group, least(min(ordinal), 0) as floor
                  from macro_core.series
                  group by table_group
                )
                update macro_core.series s
                set ordinal = lowest.floor - park.n
                from park join lowest on lowest.table_group = park.table_group
                where s.id = park.id
                """,
                [row[0] for row in rows],
                [row[2] for row in rows],
                [row[3] for row in rows],
            )
            await connection.executemany(
                """
                insert into macro_core.series
                  (key, label, table_group, ordinal, value_kind, unit, decimals,
                   cadence, release_rule, replayable, source_name, fetch_path, tier_floor)
                values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
                on conflict (key) do update set
                  label = excluded.label,
                  table_group = excluded.table_group,
                  ordinal = excluded.ordinal,
                  value_kind = excluded.value_kind,
                  unit = excluded.unit,
                  decimals = excluded.decimals,
                  cadence = excluded.cadence,
                  release_rule = excluded.release_rule,
                  replayable = excluded.replayable,
                  source_name = excluded.source_name,
                  fetch_path = excluded.fetch_path,
                  tier_floor = excluded.tier_floor
                where macro_core.series.* is distinct from excluded.*
                """,
                rows,
            )
            found = await connection.fetch("select key, id from macro_core.series")
        return {record["key"]: str(record["id"]) for record in found}

    # ------------------------------------------------------------------
    # Observations
    # ------------------------------------------------------------------

    async def cursors(self) -> dict[str, date]:
        """The newest `asof` stored per series key.

        The cursor is the data. `supabase/migrations` declares no cursor table,
        and deriving it from `macro_core.observation` is better than adding one:
        a side table can disagree with what was actually stored, and this
        cannot.
        """
        found = await self._fetch(
            """
            select s.key as key, max(o.asof) as asof
            from macro_core.observation o
            join macro_core.series s on s.id = o.series_id
            group by s.key
            """
        )
        return {record["key"]: record["asof"] for record in found if record["asof"] is not None}

    async def history(self, *, since: date) -> list[Reading]:
        """Every observation at or after `since`, for the evaluator to stand on."""
        found = await self._fetch(
            """
            select s.key as key, o.asof, o.value, o.display, o.tier, o.extra,
                   o.observed_at, o.released
            from macro_core.observation o
            join macro_core.series s on s.id = o.series_id
            where o.asof >= $1
            order by s.key, o.asof
            """,
            since,
        )
        return [
            Reading(
                series_key=record["key"],
                asof=record["asof"],
                value=record["value"],
                display=record["display"] or "",
                tier=record["tier"],
                extra=_json(record["extra"]),
                observed_at=record["observed_at"],
                released=record["released"],
            )
            for record in found
        ]

    async def open_gaps(self, *, since: date) -> list[tuple[str, date]]:
        found = await self._fetch(
            """
            select s.key as key, g.asof
            from macro_quality.gap g
            join macro_core.series s on s.id = g.series_id
            where g.asof >= $1
            """,
            since,
        )
        return [(record["key"], record["asof"]) for record in found]

    async def write_observations(
        self,
        observations: Sequence[Observation],
        series_ids: dict[str, str],
        *,
        received_at: datetime,
    ) -> int:
        """Upsert on `(series_id, asof)`. The later run of a day wins, with two exceptions.

        A row marked `defer_to_better_tier` does not replace a stored row of a
        better (lower) tier: a re-statement made without its cross-check is not
        evidence against what a past run verified. And the `extra` keys a row
        names in `keep_extra` survive from the stored row when the new one does
        not carry them; every other key is replaced wholesale, as before.

        Returns the number of rows offered, not the number that changed.
        """
        rows = [
            (
                series_ids[one.series_key],
                one.asof,
                one.value,
                one.display,
                one.tier,
                one.source,
                one.released,
                one.note,
                json.dumps(one.extra, default=str),
                one.observed_at,
                received_at,
                list(one.keep_extra),
                one.defer_to_better_tier,
            )
            for one in observations
            if one.series_key in series_ids
        ]
        if not rows:
            return 0
        await self._executemany(
            """
            insert into macro_core.observation
              (series_id, asof, value, display, tier, source, released, note, extra,
               observed_at, received_at)
            values ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10,$11)
            on conflict (series_id, asof) do update set
              value = excluded.value,
              display = excluded.display,
              tier = excluded.tier,
              source = excluded.source,
              released = excluded.released,
              note = excluded.note,
              extra = (
                select coalesce(jsonb_object_agg(kept.key, kept.value), '{}'::jsonb)
                from jsonb_each(coalesce(observation.extra, '{}'::jsonb)) as kept
                where kept.key = any($12::text[])
              ) || excluded.extra,
              observed_at = excluded.observed_at,
              received_at = excluded.received_at
            where not $13::boolean or observation.tier >= excluded.tier
            """,
            rows,
        )
        return len(rows)

    async def write_batches(self, batches: Sequence[RawBatch]) -> None:
        """Archive every payload before normalisation, so a point can be traced back."""
        if not batches:
            return
        await self._executemany(
            "insert into macro_raw.batch (source, kind, fetched_at, payload) "
            "values ($1,$2,$3,$4::jsonb)",
            [
                (one.source, one.kind, one.fetched_at, json.dumps(one.payload, default=str))
                for one in batches
            ],
        )

    # ------------------------------------------------------------------
    # Quality
    # ------------------------------------------------------------------

    async def start_run(self, source: str, run_kind: str, started_at: datetime) -> int:
        record = await self._fetchrow(
            "insert into macro_quality.ingest_run (source, run_kind, started_at, status) "
            "values ($1,$2,$3,'running') returning id",
            source,
            run_kind,
            started_at,
        )
        return int(record["id"])

    async def finish_run(
        self,
        run_id: int,
        *,
        finished_at: datetime,
        rows: int,
        status: str,
        error: str | None,
    ) -> None:
        await self._execute(
            "update macro_quality.ingest_run set finished_at = $2, rows = $3, status = $4, "
            "error = $5 where id = $1",
            run_id,
            finished_at,
            rows,
            status,
            error,
        )

    async def write_gaps(self, gaps: Sequence[Gap], series_ids: dict[str, str]) -> None:
        """A gap is opened once and kept. Re-running never re-dates it."""
        rows = [
            (series_ids[one.series_key], one.series_key, one.asof, one.reason, one.permanent)
            for one in gaps
            if one.series_key in series_ids
        ]
        if not rows:
            return
        await self._executemany(
            "insert into macro_quality.gap (series_id, series_key, asof, reason, permanent) "
            "values ($1,$2,$3,$4,$5) on conflict (series_id, asof) do nothing",
            rows,
        )

    async def clear_filled_gaps(
        self, observations: Sequence[Observation], series_ids: dict[str, str]
    ) -> int:
        """Remove every gap whose reading is now stored, permanent or not.

        A gap says a reading is missing. Once the reading is in
        `macro_core.observation` the row is false, whatever was predicted about
        it when it was opened. `permanent` is that prediction: the source was
        thought to serve only the present moment. It can be wrong, as breadth
        showed: the 09:30 run opened a permanent gap for a session the source
        had not published yet, the 21:00 run stored that session's reading, and
        the gap stayed open beside it for good.

        Joined against the stored rows rather than trusting `observations`, so
        a write that was refused clears nothing, and scoped to the series this
        run touched, so a gap opened before this rule existed is retired by the
        next run that touches its series. A Tier 4 null is not the reading and
        clears nothing.

        Without this the table drifts from meaning "is missing" to meaning "was
        once late".
        """
        touched = sorted(
            {series_ids[one.series_key] for one in observations if one.series_key in series_ids}
        )
        if not touched:
            return 0
        status = await self._execute(
            """
            delete from macro_quality.gap g
            using macro_core.observation o
            where g.series_id = any($1::uuid[])
              and o.series_id = g.series_id
              and o.asof = g.asof
              and o.value is not null
            """,
            touched,
        )
        return int(status.split()[-1])

    async def sync_findings(
        self, findings: Sequence[Finding], *, now: datetime, scope: FindingScope
    ) -> None:
        """Insert what is newly wrong and close what has come right.

        Only a finding inside `scope` can come right: the run has to have
        evaluated that check again to know it no longer holds. A finding the
        21:00 rescore cannot raise (vault, archive, grading, the authoring
        agent's) is left open for the writer that can.

        `macro_quality.finding` has no unique constraint on `(check_key,
        subject)`, so there is nothing to upsert against. Reading the open rows
        first is the price of that schema; adding a partial unique index belongs
        to `supabase/` and this worker does not reach into that directory.
        """
        open_rows = await self._fetch(
            "select id, check_key, subject from macro_quality.finding where closed_at is null"
        )
        current = {(one.check_key, one.subject): one for one in findings}
        stored: dict[tuple[str, str], list[int]] = {}
        for record in open_rows:
            stored.setdefault((record["check_key"], record["subject"]), []).append(record["id"])

        new = [key for key in current if key not in stored]
        gone = [
            identifier
            for key, identifiers in stored.items()
            if key not in current and scope.covers(*key)
            for identifier in identifiers
        ]

        async with self._acquire() as connection, connection.transaction():
            if new:
                await connection.executemany(
                    "insert into macro_quality.finding "
                    "(check_key, subject, sentence, severity, opened_at) values ($1,$2,$3,$4,$5)",
                    [
                        (
                            current[key].check_key,
                            current[key].subject,
                            current[key].sentence,
                            current[key].severity,
                            now,
                        )
                        for key in new
                    ],
                )
            if gone:
                await connection.execute(
                    "update macro_quality.finding set closed_at = $2 where id = any($1::bigint[])",
                    gone,
                    now,
                )

    # ------------------------------------------------------------------
    # The vault feed
    # ------------------------------------------------------------------

    async def write_catalysts(self, catalysts: Sequence[Catalyst]) -> dict[str, str]:
        """Upsert on `external_key`, so a reschedule updates rather than duplicates."""
        if not catalysts:
            return {}
        await self._executemany(
            """
            insert into macro_core.catalyst
              (external_key, name, starts_at, ends_at, date_precision, source_url, affected,
               status, first_seen, last_seen, date_text, date_estimated, family, category)
            values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$9,$10,$11,$12,$13)
            on conflict (external_key) do update set
              name = excluded.name,
              starts_at = excluded.starts_at,
              ends_at = excluded.ends_at,
              date_precision = excluded.date_precision,
              date_text = excluded.date_text,
              date_estimated = excluded.date_estimated,
              family = excluded.family,
              category = excluded.category,
              source_url = excluded.source_url,
              affected = excluded.affected,
              status = excluded.status,
              last_seen = excluded.last_seen,
              retired_at = null,
              retired_reason = null
            """,
            [
                (
                    one.external_key,
                    one.name,
                    one.starts_at,
                    one.ends_at,
                    one.date_precision,
                    one.source_url,
                    list(one.affected),
                    one.status,
                    one.seen_on,
                    one.raw_date_text,
                    one.date_estimated,
                    one.family,
                    one.category,
                )
                for one in catalysts
            ],
        )
        found = await self._fetch("select external_key, id from macro_core.catalyst")
        return {record["external_key"]: str(record["id"]) for record in found}

    async def retire_catalysts(
        self, retirements: Sequence[tuple[str, str]], *, retired_on: date
    ) -> None:
        """Mark a catalyst retired rather than deleting it.

        A trigger clause bound to it is voided and kept, so the row it points at
        has to stay.
        """
        if not retirements:
            return
        await self._executemany(
            "update macro_core.catalyst set status = 'retired', retired_at = $3, "
            "retired_reason = $2 where external_key = $1 and status <> 'retired'",
            [(key, reason, retired_on) for key, reason in retirements],
        )

    async def tail_risks_seen(self) -> list[TailRiskSeen]:
        """Every tail risk ever listed, with its severity, first listing and retirement.

        Retired rows are returned with `retired_on` set rather than filtered
        here: drift needs them for the day the feed was first ingested, and
        leaves them out of what counts as live.
        """
        found = await self._fetch(
            "select name, severity, first_seen, retired_on from macro_core.tail_risk"
        )
        return [
            TailRiskSeen(
                name=one["name"],
                severity=one["severity"],
                first_seen=one["first_seen"],
                retired_on=one["retired_on"],
            )
            for one in found
        ]

    async def tracked_catalysts(self) -> list[tuple[str, str]]:
        found = await self._fetch("select external_key, status from macro_core.catalyst")
        return [(record["external_key"], record["status"]) for record in found]

    async def unarchived_catalysts(self, *, before: datetime) -> list[dict[str, Any]]:
        """Live catalysts that started before `before` and have no archive row yet.

        `archive.from_live` decides which of them happened; this only narrows the
        read to the rows it could possibly archive.
        """
        found = await self._fetch(
            """
            select c.id, c.external_key, c.name, c.starts_at, c.ends_at, c.date_precision,
                   c.date_text, c.source_url, c.affected, c.first_seen, c.last_seen
            from macro_core.catalyst c
            where c.starts_at < $1
              and not exists (
                select 1 from macro_core.catalyst_occurrence o
                where o.name_key = c.external_key
                  and o.occurred_on = (c.starts_at at time zone 'UTC')::date
              )
            """,
            before,
        )
        return [dict(record) for record in found]

    async def write_occurrences(self, occurrences: Sequence[CatalystOccurrence]) -> None:
        """Upsert on `(name_key, occurred_on)`, so a replayed run or backfill changes nothing.

        A later write refreshes the descriptive columns but never clears what an
        earlier one knew: the link to the live row, the journal's market note, and
        the widest tracked span all survive a replay from a poorer source.
        """
        await self._executemany(
            """
            insert into macro_core.catalyst_occurrence
              (name, name_key, occurred_on, starts_at, ends_at, date_precision, family,
               category, source_url, affected, provenance, provenance_ref, confidence,
               moved_markets, market_note, note, catalyst_id, first_tracked, last_tracked)
            values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19)
            on conflict (name_key, occurred_on) do update set
              name = excluded.name,
              starts_at = excluded.starts_at,
              ends_at = excluded.ends_at,
              date_precision = excluded.date_precision,
              family = excluded.family,
              category = excluded.category,
              source_url = coalesce(excluded.source_url, catalyst_occurrence.source_url),
              affected = excluded.affected,
              provenance = excluded.provenance,
              provenance_ref = excluded.provenance_ref,
              confidence = excluded.confidence,
              moved_markets = coalesce(excluded.moved_markets, catalyst_occurrence.moved_markets),
              market_note = coalesce(excluded.market_note, catalyst_occurrence.market_note),
              note = coalesce(excluded.note, catalyst_occurrence.note),
              catalyst_id = coalesce(excluded.catalyst_id, catalyst_occurrence.catalyst_id),
              first_tracked = least(excluded.first_tracked, catalyst_occurrence.first_tracked),
              last_tracked = greatest(excluded.last_tracked, catalyst_occurrence.last_tracked)
            """,
            [
                (
                    one.name,
                    one.name_key,
                    one.occurred_on,
                    one.starts_at,
                    one.ends_at,
                    one.date_precision,
                    one.family,
                    one.category,
                    one.source_url,
                    list(one.affected),
                    one.provenance,
                    one.provenance_ref,
                    one.confidence,
                    one.moved_markets,
                    one.market_note,
                    one.note,
                    one.catalyst_id,
                    one.first_tracked,
                    one.last_tracked,
                )
                for one in occurrences
            ],
        )

    async def write_watchlist_changes(self, changes: Sequence[WatchlistChange]) -> None:
        if not changes:
            return
        await self._executemany(
            """
            insert into macro_core.watchlist_change
              (ticker, observed_at, change_pct, summary, source_url)
            values ($1,$2,$3,$4,$5)
            on conflict (ticker, observed_at) do update set
              change_pct = excluded.change_pct,
              summary = excluded.summary,
              source_url = excluded.source_url
            """,
            [
                (one.ticker, one.observed_at, one.change_pct, one.summary, one.source_url)
                for one in changes
            ],
        )

    async def write_holdings(
        self, holdings: Sequence[Holding], *, deactivate_missing: bool = True
    ) -> None:
        """Upsert the listed holdings, and deactivate the ones that left the list.

        Deactivate, never delete: a `holdings_check` row and a `watchlist_change`
        row both point at a ticker, and a position that was closed is still part
        of the record of the week it was held.

        `deactivate_missing` is false when the Watchlist was not read whole
        (`VaultFeed.holdings_whole`): a file that could not be read lists no
        holdings, and that silence is not a list of closed positions.
        """
        tickers = [one.ticker for one in holdings]
        async with self._acquire() as connection, connection.transaction():
            if holdings:
                await connection.executemany(
                    """
                    insert into macro_core.holding (ticker, label, book, active)
                    values ($1,$2,$3,true)
                    on conflict (ticker) do update set
                      label = excluded.label, book = excluded.book, active = true
                    """,
                    [(one.ticker, one.label, one.book) for one in holdings],
                )
            if deactivate_missing:
                await connection.execute(
                    "update macro_core.holding set active = false "
                    "where active and not (ticker = any($1::text[]))",
                    tickers,
                )

    async def write_tail_risks(
        self, risks: Sequence[TailRisk], *, retire_missing: bool = True
    ) -> None:
        """Upsert the risks the note lists now, and retire the ones it dropped.

        `seen_on` is the day the note was written, so `last_seen` says when the
        weekly cron last listed a risk rather than when the daily run re-read
        it. A dropped risk is retired, never deleted: it stays for the record
        and leaves `macro_mart.tail_risk_state`. An empty parse retires nothing,
        because an unreadable note and a note with no risks look the same here,
        and neither does a note missing one of its sections (`retire_missing`
        false, from `VaultFeed.tail_risks_whole`).
        """
        if not risks:
            return
        listed_on = max(one.seen_on for one in risks)
        async with self.transaction() as connection:
            await connection.executemany(
                """
                insert into macro_core.tail_risk
                  (key, name, severity, summary, priced_in, assets, source_url, first_seen,
                   last_seen)
                values ($1,$2,$3,$4,$5,$6,$7,$8,$8)
                on conflict (key) do update set
                  name = excluded.name,
                  severity = excluded.severity,
                  summary = excluded.summary,
                  priced_in = excluded.priced_in,
                  assets = excluded.assets,
                  source_url = excluded.source_url,
                  first_seen = least(tail_risk.first_seen, excluded.first_seen),
                  last_seen = excluded.last_seen,
                  retired_on = null
                """,
                [
                    (
                        one.key,
                        one.name,
                        one.severity,
                        one.summary,
                        one.priced_in,
                        list(one.assets),
                        one.source_url,
                        one.seen_on,
                    )
                    for one in risks
                ],
            )
            if retire_missing:
                await connection.execute(
                    "update macro_core.tail_risk set retired_on = $2 "
                    "where retired_on is null and not (key = any($1::text[]))",
                    [one.key for one in risks],
                    listed_on,
                )

    async def record_feed_run(
        self,
        feed: str,
        *,
        started_at: datetime,
        finished_at: datetime,
        rows: int,
        status: str,
        error: str | None,
    ) -> None:
        """One `ingest_run` for a feed this run read from its Obsidian note.

        `macro_mart.feed_freshness` reads these beside the desk's own runs, so the
        page can say where each feed last came from while it is still the vault.
        """
        await self._execute(
            "insert into macro_quality.ingest_run (source, run_kind, started_at, finished_at, "
            "rows, status, error, feed, origin) values ($1, 'full', $2, $3, $4, $5, $6, $7, "
            "'vault')",
            f"vault:{feed}",
            started_at,
            finished_at,
            rows,
            status,
            error,
            feed,
        )

    # ------------------------------------------------------------------
    # Judgment
    # ------------------------------------------------------------------

    async def asserted_conditions(self) -> dict[str, AssertedCondition]:
        """The as-written status of every `manual` condition."""
        found = await self._fetch(
            "select condition_id, owner_kind, status, current_text, asserted_by "
            "from macro_core.condition_state_asserted"
        )
        return {
            str(record["condition_id"]): AssertedCondition(
                condition_id=str(record["condition_id"]),
                owner_kind=record["owner_kind"],
                status=Status(record["status"]),
                current_text=record["current_text"],
                asserted_by=record["asserted_by"],
            )
            for record in found
        }

    async def active_phase_calls(self) -> list[PhaseCall]:
        """Every active phase call and its exactly four conditions."""
        found = await self._fetch(
            """
            select c.id, c.asset, c.week, c.phase, c.phase_name, c.activated_at,
                   c.classification, c.stage, c.stage_reason,
                   p.id as condition_id, p.ordinal, p.text, p.kind, p.expr
            from macro_core.phase_call c
            join macro_core.phase_condition p on p.phase_call_id = c.id
            where c.status = 'active'
            order by c.asset, p.ordinal
            """
        )
        by_call: dict[str, list[asyncpg.Record]] = {}
        for record in found:
            by_call.setdefault(str(record["id"]), []).append(record)
        out: list[PhaseCall] = []
        for call_id, records in by_call.items():
            head = records[0]
            out.append(
                PhaseCall(
                    phase_call_id=call_id,
                    asset=head["asset"],
                    week=head["week"],
                    phase=head["phase"],
                    phase_name=head["phase_name"],
                    activated_at=head["activated_at"],
                    classification=head["classification"],
                    stage=head["stage"],
                    stage_reason=head["stage_reason"],
                    conditions=tuple(
                        PhaseCondition(
                            condition_id=str(one["condition_id"]),
                            phase_call_id=call_id,
                            ordinal=one["ordinal"],
                            text=one["text"],
                            kind=one["kind"],
                            expr=_json_or_none(one["expr"]),
                        )
                        for one in records
                    ),
                )
            )
        return out

    async def active_scenario_set(self) -> ScenarioSet | None:
        """The one active scenario set, with its clauses and its target rows."""
        head = await self._fetch(
            "select id, week, authored_at, activated_at from macro_core.scenario_set "
            "where status = 'active' order by week desc limit 1"
        )
        if not head:
            return None
        set_id = str(head[0]["id"])
        activated = head[0]["activated_at"] or head[0]["authored_at"]
        scenarios = await self._fetch(
            "select id, ordinal, title, probability, positioning from macro_core.scenario "
            "where set_id = $1 order by ordinal",
            set_id,
        )
        clauses = await self._fetch(
            """
            select t.id, t.scenario_id, t.ordinal, t.text, t.kind, t.expr, t.catalyst_id,
                   t.required, t.void_at, t.void_reason
            from macro_core.trigger_clause t
            join macro_core.scenario s on s.id = t.scenario_id
            where s.set_id = $1
            order by t.scenario_id, t.ordinal
            """,
            set_id,
        )
        targets = await self._fetch(
            """
            select g.id, g.scenario_id, g.series_id, g.label, g.kind, g.comparator,
                   g.lo, g.hi, g.text, sr.key as series_key
            from macro_core.scenario_target g
            join macro_core.scenario s on s.id = g.scenario_id
            left join macro_core.series sr on sr.id = g.series_id
            where s.set_id = $1
            order by g.scenario_id, g.label
            """,
            set_id,
        )
        by_scenario_clauses: dict[str, list[TriggerClause]] = {}
        for one in clauses:
            by_scenario_clauses.setdefault(str(one["scenario_id"]), []).append(
                TriggerClause(
                    condition_id=str(one["id"]),
                    scenario_id=str(one["scenario_id"]),
                    ordinal=one["ordinal"],
                    text=one["text"],
                    kind=one["kind"],
                    expr=_json_or_none(one["expr"]),
                    catalyst_id=None if one["catalyst_id"] is None else str(one["catalyst_id"]),
                    required=one["required"],
                    void_at=one["void_at"],
                    void_reason=one["void_reason"],
                )
            )
        by_scenario_targets: dict[str, list[ScenarioTarget]] = {}
        for one in targets:
            by_scenario_targets.setdefault(str(one["scenario_id"]), []).append(
                ScenarioTarget(
                    target_id=str(one["id"]),
                    scenario_id=str(one["scenario_id"]),
                    series_key=one["series_key"],
                    label=one["label"],
                    kind=one["kind"],
                    comparator=one["comparator"],
                    lo=one["lo"],
                    hi=one["hi"],
                    text=one["text"],
                )
            )
        return ScenarioSet(
            set_id=set_id,
            week=head[0]["week"],
            authored_asof=activated.date(),
            activated_at=activated,
            scenarios=tuple(
                Scenario(
                    scenario_id=str(one["id"]),
                    set_id=set_id,
                    ordinal=one["ordinal"],
                    title=one["title"],
                    probability=one["probability"],
                    positioning=one["positioning"],
                    clauses=tuple(by_scenario_clauses.get(str(one["id"]), ())),
                    targets=tuple(by_scenario_targets.get(str(one["id"]), ())),
                )
                for one in scenarios
            ),
        )

    async def active_setups(self) -> list[Setup]:
        """Every active setup, with the series its levels are measured on.

        `macro_core.setup.series_id` says which series that is, so this reads it
        rather than deriving it. A setup with a null `series_id` is refused: it
        does not say what it is measured against, and measuring a Nasdaq setup
        against the S&P would produce an entry state, an R-multiple and a Setup
        Ledger row that are all wrong and none of them visibly so.
        """
        found = await self._fetch(
            _SETUP_SELECT + " where s.status = 'active' order by s.week, s.asset, s.name"
        )
        return self._setups(found)

    async def catalysts_by_id(self) -> dict[str, Catalyst]:
        found = await self._fetch(
            "select id, external_key, name, starts_at, ends_at, date_precision, source_url, "
            "affected, status, first_seen, last_seen, date_text, date_estimated, family, "
            "category from macro_core.catalyst"
        )
        return {
            str(one["id"]): Catalyst(
                external_key=one["external_key"],
                name=one["name"],
                starts_at=one["starts_at"],
                ends_at=one["ends_at"],
                date_precision=one["date_precision"],
                source_url=one["source_url"] or "",
                affected=tuple(one["affected"] or ()),
                status=one["status"],
                seen_on=one["last_seen"],
                raw_date_text=one["date_text"] or "",
                date_estimated=bool(one["date_estimated"]),
                family=one["family"],
                category=one["category"],
            )
            for one in found
        }

    async def graded_weeks(self) -> list[GradedWeek]:
        """The Scorecard Ledger, oldest first, as the calibration read needs it."""
        found = await self._fetch(
            "select week, base_scenario_label, base_grade, brier from macro_core.week_grade "
            "order by week"
        )
        return [
            GradedWeek(
                week=one["week"],
                base_scenario_label=one["base_scenario_label"],
                base_grade=one["base_grade"],
                brier=one["brier"],
            )
            for one in found
        ]

    async def setups_for_week(self, week: str) -> list[Setup]:
        """Every setup proposed in one week, active or superseded.

        Grading reads the week's setups as they were written, not only the ones
        still standing: a setup superseded by a re-proposal the following week
        still has an outcome for the week it was proposed in.

        A setup with a null `series_id` is refused here too, and a setup refused
        at grading is a setup that leaves no ledger row, which the setup check
        then reports as missing.
        """
        found = await self._fetch(
            _SETUP_SELECT + " where s.week = $1 order by s.asset, s.name", week
        )
        return self._setups(found)

    async def write_setup_grades(self, grades: Sequence[SetupGrade]) -> None:
        """The Setup Ledger. An `open` row is rewritten, never duplicated.

        The primary key is `setup_id`, so the rewrite is the upsert: a setup
        graded `open` in W39 and `triggered → target` in W40 is one row that
        changed, not two rows that disagree.
        """
        if not grades:
            return
        await self._executemany(
            """
            insert into macro_core.setup_grade (setup_id, graded_week, outcome, r_realised, note)
            values ($1,$2,$3,$4,$5)
            on conflict (setup_id) do update set
              graded_week = excluded.graded_week,
              outcome = excluded.outcome,
              r_realised = excluded.r_realised,
              note = excluded.note
            """,
            [
                (one.setup_id, one.graded_week, one.outcome, one.r_realised, one.note)
                for one in grades
            ],
        )

    async def write_week_grade(self, grade: WeekGrade) -> None:
        """One Scorecard Ledger row, upserted on the week."""
        await self._execute(
            """
            insert into macro_core.week_grade
              (week, base_scenario_id, base_scenario_label, base_grade, other_grades, eq_call,
               crypto_call, key_miss, targets_hit, targets_n, brier, premise_moved, graded_at)
            values ($1,$2,$3,$4,$5::jsonb,$6,$7,$8,$9,$10,$11,$12,$13)
            on conflict (week) do update set
              base_scenario_id = excluded.base_scenario_id,
              base_scenario_label = excluded.base_scenario_label,
              base_grade = excluded.base_grade,
              other_grades = excluded.other_grades,
              eq_call = excluded.eq_call,
              crypto_call = excluded.crypto_call,
              key_miss = excluded.key_miss,
              targets_hit = excluded.targets_hit,
              targets_n = excluded.targets_n,
              brier = excluded.brier,
              premise_moved = excluded.premise_moved,
              graded_at = excluded.graded_at
            """,
            grade.week,
            grade.base_scenario_id,
            grade.base_scenario_label,
            grade.base_grade,
            json.dumps(grade.other_grades, default=str),
            grade.eq_call,
            grade.crypto_call,
            grade.key_miss,
            grade.targets_hit,
            grade.targets_n,
            grade.brier,
            grade.premise_moved,
            grade.graded_at,
        )

    async def open_setup_grades(self) -> list[tuple[str, str, str]]:
        """Every setup still `open` in the ledger, for the integrity check."""
        found = await self._fetch(
            """
            select g.setup_id, g.graded_week, s.name
            from macro_core.setup_grade g
            join macro_core.setup s on s.id = g.setup_id
            where g.outcome = 'open'
            """
        )
        return [(str(one["setup_id"]), one["graded_week"], one["name"]) for one in found]

    async def graded_week_keys(self) -> list[str]:
        found = await self._fetch("select week from macro_core.week_grade order by week")
        return [one["week"] for one in found]

    async def void_clauses(self, voidings: Sequence[tuple[str, str, datetime]]) -> None:
        """Mark clauses void. Never delete one, and never touch a threshold.

        The `where void_at is null` guard is what keeps the original moment: a
        clause voided last Tuesday stays voided last Tuesday however many runs
        see it afterwards.
        """
        if not voidings:
            return
        await self._executemany(
            "update macro_core.trigger_clause set void_at = $3, void_reason = $2 "
            "where id = $1 and void_at is null",
            list(voidings),
        )

    async def activate(
        self,
        table: str,
        *,
        incoming_id: str,
        outgoing_id: str | None,
        activated_at: datetime,
    ) -> None:
        """Stand one judgment row up and the row it replaces down, in one transaction.

        The partial unique index that enforces "at most one active per subject"
        cannot be deferred, so the order is fixed: supersede first, activate
        second. The other order leaves two active rows for the length of one
        statement and is rejected.
        """
        if table not in _JUDGMENT_TABLES:
            raise WriteRefused(f"{table!r} is not a judgment table this worker activates")
        async with self._acquire() as connection, connection.transaction():
            if outgoing_id is not None:
                await connection.execute(
                    f"update macro_core.{table} set status = 'superseded', superseded_by = $2 "
                    "where id = $1",
                    outgoing_id,
                    incoming_id,
                )
            await connection.execute(
                f"update macro_core.{table} set status = 'active', activated_at = $2 "
                "where id = $1",
                incoming_id,
                activated_at,
            )


_JUDGMENT_TABLES = frozenset({"phase_call", "scenario_set", "setup", "strategy"})


class MartFlush:
    """The rows one evaluate step produced, written as one transaction.

    All of `macro_mart` moves together or none of it does. A snapshot from one
    run beside a condition state from the next is a reading that never existed,
    and the web app has no way to tell that it is looking at one.
    """

    def __init__(self, store: Store) -> None:
        self._store = store

    async def write(
        self,
        *,
        snapshot_rows: Sequence[Any],
        condition_states: Sequence[ConditionState],
        phase_scores: Sequence[PhaseScore],
        scenario_states: Sequence[ScenarioState],
        target_states: Sequence[ScenarioTargetState],
        setup_states: Sequence[SetupState],
        catalyst_rows: Sequence[CatalystWindowRow],
        calibration: Any | None,
        series_ids: dict[str, str],
        evaluated_at: datetime,
        drift: Drift | None = None,
    ) -> None:
        async with self._store.transaction() as connection:
            await _flush_snapshot(connection, snapshot_rows, series_ids, evaluated_at)
            await _flush_conditions(connection, condition_states, evaluated_at)
            await _flush_phase(connection, phase_scores, evaluated_at)
            await _flush_scenarios(
                connection, scenario_states, target_states, series_ids, evaluated_at
            )
            await _flush_setups(connection, setup_states, evaluated_at)
            await _flush_catalyst_window(connection, catalyst_rows)
            await _flush_drift(connection, drift, evaluated_at)
            if calibration is not None:
                await _flush_calibration(connection, calibration, evaluated_at)


async def _flush_snapshot(
    connection: Any,
    rows: Sequence[Any],
    series_ids: dict[str, str],
    evaluated_at: datetime,
) -> None:
    await connection.executemany(
        """
        insert into macro_mart.snapshot_row
          (series_id, key, label, table_group, ordinal, value, display, unit, value_kind,
           cadence, delta_native, delta_native_label, delta_week_open, anchor_asof, tier,
           source, asof, released, next_expected, observed_at, freshness, note, extra,
           updated_at)
        values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,
                $21,$22,$23::jsonb,$24)
        on conflict (series_id) do update set
          key = excluded.key, label = excluded.label, table_group = excluded.table_group,
          ordinal = excluded.ordinal, value = excluded.value, display = excluded.display,
          unit = excluded.unit, value_kind = excluded.value_kind,
          cadence = excluded.cadence, delta_native = excluded.delta_native,
          delta_native_label = excluded.delta_native_label,
          delta_week_open = excluded.delta_week_open, anchor_asof = excluded.anchor_asof,
          tier = excluded.tier, source = excluded.source, asof = excluded.asof,
          released = excluded.released, next_expected = excluded.next_expected,
          observed_at = excluded.observed_at, freshness = excluded.freshness,
          note = excluded.note, extra = excluded.extra,
          updated_at = excluded.updated_at
        """,
        [
            (
                series_ids[row.series_key],
                row.series_key,
                row.label,
                row.table_group,
                row.ordinal,
                row.value,
                row.display,
                row.unit,
                row.value_kind,
                row.cadence,
                row.delta_native,
                row.delta_native_label,
                row.delta_week_open,
                row.anchor_asof,
                row.tier,
                row.source,
                row.asof,
                row.released,
                row.next_expected,
                row.observed_at,
                row.freshness,
                row.note,
                json.dumps(row.extra, default=str),
                evaluated_at,
            )
            for row in rows
            if row.series_key in series_ids
        ],
    )


async def _flush_conditions(
    connection: Any, states: Sequence[ConditionState], evaluated_at: datetime
) -> None:
    if not states:
        return
    await connection.executemany(
        """
        insert into macro_mart.condition_state
          (condition_id, owner_kind, owner_id, ordinal, text, kind, status, current_display,
           evidence, source, void_at, void_reason, evaluated_at)
        values ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10,$11,$12,$13)
        on conflict (condition_id) do update set
          owner_kind = excluded.owner_kind, owner_id = excluded.owner_id,
          ordinal = excluded.ordinal, text = excluded.text, kind = excluded.kind,
          status = excluded.status, current_display = excluded.current_display,
          evidence = excluded.evidence, source = excluded.source,
          void_at = excluded.void_at, void_reason = excluded.void_reason,
          evaluated_at = excluded.evaluated_at
        """,
        [
            (
                one.condition_id,
                one.owner_kind,
                one.owner_id,
                one.ordinal,
                one.text,
                one.kind,
                str(one.status),
                one.current_display,
                json.dumps(one.evidence, default=str),
                one.source,
                one.void_at,
                one.void_reason,
                evaluated_at,
            )
            for one in states
        ],
    )


async def _flush_phase(
    connection: Any, scores: Sequence[PhaseScore], evaluated_at: datetime
) -> None:
    if not scores:
        return
    await connection.executemany(
        """
        insert into macro_mart.phase_score
          (asset, phase_call_id, phase, phase_name, stage,
           met, of, machine_checked, manual, unevaluable, failed, read_stale, evaluated_at)
        values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
        on conflict (asset) do update set
          phase_call_id = excluded.phase_call_id, phase = excluded.phase,
          phase_name = excluded.phase_name, stage = excluded.stage,
          met = excluded.met, of = excluded.of,
          machine_checked = excluded.machine_checked, manual = excluded.manual,
          unevaluable = excluded.unevaluable, failed = excluded.failed,
          read_stale = excluded.read_stale, evaluated_at = excluded.evaluated_at
        """,
        [
            (
                one.asset,
                one.phase_call_id,
                one.phase,
                one.phase_name,
                one.stage,
                one.met,
                one.of,
                one.machine_checked,
                one.manual,
                one.unevaluable,
                one.failed,
                one.read_stale,
                evaluated_at,
            )
            for one in scores
        ],
    )


async def _flush_scenarios(
    connection: Any,
    states: Sequence[ScenarioState],
    targets: Sequence[ScenarioTargetState],
    series_ids: dict[str, str],
    evaluated_at: datetime,
) -> None:
    if states:
        await connection.executemany(
            """
            insert into macro_mart.scenario_state
              (scenario_id, set_id, ordinal, title, probability, positioning, clauses_fired,
               clauses_total, clauses_void, required_unmet, bands_holding, bands_total,
               targets_holding, targets_total, premise_moved, evaluated_at)
            values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
            on conflict (scenario_id) do update set
              set_id = excluded.set_id, ordinal = excluded.ordinal, title = excluded.title,
              probability = excluded.probability, positioning = excluded.positioning,
              clauses_fired = excluded.clauses_fired,
              clauses_total = excluded.clauses_total, clauses_void = excluded.clauses_void,
              required_unmet = excluded.required_unmet, bands_holding = excluded.bands_holding,
              bands_total = excluded.bands_total, targets_holding = excluded.targets_holding,
              targets_total = excluded.targets_total, premise_moved = excluded.premise_moved,
              evaluated_at = excluded.evaluated_at
            """,
            [
                (
                    one.scenario_id,
                    one.set_id,
                    one.ordinal,
                    one.title,
                    one.probability,
                    one.positioning,
                    one.clauses_fired,
                    one.clauses_total,
                    one.clauses_void,
                    one.required_unmet,
                    one.bands_holding,
                    one.bands_total,
                    one.targets_holding,
                    one.targets_total,
                    one.premise_moved,
                    evaluated_at,
                )
                for one in states
            ],
        )
    if targets:
        await connection.executemany(
            """
            insert into macro_mart.scenario_target_state
              (target_id, scenario_id, series_id, label, kind, comparator, text, lo, hi,
               current, holding, distance_to_breach, evaluated_at)
            values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
            on conflict (target_id) do update set
              scenario_id = excluded.scenario_id, series_id = excluded.series_id,
              label = excluded.label, kind = excluded.kind,
              comparator = excluded.comparator, text = excluded.text, lo = excluded.lo,
              hi = excluded.hi,
              current = excluded.current, holding = excluded.holding,
              distance_to_breach = excluded.distance_to_breach,
              evaluated_at = excluded.evaluated_at
            """,
            [
                (
                    one.target_id,
                    one.scenario_id,
                    series_ids.get(one.series_key) if one.series_key else None,
                    one.label,
                    one.kind,
                    # Carried as written. `lo` null with `hi` set is "below X" under
                    # `lt` and "we never wrote the floor" under `between`, and only
                    # one of those is a forecast.
                    one.comparator,
                    one.text,
                    one.lo,
                    one.hi,
                    one.current,
                    one.holding,
                    one.distance_to_breach,
                    evaluated_at,
                )
                for one in targets
            ],
        )


async def _flush_setups(
    connection: Any, states: Sequence[SetupState], evaluated_at: datetime
) -> None:
    if not states:
        return
    await connection.executemany(
        """
        insert into macro_mart.setup_state
          (setup_id, week, asset, series_key, series_label, name, entry_lo, entry_hi,
           invalidation, invalidation_dir, target, rr_planned, gate_state, gate_reason,
           gate_text, sector_focus_label, band_touched_today, band_touched_close, entry_state,
           sessions_carried, r_running, evaluated_at)
        values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22)
        on conflict (setup_id) do update set
          week = excluded.week, asset = excluded.asset,
          series_key = excluded.series_key, series_label = excluded.series_label,
          name = excluded.name,
          entry_lo = excluded.entry_lo, entry_hi = excluded.entry_hi,
          invalidation = excluded.invalidation,
          invalidation_dir = excluded.invalidation_dir, target = excluded.target,
          rr_planned = excluded.rr_planned, gate_state = excluded.gate_state,
          gate_reason = excluded.gate_reason, gate_text = excluded.gate_text,
          sector_focus_label = excluded.sector_focus_label,
          band_touched_today = excluded.band_touched_today,
          band_touched_close = excluded.band_touched_close, entry_state = excluded.entry_state,
          sessions_carried = excluded.sessions_carried, r_running = excluded.r_running,
          evaluated_at = excluded.evaluated_at
        """,
        [
            (
                one.setup_id,
                one.week,
                one.asset,
                # Null means the setup names no series and is deliberately not scored.
                # Never defaulted: the page has to be able to say why it sits ungraded.
                one.series_key,
                one.series_label,
                one.name,
                one.entry_lo,
                one.entry_hi,
                one.invalidation,
                one.invalidation_dir,
                one.target,
                one.rr_planned,
                one.gate_state,
                one.gate_reason,
                one.gate_text,
                one.sector_focus_label,
                one.band_touched_today,
                one.band_touched_close,
                one.entry_state,
                one.sessions_carried,
                one.r_running,
                evaluated_at,
            )
            for one in states
        ],
    )


async def _flush_catalyst_window(
    connection: Any, rows: Sequence[CatalystWindowRow]
) -> None:
    await connection.execute("delete from macro_mart.catalyst_window")
    if not rows:
        return
    await connection.executemany(
        """
        insert into macro_mart.catalyst_window
          (catalyst_id, name, starts_at, date_precision, affected, days_out, referenced_by,
           source_url, ends_at, date_text, date_estimated, family, category)
        values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
        """,
        [
            (
                one.catalyst_id,
                one.name,
                one.starts_at,
                one.date_precision,
                list(one.affected),
                one.days_out,
                list(one.referenced_by),
                one.source_url,
                one.ends_at,
                one.date_text,
                one.date_estimated,
                one.family,
                one.category,
            )
            for one in rows
        ],
    )


async def _flush_drift(connection: Any, drift: Drift | None, evaluated_at: datetime) -> None:
    # One row for the active set and none for any other: a drift read against a
    # superseded call is a claim about a call nobody holds any more.
    await connection.execute("delete from macro_mart.drift")
    if drift is None:
        return
    await connection.execute(
        """
        insert into macro_mart.drift
          (set_id, week, anchor_asof, asof, score, material, refresh_suggested, reason, items,
           evaluated_at)
        values ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10)
        """,
        drift.set_id,
        drift.week,
        drift.anchor_asof,
        drift.asof,
        drift.score,
        drift.material,
        drift.refresh_suggested,
        drift.reason,
        json.dumps([one.as_json() for one in drift.items]),
        evaluated_at,
    )


async def _flush_calibration(
    connection: Any, read: Any, computed_at: datetime
) -> None:
    await connection.execute(
        """
        insert into macro_mart.calibration
          (id, graded_weeks, base_hit_score, base_materialized, mean_brier, brier_weeks,
           base_was_s2, "window", computed_at)
        values (1,$1,$2,$3,$4,$5,$6,$7,$8)
        on conflict (id) do update set
          graded_weeks = excluded.graded_weeks, base_hit_score = excluded.base_hit_score,
          base_materialized = excluded.base_materialized, mean_brier = excluded.mean_brier,
          brier_weeks = excluded.brier_weeks, base_was_s2 = excluded.base_was_s2,
          "window" = excluded."window", computed_at = excluded.computed_at
        """,
        read.graded_weeks,
        read.base_hit_score,
        read.base_materialized,
        read.mean_brier,
        read.brier_weeks,
        read.base_was_s2,
        read.window,
        computed_at,
    )


#: Every column a `Setup` needs, with the series key resolved from
#: `macro_core.setup.series_id`. One select, used by both setup readers, so the
#: two can never drift into reading different columns.
_SETUP_SELECT = """
    select s.id, s.week, s.asset, s.name, s.entry_lo, s.entry_hi, s.invalidation,
           s.invalidation_dir, s.target, s.rr_planned, s.gate_expr, s.gate_text,
           s.sector_focus_id, s.activated_at, s.authored_at, sr.key as series_key
    from macro_core.setup s
    left join macro_core.series sr on sr.id = s.series_id
"""


def setup_refused_finding(setup_id: str, name: str, week: str) -> Finding:
    """The finding a setup opens when it does not say what it is measured on."""
    return Finding(
        check_key="setup_series_missing",
        subject=f"{name} ({week})",
        sentence=(
            f"The setup {name!r} from {week} carries no series_id, so nothing says which series "
            "its entry, invalidation and target are levels of. It is not scored and it has no "
            "setup_state row, because measuring it against a guessed series would produce an "
            f"entry state, an R-multiple and a Setup Ledger row that are all wrong (setup "
            f"{setup_id})."
        ),
        severity=1,
    )


def _setup_from(record: Any) -> Setup:
    """One `macro_core.setup` row, with `series_key` null when it names none.

    Null is carried rather than defaulted, and the setup is carried rather than
    dropped. `macro_core.setup.series_id` is the authoring agent's statement of
    what the levels are levels of; a setup that does not make it is not scored,
    and it is still shown with the reason, because a setup that disappears from
    the board is indistinguishable from one nobody proposed.
    """
    activated = record["activated_at"] or record["authored_at"]
    return Setup(
        setup_id=str(record["id"]),
        week=record["week"],
        asset=record["asset"],
        name=record["name"],
        series_key=record["series_key"] or None,
        entry_lo=record["entry_lo"],
        entry_hi=record["entry_hi"],
        invalidation=record["invalidation"],
        invalidation_dir=record["invalidation_dir"],
        target=record["target"],
        rr_planned=record["rr_planned"],
        gate_expr=_json_or_none(record["gate_expr"]),
        gate_text=record["gate_text"],
        sector_focus_id=(
            None if record["sector_focus_id"] is None else str(record["sector_focus_id"])
        ),
        activated_on=activated.date(),
    )


def _json_or_none(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    decoded = _json(value)
    return decoded or None


def _json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}
