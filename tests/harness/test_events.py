"""Harness events: code diffs, record_decision, verdicts; no leaks, no change to outputs."""

import json

import pytest

from engenia.core import events
from engenia.harness.design_loop import generate_until_pass
from engenia.harness.limits import Limits
from engenia.harness.llm import FakeLLM, text_response, tool_call_response
from engenia.harness.tools import Toolbox, ToolError

DECISION = ("record_decision", {
    "title": "Wall thickness", "choice": "3.0 mm", "reason": "wall 3.0 mm, because R-004 min 3 mm",
    "requirements": ["R-004"], "numbers": {"wall_mm": 3.0, "min_mm": 3},
    "alternatives": [{"option": "2.0 mm", "why_not": "below the R-004 minimum"}],
})


def write(mass):
    return ("write_file", {"path": "design.txt", "content": f"mass={mass}"})


def read_log(root, run_id=1):
    return [json.loads(x) for x in events.events_path(root, run_id).read_text().splitlines()]


def run(stage, ctx, llm):
    with events.activate(ctx.root, 1):
        return generate_until_pass(
            stage, ctx, model="m", limits=Limits(max_attempts=3), chat_fn=llm
        )


def test_every_write_is_a_code_event_with_a_diff(ctx, stage):
    llm = FakeLLM([
        tool_call_response(write(450), ("run_build", {})), text_response("done"),
        tool_call_response(write(380)), text_response("done"),
    ])
    assert run(stage, ctx, llm) == "pass"
    code = [e for e in read_log(ctx.root) if e["kind"] == "code"]
    assert [e["attempt"] for e in code] == [1, 2]
    assert code[0]["data"]["file"] == "design.txt"
    assert code[0]["data"]["content"] == "mass=450"
    assert "+mass=450" in code[0]["data"]["diff"] and "/dev/null" in code[0]["data"]["diff"]
    assert "-mass=450" in code[1]["data"]["diff"] and "+mass=380" in code[1]["data"]["diff"]


def test_record_decision_becomes_a_decision_event(ctx, stage):
    llm = FakeLLM([tool_call_response(DECISION, write(380)), text_response("ok")])
    run(stage, ctx, llm)
    (decision,) = [e for e in read_log(ctx.root) if e["kind"] == "decision"]
    assert set(decision["data"]) == {
        "title", "choice", "reason", "alternatives", "requirements", "numbers"}
    assert decision["data"]["requirements"] == ["R-004"]
    assert decision["data"]["alternatives"][0]["why_not"].startswith("below")


def test_system_prompt_requires_record_decision(ctx, stage):
    llm = FakeLLM([tool_call_response(write(380)), text_response("ok")])
    run(stage, ctx, llm)
    system = llm.calls[0].messages[0]["content"]
    assert "MUST call record_decision" in system


@pytest.mark.parametrize("bad", [
    {"requirements": ["004"]}, {"numbers": {"x": "big"}}, {"title": " "},
    {"alternatives": [{"option": "a"}]},
])
def test_record_decision_rejects_bad_input(tmp_path, bad):
    tools = Toolbox(tmp_path, build=lambda: None)
    args = {**DECISION[1], **bad}
    with pytest.raises(ToolError):
        tools.call("record_decision", args)


def test_verdicts_and_messages_and_tool_calls_are_logged(ctx, stage):
    llm = FakeLLM([tool_call_response(write(450), ("run_build", {})), text_response("done")]
                  + [tool_call_response(write(380)), text_response("ok")])
    run(stage, ctx, llm)
    found = read_log(ctx.root)
    kinds = {e["kind"] for e in found}
    assert {"message", "tool_call", "tool_result", "code", "verdict"} <= kinds
    verdicts = [e for e in found if e["kind"] == "verdict"]
    assert [(e["attempt"], e["data"]["result"]) for e in verdicts] == [(1, "fail"), (2, "pass")]
    write_call = next(e for e in found if e["kind"] == "tool_call")
    assert write_call["data"]["arguments"] == {"path": "design.txt", "bytes": 8}  # no content


def test_api_keys_are_not_logged(ctx, stage, monkeypatch):
    monkeypatch.setenv("ENGENIA_API_KEY", "super-secret-value-123")
    leak = ("write_file", {"path": "notes.txt", "content": "key super-secret-value-123"})
    run(stage, ctx, FakeLLM([
        tool_call_response(write(380), leak), text_response("used super-secret-value-123")]))
    assert "super-secret-value-123" not in events.events_path(ctx.root, 1).read_text()


def test_events_do_not_change_outputs(ctx, stage, root):
    script = lambda: FakeLLM([tool_call_response(write(380)), text_response("ok")])  # noqa: E731
    generate_until_pass(stage, ctx, model="m", limits=Limits(max_attempts=2), chat_fn=script())
    quiet = (ctx.src_dir / "design.txt").read_text(), (ctx.build_dir / "verdict.json").read_text()
    run(stage, ctx, script())
    assert ((ctx.src_dir / "design.txt").read_text(), (ctx.build_dir / "verdict.json").read_text()
            ) == quiet
