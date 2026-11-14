"""demo-fault: fault lookup and recipes, the copied-run logs, and the untouched real project."""

import json
import shutil
from pathlib import Path

import pytest
import yaml

from engenia.core import demo_fault, events
from engenia.core.demo_fault import DemoError, _set, fault_ids, run_demo_fault

REPO = Path(__file__).resolve().parents[2]
PROJECT = "harbour-listener"


def scratch_root(tmp_path):
    """A throw-away root with the real project's spec plus verifiers and the pack."""
    root = tmp_path / "root"
    (root / "projects").mkdir(parents=True)
    shutil.copytree(REPO / "projects" / PROJECT, root / "projects" / PROJECT)
    for name in ("verifiers", "packs"):
        shutil.copytree(
            REPO / name, root / name, ignore=shutil.ignore_patterns("__pycache__")
        )
    return root


def test_fault_ids_come_from_the_suite():
    ids = fault_ids()
    assert ids[0].startswith("F01-") and any(name.startswith("F10-") for name in ids)


def test_fault_lookup_accepts_id_or_folder(tmp_path):
    root = scratch_root(tmp_path)
    assert demo_fault._resolve("F01") == demo_fault.FAULTS_DIR / "F01-wall-1mm"
    assert demo_fault._resolve("f01-wall-1mm") == demo_fault._resolve("F01")
    with pytest.raises(DemoError, match="known faults"):
        run_demo_fault(PROJECT, "F99", root)


def test_a_fault_without_a_recipe_is_refused(tmp_path):
    root = scratch_root(tmp_path)
    with pytest.raises(DemoError, match="no injection recipe"):
        run_demo_fault(PROJECT, "F06", root)


def test_a_missing_project_is_refused(tmp_path):
    with pytest.raises(DemoError, match="no project"):
        run_demo_fault("no-such-project", "F01", tmp_path)


def test_the_command_reports_an_unknown_fault_and_exits_1():
    from typer.testing import CliRunner

    from engenia.core.cli import app

    result = CliRunner().invoke(app, ["demo-fault", "harbour-listener", "F99"])
    assert result.exit_code == 1
    assert "unknown fault" in result.output and "F01-wall-1mm" in result.output


def test_the_recipe_edits_the_yaml_and_unmarks_the_stage(tmp_path):
    source = tmp_path / "src" / "mechanical" / "params.yaml"
    source.parent.mkdir(parents=True)
    source.write_text(yaml.safe_dump({"wall_thickness_mm": 3.0, "outer_diameter_mm": 200.0}))
    marker_dir = tmp_path / "src" / ".generated-from"
    marker_dir.mkdir()
    (marker_dir / "mechanical").write_text("x\n")
    demo_fault._source("mechanical", "src/mechanical/params.yaml",
                       {"wall_thickness_mm": 1.0})(tmp_path)
    data = yaml.safe_load(source.read_text())
    assert data["wall_thickness_mm"] == 1.0 and data["outer_diameter_mm"] == 200.0
    assert not (marker_dir / "mechanical").exists()


def test_set_reaches_nested_keys():
    data = {"a": {"b": {"c": 1}}, "d": 2}
    _set(data, "a.b.c", 9)
    _set(data, "e.f", 7)
    assert data == {"a": {"b": {"c": 9}}, "d": 2, "e": {"f": 7}}


def test_f10_edits_the_stored_verdict_to_an_inconsistent_pass(tmp_path):
    build = tmp_path / "build" / "mechanical"
    build.mkdir(parents=True)
    (build / "verdict.json").write_text(json.dumps([{
        "check": "wall_thickness_min", "requirement": "R-004", "measured": 3.0,
        "limit": {"min": 3.0}, "result": "pass", "evidence_level": "measured",
        "artifact": None, "message": None, "stage": "mechanical", "item": "C-004",
    }]))
    demo_fault._f10(tmp_path)
    entry = json.loads((build / "verdict.json").read_text())[0]
    assert entry["result"] == "pass"
    assert entry["measured"] < entry["limit"]["min"]  # recomputing the numbers says fail


def test_copied_run_logs_get_fresh_ids_in_the_real_root(tmp_path):
    root = scratch_root(tmp_path)
    scratch = tmp_path / "scratch"
    events_dir = scratch / ".engenia" / "events"
    events_dir.mkdir(parents=True)
    (events_dir / "1.jsonl").write_text('{"one": true}\n')
    (events_dir / "2.jsonl").write_text('{"two": true}\n')
    first = demo_fault.DemoResult("F01", False, "", scratch=scratch)
    demo_fault._copy_events(PROJECT, root, scratch, first)
    assert first.copied_runs == [1, 2]
    real = root / ".engenia" / "events"
    assert [p.name for p in sorted(real.glob("*.jsonl"))] == ["1.jsonl", "2.jsonl"]
    # a later demo continues the id space instead of overwriting
    (events_dir / "3.jsonl").write_text('{"three": true}\n')
    second = demo_fault.DemoResult("F01", False, "", scratch=scratch)
    demo_fault._copy_events(PROJECT, root, scratch, second)
    assert second.copied_runs == [3]
    assert (real / "3.jsonl").read_text() == '{"three": true}\n'


def test_only_events_are_written_to_the_real_root(tmp_path):
    """The demo copies the project into a scratch root and writes nothing under the real one
    except .engenia/events."""
    root = scratch_root(tmp_path)
    (root / ".engenia" / "events").mkdir(parents=True)
    (root / ".engenia" / "events" / "9.jsonl").write_text("{}\n")  # a pre-existing log

    def tree_without_events(path):
        return sorted(
            str(p.relative_to(path)) for p in path.rglob("*") if ".engenia" not in p.parts
        )

    before = tree_without_events(root)
    result = run_demo_fault(PROJECT, "F01", root)
    assert tree_without_events(root) == before
    copied = result.copied_runs
    assert copied and 9 not in copied
    for run_id in copied:
        lines = (root / ".engenia" / "events" / f"{run_id}.jsonl").read_text().splitlines()
        assert lines and all(json.loads(line)["project"] == PROJECT for line in lines)


@pytest.mark.slow
def test_the_f01_demo_is_caught_and_the_repair_loop_reacts(tmp_path, monkeypatch):
    monkeypatch.delenv("ENGENIA_DESIGN_MODEL", raising=False)  # the no-model path: a proposal
    monkeypatch.delenv("ENGENIA_VERIFY_MODEL", raising=False)
    root = scratch_root(tmp_path)
    result = run_demo_fault(PROJECT, "F01", root)
    assert result.caught, result.detail
    assert "mechanical" in result.detail
    clean, faulted, repair = result.copied_runs
    found, _ = events.read_events(root, faulted)
    assert any(
        e["kind"] == "gate_step" and e["data"].get("result") == "fail"
        and e["data"]["gate"] == "G1"
        for e in found
    )
    assert any(e["kind"] == "phase_end" and e["data"]["ok"] is False for e in found)
    # the repair loop wrote a proposal and logged it under the repair agent
    proposals = [e for e in events.read_events(root, repair)[0] if e["kind"] == "proposal"]
    assert proposals and proposals[0]["data"]["requirement"].startswith("R-")
    assert proposals[0]["agent"]["name"] == "repair"
