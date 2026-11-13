import json

import pytest

from engenia.harness.limits import Limits
from engenia.harness.llm import FakeLLM, LLMError, text_response, tool_call_response
from engenia.harness.loop import run_agent
from engenia.harness.tools import Toolbox

BUILD = ("run_build", {})


@pytest.fixture
def sandbox(tmp_path):
    allowed = tmp_path / "src"
    allowed.mkdir()
    return allowed


def run(sandbox, tmp_path, script, *, limits=None, build=lambda: None, **kwargs):
    llm = FakeLLM(script)
    tools = Toolbox(sandbox, build=build)
    result = run_agent(
        "system text",
        "task text",
        tools,
        "test-model",
        limits or Limits(),
        transcript_dir=tmp_path / "transcripts",
        chat_fn=llm,
        **kwargs,
    )
    return result, llm


def tool_messages(result):
    return [m for m in result.messages if m["role"] == "tool"]


def test_final_message_stops_the_loop_and_transcript_is_saved(sandbox, tmp_path):
    result, llm = run(
        sandbox,
        tmp_path,
        [
            tool_call_response(("write_file", {"path": "a.txt", "content": "hi"}), BUILD),
            text_response("all done"),
        ],
    )
    assert result.status == "done"
    assert result.attempts == 1
    assert result.tool_calls == 2
    assert (sandbox / "a.txt").read_text() == "hi"
    assert len(llm.calls) == 2
    assert llm.calls[0].tools is not None and len(llm.calls[0].tools) == 3

    saved = json.loads(result.transcript_path.read_text(encoding="utf-8"))
    assert result.transcript_path == tmp_path / "transcripts" / "attempt-01.json"
    assert saved["status"] == "done"
    assert saved["model"] == "test-model"
    assert saved["messages"][0] == {"role": "system", "content": "system text"}
    assert saved["messages"][1] == {"role": "user", "content": "task text"}
    assert [m["role"] for m in saved["messages"]] == [
        "system", "user", "assistant", "tool", "tool", "assistant",
    ]
    assert saved["messages"][-1]["content"] == "all done"


def test_tool_call_limit_stops_the_loop(sandbox, tmp_path):
    result, llm = run(
        sandbox,
        tmp_path,
        [tool_call_response(BUILD)] * 10,
        limits=Limits(max_tool_calls_per_attempt=3),
    )
    assert result.status == "tool_limit"
    assert result.tool_calls == 3
    assert len(llm.calls) == 4  # the 4th model call asked for a 4th tool call, which was refused
    assert "not executed" in tool_messages(result)[-1]["content"]
    assert json.loads(result.transcript_path.read_text())["status"] == "tool_limit"


def test_every_requested_call_gets_a_result_when_limit_hits_mid_batch(sandbox, tmp_path):
    result, _ = run(
        sandbox,
        tmp_path,
        [tool_call_response(BUILD, BUILD, BUILD)],
        limits=Limits(max_tool_calls_per_attempt=2),
    )
    assert result.status == "tool_limit"
    assert result.tool_calls == 2
    results = tool_messages(result)
    assert len(results) == 3
    assert "not executed" in results[2]["content"]
    requested_ids = [c["id"] for c in result.messages[2]["tool_calls"]]
    assert [m["tool_call_id"] for m in results] == requested_ids


def test_wall_time_limit_stops_the_loop(sandbox, tmp_path):
    now = [0.0]

    def slow_build():
        now[0] += 700  # the build alone uses more than the 600 s budget

    result, llm = run(
        sandbox,
        tmp_path,
        [tool_call_response(BUILD, BUILD), text_response("never reached")],
        build=slow_build,
        clock=lambda: now[0],
    )
    assert result.status == "time_limit"
    assert result.tool_calls == 1  # the second call in the batch was refused
    assert "not executed" in tool_messages(result)[-1]["content"]
    assert llm.remaining == 1  # no further model call was made


def test_wall_time_checked_before_first_model_call(sandbox, tmp_path):
    ticks = iter([0.0, 1000.0])
    result, llm = run(
        sandbox, tmp_path, [text_response("x")], clock=lambda: next(ticks)
    )
    assert result.status == "time_limit"
    assert llm.calls == []
    assert result.transcript_path.is_file()


def test_write_outside_sandbox_is_returned_to_the_model_as_a_tool_result(sandbox, tmp_path):
    result, llm = run(
        sandbox,
        tmp_path,
        [
            tool_call_response(("write_file", {"path": "../evil.txt", "content": "x"})),
            text_response("ok"),
        ],
    )
    assert result.status == "done"
    assert not (tmp_path / "evil.txt").exists()
    last_tool = llm.calls[1].messages[-1]
    assert last_tool["role"] == "tool"
    assert "outside the allowed directory" in json.loads(last_tool["content"])["error"]


def test_bad_tool_calls_are_reported_not_raised(sandbox, tmp_path):
    bad_json = tool_call_response(BUILD)
    bad_json["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = "{oops"
    result, _ = run(
        sandbox,
        tmp_path,
        [bad_json, tool_call_response(("nonexistent", {})), tool_call_response(("write_file", {})),
         text_response("end")],
    )
    assert result.status == "done"
    errors = [json.loads(m["content"])["error"] for m in tool_messages(result)]
    assert "not valid JSON" in errors[0]
    assert "unknown tool" in errors[1]
    assert "bad arguments" in errors[2]


def test_tool_bug_does_not_kill_the_session(sandbox, tmp_path):
    def build():
        raise KeyError("boom")  # run_build turns this into exit code 1, still a normal result

    script = [tool_call_response(BUILD), text_response("end")]
    result, _ = run(sandbox, tmp_path, script, build=build)
    assert result.status == "done"
    assert json.loads(tool_messages(result)[0]["content"])["exit_code"] == 1


def test_llm_failure_ends_with_error_status_and_a_transcript(sandbox, tmp_path):
    result, _ = run(sandbox, tmp_path, [LLMError("endpoint down")])
    assert result.status == "error"
    assert result.error == "endpoint down"
    saved = json.loads(result.transcript_path.read_text())
    assert saved["status"] == "error"
    assert saved["error"] == "endpoint down"


def test_transcript_written_even_if_something_unexpected_raises(sandbox, tmp_path):
    llm = FakeLLM([RuntimeError("bug in test double")])
    with pytest.raises(RuntimeError):
        run_agent(
            "s", "t", Toolbox(sandbox, build=lambda: None), "m", Limits(),
            transcript_dir=tmp_path / "transcripts", chat_fn=llm,
        )
    saved = json.loads((tmp_path / "transcripts" / "attempt-01.json").read_text())
    assert saved["status"] == "crashed"


def test_history_continues_the_conversation(sandbox, tmp_path):
    first, _ = run(sandbox, tmp_path, [text_response("first")])
    result, llm = run(
        sandbox, tmp_path, [text_response("second")], attempt=2, history=first.messages
    )
    roles = [m["role"] for m in llm.calls[0].messages]
    assert roles == ["system", "user", "assistant", "user"]
    assert result.attempts == 2
    assert result.transcript_path.name == "attempt-02.json"
    saved = json.loads(result.transcript_path.read_text())
    assert saved["attempt_start_index"] == 3
    assert len(saved["messages"]) == 5
