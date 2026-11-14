"""Deterministic contract-driven mechanical stage; no model service is needed."""

from __future__ import annotations

import importlib
import json
import logging
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

from engenia.contracts.models import Contract, Requirement, StageContext
from engenia.core import events
from engenia.harness.packs import pack_file, project_pack
from engenia.harness.proposer import propose_mechanical_params, proposer_fact

LOG = logging.getLogger(__name__)
REPO = Path(__file__).resolve().parents[3]
CHECKS = ["mass_budget", "buoyancy_margin", "print_bed_fit", "wall_thickness_min"]
MODEL = '''"""Generated mechanical source: all dimensions in mm."""
from pathlib import Path

import yaml

from packs.buoy.templates.hull import HullParams, build_hull

params = HullParams(**yaml.safe_load(Path(__file__).with_name("params.yaml").read_text()))
hull, lid = build_hull(params)
'''


# A pack template module exposes Params, propose(...), build(params) -> {name: Shape},
# analysis_solids(params), hardware(params) and extra_facts(params); see
# packs/pressure_housing/templates/sensor_pod.py. The worker exports every part it returns.
TEMPLATE_MODEL = '''"""Generated mechanical source: all dimensions in mm."""
from pathlib import Path

import yaml

from {module} import (
    Params,
    analysis_solids,
    build,
    extra_facts,
    hardware,
)

params = Params(**yaml.safe_load(Path(__file__).with_name("params.yaml").read_text()))
parts = build(params)
analysis = analysis_solids(params)
hardware_g = hardware(params)
extra = extra_facts(params)

contract_version = {contract_version}
template = {template!r}
'''


class EnvelopeBudgetError(ValueError):
    def __init__(self, required: list[float], available: list[float]):
        self.required = required
        super().__init__(
            f"Battery bay and mounts need envelope {required} mm; available {available} mm"
        )


def propose_params(contract: Contract | dict, requirements: list[Requirement]) -> dict:
    """Replaceable proposal seam using budgets and approved wall requirements.

    Approved wall minima set the wall thickness (strongest minimum wins).
    Envelope dimensions are ceilings, not targets; requirements cannot override
    the frozen contract's envelope.
    """
    c = contract if isinstance(contract, Contract) else Contract.model_validate(contract)
    x, y, z = c.budgets.electronics.bay_mm
    wall_limits = []
    for requirement in requirements:
        if requirement.status != "approved" or requirement.verify.check != "wall_thickness_min":
            continue
        limit = requirement.verify.limit
        if limit is None or limit.min is None:
            raise ValueError(f"{requirement.id}: wall_thickness_min requires verify.limit.min")
        if not math.isfinite(limit.min) or limit.min <= 0:
            raise ValueError(f"{requirement.id}: wall minimum must be finite and positive")
        wall_limits.append(limit.min)
    wall = max(wall_limits, default=3.0)
    groove, standoff = 2.0, 6.0
    boss_radius = 1.5 + wall
    holes = c.interfaces.mounts.board_holes_mm
    extent = max(
        math.hypot(x / 2 + wall, y / 2 + wall),
        *(math.hypot(hx - x / 2, hy - y / 2) + boss_radius for hx, hy in holes),
    )
    diameter = math.ceil(2 * (extent + 2 * wall + groove + 3.0))
    height = wall + z + standoff + 8.0
    lid_thickness = wall + 1.2
    ex, ey, ez = c.budgets.mechanical.outer_envelope_mm
    if diameter > min(ex, ey) or height + lid_thickness > ez:
        raise EnvelopeBudgetError(
            [diameter, diameter, round(height + lid_thickness, 6)], [ex, ey, ez]
        )
    return {
        "outer_diameter_mm": diameter,
        "height_mm": round(height, 6),
        "wall_thickness_mm": wall,
        "battery_bay_mm": [x, y, z],
        "board_holes_mm": [list(hole) for hole in holes],
        "lid_thickness_mm": lid_thickness,
        "o_ring_groove_width_mm": groove,
        "o_ring_groove_depth_mm": 1.2,
        "standoff_height_mm": standoff,
        "screw_hole_diameter_mm": 3.0,
        "infill_factor": 1.0,
    }


def _pack_file(ctx: StageContext, name: str) -> Path:
    return pack_file(ctx, name)


def _template_module(name: str):
    """A pack template module such as packs.pressure_housing.templates.sensor_pod."""
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    return importlib.import_module(name)


def _generate_from_template(
    ctx: StageContext, contract: Contract, requirements: list[Requirement], pack: dict,
    template: str, module_name: str,
) -> None:
    """Pack-owned templates: the module sizes the part (`propose`) and says why, and the
    generated model.py builds it. The stage adds nothing pack-specific."""
    module = _template_module(module_name)
    try:
        params, decisions = module.propose(
            contract, requirements, pack, _pack_file(ctx, "pack.yaml").parent
        )
    except ValueError as exc:
        required, available = getattr(exc, "required", None), getattr(exc, "available", None)
        if required is not None and available is not None:
            _request_budget(ctx, "budgets.mechanical.outer_envelope_mm", required, str(exc))
        raise
    for decision in decisions:
        events.emit_decision(ctx.root, ctx.stage, **decision)
    params["infill_factor"] = pack.get("printing", {}).get("infill_factor", 1.0)
    ctx.src_dir.mkdir(parents=True, exist_ok=True)
    _write_src(ctx, "params.yaml", yaml.safe_dump(params, sort_keys=True))
    _write_src(ctx, "model.py", TEMPLATE_MODEL.format(
        module=module_name, contract_version=contract.version, template=template,
    ))


def _request_budget(ctx: StageContext, path: str, requested, reason: str) -> None:
    # The board lands in PR #23. Keep this import at the call site so normal
    # mechanical builds remain usable until that dependency is merged.
    from engenia.core.board import load_requests, submit_change_request

    for _, existing in load_requests(ctx.root, ctx.project):
        if (existing.from_stage == "mechanical" and existing.path == path
                and existing.requested == requested):
            return
    submit_change_request(ctx, path=path, requested=requested, reason=reason)


def _write_src(ctx: StageContext, name: str, content: str) -> None:
    """Write a source file and log it as a code event with its diff."""
    path = ctx.src_dir / name
    try:
        old = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        old = None
    path.write_text(content, encoding="utf-8")
    events.emit_code(ctx.root, ctx.stage, name, old, content)


def _emit_param_decisions(
    ctx: StageContext, contract: Contract, requirements: list[Requirement], params: dict
) -> None:
    """Deterministic fallback: one decision event per headline parameter, from the same facts."""
    walls = [
        r for r in requirements
        if r.status == "approved" and r.verify.check == "wall_thickness_min"
    ]
    wall = params["wall_thickness_mm"]
    ex, ey, ez = contract.budgets.mechanical.outer_envelope_mm
    bay = contract.budgets.electronics.bay_mm
    if walls:
        strongest = max(walls, key=lambda r: r.verify.limit.min)
        reason = f"wall {wall:g} mm, because {strongest.id} min {strongest.verify.limit.min:g} mm"
        ids, numbers = [r.id for r in walls], {"wall_mm": wall,
                                               "min_mm": strongest.verify.limit.min}
    else:
        reason = f"wall {wall:g} mm: no approved wall requirement, so the default 3.0 mm"
        ids, numbers = [], {"wall_mm": wall}
    events.emit_decision(
        ctx.root, ctx.stage, title="Wall thickness", choice=f"{wall:g} mm", reason=reason,
        requirements=ids, numbers=numbers,
    )
    diameter, height = params["outer_diameter_mm"], params["height_mm"]
    events.emit_decision(
        ctx.root, ctx.stage, title="Outer diameter", choice=f"{diameter:g} mm",
        reason=(f"diameter {diameter:g} mm clears the {bay[0]:g} x {bay[1]:g} mm battery bay, "
                f"its mounts and the walls; the envelope allows {min(ex, ey):g} mm"),
        numbers={"outer_diameter_mm": diameter, "envelope_xy_mm": min(ex, ey)},
    )
    events.emit_decision(
        ctx.root, ctx.stage, title="Hull height", choice=f"{height:g} mm",
        reason=(f"height {height:g} mm holds the {bay[2]:g} mm bay on a "
                f"{params['standoff_height_mm']:g} mm standoff; the envelope allows {ez:g} mm "
                "with the lid"),
        numbers={"height_mm": height, "bay_height_mm": bay[2], "envelope_z_mm": ez},
    )


def _emit_previews(ctx: StageContext) -> None:
    """Copy this attempt's STL files to .engenia/previews and log a preview event for each."""
    run = events.active_run(ctx.root)
    if run is None:
        return
    attempt = events.current_attempt(ctx.root, ctx.stage)
    for stl in sorted(ctx.build_dir.glob("*.stl")):
        try:
            relative = Path("previews") / str(run) / f"{ctx.stage}-a{attempt:02d}-{stl.name}"
            target = ctx.root / ".engenia" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(stl, target)
        except OSError:
            continue
        events.emit(ctx.root, ctx.stage, "preview", {
            "stl": f"/api/previews/{run}/{target.name}", "file": relative.as_posix(),
        })


class MechanicalStage:
    name = "mechanical"
    needs = ["architect"]
    reads_contract = True
    reads_calibration = True
    checks = CHECKS.copy()

    def generate(self, ctx: StageContext) -> None:
        contract_path = ctx.contract_path
        if not contract_path.is_file():
            contract_path = _pack_file(ctx, "default_contract.json")
        contract_data = json.loads(contract_path.read_text(encoding="utf-8"))
        pack_path = _pack_file(ctx, "pack.yaml")
        pack = yaml.safe_load(pack_path.read_text(encoding="utf-8"))
        templates = pack.get("templates", {"hull": {"checks": CHECKS}})
        template = contract_data.pop("template", next(iter(templates), "hull"))
        if template not in templates:
            raise ValueError(f"Unknown mechanical template: {template}")
        contract = Contract.model_validate(contract_data)
        requirements = [
            Requirement.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
            for path in sorted(ctx.spec_dir.glob("R-*.yaml"))
        ]
        if templates[template].get("module"):
            self.checks = list(templates[template]["checks"])
            _generate_from_template(
                ctx, contract, requirements, pack, template, templates[template]["module"]
            )
            return
        try:
            params = propose_mechanical_params(ctx, contract, requirements, propose_params)
        except EnvelopeBudgetError as exc:
            _request_budget(
                ctx, "budgets.mechanical.outer_envelope_mm", exc.required, str(exc),
            )
            raise
        self.checks = list(templates[template]["checks"])
        if proposer_fact(ctx) == "deterministic":
            _emit_param_decisions(ctx, contract, requirements, params)
        params["infill_factor"] = pack.get("printing", {}).get("infill_factor", 1.0)
        ctx.src_dir.mkdir(parents=True, exist_ok=True)
        _write_src(ctx, "params.yaml", yaml.safe_dump(params, sort_keys=True))
        if project_pack(ctx) == "enclosure" and template == "deck_enclosure":
            model = (
                MODEL.replace(
                    "from packs.buoy.templates.hull import HullParams, build_hull",
                    "from packs.enclosure.templates.deck_enclosure "
                    "import DeckEnclosureParams, build_deck_enclosure",
                )
                .replace("HullParams(**", "DeckEnclosureParams(**")
                .replace(
                    "hull, lid = build_hull(params)", "body, lid = build_deck_enclosure(params)"
                )
            )
        else:
            model = (
                MODEL
                if template == "hull"
                else MODEL.replace(
                    "from packs.buoy.templates.hull import HullParams, build_hull",
                    "from packs.buoy.templates.mast_housing "
                    "import MastHousingParams, build_mast_housing",
                )
                .replace("HullParams(**", "MastHousingParams(**")
                .replace("build_hull(params)", "build_mast_housing(params)")
            )
        _write_src(
            ctx, "model.py",
            model + f"\ncontract_version = {contract.version}\n" + f"template = {template!r}\n",
        )

    def build(self, ctx: StageContext) -> None:
        density = 1.27
        pack_path = _pack_file(ctx, "pack.yaml")
        if pack_path.is_file():
            pack = yaml.safe_load(pack_path.read_text(encoding="utf-8"))
            density = float(pack["materials"]["PETG"]["density_g_cm3"])
        else:
            LOG.warning("Missing %s; using PETG density 1.27 g/cm3", pack_path)
        if not math.isfinite(density) or density <= 0:
            raise ValueError("PETG density_g_cm3 must be finite and positive")
        calibration_path = ctx.project_dir / "calibration.yaml"
        if calibration_path.is_file():
            calibration = yaml.safe_load(calibration_path.read_text(encoding="utf-8"))
            factor = calibration["mass_factor"]
            if (isinstance(factor, bool) or not isinstance(factor, (int, float))
                    or not math.isfinite(factor) or factor <= 0):
                raise ValueError("calibration mass_factor must be finite and positive")
            density *= factor

        # The scheduler hashes the entire build directory. Clear prior verdicts
        # and artifacts so reruns cannot accidentally hash a previous run.
        if ctx.build_dir.exists():
            shutil.rmtree(ctx.build_dir)

        # Isolate CAD-kernel and tessellation state between builds. PYTHONPATH
        # allows generated source to import repository pack templates.
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join(
            [str(ctx.root.resolve()), str(REPO), env.get("PYTHONPATH", "")]
        )
        subprocess.run(
            [
                sys.executable,
                "-m",
                "engenia.agents.mechanical.builder",
                str(ctx.src_dir.resolve()),
                str(ctx.build_dir.resolve()),
                str(density),
            ],
            env=env,
            check=True,
        )
        _emit_previews(ctx)
        facts = json.loads((ctx.build_dir / "interface.json").read_text(encoding="utf-8"))["facts"]
        contract_path = (ctx.contract_path if ctx.contract_path.is_file()
                         else _pack_file(ctx, "default_contract.json"))
        contract_data = json.loads(contract_path.read_text(encoding="utf-8"))
        contract_data.pop("template", None)
        budget = Contract.model_validate(contract_data).budgets.mechanical.mass_g
        if facts["dry_mass_g"] > budget:
            _request_budget(
                ctx, "budgets.mechanical.mass_g", math.ceil(facts["dry_mass_g"]),
                f"Calculated mechanical mass {facts['dry_mass_g']} g exceeds {budget:g} g budget",
            )
        for check in self.checks:
            if not (ctx.root / "verifiers" / f"{check}.py").is_file():
                LOG.warning("Skipping missing mechanical verifier: %s", check)

    def interface(self, ctx: StageContext) -> dict:
        """Return facts, as required by Stage; build also publishes the envelope."""
        facts = json.loads((ctx.build_dir / "interface.json").read_text(encoding="utf-8"))["facts"]
        facts["proposer"] = proposer_fact(ctx)
        return facts
