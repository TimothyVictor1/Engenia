"""`engenia status`: the latest run, read from .engenia/run.db."""

import json

from typer.testing import CliRunner

from engenia.core.cli import app
from engenia.core.runstate import RunStore
from engenia.core.status import status_lines

PROJECT = "harbour-listener"


def _write(root, relative, value):
    path = root / "projects" / PROJECT / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_no_database_is_reported_and_not_created(root):
    lines = status_lines(PROJECT, root)
    assert "no runs yet" in lines[0]
    assert "gate: not run" in lines
    assert "open change requests: none" in lines
    assert not (root / ".engenia" / "run.db").exists()


def test_latest_run_stages_attempts_and_gate(root):
    store = RunStore(root)
    old = store.start_run(PROJECT, ["architect"])
    store.finish_run(old, "done")
    other = store.start_run("deck-sensor", ["architect"])
    store.finish_run(other, "done")
    run_id = store.start_run(PROJECT, ["architect", "mechanical", "firmware"])
    store.stage_started(run_id, "architect")
    store.stage_finished(run_id, "architect", "done", key="k")
    store.stage_started(run_id, "mechanical")
    store.stage_started(run_id, "mechanical")
    store.stage_finished(run_id, "mechanical", "failed", key=None, message="boom\nmore")
    store.stage_blocked(run_id, "firmware", "needs failed mechanical")
    store.finish_run(run_id, "failed")
    store.close()
    _write(root, "build/gate/report.json", {
        "passed": False, "gates": {"G1": {"passed": True}, "G3": {"passed": False}},
    })
    _write(root, "contract/change_requests/CR-001.json", {
        "id": "CR-001", "path": "budgets.x", "status": "open",
    })
    _write(root, "contract/change_requests/CR-002.json", {
        "id": "CR-002", "path": "budgets.y", "status": "accepted",
    })
    text = "\n".join(status_lines(PROJECT, root))
    assert f"run {run_id} failed" in text
    assert f"run {old} " not in text
    row = {line.split()[0]: line.split() for line in text.splitlines() if line.startswith("  ")}
    assert row["architect"][1] == "done" and row["architect"][2] == "1"
    assert row["mechanical"][1] == "failed" and row["mechanical"][2] == "2"
    assert row["firmware"][1] == "blocked" and row["firmware"][2] == "0"
    assert "boom" in text and "more" not in text
    assert "gate: FAIL (G3)" in text
    assert "open change requests: CR-001 (budgets.x)" in text


def test_cli_status(root):
    store = RunStore(root)
    run_id = store.start_run(PROJECT, ["architect"])
    store.stage_started(run_id, "architect")
    store.stage_finished(run_id, "architect", "cached", key="k")
    store.finish_run(run_id, "done")
    store.close()
    result = CliRunner().invoke(app, ["status", PROJECT])
    assert result.exit_code == 0, result.output
    assert "architect" in result.output and "cached" in result.output
