import json
import shutil
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError
from typer.testing import CliRunner

from engenia.contracts.models import (
    STAGE_NAMES,
    ChangeRequest,
    Contract,
    Interface,
    Requirement,
    Verdict,
)
from engenia.core.cli import app
from engenia.stages.base import Stage
from engenia.stages.registry import REGISTRY

REPO = Path(__file__).resolve().parents[2]
SPEC_DIR = REPO / "projects" / "harbour-listener" / "spec"
runner = CliRunner()

EXPECTED_NEEDS = {
    "verify_plan": [],
    "architect": ["verify_plan"],
    "mechanical": ["architect"],
    "electronics": ["architect"],
    "firmware": ["architect"],
    "redteam": [],
    "testplan": [],
    "simprep": [],
    "integration": ["mechanical", "electronics", "firmware"],
    "simulation": ["integration", "redteam", "simprep"],
    "final": ["simulation", "testplan"],
    "report": ["final"],
}


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    """A scratch repo root holding a copy of the example project, so runs leave no files behind."""
    shutil.copytree(
        REPO / "projects" / "harbour-listener" / "spec",
        tmp_path / "projects" / "harbour-listener" / "spec",
    )
    monkeypatch.setenv("ENGENIA_ROOT", str(tmp_path))
    return tmp_path


def test_models_import():
    assert len(STAGE_NAMES) == 12


def test_three_specs_validate():
    files = sorted(SPEC_DIR.glob("R-*.yaml"))
    assert [f.stem for f in files] == ["R-001", "R-003", "R-005"]
    for path in files:
        requirement = Requirement.model_validate(yaml.safe_load(path.read_text()))
        assert requirement.id == path.stem
        assert requirement.status == "approved"


def test_registry_has_all_twelve_stages():
    assert list(REGISTRY) == list(STAGE_NAMES)
    for name, stage in REGISTRY.items():
        assert isinstance(stage, Stage)
        assert stage.name == name
        assert stage.needs == EXPECTED_NEEDS[name]
        assert stage.checks == []


def test_run_exits_zero_and_builds_every_stage(workdir):
    result = runner.invoke(app, ["run", "harbour-listener"])
    assert result.exit_code == 0, result.output
    for name in STAGE_NAMES:
        build = workdir / "projects" / "harbour-listener" / "build" / name
        assert (build / "placeholder.txt").is_file()
        interface = Interface.model_validate_json((build / "interface.json").read_text())
        assert interface.facts == {"stub": True}
        assert json.loads((build / "verdict.json").read_text()) == []
        assert (workdir / "projects" / "harbour-listener" / "src" / name).is_dir()


def test_run_is_deterministic(workdir):
    interfaces = []
    for _ in range(2):
        assert runner.invoke(app, ["run", "harbour-listener"]).exit_code == 0
        build = workdir / "projects" / "harbour-listener" / "build"
        interfaces.append({p.parent.name: p.read_text() for p in build.glob("*/interface.json")})
    assert interfaces[0] == interfaces[1]


def test_run_respects_dependency_order(workdir):
    result = runner.invoke(app, ["run", "harbour-listener"])
    ran = [line.split()[1] for line in result.output.splitlines() if line.startswith("ran")]
    assert sorted(ran) == sorted(STAGE_NAMES)
    for name, needs in EXPECTED_NEEDS.items():
        assert all(ran.index(dep) < ran.index(name) for dep in needs)


def test_lint_passes_on_example_spec(workdir):
    result = runner.invoke(app, ["lint", "harbour-listener"])
    assert result.exit_code == 0, result.output


def test_lint_rejects_bad_spec(workdir):
    bad = workdir / "projects" / "harbour-listener" / "spec" / "R-009.yaml"
    bad.write_text("id: R-009\nstatement: The buoy is robust.\n")
    result = runner.invoke(app, ["lint", "harbour-listener"])
    assert result.exit_code == 1


def test_lint_rejects_id_not_matching_file_name(workdir):
    spec = workdir / "projects" / "harbour-listener" / "spec"
    (spec / "R-004.yaml").write_text((spec / "R-001.yaml").read_text())
    assert runner.invoke(app, ["lint", "harbour-listener"]).exit_code == 1


@pytest.mark.parametrize("command", ["plan", "gate", "matrix", "status"])
def test_unimplemented_commands_exit_zero(command, workdir):
    result = runner.invoke(app, [command, "harbour-listener"])
    assert result.exit_code == 0
    assert "not implemented" in result.output


def test_requirement_rejects_unknown_fields():
    data = yaml.safe_load((SPEC_DIR / "R-001.yaml").read_text())
    with pytest.raises(ValidationError):
        Requirement.model_validate({**data, "extra": 1})


@pytest.mark.parametrize(
    "patch",
    [
        {"statement": "The buoy must be light."},  # no "shall"
        {"value": None},  # value missing, method is not inspection
        {"unit": None},  # unit missing while value is set
        {"affects": []},  # at least one stage
        {"affects": ["plumbing"]},  # unknown stage
        {"id": "R-1"},
        {"status": "done"},
    ],
)
def test_requirement_rules(patch):
    data = yaml.safe_load((SPEC_DIR / "R-001.yaml").read_text())
    with pytest.raises(ValidationError):
        Requirement.model_validate({**data, **patch})


def test_section_6_examples_validate():
    Contract.model_validate(
        {
            "version": 1,
            "budgets": {
                "mechanical": {"mass_g": 300, "outer_envelope_mm": [160, 160, 220]},
                "electronics": {
                    "mass_g": 60,
                    "bay_mm": [70, 40, 22],
                    "energy_mwh_per_day": 180,
                },
                "firmware": {"flash_kb": 512, "ram_kb": 64},
            },
            "interfaces": {
                "pin_map": {"HYDROPHONE_ADC": "GPIO34", "LORA_CS": "GPIO5"},
                "mounts": {"board_holes_mm": [[3, 3], [67, 3], [3, 37], [67, 37]]},
            },
            "totals": {"mass_g_max": 400},
        }
    )
    ChangeRequest.model_validate(
        {
            "id": "CR-001",
            "from_stage": "electronics",
            "path": "budgets.electronics.bay_mm",
            "requested": [70, 40, 26],
            "reason": "battery height 24 mm",
            "status": "open",
        }
    )
    Interface.model_validate(
        {"stage": "mechanical", "key": "sha256:" + "a" * 64, "facts": {"dry_mass_g": 318.4}}
    )
    Verdict.model_validate(
        {
            "check": "buoyancy_margin",
            "requirement": "R-005",
            "measured": 0.27,
            "limit": {"min": 0.20},
            "result": "pass",
            "evidence_level": "calculated",
            "artifact": "sha256:" + "b" * 64,
        }
    )


def test_contract_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        Contract.model_validate({"version": 1, "surprise": True})
