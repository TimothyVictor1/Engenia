"""Exercise real OCCT geometry and independent deterministic build processes."""

import hashlib
import json
import math
import shutil
import struct
from dataclasses import replace

import pytest
import yaml
from build123d import Align, Box, CenterOf, Cylinder, Pos, import_step

from engenia.agents.mechanical.builder import ARTIFACTS, STEP_TIMESTAMP
from engenia.agents.mechanical.stage import CHECKS, REPO, MechanicalStage, propose_params
from engenia.contracts.models import Contract, Interface, StageContext
from engenia.stages.base import Stage
from packs.buoy.templates.hull import HullParams, build_hull


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    root = tmp_path_factory.mktemp("mechanical")
    ctx = StageContext(root=root, project="buoy", stage="mechanical")
    stage = MechanicalStage()
    stage.generate(ctx)
    stage.build(ctx)
    return stage, ctx


def clone_source(ctx, project):
    clone = StageContext(root=ctx.root, project=project, stage="mechanical")
    shutil.copytree(ctx.src_dir, clone.src_dir)
    return clone


def default_contract():
    return Contract.model_validate_json((REPO / "packs/buoy/default_contract.json").read_text())


def hashes(ctx):
    return {name: hashlib.sha256((ctx.build_dir / name).read_bytes()).hexdigest()
            for name in (*ARTIFACTS, "interface.json")}


def test_build_produces_real_artifacts_and_valid_interface(built):
    stage, ctx = built
    assert isinstance(stage, Stage)
    assert stage.checks == CHECKS
    interface = Interface.model_validate_json((ctx.build_dir / "interface.json").read_text())
    assert interface.stage == "mechanical"
    assert stage.interface(ctx) == interface.facts
    assert set(interface.facts) == {
        "dry_mass_g", "displaced_volume_cm3", "internal_volume_cm3", "battery_bay_mm",
        "wall_min_mm", "bbox_mm", "centre_of_mass_mm",
    }
    for name in ("hull", "lid"):
        solid = import_step(ctx.build_dir / f"{name}.step")
        assert solid.is_valid and len(solid.solids()) == 1 and solid.volume > 0
        data = (ctx.build_dir / f"{name}.stl").read_bytes()
        triangle_count, = struct.unpack("<I", data[80:84])
        assert triangle_count > 0
        assert len(data) == 84 + 50 * triangle_count


def test_two_builds_from_same_source_have_identical_hashes(built, caplog):
    stage, ctx = built
    # Fresh worker, different directory, byte-for-byte source copy, no generate().
    clone = clone_source(ctx, "reproduction")
    stage.build(clone)
    assert hashes(clone) == hashes(ctx)
    first = hashes(clone)
    (clone.build_dir / "verdict.json").write_text("[]\n")
    (clone.build_dir / "stale-artifact.txt").write_text("old build")
    stage.build(clone)
    assert hashes(clone) == first
    assert not (clone.build_dir / "verdict.json").exists()
    assert not (clone.build_dir / "stale-artifact.txt").exists()
    for check in CHECKS:
        assert f"Skipping missing mechanical verifier: {check}" in caplog.text
    for name in ("hull", "lid"):
        step = (clone.build_dir / f"{name}.step").read_text()
        assert STEP_TIMESTAMP in step
        assert str(clone.build_dir) not in step


def test_taller_battery_bay_increases_internal_volume(built):
    stage, ctx = built
    changed = StageContext(root=ctx.root, project="taller-bay", stage="mechanical")
    contract = default_contract().model_dump(mode="json")
    contract["budgets"]["electronics"]["bay_mm"][2] = 26
    changed.contract_path.parent.mkdir(parents=True)
    changed.contract_path.write_text(json.dumps(contract))
    stage.generate(changed)
    stage.build(changed)
    original, taller = stage.interface(ctx), stage.interface(changed)
    assert taller["battery_bay_mm"] == [70, 40, 26]
    assert taller["internal_volume_cm3"] > original["internal_volume_cm3"]
    assert taller["bbox_mm"][2] == pytest.approx(original["bbox_mm"][2] + 4)
    assert hashes(changed) != hashes(ctx)


def test_default_bbox_fits_print_bed_and_contract(built):
    stage, ctx = built
    size = stage.interface(ctx)["bbox_mm"]
    assert all(0 < d <= limit for d, limit in zip(size, [220, 220, 250], strict=True))
    envelope = default_contract().budgets.mechanical.outer_envelope_mm
    assert all(dimension <= limit for dimension, limit in zip(size, envelope, strict=True))


def test_wall_min_matches_source_params_and_actual_shell(built):
    stage, ctx = built
    params = yaml.safe_load((ctx.src_dir / "params.yaml").read_text())
    wall = params["wall_thickness_mm"]
    assert stage.interface(ctx)["wall_min_mm"] == wall
    # Probe the ordinary sidewall away from the battery well and sealing rim.
    r = params["outer_diameter_mm"] / 2
    shell = import_step(ctx.build_dir / "hull.step")
    z = params["height_mm"] - 5
    inside = Pos(r - wall - 0.1, 0, z) * Box(0.1, 0.1, 0.1)
    material = Pos(r - wall + 0.1, 0, z) * Box(0.1, 0.1, 0.1)
    assert (shell & inside) is None
    assert (shell & material).volume == pytest.approx(0.001, abs=1e-8)


def test_geometric_facts_and_mass_match_exported_solids(built):
    stage, ctx = built
    facts = stage.interface(ctx)
    hull = import_step(ctx.build_dir / "hull.step")
    lid = import_step(ctx.build_dir / "lid.step")
    volume = hull.volume + lid.volume
    assert facts["dry_mass_g"] == pytest.approx(volume / 1000 * 1.27, abs=1e-6)
    assert facts["internal_volume_cm3"] == pytest.approx(
        facts["displaced_volume_cm3"] - volume / 1000, abs=2e-6
    )
    centres = [tuple(solid.center(CenterOf.MASS)) for solid in (hull, lid)]
    expected = [(centres[0][i] * hull.volume + centres[1][i] * lid.volume) / volume
                for i in range(3)]
    assert facts["centre_of_mass_mm"] == pytest.approx(expected, abs=1e-6)


def test_pack_density_and_infill_are_applied(built):
    stage, ctx = built
    root = ctx.root / "custom-pack"
    pack = root / "packs/buoy/pack.yaml"
    pack.parent.mkdir(parents=True)
    pack.write_text(yaml.safe_dump({
        "materials": {"PETG": {"density_g_cm3": 1.4}},
        "printing": {"infill_factor": 0.75},
    }))
    custom = StageContext(root=root, project="buoy", stage="mechanical")
    stage.generate(custom)
    stage.build(custom)
    ratio = 1.4 / 1.27 * 0.75
    assert stage.interface(custom)["dry_mass_g"] == pytest.approx(
        stage.interface(ctx)["dry_mass_g"] * ratio, abs=1e-6
    )


def test_build_respects_edited_source_wall_without_regenerating(built):
    stage, ctx = built
    custom = clone_source(ctx, "thicker-wall")
    path = custom.src_dir / "params.yaml"
    params = yaml.safe_load(path.read_text())
    params.update(wall_thickness_mm=3.0, lid_thickness_mm=4.2)
    path.write_text(yaml.safe_dump(params))
    stage.build(custom)
    facts = stage.interface(custom)
    assert facts["wall_min_mm"] == 3.0
    assert facts["dry_mass_g"] > stage.interface(ctx)["dry_mass_g"]
    assert path.read_text() == yaml.safe_dump(params)


def test_template_has_clear_bay_four_blind_mounts_and_lid_groove():
    params = HullParams(**propose_params(default_contract(), []))
    hull, lid = build_hull(params)
    x, y, z = params.battery_bay_mm
    w = params.wall_thickness_mm
    bay = Pos(0, 0, w + 0.01) * Box(
        x - 0.02, y - 0.02, z - 0.02, align=(Align.CENTER, Align.CENTER, Align.MIN)
    )
    assert (hull & bay).volume == pytest.approx(0, abs=1e-6)
    for hx, hy in params.board_holes_mm:
        hole = Pos(hx - x / 2, hy - y / 2, z + 2 * w + 0.01) * Cylinder(
            params.screw_hole_diameter_mm / 2 - 0.01, 2,
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        )
        assert (hull & hole).volume == pytest.approx(0, abs=1e-6)
    r = params.outer_diameter_mm / 2
    groove_volume = math.pi * ((r - w) ** 2 - (r - w - params.o_ring_groove_width_mm) ** 2)
    groove_volume *= params.o_ring_groove_depth_mm
    assert lid.volume == pytest.approx(math.pi * r ** 2 * params.lid_thickness_mm - groove_volume)


def test_generate_uses_replaceable_proposal_and_reads_requirements(tmp_path, monkeypatch):
    import engenia.agents.mechanical.stage as module

    ctx = StageContext(root=tmp_path, project="proposal", stage="mechanical")
    ctx.spec_dir.mkdir(parents=True)
    requirement = {
        "id": "R-001", "statement": "The hull shall fit the envelope.",
        "verify": {"method": "inspection", "check": "print_bed_fit"},
        "affects": ["mechanical"], "status": "approved",
    }
    (ctx.spec_dir / "R-001.yaml").write_text(yaml.safe_dump(requirement))
    seen = []

    def proposal(contract, requirements):
        seen.extend(requirements)
        return propose_params(contract, requirements)

    monkeypatch.setattr(module, "propose_params", proposal)
    MechanicalStage().generate(ctx)
    first = {p.name: p.read_bytes() for p in ctx.src_dir.iterdir()}
    MechanicalStage().generate(ctx)
    assert first == {p.name: p.read_bytes() for p in ctx.src_dir.iterdir()}
    assert seen[0].id == "R-001"
    assert "build_hull(params)" in (ctx.src_dir / "model.py").read_text()
    assert not ctx.contract_path.exists()


def test_cli_repeated_runs_publish_identical_mechanical_interfaces(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from engenia.core.cli import app

    ctx = StageContext(root=tmp_path, project="pipeline", stage="mechanical")
    ctx.project_dir.mkdir(parents=True)
    shutil.copytree(REPO / "projects" / "harbour-listener" / "spec", ctx.spec_dir)
    monkeypatch.setenv("ENGENIA_ROOT", str(tmp_path))
    runner = CliRunner()
    first = runner.invoke(app, ["run", "pipeline"])
    assert first.exit_code == 0, first.output
    before = hashes(ctx)
    second = runner.invoke(app, ["run", "pipeline"])
    assert second.exit_code == 0, second.output
    assert hashes(ctx) == before


def test_too_small_envelope_is_rejected():
    contract = default_contract().model_dump(mode="json")
    contract["budgets"]["mechanical"]["outer_envelope_mm"] = [50, 50, 30]
    with pytest.raises(ValueError, match="envelope"):
        propose_params(contract, [])


@pytest.mark.parametrize("updates", [
    {"wall_thickness_mm": 0}, {"infill_factor": 0}, {"infill_factor": 1.1},
    {"height_mm": 20}, {"outer_diameter_mm": 40}, {"o_ring_groove_depth_mm": 3},
    {"board_holes_mm": ((-1, 3), (67, 3), (3, 37), (67, 37))},
    {"battery_bay_mm": (70, 40, float("nan"))},
])
def test_invalid_parameters_fail_before_cad(updates):
    params = HullParams(**propose_params(default_contract(), []))
    with pytest.raises(ValueError):
        replace(params, **updates)
