"""Deterministic contract-driven mechanical stage; no model service is needed."""

from __future__ import annotations

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
    local = ctx.root / "packs" / "buoy" / name
    return local if local.is_file() else REPO / "packs" / "buoy" / name


def _request_budget(ctx: StageContext, path: str, requested, reason: str) -> None:
    # The board lands in PR #23. Keep this import at the call site so normal
    # mechanical builds remain usable until that dependency is merged.
    from engenia.core.board import load_requests, submit_change_request

    for _, existing in load_requests(ctx.root, ctx.project):
        if (existing.from_stage == "mechanical" and existing.path == path
                and existing.requested == requested):
            return
    submit_change_request(ctx, path=path, requested=requested, reason=reason)


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
        template = contract_data.pop("template", "hull")
        contract = Contract.model_validate(contract_data)
        requirements = [
            Requirement.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
            for path in sorted(ctx.spec_dir.glob("R-*.yaml"))
        ]
        try:
            params = propose_mechanical_params(ctx, contract, requirements, propose_params)
        except EnvelopeBudgetError as exc:
            _request_budget(
                ctx, "budgets.mechanical.outer_envelope_mm", exc.required, str(exc),
            )
            raise
        pack_path = _pack_file(ctx, "pack.yaml")
        if pack_path.is_file():
            pack = yaml.safe_load(pack_path.read_text(encoding="utf-8"))
            templates = pack.get("templates", {"hull": {"checks": CHECKS}})
            if template not in templates:
                raise ValueError(f"Unknown mechanical template: {template}")
            self.checks = list(templates[template]["checks"])
            params["infill_factor"] = pack.get("printing", {}).get("infill_factor", 1.0)
        ctx.src_dir.mkdir(parents=True, exist_ok=True)
        (ctx.src_dir / "params.yaml").write_text(
            yaml.safe_dump(params, sort_keys=True), encoding="utf-8"
        )
        (ctx.src_dir / "model.py").write_text(
            (MODEL if template == "hull" else MODEL.replace(
                "from packs.buoy.templates.hull import HullParams, build_hull",
                "from packs.buoy.templates.mast_housing "
                "import MastHousingParams, build_mast_housing",
            ).replace("HullParams(**", "MastHousingParams(**").replace(
                "build_hull(params)", "build_mast_housing(params)"
            )) + f"\ncontract_version = {contract.version}\n"
            + f"template = {template!r}\n", encoding="utf-8"
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
                sys.executable, "-m", "engenia.agents.mechanical.builder",
                str(ctx.src_dir.resolve()), str(ctx.build_dir.resolve()), str(density),
            ],
            env=env,
            check=True,
        )
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
