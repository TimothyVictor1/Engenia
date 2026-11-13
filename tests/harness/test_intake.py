import json

import pytest
import yaml

from engenia.agents.intake import IntakeError, intake
from engenia.contracts.models import Requirement, RequirementStatus
from engenia.harness.llm import FakeLLM, text_response, tool_call_response

REQUEST = "The buoy must weigh no more than 400 g."


def write(name: str, content: str):
    return ("write_file", {"path": name, "content": content})


def run(llm, project="demo", **kwargs):
    return intake(REQUEST, project, model="test-model", chat_fn=llm, **kwargs)


def tool_results(llm, call_index):
    return [
        json.loads(m["content"])
        for m in llm.calls[call_index].messages
        if m["role"] == "tool"
    ]


def test_output_validates_against_requirement_and_is_draft(root, requirement_text):
    llm = FakeLLM(
        [
            tool_call_response(
                write("R-001.yaml", requirement_text(1)), write("R-002.yaml", requirement_text(2))
            ),
            text_response("Wrote two requirements."),
        ]
    )
    result = run(llm)

    assert [r.id for r in result] == ["R-001", "R-002"]
    for requirement in result:
        assert isinstance(requirement, Requirement)
        assert requirement.status == RequirementStatus.DRAFT
    for name in ("R-001.yaml", "R-002.yaml"):
        data = yaml.safe_load((root / "projects" / "demo" / "spec" / name).read_text())
        assert Requirement.model_validate(data).status == RequirementStatus.DRAFT


def test_request_and_next_free_id_reach_the_model(root, requirement_text):
    (root / "projects" / "demo" / "spec" / "R-004.yaml").write_text(
        requirement_text(4, status="approved")
    )
    llm = FakeLLM(
        [tool_call_response(write("R-005.yaml", requirement_text(5))), text_response("ok")]
    )
    run(llm)
    task = llm.calls[0].messages[1]["content"]
    assert REQUEST in task
    assert "R-004 [approved]" in task
    assert "next free id is R-005" in task


def test_lint_errors_are_sent_back_and_fixed(root, requirement_text):
    bad = requirement_text(1, statement="The buoy mass is at most 400 g.")  # no "shall"
    llm = FakeLLM(
        [
            tool_call_response(write("R-001.yaml", bad)),
            text_response("done"),
            tool_call_response(write("R-001.yaml", requirement_text(1))),
            text_response("fixed"),
        ]
    )
    result = run(llm)

    assert [r.id for r in result] == ["R-001"]
    lint_report = llm.calls[2].messages[-1]["content"]
    assert "engenia lint" in lint_report
    assert "R-001.yaml" in lint_report
    assert "L1" in lint_report and '"shall"' in lint_report
    assert len(llm.calls) == 4


def test_gives_up_after_three_fix_rounds(root, requirement_text):
    bad = requirement_text(1, statement="The buoy mass is at most 400 g.")
    llm = FakeLLM([tool_call_response(write("R-001.yaml", bad)), text_response("done")] * 4)

    with pytest.raises(IntakeError, match="3 fix rounds"):
        run(llm)
    assert llm.remaining == 0  # one initial round plus three fix rounds, no more


def test_agent_can_run_lint_itself_through_run_build(root, requirement_text):
    bad = requirement_text(1, statement="No obligation here.")
    llm = FakeLLM(
        [
            tool_call_response(write("R-001.yaml", bad), ("run_build", {})),
            tool_call_response(write("R-001.yaml", requirement_text(1))),
            text_response("done"),
        ]
    )
    run(llm)
    (_, lint_run) = tool_results(llm, 1)
    assert lint_run["exit_code"] == 1
    report = lint_run["stdout"] + lint_run["stderr"]
    assert "R-001.yaml" in report and "L1" in report and '"shall"' in report


def test_intake_never_writes_approved_status(root, requirement_text):
    llm = FakeLLM(
        [
            tool_call_response(write("R-001.yaml", requirement_text(1, status="approved"))),
            tool_call_response(write("R-001.yaml", requirement_text(1))),
            text_response("done"),
        ]
    )
    result = run(llm)

    (refusal,) = tool_results(llm, 1)
    assert "never approves" in refusal["error"]
    assert result[0].status == RequirementStatus.DRAFT
    on_disk = yaml.safe_load((root / "projects" / "demo" / "spec" / "R-001.yaml").read_text())
    assert on_disk["status"] == "draft"


def test_an_approved_requirement_is_never_overwritten(root, requirement_text):
    approved = requirement_text(1, status="approved")
    path = root / "projects" / "demo" / "spec" / "R-001.yaml"
    path.write_text(approved)
    changed = requirement_text(1, statement="Changed shall.")
    llm = FakeLLM(
        [
            tool_call_response(write("R-001.yaml", changed)),
            tool_call_response(write("R-002.yaml", requirement_text(2))),
            text_response("done"),
        ]
    )
    result = run(llm)

    assert "R-001 is approved" in tool_results(llm, 1)[0]["error"]
    assert path.read_text() == approved
    assert [r.id for r in result] == ["R-002"]  # only what this call wrote


@pytest.mark.parametrize("name", ["notes.txt", "sub/R-001.yaml", "../R-001.yaml", "R-1.yaml"])
def test_only_requirement_files_can_be_written(root, requirement_text, name):
    llm = FakeLLM(
        [
            tool_call_response(write(name, requirement_text(1))),
            tool_call_response(write("R-001.yaml", requirement_text(1))),
            text_response("done"),
        ]
    )
    run(llm)
    assert "error" in tool_results(llm, 1)[0]
    spec = root / "projects" / "demo" / "spec"
    assert sorted(p.name for p in spec.iterdir()) == ["R-001.yaml"]


def test_writing_nothing_is_a_failure_that_is_fed_back(root, requirement_text):
    # An approved file already passes lint, so only intake's own check notices nothing was written.
    (root / "projects" / "demo" / "spec" / "R-009.yaml").write_text(
        requirement_text(9, status="approved")
    )
    llm = FakeLLM(
        [
            text_response("I have nothing to add."),
            tool_call_response(write("R-001.yaml", requirement_text(1))),
            text_response("ok"),
        ]
    )
    result = run(llm)
    assert "No requirement files were written" in llm.calls[1].messages[-1]["content"]
    assert [r.id for r in result] == ["R-001"]


def test_empty_spec_dir_failure_is_fed_back_from_lint(root, requirement_text):
    llm = FakeLLM(
        [
            text_response("I have nothing to add."),
            tool_call_response(write("R-001.yaml", requirement_text(1))),
            text_response("ok"),
        ]
    )
    run(llm)
    report = llm.calls[1].messages[-1]["content"]
    assert "S0" in report and "R-*.yaml" in report


def test_creates_the_spec_directory_for_a_new_project(root, requirement_text):
    llm = FakeLLM(
        [tool_call_response(write("R-001.yaml", requirement_text(1))), text_response("ok")]
    )
    run(llm, project="brand-new")
    assert (root / "projects" / "brand-new" / "spec" / "R-001.yaml").is_file()


@pytest.mark.parametrize("project", ["../escape", "a/b", "", ".hidden"])
def test_rejects_unsafe_project_names(root, project):
    with pytest.raises(IntakeError, match="invalid project name"):
        run(FakeLLM([]), project=project)


def test_a_transcript_is_saved_for_every_round(root, requirement_text):
    bad = requirement_text(1, statement="The buoy mass is at most 400 g.")
    llm = FakeLLM(
        [
            tool_call_response(write("R-001.yaml", bad)),
            text_response("done"),
            tool_call_response(write("R-001.yaml", requirement_text(1))),
            text_response("fixed"),
        ]
    )
    run(llm)
    directory = root / "projects" / "demo" / "build" / "intake" / "transcripts"
    assert sorted(p.name for p in directory.iterdir()) == ["attempt-01.json", "attempt-02.json"]
