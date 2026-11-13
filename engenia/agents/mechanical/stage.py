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


def propose_params(contract: Contract | dict, requirements: list[Requirement]) -> dict:
    """Replaceable proposal seam for T2; this sprint uses only contract budgets.

    Requirements are passed for future harness proposals. Envelope dimensions
    are ceilings, not targets. The frozen contract cannot be overridden here.
    """
    c = contract if isinstance(contract, Contract) else Contract.model_validate(contract)
    x, y, z = c.budgets.electronics.bay_mm
    wall, groove, standoff = 2.4, 2.0, 6.0
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
        raise ValueError("Battery bay and mounts exceed the mechanical envelope; revise contract")
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


class MechanicalStage:
    name = "mechanical"
    needs = ["architect"]
    reads_contract = True
    checks = CHECKS.copy()

    def generate(self, ctx: StageContext) -> None:
        contract_path = ctx.contract_path
        if not contract_path.is_file():
            contract_path = _pack_file(ctx, "default_contract.json")
        contract = Contract.model_validate_json(contract_path.read_text(encoding="utf-8"))
        requirements = [
            Requirement.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
            for path in sorted(ctx.spec_dir.glob("R-*.yaml"))
        ]
        params = propose_params(contract, requirements)
        pack_path = _pack_file(ctx, "pack.yaml")
        if pack_path.is_file():
            pack = yaml.safe_load(pack_path.read_text(encoding="utf-8"))
            params["infill_factor"] = pack.get("printing", {}).get("infill_factor", 1.0)
        ctx.src_dir.mkdir(parents=True, exist_ok=True)
        (ctx.src_dir / "params.yaml").write_text(
            yaml.safe_dump(params, sort_keys=True), encoding="utf-8"
        )
        (ctx.src_dir / "model.py").write_text(MODEL, encoding="utf-8")

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
        for check in self.checks:
            if not (ctx.root / "verifiers" / f"{check}.py").is_file():
                LOG.warning("Skipping missing mechanical verifier: %s", check)

    def interface(self, ctx: StageContext) -> dict:
        """Return facts, as required by Stage; build also publishes the envelope."""
        return json.loads((ctx.build_dir / "interface.json").read_text(encoding="utf-8"))["facts"]
