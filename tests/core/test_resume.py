"""`engenia run --resume`: continue the last run that did not finish."""

import sqlite3

import pytest
from typer.testing import CliRunner

from engenia.core.cli import app
from engenia.core.engine import execute
from engenia.core.runstate import db_path

PROJECT = "harbour-listener"
DEPENDANTS = {"integration", "simulation", "final", "report"}
runner = CliRunner()


class Interrupted(BaseException):
    """Like Ctrl-C: not an Exception, so the scheduler does not treat it as a stage failure."""


def stage_rows(root, run_id):
    db = sqlite3.connect(db_path(root))
    db.row_factory = sqlite3.Row
    try:
        rows = db.execute("SELECT * FROM stage_run WHERE run_id = ?", (run_id,))
        return {r["stage"]: dict(r) for r in rows}
    finally:
        db.close()


def test_resume_skips_finished_stages_and_redoes_the_failed_one(root, recorder, fake_registry):
    broken = execute(PROJECT, root, registry=fake_registry({"mechanical": {"fail_in": "build"}}))
    assert not broken.ok
    recorder.reset()

    report = execute(PROJECT, root, registry=fake_registry(), resume=True)

    assert report.ok and report.run_id == broken.run_id and report.resumed_run
    assert {r.stage for r in report.results if r.resumed} == (
        {"verify_plan", "architect", "electronics", "firmware", "redteam", "testplan", "simprep"}
    )
    assert recorder.stages("build") == {"mechanical"} | DEPENDANTS
    rows = stage_rows(root, report.run_id)
    assert {r["status"] for r in rows.values()} == {"done"}
    assert rows["mechanical"]["attempts"] == 2  # failed once, redone once
    assert rows["firmware"]["attempts"] == 1  # never started again
    assert rows["integration"]["attempts"] == 1  # blocked the first time, so started just once


def test_resume_restarts_a_stage_the_run_was_interrupted_in(root, recorder, fake_registry):
    registry = fake_registry()

    def build(ctx, original=registry["firmware"].build):
        raise Interrupted

    registry["firmware"].build = build
    with pytest.raises(Interrupted):
        execute(PROJECT, root, registry=registry, workers=1)
    interrupted = sqlite3.connect(db_path(root)).execute(
        "SELECT status FROM run"
    ).fetchall()
    assert interrupted == [("running",)]  # the run never got to say it ended

    recorder.reset()
    report = execute(PROJECT, root, registry=fake_registry(), resume=True)
    assert report.ok and report.run_id == 1
    rows = stage_rows(root, 1)
    assert rows["firmware"]["attempts"] == 2 and rows["firmware"]["status"] == "done"
    assert rows["architect"]["attempts"] == 1
    assert "architect" not in recorder.stages("build")
    assert "firmware" in recorder.stages("build")


def test_a_finished_stage_whose_inputs_changed_is_not_skipped(
    root, recorder, fake_registry, project_dir, edit
):
    execute(PROJECT, root, registry=fake_registry({"mechanical": {"fail_in": "build"}}))
    edit(project_dir / "spec" / "R-005.yaml", "value: 0.20", "value: 0.30")  # affects mechanical
    recorder.reset()
    report = execute(PROJECT, root, registry=fake_registry(), resume=True)
    assert report.ok
    # verify_plan and the other spec-wide stages read R-005, so they cannot be trusted either
    assert {"verify_plan", "mechanical", "integration"} <= recorder.stages("build")
    assert "firmware" not in recorder.stages("build")  # not touched by the edit: skipped


def test_a_finished_stage_whose_build_was_deleted_is_not_skipped(
    root, recorder, fake_registry, project_dir
):
    import shutil

    execute(PROJECT, root, registry=fake_registry({"mechanical": {"fail_in": "build"}}))
    shutil.rmtree(project_dir / "build" / "firmware")
    recorder.reset()
    execute(PROJECT, root, registry=fake_registry(), resume=True)
    assert (project_dir / "build" / "firmware" / "interface.json").is_file()


def test_with_nothing_to_resume_a_new_run_starts(root, recorder, fake_registry):
    first = execute(PROJECT, root, registry=fake_registry())  # ended cleanly
    second = execute(PROJECT, root, registry=fake_registry(), resume=True)
    assert second.run_id == first.run_id + 1 and not second.resumed_run
    assert not any(r.resumed for r in second.results)


def test_without_resume_a_failed_run_is_left_alone(root, fake_registry):
    broken = execute(PROJECT, root, registry=fake_registry({"mechanical": {"fail_in": "build"}}))
    again = execute(PROJECT, root, registry=fake_registry())
    assert again.run_id == broken.run_id + 1


def test_the_command_says_what_it_skipped(root, monkeypatch, fake_registry):
    monkeypatch.setattr(
        "engenia.core.engine._default_registry",
        lambda: fake_registry({"mechanical": {"fail_in": "build"}}),
    )
    assert runner.invoke(app, ["run", PROJECT]).exit_code == 1
    monkeypatch.setattr("engenia.core.engine._default_registry", fake_registry)
    result = runner.invoke(app, ["run", PROJECT, "--resume"])
    assert result.exit_code == 0, result.output
    assert "skip firmware" in result.output and "ran  mechanical" in result.output
    assert "7 skipped (finished in run 1)" in result.output
