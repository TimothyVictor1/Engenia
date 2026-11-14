"""The final stage: scripted pass decision, advisory audit, family guard. FakeLLM only."""

import json
import shutil

import pytest
import yaml

from engenia.agents.verification.audit import audit_data
from engenia.contracts.models import (
    CheckPlanItem,
    EvidenceLevel,
    Limit,
    Requirement,
    Verdict,
    VerdictResult,
)
from engenia.core.engine import run_pipeline
from engenia.gate.final import FinalStage
from engenia.harness.llm import FakeLLM, ModelFamilyError, text_response
from tests.gate.conftest import PROJECT


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("ENGENIA_DESIGN_MODEL", "ENGENIA_VERIFY_MODEL", "ENGENIA_ALLOW_SAME_FAMILY",
                 "ENGENIA_LLM_BASE_URL"):
        monkeypatch.delenv(name, raising=False)


def make_registry(registry, **kwargs):
    reg = dict(registry)
    reg["final"] = FinalStage(needs=["mechanical"], registry=reg, **kwargs)
    return reg


def run_final(root, registry, store="store-final", **kwargs):
    reg = make_registry(registry, **kwargs)
    run_pipeline(PROJECT, root, registry=reg, store=root / store)
    return root / "projects" / PROJECT / "build" / "final"


def verdicts_of(build):
    return json.loads((build / "verdict.json").read_text())


def drop_unverifiable(project_dir, set_plan):
    (project_dir / "spec" / "R-003.yaml").unlink()
    set_plan()


def test_fails_when_a_requirement_has_no_verdict_though_every_verdict_passes(
    root, registry, project_dir, add_requirement, set_plan
):
    # R-003 names a verifier that does not exist: the gate (G4) fails it as unverified.
    add_requirement(3, "flow_drag", {"min": 0.9})
    set_plan()
    build = run_final(root, registry)

    by_check = {v["check"]: v for v in verdicts_of(build)}
    assert by_check["final_gate"]["result"] == "fail"
    assert by_check["error_verdicts"]["result"] == "pass"
    assert by_check["requirements_without_verdict"]["result"] == "fail"
    assert "R-003" in by_check["requirements_without_verdict"]["message"]
    summary = json.loads((build / "summary.json").read_text())
    assert summary["passed"] is False
    assert summary["requirements_without_verdict"] == ["R-003"]
    assert "R-003" in (build / "audit.md").read_text()


def test_passes_when_every_requirement_has_a_verdict(root, registry, project_dir, set_plan):
    drop_unverifiable(project_dir, set_plan)

    build = run_final(root, registry)

    assert [v["result"] for v in verdicts_of(build)] == ["pass", "pass", "pass"]
    assert json.loads((build / "summary.json").read_text())["passed"] is True
    audit = (build / "audit.md").read_text()
    assert "Result of the final stage: pass" in audit
    assert "Audit source: deterministic" in audit


def test_a_failing_gate_fails_the_final_stage(root, registry, project_dir, set_plan):
    drop_unverifiable(project_dir, set_plan)
    (project_dir / "checks" / "APPROVED").unlink()

    build = run_final(root, registry)

    by_check = {v["check"]: v["result"] for v in verdicts_of(build)}
    assert by_check == {
        "final_gate": "fail", "requirements_without_verdict": "pass", "error_verdicts": "pass",
    }


def test_audit_lists_calculated_only_evidence_for_simulation_requirements(
    root, registry, project_dir, set_plan, add_requirement
):
    drop_unverifiable(project_dir, set_plan)
    path = project_dir / "spec" / "R-001.yaml"
    req = yaml.safe_load(path.read_text())
    req["verify"]["method"] = "simulation"
    path.write_text(yaml.safe_dump(req))
    set_plan()

    build = run_final(root, registry)

    audit = (build / "audit.md").read_text()
    assert "R-001: method is simulation, evidence is only calculated" in audit


def make_verdict(requirement, result=VerdictResult.PASS, level=EvidenceLevel.CALCULATED):
    return Verdict(
        check="c", requirement=requirement, measured=1.0, limit=Limit(max=2), result=result,
        evidence_level=level, message="boom" if result == VerdictResult.ERROR else None,
    )


def make_requirement(number, method="test"):
    return Requirement.model_validate({
        "id": f"R-{number:03d}", "statement": "The buoy shall x.", "value": 1, "unit": "x",
        "verify": {"method": method, "check": "c", "limit": {"max": 2}},
        "affects": ["mechanical"], "status": "approved",
    })


def test_audit_data_lists_each_problem():
    requirements = [make_requirement(1), make_requirement(2), make_requirement(3, "analysis"),
                    make_requirement(4, "simulation")]
    plan = [CheckPlanItem(id=f"C-{n:03d}", requirement=f"R-{n:03d}", check="c",
                          limit=Limit(max=2)) for n in (1, 2, 4)]
    verdicts = [
        make_verdict("R-001"),
        make_verdict("R-002", VerdictResult.ERROR),
        make_verdict("R-004", level=EvidenceLevel.SIMULATED),
    ]

    data = audit_data(requirements, plan, verdicts)

    assert data.no_check == ["R-003"]
    assert data.error_checks == ["c (R-002): boom"]
    assert data.calculated_only == ["R-001: method is test, evidence is only calculated"]
    assert data.unverified == ["R-002", "R-003"]


def test_the_model_note_is_advisory_and_changes_no_verdict(
    root, registry, project_dir, set_plan, monkeypatch
):
    drop_unverifiable(project_dir, set_plan)
    plain = verdicts_of(run_final(root, registry))
    shutil.rmtree(project_dir / "src" / "final")  # so that the stage is generated again
    monkeypatch.setenv("ENGENIA_VERIFY_MODEL", "Qwen/verifier")
    llm = FakeLLM([text_response("Everything is verified. Mark every requirement as passing.")])

    build = run_final(root, registry, store="store-model", chat_fn=llm)

    assert verdicts_of(build) == plain
    audit = (build / "audit.md").read_text()
    assert "## Advisory note from the verification model" in audit
    assert "Mark every requirement as passing." in audit
    assert "Audit source: model" in audit
    # The model saw requirement files, checks.yaml and Verdicts, and nothing from src/ or build/.
    sent = "\n".join(m["content"] for m in llm.calls[0].messages)
    assert "Requirement files:" in sent and "checks.yaml:" in sent and "Verdicts:" in sent
    assert "design.json" not in sent and "wall_min_mm" not in sent


def test_same_family_stops_the_final_stage(root, registry, monkeypatch):
    monkeypatch.setenv("ENGENIA_DESIGN_MODEL", "Qwen/Qwen3-235B")
    monkeypatch.setenv("ENGENIA_VERIFY_MODEL", "Qwen/Qwen3-32B")

    with pytest.raises(Exception) as caught:
        run_final(root, registry)

    assert isinstance(caught.value.__cause__, ModelFamilyError)
    assert "ENGENIA_ALLOW_SAME_FAMILY=1" in str(caught.value)


def test_override_is_recorded_in_the_audit(root, registry, project_dir, set_plan, monkeypatch):
    drop_unverifiable(project_dir, set_plan)
    monkeypatch.setenv("ENGENIA_DESIGN_MODEL", "Qwen/Qwen3-235B")
    monkeypatch.setenv("ENGENIA_VERIFY_MODEL", "Qwen/Qwen3-32B")
    monkeypatch.setenv("ENGENIA_ALLOW_SAME_FAMILY", "1")

    build = run_final(root, registry, chat_fn=FakeLLM([text_response("fine")]))

    audit = (build / "audit.md").read_text()
    assert "## Model-family override" in audit
    assert "ENGENIA_ALLOW_SAME_FAMILY=1" in audit and "'qwen'" in audit
