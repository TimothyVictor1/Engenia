"""PETG hull stress analysis using a STEP tetrahedral mesh and scikit-fem.

Two load models share one solver. The buoy hull is a cup closed by a lid: the cylinder and the
bottom are wet and the lid hands the bottom force back through its rim. A pack whose mechanical
interface lists `structure_dry_regions_mm` (the pressure pod) is a closed vessel loaded on every
wet face, which balances by itself. The material, the mesh size and the water density come from
the project's own pack (`structure:` in pack.yaml); the buoy values are the defaults.
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import yaml
from scipy.sparse.linalg import spsolve
from skfem import Basis, ElementTetP1, ElementVector, FacetBasis, LinearForm, MeshTet, asm, condense
from skfem.helpers import dot
from skfem.models.elasticity import linear_elasticity

from engenia.harness.packs import project_pack
from engenia.sim.depth import parse_depth_m, pressure_mpa

MESH_SIZE_MM = 5.0
WATER_DENSITY_KG_M3 = 1005.0
DRY_TOLERANCE_MM = 0.2


def _requirement_file(ctx, case: dict):
    """The requirement the case answers: the case's own, else the one whose check is
    stress_safety_factor, else R-012 (the buoy's, and what older fixtures carry)."""
    named = case.get("requirement")
    if named and (ctx.spec_dir / f"{named}.yaml").is_file():
        return ctx.spec_dir / f"{named}.yaml"
    for path in sorted(ctx.spec_dir.glob("R-*.yaml")):
        data = yaml.safe_load(path.read_text()) or {}
        if (data.get("verify") or {}).get("check") == "stress_safety_factor":
            return path
    return ctx.spec_dir / "R-012.yaml"


def _depth(ctx, case: dict | None = None) -> tuple[float, str]:
    path = _requirement_file(ctx, case or {})
    requirement = yaml.safe_load(path.read_text())
    depth = parse_depth_m(requirement["statement"])
    if depth is None:
        raise ValueError(f"{path.stem} must state a depth in metres")
    return depth, path.stem


def _mesh(step: Path, size_mm: float = MESH_SIZE_MM) -> MeshTet:
    # gmsh runs in a fresh interpreter: its bundled OpenCASCADE crashes the process
    # if it shares one with build123d's OCP. This module must never import gmsh.
    with tempfile.TemporaryDirectory() as work:
        request, result = Path(work) / "request.json", Path(work) / "mesh.json"
        request.write_text(json.dumps({"step": str(step), "mesh_size_mm": size_mm}))
        proc = subprocess.run(
            [sys.executable, "-m", "engenia.sim.structure._mesh_worker",
             str(request), str(result)],
            capture_output=True, text=True, check=False,
        )
        if proc.returncode != 0 or not result.exists():
            raise RuntimeError(
                f"gmsh meshing process failed (exit {proc.returncode}): "
                f"{proc.stderr.strip()[-500:]}"
            )
        data = json.loads(result.read_text())
    if data.get("error") == "import":
        raise RuntimeError(
            "structural simulation not_run: gmsh needs its system libraries, including GLU"
        )
    if not data["tetrahedra"]:
        raise ValueError("STEP meshing produced no linear tetrahedra")
    coordinates = np.asarray(data["coordinates"], dtype=float)
    return MeshTet(coordinates.reshape(-1, 3).T, np.asarray(data["tetrahedra"]).T)


def _tetra_volumes(mesh: MeshTet) -> np.ndarray:
    xyz = mesh.p[:, mesh.t]
    edges = xyz[:, 1:] - xyz[:, :1]
    return np.linalg.det(np.moveaxis(edges, -1, 0)) / 6.0


def _material(ctx) -> dict:
    """The `structure:` block of the project's pack, else the buoy pack's."""
    repo = Path(__file__).resolve().parents[3]
    for name in dict.fromkeys((project_pack(ctx), "buoy")):
        for base in (Path(ctx.root), repo):
            path = base / "packs" / name / "pack.yaml"
            if path.is_file():
                structure = (yaml.safe_load(path.read_text()) or {}).get("structure")
                if structure:
                    return structure
    raise FileNotFoundError("no pack.yaml with a structure: block")


def _scenarios(ctx, requirement: str) -> dict[str, dict]:
    """Built red-team scenarios that target the requirement, by name."""
    found = {}
    for path in sorted((ctx.stage_build_dir("redteam") / "scenarios").glob("*.yaml")):
        data = yaml.safe_load(path.read_text()) or {}
        if requirement in data.get("targets", []):
            found[data["name"]] = data
    return found


def _facts(ctx) -> dict:
    path = ctx.stage_build_dir("mechanical") / "interface.json"
    return json.loads(path.read_text())["facts"] if path.exists() else {}


def _geometry(ctx, mesh: MeshTet) -> tuple[float, float]:
    facts = _facts(ctx)
    if facts:
        return facts["bbox_mm"][0] / 2, facts["wall_min_mm"]
    # Fixture projects can provide the same dimensions through mechanical params.
    path = ctx.project_dir / "src/mechanical/params.yaml"
    params = yaml.safe_load(path.read_text())
    return params["outer_diameter_mm"] / 2, params["wall_thickness_mm"]


def run_case(case: dict, ctx) -> dict:
    """Analyze the mechanical hull; case can set fixture load imbalance for sanity testing."""
    depth, requirement_id = _depth(ctx, case)
    material = _material(ctx)
    facts = _facts(ctx)
    dry_regions = facts.get("structure_dry_regions_mm")  # a closed vessel; else the buoy hull
    mesh = _mesh(
        ctx.stage_build_dir("mechanical") / facts.get("structure_step", "hull.step"),
        float(material.get("mesh_size_mm", MESH_SIZE_MM)),
    )
    volumes = _tetra_volumes(mesh)
    messages: list[str] = []
    if np.any(np.abs(volumes) < 1e-9) or np.any(~np.isfinite(volumes)):
        messages.append("Mesh has degenerate tetrahedra")
    # scikit-fem normalizes tetrahedron orientation; signed volume is checked after this.
    if np.any(volumes <= 0):
        messages.append("Mesh has inverted tetrahedra")
    radius, wall = _geometry(ctx, mesh)
    modulus = float(material["youngs_modulus_mpa"])
    nu = float(material["poisson_ratio"])
    shear = modulus / (2 * (1 + nu))
    lame = modulus * nu / ((1 + nu) * (1 - 2 * nu))
    element = ElementVector(ElementTetP1())
    basis = Basis(mesh, element)
    stiffness = asm(linear_elasticity(lame, shear), basis)
    facets = mesh.boundary_facets()
    centers = mesh.p[:, mesh.facets[:, facets]].mean(axis=1)
    radial = np.linalg.norm(centers[:2], axis=0)
    zmin, zmax = mesh.p[2].min(), mesh.p[2].max()
    if dry_regions is None:
        # The cylindrical exterior and bottom are wetted; the cavity is dry.
        wet = facets[(radial > radius - wall * 0.5) | (centers[2] < zmin + 0.1)]
        top = facets[centers[2] > zmax - 0.1]
    else:
        # A closed vessel: every boundary face is wet except the ones inside a dry region
        # (the cavity and the O-ring grooves), given as (r_min, r_max, z_min, z_max) boxes.
        dry = np.zeros(len(facets), dtype=bool)
        for r0, r1, z0, z1 in dry_regions:
            dry |= (
                (radial >= r0 - DRY_TOLERANCE_MM) & (radial <= r1 + DRY_TOLERANCE_MM)
                & (centers[2] >= z0 - DRY_TOLERANCE_MM) & (centers[2] <= z1 + DRY_TOLERANCE_MM)
            )
        wet, top = facets[~dry], facets[:0]
    base_density = float(material.get("water_density_kg_m3", WATER_DENSITY_KG_M3))
    pressure = pressure_mpa(base_density, depth)
    imbalance = float(case.get("load_imbalance_factor", 1.0))
    wet_basis = FacetBasis(mesh, element, facets=wet)

    @LinearForm
    def pressure_load(v, w):
        return -pressure * dot(w.n, v)

    load = asm(pressure_load, wet_basis)
    # The attached lid transmits the opposing bottom force through the rim.
    # Its traction is distributed by facet area to avoid nodal point loads.
    if dry_regions is None:
        bottom_force = load.reshape(-1, 3).sum(axis=0)[2]
        top_basis = FacetBasis(mesh, element, facets=top)
        top_area = float(asm(LinearForm(lambda v, w: v[2]), top_basis).reshape(-1, 3)[:, 2].sum())

        @LinearForm
        def lid_load(v, w):
            return -bottom_force / top_area * v[2]

        load += asm(lid_load, top_basis)
    else:
        # Pressure on the closed ends is balanced by itself; the reference for the balance
        # check is the force on the end face of the outer cylinder.
        bottom_force = pressure * math.pi * radius**2
    # Six independent constraints remove the rigid body modes while leaving
    # the cylinder free to contract under radial hydrostatic pressure.
    a = int(np.argmin(np.linalg.norm(mesh.p - np.array([[radius], [0], [zmin]]), axis=0)))
    b = int(np.argmin(np.linalg.norm(mesh.p - np.array([[-radius], [0], [zmin]]), axis=0)))
    c = int(np.argmin(np.linalg.norm(mesh.p - np.array([[0], [radius], [zmax]]), axis=0)))
    fixed = np.array([*basis.nodal_dofs[:, a], basis.nodal_dofs[1, b],
                      basis.nodal_dofs[2, b], basis.nodal_dofs[2, c]])
    solution = np.zeros(basis.N)
    solution[basis.complement_dofs(fixed)] = spsolve(*condense(stiffness, load, D=fixed)[:2])
    reaction = stiffness @ solution - load
    # Balance is computed on the complete load vector, before any fixture
    # corruption, and on the constrained reaction vector.
    applied = load.reshape(-1, 3).sum(axis=0)
    supported = np.bincount(fixed % 3, weights=reaction[fixed], minlength=3)
    applied[2] += bottom_force * (imbalance - 1)
    balance_error = np.linalg.norm(applied + supported) / max(abs(bottom_force), 1e-12)
    if balance_error > 0.02:
        messages.append(f"Reaction forces do not balance applied load ({balance_error:.1%})")
    # Constant strain in each P1 tetrahedron. Derivatives from the affine
    # element map avoid quadrature-dependent stress maxima.
    displacement = solution.reshape(-1, 3).T
    xyz = mesh.p[:, mesh.t]
    edges = np.moveaxis(xyz[:, 1:] - xyz[:, :1], -1, 0)
    delta = np.moveaxis(displacement[:, mesh.t[1:]] - displacement[:, mesh.t[:1]], -1, 0)
    grad = np.matmul(delta, np.linalg.inv(edges))
    strain = (grad + np.swapaxes(grad, 1, 2)) / 2
    trace = np.trace(strain, axis1=1, axis2=2)[:, None, None]
    stress = 2 * shear * strain + lame * trace * np.eye(3)
    deviator = stress - np.trace(stress, axis1=1, axis2=2)[:, None, None] * np.eye(3) / 3
    von_mises = np.sqrt(1.5 * np.sum(deviator**2, axis=(1, 2)))
    maximum = float(von_mises.max())
    mid = mesh.p[:, mesh.t].mean(axis=1)
    side = (
        (np.linalg.norm(mid[:2], axis=0) > radius - 2 * wall)
        & (mid[2] > zmin + (zmax - zmin) / 3)
        & (mid[2] < zmin + 2 * (zmax - zmin) / 3)
    )
    shell_stress = float(von_mises[side].max())
    hoop = pressure * radius / wall
    if not math.isfinite(shell_stress) or abs(shell_stress - hoop) / hoop > 0.5:
        messages.append(
            "Mid-shell stress differs from thin-wall cylinder estimate by more than 50%"
        )
    allowable = float(material["allowable_stress_mpa"]) * float(
        material["printed_knock_down_factor"]
    )
    result = {
        "safety_factor": float(allowable / maximum),
        "max_von_mises_mpa": maximum,
        "structure_depth_m": depth,
        "sanity": {"passed": not messages, "messages": messages},
    }
    # The problem is linear, so a scenario with another water density or depth scales the
    # stress by its pressure ratio: no second solve is needed. When the pack says so, the
    # published safety factor is the worst of the design depth and these scenarios.
    by_scenario = {}
    for name, scenario in _scenarios(ctx, requirement_id).items():
        density = float(scenario.get("water_density") or base_density)
        factor = float((scenario.get("extra") or {}).get("depth_factor", 1.0))
        if density == base_density and factor == 1.0:
            continue
        ratio = pressure_mpa(density, depth * factor) / pressure
        by_scenario[name] = float(allowable / (maximum * ratio))
    if by_scenario:
        result["safety_factor_by_scenario"] = by_scenario
        if material.get("scenarios_govern"):
            result["safety_factor"] = min(result["safety_factor"], *by_scenario.values())
    return result
