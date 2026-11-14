"""Engineering baseline intake and approval on an isolated pack and specification."""

import json
import shutil
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from engenia.agents.verification.stage import deterministic_plan
from engenia.contracts.models import Requirement
from engenia.core.baseline import BaselineError, derive_baseline, reject_derived
from engenia.core.cli import app
from engenia.core.intake_run import run_intake, summary_lines


@pytest.fixture
def baseline_pack(root):
    pack = root / "packs" / "buoy" / "pack.yaml"
    data = {"baseline": [{
        "check": "wall_thickness_min", "value": 3, "unit": "mm", "method": "analysis",
        "limit": {"min": 3}, "affects": ["mechanical"],
        "statement": "The shell shall have a wall thickness of at least 3 mm.",
        "reason": "Fixture manufacturing policy.",
    }]}
    pack.write_text(yaml.safe_dump(data))
    source = Path(__file__).resolve().parents[2] / "verifiers"
    shutil.copytree(source, root / "verifiers")
    return data


def test_missing_baseline_is_draft_with_provenance(root, baseline_pack):
    result = run_intake("A useful product.", "fixture", root)
    assert len(result.paths) == 1
    data = yaml.safe_load(result.paths[0].read_text())
    assert data["origin"] == "derived" and data["status"] == "draft"
    assert "Fixture manufacturing policy" in data["reason"]
    assert "derived engineering requirements" in "\n".join(summary_lines(result.paths, []))
    assert derive_baseline("fixture", root, pack="buoy") == []


def test_weaker_user_requirement_does_not_hide_baseline(root, baseline_pack):
    result = run_intake("Walls at least 1 mm.", "fixture", root)
    data = [Requirement.model_validate(yaml.safe_load(p.read_text())) for p in result.paths]
    assert [(r.origin, r.value) for r in data] == [("user", 1), ("derived", 3)]
    assert deterministic_plan({r.id: r for r in data}, baseline_pack) == []
    approved = {r.id: r.model_copy(update={"status": "approved"}) for r in data}
    assert len(deterministic_plan(approved, baseline_pack)) == 2


def test_stronger_user_requirement_covers_baseline(root, baseline_pack):
    result = run_intake("Walls at least 4 mm.", "fixture", root)
    assert len(result.paths) == 1
    assert yaml.safe_load(result.paths[0].read_text()).get("origin", "user") == "user"


def test_rejected_baseline_is_not_reproposed_and_next_id_is_unique(root, baseline_pack):
    path = derive_baseline("fixture", root, pack="buoy")[0]
    reject_derived(path)
    assert not path.exists()
    assert derive_baseline("fixture", root, pack="buoy") == []
    result = run_intake("Mass at most 200 g.", "fixture", root)
    assert result.paths[0].stem != path.stem


def test_reject_refuses_approved_or_user_requirement(root, baseline_pack):
    path = derive_baseline("fixture", root, pack="buoy")[0]
    data = yaml.safe_load(path.read_text())
    data["status"] = "approved"
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(BaselineError):
        reject_derived(path)


def test_invalid_pack_fails_without_partial_baseline(root, baseline_pack):
    baseline_pack["baseline"].append({"check": "unknown"})
    (root / "packs/buoy/pack.yaml").write_text(yaml.safe_dump(baseline_pack))
    with pytest.raises(BaselineError):
        derive_baseline("fixture", root, pack="buoy")
    assert list((root / "projects/fixture/spec").glob("R-*.yaml")) == []


def test_cli_reject_archives_draft(root, baseline_pack):
    path = derive_baseline("fixture", root, pack="buoy")[0]
    result = CliRunner().invoke(app, ["reject", "fixture", path.stem])
    assert result.exit_code == 0, result.output
    assert (path.parent / "rejected" / path.name).exists()


def test_dashboard_exposes_origin_and_reason(root, baseline_pack):
    from engenia.dashboard.reader import ProjectReader

    path = derive_baseline("fixture", root, pack="buoy")[0]
    reader = ProjectReader(root, "fixture")
    row = reader.requirements()["requirements"][0]
    assert row["origin"] == "derived"
    assert row["reason"] == yaml.safe_load(path.read_text())["reason"]
    assert json.dumps(row)


def test_baseline_load_case_is_not_covered_by_shallower_user_requirement():
    from engenia.core.baseline import covers

    item = {"check": "stress_safety_factor", "unit": "ratio", "limit": {"min": 1},
            "params": {"depth_m": 3}}
    requirement = Requirement.model_validate({
        "id": "R-001", "statement": "The hull shall keep safety factor 2 at 1 m depth.",
        "value": 2, "unit": "ratio", "status": "approved", "affects": ["simulation"],
        "verify": {"method": "simulation", "check": "stress_safety_factor", "limit": {"min": 2}},
    })
    assert not covers(requirement, item)
    assert covers(requirement.model_copy(update={
        "statement": "The hull shall keep safety factor 2 at 4 m depth."
    }), item)


def test_intake_uses_the_projects_selected_pack(root, baseline_pack):
    folder = root / "projects/fixture"
    folder.mkdir()
    (folder / "project.yaml").write_text("pack: enclosure\n")
    result = run_intake("Walls at least 4 mm.", "fixture", root)
    assert "enclosure pack" in result.how
    assert len(result.paths) == 1
