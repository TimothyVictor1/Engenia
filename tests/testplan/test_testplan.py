"""testplan stage: a physical test plan from the requirements. Uses its own fixture project."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from engenia.agents.testplan.stage import TEMPLATES, TestplanStage
from engenia.contracts.models import StageContext
from engenia.core.inputs import declared_inputs

PROJECT = "plan-demo"


def write_req(root: Path, number: int, check: str, limit: dict, *, method="analysis",
              status="approved", value=1, unit="x") -> None:
    data = {
        "id": f"R-{number:03d}",
        "statement": f"The product shall satisfy {check}.",
        "value": value,
        "unit": unit,
        "verify": {"method": method, "check": check, "limit": limit},
        "affects": ["mechanical"],
        "status": status,
    }
    spec = root / "projects" / PROJECT / "spec"
    spec.mkdir(parents=True, exist_ok=True)
    (spec / f"R-{number:03d}.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    write_req(tmp_path, 1, "mass_budget", {"max": 400}, value=400, unit="g")
    write_req(tmp_path, 2, "print_bed_fit", {"equals": 1}, unit="fits")
    write_req(tmp_path, 3, "battery_capacity", {"min": 7}, value=7, unit="day")
    write_req(tmp_path, 4, "buoyancy_margin", {"min": 0.2}, value=0.2, unit="ratio")
    write_req(tmp_path, 5, "metacentric_height", {"min": 5}, value=5, unit="mm")
    write_req(tmp_path, 6, "measured_waterline", {"max": 60}, method="test", value=60, unit="mm")
    write_req(tmp_path, 7, "measured_tilt", {"max": 10}, method="test", value=10, unit="deg")
    write_req(tmp_path, 8, "detection_rate", {"min": 0.9}, value=0.9, unit="ratio")
    write_req(tmp_path, 9, "mass_budget", {"max": 1}, status="draft")
    return tmp_path


def ctx(root: Path) -> StageContext:
    return StageContext(project=PROJECT, stage="testplan", root=root)


def run_stage(root: Path) -> StageContext:
    c = ctx(root)
    stage = TestplanStage()
    stage.generate(c)
    stage.build(c)
    return c


def test_plan_has_procedure_instrument_expected_and_limit_for_each_test(root):
    c = run_stage(root)
    plan = json.loads((c.build_dir / "physical-tests.json").read_text())
    tests = {t["requirement"]: t for t in plan["tests"]}
    assert set(tests) == {"R-001", "R-002", "R-003", "R-004", "R-005", "R-006", "R-007"}
    for test in tests.values():
        assert test["id"] == "T-" + test["requirement"][2:]
        assert test["procedure"] and test["instrument"] and test["limit"]
        assert test["expected"]["text"] in test["pass_rule"]
    assert tests["R-001"]["limit"] == {"max": 400}
    assert tests["R-001"]["expected"] == {"value": 400, "unit": "g", "text": "<= 400 g"}
    assert tests["R-001"]["measurement_key"] == "mass_g"
    assert tests["R-006"]["measurement_key"] == "waterline_mm"
    assert tests["R-007"]["measurement_key"] == "tilt_deg"
    assert tests["R-004"]["measurement_key"] is None
    assert tests["R-003"]["quantity"] == "endurance"
    assert tests["R-002"]["quantity"] == "fit"


def test_not_covered_requirements_are_listed_with_a_reason_and_drafts_are_skipped(root):
    c = run_stage(root)
    plan = json.loads((c.build_dir / "physical-tests.json").read_text())
    assert [n["requirement"] for n in plan["not_covered"]] == ["R-008"]
    assert plan["not_covered"][0]["reason"]
    assert "R-009" not in json.dumps(plan)


def test_markdown_matches_the_json(root):
    c = run_stage(root)
    md = (c.build_dir / "physical-tests.md").read_text()
    plan = json.loads((c.build_dir / "physical-tests.json").read_text())
    for test in plan["tests"]:
        assert f"## {test['id']} " in md
        assert test["pass_rule"] in md
    assert "Not covered" in md and "R-008" in md


def test_interface_lists_the_test_ids(root):
    c = run_stage(root)
    facts = TestplanStage().interface(c)
    assert facts["tests"] == 7
    assert facts["test_ids"][0] == "T-001"
    assert facts["not_covered"] == ["R-008"]


def test_output_is_reproducible(root, tmp_path):
    first = run_stage(root)
    a = {p.name: p.read_bytes() for p in first.build_dir.iterdir()}
    second = run_stage(root)
    assert a == {p.name: p.read_bytes() for p in second.build_dir.iterdir()}


def test_spec_is_an_input_of_the_stage(root):
    files, trees = declared_inputs(TestplanStage())
    assert "{project}/spec" in trees
    assert "engenia/agents/testplan" in trees


def test_every_template_check_is_an_existing_verifier():
    repo = Path(__file__).resolve().parents[2]
    for check in TEMPLATES:
        assert (repo / "verifiers" / f"{check}.py").is_file(), check


@pytest.mark.parametrize("project", ["harbour-listener", "deck-sensor"])
def test_example_projects_get_a_plan(project, tmp_path):
    import shutil

    repo = Path(__file__).resolve().parents[2]
    shutil.copytree(repo / "projects" / project / "spec", tmp_path / "projects" / project / "spec")
    c = StageContext(project=project, stage="testplan", root=tmp_path)
    stage = TestplanStage()
    stage.generate(c)
    stage.build(c)
    plan = json.loads((c.build_dir / "physical-tests.json").read_text())
    assert plan["tests"]
    assert all(t["limit"] and t["procedure"] for t in plan["tests"])
