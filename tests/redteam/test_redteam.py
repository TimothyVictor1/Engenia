"""Tests for the red-team stage and the expanded example spec (task T4-S1).

Acceptance criteria:
- engenia lint harbour-listener exits 0 with twelve requirements.
- Every scenario file validates and names at least one real requirement id.
- A scenario targeting an unknown requirement id fails build().
"""

import shutil
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError
from typer.testing import CliRunner

from engenia.agents.redteam import stage as redteam_module
from engenia.agents.redteam.stage import SCENARIOS_DIRNAME, RedteamStage, Scenario
from engenia.contracts.models import Interface, StageContext
from engenia.core.cli import app
from engenia.stages.base import Stage

REPO = Path(__file__).resolve().parents[2]
SPEC_DIR = REPO / "projects" / "harbour-listener" / "spec"
PROJECT = "harbour-listener"
EXPECTED_IDS = [f"R-{number:03d}" for number in range(1, 13)]
runner = CliRunner()


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    """Scratch repo root holding a copy of the example spec, so runs leave no files behind."""
    shutil.copytree(SPEC_DIR, tmp_path / "projects" / PROJECT / "spec")
    monkeypatch.setenv("ENGENIA_ROOT", str(tmp_path))
    return tmp_path


def make_ctx(workdir: Path, stage: str = "redteam") -> StageContext:
    return StageContext(project=PROJECT, stage=stage, root=workdir)


def scenario_files(directory: Path) -> list[Path]:
    return sorted(directory.glob("*.yaml"))


def make_scenario_file(
    directory: Path, file_name: str, *, targets: list[str] | None = None, **overrides
) -> Path:
    data: dict = {
        "name": file_name,
        "wave_height_m": 0.5,
        "wave_period_s": 4.0,
        "water_temp_c": 10.0,
        "water_density": 1005.0,
        "depth_m": 3.0,
        "extra": {},
        "rationale": "test scenario",
        "targets": targets if targets is not None else ["R-001"],
    }
    data.update(overrides)
    path = directory / f"{file_name}.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")
    return path


# --- Acceptance 1: lint exits 0 with twelve requirements ---------------------------------------


def test_lint_exits_zero_with_twelve_requirements(workdir):
    spec_dir = workdir / "projects" / PROJECT / "spec"
    assert sorted(path.stem for path in spec_dir.glob("R-*.yaml")) == EXPECTED_IDS
    result = runner.invoke(app, ["lint", PROJECT])
    assert result.exit_code == 0, result.output
    assert sum(1 for line in result.output.splitlines() if line.startswith("ok")) == 12


def test_original_requirements_are_unchanged():
    original = {"R-001.yaml", "R-003.yaml", "R-005.yaml"}
    files = {path.name for path in SPEC_DIR.glob("R-*.yaml")}
    assert original <= files


def test_new_requirements_are_approved_with_one_obligation():
    for number in [2, 4, 6, 7, 8, 9, 10, 11, 12]:
        path = SPEC_DIR / f"R-{number:03d}.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert data["id"] == f"R-{number:03d}"
        assert data["status"] == "approved"
        statement = data["statement"]
        assert "shall" in statement
        assert data["value"] is not None and data["unit"]
        limit = data["verify"]["limit"]
        assert any(key in limit for key in ("min", "max", "equals"))


# --- Acceptance 2: every scenario file validates with real requirement targets -----------------


def test_propose_scenarios_are_the_four_fixed_ones():
    scenarios = redteam_module.propose_scenarios()
    assert sorted(scenario.name for scenario in scenarios) == [
        "baltic_winter",
        "calm_harbour",
        "flooded_bay",
        "storm",
    ]
    again = redteam_module.propose_scenarios()
    assert [s.model_dump() for s in scenarios] == [s.model_dump() for s in again]


def test_proposed_targets_are_real_requirement_ids():
    real_ids = {path.stem for path in SPEC_DIR.glob("R-*.yaml")}
    for scenario in redteam_module.propose_scenarios():
        assert scenario.targets, f"{scenario.name} names no requirement"
        assert set(scenario.targets) <= real_ids


def test_generate_writes_valid_scenario_files(workdir):
    ctx = make_ctx(workdir)
    RedteamStage().generate(ctx)
    files = scenario_files(ctx.src_dir / SCENARIOS_DIRNAME)
    assert [path.stem for path in files] == [
        "baltic_winter",
        "calm_harbour",
        "flooded_bay",
        "storm",
    ]
    for path in files:
        scenario = Scenario.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        assert scenario.name == path.stem


def test_build_copies_validated_scenarios_and_interface_lists_them(workdir):
    stage = RedteamStage()
    ctx = make_ctx(workdir)
    stage.generate(ctx)
    stage.build(ctx)
    source_files = scenario_files(ctx.src_dir / SCENARIOS_DIRNAME)
    built_files = scenario_files(ctx.build_dir / SCENARIOS_DIRNAME)
    assert [path.name for path in built_files] == [path.name for path in source_files]
    for source, built in zip(source_files, built_files, strict=True):
        assert source.read_bytes() == built.read_bytes()
    assert stage.interface(ctx) == {
        "scenarios": ["baltic_winter", "calm_harbour", "flooded_bay", "storm"]
    }


def test_run_builds_redteam_scenarios_in_the_pipeline(workdir):
    result = runner.invoke(app, ["run", PROJECT])
    assert result.exit_code == 0, result.output
    build = workdir / "projects" / PROJECT / "build" / "redteam"
    assert [path.stem for path in scenario_files(build / SCENARIOS_DIRNAME)] == [
        "baltic_winter",
        "calm_harbour",
        "flooded_bay",
        "storm",
    ]
    interface = Interface.model_validate_json((build / "interface.json").read_text())
    assert interface.facts == {
        "scenarios": ["baltic_winter", "calm_harbour", "flooded_bay", "storm"]
    }


def test_generate_is_idempotent(workdir):
    stage = RedteamStage()
    ctx = make_ctx(workdir)
    stage.generate(ctx)
    scenarios_dir = ctx.src_dir / SCENARIOS_DIRNAME
    first = {path.name: path.read_bytes() for path in scenario_files(scenarios_dir)}
    (scenarios_dir / "stale.yaml").write_text("name: stale\n", encoding="utf-8")
    stage.generate(ctx)
    second = {path.name: path.read_bytes() for path in scenario_files(scenarios_dir)}
    assert first == second
    assert "stale.yaml" not in second


def test_build_is_deterministic(workdir):
    stage = RedteamStage()
    ctx = make_ctx(workdir)
    stage.generate(ctx)
    builds = []
    for _ in range(2):
        stage.build(ctx)
        builds.append(
            {
                path.name: path.read_bytes()
                for path in sorted((ctx.build_dir / SCENARIOS_DIRNAME).glob("*"))
            }
        )
    assert builds[0] == builds[1]


# --- Acceptance 3: unknown requirement target fails build ---------------------------------------


def test_build_rejects_unknown_requirement_target(workdir):
    stage = RedteamStage()
    ctx = make_ctx(workdir)
    stage.generate(ctx)
    scenarios_dir = ctx.src_dir / SCENARIOS_DIRNAME
    make_scenario_file(scenarios_dir, "rogue", targets=["R-999"])
    with pytest.raises(ValueError, match="R-999"):
        stage.build(ctx)


def test_build_rejects_every_unknown_target(workdir):
    stage = RedteamStage()
    ctx = make_ctx(workdir)
    stage.generate(ctx)
    scenarios_dir = ctx.src_dir / SCENARIOS_DIRNAME
    make_scenario_file(scenarios_dir, "rogue", targets=["R-001", "R-424"])
    with pytest.raises(ValueError, match="R-424"):
        stage.build(ctx)


def test_build_rejects_scenario_without_targets(workdir):
    stage = RedteamStage()
    ctx = make_ctx(workdir)
    stage.generate(ctx)
    make_scenario_file(ctx.src_dir / SCENARIOS_DIRNAME, "rogue", targets=[])
    with pytest.raises(ValueError):
        stage.build(ctx)


def test_build_rejects_invalid_scenario(workdir):
    stage = RedteamStage()
    ctx = make_ctx(workdir)
    stage.generate(ctx)
    scenarios_dir = ctx.src_dir / SCENARIOS_DIRNAME
    make_scenario_file(scenarios_dir, "rogue")
    (scenarios_dir / "rogue.yaml").write_text(
        yaml.safe_dump({"name": "rogue", "wave_height_m": 1.0}, sort_keys=True),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        stage.build(ctx)


def test_build_rejects_scenario_name_not_matching_file(workdir):
    stage = RedteamStage()
    ctx = make_ctx(workdir)
    stage.generate(ctx)
    make_scenario_file(ctx.src_dir / SCENARIOS_DIRNAME, "rogue", name="mismatch")
    with pytest.raises(ValueError, match="does not match"):
        stage.build(ctx)


def test_build_rejects_unknown_scenario_fields(workdir):
    stage = RedteamStage()
    ctx = make_ctx(workdir)
    stage.generate(ctx)
    scenarios_dir = ctx.src_dir / SCENARIOS_DIRNAME
    make_scenario_file(scenarios_dir, "rogue", surprise=1)
    with pytest.raises(ValueError):
        stage.build(ctx)


def test_build_without_generate_fails(workdir):
    stage = RedteamStage()
    ctx = make_ctx(workdir)
    with pytest.raises(ValueError, match="no scenario files"):
        stage.build(ctx)


# --- Scenario model and stage contract ----------------------------------------------------------


def test_scenario_model_rejects_unknown_fields():
    data = redteam_module.propose_scenarios()[0].model_dump()
    with pytest.raises(ValidationError):
        Scenario.model_validate({**data, "surprise": 1})


def test_scenario_model_requires_at_least_one_target():
    data = redteam_module.propose_scenarios()[0].model_dump()
    data["targets"] = []
    with pytest.raises(ValidationError):
        Scenario.model_validate(data)


def test_redteam_stage_satisfies_the_stage_protocol():
    stage = RedteamStage()
    assert isinstance(stage, Stage)
    assert (stage.name, stage.needs, stage.reads_contract, stage.checks) == (
        "redteam",
        [],
        False,
        [],
    )


def test_generate_uses_the_replaceable_propose_scenarios(workdir, monkeypatch):
    custom = Scenario(
        name="custom",
        wave_height_m=1.0,
        wave_period_s=2.0,
        water_temp_c=10.0,
        water_density=1000.0,
        depth_m=2.0,
        rationale="harness-proposed",
        targets=["R-001"],
    )
    monkeypatch.setattr(redteam_module, "propose_scenarios", lambda: [custom])
    ctx = make_ctx(workdir)
    RedteamStage().generate(ctx)
    written = yaml.safe_load(
        (ctx.src_dir / SCENARIOS_DIRNAME / "custom.yaml").read_text(encoding="utf-8")
    )
    assert written["name"] == "custom"
    assert written["targets"] == ["R-001"]
