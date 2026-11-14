import json
import sys

import pytest

from engenia.contracts.models import EvidenceLevel, Limit, Verdict, VerdictResult
from engenia.harness.tools import Toolbox, ToolError


@pytest.fixture
def sandbox(tmp_path):
    allowed = tmp_path / "src"
    allowed.mkdir()
    return allowed


def make_tools(allowed, build=lambda: None, verdicts=None, **kwargs):
    return Toolbox(allowed, build=build, verdicts_path=verdicts, **kwargs)


def test_exactly_four_tools(sandbox):
    names = [schema["function"]["name"] for schema in make_tools(sandbox).schemas()]
    assert names == ["write_file", "run_build", "read_verdicts", "record_decision"]


def test_write_file_inside_sandbox(sandbox):
    tools = make_tools(sandbox)
    result = tools.call("write_file", {"path": "a/b.txt", "content": "héllo"})
    assert (sandbox / "a" / "b.txt").read_text(encoding="utf-8") == "héllo"
    assert result == {"path": "a/b.txt", "bytes": 6}


def test_write_file_accepts_absolute_path_inside_sandbox(sandbox):
    make_tools(sandbox).call("write_file", {"path": str(sandbox / "x.txt"), "content": "1"})
    assert (sandbox / "x.txt").read_text() == "1"


@pytest.mark.parametrize(
    "bad_path",
    ["../escape.txt", "a/../../escape.txt", "/etc/engenia-test.txt", "", "   ", ".", "a\0b"],
)
def test_write_file_outside_sandbox_is_refused(sandbox, tmp_path, bad_path):
    tools = make_tools(sandbox)
    with pytest.raises(ToolError):
        tools.call("write_file", {"path": bad_path, "content": "x"})
    assert not (tmp_path / "escape.txt").exists()
    assert list(sandbox.iterdir()) == []


def test_write_file_refuses_sibling_directory_with_same_prefix(sandbox, tmp_path):
    sibling = tmp_path / "src-evil"
    sibling.mkdir()
    with pytest.raises(ToolError, match="outside"):
        make_tools(sandbox).call("write_file", {"path": str(sibling / "x.txt"), "content": "x"})
    assert not (sibling / "x.txt").exists()


def test_write_file_refuses_symlink_leading_out(sandbox, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (sandbox / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ToolError, match="outside"):
        make_tools(sandbox).call("write_file", {"path": "link/x.txt", "content": "x"})
    assert not (outside / "x.txt").exists()


def test_write_file_refuses_directory_target(sandbox):
    (sandbox / "d").mkdir()
    with pytest.raises(ToolError, match="directory"):
        make_tools(sandbox).call("write_file", {"path": "d", "content": "x"})


def test_write_guard_can_refuse_and_written_is_tracked(sandbox):
    def guard(relative, content):
        if "nope" in content:
            raise ToolError("guarded")

    tools = make_tools(sandbox, write_guard=guard)
    with pytest.raises(ToolError, match="guarded"):
        tools.call("write_file", {"path": "a.txt", "content": "nope"})
    assert not (sandbox / "a.txt").exists()
    tools.call("write_file", {"path": "a.txt", "content": "fine"})
    tools.call("write_file", {"path": "a.txt", "content": "fine again"})
    assert tools.written == [(sandbox / "a.txt").resolve()]


def test_unknown_tool_and_bad_arguments(sandbox):
    tools = make_tools(sandbox)
    with pytest.raises(ToolError, match="unknown tool"):
        tools.call("delete_everything", {})
    with pytest.raises(ToolError, match="bad arguments"):
        tools.call("write_file", {"path": "a"})
    with pytest.raises(ToolError, match="bad arguments"):
        tools.call("run_build", {"extra": 1})


def test_run_build_reports_stdout_stderr_and_exit_code(sandbox):
    def build():
        print("compiling")
        print("warning", file=sys.stderr)

    result = make_tools(sandbox, build=build).call("run_build", {})
    assert result == {"stdout": "compiling\n", "stderr": "warning\n", "exit_code": 0}


def test_run_build_exception_becomes_exit_code_1(sandbox):
    def build():
        raise ValueError("bad geometry")

    result = make_tools(sandbox, build=build).call("run_build", {})
    assert result["exit_code"] == 1
    assert "ValueError: bad geometry" in result["stderr"]


def test_run_build_system_exit_code_is_kept(sandbox):
    def build():
        raise SystemExit(3)

    assert make_tools(sandbox, build=build).call("run_build", {})["exit_code"] == 3


def test_run_build_truncates_long_output(sandbox):
    result = make_tools(sandbox, build=lambda: print("x" * 50_000)).call("run_build", {})
    assert len(result["stdout"]) < 9000
    assert "truncated" in result["stdout"]


def test_read_verdicts(sandbox, tmp_path):
    path = tmp_path / "verdict.json"
    tools = make_tools(sandbox, verdicts=path)
    assert tools.call("read_verdicts", {}) == []  # no file yet

    verdict = Verdict(
        check="mass_budget",
        requirement="R-001",
        measured=450.0,
        limit=Limit(max=400),
        result=VerdictResult.FAIL,
        evidence_level=EvidenceLevel.CALCULATED,
        message="too heavy",
    )
    path.write_text(json.dumps([verdict.model_dump(mode="json")]), encoding="utf-8")
    (item,) = tools.call("read_verdicts", {})
    assert item["check"] == "mass_budget"
    assert item["result"] == "fail"
    assert item["measured"] == 450.0


def test_read_verdicts_rejects_corrupt_file(sandbox, tmp_path):
    path = tmp_path / "verdict.json"
    path.write_text('[{"check": "x"}]', encoding="utf-8")
    with pytest.raises(ToolError, match="unreadable"):
        make_tools(sandbox, verdicts=path).call("read_verdicts", {})
