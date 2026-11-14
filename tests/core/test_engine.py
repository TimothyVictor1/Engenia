"""The acceptance tests for `engenia run`: what reruns, and what does not."""

import json

import pytest
from typer.testing import CliRunner

from engenia.core.cli import app
from engenia.core.engine import PipelineError, Status, run_pipeline
from engenia.core.errors import CoreError
from engenia.core.hashing import stage_key, tree_hash
from engenia.core.spec import SpecError

PROJECT = "harbour-listener"
ALL = {
    "verify_plan", "architect", "mechanical", "electronics", "firmware", "redteam",
    "testplan", "simprep", "integration", "simulation", "final", "report",
}
# These read the whole specification, so any requirement edit reruns them.
SPEC_WIDE = {"verify_plan", "architect", "testplan", "simprep", "redteam", "final", "report"}
runner = CliRunner()


@pytest.fixture
def run(root, fake_registry):
    """run(registry): one pipeline run on the scratch project; returns the results."""

    def do(registry=None, **kwargs):
        return run_pipeline(PROJECT, root, registry=registry or fake_registry(), **kwargs)

    return do


def interface_bytes(project_dir):
    return {
        p.parent.name: p.read_bytes() for p in (project_dir / "build").glob("*/interface.json")
    }


# --- acceptance -----------------------------------------------------------------------------


def test_first_run_executes_every_stage_in_dependency_order(run, recorder):
    results = run()
    assert [r.stage for r in results][:2] == ["verify_plan", "architect"]
    assert {r.stage for r in results} == ALL
    assert all(r.status is Status.RAN and r.generated and r.built for r in results)
    assert recorder.stages("generate") == recorder.stages("build") == ALL


def test_second_run_executes_zero_stages(run, recorder):
    run()
    recorder.reset()
    results = run()
    assert recorder.events == []
    assert {r.stage for r in results} == ALL
    assert all(r.status is Status.CACHED and not r.generated and not r.built for r in results)


def test_editing_r003_reruns_firmware_after_electronics_changes(
    run, recorder, project_dir, edit
):
    run()
    recorder.reset()
    edit(project_dir / "spec" / "R-003.yaml", "value: 7", "value: 30")
    run()
    # R-003 affects electronics and mechanical; integration follows their new interfaces;
    # the spec-wide stages read it whether or not `affects` names them.
    rerun = SPEC_WIDE | {"electronics", "mechanical", "firmware", "integration", "simulation"}
    assert recorder.stages("build") == rerun
    assert recorder.stages("generate") == rerun
    # Firmware consumes the changed electronics interface.
    assert "architect" in recorder.stages("build")
    assert "firmware" in recorder.stages("build") & recorder.stages("generate")
    assert recorder.count("build", "mechanical") == 1  # once, not once per downstream stage


def test_the_new_value_reaches_the_downstream_interface(run, project_dir, edit):
    run()
    edit(project_dir / "spec" / "R-003.yaml", "value: 7", "value: 30")
    run()
    facts = json.loads((project_dir / "build" / "integration" / "interface.json").read_text())
    assert facts["facts"]["up"]["electronics"]["own"]["R-003"] == 30


def test_firmware_reruns_when_an_upstream_interface_changed(
    run, recorder, project_dir, edit
):
    """A requirement on the architect changes the architect's interface, which firmware reads."""
    (project_dir / "spec" / "R-007.yaml").write_text(
        "id: R-007\n"
        "statement: The hull shall have a diameter of 120 mm.\n"
        "value: 120\n"
        "unit: mm\n"
        "verify: {method: analysis, check: hull_size, limit: {max: 120}}\n"
        "affects: [architect]\n"
        "status: approved\n"
    )
    run()
    recorder.reset()
    edit(project_dir / "spec" / "R-007.yaml", "value: 120", "value: 140")
    run()
    assert recorder.stages("build") == SPEC_WIDE | {
        "mechanical", "electronics", "firmware", "integration", "simulation",
    }


def test_a_stage_that_reruns_with_an_unchanged_interface_does_not_wake_its_downstream(
    root, run, recorder, project_dir, fake_registry, edit
):
    registry = fake_registry(follow_spec=False)
    run(registry)
    before = interface_bytes(project_dir)
    recorder.reset()
    edit(project_dir / "spec" / "R-003.yaml", "value: 7", "value: 30")

    results = run(registry)

    reran = SPEC_WIDE | {"electronics", "mechanical"}
    assert recorder.stages("build") == reran  # they reran ...
    assert interface_bytes(project_dir) == before  # ... and said the same thing
    by_stage = {r.stage: r for r in results}
    for name in ALL - reran:  # firmware, integration, simulation
        assert by_stage[name].status is Status.CACHED, name
    assert {"firmware", "integration"} <= ALL - reran


def test_a_whitespace_only_edit_reruns_the_stage_but_stops_there(
    run, recorder, project_dir
):
    """Spec files are hashed as bytes, so a trailing newline reruns the stage it affects."""
    run()
    recorder.reset()
    with open(project_dir / "spec" / "R-005.yaml", "a", encoding="utf-8") as handle:
        handle.write("\n")  # R-005 affects mechanical only
    run()
    # Same facts, so nothing follows mechanical (integration stays cached).
    assert recorder.stages("build") == SPEC_WIDE | {"mechanical"}


def test_reverting_a_change_restores_the_earlier_build_from_the_cache(
    run, recorder, project_dir, edit
):
    run()
    original = {p.name: p.read_bytes() for p in (project_dir / "build" / "mechanical").iterdir()}
    edit(project_dir / "spec" / "R-005.yaml", "value: 0.20", "value: 0.30")
    run()
    recorder.reset()
    edit(project_dir / "spec" / "R-005.yaml", "value: 0.30", "value: 0.20")
    results = run()
    assert recorder.stages("build") == set()  # every build came out of the cache
    assert all(r.built is False for r in results)
    restored = {p.name: p.read_bytes() for p in (project_dir / "build" / "mechanical").iterdir()}
    assert restored == original


def test_the_cache_serves_a_project_whose_build_directory_was_deleted(
    run, recorder, project_dir
):
    import shutil

    run()
    shutil.rmtree(project_dir / "build")
    recorder.reset()
    run()
    assert recorder.events == []
    assert (project_dir / "build" / "mechanical" / "interface.json").is_file()
    assert (project_dir / "build" / "mechanical" / "verdict.json").read_text() == "[]\n"


def test_an_emptied_cache_makes_a_rerun_rebuild_without_regenerating(
    root, run, recorder
):
    import shutil

    run()
    shutil.rmtree(root / "store")
    recorder.reset()
    run()
    assert recorder.stages("build") == ALL
    assert recorder.stages("generate") == set()  # the committed code is current


def test_interface_json_describes_the_build(run, project_dir):
    run()
    data = json.loads((project_dir / "build" / "mechanical" / "interface.json").read_text())
    assert data["stage"] == "mechanical"
    assert set(data) == {"stage", "key", "facts"}
    # key is the hash of the outputs, not of the inputs, so equal outputs give equal interfaces
    outputs = project_dir / "build" / "mechanical"
    assert data["key"] == tree_hash(outputs, exclude=["interface.json", "verdict.json"])


# --- generate ------------------------------------------------------------------------------


def test_code_written_by_hand_is_never_overwritten(run, recorder, project_dir):
    code = project_dir / "src" / "mechanical"
    code.mkdir(parents=True)
    (code / "code.txt").write_text("my own hull\n")
    run()
    assert "mechanical" not in recorder.stages("generate")
    assert recorder.count("build", "mechanical") == 1
    assert (project_dir / "build" / "mechanical" / "out.txt").read_text() == (
        "built from: my own hull\n"
    )
    assert not (project_dir / "src" / ".generated-from" / "mechanical").exists()


def test_generation_is_recorded_next_to_the_code(run, project_dir):
    run()
    marker = project_dir / "src" / ".generated-from" / "mechanical"
    assert marker.read_text().strip().startswith("sha256:")


def test_a_fresh_clone_with_committed_code_does_not_call_the_agent(
    root, run, recorder, project_dir
):
    """src/ and the .generated-from record are committed; build/ and the store are not."""
    import shutil

    run()
    shutil.rmtree(project_dir / "build")
    shutil.rmtree(root / "store")
    recorder.reset()
    run()
    assert recorder.stages("generate") == set()
    assert recorder.stages("build") == ALL


def test_a_stage_that_writes_no_code_is_not_asked_again(
    run, recorder, fake_registry
):
    registry = fake_registry({"verify_plan": {"writes_code": False}})
    run(registry)
    assert recorder.count("generate", "verify_plan") == 1
    recorder.reset()
    run(registry)
    assert recorder.events == []


def test_a_stage_that_dropped_its_code_is_asked_again(run, recorder, project_dir):
    run()
    for path in (project_dir / "src" / "firmware").iterdir():
        path.unlink()
    recorder.reset()
    run()
    assert recorder.stages("generate") == {"firmware"}


def test_old_outputs_do_not_survive_into_a_new_build(run, project_dir, edit):
    run()
    stale = project_dir / "build" / "mechanical" / "stale.txt"
    stale.write_text("left over")
    edit(project_dir / "spec" / "R-005.yaml", "value: 0.20", "value: 0.30")
    run()
    assert not stale.exists()


# --- failures ------------------------------------------------------------------------------


@pytest.mark.parametrize("phase", ["generate", "build"])
def test_a_failing_stage_blocks_its_dependants_and_is_not_cached(
    run, recorder, project_dir, fake_registry, phase
):
    registry = fake_registry({"mechanical": {"fail_in": phase}})
    seen = []
    with pytest.raises(PipelineError, match=f"stage mechanical failed in {phase}"):
        run(registry, on_result=seen.append)
    done = {r.stage for r in seen}
    dependants = {"integration", "simulation", "final", "report"}
    assert done == ALL - {"mechanical"} - dependants  # everything independent still ran
    assert not recorder.stages("build") & dependants

    recorder.reset()
    registry = fake_registry()  # fixed
    results = run(registry)
    assert {r.stage for r in results} == ALL
    assert recorder.stages("build") == {"mechanical"} | dependants
    assert recorder.count("build", "architect") == 0  # finished work stayed cached


def test_a_broken_spec_stops_before_any_stage_runs(run, recorder, project_dir):
    (project_dir / "spec" / "R-009.yaml").write_text("id: R-001\n")
    with pytest.raises(SpecError):
        run()
    assert recorder.events == []


def test_interface_that_is_not_valid_is_a_pipeline_error(run, fake_registry):
    registry = fake_registry()
    registry["verify_plan"].interface = lambda ctx: ["not", "a", "mapping"]
    with pytest.raises(PipelineError, match="invalid interface"):
        run(registry)


def test_transcripts_do_not_change_the_interface_or_wake_downstream(
    run, recorder, project_dir, fake_registry, edit
):
    """Agent transcripts differ on every run; they must not count as design outputs."""
    registry = fake_registry({"mechanical": {"transcripts": True}})
    run(registry)
    before = interface_bytes(project_dir)
    first = sorted((project_dir / "build" / "mechanical" / "transcripts").iterdir())
    assert [p.name for p in first] == ["run-1.txt"]
    edit(project_dir / "spec" / "R-005.yaml", "value: 0.20", "value: 0.20\n")  # forces a rebuild
    results = {r.stage: r for r in run(registry)}
    transcripts = sorted((project_dir / "build" / "mechanical" / "transcripts").iterdir())
    assert recorder.count("build", "mechanical") == 2
    assert [p.name for p in transcripts] == ["run-2.txt"]  # a different transcript ...
    assert interface_bytes(project_dir) == before  # ... and the same interface
    assert results["integration"].status is Status.CACHED


def test_every_core_error_shares_one_base():
    assert issubclass(PipelineError, CoreError) and issubclass(SpecError, CoreError)


# --- keys recorded by the engine match the public function ---------------------------------


def test_result_key_is_the_stage_key(root, run, project_dir, make_ctx, fake_registry):
    registry = fake_registry()
    results = {r.stage: r for r in run(registry)}
    ctx = make_ctx("mechanical")
    assert results["mechanical"].key == stage_key(registry["mechanical"], ctx)


# --- the command line -----------------------------------------------------------------------
# The CLI runs the registry's stages. These tests swap in FakeStage, whose interface does not
# follow the spec (as with the step 0 stubs), so no other lane's stage code is involved.


@pytest.fixture
def cli_stages(monkeypatch, fake_registry):
    registry = fake_registry(follow_spec=False)
    monkeypatch.setattr("engenia.core.engine._default_registry", lambda: registry)
    return registry


def test_cli_second_run_executes_zero_stages(root, cli_stages):
    first = runner.invoke(app, ["run", PROJECT])
    assert first.exit_code == 0, first.output
    assert "done: 12 ran, 0 from cache" in first.output
    second = runner.invoke(app, ["run", PROJECT])
    assert second.exit_code == 0, second.output
    assert "done: 0 ran, 12 from cache" in second.output
    assert not any(line.startswith("ran") for line in second.output.splitlines())


def test_cli_editing_r003_reruns_the_affected_and_spec_wide_stages_only(
    root, cli_stages, project_dir, edit
):
    """The interfaces do not change, so nothing else follows."""
    runner.invoke(app, ["run", PROJECT])
    edit(project_dir / "spec" / "R-003.yaml", "value: 7", "value: 30")
    result = runner.invoke(app, ["run", PROJECT])
    ran = {line.split()[1] for line in result.output.splitlines() if line.startswith("ran")}
    assert ran == SPEC_WIDE | {"mechanical", "electronics"}
    assert "done: 9 ran, 3 from cache" in result.output


def test_cli_reports_a_broken_spec_and_exits_1(root, project_dir):
    spec_dir = project_dir / "spec"
    (spec_dir / "R-004.yaml").write_text((spec_dir / "R-001.yaml").read_text())
    result = runner.invoke(app, ["run", PROJECT])
    assert result.exit_code == 1
    assert "duplicate id R-001" in result.output
    assert not (project_dir / "build").exists()


def test_cli_unknown_project_exits_1(root):
    assert runner.invoke(app, ["run", "nope"]).exit_code == 1
