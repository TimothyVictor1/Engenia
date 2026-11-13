"""Isolated CAD worker, deliberately free of proposal logic."""

from __future__ import annotations

import hashlib
import json
import math
import runpy
import sys
from io import BytesIO
from pathlib import Path

from build123d import CenterOf, Compound, export_step, export_stl

from engenia.contracts.models import Interface

ARTIFACTS = ("hull.step", "lid.step", "hull.stl", "lid.stl")
STEP_TIMESTAMP = "2000-01-01T00:00:00"
STL_TOLERANCE_MM = 0.01
STL_ANGULAR_TOLERANCE = 0.1


def build(src_dir: Path, build_dir: Path, density_g_cm3: float) -> None:
    model = runpy.run_path(str(src_dir / "model.py"))
    hull, lid, params = model["hull"], model["lid"], model["params"]
    build_dir.mkdir(parents=True, exist_ok=True)
    for name, solid in (("hull", hull), ("lid", lid)):
        # Stream export avoids absolute output paths in FILE_NAME; timestamp
        # fixes the kernel's date field before writing rather than patching it.
        step = BytesIO()
        if not export_step(solid, step, timestamp=STEP_TIMESTAMP):
            raise RuntimeError(f"STEP export failed: {name}")
        (build_dir / f"{name}.step").write_bytes(step.getvalue())
        stl_path = build_dir / f"{name}.stl"
        if not export_stl(
            solid, stl_path, tolerance=STL_TOLERANCE_MM,
            angular_tolerance=STL_ANGULAR_TOLERANCE, ascii_format=False,
        ):
            raise RuntimeError(f"STL export failed: {name}")
        # Binary STL's 80-byte comment has no geometric meaning. Do not change
        # triangle count or mesh data when replacing the kernel's comment.
        data = stl_path.read_bytes()
        stl_path.write_bytes(b"Engenia deterministic STL (mm)".ljust(80, b"\0") + data[80:])

    assembly = Compound(children=[hull, lid])
    size, centre = assembly.bounding_box().size, assembly.center(CenterOf.MASS)
    volume = hull.volume + lid.volume
    closed_volume = math.pi * (params.outer_diameter_mm / 2) ** 2 * (
        params.height_mm + params.lid_thickness_mm
    )
    facts = {
        "contract_version": model["contract_version"],
        "dry_mass_g": round(volume / 1000 * density_g_cm3 * params.infill_factor, 6),
        "displaced_volume_cm3": round(closed_volume / 1000, 6),
        "internal_volume_cm3": round((closed_volume - volume) / 1000, 6),
        "battery_bay_mm": list(params.battery_bay_mm),
        "wall_min_mm": params.wall_thickness_mm,
        "bbox_mm": [round(v, 6) for v in size],
        "centre_of_mass_mm": [round(v, 6) for v in centre],
    }
    digest = hashlib.sha256(json.dumps(facts, sort_keys=True).encode())
    for name in ARTIFACTS:
        digest.update(name.encode())
        digest.update((build_dir / name).read_bytes())
    interface = Interface(stage="mechanical", key=f"sha256:{digest.hexdigest()}", facts=facts)
    (build_dir / "interface.json").write_text(
        json.dumps(interface.model_dump(mode="json"), sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    build(Path(sys.argv[1]), Path(sys.argv[2]), float(sys.argv[3]))
