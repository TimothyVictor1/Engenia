"""run_case for the waves solver. Lengths in metres, masses in kilograms, angles in degrees."""

from __future__ import annotations

import json
import math
from pathlib import Path

import capytaine as cpt
import numpy as np
import xarray as xr
import yaml

from engenia.contracts.models import StageContext
from engenia.sim.waves import hull

REPO = Path(__file__).resolve().parents[3]
DOFS = ("Surge", "Heave", "Pitch")
MAX_TILT_DEG = 90.0
DECIMALS = 6  # published digits; hides last-bit differences between machines


class InputError(ValueError):
    """An upstream fact or scenario the solver needs is missing or unusable."""


def load_params(ctx: StageContext) -> dict:
    local = ctx.root / "packs" / "buoy" / "sim" / "waves" / "params.yaml"
    path = local if local.is_file() else REPO / "packs" / "buoy" / "sim" / "waves" / "params.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _facts(ctx: StageContext, stage: str) -> dict:
    try:
        data = json.loads(ctx.stage_interface_path(stage).read_text(encoding="utf-8"))
        return data["facts"]
    except (OSError, ValueError, KeyError) as exc:
        raise InputError(f"no usable {stage} interface ({type(exc).__name__})") from exc


def _vector(value, name: str) -> list[float]:
    ok = (
        isinstance(value, list) and len(value) == 3
        and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                for v in value)
    )
    if not ok:
        raise InputError(f"{name} is missing or not three finite numbers")
    return [float(v) for v in value]


def load_buoy(ctx: StageContext) -> dict:
    """Radius, height, mass and centre-of-mass height of the assembled buoy, in SI units.

    Size comes from the mechanical bounding box (diameter in x/y, height in z), mass and centre of
    mass from integration. Where integration could not place the centre of mass (it needs the
    battery bay position), the hull's own centre of mass is used and `com_source` says so. The
    frame has z = 0 at the bottom of the hull, pointing up.
    """
    mechanical, integration = _facts(ctx, "mechanical"), _facts(ctx, "integration")
    bbox = _vector(mechanical.get("bbox_mm"), "mechanical bbox_mm")
    mass_g = integration.get("total_mass_g")
    if not isinstance(mass_g, (int, float)) or isinstance(mass_g, bool) or not mass_g > 0:
        raise InputError("integration total_mass_g is missing or not positive")
    if integration.get("centre_of_mass_mm") is not None:
        centre, source = _vector(integration["centre_of_mass_mm"], "integration centre"), "assembly"
    else:
        centre, source = _vector(mechanical.get("centre_of_mass_mm"), "mechanical centre"), "hull"
    return {
        "radius_m": max(bbox[0], bbox[1]) / 2000.0,
        "height_m": bbox[2] / 1000.0,
        "mass_kg": mass_g / 1000.0,
        "com_z_m": centre[2] / 1000.0,
        "com_source": source,
    }


def load_scenarios(ctx: StageContext) -> dict[str, dict]:
    directory = ctx.stage_build_dir("redteam") / "scenarios"
    scenarios = {}
    for path in sorted(directory.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        scenarios[data["name"]] = data
    if not scenarios:
        raise InputError("no red-team scenarios were built")
    return scenarios


def mesh_messages(mesh: cpt.Mesh, level1_volume_m3: float, tolerance: float) -> list[str]:
    """Why the mesh cannot be trusted: not closed, or displaced volume off the level 1 value."""
    messages = []
    if not hull.is_closed(mesh):
        messages.append("hull mesh is not closed")
    volume = hull.immersed_volume(mesh)
    error = abs(volume - level1_volume_m3) / level1_volume_m3
    if error > tolerance:
        messages.append(
            f"mesh displaced volume {volume * 1e6:.1f} cm3 differs from mass/density "
            f"{level1_volume_m3 * 1e6:.1f} cm3 by {error * 100:.1f} percent "
            f"(limit {tolerance * 100:.0f})"
        )
    return messages


def pitch_rao(mesh: cpt.Mesh, buoy: dict, draft: float, scenario: dict, g: float) -> tuple:
    """Pitch RAO (rad per metre of wave amplitude) and pitch stiffness (N m / rad).

    Surge, heave and pitch are solved together, pitch taken about the centre of mass. Mass and
    inertia are those of a uniform cylinder of the assembled size; stiffness is from the mesh.
    """
    rho, mass = scenario["water_density"], buoy["mass_kg"]
    radius, height = buoy["radius_m"], buoy["height_m"]
    zg = buoy["com_z_m"] - draft  # waterline is z = 0
    body = cpt.FloatingBody(mesh=mesh.immersed_part(), center_of_mass=(0, 0, zg), mass=mass)
    body.add_translation_dof(direction=(1, 0, 0), name="Surge")
    body.add_translation_dof(direction=(0, 0, 1), name="Heave")
    body.add_rotation_dof(rotation_center=(0, 0, zg), direction=(0, 1, 0), name="Pitch")

    omega = 2 * math.pi / scenario["wave_period_s"]
    matrix = xr.Dataset(coords={
        "omega": [omega], "wave_direction": [0.0], "radiating_dof": list(DOFS),
        "water_depth": [scenario["depth_m"]], "rho": [rho], "g": [g],
    })
    data = cpt.BEMSolver().fill_dataset(matrix, body, hydrostatics=False)
    offset = buoy["com_z_m"] - height / 2  # centre of mass below or above the cylinder centre
    inertia_yy = mass * (3 * radius**2 + height**2) / 12 + mass * offset**2
    dims = {"influenced_dof": list(DOFS), "radiating_dof": list(DOFS)}
    data["inertia_matrix"] = xr.DataArray(
        np.diag([mass, mass, inertia_yy]), coords=dims, dims=tuple(dims)
    )
    stiffness = body.compute_hydrostatic_stiffness(rho=rho, g=g)
    data["hydrostatic_stiffness"] = stiffness
    rao = cpt.post_pro.rao(data).sel(radiating_dof="Pitch")
    pitch_stiffness = stiffness.sel(influenced_dof="Pitch", radiating_dof="Pitch")
    return float(abs(rao).item()), float(pitch_stiffness)


def run_case(case: dict, ctx: StageContext) -> dict:
    """Max tilt per scenario: |pitch RAO at the scenario period| x wave amplitude, in degrees.

    Wave amplitude is half the significant wave height. Returns `wave_tilt`, `sanity` and `notes`.
    Bad or missing inputs, and results that fail a sanity check, give `sanity.passed` False.
    """
    messages: list[str] = []
    wave_tilt: dict[str, dict] = {}
    notes: list[str] = []
    try:
        params, buoy = load_params(ctx), load_buoy(ctx)
        built = load_scenarios(ctx)
    except InputError as exc:
        return {"sanity": {"passed": False, "messages": [str(exc)]}}

    wanted = case.get("scenarios") or sorted(built)
    unknown = sorted(set(wanted) - set(built))
    missing = sorted(set(built) - set(wanted))
    if unknown:
        messages.append(f"case names scenarios that were not built: {unknown}")
    if missing:
        messages.append(f"case lacks built scenarios {missing}; simprep output is out of date")
    if buoy["com_source"] == "hull":
        notes.append("centre of mass is the hull's own: integration could not place the assembly")

    g = params["gravity_m_s2"]
    for name in sorted(set(wanted) & set(built)):
        scenario = built[name]
        area = math.pi * buoy["radius_m"] ** 2
        level1 = buoy["mass_kg"] / scenario["water_density"]  # level 1: weight = buoyancy
        draft = level1 / area
        problems = []
        if draft >= buoy["height_m"]:
            problems.append(f"hull does not float: draft {draft * 1000:.1f} mm reaches the top")
        elif draft >= scenario["depth_m"]:
            problems.append("draft reaches the sea bed")
        if not problems:
            mesh = hull.build_mesh(buoy["radius_m"], buoy["height_m"], draft,
                                   params["mesh_resolution"])
            problems += mesh_messages(mesh, level1, params["displaced_volume_tolerance"])
        if not problems:
            rao, pitch_stiffness = pitch_rao(mesh, buoy, draft, scenario, g)
            tilt = math.degrees(rao * scenario["wave_height_m"] / 2)
            if pitch_stiffness <= 0:
                problems.append("hull is not statically stable in pitch")
            if not math.isfinite(tilt) or not 0 <= tilt <= MAX_TILT_DEG:
                problems.append(f"max tilt {tilt} deg is outside 0 to {MAX_TILT_DEG:.0f}")
            if not problems:
                wave_tilt[name] = {
                    "wave_height_m": scenario["wave_height_m"],
                    "max_tilt_deg": round(tilt, DECIMALS),
                }
        messages += [f"{name}: {p}" for p in problems]

    return {
        "wave_tilt": wave_tilt,
        "notes": notes,
        "sanity": {"passed": not messages, "messages": messages},
    }
