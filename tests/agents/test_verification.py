import json

import pytest
import yaml

from engenia.agents.verification.stage import (
    VerifyPlanStage,
    deterministic_plan,
    dump_plan,
    load_requirements,
    validate_plan,
    verifier_docs,
)
from engenia.core.hashing import file_hash
from engenia.harness.limits import Limits
from engenia.harness.llm import FakeLLM, LLMError, text_response, tool_call_response


def good_plan(root):
    return deterministic_plan(load_requirements(root / "projects" / "demo" / "spec"),
                              {"printing": {"bed_mm": [220, 220, 250]}})


def validate(root, plan):
    return validate_plan(
        yaml.safe_dump(plan),
        load_requirements(root / "projects" / "demo" / "spec"),
        set(verifier_docs(root)),
    )


# --- validation ------------------------------------------------------------------------------


def test_deterministic_plan_is_valid_and_marks_missing_verifier_unverified(root):
    report = validate(root, good_plan(root))
    assert report.ok, report.problems
    assert [i.requirement for i in report.items] == ["R-001", "R-002", "R-003"]  # R-004 is draft
    assert report.unverified == ["R-003"]
    assert report.items[1].params == {"bed_mm": [220, 220, 250]}


def test_check_citing_unknown_requirement_is_rejected(root):
    plan = good_plan(root)
    plan.append({"id": "C-009", "requirement": "R-999", "check": "mass_budget",
                 "params": {}, "limit": {"max": 400}})
    report = validate(root, plan)
    assert any("R-999" in p and "does not exist" in p for p in report.problems)


def test_null_requirement_is_rejected(root):
    plan = good_plan(root)
    plan.append({"id": "C-009", "requirement": None, "check": "mass_budget",
                 "params": {}, "limit": {"max": 400}})
    assert any("null" in p for p in validate(root, plan).problems)


def test_check_whose_limit_differs_from_its_requirement_is_rejected(root):
    plan = good_plan(root)
    plan[0]["limit"] = {"max": 450}
    report = validate(root, plan)
    assert any("R-001" in p and "differs" in p for p in report.problems)


def test_params_may_be_added_without_changing_the_limit(root):
    plan = good_plan(root)
    plan[0]["params"] = {"anything": 1}
    assert validate(root, plan).ok


def test_unknown_verifier_is_rejected(root):
    plan = good_plan(root)
    plan.append({"id": "C-009", "requirement": "R-001", "check": "made_up",
                 "params": {}, "limit": {"max": 400}})
    assert any("made_up" in p for p in validate(root, plan).problems)


def test_approved_requirement_without_item_is_rejected(root):
    plan = [i for i in good_plan(root) if i["requirement"] != "R-002"]
    assert any(p.startswith("R-002") for p in validate(root, plan).problems)


def test_item_running_a_different_verifier_does_not_cover_the_requirement(root):
    plan = good_plan(root)
    plan[1]["check"] = "wall_thickness_min"
    assert any(p.startswith("R-002") for p in validate(root, plan).problems)


def test_bad_yaml_and_non_list_are_problems(root):
    reqs = load_requirements(root / "projects" / "demo" / "spec")
    assert not validate_plan("a: [", reqs, set()).ok
    assert not validate_plan("a: 1", reqs, set()).ok


# --- the stage without a model -----------------------------------------------------------------


def test_without_a_model_a_deterministic_plan_is_written_and_built(root, verify_ctx):
    stage = VerifyPlanStage()
    stage.generate(verify_ctx)
    stage.build(verify_ctx)

    digest = file_hash(verify_ctx.checks_path)
    assert (verify_ctx.checks_path.parent / "checks.sha256").read_text().strip() == digest
    assert (verify_ctx.build_dir / "checks.yaml").is_file()
    facts = stage.interface(verify_ctx)
    assert facts["source"] == "deterministic"
    assert facts["checks_sha256"] == digest
    assert facts["unverified"] == ["R-003"]
    assert not (verify_ctx.checks_path.parent / "APPROVED").exists()


def test_build_rejects_an_invalid_plan(root, verify_ctx):
    stage = VerifyPlanStage()
    stage.generate(verify_ctx)
    plan = yaml.safe_load(verify_ctx.checks_path.read_text())
    plan[0]["limit"] = {"max": 999}
    verify_ctx.checks_path.write_text(yaml.safe_dump(plan))
    with pytest.raises(ValueError, match="differs"):
        stage.build(verify_ctx)
    assert not (verify_ctx.checks_path.parent / "checks.sha256").exists()


def test_model_unavailable_falls_back(root, verify_ctx):
    llm = FakeLLM([LLMError("down")])
    stage = VerifyPlanStage(chat_fn=llm, model="m")
    stage.generate(verify_ctx)
    stage.build(verify_ctx)
    assert stage.interface(verify_ctx)["source"] == "deterministic"


# --- the stage with a model --------------------------------------------------------------------


def test_model_plan_is_used_and_model_name_comes_from_env(root, verify_ctx, monkeypatch):
    monkeypatch.setenv("ENGENIA_VERIFY_MODEL", "verify-model")
    text = dump_plan(good_plan(root))
    llm = FakeLLM([
        tool_call_response(("write_file", {"path": "checks.yaml", "content": text})),
        text_response("done"),
    ])
    stage = VerifyPlanStage(chat_fn=llm)
    stage.generate(verify_ctx)
    stage.build(verify_ctx)
    assert llm.calls[0].model == "verify-model"
    assert stage.interface(verify_ctx)["source"] == "model"
    assert verify_ctx.checks_path.read_text() == text


def test_invalid_model_plan_gets_feedback_then_succeeds(root, verify_ctx):
    bad = good_plan(root)
    bad[0]["limit"] = {"max": 1000}
    good = dump_plan(good_plan(root))
    llm = FakeLLM([
        tool_call_response(("write_file", {"path": "checks.yaml", "content": dump_plan(bad)})),
        text_response("done"),
        tool_call_response(("write_file", {"path": "checks.yaml", "content": good})),
        text_response("fixed"),
    ])
    stage = VerifyPlanStage(chat_fn=llm, model="m")
    stage.generate(verify_ctx)
    assert any("differs" in str(m["content"]) for m in llm.calls[2].messages)
    assert stage.interface(verify_ctx)["source"] == "model"


def test_model_that_never_fixes_its_plan_falls_back(root, verify_ctx):
    bad = good_plan(root)
    bad[0]["limit"] = {"max": 1000}
    attempt = [
        tool_call_response(("write_file", {"path": "checks.yaml", "content": dump_plan(bad)})),
        text_response("done"),
    ]
    llm = FakeLLM(attempt * 2)
    stage = VerifyPlanStage(chat_fn=llm, model="m", limits=Limits(max_attempts=2))
    stage.generate(verify_ctx)
    stage.build(verify_ctx)
    assert stage.interface(verify_ctx)["source"] == "deterministic"


# --- the sandbox -------------------------------------------------------------------------------

SECRET_SRC = "SECRET-FROM-SRC-7f3a"
SECRET_BUILD = "SECRET-FROM-BUILD-91bc"


def test_sandbox_cannot_read_src_or_build_and_input_has_neither(root, verify_ctx):
    project = root / "projects" / "demo"
    (project / "src" / "mechanical").mkdir(parents=True)
    (project / "src" / "mechanical" / "model.py").write_text(SECRET_SRC)
    (project / "build" / "mechanical").mkdir(parents=True)
    (project / "build" / "mechanical" / "verdict.json").write_text(f'["{SECRET_BUILD}"]')
    (project / "build" / "verify_plan").mkdir(parents=True)
    (project / "build" / "verify_plan" / "verdict.json").write_text(f'["{SECRET_BUILD}"]')

    llm = FakeLLM([
        tool_call_response(
            ("write_file", {"path": "../src/mechanical/model.py", "content": "x"}),
            ("write_file", {"path": str(project / "build" / "mechanical" / "x"), "content": "x"}),
            ("write_file", {"path": "APPROVED", "content": "sha256:" + "0" * 64}),
            ("write_file", {"path": "checks.sha256", "content": "x"}),
            ("read_verdicts", {}),
            ("read_file", {"path": "../src/mechanical/model.py"}),
        ),
        text_response("done"),
    ])
    VerifyPlanStage(chat_fn=llm, model="m", limits=Limits(max_attempts=1)).generate(verify_ctx)

    # nothing from src/ or build/ was sent to the model, in any message
    sent = json.dumps([call.messages for call in llm.calls])
    assert SECRET_SRC not in sent and SECRET_BUILD not in sent
    results = [m["content"] for m in llm.calls[1].messages if m["role"] == "tool"]
    assert "outside the allowed directory" in results[0]
    assert "outside the allowed directory" in results[1]
    assert "you may write only checks.yaml" in results[2]
    assert "you may write only checks.yaml" in results[3]
    assert results[4] == "[]"  # read_verdicts has nothing to read
    assert "unknown tool" in results[5]
    # and nothing was written outside checks/checks.yaml by the agent
    assert (project / "src" / "mechanical" / "model.py").read_text() == SECRET_SRC
    assert not (project / "checks" / "APPROVED").exists()


def test_input_to_model_is_only_requirements_and_verifier_docs(root, verify_ctx):
    (root / "packs" / "buoy" / "pack.yaml").write_text("secret_pack: PACK-SENTINEL")
    llm = FakeLLM([text_response("nothing")])
    VerifyPlanStage(chat_fn=llm, model="m", limits=Limits(max_attempts=1)).generate(verify_ctx)
    first_user = [m["content"] for m in llm.calls[0].messages if m["role"] == "user"][0]
    assert "R-001" in first_user and "mass_budget" in first_user
    assert "PACK-SENTINEL" not in json.dumps(llm.calls[0].messages)
    assert "R-004" not in first_user  # draft requirement
