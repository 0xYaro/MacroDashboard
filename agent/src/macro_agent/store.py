"""The only module in this package that talks to the database.

Same connection as the worker, for the same reason. `macro_raw` and `macro_core`
are deliberately not exposed through the Data API, so PostgREST returns
`PGRST106` for every table written here, and a scenario set has to be written
whole in one transaction, which PostgREST cannot span. `DATABASE_URL` over the
Supabase **Session pooler** on port 5432, asyncpg.

Conventions carried from `worker/src/macro/store.py` rather than invented:

- Every statement goes through one of four gateways, so an asyncpg fault becomes
  a `WriteRefused` carrying a sentence rather than a driver exception reaching a
  caller that has no idea what to say about it.
- Every name is fully schema-qualified. `search_path` is never set.
- `jsonb` is bound as text with an explicit `::jsonb` cast. It is decoded by
  the one codec `_init_connection` installs on every pooled connection, so a
  `jsonb` column always comes back as the Python value it holds. Without it
  asyncpg returns the raw text, and `observations.readings_from` calling
  `dict()` on a string is a `ValueError` at the first observation whose
  `extra` is set. The encoder passes text through untouched, so every write
  that already binds `json.dumps(...)` is unchanged.
- Every write is an idempotent upsert on a natural key. Replaying a run is safe.

Two things this module does that the worker's store does not, and both are the
reason it exists:

**A judgment is written whole, in one transaction.** The cardinality triggers in
`20260921000500_core_constraints.sql` are `DEFERRABLE INITIALLY DEFERRED` and fire at
`COMMIT`, so a scenario set arrives as one set head, three scenarios, their
clauses and their targets inside a single transaction or not at all. A partial
set is refused by the database, which is the point: a two-scenario set that
committed would be a forecast with a missing branch and nothing would say so.
`SET CONSTRAINTS ALL IMMEDIATE` is never issued here.

**An activation is two statements in one transaction, in a fixed order.** The
partial unique index on `status = 'active'` cannot be deferred, so the outgoing
row is stood down first and the incoming one stood up second. The other order
leaves two active rows for the length of one statement and is rejected.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import asyncpg

from .model import (
    Condition,
    Event,
    NarrativeObservation,
    PhaseCallProposal,
    ProposedGrade,
    ScenarioSetProposal,
    SetupProposal,
    StrategyProposal,
)

#: The four tables whose lifecycle this package may move. Anything else is not a
#: judgment and has no `proposed`, `active`, `superseded`.
JUDGMENT_TABLES: frozenset[str] = frozenset({"phase_call", "scenario_set", "setup", "strategy"})


class WriteRefused(Exception):
    """The database declined a read or a write. Expected and handled.

    The worker's own `WriteRefused` carries the same two rewrites, because the
    two commonest causes are the same two causes and a second wording for them
    would send the operator looking in a second place.
    """

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
        cardinality = ("check_violation", "scenarios; exactly 3", "conditions; exactly")
        if any(one in text for one in cardinality):
            return (
                f"{text}. A scenario set and a phase call are written whole in one transaction, "
                "because the cardinality triggers are deferred and fire at COMMIT. Nothing was "
                "partially applied."
            )
        return text


@dataclass(frozen=True, slots=True)
class LedgerRow:
    """One `macro_core.week_grade` row, as the context assembly needs it."""

    week: str
    base_scenario_label: str
    base_grade: str
    #: An array of `{"id", "prob", "grade", "note"}`, never an object keyed by
    #: scenario id. The web app reads an array, so an object parses as a
    #: malformed row and empties the calibration route.
    other_grades: list[dict[str, Any]]
    eq_call: str | None
    crypto_call: str | None
    key_miss: str | None
    targets_hit: int | None
    targets_n: int | None
    brier: Decimal | None
    premise_moved: bool


class Store:
    """A pool of two to four connections, and every statement this package sends."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    @classmethod
    async def connect(cls, dsn: str, *, ssl: str = "require") -> Store:
        try:
            pool = await asyncpg.create_pool(
                dsn,
                min_size=2,
                max_size=4,
                ssl=False if ssl == "disable" else "require",
                init=_init_connection,
            )
        except (OSError, asyncpg.PostgresError) as exc:
            raise WriteRefused(f"{type(exc).__name__}: {exc}") from exc
        if pool is None:  # pragma: no cover - asyncpg only returns None on a bad dsn
            raise WriteRefused("asyncpg returned no pool for the given DATABASE_URL")
        return cls(pool)

    async def aclose(self) -> None:
        await self._pool.close()

    # ------------------------------------------------------------------
    # The four gateways
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

    # ------------------------------------------------------------------
    # Reference data. Read only: config/series.yaml is the one authority for
    # macro_core.series and the worker applies it on every run, so a row
    # written here would either be overwritten or fight that sync.
    # ------------------------------------------------------------------

    async def series_ids(self) -> dict[str, str]:
        rows = await self._fetch("select key, id from macro_core.series")
        return {one["key"]: str(one["id"]) for one in rows}

    async def series_kinds(self) -> dict[str, tuple[str, str | None, int]]:
        """`key -> (value_kind, unit, tier_floor)`, for validating what a model returned."""
        rows = await self._fetch(
            "select key, value_kind, unit, tier_floor from macro_core.series"
        )
        return {one["key"]: (one["value_kind"], one["unit"], one["tier_floor"]) for one in rows}

    # ------------------------------------------------------------------
    # Mode A: the narrative half of the 09:30 run
    # ------------------------------------------------------------------

    async def latest_agent_observations(self, keys: Sequence[str]) -> dict[str, dict[str, Any]]:
        """The newest observation each agent series carries, for the prompt's sanity block.

        `gather.workflow.js` pasted the previous journal's values into every
        prompt so the model could notice a return it should not believe. The
        same job, read from the rows rather than from a markdown sidecar.
        """
        if not keys:
            return {}
        rows = await self._fetch(
            """
            select distinct on (s.key)
                   s.key, o.asof, o.value, o.display, o.tier, o.source, o.note
            from macro_core.series s
            join macro_core.observation o on o.series_id = s.id
            where s.key = any($1::text[])
            order by s.key, o.asof desc
            """,
            list(keys),
        )
        return {
            one["key"]: {
                "asof": one["asof"].isoformat(),
                "value": None if one["value"] is None else str(one["value"]),
                "display": one["display"],
                "tier": one["tier"],
                "source": one["source"],
                "note": one["note"],
            }
            for one in rows
        }

    async def write_observations(
        self,
        observations: Sequence[NarrativeObservation],
        series_ids: dict[str, str],
        *,
        received_at: datetime,
    ) -> int:
        """Upsert on `(series_id, asof)`. The later run of a day wins.

        The same statement and the same column order as the worker's, because
        the two write the same table and a divergence would show up as a column
        one writer sets and the other silently leaves behind.
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
              extra = excluded.extra,
              observed_at = excluded.observed_at,
              received_at = excluded.received_at
            """,
            rows,
        )
        return len(rows)

    async def write_search_batches(
        self, batches: Sequence[tuple[str, datetime, dict[str, Any]]]
    ) -> None:
        """Every search-agent return, archived as `macro_raw.batch` kind `search`.

        `docs/CONTRACT.md` requires it: a narrative point has to be traceable to
        the text it came from, and a search return that is not archived is a
        number with no provenance at all.
        """
        await self._executemany(
            "insert into macro_raw.batch (source, kind, fetched_at, payload) "
            "values ($1, 'search', $2, $3::jsonb)",
            [(source, at, json.dumps(payload, default=str)) for source, at, payload in batches],
        )

    async def write_gaps(self, gaps: Sequence[tuple[str, date, str, bool]]) -> None:
        """`(series_key, asof, reason, permanent)`. Opened once, never re-dated."""
        ids = await self.series_ids()
        await self._executemany(
            "insert into macro_quality.gap (series_id, series_key, asof, reason, permanent) "
            "values ($1, $2, $3, $4, $5) on conflict (series_id, asof) do nothing",
            [
                (ids[key], key, asof, reason, permanent)
                for key, asof, reason, permanent in gaps
                if key in ids
            ],
        )

    async def write_findings(
        self, findings: Sequence[tuple[str, str, str, int]], *, now: datetime
    ) -> None:
        """`(check_key, subject, sentence, severity)`. One sentence each, rendered verbatim."""
        await self._executemany(
            "insert into macro_quality.finding (check_key, subject, sentence, severity, opened_at)"
            " values ($1, $2, $3, $4, $5)",
            [
                (key, subject, sentence, severity, now)
                for key, subject, sentence, severity in findings
            ],
        )

    async def start_run(self, source: str, started_at: datetime) -> int:
        """One `macro_quality.ingest_run` row. `run_kind` is always `full`.

        This package never runs on the 21:00 rescore: there is no LLM on that
        path by decision, and `rescore` would be a lie about which run wrote it.
        """
        rows = await self._fetch(
            "insert into macro_quality.ingest_run (source, run_kind, started_at, status) "
            "values ($1, 'full', $2, 'running') returning id",
            source,
            started_at,
        )
        return int(rows[0]["id"])

    async def finish_run(
        self, run_id: int, *, finished_at: datetime, rows: int, status: str, error: str | None
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

    async def write_events(self, events: Sequence[Event]) -> int:
        """The dated event log.

        Upserted on `uq_macro_event_natural`, which
        `supabase/migrations/20260921001500_event_natural_key.sql` declares over
        `(occurred_on, kind, md5(text))`. Without it a replayed 09:30 run would
        append the whole log again, and the event log is the one table in this
        system a replay would have grown rather than restated.

        The key is the digest rather than the text because an event line is long
        and a btree index over it would sit near the page limit.
        """
        rows = [
            (one.occurred_on, one.kind, one.text, list(one.assets), one.surprise, one.source)
            for one in events
        ]
        if not rows:
            return 0
        await self._executemany(
            "insert into macro_core.event (occurred_on, kind, text, assets, surprise, source) "
            "values ($1,$2,$3,$4,$5,$6) "
            "on conflict (occurred_on, kind, md5(text)) do update set "
            "assets = excluded.assets, surprise = excluded.surprise, source = excluded.source",
            rows,
        )
        return len(rows)

    async def recent_watchlist_changes(self, *, since: date) -> list[str]:
        """The holdings deltas already recorded, as lines for the events prompt.

        Two writers, one before the desk cutover and one after: the worker's
        parse of `Market Watchlist.md` (`watchlist_change`) and the desk's
        holdings watch (`holding_watch`). Only a desk row with something to say
        is a delta: a material move, a driver, or no clear reason for a big
        move. A quiet name is not news, and a line per holding per day would
        bury the ones that are.
        """
        rows = await self._fetch(
            "select observed_at::date as day, ticker, change_pct, summary "
            "from macro_core.watchlist_change where observed_at >= $1 "
            "union all "
            "select as_of, symbol, change_24h_pct, "
            "  coalesce(driver_summary, case when no_clear_reason "
            "    then 'no clear reason found' end) "
            "from macro_core.holding_watch "
            "where as_of >= $2 and (material or driver_summary is not null or no_clear_reason) "
            "order by 1, 2",
            datetime.combine(since, datetime.min.time()).replace(tzinfo=None),
            since,
        )
        return [
            "[{d}] {t} {pct}{sep}{s}".format(
                d=one["day"].isoformat(),
                t=one["ticker"],
                pct="" if one["change_pct"] is None else f"{one['change_pct']}%",
                sep=": " if one["summary"] else "",
                s=one["summary"] or "",
            )
            for one in rows
        ]

    async def tail_risk_lines(self) -> list[str]:
        rows = await self._fetch(
            "select name, severity, summary, priced_in from macro_core.tail_risk "
            "where retired_on is null "
            "order by case severity when 'acute' then 0 when 'elevated' then 1 else 2 end, name"
        )
        return [
            f"[{one['severity']}] {one['name']} — {one['summary'] or ''}"
            f"{' (priced in: ' + one['priced_in'] + ')' if one['priced_in'] else ''}"
            for one in rows
        ]

    async def catalyst_lines(self, *, within_days: int = 30) -> list[str]:
        rows = await self._fetch(
            """
            select name, starts_at, affected from macro_core.catalyst
            where status = 'tracked'
              and starts_at is not null
              and starts_at >= now() - interval '2 days'
              and starts_at <= now() + ($1 || ' days')::interval
            order by starts_at
            """,
            str(within_days),
        )
        return [
            f"{one['starts_at'].date().isoformat()} {one['name']}"
            f"{' — ' + ', '.join(one['affected']) if one['affected'] else ''}"
            for one in rows
        ]

    async def catalysts_by_name(self) -> dict[str, str]:
        """`name -> id`, so a trigger clause can be bound to the catalyst it names."""
        rows = await self._fetch(
            "select id, name from macro_core.catalyst where status <> 'retired'"
        )
        return {one["name"]: str(one["id"]) for one in rows}

    async def holdings(self) -> list[tuple[str, str]]:
        rows = await self._fetch(
            "select ticker, book from macro_core.holding where active order by book, ticker"
        )
        return [(one["ticker"], one["book"]) for one in rows]

    # ------------------------------------------------------------------
    # Mode B: reading the week that is being graded, and the record
    # ------------------------------------------------------------------

    async def scenario_set_for_week(self, week: str) -> dict[str, Any] | None:
        """The scenario set of one week, whatever its status.

        The worker's reader takes the one `active` set. Grading reads the set of
        the week that has just closed, which by Monday may already have been
        superseded by an amendment, and a superseded set is still what was
        stated. The row is selected by week and the newest authored one wins.
        """
        head = await self._fetch(
            "select id, week, synthesis, probability_check, swing_variables, rules_waived, "
            "status, authored_at, activated_at, amends, amend_reason "
            "from macro_core.scenario_set where week = $1 "
            "order by authored_at desc limit 1",
            week,
        )
        if not head:
            return None
        set_id = str(head[0]["id"])
        scenarios = await self._fetch(
            "select id, ordinal, title, probability, positioning from macro_core.scenario "
            "where set_id = $1 order by ordinal",
            set_id,
        )
        clauses = await self._fetch(
            "select t.id, t.scenario_id, t.ordinal, t.text, t.kind, t.expr, t.catalyst_id, "
            "t.required, t.void_at, t.void_reason "
            "from macro_core.trigger_clause t join macro_core.scenario s on s.id = t.scenario_id "
            "where s.set_id = $1 order by t.scenario_id, t.ordinal",
            set_id,
        )
        targets = await self._fetch(
            "select g.id, g.scenario_id, g.series_id, g.label, g.kind, g.comparator, g.lo, g.hi, "
            "g.text, sr.key as series_key "
            "from macro_core.scenario_target g join macro_core.scenario s on s.id = g.scenario_id "
            "left join macro_core.series sr on sr.id = g.series_id "
            "where s.set_id = $1 order by g.scenario_id, g.label",
            set_id,
        )
        return {
            "set_id": set_id,
            "week": head[0]["week"],
            "synthesis": head[0]["synthesis"],
            "probability_check": head[0]["probability_check"],
            "swing_variables": _json_list(head[0]["swing_variables"]),
            "rules_waived": list(head[0]["rules_waived"] or ()),
            "status": head[0]["status"],
            "authored_at": head[0]["authored_at"],
            "activated_at": head[0]["activated_at"],
            "scenarios": [
                {
                    "scenario_id": str(one["id"]),
                    "ordinal": one["ordinal"],
                    "title": one["title"],
                    "probability": one["probability"],
                    "positioning": one["positioning"],
                    "clauses": [
                        {
                            "condition_id": str(c["id"]),
                            "ordinal": c["ordinal"],
                            "text": c["text"],
                            "kind": c["kind"],
                            "expr": _json_or_none(c["expr"]),
                            "catalyst_id": None
                            if c["catalyst_id"] is None
                            else str(c["catalyst_id"]),
                            "required": c["required"],
                            "void_at": c["void_at"],
                            "void_reason": c["void_reason"],
                        }
                        for c in clauses
                        if str(c["scenario_id"]) == str(one["id"])
                    ],
                    "targets": [
                        {
                            "target_id": str(t["id"]),
                            "series_key": t["series_key"],
                            "label": t["label"],
                            "kind": t["kind"],
                            "comparator": t["comparator"],
                            "lo": t["lo"],
                            "hi": t["hi"],
                            "text": t["text"],
                        }
                        for t in targets
                        if str(t["scenario_id"]) == str(one["id"])
                    ],
                }
                for one in scenarios
            ],
        }

    async def target_rows_for_week(self, week: str) -> list[dict[str, Any]]:
        """Every `macro_core.scenario_target` of a week, band and manual alike.

        This is the query behind `targets_n`. It counts rows and does not filter
        on `kind`, because the denominator is the row count and there is nowhere
        for a second convention to live.
        """
        return [
            dict(one)
            for one in await self._fetch(
                "select g.id, g.scenario_id, g.kind, g.label, g.comparator, g.lo, g.hi, g.text, "
                "sr.key as series_key, s.ordinal as scenario_ordinal "
                "from macro_core.scenario_target g "
                "join macro_core.scenario s on s.id = g.scenario_id "
                "join macro_core.scenario_set ss on ss.id = s.set_id "
                "left join macro_core.series sr on sr.id = g.series_id "
                "where ss.week = $1 order by s.ordinal, g.label",
                week,
            )
        ]

    async def setup_grades_for_week(self, week: str) -> list[dict[str, Any]]:
        """What the worker already decided about the prior week's setups.

        Read, never recomputed. A setup's outcome is a fact about the tape and
        the worker computed it deterministically on Monday's 09:30 run; grading
        it a second time here would put two graders on one ledger.
        """
        return [
            dict(one)
            for one in await self._fetch(
                "select g.setup_id, g.graded_week, g.outcome, g.r_realised, g.note, "
                "s.name, s.asset, s.style, s.week as proposed_week, s.entry_lo, s.entry_hi, "
                "s.invalidation, s.invalidation_dir, s.target, s.rr_planned, s.gate_text "
                "from macro_core.setup_grade g join macro_core.setup s on s.id = g.setup_id "
                "where g.graded_week = $1 order by s.asset, s.name",
                week,
            )
        ]

    async def ledger(self) -> list[LedgerRow]:
        """The Scorecard Ledger, oldest first, which is how the calibration read wants it."""
        return [
            LedgerRow(
                week=one["week"],
                base_scenario_label=one["base_scenario_label"],
                base_grade=one["base_grade"],
                other_grades=_json_list(one["other_grades"]),
                eq_call=one["eq_call"],
                crypto_call=one["crypto_call"],
                key_miss=one["key_miss"],
                targets_hit=one["targets_hit"],
                targets_n=one["targets_n"],
                brier=one["brier"],
                premise_moved=one["premise_moved"],
            )
            for one in await self._fetch(
                "select week, base_scenario_label, base_grade, other_grades, eq_call, "
                "crypto_call, key_miss, targets_hit, targets_n, brier, premise_moved "
                "from macro_core.week_grade order by week"
            )
        ]

    async def rules(self) -> list[dict[str, Any]]:
        rows = await self._fetch(
            "select r.number, r.text, r.retired_week, "
            "coalesce(array_agg(w.week order by w.week) filter (where w.week is not null), "
            "'{}') as weeks "
            "from macro_core.scenario_rule r "
            "left join macro_core.scenario_rule_week w on w.rule_number = r.number "
            "group by r.number, r.text, r.retired_week order by r.number"
        )
        return [
            {
                "number": one["number"],
                "text": one["text"],
                "retired_week": one["retired_week"],
                "weeks": list(one["weeks"]),
                "fire_count": len(one["weeks"]),
            }
            for one in rows
        ]

    async def active_phase_calls(self) -> list[dict[str, Any]]:
        head = await self._fetch(
            "select id, asset, week, phase, phase_name, prior_phase, direction, reason, "
            "evidence, falsifier, stage, stage_reason, activated_at, classification, "
            "classified_at, change_note "
            "from macro_core.phase_call where status = 'active' order by asset"
        )
        conditions = await self._fetch(
            "select c.id, c.phase_call_id, c.ordinal, c.text, c.kind, c.expr "
            "from macro_core.phase_condition c join macro_core.phase_call p "
            "on p.id = c.phase_call_id where p.status = 'active' order by c.ordinal"
        )
        return [
            {
                "phase_call_id": str(one["id"]),
                "asset": one["asset"],
                "week": one["week"],
                "phase": one["phase"],
                "phase_name": one["phase_name"],
                "prior_phase": one["prior_phase"],
                "direction": one["direction"],
                "reason": one["reason"],
                "evidence": list(one["evidence"] or ()),
                "falsifier": one["falsifier"],
                "stage": one["stage"],
                "stage_reason": one["stage_reason"],
                "activated_at": one["activated_at"],
                # `carried` on the fourteen migrated rows, whose four conditions
                # counted toward the next phase; `fresh` on every weekly read,
                # whose four are what must stay true for this phase to hold.
                "classification": one["classification"],
                "classified_at": one["classified_at"],
                "change_note": one["change_note"],
                "conditions": [
                    {
                        "condition_id": str(c["id"]),
                        "ordinal": c["ordinal"],
                        "text": c["text"],
                        "kind": c["kind"],
                        "expr": _json_or_none(c["expr"]),
                    }
                    for c in conditions
                    if str(c["phase_call_id"]) == str(one["id"])
                ],
            }
            for one in head
        ]

    async def snapshot(self) -> list[dict[str, Any]]:
        """`macro_mart.snapshot_row` as the worker last wrote it.

        Read from the mart on purpose. The authoring run must see exactly the
        board the operator sees, including each row's freshness and its note; a
        second derivation here could disagree with the page it is reasoning
        about.
        """
        return [
            dict(one)
            for one in await self._fetch(
                "select key, label, table_group, ordinal, value, display, unit, cadence, "
                "delta_native, delta_native_label, delta_week_open, tier, source, asof, "
                "freshness, note from macro_mart.snapshot_row "
                "order by table_group, ordinal"
            )
        ]

    async def history_rows(self, *, since: date) -> list[dict[str, Any]]:
        """Observations for the evaluator, in the shape `observations.readings_from` wants."""
        return [
            dict(one)
            for one in await self._fetch(
                "select s.key, o.asof, o.value, o.display, o.tier, o.extra, o.observed_at, "
                "o.released from macro_core.observation o "
                "join macro_core.series s on s.id = o.series_id "
                "where o.asof >= $1 order by s.key, o.asof",
                since,
            )
        ]

    async def evaluation_date(self, *, on_or_before: date | None = None) -> date | None:
        """The newest session the equity tape carries, the date the worker evaluates at.

        `worker/src/macro/main.py` `_evaluation_date`: the newest `spx_cash`
        observation, else the newest observation of any series. A condition is
        read on exactly one day, so scoring one against the calendar date on a
        Monday morning, before the US session, finds nothing and calls it
        unevaluable. `on_or_before` bounds it, for grading a closed week.
        """
        bound = on_or_before or date.max
        rows = await self._fetch(
            "select coalesce("
            "  (select max(o.asof) from macro_core.observation o "
            "     join macro_core.series s on s.id = o.series_id "
            "    where s.key = 'spx_cash' and o.asof <= $1), "
            "  (select max(o.asof) from macro_core.observation o where o.asof <= $1)"
            ") as asof",
            bound,
        )
        return None if not rows else rows[0]["asof"]

    async def open_gaps(self, *, since: date) -> list[tuple[str, date]]:
        return [
            (one["series_key"], one["asof"])
            for one in await self._fetch(
                "select series_key, asof from macro_quality.gap where asof >= $1", since
            )
        ]

    # ------------------------------------------------------------------
    # Mode B: writing
    # ------------------------------------------------------------------

    async def write_week_grade(self, grade: ProposedGrade) -> None:
        """One Scorecard Ledger row, upserted on the week."""
        await self._execute(
            """
            insert into macro_core.week_grade
              (week, base_scenario_id, base_scenario_label, base_grade, other_grades,
               eq_call, crypto_call, key_miss, targets_hit, targets_n, brier, premise_moved,
               graded_at)
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

    async def add_rule_week(self, number: int, week: str) -> None:
        """Add a graded week to a standing rule.

        `scenario_rule_week.week` is a real foreign key onto `week_grade`, so
        the grade row has to exist first. That ordering is the schema's, not a
        convention: a rule cannot cite a week nobody graded.
        """
        await self._execute(
            "insert into macro_core.scenario_rule_week (rule_number, week) values ($1, $2) "
            "on conflict do nothing",
            number,
            week,
        )

    async def add_rule(self, number: int, text: str, weeks: Sequence[str]) -> None:
        """A new standing rule. Numbers are never reused."""
        async with self.transaction() as connection:
            await connection.execute(
                "insert into macro_core.scenario_rule (number, text) values ($1, $2) "
                "on conflict (number) do update set text = excluded.text",
                number,
                text,
            )
            for week in weeks:
                await connection.execute(
                    "insert into macro_core.scenario_rule_week (rule_number, week) "
                    "values ($1, $2) on conflict do nothing",
                    number,
                    week,
                )

    async def rule_number_for_text(self, text: str) -> int | None:
        """The number a rule with exactly this text already carries, if one does.

        A retried grade pass sends the same `new_rules` again, and a rule is
        new once: the second pass cites the week on the rule the first one
        added instead of adding it a second time under the next number.
        """
        rows = await self._fetch(
            "select number from macro_core.scenario_rule "
            "where lower(btrim(text)) = lower(btrim($1)) order by number limit 1",
            text,
        )
        return None if not rows else int(rows[0]["number"])

    async def next_rule_number(self) -> int:
        rows = await self._fetch(
            "select coalesce(max(number), 0) + 1 as n from macro_core.scenario_rule"
        )
        return int(rows[0]["n"])

    async def write_scenario_set(self, proposal: ScenarioSetProposal) -> str:
        """A whole scenario set, in one transaction, as `proposed`.

        The head, three scenarios, every clause and every target commit together
        or not at all. The deferred cardinality trigger fires at `COMMIT` and
        refuses a set that does not carry exactly three scenarios summing to
        100, which is why a partial write cannot leave a half-built forecast
        standing.
        """
        async with self.transaction() as connection:
            row = await connection.fetchrow(
                "insert into macro_core.scenario_set "
                "(week, synthesis, probability_check, swing_variables, rules_waived, status, "
                " authored_at, amends, amend_reason) "
                "values ($1,$2,$3,$4::jsonb,$5,'proposed',$6,$7,$8) returning id",
                proposal.week,
                proposal.synthesis,
                proposal.probability_check,
                json.dumps(proposal.swing_variables, default=str),
                list(proposal.rules_waived),
                proposal.authored_at,
                proposal.amends,
                proposal.amend_reason,
            )
            set_id = str(row["id"])
            for scenario in proposal.scenarios:
                srow = await connection.fetchrow(
                    "insert into macro_core.scenario (set_id, ordinal, title, probability, "
                    "positioning) values ($1,$2,$3,$4,$5) returning id",
                    set_id,
                    scenario.ordinal,
                    scenario.title,
                    scenario.probability,
                    scenario.positioning,
                )
                scenario_id = str(srow["id"])
                for clause in scenario.clauses:
                    await connection.execute(
                        "insert into macro_core.trigger_clause "
                        "(scenario_id, ordinal, text, kind, expr, catalyst_id, required) "
                        "values ($1,$2,$3,$4,$5::jsonb,$6,$7)",
                        scenario_id,
                        clause.ordinal,
                        clause.text,
                        clause.kind,
                        None if clause.expr is None else json.dumps(clause.expr, default=str),
                        clause.catalyst_id,
                        clause.required,
                    )
                for target in scenario.targets:
                    await connection.execute(
                        "insert into macro_core.scenario_target "
                        "(scenario_id, series_id, label, kind, comparator, lo, hi, text) "
                        "values ($1, (select id from macro_core.series where key = $2), "
                        "$3,$4,$5,$6,$7,$8)",
                        scenario_id,
                        target.series_key,
                        target.label,
                        target.kind,
                        target.comparator,
                        target.lo,
                        target.hi,
                        target.text,
                    )
            return set_id

    async def write_phase_call(self, proposal: PhaseCallProposal) -> str:
        """One fresh read and its exactly four conditions, in one transaction.

        `prior_phase` and `direction` come from the call that was active when
        the read was written, filled in by `author classify` and never by the
        read. `classified_at` is stamped here and `revealed_at` is left null:
        it is set only when `author context` first shows this run last week's
        call, and the schema refuses one earlier than the other.
        """
        async with self.transaction() as connection:
            row = await connection.fetchrow(
                "insert into macro_core.phase_call "
                "(asset, week, phase, phase_name, prior_phase, direction, reason, evidence, "
                " falsifier, stage, stage_reason, classification, classified_at, change_note, "
                " status, authored_at) "
                "values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,'fresh',$12,$13,'proposed',$14) "
                "returning id",
                proposal.asset,
                proposal.week,
                proposal.phase,
                proposal.phase_name,
                proposal.prior_phase,
                proposal.direction,
                proposal.reason,
                list(proposal.evidence),
                proposal.falsifier,
                proposal.stage,
                proposal.stage_reason,
                proposal.classified_at or proposal.authored_at,
                proposal.change_note,
                proposal.authored_at,
            )
            call_id = str(row["id"])
            for condition in proposal.conditions:
                await connection.execute(
                    "insert into macro_core.phase_condition "
                    "(phase_call_id, ordinal, text, kind, expr) values ($1,$2,$3,$4,$5::jsonb)",
                    call_id,
                    condition.ordinal,
                    condition.text,
                    condition.kind,
                    None if condition.expr is None else json.dumps(condition.expr, default=str),
                )
            return call_id

    async def fresh_reads(self, week: str) -> list[dict[str, Any]]:
        """The week's fresh reads that are proposed or active, one row per read."""
        return [
            dict(one)
            for one in await self._fetch(
                "select id::text as id, asset, status, phase, prior_phase, classified_at, "
                "revealed_at, change_note from macro_core.phase_call "
                "where week = $1 and classification = 'fresh' "
                "and status in ('proposed', 'active') order by asset",
                week,
            )
        ]

    async def standing_assets(self, week: str) -> set[str]:
        """The assets whose active phase call is the week's, fresh or carried."""
        return {
            one["asset"]
            for one in await self._fetch(
                "select asset from macro_core.phase_call where week = $1 and status = 'active'",
                week,
            )
        }

    async def discard_unrevealed_reads(self, week: str) -> int:
        """Delete the week's proposed reads the run has not yet been shown the prior for.

        Only these may be replaced: a read written before the reveal and
        rewritten before the reveal is still a fresh read. The conditions go
        with the call, and their asserted states with them.
        """
        async with self.transaction() as connection:
            ids = [
                one["id"]
                for one in await connection.fetch(
                    "select id from macro_core.phase_call where week = $1 "
                    "and classification = 'fresh' and status = 'proposed' "
                    "and revealed_at is null",
                    week,
                )
            ]
            if not ids:
                return 0
            await connection.execute(
                "delete from macro_core.condition_state_asserted where condition_id in "
                "(select id from macro_core.phase_condition where phase_call_id = any($1::uuid[]))",
                ids,
            )
            await connection.execute(
                "delete from macro_core.phase_call where id = any($1::uuid[])", ids
            )
        return len(ids)

    async def reveal_reads(self, week: str, at: datetime) -> int:
        """Stamp the moment this run is first shown last week's call."""
        status = await self._execute(
            "update macro_core.phase_call set revealed_at = $2 where week = $1 "
            "and classification = 'fresh' and status = 'proposed' and revealed_at is null",
            week,
            at,
        )
        return int(status.split()[-1]) if status else 0

    async def set_change_note(self, call_id: str, note: str) -> None:
        await self._execute(
            "update macro_core.phase_call set change_note = $2 where id = $1::uuid "
            "and status = 'proposed'",
            call_id,
            note,
        )

    async def proposed_reads(self, week: str) -> list[PhaseCallProposal]:
        """The week's proposed fresh reads, rebuilt as the records `author classify` wrote.

        The manual conditions carry their asserted status back with them, so a
        preflight run after the reveal checks the same read that was written.
        """
        heads = await self._fetch(
            "select p.id, p.asset, p.week, p.phase, p.phase_name, p.reason, p.evidence, "
            "p.falsifier, p.stage, p.stage_reason, p.prior_phase, p.classified_at, "
            "p.change_note, p.authored_at, "
            "(select q.week from macro_core.phase_call q where q.asset = p.asset "
            "  and q.week < p.week and q.status in ('active', 'superseded') "
            "  and q.activated_at is not null "
            "  order by q.week desc limit 1) as prior_week "
            "from macro_core.phase_call p where p.week = $1 "
            "and p.classification = 'fresh' and p.status = 'proposed' order by p.asset",
            week,
        )
        conditions = await self._fetch(
            "select c.phase_call_id, c.ordinal, c.text, c.kind, c.expr, "
            "a.status as asserted_status, a.current_text as asserted_text "
            "from macro_core.phase_condition c "
            "join macro_core.phase_call p on p.id = c.phase_call_id "
            "left join macro_core.condition_state_asserted a on a.condition_id = c.id "
            "where p.week = $1 and p.classification = 'fresh' and p.status = 'proposed' "
            "order by c.ordinal",
            week,
        )
        return [
            PhaseCallProposal(
                asset=one["asset"],
                week=one["week"],
                phase=one["phase"],
                phase_name=one["phase_name"],
                reason=one["reason"],
                evidence=tuple(one["evidence"] or ()),
                falsifier=one["falsifier"],
                conditions=tuple(
                    Condition(
                        ordinal=c["ordinal"],
                        text=c["text"],
                        kind=c["kind"],
                        expr=_json_or_none(c["expr"]),
                        asserted_status=c["asserted_status"],
                        asserted_text=c["asserted_text"],
                    )
                    for c in conditions
                    if c["phase_call_id"] == one["id"]
                ),
                authored_at=one["authored_at"],
                stage=one["stage"],
                stage_reason=one["stage_reason"],
                prior_phase=one["prior_phase"],
                prior_week=one["prior_week"],
                change_note=one["change_note"],
                classified_at=one["classified_at"],
                call_id=str(one["id"]),
            )
            for one in heads
        ]

    async def write_strategy(self, proposal: StrategyProposal) -> tuple[str, dict[str, str]]:
        """One strategy, its four sector-focus rows and its holdings check, in one transaction.

        Returns the strategy id and `band -> sector_focus_id`, because a setup
        names the sector-focus row it sits under by key rather than by string.
        """
        async with self.transaction() as connection:
            row = await connection.fetchrow(
                "insert into macro_core.strategy "
                "(week, asset, stance, posture, exposure_lo, exposure_hi, "
                " exposure_operated_lo, exposure_operated_hi, status, authored_at, amends, "
                " amend_reason) values ($1,$2,$3,$4,$5,$6,$7,$8,'proposed',$9,$10,$11) "
                "returning id",
                proposal.week,
                proposal.asset,
                proposal.stance,
                proposal.posture,
                proposal.exposure_lo,
                proposal.exposure_hi,
                proposal.exposure_operated_lo,
                proposal.exposure_operated_hi,
                proposal.authored_at,
                proposal.amends,
                proposal.amend_reason,
            )
            strategy_id = str(row["id"])
            bands: dict[str, str] = {}
            for focus in proposal.sector_focus:
                frow = await connection.fetchrow(
                    "insert into macro_core.sector_focus (strategy_id, band, text) "
                    "values ($1,$2,$3) returning id",
                    strategy_id,
                    focus.band,
                    focus.text,
                )
                bands[focus.band] = str(frow["id"])
            for check in proposal.holdings_check:
                await connection.execute(
                    "insert into macro_core.holdings_check "
                    "(strategy_id, ticker, action, text, sector_focus_id) values ($1,$2,$3,$4,$5)",
                    strategy_id,
                    check.ticker,
                    check.action,
                    check.text,
                    bands.get(check.band or ""),
                )
            return strategy_id, bands

    async def write_setup(self, proposal: SetupProposal, *, sector_focus_id: str | None) -> str:
        """One setup. It always names the series its levels are measured against.

        A setup with a null `series_id` is refused by the worker: no
        `setup_state` row, no ledger row, and a `setup_series_missing` finding.
        Refusing it here instead means the operator never sees a proposal that
        could not have been scored.
        """
        rows = await self._fetch(
            """
            insert into macro_core.setup
              (week, asset, name, style, entry_lo, entry_hi, invalidation, invalidation_dir,
               target, rr_planned, gate_expr, gate_text, sector_focus_id, rationale,
               series_id, status, authored_at, amends, amend_reason)
            values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb,$12,$13,$14,
                    (select id from macro_core.series where key = $15),'proposed',$16,$17,$18)
            returning id
            """,
            proposal.week,
            proposal.asset,
            proposal.name,
            proposal.style,
            proposal.entry_lo,
            proposal.entry_hi,
            proposal.invalidation,
            proposal.invalidation_dir,
            proposal.target,
            proposal.rr_planned,
            None if proposal.gate_expr is None else json.dumps(proposal.gate_expr, default=str),
            proposal.gate_text,
            sector_focus_id,
            proposal.rationale,
            proposal.series_key,
            proposal.authored_at,
            proposal.amends,
            proposal.amend_reason,
        )
        return str(rows[0]["id"])

    async def phase_condition_ids(self, phase_call_id: str) -> dict[int, str]:
        """`ordinal -> condition id`, so a manual condition's assertion can find its row."""
        rows = await self._fetch(
            "select id, ordinal from macro_core.phase_condition where phase_call_id = $1",
            phase_call_id,
        )
        return {one["ordinal"]: str(one["id"]) for one in rows}

    async def trigger_clause_ids(self, set_id: str) -> dict[tuple[int, int], str]:
        """`(scenario ordinal, clause ordinal) -> clause id`, for the same reason."""
        rows = await self._fetch(
            "select t.id, s.ordinal as scenario_ordinal, t.ordinal "
            "from macro_core.trigger_clause t join macro_core.scenario s on s.id = t.scenario_id "
            "where s.set_id = $1",
            set_id,
        )
        return {(one["scenario_ordinal"], one["ordinal"]): str(one["id"]) for one in rows}

    async def assert_condition_state(
        self,
        condition_id: str,
        *,
        owner_kind: str,
        status: str,
        current_text: str | None,
        asserted_at: datetime,
        asserted_by: str = "authoring",
    ) -> None:
        """The as-written status of a `manual` condition.

        It lives in `macro_core` and not in `macro_mart` because `macro_mart` is
        rebuilt on every run, and a judgment a rebuild destroys is not a record
        of anything.
        """
        await self._execute(
            "insert into macro_core.condition_state_asserted "
            "(condition_id, owner_kind, status, current_text, asserted_at, asserted_by) "
            "values ($1,$2,$3,$4,$5,$6) on conflict (condition_id) do update set "
            "owner_kind = excluded.owner_kind, status = excluded.status, "
            "current_text = excluded.current_text, asserted_at = excluded.asserted_at, "
            "asserted_by = excluded.asserted_by",
            condition_id,
            owner_kind,
            status,
            current_text,
            asserted_at,
            asserted_by,
        )

    async def active_row_id(self, table: str, **subject: Any) -> str | None:
        """The id of the row currently `active` for a subject, or None."""
        if table not in JUDGMENT_TABLES:
            raise WriteRefused(f"{table!r} is not a judgment table")
        where = " and ".join(f"{name} = ${i + 1}" for i, name in enumerate(subject))
        clause = f" and {where}" if where else ""
        rows = await self._fetch(
            f"select id from macro_core.{table} where status = 'active'{clause}",
            *subject.values(),
        )
        return None if not rows else str(rows[0]["id"])

    async def activate(
        self,
        table: str,
        *,
        incoming_id: str,
        outgoing_id: str | None,
        activated_at: datetime,
    ) -> None:
        """Stand one judgment row up and the row it replaces down, in one transaction.

        **This is the operator's move, not the run's.** It is here because the
        two statements have to share a transaction and the order is fixed, not
        because the authoring run calls it: `docs/DECISIONS.md` puts the single
        human review gate exactly here, and a run that activated its own
        proposal would be a system with no review in it.

        The partial unique index that enforces "at most one active per subject"
        cannot be deferred, so the outgoing row is superseded first and the
        incoming one activated second. The other order leaves two active rows
        for the length of one statement and is rejected.
        """
        async with self.transaction() as connection:
            await activate_on(
                connection,
                table,
                incoming_id=incoming_id,
                outgoing_id=outgoing_id,
                activated_at=activated_at,
            )

    async def replace_unaccepted_week(self, week: str) -> int:
        """Stand down the week's earlier weekly proposal before a new one is written.

        `author propose` run twice would otherwise leave two proposed scenario
        sets, two strategies per book and every setup twice, and an Accept
        would try to activate both and trip the one-active-per-subject index
        half way. The earlier rows are superseded, never deleted, and never
        having been activated they are not history. Amendments are left alone:
        each names the row it replaces and is reviewed on its own.
        """
        total = 0
        async with self.transaction() as connection:
            for table in ("scenario_set", "strategy", "setup"):
                status = await connection.execute(
                    f"update macro_core.{table} set status = 'superseded' "
                    "where week = $1 and status = 'proposed' and amends is null",
                    week,
                )
                total += int(status.split()[-1]) if status else 0
        return total


async def activate_on(
    connection: Any,
    table: str,
    *,
    incoming_id: str,
    outgoing_id: str | None,
    activated_at: datetime,
) -> None:
    """`Store.activate`'s two statements on a connection the caller holds a transaction on.

    The runner applies a whole accepted proposal inside one transaction, so a
    refusal half way leaves last week's rows standing rather than half of each.
    """
    if table not in JUDGMENT_TABLES:
        raise WriteRefused(f"{table!r} is not a judgment table this package activates")
    if outgoing_id is not None:
        await connection.execute(
            f"update macro_core.{table} set status = 'superseded', superseded_by = $2 "
            "where id = $1",
            outgoing_id,
            incoming_id,
        )
    await connection.execute(
        f"update macro_core.{table} set status = 'active', activated_at = $2 where id = $1",
        incoming_id,
        activated_at,
    )


def _encode_json(value: Any) -> str:
    """Text is already JSON (every write binds `json.dumps(...)`); anything else is dumped."""
    return value if isinstance(value, str) else json.dumps(value, default=str)


async def _init_connection(connection: asyncpg.Connection) -> None:
    """The one place `json` and `jsonb` are decoded, for every connection in the pool."""
    for name in ("json", "jsonb"):
        await connection.set_type_codec(
            name, schema="pg_catalog", encoder=_encode_json, decoder=json.loads, format="text"
        )


def _json_or_none(value: Any) -> dict[str, Any] | None:
    found = _json(value)
    return found or None


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


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return []
        return decoded if isinstance(decoded, list) else []
    return []
