"""End-to-end structural checks on an isolated cylindrical hull fixture."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
import yaml
from build123d import Align, Cylinder, export_step

from engenia.contracts.models import StageContext
from engenia.sim.structure import run_case

pytestmark = pytest.mark.slow


def _fixture(tmp_path: Path) -> StageContext:
    project = tmp_path / "projects" / "fixture"
    spec = project / "spec"
    build = project / "build" / "mechanical"
    source = project / "src" / "mechanical"
    pack = tmp_path / "packs" / "buoy"
    for folder in (spec, build, source, pack):
        folder.mkdir(parents=True)
    (spec / "R-012.yaml").write_text(
        "id: R-012\nstatement: The hull shall survive at 3 m depth.\n"
    )
    (pack / "pack.yaml").write_text(
        yaml.safe_dump({"structure": {"youngs_modulus_mpa": 2100,
                                      "poisson_ratio": 0.38,
                                      "allowable_stress_mpa": 35,
                                      "printed_knock_down_factor": 0.6}})
    )
    (source / "params.yaml").write_text(
        yaml.safe_dump({"outer_diameter_mm": 60, "wall_thickness_mm": 3})
    )
    align = (Align.CENTER, Align.CENTER, Align.MIN)
    hull = Cylinder(30, 30, align=align) - Cylinder(27, 31, align=align).translate((0, 0, 3))
    export_step(hull, build / "hull.step")
    return StageContext(project="fixture", stage="simulation", root=tmp_path)


def test_depth_scaling_determinism_and_balance(tmp_path):
    ctx = _fixture(tmp_path)
    start = time.monotonic()
    first = run_case({}, ctx)
    assert time.monotonic() - start < 300
    assert first == run_case({}, ctx)
    assert first["sanity"]["passed"], first["sanity"]["messages"]
    path = ctx.spec_dir / "R-012.yaml"
    path.write_text(path.read_text().replace("3 m", "6 m"))
    double = run_case({}, ctx)
    assert abs(double["max_von_mises_mpa"] / first["max_von_mises_mpa"] - 2) < 0.02
    assert double["structure_depth_m"] == 6
    bad = run_case({"load_imbalance_factor": 1.1}, ctx)
    assert not bad["sanity"]["passed"]
    assert any("Reaction" in msg for msg in bad["sanity"]["messages"])


def test_full_hull_under_five_minutes(tmp_path):
    from engenia.agents.mechanical.stage import propose_params
    from engenia.contracts.models import Contract
    from packs.buoy.templates.hull import HullParams, build_hull

    ctx = _fixture(tmp_path)
    contract_path = Path(__file__).resolve().parents[3] / "packs/buoy/default_contract.json"
    params = propose_params(Contract.model_validate_json(contract_path.read_text()), [])
    (ctx.project_dir / "src/mechanical/params.yaml").write_text(yaml.safe_dump(params))
    hull, _ = build_hull(HullParams(**params))
    export_step(hull, ctx.stage_build_dir("mechanical") / "hull.step")
    start = time.monotonic()
    result = run_case({}, ctx)
    assert time.monotonic() - start < 300
    assert result["sanity"]["passed"], result["sanity"]["messages"]


def test_build123d_then_structure_case_in_one_process_does_not_crash(tmp_path):
    """Regression: OCP (build123d) and gmsh's OpenCASCADE segfault if they share a process."""
    import subprocess
    import sys

    code = (
        "import sys, pathlib; sys.path.insert(0, 'tests/sim/structure');"
        "import importlib.util as u;"
        "s = u.spec_from_file_location('ts', 'tests/sim/structure/test_solver.py');"
        "m = u.module_from_spec(s); s.loader.exec_module(m);"
        "ctx = m._fixture(pathlib.Path(sys.argv[1]));"
        "from engenia.sim.structure import run_case;"
        "assert run_case({}, ctx)['sanity']['passed'];"
        "assert 'gmsh' not in sys.modules"
    )
    done = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)], capture_output=True, text=True
    )
    assert done.returncode == 0, done.stderr[-800:]
