"""The one seam onto the worker's pure modules.

Nothing else in `macro_agent` imports anything from `worker/`. This module does,
in exactly one place, deliberately, and it is the only file that may.

The reason it exists rather than the alternative:

- The Brier arithmetic must not be reimplemented. Twelve graded weeks were
  scored with `worker/src/macro/calibration.py`, which is itself an unchanged
  port of the vault skill's script, and the Brier series is only comparable if
  the formula does not move. A second implementation is a second formula the
  day one of them is touched.
- A typed expression this package authors has to be evaluated by the evaluator
  that will actually run it at 21:00. Validating it against a lookalike written
  here would prove that two of our own modules agree, which is not the thing
  worth knowing.

The seam is a path, not a dependency. `worker/src` is put on `sys.path` at call
time and the worker's modules are imported from there. This package declares no
dependency on the worker, builds without it, and edits nothing inside it. When
the worker is absent, `require` raises a sentence naming what is missing rather
than an `ImportError` five frames down.
"""

from __future__ import annotations

import sys
from functools import cache
from pathlib import Path
from types import ModuleType
from typing import Any

from .config import Settings

#: The worker modules this package is allowed to reach. Anything not on this
#: list is out of bounds: the seam is narrow on purpose, and a module added
#: here is a coupling that has to be justified in the README.
ALLOWED: frozenset[str] = frozenset(
    {
        "macro.calibration",  # Brier and the rolling read
        "macro.expr",  # the condition expression form, three-valued
        "macro.predicates",  # the four named path-dependent predicates
        "macro.observations",  # Reading and History, the read model
        "macro.series",  # config/series.yaml into a Registry
        "macro.model",  # the normalized vocabulary the above speak
        "macro.grading",  # week_grade and count_targets
        "macro.evaluate",  # scenario target states, for the grading counts
        # The desk command writes the rows the vault parser used to, so it has to
        # apply the parser's rules, not a copy of them.
        "macro.vault",  # external_key, parse_when, is_estimated
        "macro.families",  # the sixteen categories and the family of a name
        "macro.clock",  # the US session calendar and the Singapore day
        # Not pure: it imports httpx, which this package now declares. It is the one
        # place the Yahoo user agent, the pacing and the 429 breaker are written.
        "macro.http",
    }
)


class WorkerUnavailable(Exception):
    """The worker's pure modules could not be reached. The message is the diagnosis."""


@cache
def _install(src: Path) -> None:
    if not (src / "macro" / "__init__.py").is_file():
        raise WorkerUnavailable(
            f"{src}/macro is not a Python package, so the Brier arithmetic and the condition "
            "evaluator cannot be reached. This package calls the worker's implementations "
            "rather than holding a second copy of either. Set WORKER_SRC to the worker's src "
            "directory, or run from a checkout that has worker/ in it."
        )
    text = str(src)
    if text not in sys.path:
        sys.path.insert(0, text)


def require(name: str, settings: Settings) -> ModuleType:
    """Import one allowed worker module, or say why it could not be reached."""
    if name not in ALLOWED:
        raise WorkerUnavailable(
            f"{name!r} is not on this package's list of worker modules. The seam is narrow on "
            f"purpose; widening it is a change to agent/README.md first. Allowed: "
            f"{', '.join(sorted(ALLOWED))}."
        )
    _install(settings.worker_src)
    try:
        return __import__(name, fromlist=["_"])
    except ImportError as exc:  # pragma: no cover - only on a broken checkout
        raise WorkerUnavailable(
            f"{name} could not be imported from {settings.worker_src}: {exc}. The worker's "
            "pure modules depend only on the standard library, so this is a missing or "
            "partial checkout rather than an unmet dependency."
        ) from exc


def calibration(settings: Settings) -> Any:
    """`macro.calibration`: `brier`, `read`, `ScenarioOutcome`, `GradedWeek`."""
    return require("macro.calibration", settings)


def grading(settings: Settings) -> Any:
    """`macro.grading`: `week_grade`, `count_targets`, `ScenarioJudgment`."""
    return require("macro.grading", settings)


def evaluator(settings: Settings) -> tuple[Any, Any, Any]:
    """`(expr, predicates, observations)`, with the four predicates registered.

    Importing `macro.predicates` is what fills `macro.expr.REGISTRY`, so a
    caller that took `expr` alone would find every `{"predicate": ...}`
    unresolvable and would conclude the expression was malformed.
    """
    expr = require("macro.expr", settings)
    predicates = require("macro.predicates", settings)
    observations = require("macro.observations", settings)
    return expr, predicates, observations


def registry(settings: Settings) -> Any:
    """The live `config/series.yaml`, as a `macro.series.Registry`.

    The repository's own file, never a copy: a copied registry is a second place
    for a unit to be wrong, which is the failure the one-canonical-unit rule
    exists to prevent.
    """
    series = require("macro.series", settings)
    return series.load(settings.series_yaml)


def vault(settings: Settings) -> Any:
    """`macro.vault`: `external_key`, `parse_when`, `is_estimated`, the catalyst rules."""
    return require("macro.vault", settings)


def families(settings: Settings) -> Any:
    """`macro.families`: `classify(name) -> (family, category)` and `CATEGORIES`."""
    return require("macro.families", settings)


def clock(settings: Settings) -> Any:
    """`macro.clock`: `last_completed_us_session`, `singapore_local`, the US calendar."""
    return require("macro.clock", settings)


def http(settings: Settings) -> Any:
    """`macro.http`: `Client` and `FetchRefused`, with the per-host user agents."""
    return require("macro.http", settings)
