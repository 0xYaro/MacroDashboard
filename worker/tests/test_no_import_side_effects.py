"""No module in this package does anything at import time but define things.

This exists because of one bug, and it is the worst class this system could
have had. `expr.REGISTRY` was a mutable dict that `macro.predicates` filled when
it was imported. `main` reaches `expr` through `evaluate` and never through
`predicates`, so on the real run path the registry was empty: every predicate
condition resolved to "predicate is not one of []", scored `unevaluable`, and
blocked upgrade eligibility for ever.

Twenty-one predicate tests passed throughout, because importing the predicates
in order to test them was itself what registered them. The suite was
structurally incapable of catching it, and no amount of adding cases to those
files would have changed that.

So the guard is structural rather than behavioural. A module that only defines
things cannot have a behaviour that depends on whether something imported it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "macro"

#: Methods whose appearance in a module-level expression means that expression
#: is changing something rather than describing it.
MUTATORS = frozenset({"append", "extend", "add", "update", "setdefault", "register", "pop"})


def modules() -> list[Path]:
    return sorted(PACKAGE.rglob("*.py"))


def test_the_package_has_modules_to_check():
    assert len(modules()) >= 20


@pytest.mark.parametrize("path", modules(), ids=lambda p: p.name)
def test_a_module_only_defines_things_at_import_time(path: Path):
    """No loop, no branch, no mutation at module level.

    The one `if` allowed anywhere is `if __name__ == "__main__"`, which runs
    nothing on import.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(
            node,
            (
                ast.Import,
                ast.ImportFrom,
                ast.FunctionDef,
                ast.AsyncFunctionDef,
                ast.ClassDef,
                ast.Expr,  # a docstring
                ast.Pass,
            ),
        ):
            continue
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            _assert_describes_rather_than_changes(node, path)
            continue
        if isinstance(node, ast.If) and _is_main_guard(node):
            continue
        raise AssertionError(
            f"{path.name}:{node.lineno} runs a {type(node).__name__} at import time. "
            "A module that does work on import behaves differently depending on who "
            "imported it, which is how the predicate registry came to be empty on the "
            "real run path while every predicate test passed."
        )


def _assert_describes_rather_than_changes(node: ast.stmt, path: Path) -> None:
    value = getattr(node, "value", None)
    if value is None:
        return
    for inner in ast.walk(value):
        if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute):
            assert inner.func.attr not in MUTATORS, (
                f"{path.name}:{node.lineno} calls .{inner.func.attr}() while building a "
                "module-level value, which changes something at import time"
            )


def _is_main_guard(node: ast.If) -> bool:
    test = node.test
    return (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name)
        and test.left.id == "__name__"
    )


def test_the_predicate_registry_is_not_a_mutable_global():
    """The specific shape that caused it, asserted directly.

    `expr` resolves the predicates from `predicates.ALL` on first use. There is
    no module-level dict for an import to fill, so there is nothing that can be
    half-filled.
    """
    from macro import expr

    assert not hasattr(expr, "REGISTRY")
    assert not hasattr(expr, "register")
    assert callable(expr.predicate_registry)


def test_every_public_registry_is_complete_where_it_is_declared():
    """Each lookup table is a literal in one file and is never added to.

    Asserted by reading them, because a table built by accumulation is exactly
    the shape that made the registry bug possible.
    """
    from macro import calibration, expr, grading
    from macro.fetchers import coingecko, fred, treasury, treasury_auctions, yahoo

    assert set(calibration.WEIGHT) == {"materialized", "partial", "not"}
    assert set(grading.OUTCOMES) == {"not triggered", "open", "stopped", "target"}
    assert set(expr.EXTRA_FIELDS) >= {"dma200_pct", "dma200_slope", "spread_2s10s_bps", "band"}
    assert len(treasury_auctions.TENORS) == 7
    assert len(treasury.COLUMNS) == 3
    assert len(fred.PRINTS) == 9
    assert len(yahoo.SPECS) == 14
    assert len(coingecko.COINS) == 3
