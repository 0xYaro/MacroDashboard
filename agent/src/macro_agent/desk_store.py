"""Every statement `macro-agent desk` sends.

A subclass of `Store` for its four gateways (every asyncpg fault becomes a
`WriteRefused` with a sentence) and its connection handling, and nothing else
from it: the desk runs as `macro_desk`, which can read and write only the feed
tables, so a method here that touched anything else would fail on privilege,
which is the point of the role.

Conventions are `Store`'s: every name schema-qualified, jsonb bound as text with
an explicit cast, every write an upsert on a natural key or an update of one
row by its key.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from .store import Store, WriteRefused

#: The key levels `desk context` carries, in the order it prints them.
LEVEL_KEYS: tuple[str, ...] = (
    "spx_cash",
    "nasdaq_cash",
    "vix",
    "move",
    "hy_oas_bps",
    "ig_oas_bps",
    "breadth_spx_above_200dma",
    "ust_10y",
    "dxy",
    "brent",
    "gold",
    "btc",
    "eth",
)


def _iso(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _num(value: Any) -> str | None:
    return None if value is None else str(value)


def _hundredths(value: Any) -> str | None:
    if value is None:
        return None
    return str(Decimal(str(value)).quantize(Decimal("0.01")))


# ---------------------------------------------------------------------------
# Account identifiers: never past this module
# ---------------------------------------------------------------------------
#
# `desk positions` is read by a model and may be posted to Telegram. Accounting's
# own words (a venue's `reason`, an instrument's name) can name an account, as
# in "Tiger account 50400376 holds cash only", so every string read from
# Accounting passes `redact_accounts` here, before it is archived or printed.

#: An account number (50400376, U1234567, 5040-0376) or a wallet address.
_ACCOUNT_ID = r"(?:0x[0-9a-fA-F]{6,}|[A-Za-z]{0,4}\d(?:[\d-]{4,})\d)"
_ACCOUNT_PHRASE = re.compile(
    r"\b(?:(?P<lead>[A-Za-z][\w.&]*)\s+)?(?:sub[- ]?)?(?:account|acct|a/c)"
    r"(?:\s+(?:no\.?|number|id|#))?\s*[:#]?\s*" + _ACCOUNT_ID + r"\b",
    re.IGNORECASE,
)
_ADDRESS = re.compile(r"\b0x[0-9a-fA-F]{6,}\b")
_LONG_NUMBER = re.compile(r"\b[A-Za-z]{0,4}\d{6,}\b")
_DETERMINERS = frozenset({"the", "a", "an", "this", "that", "each", "one", "its", "their"})
_FUNCTION_WORDS = frozenset({
    "for", "in", "on", "of", "from", "to", "by", "with", "at", "and", "or", "but", "via",
    "is", "was", "under", "into", "per", "as",
})
#: Accounting's venue states, freshest first. A state not listed ranks as the stalest.
VENUE_STATES: tuple[str, ...] = ("fresh", "stale", "degraded", "error", "failed", "down")


def _account_phrase(match: re.Match[str]) -> str:
    lead = match.group("lead")
    if lead is not None and lead.lower() in _DETERMINERS:
        return f"{lead} account"
    if lead is not None and lead.lower() in _FUNCTION_WORDS:
        return f"{lead} an account"
    said = "an account" if lead is None else (
        f"{'an' if lead[0].lower() in 'aeiou' else 'a'} {lead} account"
    )
    before = match.string[: match.start()].rstrip()
    if before == "" or before.endswith((".", "!", "?")):
        said = said[0].upper() + said[1:]
    return said


def redact_accounts(text: str) -> str:
    """`text` with every account number and wallet address taken out.

    "Tiger account 50400376 holds cash only" reads "A Tiger account holds cash
    only". An address or a long number outside an account phrase is withheld
    too: a reason has no business carrying either.
    """
    text = _ACCOUNT_PHRASE.sub(_account_phrase, text)
    text = _ADDRESS.sub("[address withheld]", text)
    return _LONG_NUMBER.sub("[number withheld]", text)


def _redacted(value: Any) -> Any:
    if isinstance(value, str):
        return redact_accounts(value)
    if isinstance(value, (list, tuple)):
        return [_redacted(one) for one in value]
    return value


def aggregate_venues(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Accounting's per-account venue status, as one row per venue.

    A venue is as fresh as its stalest account: `state` is the worst state,
    `venue_as_of` and `received_at` the oldest. `reasons` are the distinct
    reasons, each redacted. The count of accounts is kept; which accounts is not.
    """
    by_venue: dict[str, list[dict[str, Any]]] = {}
    for one in rows:
        by_venue.setdefault(str(one.get("venue") or "unknown"), []).append(one)

    def rank(state: Any) -> int:
        return VENUE_STATES.index(state) if state in VENUE_STATES else len(VENUE_STATES)

    out = []
    for venue in sorted(by_venue):
        group = by_venue[venue]
        reasons: list[str] = []
        for one in group:
            reason = one.get("reason")
            if reason:
                said = redact_accounts(str(reason))
                if said not in reasons:
                    reasons.append(said)
        as_of = [one["venue_as_of"] for one in group if one.get("venue_as_of")]
        received = [one["received_at"] for one in group if one.get("received_at")]
        out.append({
            "venue": redact_accounts(venue),
            "state": redact_accounts(str(max((one.get("state") for one in group), key=rank))),
            "accounts": len(group),
            "position_count": sum(int(one.get("position_count") or 0) for one in group),
            "venue_as_of": _iso(min(as_of)) if as_of else None,
            "received_at": _iso(min(received)) if received else None,
            "reasons": reasons,
        })
    return out


#: Accounting's position fields that are instrument names, kept as Accounting wrote them.
_INSTRUMENT_FIELDS = frozenset({"symbol", "underlying_symbol"})


class DeskStore(Store):
    """The desk's reads and writes. Connect with `DeskStore.connect(url)`."""

    async def whoami(self) -> str:
        rows = await self._fetch("select current_user as who")
        return str(rows[0]["who"])

    # ------------------------------------------------------------------
    # desk context: the board, read from macro_mart only
    # ------------------------------------------------------------------

    async def phases(self) -> list[dict[str, Any]]:
        rows = await self._fetch(
            """
            select d.asset, d.phase, d.phase_name, d.stage, d.week, d.classification,
                   d.activated_at, s.met, s.of, s.failed, s.unevaluable, s.read_stale,
                   s.evaluated_at
            from macro_mart.phase_call_detail d
            left join macro_mart.phase_score s on s.asset = d.asset
            order by d.asset
            """
        )
        return [
            {
                "asset": one["asset"],
                "phase": one["phase"],
                "phase_name": one["phase_name"],
                "stage": one["stage"],
                "week": one["week"],
                "classification": one["classification"],
                "conditions_met": one["met"],
                "conditions_of": one["of"],
                "conditions_failed": one["failed"],
                "read_stale": one["read_stale"],
                "evaluated_at": _iso(one["evaluated_at"]),
            }
            for one in rows
        ]

    async def scenarios(self) -> dict[str, Any]:
        head = await self._fetch(
            "select set_id, week, activated_at from macro_mart.scenario_set_state "
            "order by activated_at desc nulls last limit 1"
        )
        if not head:
            return {"week": None, "items": []}
        rows = await self._fetch(
            "select ordinal, title, probability, premise_moved, clauses_fired, clauses_total, "
            "targets_holding, targets_total from macro_mart.scenario_state "
            "where set_id = $1 order by ordinal",
            head[0]["set_id"],
        )
        return {
            "week": head[0]["week"],
            "activated_at": _iso(head[0]["activated_at"]),
            "items": [
                {
                    "ordinal": one["ordinal"],
                    "title": one["title"],
                    "probability": one["probability"],
                    "premise_moved": one["premise_moved"],
                    "clauses_fired": one["clauses_fired"],
                    "clauses_total": one["clauses_total"],
                    "targets_holding": one["targets_holding"],
                    "targets_total": one["targets_total"],
                }
                for one in rows
            ],
        }

    async def levels(self, keys: Sequence[str] = LEVEL_KEYS) -> list[dict[str, Any]]:
        rows = await self._fetch(
            "select key, label, value, display, unit, asof, observed_at, freshness, "
            "delta_native, delta_native_label from macro_mart.snapshot_row "
            "where key = any($1::text[])",
            list(keys),
        )
        by_key = {one["key"]: one for one in rows}
        return [
            {
                "key": key,
                "label": one["label"],
                "value": _num(one["value"]),
                "display": one["display"],
                "unit": one["unit"],
                "asof": _iso(one["asof"]),
                "freshness": one["freshness"],
                # The mart stores the delta as the worker computed it, sometimes
                # with a float's tail; the agent reads it to the hundredth.
                "change": _hundredths(one["delta_native"]),
                "change_label": one["delta_native_label"],
            }
            for key in keys
            if (one := by_key.get(key)) is not None
        ]

    async def gauge_values(self, keys: Sequence[str]) -> dict[str, dict[str, Any]]:
        """`key -> {value, display, unit, asof}` from the snapshot, for a tail risk's gauges."""
        if not keys:
            return {}
        rows = await self._fetch(
            "select key, value, display, unit, asof from macro_mart.snapshot_row "
            "where key = any($1::text[])",
            list(keys),
        )
        return {
            one["key"]: {
                "value": _num(one["value"]),
                "display": one["display"],
                "unit": one["unit"],
                "asof": _iso(one["asof"]),
            }
            for one in rows
        }

    async def drift(self) -> dict[str, Any] | None:
        rows = await self._fetch(
            "select week, score, material, refresh_suggested, reason, items, evaluated_at "
            "from macro_mart.drift order by evaluated_at desc limit 1"
        )
        if not rows:
            return None
        one = rows[0]
        items = json.loads(one["items"]) if isinstance(one["items"], str) else one["items"]
        return {
            "week": one["week"],
            "score": one["score"],
            "material": one["material"],
            "refresh_suggested": one["refresh_suggested"],
            "reason": one["reason"],
            "evaluated_at": _iso(one["evaluated_at"]),
            "top": [
                str(item.get("sentence"))
                for item in sorted(
                    items or [],
                    key=lambda x: (-bool(x.get("material")), -int(x.get("weight") or 0)),
                )[:6]
                if item.get("sentence")
            ],
        }

    async def feed_freshness(self) -> list[dict[str, Any]]:
        rows = await self._fetch(
            "select feed, origin, last_run_at, last_status, last_ok_at, last_ok_origin, rows "
            "from macro_mart.feed_freshness order by feed"
        )
        return [{key: _iso(value) for key, value in dict(one).items()} for one in rows]

    # ------------------------------------------------------------------
    # Runs, archive, findings
    # ------------------------------------------------------------------

    async def start_desk_run(self, feed: str, origin: str, started_at: datetime) -> int:
        rows = await self._fetch(
            "insert into macro_quality.ingest_run (source, run_kind, started_at, status, feed, "
            "origin) values ($1, 'desk', $2, 'running', $3, $4) returning id",
            f"desk:{feed}",
            started_at,
            feed,
            origin,
        )
        return int(rows[0]["id"])

    async def archive(
        self, feed: str, kind: str, payload: Any, *, at: datetime, shadow: bool
    ) -> int:
        rows = await self._fetch(
            "insert into macro_raw.batch (source, kind, fetched_at, payload, shadow) "
            "values ($1, $2, $3, $4::jsonb, $5) returning id",
            f"desk:{feed}",
            kind,
            at,
            json.dumps(payload, default=str),
            shadow,
        )
        return int(rows[0]["id"])

    async def batch(self, batch_id: int) -> dict[str, Any] | None:
        rows = await self._fetch(
            "select id, source, kind, fetched_at, payload, shadow from macro_raw.batch "
            "where id = $1",
            batch_id,
        )
        if not rows:
            return None
        one = dict(rows[0])
        if isinstance(one["payload"], str):
            one["payload"] = json.loads(one["payload"])
        return one

    # ------------------------------------------------------------------
    # Catalysts
    # ------------------------------------------------------------------

    async def catalysts(self) -> list[dict[str, Any]]:
        rows = await self._fetch(
            """
            select id, external_key, name, starts_at, ends_at, date_precision, date_estimated,
                   date_text, category, family, source_url, affected, status, first_seen,
                   last_seen, retired_at, retired_reason, feed_source, last_run_id, updated_at
            from macro_core.catalyst
            order by starts_at nulls last, name
            """
        )
        return [dict(one) | {"id": str(one["id"])} for one in rows]

    async def catalyst_aliases(self) -> dict[str, dict[str, Any]]:
        rows = await self._fetch(
            "select external_key, catalyst_id, name, renamed_at from macro_core.catalyst_alias"
        )
        return {
            one["external_key"]: {
                "catalyst_id": str(one["catalyst_id"]),
                "name": one["name"],
                "renamed_at": one["renamed_at"],
            }
            for one in rows
        }

    @staticmethod
    async def upsert_catalyst(connection: Any, row: dict[str, Any]) -> str:
        """Insert a catalyst or update the one with this key. Returns its id.

        `row` carries the columns below. A retired row with the same key is
        revived: the desk re-listing an event is the same act as the monitor
        re-listing it.
        """
        found = await connection.fetchrow(
            """
            insert into macro_core.catalyst
              (external_key, name, starts_at, ends_at, date_precision, date_text,
               date_estimated, family, category, source_url, affected, status, first_seen,
               last_seen, feed_source, last_run_id, updated_at)
            values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,'tracked',$12,$12,$13,$14,$15)
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
              status = 'tracked',
              last_seen = excluded.last_seen,
              retired_at = null,
              retired_reason = null,
              feed_source = excluded.feed_source,
              last_run_id = excluded.last_run_id,
              updated_at = excluded.updated_at
            returning id
            """,
            row["external_key"],
            row["name"],
            row["starts_at"],
            row["ends_at"],
            row["date_precision"],
            row["date_text"],
            row["date_estimated"],
            row["family"],
            row["category"],
            row["source_url"],
            list(row["affected"]),
            row["seen_on"],
            row["feed_source"],
            row["run_id"],
            row["at"],
        )
        return str(found["id"])

    @staticmethod
    async def retire_catalyst(
        connection: Any, catalyst_id: str, *, reason: str, on: date, run_id: int, at: datetime,
        feed_source: str,
    ) -> None:
        await connection.execute(
            "update macro_core.catalyst set status = 'retired', retired_at = $2, "
            "retired_reason = $3, last_run_id = $4, updated_at = $5, feed_source = $6 "
            "where id = $1::uuid and status <> 'retired'",
            catalyst_id,
            on,
            reason,
            run_id,
            at,
            feed_source,
        )

    @staticmethod
    async def rename_catalyst(
        connection: Any,
        catalyst_id: str,
        *,
        old_key: str,
        old_name: str,
        new_name: str,
        new_key: str,
        family: str | None,
        category: str,
        reason: str | None,
        run_id: int,
        at: datetime,
        feed_source: str,
    ) -> None:
        await connection.execute(
            "insert into macro_core.catalyst_alias (external_key, catalyst_id, name, renamed_at, "
            "reason, run_id) values ($1, $2::uuid, $3, $4, $5, $6) "
            "on conflict (external_key) do nothing",
            old_key,
            catalyst_id,
            old_name,
            at,
            reason,
            run_id,
        )
        await connection.execute(
            "update macro_core.catalyst set name = $2, external_key = $3, family = $4, "
            "category = $5, last_run_id = $6, updated_at = $7, feed_source = $8 "
            "where id = $1::uuid",
            catalyst_id,
            new_name,
            new_key,
            family,
            category,
            run_id,
            at,
            feed_source,
        )

    @staticmethod
    async def confirm_listed(connection: Any, *, on: date) -> int:
        """Every tracked catalyst is still on the desk's calendar as of `on`.

        The archive (`worker/src/macro/archive.py`) keeps an occurrence only if
        the catalyst was still listed within three days of its date. The vault
        re-listed every row every morning; the desk submits changes, so a live
        desk run that read the calendar is what confirms the rest of it.
        """
        # Through a definer function that moves last_seen and nothing else: the desk
        # may update only rows it writes as the desk (20260925000200), and these
        # include the vault's.
        found = await connection.fetchval(
            "select macro_core.desk_confirm_catalysts_listed($1)", on
        )
        return int(found or 0)

    # ------------------------------------------------------------------
    # Tail risks
    # ------------------------------------------------------------------

    async def tail_risks(self) -> list[dict[str, Any]]:
        rows = await self._fetch(
            """
            select key, risk_id, name, severity, summary, priced_in, assets, source_url,
                   first_seen, last_seen, retired_on, retired_reason, feed_source, last_run_id
            from macro_core.tail_risk
            order by retired_on nulls first,
                     case severity when 'acute' then 0 when 'elevated' then 1 else 2 end, name
            """
        )
        return [dict(one) for one in rows]

    async def tail_risk_checks(self, *, per_risk: int = 5) -> dict[str, list[dict[str, Any]]]:
        rows = await self._fetch(
            """
            select risk_id, checked_at, kind, severity, retired from (
              select c.*, row_number() over (partition by risk_id order by checked_at desc) n
              from macro_core.tail_risk_check c
            ) x where n <= $1 order by risk_id, checked_at desc
            """,
            per_risk,
        )
        out: dict[str, list[dict[str, Any]]] = {}
        for one in rows:
            out.setdefault(one["risk_id"], []).append(
                {
                    "checked_at": _iso(one["checked_at"]),
                    "kind": one["kind"],
                    "severity": one["severity"],
                    "retired": one["retired"],
                }
            )
        return out

    @staticmethod
    async def upsert_tail_risk(connection: Any, row: dict[str, Any]) -> None:
        await connection.execute(
            """
            insert into macro_core.tail_risk
              (key, risk_id, name, severity, summary, priced_in, assets, source_url,
               first_seen, last_seen, feed_source, last_run_id)
            values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$9,$10,$11)
            on conflict (key) do update set
              name = excluded.name,
              severity = excluded.severity,
              summary = excluded.summary,
              priced_in = excluded.priced_in,
              assets = excluded.assets,
              source_url = excluded.source_url,
              last_seen = excluded.last_seen,
              retired_on = null,
              retired_reason = null,
              feed_source = excluded.feed_source,
              last_run_id = excluded.last_run_id
            """,
            row["key"],
            row["risk_id"],
            row["name"],
            row["severity"],
            row["summary"],
            row["priced_in"],
            list(row["assets"]),
            row["source_url"],
            row["seen_on"],
            row["feed_source"],
            row["run_id"],
        )

    @staticmethod
    async def retire_tail_risk(
        connection: Any, risk_id: str, *, reason: str, on: date, run_id: int, feed_source: str
    ) -> None:
        await connection.execute(
            "update macro_core.tail_risk set retired_on = $2, retired_reason = $3, "
            "last_run_id = $4, feed_source = $5 where risk_id = $1 and retired_on is null",
            risk_id,
            on,
            reason,
            run_id,
            feed_source,
        )

    @staticmethod
    async def write_tail_risk_check(connection: Any, row: dict[str, Any]) -> None:
        """Append one check. The history is never rewritten, and macro_desk cannot update it."""
        await connection.execute(
            """
            insert into macro_core.tail_risk_check
              (risk_id, checked_at, kind, severity, summary, priced_in, gauges, source_url,
               retired, run_id)
            values ($1,$2,$3,$4,$5,$6,$7::jsonb,$8,$9,$10)
            on conflict (risk_id, checked_at) do nothing
            """,
            row["risk_id"],
            row["checked_at"],
            row["kind"],
            row["severity"],
            row["summary"],
            row["priced_in"],
            json.dumps(row["gauges"], default=str),
            row["source_url"],
            row["retired"],
            row["run_id"],
        )

    # ------------------------------------------------------------------
    # Positions and the holdings watch
    # ------------------------------------------------------------------

    async def accounting_positions(self) -> list[dict[str, Any]]:
        """Accounting's open non-cash positions, through the definer function.

        Every string but the instrument's own symbols is redacted of account
        identifiers (`redact_accounts`).
        """
        rows = await self._fetch("select * from macro_core.accounting_open_positions()")
        return [
            {
                key: value if key in _INSTRUMENT_FIELDS else _redacted(value)
                for key, value in dict(one).items()
            }
            for one in rows
        ]

    async def accounting_venues(self) -> list[dict[str, Any]]:
        """One row per venue, never per account, with every identifier redacted."""
        rows = await self._fetch("select * from macro_core.accounting_venue_status()")
        return aggregate_venues([dict(one) for one in rows])

    async def manual_positions(self) -> list[dict[str, Any]]:
        rows = await self._fetch(
            "select id, symbol, name, venue, asset_class, quantity, note "
            "from macro_core.manual_position where active order by symbol"
        )
        return [dict(one) | {"id": str(one["id"])} for one in rows]

    async def watch_tickers(self) -> list[dict[str, Any]]:
        rows = await self._fetch(
            "select id, symbol, name, asset_class, note from macro_core.watch_ticker "
            "where active order by symbol"
        )
        return [dict(one) | {"id": str(one["id"])} for one in rows]

    async def holding_events(self, symbols: Sequence[str]) -> dict[tuple[str, str], str]:
        rows = await self._fetch(
            "select symbol, name_key, id from macro_core.holding_event where symbol = any($1)",
            list(symbols),
        )
        return {(one["symbol"], one["name_key"]): str(one["id"]) for one in rows}

    async def holding_watch_keys(self, as_of: date) -> set[tuple[str, str]]:
        """The (symbol, source) pairs that already have a holdings-watch row for `as_of`."""
        rows = await self._fetch(
            "select symbol, source from macro_core.holding_watch where as_of = $1", as_of
        )
        return {(one["symbol"], one["source"]) for one in rows}

    @staticmethod
    async def upsert_holding_watch(
        connection: Any, rows: Sequence[dict[str, Any]], *, keep_existing: bool = False
    ) -> None:
        """Write holdings-watch rows on (as_of, symbol, source).

        `keep_existing` inserts only: a name that already has a row for the day
        keeps it untouched, which is how a quiet row never overwrites a driver
        an earlier submission recorded.
        """
        if not rows:
            return
        conflict = (
            "on conflict (as_of, symbol, source) do nothing"
            if keep_existing
            else "on conflict (as_of, symbol, source) do update set"
        )
        update = "" if keep_existing else """
              name = excluded.name,
              asset_class = excluded.asset_class,
              market_symbol = excluded.market_symbol,
              price = excluded.price,
              price_at = excluded.price_at,
              currency = excluded.currency,
              change_24h_pct = excluded.change_24h_pct,
              change_basis = excluded.change_basis,
              change_session = excluded.change_session,
              price_source = excluded.price_source,
              unavailable_reason = excluded.unavailable_reason,
              underlying_only = excluded.underlying_only,
              quantity = excluded.quantity,
              exposure_usd = excluded.exposure_usd,
              material = excluded.material,
              driver_summary = excluded.driver_summary,
              source_url = excluded.source_url,
              no_clear_reason = excluded.no_clear_reason,
              run_id = excluded.run_id,
              recorded_at = excluded.recorded_at
            """
        await connection.executemany(
            """
            insert into macro_core.holding_watch
              (as_of, symbol, source, name, asset_class, market_symbol, price, price_at,
               currency, change_24h_pct, change_basis, change_session, price_source,
               unavailable_reason, underlying_only, quantity, exposure_usd, material,
               driver_summary, source_url, run_id, recorded_at, no_clear_reason)
            values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,
                    $21,$22,$23)
            """
            + conflict
            + update,
            [
                (
                    one["as_of"],
                    one["symbol"],
                    one["source"],
                    one["name"],
                    one["asset_class"],
                    one["market_symbol"],
                    one["price"],
                    one["price_at"],
                    one["currency"],
                    one["change_24h_pct"],
                    one["change_basis"],
                    one["change_session"],
                    one["price_source"],
                    one["unavailable_reason"],
                    one["underlying_only"],
                    one["quantity"],
                    one["exposure_usd"],
                    one["material"],
                    one["driver_summary"],
                    one["source_url"],
                    one["run_id"],
                    one["recorded_at"],
                    bool(one.get("no_clear_reason")),
                )
                for one in rows
            ],
        )

    @staticmethod
    async def upsert_holding_event(connection: Any, row: dict[str, Any]) -> None:
        await connection.execute(
            """
            insert into macro_core.holding_event
              (symbol, kind, name, name_key, starts_at, ends_at, date_precision,
               date_estimated, date_text, source_url, catalyst_id, run_id, first_seen,
               last_seen)
            values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::uuid,$12,$13,$13)
            on conflict (symbol, name_key) do update set
              kind = excluded.kind,
              name = excluded.name,
              starts_at = excluded.starts_at,
              ends_at = excluded.ends_at,
              date_precision = excluded.date_precision,
              date_estimated = excluded.date_estimated,
              date_text = excluded.date_text,
              source_url = excluded.source_url,
              catalyst_id = coalesce(excluded.catalyst_id, holding_event.catalyst_id),
              run_id = excluded.run_id,
              last_seen = excluded.last_seen
            """,
            row["symbol"],
            row["kind"],
            row["name"],
            row["name_key"],
            row["starts_at"],
            row["ends_at"],
            row["date_precision"],
            row["date_estimated"],
            row["date_text"],
            row["source_url"],
            row["catalyst_id"],
            row["run_id"],
            row["seen_on"],
        )

    @staticmethod
    async def link_catalyst_dates(connection: Any, catalyst_id: str, row: dict[str, Any]) -> None:
        """A firmer date for an earnings catalyst already on the calendar."""
        await connection.execute(
            "update macro_core.catalyst set starts_at = $2, ends_at = $3, date_precision = $4, "
            "date_text = $5, date_estimated = $6, source_url = $7, last_seen = $8, "
            "last_run_id = $9, updated_at = $10, feed_source = $11 where id = $1::uuid",
            catalyst_id,
            row["starts_at"],
            row["ends_at"],
            row["date_precision"],
            row["date_text"],
            row["date_estimated"],
            row["source_url"],
            row["seen_on"],
            row["run_id"],
            row["at"],
            row["feed_source"],
        )


__all__ = ["LEVEL_KEYS", "DeskStore", "WriteRefused"]
