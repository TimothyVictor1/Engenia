"""Verifier registry and loader.

Built-in verifiers live in this package. A stage can add a verifier without editing this
package by registering a callable with the standard ``run(ctx) -> Verdict`` signature::

    from verifiers import register
    register("int_board_fits_bay", run_int_board_fits_bay)

``load(name)`` returns that callable. Unknown names raise ``KeyError``.
"""

from __future__ import annotations

from collections.abc import Callable
from importlib import import_module

from engenia.contracts.models import CheckContext, Verdict

Runner = Callable[[CheckContext], Verdict]

_BUILTIN_NAMES = (
    "mass_budget",
    "buoyancy_margin",
    "print_bed_fit",
    "wall_thickness_min",
    "metacentric_height",
    "power_budget",
    "battery_capacity",
    "erc_clean",
    "bay_fit",
    "wave_tilt",
    "stress_safety_factor",
)

REGISTRY: dict[str, Runner] = {}

__all__ = ["REGISTRY", "available", "load", "register"]


def register(name: str, runner: Runner | None = None):
    """Register ``runner`` under ``name``; usable directly or as a decorator.

    ``register("name", func)`` returns ``func``. ``@register("name")`` returns a
    decorator so the function is registered and remains available to its own module.
    """

    def _add(func: Runner) -> Runner:
        REGISTRY[name] = func
        return func

    if runner is not None:
        return _add(runner)
    return _add


def load(name: str) -> Runner:
    """Return the ``run(ctx)`` callable registered under ``name``.

    A built-in verifier is imported on first use if it was not loaded at package import.
    """
    if name in REGISTRY:
        return REGISTRY[name]

    try:
        module = import_module(f"verifiers.{name}")
    except ModuleNotFoundError as exc:
        if exc.name == f"verifiers.{name}":
            raise KeyError(f"unknown verifier: {name}") from exc
        raise
    runner = getattr(module, "run", None)
    if not callable(runner):
        raise KeyError(f"verifier {name} has no run(ctx) function")
    REGISTRY[name] = runner
    return runner


def available() -> tuple[str, ...]:
    """Return the sorted names currently in the registry."""
    return tuple(sorted(REGISTRY))


def _register_builtin(name: str) -> None:
    module = import_module(f"verifiers.{name}")
    register(name, module.run)


for _name in _BUILTIN_NAMES:
    _register_builtin(_name)
