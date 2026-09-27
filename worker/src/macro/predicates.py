"""The four named path-dependent predicates.

`config/predicates.md` owns these conventions and is binding on this file in the
same way `docs/CONTRACT.md` is binding on the schema. Nothing here improves on
one. Where that file is silent, this module takes the conservative reading and
says so in the docstring.

The five rules every predicate below obeys, restated so a reader of this file
alone still has them:

1. **Closes decide, highs and lows only witness.** A predicate may read
   `session_high` and `session_low` to answer "was this level reached", and it
   must read the close to answer "was this level accepted". No predicate
   resolves true on an intraday print alone.
2. **A missing observation is `unevaluable`, never `false`.** If any session
   inside the window lacks an observation for a series the predicate names, the
   answer is `unevaluable` with the series key and the missing `asof` in the
   evidence.
3. **A window is counted in sessions of the named series, not in calendar
   days.** Five sessions of `spx_cash` is five US trading days; five sessions of
   `btc` is five calendar days, because `btc` advances on `calendar_day`.
4. **A window never reaches back past the moment the judgment was authored.**
   It is clipped at the owning row's `activated_at` session. Without the clip a
   setup proposed on Monday grades as entered on the prior Thursday's tape.
5. **Every argument is in the series' declared unit.** `hy_oas_bps` takes `292`,
   never `2.92`.

And one shape rule: every predicate returns its evidence, naming the sessions
examined, the values read, and the one session the answer turned on.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from .expr import (
    FAILED,
    MET,
    NOT_YET,
    UNEVALUABLE,
    Context,
    ExpressionRefused,
    Outcome,
    evaluate,
)
from .observations import Reading

Direction = Literal["above", "below"]
Flow = Literal["inflow", "outflow"]


# ---------------------------------------------------------------------------
# Argument reading
# ---------------------------------------------------------------------------


def _series_key(args: dict[str, Any], predicate: str, ctx: Context) -> str:
    key = args.get("series")
    if not isinstance(key, str) or not key:
        raise ExpressionRefused(f"{predicate} needs a `series`")
    series = ctx.registry.get(key)
    if series is None:
        raise ExpressionRefused(
            f"{predicate} names series {key!r}, which config/series.yaml does not declare"
        )
    if series.value_kind == "text":
        raise ExpressionRefused(
            f"{predicate} names {key!r}, a prose row, which may only appear in a manual condition"
        )
    return key


def _number(args: dict[str, Any], name: str, predicate: str) -> Decimal:
    if name not in args:
        raise ExpressionRefused(f"{predicate} needs a `{name}`")
    try:
        return Decimal(str(args[name]))
    except ArithmeticError as exc:
        raise ExpressionRefused(f"{predicate}'s `{name}` is not a number: {args[name]!r}") from exc


def _window_size(args: dict[str, Any], predicate: str) -> int:
    if "within_sessions" not in args:
        raise ExpressionRefused(f"{predicate} needs a `within_sessions`")
    try:
        count = int(args["within_sessions"])
    except (TypeError, ValueError) as exc:
        raise ExpressionRefused(
            f"{predicate}'s `within_sessions` is not an integer: {args['within_sessions']!r}"
        ) from exc
    if count < 1:
        raise ExpressionRefused(f"{predicate}'s `within_sessions` must be at least 1")
    return count


def _direction(args: dict[str, Any], predicate: str, *, default: Direction | None) -> Direction:
    raw = args.get("direction", default)
    if raw is None:
        raise ExpressionRefused(f"{predicate} needs a `direction` of above or below")
    if raw == "above":
        return "above"
    if raw == "below":
        return "below"
    raise ExpressionRefused(f"{predicate}'s `direction` must be above or below, not {raw!r}")


def _window(
    ctx: Context, series_key: str, count: int
) -> tuple[tuple[date, ...], list[Reading], Outcome | None]:
    """The clipped window, its readings, and an `unevaluable` if one is missing.

    The window is the last `count` sessions of the series ending at the
    evaluation date, clipped at `ctx.not_before`. A session inside it with no
    reading, or with a null value, makes the whole predicate unevaluable: the
    answer depends on a sequence and a sequence with a hole in it is not one.
    """
    sessions = ctx.history.window(
        series_key, ending=ctx.asof, count=count, not_before=ctx.not_before
    )
    if not sessions:
        return (), [], None
    readings: list[Reading] = []
    for session in sessions:
        reading = ctx.history.at(series_key, session)
        if reading is None or reading.value is None:
            return (
                sessions,
                readings,
                Outcome(
                    status=UNEVALUABLE,
                    evidence={
                        "why": f"{series_key} has no value for {session.isoformat()}",
                        "series": series_key,
                        "missing_asof": session.isoformat(),
                        "gap": ctx.history.has_gap(series_key, session),
                        "window": [one.isoformat() for one in sessions],
                    },
                ),
            )
        readings.append(reading)
    return sessions, readings, None


def _empty_window_evidence(ctx: Context, series_key: str, count: int) -> dict[str, Any]:
    return {
        "series": series_key,
        "within_sessions": count,
        "asof": ctx.asof.isoformat(),
        "not_before": None if ctx.not_before is None else ctx.not_before.isoformat(),
        "why": (
            f"{series_key} carries no session inside the window; the judgment's activation clip "
            "leaves nothing to read"
        ),
    }


# ---------------------------------------------------------------------------
# failed_retest
# ---------------------------------------------------------------------------


def failed_retest(args: dict[str, Any], ctx: Context) -> Outcome:
    """Price came back to a level, touched it, and was refused there.

    With `direction: above`, which is the default:

    1. Some session in the window has `session_high >= level`. That is the touch.
    2. No session in the window at or after that touch has `close >= level`.
       That is the refusal.

    With `direction: below`, read `session_low <= level` and `close <= level`.

    No touch in the window is `not yet`: a level price never approached has not
    been refused. A touch followed by a close beyond the level is `failed`: the
    level was accepted rather than refused and the condition is not going to
    become true by waiting. That distinction is the point of the predicate. A
    tracker reporting both as "not yet" hides the difference between a setup
    still waiting and a setup whose premise is gone.

    Comparisons against `level` are inclusive on the touch and inclusive on the
    acceptance, so a close exactly at the level is acceptance, not refusal.

    Whether the level was previously traded through is not part of the test. The
    authoring agent names a level because of its history, and re-deriving that
    history here would score the same judgment twice.

    Unevaluable when any session in the window lacks an observation, or lacks
    the session high (or low) the touch is read from.
    """
    predicate = "failed_retest"
    key = _series_key(args, predicate, ctx)
    level = _number(args, "level", predicate)
    count = _window_size(args, predicate)
    direction = _direction(args, predicate, default="above")

    sessions, readings, refusal = _window(ctx, key, count)
    if refusal is not None:
        return refusal
    if not sessions:
        return Outcome(status=UNEVALUABLE, evidence=_empty_window_evidence(ctx, key, count))

    extremes: list[Decimal | None] = [
        reading.session_high if direction == "above" else reading.session_low
        for reading in readings
    ]
    for session, extreme in zip(sessions, extremes, strict=True):
        if extreme is None:
            return Outcome(
                status=UNEVALUABLE,
                evidence={
                    "series": key,
                    "missing_asof": session.isoformat(),
                    "why": (
                        f"{key} carries no session "
                        f"{'high' if direction == 'above' else 'low'} for "
                        f"{session.isoformat()}, and the touch is read from it"
                    ),
                    "window": [one.isoformat() for one in sessions],
                },
            )

    touched_at: int | None = None
    for index, extreme in enumerate(extremes):
        assert extreme is not None
        if (direction == "above" and extreme >= level) or (
            direction == "below" and extreme <= level
        ):
            touched_at = index
            break

    evidence: dict[str, Any] = {
        "predicate": predicate,
        "series": key,
        "level": str(level),
        "direction": direction,
        "within_sessions": count,
        "not_before": None if ctx.not_before is None else ctx.not_before.isoformat(),
        "sessions": [
            {
                "asof": session.isoformat(),
                "close": str(reading.value),
                "high": _text(reading.session_high),
                "low": _text(reading.session_low),
            }
            for session, reading in zip(sessions, readings, strict=True)
        ],
    }

    if touched_at is None:
        evidence["why"] = (
            f"no session in the window reached {level}, so the level has not been refused"
        )
        return Outcome(status=NOT_YET, evidence=evidence)

    evidence["touched_on"] = sessions[touched_at].isoformat()
    for index in range(touched_at, len(readings)):
        close = readings[index].value
        assert close is not None
        accepted = close >= level if direction == "above" else close <= level
        if accepted:
            evidence["decided_on"] = sessions[index].isoformat()
            evidence["why"] = (
                f"{key} closed {close} at or beyond {level} on {sessions[index].isoformat()}, "
                "which is acceptance rather than refusal"
            )
            return Outcome(status=FAILED, display=str(close), evidence=evidence)

    evidence["decided_on"] = sessions[-1].isoformat()
    evidence["why"] = (
        f"{key} reached {level} on {sessions[touched_at].isoformat()} and has not closed at or "
        "beyond it since"
    )
    return Outcome(status=MET, display=str(readings[-1].value), evidence=evidence)


# ---------------------------------------------------------------------------
# streak_intact
# ---------------------------------------------------------------------------


def streak_intact(args: dict[str, Any], ctx: Context) -> Outcome:
    """A run of same-signed sessions is still running and is long enough.

    The series it reads is a signed session count, not prose: `+2` is two
    consecutive inflow sessions, `-4` is four consecutive outflow sessions, `0`
    is a session whose net flow was exactly zero. `fetchers/etf_flows.py`
    computes it from the daily net flows.

    Read the latest observation at or before the evaluation date. The predicate
    is `met` when the sign of the value matches `direction` and its magnitude is
    at least `min_sessions`, and `not yet` otherwise. It never returns `failed`:
    a streak that ends can start again the next session, so there is no state
    from which the condition cannot become true.

    There is no default for `min_sessions`. A streak of unstated length is not a
    checkable condition.

    **This is not a flow threshold and must not be used as one.** "ETH ETF flows
    positive on the week" is a statement about `eth_etf_weekly_flow` and is
    written `{"op": "gt", "series": "eth_etf_weekly_flow", "value": 0}`. Writing
    it as a streak asks a different question and gets a different answer, which
    is exactly what happened in W38 and cost about 1.7R.

    Unevaluable when there is no observation at or before the evaluation date,
    or when the latest observation is older than the series' own
    `next_expected`, which means a print is genuinely missing rather than merely
    not due yet.
    """
    predicate = "streak_intact"
    key = _series_key(args, predicate, ctx)
    direction = args.get("direction")
    if direction not in ("inflow", "outflow"):
        raise ExpressionRefused(
            f"{predicate}'s `direction` must be inflow or outflow, not {direction!r}"
        )
    if "min_sessions" not in args:
        raise ExpressionRefused(f"{predicate} needs a `min_sessions`; a streak of unstated "
                                "length is not a checkable condition")
    try:
        minimum = int(args["min_sessions"])
    except (TypeError, ValueError) as exc:
        raise ExpressionRefused(
            f"{predicate}'s `min_sessions` is not an integer: {args['min_sessions']!r}"
        ) from exc
    if minimum < 1:
        raise ExpressionRefused(f"{predicate}'s `min_sessions` must be at least 1")

    reading = ctx.history.latest(key, on_or_before=ctx.asof)
    if reading is None or reading.value is None:
        return Outcome(
            status=UNEVALUABLE,
            evidence={
                "predicate": predicate,
                "series": key,
                "asof": ctx.asof.isoformat(),
                "why": f"{key} has no reading at or before {ctx.asof.isoformat()}",
                "gap": ctx.history.has_gap(key, ctx.asof),
            },
        )

    series = ctx.registry.require(key)
    due = series.release_rule
    stale_from = _next_expected_or_none(due, reading.asof)
    if stale_from is not None and ctx.asof >= stale_from:
        return Outcome(
            status=UNEVALUABLE,
            evidence={
                "predicate": predicate,
                "series": key,
                "asof": ctx.asof.isoformat(),
                "latest_asof": reading.asof.isoformat(),
                "next_expected": stale_from.isoformat(),
                "why": (
                    f"{key} last printed for {reading.asof.isoformat()} and the next print was "
                    f"due by {stale_from.isoformat()}, so a session is missing rather than "
                    "merely not due yet"
                ),
                "gap": ctx.history.has_gap(key, stale_from),
            },
        )

    value = reading.value
    matches = value > 0 if direction == "inflow" else value < 0
    long_enough = abs(value) >= minimum
    evidence: dict[str, Any] = {
        "predicate": predicate,
        "series": key,
        "direction": direction,
        "min_sessions": minimum,
        "asof": reading.asof.isoformat(),
        "value": str(value),
        "sign_matches": matches,
        "long_enough": long_enough,
    }
    if matches and long_enough:
        evidence["why"] = (
            f"{key} read {value} on {reading.asof.isoformat()}: the sign matches {direction} "
            f"and the run is at least {minimum} sessions"
        )
        return Outcome(status=MET, display=reading.display or str(value), evidence=evidence)
    evidence["why"] = (
        f"{key} read {value} on {reading.asof.isoformat()}, which does not carry a "
        f"{direction} run of at least {minimum} sessions"
    )
    return Outcome(status=NOT_YET, display=reading.display or str(value), evidence=evidence)


# ---------------------------------------------------------------------------
# crossed_within
# ---------------------------------------------------------------------------


def crossed_within(args: dict[str, Any], ctx: Context) -> Outcome:
    """The series accepted beyond a level at least once in the recent past.

    `met` when some session in the window has `close > level` for
    `direction: above`, or `close < level` for `direction: below`, and `not yet`
    otherwise. It never returns `failed`: another session can always cross.

    **The comparison is strict and the close is the only reading consulted.** A
    level named in a setup is a threshold to clear, not a value to reach, so a
    close exactly at `level` has not crossed it. This is the one place the
    convention differs from `between`, whose bounds are inclusive because a band
    is a region price occupies. `session_high` and `session_low` are not read at
    all: an intraday print through a level is a print, and the ledger's own
    vocabulary calls acceptance a daily close.

    `direction` is required here, unlike in `failed_retest`. An acceptance has
    no natural default side, and a setup that reads "after a daily close above"
    and one that reads "after a daily close below" are opposite trades.

    Unevaluable when any session in the window lacks an observation.
    """
    predicate = "crossed_within"
    key = _series_key(args, predicate, ctx)
    level = _number(args, "level", predicate)
    count = _window_size(args, predicate)
    direction = _direction(args, predicate, default=None)

    sessions, readings, refusal = _window(ctx, key, count)
    if refusal is not None:
        return refusal
    if not sessions:
        return Outcome(status=UNEVALUABLE, evidence=_empty_window_evidence(ctx, key, count))

    evidence: dict[str, Any] = {
        "predicate": predicate,
        "series": key,
        "level": str(level),
        "direction": direction,
        "within_sessions": count,
        "not_before": None if ctx.not_before is None else ctx.not_before.isoformat(),
        "sessions": [
            {"asof": session.isoformat(), "close": str(reading.value)}
            for session, reading in zip(sessions, readings, strict=True)
        ],
    }
    for session, reading in zip(sessions, readings, strict=True):
        close = reading.value
        assert close is not None
        crossed = close > level if direction == "above" else close < level
        if crossed:
            evidence["decided_on"] = session.isoformat()
            evidence["why"] = (
                f"{key} closed {close} {direction} {level} on {session.isoformat()}"
            )
            return Outcome(status=MET, display=str(close), evidence=evidence)

    evidence["why"] = (
        f"no session in the window closed {direction} {level}, so the acceptance never happened"
    )
    return Outcome(status=NOT_YET, display=str(readings[-1].value), evidence=evidence)


# ---------------------------------------------------------------------------
# gate_at_entry
# ---------------------------------------------------------------------------


def gate_at_entry(args: dict[str, Any], ctx: Context) -> Outcome:
    """The gate held on the session the entry band was occupied, not on today.

    1. Find the entry session: the most recent session in the window whose close
       lies inside `[entry_lo, entry_hi]`, inclusive at both ends.
    2. No session in the window closed inside the band: `not yet`. The entry
       never happened, and that is a fact about the tape rather than a failure
       to evaluate.
    3. Otherwise evaluate `gate` against the observations whose `asof` is the
       entry session. Every series the gate names is read at that `asof` and at
       no other.
    4. `met` if the gate holds there, `failed` if it does not.

    `failed` rather than `not yet` at step 4 is deliberate. The entry session has
    passed and its readings will not change, so the gate's verdict on that entry
    is final. A later entry into the same band is a different entry session and
    is evaluated on its own readings.

    **The band is tested on the close.** A session whose low reached into the
    band but whose close did not is not an entry session here; it raises
    `band_touched_today` on `macro_mart.setup_state` instead, so that "entry band
    reached, gate refused, trade forgone" surfaces the same day rather than the
    following Monday.

    Unevaluable when an entry session exists but any series the gate names has
    no observation for that `asof`, or carries a recorded gap there. Reporting
    that as `blocked` would be indistinguishable from a gate that genuinely
    refused.

    `gate` is a nested expression rather than a flat list of comparisons, so a
    gate can say `n_of 2 of 3` without this predicate growing an argument for it.
    """
    predicate = "gate_at_entry"
    key = _series_key(args, predicate, ctx)
    entry_lo = _number(args, "entry_lo", predicate)
    entry_hi = _number(args, "entry_hi", predicate)
    if entry_lo > entry_hi:
        raise ExpressionRefused(
            f"{predicate} has entry_lo {entry_lo} above entry_hi {entry_hi}"
        )
    count = _window_size(args, predicate)
    gate = args.get("gate")
    if not isinstance(gate, dict):
        raise ExpressionRefused(f"{predicate} needs a `gate` expression")

    sessions, readings, refusal = _window(ctx, key, count)
    if refusal is not None:
        return refusal
    if not sessions:
        return Outcome(status=UNEVALUABLE, evidence=_empty_window_evidence(ctx, key, count))

    evidence: dict[str, Any] = {
        "predicate": predicate,
        "series": key,
        "entry_lo": str(entry_lo),
        "entry_hi": str(entry_hi),
        "within_sessions": count,
        "not_before": None if ctx.not_before is None else ctx.not_before.isoformat(),
        "sessions": [
            {"asof": session.isoformat(), "close": str(reading.value)}
            for session, reading in zip(sessions, readings, strict=True)
        ],
    }

    entry_session = _entry_session(sessions, readings, entry_lo, entry_hi)
    if entry_session is None:
        evidence["why"] = (
            f"no session in the window closed inside [{entry_lo}, {entry_hi}], so the entry "
            "never happened"
        )
        return Outcome(status=NOT_YET, evidence=evidence)

    evidence["entry_session"] = entry_session.isoformat()
    inner = evaluate(gate, ctx.with_asof(entry_session))
    evidence["gate"] = inner.evidence
    if inner.is_unevaluable:
        evidence["why"] = (
            f"the entry session {entry_session.isoformat()} exists but the gate could not be "
            "read there, so the gate is unevaluable rather than blocked"
        )
        return Outcome(status=UNEVALUABLE, evidence=evidence)
    if inner.is_met:
        evidence["why"] = f"the gate held on the entry session {entry_session.isoformat()}"
        return Outcome(status=MET, display=inner.display, evidence=evidence)
    evidence["why"] = (
        f"the gate did not hold on the entry session {entry_session.isoformat()}, and that "
        "session's readings will not change"
    )
    return Outcome(status=FAILED, display=inner.display, evidence=evidence)


def _entry_session(
    sessions: Sequence[date],
    readings: Sequence[Reading],
    entry_lo: Decimal,
    entry_hi: Decimal,
) -> date | None:
    """The most recent session in the window that closed inside the band."""
    for session, reading in zip(reversed(sessions), reversed(readings), strict=True):
        close = reading.value
        if close is not None and entry_lo <= close <= entry_hi:
            return session
    return None


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

#: Every predicate, by the name a condition expression uses. `expr.registry`
#: reads this on first use, so the set is whatever this file declares and never
#: whatever happened to have been imported.
#:
#: A fifth predicate is not added by writing one: it is added by amending
#: `docs/CONTRACT.md` and `config/predicates.md` first, because an unlisted
#: predicate is a condition nobody can verify by reading it.
ALL = {
    "failed_retest": failed_retest,
    "streak_intact": streak_intact,
    "crossed_within": crossed_within,
    "gate_at_entry": gate_at_entry,
}


def _text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _next_expected_or_none(rule: Any, asof: date) -> date | None:
    from . import release  # local: keeps `release` out of the module's import cycle

    try:
        return release.next_expected(rule, asof)
    except release.RuleRefused:
        return None
