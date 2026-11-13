"""Deterministic drag estimates for the red-team current scenarios."""

from __future__ import annotations

import math
import shutil
from pathlib import Path

import yaml

from engenia.contracts.models import StageContext
from engenia.sim.waves.solver import InputError, flooded_buoy, load_buoy, load_scenarios

REPO = Path(__file__).resolve().parents[3]
DECIMALS = 6


def load_params(ctx: StageContext) -> dict:
    local = ctx.root / "packs/buoy/sim/flow/params.yaml"
    path = local if local.is_file() else REPO / "packs/buoy/sim/flow/params.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _speed(scenario: dict, params: dict) -> float:
    value = scenario.get("extra", {}).get("current_speed_ms")
    if value is None:
        value = params["current_speed_ms"].get(
            scenario["name"], params["default_current_speed_ms"]
        )
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise InputError("current_speed_ms must be a positive finite number")
    speed = float(value)
    if not math.isfinite(speed) or speed <= 0:
        raise InputError("current_speed_ms must be a positive finite number")
    return speed


def _calculated_drag(rho: float, cd: float, area: float, speed: float) -> float:
    return 0.5 * rho * cd * area * speed**2


def run_case(case: dict, ctx: StageContext) -> dict:
    """Return per-scenario drag in N and sanity results.

    OpenFOAM is optional and is never invoked without a solver executable. The
    deterministic coefficient method is used until a configured OpenFOAM case
    supplies a mesh and force integration setup for this hull.
    """
    messages: list[str] = []
    drag: dict[str, float] = {}
    evidence: dict[str, str] = {}
    try:
        params, buoy, built = load_params(ctx), load_buoy(ctx), load_scenarios(ctx)
    except (InputError, OSError, KeyError, ValueError) as exc:
        return {"drag_n": drag, "evidence": evidence,
                "sanity": {"passed": False, "messages": [str(exc)]}}
    cd = float(params["cd"])
    if not math.isfinite(cd) or cd <= 0:
        raise InputError("flow Cd must be positive and finite")
    wanted = case.get("scenarios") or sorted(built)
    unknown = sorted(set(wanted) - set(built))
    missing = sorted(set(built) - set(wanted))
    if unknown:
        messages.append(f"case names scenarios that were not built: {unknown}")
    if missing:
        messages.append(f"case lacks built scenarios {missing}; simprep output is out of date")
    # Detection is explicit. No system solver is required by this package.
    openfoam_installed = shutil.which("simpleFoam") is not None
    for name in sorted(set(wanted) & set(built)):
        scenario = built[name]
        try:
            speed = _speed(scenario, params)
            scenario_buoy, _ = flooded_buoy(buoy, scenario)
            density = float(scenario["water_density"])
            if not math.isfinite(density) or density <= 0:
                raise InputError("water_density must be positive and finite")
            area = math.pi * buoy["radius_m"] ** 2
            draft = scenario_buoy["mass_kg"] / (density * area)
            if draft <= 0 or draft >= buoy["height_m"]:
                raise InputError("hull does not float within its height")
            projected_area = 2 * buoy["radius_m"] * draft
            force = _calculated_drag(density, cd, projected_area, speed)
            doubled = _calculated_drag(density, cd, projected_area, 2 * speed)
            if force <= 0 or not math.isclose(doubled, 4 * force, rel_tol=1e-12):
                raise InputError("drag is not positive or does not scale with speed squared")
            drag[name] = round(force, DECIMALS)
            evidence[name] = "calculated"
        except (InputError, ValueError, KeyError) as exc:
            messages.append(f"{name}: {exc}")
    return {
        "drag_n": drag,
        "evidence": evidence,
        "openfoam_available": openfoam_installed,
        "sanity": {"passed": not messages, "messages": messages},
    }
