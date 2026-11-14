"""Tests for project-owned red-team scenarios (task T4-S3, deck-sensor).

The red-team stage prefers projects/<p>/scenarios/ over its fixed buoy proposal, so a
second project can ship scenarios about its own environment. A project scenario file that
does not validate must raise, never silently fall back to the buoy scenarios.
"""

import shutil
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from engenia.agents.redteam.stage import (
    SCENARIOS_DIRNAME,
    RedteamStage,
    Scenario,
    project_scenarios,
)
from engenia.contracts.models import StageContext

REPO = Path(__file__).resolve().parents[2]
DECK_SENSOR = "deck-sensor"


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    """Scratch root holding the deck-sensor spec, so stage runs leave no files behind."""
    shutil.copytree(
        REPO / "projects" / DECK_SENSOR / "spec",
        tmp_path / "projects" / DECK_SENSOR / "spec",
    )
    monkeypatch.setenv("ENGENIA_ROOT", str(tmp_path))
    return tmp_path


def make_ctx(workdir: Path) -> StageContext:
    return StageContext(project=DECK_SENSOR, stage="redteam", root=workdir)


def write_scenario(
    workdir: Path, file_name: str, *, targets: list[str] | None = None, **overrides
) -> Path:
    """One valid project scenario file in projects/<p>/scenarios/."""
    data: dict = {
        "name": file_name,
        "wave_height_m": 0.0,
        "wave_period_s": 0.0,
        "water_temp_c": 20.0,
        "water_density": 1000.0,
        "depth_m": 0.0,
        "extra": {"air_temp_c": 30.0},
        "rationale": "test scenario for the deck sensor",
        "targets": targets if targets is not None else ["R-001"],
    }
    data.update(overrides)
    directory = workdir / "projects" / DECK_SENSOR / SCENARIOS_DIRNAME
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{file_name}.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")
    return path


# --- the shipped deck-sensor scenarios ----------------------------------------------------------


def test_shipped_deck_scenarios_validate_against_the_model():
    paths = sorted((REPO / "projects" / DECK_SENSOR / SCENARIOS_DIRNAME).glob("*.yaml"))
    assert len(paths) >= 2
    for path in paths:
        scenario = Scenario.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        assert scenario.name == path.stem


def test_shipped_deck_scenarios_target_deck_sensor_requirements():
    spec_ids = {path.stem for path in (REPO / "projects" / DECK_SENSOR / "spec").glob("*.yaml")}
    for path in sorted((REPO / "projects" / DECK_SENSOR / SCENARIOS_DIRNAME).glob("*.yaml")):
        scenario = Scenario.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        assert scenario.targets, f"{scenario.name} names no requirement"
        assert set(scenario.targets) <= spec_ids, scenario.name


def test_shipped_deck_scenarios_are_deck_scenarios_not_buoy_ones():
    names = {
        path.stem for path in (REPO / "projects" / DECK_SENSOR / SCENARIOS_DIRNAME).glob("*.yaml")
    }
    assert not names & {"calm_harbour", "baltic_winter", "storm", "flooded_bay"}


# --- the project-scenarios hook -----------------------------------------------------------------


def test_project_scenarios_empty_when_the_project_ships_none(workdir):
    assert project_scenarios(make_ctx(workdir)) == []


def test_generate_uses_project_scenarios_when_present(workdir):
    write_scenario(workdir, "heat_soak", targets=["R-001"])
    write_scenario(workdir, "cold_snap", targets=["R-003"])
    stage = RedteamStage()
    ctx = make_ctx(workdir)
    stage.generate(ctx)
    written = sorted(path.stem for path in (ctx.src_dir / SCENARIOS_DIRNAME).glob("*.yaml"))
    assert written == ["cold_snap", "heat_soak"]
    for path in (ctx.src_dir / SCENARIOS_DIRNAME).glob("*.yaml"):
        scenario = Scenario.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        assert scenario.name == path.stem


def test_generate_falls_back_to_the_fixed_scenarios_without_a_project_directory(workdir):
    ctx = make_ctx(workdir)
    RedteamStage().generate(ctx)
    written = sorted(path.stem for path in (ctx.src_dir / SCENARIOS_DIRNAME).glob("*.yaml"))
    # deck-sensor has no tilt, stress or buoyancy requirement: only baltic_winter (power
    # budget) matches, the other pack scenarios are skipped with a recorded reason.
    assert written == ["baltic_winter"]
    assert (ctx.src_dir / "skipped_scenarios.yaml").is_file()


def test_generate_falls_back_when_the_project_directory_is_empty(workdir):
    (workdir / "projects" / DECK_SENSOR / SCENARIOS_DIRNAME).mkdir(parents=True)
    ctx = make_ctx(workdir)
    RedteamStage().generate(ctx)
    written = sorted(path.stem for path in (ctx.src_dir / SCENARIOS_DIRNAME).glob("*.yaml"))
    # deck-sensor has no tilt, stress or buoyancy requirement: only baltic_winter (power
    # budget) matches, the other pack scenarios are skipped with a recorded reason.
    assert written == ["baltic_winter"]
    assert (ctx.src_dir / "skipped_scenarios.yaml").is_file()


def test_generate_raises_on_a_broken_project_scenario(workdir):
    directory = workdir / "projects" / DECK_SENSOR / SCENARIOS_DIRNAME
    directory.mkdir(parents=True)
    (directory / "broken.yaml").write_text("name: broken\n", encoding="utf-8")
    with pytest.raises((ValueError, ValidationError)):
        RedteamStage().generate(make_ctx(workdir))


def test_generate_raises_on_a_project_scenario_that_fails_validation(workdir):
    write_scenario(workdir, "rogue", targets=["R-999"])
    # targets are checked in build(); validation happens first: an unknown field must fail.
    directory = workdir / "projects" / DECK_SENSOR / SCENARIOS_DIRNAME
    (directory / "rogue.yaml").write_text(
        yaml.safe_dump({"name": "rogue", "surprise": 1}, sort_keys=True), encoding="utf-8"
    )
    with pytest.raises((ValueError, ValidationError)):
        RedteamStage().generate(make_ctx(workdir))


def test_generate_raises_when_the_scenario_name_does_not_match_the_file(workdir):
    write_scenario(workdir, "rogue", name="mismatch")
    with pytest.raises(ValueError, match="does not match"):
        RedteamStage().generate(make_ctx(workdir))


def test_project_scenarios_flow_through_build_and_interface(workdir):
    write_scenario(workdir, "heat_soak", targets=["R-001"])
    write_scenario(workdir, "cold_snap", targets=["R-003"])
    stage = RedteamStage()
    ctx = make_ctx(workdir)
    stage.generate(ctx)
    stage.build(ctx)
    built = sorted(path.stem for path in (ctx.build_dir / SCENARIOS_DIRNAME).glob("*.yaml"))
    assert built == ["cold_snap", "heat_soak"]
    assert stage.interface(ctx) == {"scenarios": built}


def test_build_still_rejects_unknown_targets_in_project_scenarios(workdir):
    write_scenario(workdir, "rogue", targets=["R-999"])
    stage = RedteamStage()
    ctx = make_ctx(workdir)
    stage.generate(ctx)
    with pytest.raises(ValueError, match="R-999"):
        stage.build(ctx)
