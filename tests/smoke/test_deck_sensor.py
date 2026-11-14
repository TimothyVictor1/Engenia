"""The second project runs through the same pipeline (task T4-S3, deck-sensor).

Acceptance: `engenia run deck-sensor` completes every stage, `engenia gate deck-sensor`
passes, and the committed check plan is the deterministic one for this spec (so the
verify_plan stage never rewrites an unapproved plan under it).

The module fixture runs the pipeline once in a scratch repository root (spec, scenarios,
check plan and verifiers copied in, ENGENIA_ROOT pointed at it), so the real repository is
left untouched and the heavy run is paid once for all tests in this module.
"""

import json
import os
import shutil
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from engenia.agents.verification.stage import (
    deterministic_plan,
    dump_plan,
    load_requirements,
)
from engenia.core.cli import app
from engenia.core.hashing import file_hash

REPO = Path(__file__).resolve().parents[2]
PROJECT = "deck-sensor"
runner = CliRunner()


@pytest.fixture(scope="module")
def workdir(tmp_path_factory):
    """A scratch repo root with the deck-sensor project and the verifiers; one pipeline run."""
    root = tmp_path_factory.mktemp("deck-sensor")
    shutil.copytree(REPO / "projects" / PROJECT, root / "projects" / PROJECT)
    shutil.copytree(
        REPO / "verifiers",
        root / "verifiers",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    previous = os.environ.get("ENGENIA_ROOT")
    os.environ["ENGENIA_ROOT"] = str(root)
    try:
        result = runner.invoke(app, ["run", PROJECT])
        yield root, result
    finally:
        if previous is None:
            os.environ.pop("ENGENIA_ROOT", None)
        else:
            os.environ["ENGENIA_ROOT"] = previous


def test_pipeline_completes(workdir):
    root, result = workdir
    assert result.exit_code == 0, result.output
    build = root / "projects" / PROJECT / "build"
    ran = [line.split()[1] for line in result.output.splitlines() if line.startswith("ran")]
    hit = [line.split()[1] for line in result.output.splitlines() if line.startswith("hit")]
    assert sorted(ran + hit) == [
        "architect",
        "electronics",
        "final",
        "firmware",
        "integration",
        "mechanical",
        "redteam",
        "report",
        "simprep",
        "simulation",
        "testplan",
        "verify_plan",
    ]
    for name in ran:  # every stage that ran produced an interface and a verdict list
        assert (build / name / "interface.json").is_file(), name
        verdicts = json.loads((build / name / "verdict.json").read_text())
        assert isinstance(verdicts, list), name


def test_pipeline_used_the_project_scenarios(workdir):
    root, _ = workdir
    built = sorted(
        path.stem
        for path in (root / "projects" / PROJECT / "build" / "redteam" / "scenarios").glob("*.yaml")
    )
    assert built == ["cold_snap", "heat_soak", "transport_vibration", "washdown"]


def test_mechanical_built_the_enclosure(workdir):
    root, _ = workdir
    project = root / "projects" / PROJECT
    source = (project / "src" / "mechanical" / "model.py").read_text()
    assert "build_deck_enclosure" in source
    build = project / "build" / "mechanical"
    assert (build / "body.step").is_file()
    assert (build / "body.stl").is_file()
    assert not (build / "hull.step").exists()
    facts = json.loads((build / "interface.json").read_text())["facts"]
    assert "displaced_volume_cm3" not in facts


def test_electronics_selected_from_the_enclosure_pack(workdir):
    root, _ = workdir
    selection = yaml.safe_load(
        (root / "projects" / PROJECT / "src" / "electronics" / "selection.yaml").read_text()
    )
    assert selection["parts"] == {
        "battery": "LP503450",
        "environment_sensor": "BME280",
        "lora": "RFM95W-868S2",
        "microcontroller": "ESP32-WROVER-B",
        "regulator": "S7V8F3",
        "vibration_amp": "MAX4466EUK",
    }
    assert selection["target_endurance_days"] == 14.0


def test_firmware_generated_the_pack_threshold_detector(workdir):
    root, _ = workdir
    firmware = root / "projects" / PROJECT / "src" / "firmware"
    config = json.loads((firmware / "config.json").read_text())
    assert config["detector_type"] == "threshold"
    assert config["detector"] == {"threshold": 0.25, "hold_s": 0.15}
    assert "rms" in (firmware / "src" / "detector.c").read_text()
    metrics = json.loads(
        (root / "projects" / PROJECT / "build" / "firmware" / "metrics.json").read_text()
    )
    assert metrics["detection_rate"] == 1.0


def test_gate_passes_after_the_run(workdir):
    root, _ = workdir
    result = runner.invoke(app, ["gate", PROJECT])
    assert result.exit_code == 0, result.output
    report = json.loads(
        (root / "projects" / PROJECT / "build" / "gate" / "report.json").read_text()
    )
    assert report["passed"] is True, report["gates"]
    assert report["unverified"] == []


def test_gate_recomputes_every_requirement_as_pass(workdir):
    root, _ = workdir
    report = json.loads(
        (root / "projects" / PROJECT / "build" / "gate" / "report.json").read_text()
    )
    results = {v["requirement"]: v["result"] for v in report["verdicts"]}
    assert results == {f"R-{number:03d}": "pass" for number in range(1, 9)}


def test_matrix_reports_all_eight_requirements():
    result = runner.invoke(app, ["matrix", PROJECT])
    assert result.exit_code == 0, result.output
    assert "8 requirements" in result.output


def test_lint_deck_sensor():
    result = runner.invoke(app, ["lint", PROJECT])
    assert result.exit_code == 0, result.output


def test_committed_check_plan_is_the_deterministic_plan():
    """The committed plan and approval match what verify_plan writes for this spec."""
    spec = REPO / "projects" / PROJECT / "spec"
    pack = yaml.safe_load((REPO / "packs" / "buoy" / "pack.yaml").read_text(encoding="utf-8"))
    expected = dump_plan(deterministic_plan(load_requirements(spec), pack))
    checks = REPO / "projects" / PROJECT / "checks" / "checks.yaml"
    assert checks.read_text(encoding="utf-8") == expected
    approved = (REPO / "projects" / PROJECT / "checks" / "APPROVED").read_text().strip()
    assert approved == file_hash(checks)


def test_enclosure_pack_template_builds_a_body_and_a_lid():
    from packs.enclosure.templates.deck_enclosure import (
        DeckEnclosureParams,
        build_deck_enclosure,
    )

    params = DeckEnclosureParams(
        outer_diameter_mm=114.0,
        height_mm=39.0,
        wall_thickness_mm=3.0,
        battery_bay_mm=(72.0, 42.0, 22.0),
        board_holes_mm=((3.0, 3.0), (67.0, 3.0), (3.0, 37.0), (67.0, 37.0)),
    )
    body, lid = build_deck_enclosure(params)
    assert len(body.solids()) == 1 and len(lid.solids()) == 1
    width, depth, _ = body.bounding_box().size
    assert width >= params.battery_bay_mm[0] + 8 * params.wall_thickness_mm
    assert depth >= params.battery_bay_mm[1] + 8 * params.wall_thickness_mm


def test_enclosure_pack_template_rejects_bad_params():
    from packs.enclosure.templates.deck_enclosure import DeckEnclosureParams

    good = {
        "outer_diameter_mm": 114.0,
        "height_mm": 39.0,
        "wall_thickness_mm": 3.0,
        "battery_bay_mm": (72.0, 42.0, 22.0),
        "board_holes_mm": ((3.0, 3.0), (67.0, 3.0), (3.0, 37.0), (67.0, 37.0)),
    }
    with pytest.raises(ValueError):
        DeckEnclosureParams(**{**good, "outer_diameter_mm": 10.0})
    with pytest.raises(ValueError):
        DeckEnclosureParams(**{**good, "height_mm": 5.0})
    with pytest.raises(ValueError):
        DeckEnclosureParams(**{**good, "lid_thickness_mm": 1.0})
    with pytest.raises(ValueError):
        DeckEnclosureParams(**{**good, "infill_factor": 1.5})
    with pytest.raises(ValueError):
        DeckEnclosureParams(**{**good, "board_holes_mm": ((1.0, 1.0),)})
