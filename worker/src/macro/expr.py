"""The condition expression form, exactly as `docs/CONTRACT.md` fixes it.

    {"op": "lt",  "series": "breadth_spx_above_200dma", "value": 45}
    {"op": "gt",  "series": "hy_oas_bps",               "value": 300}
    {"op": "between", "series": "brent",  "lo": 92, "hi": 112}
    {"all":  [ ... ]}
    {"any":  [ ... ]}
    {"n_of": 1, "of": [ ... ]}
    {"predicate": "failed_retest", "args": { ... }}
    {"manual": "Rallies fail at prior support"}

    {"op": "lt", "series": "spx_cash", "field": "dma200_pct", "value": 0}
    {"op": "eq", "series": "cftc_asset_managers_es", "field": "band", "value": "heavy"}

Operators: `lt`, `lte`, `gt`, `gte`, `eq`, `between`, `outside`.
Combinators: `all`, `any`, `n_of`.

## The `field` accessor

`field` reads one named key from the observation's `extra`, and nothing else.
No arithmetic, no derivation, no traversal: one key, one level deep, on a value
this worker already computed and stored. A field absent from `extra` resolves to
`unevaluable`, exactly as a missing observation does, and never to false.

Only `eq` is permitted against a text field such as the CFTC regime band, and
that is refused at **validation** rather than at evaluation, so a malformed
condition fails when it is written and not on the morning it first matters.
`EXTRA_FIELDS` below is the whole vocabulary: a field this worker does not
store, or a field the named series does not carry, is refused the same way.

This widens the expression form, which `docs/DECISIONS.md` argued against, and
the reason is narrow. `dma200_pct`, `dma200_slope`, `spread_2s10s_bps` and the
CFTC `band` are already computed, already stored, and already first-class rows
in the operator's own frameworks: "below a declining 200dma" is a standing
phase-tracker line, not a new idea. Leaving them unreachable does not keep the
language narrow, it makes the trackers score against a subset of their own
recipe. The accessor adds no operator and no arithmetic, so it cannot express
anything that was not already measured.

This is a narrow typed form and not a language. Every operator added is another
way to write a condition that cannot be verified by reading it, and the failure
mode recorded across R1 to R12 is bad judgment about which variable matters,
never insufficient expressiveness.

## The three-valued logic, and why it is not two-valued

An expression resolves to `met`, `not yet` or `unevaluable`, and the third one
is the load-bearing one. A series with no observation for the evaluation date is
`unevaluable`, never false. A tracker that silently scores an unevaluated
condition as "not yet" is not trustworthy unattended, and that is the whole
argument for this module returning three values instead of a boolean.

The combinators propagate it the way three-valued logic requires, which is not
the same as propagating it eagerly:

- `all` is false as soon as one child is false, even if another is unknown. A
  conjunction with a false term cannot become true.
- `any` is true as soon as one child is true, even if another is unknown.
- `n_of` is true once `n` children are true, and unknown only while the known
  trues plus the unknowns could still reach `n`.

`failed` is never produced here. It is a judgment that a condition has moved
decisively away and no expression can decide it; the predicates produce it in
the two places `config/predicates.md` says they must, and nowhere else.

## Units

Values compare in the series' declared canonical unit from `config/series.yaml`.
`hy_oas_bps` takes `292`, never `2.92`. Nothing in this module converts: a
comparison whose operand is in the wrong unit is not a comparison this module
can rescue, and converting silently is how a percent reaches a basis-point
threshold. The registry is consulted only to refuse a series it does not know,
and to refuse a numeric comparison against a `text` series, which may appear in
a `manual` condition and nowhere else.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Literal, Protocol

from .model import Status
from .observations import History
from .series import Registry

OPERATORS: frozenset[str] = frozenset({"lt", "lte", "gt", "gte", "eq", "between", "outside"})
COMBINATORS: frozenset[str] = frozenset({"all", "any", "n_of"})


@dataclass(frozen=True, slots=True)
class ExtraField:
    """One key of `macro_core.observation.extra` that a condition may read.

    `series` is which series carry it, because a field is a property of what its
    fetcher computed and not of the schema. `values` is the closed vocabulary of
    a text field, which makes a typo in a band name a validation error rather
    than a condition that is false for ever.
    """

    name: str
    kind: Literal["numeric", "text"]
    unit: str | None
    series: frozenset[str]
    values: frozenset[str] | None = None


#: The two index series that carry a 200-day average. Declared here and asserted
#: against `fetchers.yahoo.SPECS` by a test, rather than imported, because the
#: expression form must not depend on a fetcher.
_DMA200_SERIES = frozenset({"spx_cash", "nasdaq_cash"})

#: Everything that stores a session high and low.
_SESSION_SERIES = frozenset(
    {
        "spx_front", "spx_cash", "dow_front", "dow_cash", "nasdaq_cash",
        "rut_front", "rut_cash", "gold", "silver", "wti", "brent", "dxy",
        "vix", "move", "breadth_spx_above_200dma",
    }
)

#: Every `extra` key a condition may name. A key absent from this table is
#: refused at validation: the accessor exists to reach figures this worker
#: already computes, so a field nobody computes is a typo, not a feature.
EXTRA_FIELDS: dict[str, ExtraField] = {
    "dma200_pct": ExtraField(
        name="dma200_pct",
        kind="numeric",
        unit="percent",
        series=_DMA200_SERIES,
    ),
    "dma200_slope": ExtraField(
        name="dma200_slope",
        kind="text",
        unit=None,
        series=_DMA200_SERIES,
        values=frozenset({"rising", "falling", "flat"}),
    ),
    "spread_2s10s_bps": ExtraField(
        name="spread_2s10s_bps",
        kind="numeric",
        unit="bps",
        series=frozenset({"ust_10y"}),
    ),
    "band": ExtraField(
        name="band",
        kind="text",
        unit=None,
        series=frozenset({"cftc_asset_managers_es", "cftc_leveraged_funds_es"}),
        values=frozenset({"sidelined", "neutral", "heavy", "extreme"}),
    ),
    "session_high": ExtraField(
        name="session_high", kind="numeric", unit=None, series=_SESSION_SERIES
    ),
    "session_low": ExtraField(
        name="session_low", kind="numeric", unit=None, series=_SESSION_SERIES
    ),
}


class ExpressionRefused(Exception):
    """An expression this worker will not guess at. The message is the diagnosis."""


@dataclass(frozen=True, slots=True)
class Outcome:
    """What one expression resolved to, and why.

    `evidence` is a JSON object naming what was read and the term the answer
    turned on. `macro_mart.condition_state.evidence` carries it and the web app
    renders it, so a reader can see why a gate refused without opening the
    worker.
    """

    status: Status
    display: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def is_met(self) -> bool:
        return self.status is Status.MET

    @property
    def is_unevaluable(self) -> bool:
        return self.status is Status.UNEVALUABLE


MET = Status.MET
NOT_YET = Status.NOT_YET
UNEVALUABLE = Status.UNEVALUABLE
FAILED = Status.FAILED


@dataclass(frozen=True, slots=True)
class Context:
    """Everything an expression is resolved against.

    `not_before` is the predicate lookback clip: the `activated_at` session of
    the judgment that owns the condition. Nothing in the plain operator form
    reads it, because a plain operator looks at one day. Every predicate does.
    """

    asof: date
    history: History
    registry: Registry
    not_before: date | None = None

    def with_asof(self, asof: date) -> Context:
        """The same context pinned to a different evaluation date.

        `gate_at_entry` uses it: a gate is a statement about the entry session,
        so its inner expression is resolved at that session and at no other.
        """
        return Context(
            asof=asof,
            history=self.history,
            registry=self.registry,
            not_before=self.not_before,
        )


class PredicateFn(Protocol):
    """A named path-dependent predicate, as `config/predicates.md` defines them."""

    def __call__(self, args: dict[str, Any], ctx: Context) -> Outcome: ...


def predicate_registry() -> dict[str, PredicateFn]:
    """The named predicates, resolved from `macro.predicates` on first use.

    A lazy import rather than a module-level one, because `predicates` imports
    this module and the two would deadlock at import time. A lazy import rather
    than a mutable global that `predicates` fills on import, because that made
    the registry depend on whether something had happened to import the right
    module: `main` reaches `expr` through `evaluate` and never through
    `predicates`, so every predicate condition on the real run path resolved to
    "predicate is not one of []" while the predicate tests passed, because they
    imported it directly.
    """
    from . import predicates

    return predicates.ALL


def registered() -> frozenset[str]:
    """Every predicate name a condition may use."""
    return frozenset(predicate_registry())


def evaluate(expression: Any, ctx: Context) -> Outcome:
    """Resolve one expression to `met`, `not yet`, `failed` or `unevaluable`."""
    if not isinstance(expression, dict):
        raise ExpressionRefused(
            f"an expression must be a JSON object, not {type(expression).__name__}"
        )

    if "manual" in expression:
        return Outcome(
            status=UNEVALUABLE,
            display=None,
            evidence={
                "kind": "manual",
                "text": str(expression["manual"]),
                "why": "awaiting judgment: a manual condition is reported, never scored",
            },
        )
    if "predicate" in expression:
        return _predicate(expression, ctx)
    if "all" in expression:
        return _all(expression["all"], ctx)
    if "any" in expression:
        return _any(expression["any"], ctx)
    if "n_of" in expression:
        return _n_of(expression, ctx)
    if "op" in expression:
        return _comparison(expression, ctx)
    raise ExpressionRefused(
        f"expression has no op, combinator, predicate or manual key: {sorted(expression)}"
    )


# ---------------------------------------------------------------------------
# Comparisons
# ---------------------------------------------------------------------------


def _comparison(expression: dict[str, Any], ctx: Context) -> Outcome:
    op = str(expression["op"])
    if op not in OPERATORS:
        raise ExpressionRefused(f"operator {op!r} is not one of {sorted(OPERATORS)}")
    key = str(expression.get("series") or "")
    if not key:
        raise ExpressionRefused(f"a {op!r} comparison names no series")

    series = ctx.registry.get(key)
    if series is None:
        raise ExpressionRefused(
            f"a {op!r} comparison names series {key!r}, which config/series.yaml does not declare"
        )
    if series.value_kind == "text":
        raise ExpressionRefused(
            f"series {key!r} is a prose row and may only appear in a manual condition, "
            f"not in a {op!r} comparison"
        )

    field_name = expression.get("field")
    field = None if field_name is None else _field(str(field_name), key, op)

    reading = ctx.history.at(key, ctx.asof)
    if reading is None or (field is None and reading.value is None):
        return Outcome(
            status=UNEVALUABLE,
            display=None,
            evidence={
                "kind": "op",
                "op": op,
                "series": key,
                **({"field": field.name} if field else {}),
                "asof": ctx.asof.isoformat(),
                "why": (
                    f"{key} has no observation for {ctx.asof.isoformat()}"
                    if reading is None
                    else f"{key} carries a null value for {ctx.asof.isoformat()}"
                ),
                "gap": ctx.history.has_gap(key, ctx.asof),
            },
        )

    evidence: dict[str, Any] = {
        "kind": "op",
        "op": op,
        "series": key,
        "asof": ctx.asof.isoformat(),
        "unit": series.unit,
    }

    if field is None:
        assert reading.value is not None  # narrowed above
        evidence["current"] = str(reading.value)
        held = _compare(op, reading.value, expression, evidence)
        return Outcome(
            status=MET if held else NOT_YET,
            display=reading.display or str(reading.value),
            evidence=evidence,
        )

    evidence["field"] = field.name
    evidence["unit"] = field.unit
    if field.name not in reading.extra:
        # A field absent from `extra` is exactly a missing observation: the
        # figure the condition names was not computed for this day, and a
        # condition over a figure nobody measured is not false.
        evidence["why"] = (
            f"{key} carries no {field.name!r} in its extra for {ctx.asof.isoformat()}"
        )
        evidence["available"] = sorted(reading.extra)
        return Outcome(status=UNEVALUABLE, display=None, evidence=evidence)

    raw = reading.extra[field.name]
    if raw is None:
        evidence["why"] = f"{key}'s {field.name!r} is null for {ctx.asof.isoformat()}"
        return Outcome(status=UNEVALUABLE, display=None, evidence=evidence)

    if field.kind == "text":
        current = str(raw)
        evidence["current"] = current
        # Validation has already refused anything but `eq` on a text field.
        held = current == str(expression.get("value"))
        evidence["value"] = str(expression.get("value"))
        return Outcome(status=MET if held else NOT_YET, display=current, evidence=evidence)

    try:
        current_value = Decimal(str(raw))
    except ArithmeticError:
        evidence["why"] = (
            f"{key}'s {field.name!r} reads {raw!r} for {ctx.asof.isoformat()}, which is not a "
            "number, so the comparison has nothing to stand on"
        )
        return Outcome(status=UNEVALUABLE, display=str(raw), evidence=evidence)

    evidence["current"] = str(current_value)
    held = _compare(op, current_value, expression, evidence)
    return Outcome(
        status=MET if held else NOT_YET, display=str(current_value), evidence=evidence
    )


def _field(name: str, series_key: str, op: str) -> ExtraField:
    """The declared field, or a refusal naming what is available.

    Both halves matter. An unknown key is a typo, and a known key on a series
    that does not carry it is a condition that would be `unevaluable` for ever
    while looking perfectly well formed.
    """
    field = EXTRA_FIELDS.get(name)
    if field is None:
        raise ExpressionRefused(
            f"{name!r} is not a field this worker stores; the fields a condition may read are "
            f"{sorted(EXTRA_FIELDS)}"
        )
    if series_key not in field.series:
        raise ExpressionRefused(
            f"series {series_key!r} does not carry {name!r}; it is stored on "
            f"{sorted(field.series)}"
        )
    if field.kind == "text" and op != "eq":
        raise ExpressionRefused(
            f"{name!r} is a text field and only `eq` compares against one, not {op!r}"
        )
    return field


def _compare(
    op: str, current: Decimal, expression: dict[str, Any], evidence: dict[str, Any]
) -> bool:
    if op in ("between", "outside"):
        lo = _operand(expression, "lo", op)
        hi = _operand(expression, "hi", op)
        if lo > hi:
            raise ExpressionRefused(f"a {op!r} band has lo {lo} above hi {hi}")
        evidence["lo"], evidence["hi"] = str(lo), str(hi)
        # Inclusive at both ends. A band is a region price occupies, so a value
        # exactly on a bound is inside it, which is what every scenario target
        # in the journal means by "S&P 7,520-7,800".
        inside = lo <= current <= hi
        return inside if op == "between" else not inside

    threshold = _operand(expression, "value", op)
    evidence["value"] = str(threshold)
    if op == "lt":
        return current < threshold
    if op == "lte":
        return current <= threshold
    if op == "gt":
        return current > threshold
    if op == "gte":
        return current >= threshold
    if op == "eq":
        return current == threshold
    raise ExpressionRefused(f"operator {op!r} has no comparison")


def _operand(expression: dict[str, Any], name: str, op: str) -> Decimal:
    if name not in expression:
        raise ExpressionRefused(f"a {op!r} comparison has no {name!r}")
    raw = expression[name]
    try:
        return Decimal(str(raw))
    except ArithmeticError as exc:
        raise ExpressionRefused(f"a {op!r} comparison has a non-numeric {name!r}: {raw!r}") from exc


# ---------------------------------------------------------------------------
# Combinators
# ---------------------------------------------------------------------------


def _children(raw: Any, name: str) -> list[Any]:
    if not isinstance(raw, list) or not raw:
        raise ExpressionRefused(f"{name!r} needs a non-empty list of expressions")
    return list(raw)


def _all(raw: Any, ctx: Context) -> Outcome:
    outcomes = [evaluate(child, ctx) for child in _children(raw, "all")]
    evidence = _child_evidence("all", outcomes)
    # A conjunction with a false term cannot become true, so a known false wins
    # over an unknown. That is three-valued logic, not an optimisation.
    if any(_is_false(one) for one in outcomes):
        return Outcome(status=NOT_YET, evidence=evidence)
    if any(one.is_unevaluable for one in outcomes):
        return Outcome(status=UNEVALUABLE, evidence=evidence)
    return Outcome(status=MET, evidence=evidence)


def _any(raw: Any, ctx: Context) -> Outcome:
    outcomes = [evaluate(child, ctx) for child in _children(raw, "any")]
    evidence = _child_evidence("any", outcomes)
    if any(one.is_met for one in outcomes):
        return Outcome(status=MET, evidence=evidence)
    if any(one.is_unevaluable for one in outcomes):
        return Outcome(status=UNEVALUABLE, evidence=evidence)
    return Outcome(status=NOT_YET, evidence=evidence)


def _n_of(expression: dict[str, Any], ctx: Context) -> Outcome:
    raw_threshold = expression["n_of"]
    try:
        threshold = int(raw_threshold)
    except (TypeError, ValueError) as exc:
        raise ExpressionRefused(f"n_of needs an integer threshold, not {raw_threshold!r}") from exc
    children = _children(expression.get("of"), "n_of")
    if not 1 <= threshold <= len(children):
        raise ExpressionRefused(
            f"n_of {threshold} is outside 1..{len(children)}, the number of clauses it counts"
        )
    outcomes = [evaluate(child, ctx) for child in children]
    met = sum(1 for one in outcomes if one.is_met)
    unknown = sum(1 for one in outcomes if one.is_unevaluable)
    evidence = _child_evidence("n_of", outcomes)
    evidence["n_of"] = threshold
    evidence["met"] = met
    evidence["unevaluable"] = unknown
    # The threshold is never renormalised against how many children could be
    # read. A clause that cannot be evaluated is counted in the denominator and
    # leaves the answer unknown; it never makes the rest of the set easier.
    if met >= threshold:
        return Outcome(status=MET, evidence=evidence)
    if met + unknown >= threshold:
        return Outcome(status=UNEVALUABLE, evidence=evidence)
    return Outcome(status=NOT_YET, evidence=evidence)


def _child_evidence(kind: str, outcomes: list[Outcome]) -> dict[str, Any]:
    return {
        "kind": kind,
        "children": [
            {"status": str(one.status), **({"evidence": one.evidence} if one.evidence else {})}
            for one in outcomes
        ],
    }


def _is_false(outcome: Outcome) -> bool:
    """A term that cannot become true: a machine false, or a predicate's `failed`."""
    return outcome.status in (NOT_YET, FAILED, Status.VOID)


# ---------------------------------------------------------------------------
# Predicates
# ---------------------------------------------------------------------------


def _predicate(expression: dict[str, Any], ctx: Context) -> Outcome:
    name = str(expression["predicate"])
    fn = predicate_registry().get(name)
    if fn is None:
        raise ExpressionRefused(
            f"predicate {name!r} is not one of {sorted(predicate_registry())}; an unlisted "
            "predicate is a condition nobody can verify by reading it"
        )
    args = expression.get("args")
    if not isinstance(args, dict):
        raise ExpressionRefused(f"predicate {name!r} has no args object")
    return fn(dict(args), ctx)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate(expression: Any, registry: Registry) -> None:
    """Refuse an expression that could never be evaluated, at write time.

    Everything `evaluate` would raise on, raised here instead, against the
    registry alone and without any observations. The authoring run calls this
    before it stores a condition, and every run re-checks what is stored, so a
    malformed condition fails on the morning it is written rather than on the
    morning it first matters.

    Silence means the expression is well formed. It does not mean it is true,
    and it does not promise a reading will exist for it.
    """
    if not isinstance(expression, dict):
        raise ExpressionRefused(
            f"an expression must be a JSON object, not {type(expression).__name__}"
        )
    if "manual" in expression:
        if not str(expression["manual"]).strip():
            raise ExpressionRefused("a manual condition with no text states nothing")
        return
    if "predicate" in expression:
        _validate_predicate(expression, registry)
        return
    for combinator in ("all", "any"):
        if combinator in expression:
            for child in _children(expression[combinator], combinator):
                validate(child, registry)
            return
    if "n_of" in expression:
        children = _children(expression.get("of"), "n_of")
        try:
            threshold = int(expression["n_of"])
        except (TypeError, ValueError) as exc:
            raise ExpressionRefused(
                f"n_of needs an integer threshold, not {expression['n_of']!r}"
            ) from exc
        if not 1 <= threshold <= len(children):
            raise ExpressionRefused(
                f"n_of {threshold} is outside 1..{len(children)}, the number of clauses it counts"
            )
        for child in children:
            validate(child, registry)
        return
    if "op" in expression:
        _validate_comparison(expression, registry)
        return
    raise ExpressionRefused(
        f"expression has no op, combinator, predicate or manual key: {sorted(expression)}"
    )


def _validate_comparison(expression: dict[str, Any], registry: Registry) -> None:
    op = str(expression["op"])
    if op not in OPERATORS:
        raise ExpressionRefused(f"operator {op!r} is not one of {sorted(OPERATORS)}")
    key = str(expression.get("series") or "")
    if not key:
        raise ExpressionRefused(f"a {op!r} comparison names no series")
    series = registry.get(key)
    if series is None:
        raise ExpressionRefused(
            f"a {op!r} comparison names series {key!r}, which config/series.yaml does not declare"
        )
    if series.value_kind == "text":
        raise ExpressionRefused(
            f"series {key!r} is a prose row and may only appear in a manual condition, "
            f"not in a {op!r} comparison"
        )

    field_name = expression.get("field")
    if field_name is None:
        _validate_operands(expression, op)
        return

    field = _field(str(field_name), key, op)
    if field.kind == "text":
        value = expression.get("value")
        if value is None:
            raise ExpressionRefused(f"an `eq` on the text field {field.name!r} names no value")
        if field.values is not None and str(value) not in field.values:
            raise ExpressionRefused(
                f"{str(value)!r} is not one of {field.name!r}'s values, which are "
                f"{sorted(field.values)}; a typo here is a condition that is false for ever"
            )
        return
    _validate_operands(expression, op)


def _validate_operands(expression: dict[str, Any], op: str) -> None:
    if op in ("between", "outside"):
        lo = _operand(expression, "lo", op)
        hi = _operand(expression, "hi", op)
        if lo > hi:
            raise ExpressionRefused(f"a {op!r} band has lo {lo} above hi {hi}")
        return
    _operand(expression, "value", op)


def _validate_predicate(expression: dict[str, Any], registry: Registry) -> None:
    name = str(expression["predicate"])
    if name not in predicate_registry():
        raise ExpressionRefused(
            f"predicate {name!r} is not one of {sorted(predicate_registry())}; an unlisted "
            "predicate is a condition nobody can verify by reading it"
        )
    args = expression.get("args")
    if not isinstance(args, dict):
        raise ExpressionRefused(f"predicate {name!r} has no args object")
    key = args.get("series")
    if isinstance(key, str) and key and registry.get(key) is None:
        raise ExpressionRefused(
            f"predicate {name!r} names series {key!r}, which config/series.yaml does not declare"
        )
    gate = args.get("gate")
    if gate is not None:
        validate(gate, registry)


def series_referenced(expression: Any) -> set[str]:
    """Every series key an expression names, at any depth.

    Used to decide which readings a condition depends on, and therefore which
    recorded gaps make it unevaluable.
    """
    found: set[str] = set()
    _walk(expression, found)
    return found


def _walk(node: Any, found: set[str]) -> None:
    if isinstance(node, dict):
        series = node.get("series")
        if isinstance(series, str):
            found.add(series)
        for key in ("all", "any", "of"):
            for child in node.get(key) or []:
                _walk(child, found)
        args = node.get("args")
        if isinstance(args, dict):
            _walk(args, found)
        gate = node.get("gate")
        if gate is not None:
            _walk(gate, found)
    elif isinstance(node, list):
        for child in node:
            _walk(child, found)
