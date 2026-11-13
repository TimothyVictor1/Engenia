import json

import pytest

from engenia.contracts.models import EvidenceLevel, Limit, Verdict, VerdictResult
from engenia.harness.checks import run_stage_checks
from engenia.harness.limits import Limits
from engenia.harness.llm import FakeLLM, text_response, tool_call_response
from engenia.harness.repair import repair_stage

BUILD = ("run_build", {})


def write_design(mass):
    return ("write_file", {"path": "design.txt", "content": f"mass={mass}"})


def failing_ctx(ctx, stage, mass=450):
    ctx.src_dir.mkdir(parents=True)
    (ctx.src_dir / "design.txt").write_text(f"mass={mass}")
    stage.build(ctx)
    verdicts = run_stage_checks(stage, ctx)
    assert verdicts[0].result == VerdictResult.FAIL
    return verdicts


def repair(stage, ctx, failed, llm, **kwargs):
    return repair_stage(stage, ctx, failed, model="test-model", chat_fn=llm, **kwargs)


def test_failing_check_gets_repaired(ctx, stage):
    failed = failing_ctx(ctx, stage)
    llm = FakeLLM([tool_call_response(write_design(380), BUILD), text_response("fixed")])

    assert repair(stage, ctx, failed, llm) == "repaired"

    assert (ctx.src_dir / "design.txt").read_text() == "mass=380"
    verdicts = json.loads((ctx.build_dir / "verdict.json").read_text())
    assert [v["result"] for v in verdicts] == ["pass"]
    first = [m["content"] for m in llm.calls[0].messages if m["role"] == "user"][0]
    assert "mass_budget" in first and "450.0" in first and '"max": 400' in first
    assert "g budget" in first
    assert "mass=450" in first  # the model is shown the current source


def test_model_gets_only_the_three_tools(ctx, stage):
    failed = failing_ctx(ctx, stage)
    llm = FakeLLM([tool_call_response(write_design(380)), text_response("ok")])
    repair(stage, ctx, failed, llm)
    names = {t["function"]["name"] for t in llm.calls[0].tools}
    assert names == {"write_file", "run_build", "read_verdicts"}


def test_writes_outside_the_stage_src_are_refused(ctx, stage, root):
    failed = failing_ctx(ctx, stage)
    escape = ("write_file", {"path": "../electronics/design.txt", "content": "x"})
    llm = FakeLLM([tool_call_response(escape, write_design(380)), text_response("ok")])
    assert repair(stage, ctx, failed, llm) == "repaired"
    assert not (ctx.src_dir.parent / "electronics").exists()


def test_exhausted_after_three_attempts_and_src_restored(ctx, stage):
    failed = failing_ctx(ctx, stage)
    script = []
    for mass in (440, 430, 420):
        script += [tool_call_response(write_design(mass), BUILD), text_response("done")]
    llm = FakeLLM(script)

    assert repair(stage, ctx, failed, llm, limits=Limits(max_attempts=10)) == "exhausted"

    assert llm.remaining == 0  # never more than three attempts
    assert (ctx.src_dir / "design.txt").read_text() == "mass=450"


def test_no_key_gives_no_model_and_changes_nothing(ctx, stage, monkeypatch):
    failed = failing_ctx(ctx, stage)
    monkeypatch.setenv("ENGENIA_DESIGN_MODEL", "some/model")
    monkeypatch.setenv("ENGENIA_LLM_BASE_URL", "https://example.invalid/v1")
    before = sorted(p.relative_to(ctx.root).as_posix() for p in ctx.root.rglob("*"))
    contents = (ctx.src_dir / "design.txt").read_bytes()

    assert repair_stage(stage, ctx, failed) == "no_model"

    assert sorted(p.relative_to(ctx.root).as_posix() for p in ctx.root.rglob("*")) == before
    assert (ctx.src_dir / "design.txt").read_bytes() == contents


def test_no_model_name_gives_no_model(ctx, stage):
    failed = failing_ctx(ctx, stage)
    assert repair_stage(stage, ctx, failed, chat_fn=FakeLLM([])) == "no_model"


def test_nothing_to_repair_is_an_error(ctx, stage):
    with pytest.raises(ValueError):
        repair(stage, ctx, [], FakeLLM([]))


def test_verdict_objects_are_summarised_with_numbers(ctx, stage):
    failed = [Verdict(check="x", requirement="R-001", measured=1.5, limit=Limit(min=2),
                      result=VerdictResult.FAIL, evidence_level=EvidenceLevel.CALCULATED,
                      message="too low")]
    ctx.src_dir.mkdir(parents=True)
    (ctx.src_dir / "design.txt").write_text("mass=100")
    llm = FakeLLM([text_response("nothing")])
    repair(stage, ctx, failed, llm)
    text = llm.calls[0].messages[1]["content"]
    assert '"measured": 1.5' in text and '"min": 2' in text and "too low" in text
