"""Wave-tilt verifier, judged on simulated evidence.

The simulation interface publishes one entry per scenario under the ``wave_tilt`` fact::

    {"wave_tilt": {"calm_harbour": {"wave_height_m": 0.5, "max_tilt_deg": 4.0}, ...}}

The scenarios judged are the ones matching the requirement's conditions:

- when the check plan supplies a significant wave height (``params.wave_height_m``, or
  ``params.sig_wave_height_m`` / ``params.significant_wave_height_m``), every scenario whose
  wave height is at most that value is judged, and
- otherwise (no wave-height condition in the plan) the calm_harbour and baltic_winter scenarios
  are judged by name.

FORMULA:
    measured = the largest max_tilt_deg across the judged scenarios
    pass when measured satisfies ctx.limit, normally ``limit.max``

The verdict's evidence level is "simulated". Missing facts give ``error``: no wave_tilt fact, a
judged scenario without a numeric ``max_tilt_deg`` (or, with a wave-height condition, without
``wave_height_m``), a named scenario the simulation never ran, or no scenario matching the
condition at all. Errors are never passes.

ASSUMES: wave heights are significant wave heights in metres and tilts are in degrees.
"""

from __future__ import annotations

from typing import Any

from engenia.contracts.models import CheckContext, Verdict
from verifiers._common import (
    MissingInput,
    as_number,
    checked_simulated_verdict,
    guard,
    read_interface,
)

WAVE_HEIGHT_KEYS = ("wave_height_m", "sig_wave_height_m", "significant_wave_height_m")
FALLBACK_SCENARIOS = ("calm_harbour", "baltic_winter")


def _condition_m(params: dict[str, Any]) -> float | None:
    """The significant wave height the requirement allows, or None when the plan has none."""
    for key in WAVE_HEIGHT_KEYS:
        if key in params:
            return as_number(params[key], f"params.{key}")
    return None


def _scenario_value(scenario: str, entry: Any, key: str) -> float:
    if not isinstance(entry, dict):
        raise MissingInput(f"scenario {scenario} must be a mapping of facts, got {entry!r}")
    if key not in entry:
        raise MissingInput(f"simulation scenario {scenario} has no {key} fact")
    return as_number(entry[key], f"simulation scenario {scenario}.{key}")


def _selected(scenarios: dict[str, Any], condition: float | None) -> list[str]:
    """The scenario names this verdict judges, in a deterministic order."""
    if condition is None:
        missing = [name for name in FALLBACK_SCENARIOS if name not in scenarios]
        if missing:
            raise MissingInput(
                "simulation interface has no "
                + " or ".join(f"{name} scenario" for name in missing)
                + f"; ran: {sorted(scenarios)}"
            )
        return list(FALLBACK_SCENARIOS)
    matching = sorted(
        name
        for name, entry in scenarios.items()
        if _scenario_value(name, entry, "wave_height_m") <= condition
    )
    if not matching:
        raise MissingInput(
            f"no scenario with wave height at most {condition} m in {sorted(scenarios)}"
        )
    return matching


@guard
def run(ctx: CheckContext) -> Verdict:
    facts = read_interface(ctx)
    if "wave_tilt" not in facts:
        raise MissingInput("simulation interface has no 'wave_tilt' fact")
    scenarios = facts["wave_tilt"]
    if not isinstance(scenarios, dict):
        raise MissingInput(
            f"simulation 'wave_tilt' fact must map names to results, got {scenarios!r}"
        )
    condition = _condition_m(ctx.params or {})
    selected = _selected(scenarios, condition)
    measured = max(_scenario_value(name, scenarios[name], "max_tilt_deg") for name in selected)
    return checked_simulated_verdict(ctx, measured)
