import json
import shutil

import pytest
import yaml

from engenia.contracts.models import Limit, Verdict, VerdictResult
from engenia.gate.canary import CASES
from engenia.gate.gate import recompute_result, run_gate, verdict_problem

PROJECT = "demo"

ALWAYS_PASS = '''\
"""Always passes."""
from engenia.contracts.models import EvidenceLevel, Verdict, VerdictResult


def run(ctx):
    return Verdict(check=ctx.check, requirement=ctx.requirement, measured=0.0, limit=ctx.limit,
                   result=VerdictResult.PASS, evidence_level=EvidenceLevel.CALCULATED)
'''


def gate(root, registry):
    return run_gate(PROJECT, root, registry=registry)


def problems(result, name):
    return result.report["gates"][name]["problems"]


def failed(result):
    return sorted(n for n, g in result.report["gates"].items() if not g["passed"])


def test_clean_project_passes_and_unverified_is_reported(root, registry, project_dir):
    result = gate(root, registry)
    assert result.passed, result.report["gates"]
    assert result.exit_code == 0
    assert result.report["unverified"] == [
        {"requirement": "R-003", "check": "detection_rate", "status": "unverified",
         "reason": "no verifier verifiers/detection_rate.py yet"}
    ]
    assert {v["requirement"] for v in result.report["verdicts"]} == {"R-001", "R-002"}
    saved = json.loads((project_dir / "build" / "gate" / "report.json").read_text())
    assert saved == result.report


def test_report_is_identical_between_runs(root, registry, project_dir):
    gate(root, registry)
    first = (project_dir / "build" / "gate" / "report.json").read_text()
    gate(root, registry)
    assert (project_dir / "build" / "gate" / "report.json").read_text() == first


def test_failing_measurement_fails_g1(root, registry, project_dir):
    (project_dir / "src" / "mechanical" / "design.json").write_text(
        json.dumps({"dry_mass_g": 500.0, "wall_min_mm": 3.5}))
    from engenia.core.engine import run_pipeline

    run_pipeline(PROJECT, root, registry=registry, store=root / "store2")
    result = gate(root, registry)
    assert not result.passed
    assert failed(result) == ["G1"]
    assert "R-001" in problems(result, "G1")[0]


def test_unapproved_plan_fails(root, registry, set_plan):
    set_plan(approve=False)
    result = gate(root, registry)
    assert not result.passed
    assert "has not been approved" in problems(result, "approval")[0]


def test_editing_checks_after_approval_fails_the_gate(root, registry, project_dir):
    path = project_dir / "checks" / "checks.yaml"
    plan = yaml.safe_load(path.read_text())
    plan[0]["params"] = {"tweak": 1}  # a harmless-looking edit; APPROVED still holds the old hash
    path.write_text(yaml.safe_dump(plan))
    result = gate(root, registry)
    assert not result.passed
    assert "changed after approval" in problems(result, "approval")[0]


def test_editing_checks_to_loosen_a_limit_fails_even_if_reapproved(root, registry, set_plan,
                                                                   project_dir):
    plan = yaml.safe_load((project_dir / "checks" / "checks.yaml").read_text())
    plan[0]["limit"] = {"max": 9999}
    set_plan(plan, approve=True)
    result = gate(root, registry)
    assert not result.passed
    assert any("differs from the requirement" in p for p in problems(result, "approval"))


# --- G2 ----------------------------------------------------------------------------------------


def test_recompute_result():
    assert recompute_result(5, Limit(max=6)) == VerdictResult.PASS
    assert recompute_result(7, Limit(max=6)) == VerdictResult.FAIL
    assert recompute_result(3, Limit(min=3, max=4)) == VerdictResult.PASS
    assert recompute_result(1.0, Limit(equals=1)) == VerdictResult.PASS
    assert recompute_result(0.0, Limit(equals=1)) == VerdictResult.FAIL
    assert recompute_result(1, Limit()) == VerdictResult.FAIL
    assert recompute_result(None, Limit(max=1)) is None
    assert recompute_result(float("nan"), Limit(max=1)) is None


def stored_verdict(result: str, measured, limit) -> list[dict]:
    return [{"check": "mass_budget", "requirement": "R-001", "measured": measured,
             "limit": limit, "result": result, "evidence_level": "calculated",
             "artifact": None, "message": None}]


def test_g2_catches_a_verdict_edited_to_pass(root, registry, project_dir):
    path = project_dir / "build" / "mechanical" / "verdict.json"
    path.write_text(json.dumps(stored_verdict("pass", 450.0, {"max": 400.0})))
    result = gate(root, registry)
    assert not result.passed
    assert "G2" in failed(result)
    assert "gives fail" in problems(result, "G2")[0]


def test_g2_catches_pass_without_a_measured_value(root, registry, project_dir):
    path = project_dir / "build" / "mechanical" / "verdict.json"
    path.write_text(json.dumps(stored_verdict("pass", None, {"max": 400.0})))
    assert "G2" in failed(gate(root, registry))


def test_g2_accepts_a_consistent_stored_verdict(root, registry, project_dir):
    path = project_dir / "build" / "mechanical" / "verdict.json"
    path.write_text(json.dumps(stored_verdict("pass", 280.0, {"max": 400.0})))
    assert gate(root, registry).passed


def test_g2_flags_an_unreadable_stored_verdict(root, registry, project_dir):
    (project_dir / "build" / "mechanical" / "verdict.json").write_text("{nope")
    assert "G2" in failed(gate(root, registry))


def test_verdict_problem_ignores_error_results():
    from engenia.contracts.models import EvidenceLevel

    verdict = Verdict(check="c", measured=None, limit=Limit(), result=VerdictResult.ERROR,
                      evidence_level=EvidenceLevel.CALCULATED)
    assert verdict_problem(verdict) is None


# --- G3 ----------------------------------------------------------------------------------------


def test_g3_fails_when_a_build_output_is_altered_by_hand(root, registry, project_dir):
    (project_dir / "build" / "mechanical" / "design.json").write_text(
        json.dumps({"dry_mass_g": 1.0, "wall_min_mm": 9.0}))
    result = gate(root, registry)
    assert not result.passed
    assert "G3" in failed(result)
    assert "mechanical: design.json: differs from rebuild" in problems(result, "G3")


def test_g3_fails_for_an_extra_file_and_ignores_transcripts(root, registry, project_dir):
    transcripts = project_dir / "build" / "mechanical" / "transcripts"
    transcripts.mkdir()
    (transcripts / "attempt-01.json").write_text("{}")
    assert gate(root, registry).passed  # transcripts are not outputs

    (project_dir / "build" / "mechanical" / "extra.txt").write_text("hand made")
    result = gate(root, registry)
    assert "mechanical: extra.txt: in build/ but not produced by rebuild" in problems(result, "G3")


def test_g3_fails_when_interface_json_is_edited(root, registry, project_dir):
    path = project_dir / "build" / "mechanical" / "interface.json"
    data = json.loads(path.read_text())
    data["facts"]["dry_mass_g"] = 1.0
    path.write_text(json.dumps(data))
    result = gate(root, registry)
    assert "mechanical: interface.json facts differ from the rebuild" in problems(result, "G3")


def test_g3_fails_when_a_stage_was_never_built(root, registry, project_dir):
    shutil.rmtree(project_dir / "build" / "mechanical")
    assert "does not exist" in problems(gate(root, registry), "G3")[0]


def test_g3_fails_for_a_stage_that_does_not_rebuild_identically(root, registry, project_dir):
    counter = {"n": 0}
    original = registry["mechanical"].build

    def drifting(ctx):
        original(ctx)
        counter["n"] += 1
        (ctx.build_dir / "stamp.txt").write_text(str(counter["n"]))

    registry["mechanical"].build = drifting
    from engenia.core.engine import run_pipeline

    run_pipeline(PROJECT, root, registry=registry, store=root / "store3")
    assert "G3" in failed(gate(root, registry))


def test_g3_does_not_touch_the_real_build(root, registry, project_dir):
    before = sorted(p.name for p in (project_dir / "build" / "mechanical").iterdir())
    gate(root, registry)
    assert sorted(p.name for p in (project_dir / "build" / "mechanical").iterdir()) == before


# --- G4 ----------------------------------------------------------------------------------------


def test_g4_fails_when_an_approved_requirement_has_no_verdict(root, registry, set_plan,
                                                              add_requirement, project_dir):
    add_requirement(4, "wall_thickness_min", {"min": 2})
    plan = yaml.safe_load((project_dir / "checks" / "checks.yaml").read_text())
    set_plan(plan, approve=True)  # R-004 has no item, and the plan is approved as it is
    result = gate(root, registry)
    assert not result.passed
    assert "R-004: no pass or fail Verdict" in problems(result, "G4")


def test_g4_ignores_draft_requirements(root, registry, add_requirement):
    add_requirement(4, "wall_thickness_min", {"min": 2}, status="draft")
    assert gate(root, registry).passed


def test_g4_error_verdict_does_not_cover(root, registry, project_dir):
    shutil.rmtree(project_dir / "src" / "mechanical")
    (project_dir / "src" / "mechanical").mkdir()
    (project_dir / "src" / "mechanical" / "design.json").write_text(
        json.dumps({"dry_mass_g": 280.0}))  # wall_min_mm is missing: wall check errors
    run_pipeline_for(root, registry)
    result = gate(root, registry)
    assert "R-002: no pass or fail Verdict" in problems(result, "G4")
    assert "G1" in failed(result)


def run_pipeline_for(root, registry):
    from engenia.core.engine import run_pipeline

    run_pipeline(PROJECT, root, registry=registry, store=root / "store4")


# --- G5 ----------------------------------------------------------------------------------------


def test_g5_fails_when_a_verifier_always_returns_pass(root, registry):
    (root / "verifiers" / "mass_budget.py").write_text(ALWAYS_PASS)
    result = gate(root, registry)
    assert not result.passed
    assert failed(result) == ["G5"]  # G1 and G2 are fooled; only the canary notices
    assert "mass_budget: returned pass on a fixture that must fail" in problems(result, "G5")


def test_g5_fails_closed_for_a_verifier_with_no_fixture(root, registry, project_dir, set_plan):
    (root / "verifiers" / "novel_check.py").write_text(ALWAYS_PASS)
    req = yaml.safe_load((project_dir / "spec" / "R-002.yaml").read_text())
    req["id"], req["verify"]["check"] = "R-005", "novel_check"
    (project_dir / "spec" / "R-005.yaml").write_text(yaml.safe_dump(req))
    from engenia.agents.verification.stage import deterministic_plan, load_requirements

    set_plan(deterministic_plan(load_requirements(project_dir / "spec")))
    result = gate(root, registry)
    assert any("novel_check: no canary fixture" in p for p in problems(result, "G5"))


def test_g5_crashing_verifier_is_a_problem_not_a_crash(root, registry):
    (root / "verifiers" / "mass_budget.py").write_text("def run(ctx):\n    raise RuntimeError('x')")
    result = gate(root, registry)
    assert any("mass_budget" in p for p in problems(result, "G5"))


@pytest.mark.parametrize("name", sorted(CASES))
def test_every_canary_fixture_fails_its_real_verifier(root, name):
    from engenia.gate.gate import _canary

    assert _canary(root, name) is None


def test_every_real_verifier_has_a_canary(root):
    names = {p.stem for p in (root / "verifiers").glob("*.py") if not p.stem.startswith("_")}
    assert names <= set(CASES)
