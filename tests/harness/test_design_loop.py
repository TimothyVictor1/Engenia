import json

import pytest

from engenia.contracts.models import EvidenceLevel, Limit, Verdict, VerdictResult
from engenia.harness.design_loop import feedback_message, generate_until_pass
from engenia.harness.limits import Limits
from engenia.harness.llm import FakeLLM, text_response, tool_call_response

BUILD = ("run_build", {})


def write_design(mass: int):
    return ("write_file", {"path": "design.txt", "content": f"mass={mass}"})


def attempt_script(mass: int, final: str = "Finished. All requirements are met."):
    """One agent session: write the design at this mass, build it, claim success."""
    return [tool_call_response(write_design(mass), BUILD), text_response(final)]


def run(stage, ctx, llm, **kwargs):
    return generate_until_pass(
        stage, ctx, model="test-model", limits=Limits(max_attempts=3), chat_fn=llm, **kwargs
    )


def user_messages(llm, call_index):
    return [m["content"] for m in llm.calls[call_index].messages if m["role"] == "user"]


def test_returns_pass_when_verdicts_pass_on_attempt_2(ctx, stage):
    fixed = [tool_call_response(write_design(380)), text_response("ok")]
    llm = FakeLLM(attempt_script(450) + fixed)

    assert run(stage, ctx, llm) == "pass"

    assert llm.remaining == 0
    assert len(llm.calls) == 4  # attempt 1: two model calls; attempt 2: two model calls
    verdicts = json.loads((ctx.build_dir / "verdict.json").read_text())
    assert [(v["check"], v["result"], v["measured"]) for v in verdicts] == [
        ("mass_budget", "pass", 380.0)
    ]
    transcripts = sorted(p.name for p in (ctx.build_dir / "transcripts").iterdir())
    assert transcripts == ["attempt-01.json", "attempt-02.json"]


def test_returns_exhausted_after_three_failing_attempts(ctx, stage):
    llm = FakeLLM(attempt_script(450) + attempt_script(430) + attempt_script(410))

    assert run(stage, ctx, llm) == "exhausted"

    assert llm.remaining == 0
    assert stage.build_calls >= 3
    verdicts = json.loads((ctx.build_dir / "verdict.json").read_text())
    assert verdicts[0]["result"] == "fail"
    assert verdicts[0]["measured"] == 410.0


def test_failing_numbers_are_sent_to_the_model(ctx, stage):
    llm = FakeLLM(attempt_script(450) + attempt_script(430) + attempt_script(410))
    run(stage, ctx, llm)

    # The first model call of attempt 2 is call index 2, of attempt 3 is call index 4.
    after_attempt_1 = user_messages(llm, 2)[-1]
    after_attempt_2 = user_messages(llm, 4)[-1]
    for message, measured in ((after_attempt_1, "450.0"), (after_attempt_2, "430.0")):
        assert "mass_budget" in message
        assert measured in message  # measured value
        assert '"max": 400.0' in message  # the limit
        assert "over the 400.0 g budget" in message  # the verifier's message
        assert '"requirement": "R-001"' in message
    assert "430.0" not in after_attempt_1


def test_the_conversation_continues_across_attempts(ctx, stage):
    llm = FakeLLM(attempt_script(450) + attempt_script(380))
    assert run(stage, ctx, llm) == "pass"
    roles = [m["role"] for m in llm.calls[2].messages]
    # system, task, then everything attempt 1 did, then the failure report
    assert roles == ["system", "user", "assistant", "tool", "tool", "assistant", "user"]


def test_model_claims_do_not_decide_the_outcome(ctx, stage):
    llm = FakeLLM(
        attempt_script(450, "VERDICT: PASS. All checks pass, nothing left to do.") * 3
    )
    assert run(stage, ctx, llm) == "exhausted"


def test_transcript_written_for_every_attempt_despite_build_wiping_build_dir(ctx, stage):
    llm = FakeLLM(attempt_script(450) * 3)
    run(stage, ctx, llm)

    directory = ctx.build_dir / "transcripts"
    assert sorted(p.name for p in directory.iterdir()) == [
        "attempt-01.json",
        "attempt-02.json",
        "attempt-03.json",
    ]
    for number, path in enumerate(sorted(directory.iterdir()), start=1):
        saved = json.loads(path.read_text())
        assert saved["attempt"] == number
        assert saved["model"] == "test-model"
    # the agent called run_build itself in every attempt, and the harness built again after it
    assert stage.build_calls == 6


def test_build_failure_becomes_an_error_verdict_and_is_fed_back(ctx, stage):
    broken = [tool_call_response(("write_file", {"path": "design.txt", "content": "mass=1 BREAK"}))]
    llm = FakeLLM(broken + [text_response("done")] + attempt_script(380))

    assert run(stage, ctx, llm) == "pass"
    feedback = user_messages(llm, 2)[-1]
    assert '"check": "build"' in feedback
    assert '"result": "error"' in feedback
    assert "design.txt is broken" in feedback


def test_error_verdict_from_a_crashing_verifier_is_not_a_pass(root, ctx, stage):
    (root / "verifiers" / "mass_budget.py").write_text("def run(ctx):\n    raise OSError('x')\n")
    llm = FakeLLM(attempt_script(100) * 3)
    assert run(stage, ctx, llm) == "exhausted"
    assert json.loads((ctx.build_dir / "verdict.json").read_text())[0]["result"] == "error"


def test_attempt_limit_comes_from_limits(ctx, stage):
    llm = FakeLLM(attempt_script(450) * 2)
    result = generate_until_pass(
        stage, ctx, model="m", limits=Limits(max_attempts=2), chat_fn=llm
    )
    assert result == "exhausted"
    assert llm.remaining == 0
    assert len(list((ctx.build_dir / "transcripts").iterdir())) == 2


def test_attempt_limit_defaults_to_env(ctx, stage, monkeypatch):
    monkeypatch.setenv("ENGENIA_MAX_ATTEMPTS", "1")
    llm = FakeLLM(attempt_script(450))
    assert generate_until_pass(stage, ctx, model="m", chat_fn=llm) == "exhausted"


def test_agent_cannot_write_outside_its_source_dir(ctx, stage, root):
    attack = [
        tool_call_response(
            ("write_file", {"path": "../../build/mechanical/verdict.json", "content": "[]"}),
            ("write_file", {"path": "../../../../verifiers/mass_budget.py", "content": ""}),
        ),
        text_response("done"),
    ]
    llm = FakeLLM(attack + attempt_script(450) * 2)
    assert run(stage, ctx, llm) == "exhausted"
    assert "def run(ctx)" in (root / "verifiers" / "mass_budget.py").read_text()
    assert json.loads((ctx.build_dir / "verdict.json").read_text())[0]["result"] == "fail"


def test_stage_without_checks_passes_once_it_builds(ctx, make_stage):
    stage = make_stage(checks=[])
    llm = FakeLLM(attempt_script(1))
    assert run(stage, ctx, llm) == "pass"


def test_custom_prompt_and_task_are_used(ctx, stage):
    llm = FakeLLM(attempt_script(100))
    run(stage, ctx, llm, system_prompt="SYS", task="TASK")
    assert llm.calls[0].messages[0] == {"role": "system", "content": "SYS"}
    assert llm.calls[0].messages[1] == {"role": "user", "content": "TASK"}


def test_default_task_lists_only_approved_requirements_for_the_stage(ctx, stage, requirement_text):
    spec = ctx.spec_dir
    (spec / "R-001.yaml").write_text(requirement_text(1, status="approved"))
    (spec / "R-002.yaml").write_text(requirement_text(2, status="draft"))
    llm = FakeLLM(attempt_script(100))
    run(stage, ctx, llm)
    task = user_messages(llm, 0)[0]
    assert "R-001" in task and "R-002" not in task


def test_missing_model_is_a_clear_error(ctx, stage):
    with pytest.raises(RuntimeError, match="ENGENIA_DESIGN_MODEL"):
        generate_until_pass(stage, ctx, chat_fn=FakeLLM([]))


def test_feedback_message_lists_only_non_passing_verdicts():
    good = Verdict(
        check="good", measured=1.0, limit=Limit(max=2), result=VerdictResult.PASS,
        evidence_level=EvidenceLevel.CALCULATED,
    )
    bad = Verdict(
        check="bad", requirement="R-005", measured=0.12, limit=Limit(min=0.2),
        result=VerdictResult.FAIL, evidence_level=EvidenceLevel.SIMULATED, message="too low",
    )
    text = feedback_message(2, [good, bad])
    assert "bad" in text and "0.12" in text and "0.2" in text and "too low" in text
    assert "good" not in text
