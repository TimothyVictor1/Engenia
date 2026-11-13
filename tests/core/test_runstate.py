"""The run database: .engenia/run.db."""

import sqlite3

from engenia.core.engine import execute
from engenia.core.runstate import RunStore, db_path

PROJECT = "harbour-listener"


def rows(root, sql, *args):
    db = sqlite3.connect(db_path(root))
    db.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in db.execute(sql, args)]
    finally:
        db.close()


def test_tables_have_the_specified_columns(root):
    RunStore(root).close()
    columns = {
        t: [r["name"] for r in rows(root, f"PRAGMA table_info({t})")] for t in ("run", "stage_run")
    }
    assert columns["run"] == ["id", "project", "started", "finished", "status"]
    assert columns["stage_run"] == [
        "run_id", "stage", "key", "status", "attempts", "started", "finished", "message",
    ]


def test_a_clean_run_is_recorded_as_done_then_cached(root, fake_registry):
    first = execute(PROJECT, root, registry=fake_registry())
    second = execute(PROJECT, root, registry=fake_registry())
    runs = rows(root, "SELECT * FROM run ORDER BY id")
    assert [r["id"] for r in runs] == [first.run_id, second.run_id]
    assert [r["status"] for r in runs] == ["done", "done"]
    assert all(r["project"] == PROJECT and r["finished"] >= r["started"] for r in runs)
    stages = rows(root, "SELECT * FROM stage_run WHERE run_id = ?", first.run_id)
    assert len(stages) == 12
    assert {s["status"] for s in stages} == {"done"}
    assert all(s["attempts"] == 1 and s["key"].startswith("sha256:") for s in stages)
    again = rows(root, "SELECT * FROM stage_run WHERE run_id = ?", second.run_id)
    assert {s["status"] for s in again} == {"cached"}
    assert {s["key"] for s in again} == {s["key"] for s in stages}


def test_failed_and_blocked_stages_carry_a_message(root, fake_registry):
    registry = fake_registry({"mechanical": {"fail_in": "build"}})
    report = execute(PROJECT, root, registry=registry)
    run = rows(root, "SELECT * FROM run WHERE id = ?", report.run_id)[0]
    assert run["status"] == "failed"
    by_stage = {s["stage"]: s for s in rows(
        root, "SELECT * FROM stage_run WHERE run_id = ?", report.run_id
    )}
    assert by_stage["mechanical"]["status"] == "failed"
    assert "tool crashed" in by_stage["mechanical"]["message"]
    assert by_stage["mechanical"]["key"].startswith("sha256:")
    assert by_stage["mechanical"]["attempts"] == 1
    for name in ("integration", "simulation", "final", "report"):
        assert by_stage[name]["status"] == "blocked"
        assert "mechanical" in by_stage[name]["message"]
        assert by_stage[name]["attempts"] == 0 and by_stage[name]["started"] is None
    assert by_stage["firmware"]["status"] == "done"


def test_a_stage_is_pending_then_running_before_it_finishes(root):
    store = RunStore(root)
    run_id = store.start_run(PROJECT, ["a", "b"])
    assert {s["status"] for s in store.stage_runs(run_id)} == {"pending"}
    store.stage_started(run_id, "a")
    a = store.stage_runs(run_id)[0]
    assert (a["status"], a["attempts"]) == ("running", 1) and a["started"]
    store.close()
