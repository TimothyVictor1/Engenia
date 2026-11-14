"""The loop events: phase start/end pairs, cache hits and misses, change requests, proposals,
gate steps and approvals — and where stand-alone steps log when no run is active."""

import json

from engenia.core import board, events
from engenia.core.engine import execute
from engenia.gate.gate import run_gate

from .test_repair_loop import Script, gate_result, go, verdict

PROJECT = "harbour-listener"


def lines(root, run_id=1):
    return [json.loads(x) for x in events.events_path(root, run_id).read_text().splitlines()]


# --- phase events ------------------------------------------------------------------------------


def test_a_phase_emits_start_and_end_with_duration(root):
    with events.activate(root, 1, PROJECT):
        with events.phase(root, "mechanical", "build"):
            pass
    found = lines(root, 1)
    assert [e["kind"] for e in found] == ["phase_start", "phase_end"]
    assert (found[0]["stage"], found[0]["data"]) == ("mechanical", {"phase": "build"})
    assert found[0]["phase"] == "build"
    end = found[1]["data"]
    assert end["phase"] == "build" and end["ok"] is True and end["duration_ms"] >= 0


def test_events_inside_a_phase_carry_it_and_nesting_restores(root):
    with events.activate(root, 1, PROJECT):
        with events.phase(root, "mechanical", "build"):
            events.emit(root, "mechanical", "message", {"text": "inside"})
            with events.phase(root, "mechanical", "check"):
                events.emit(root, "mechanical", "message", {"text": "inner"})
            events.emit(root, "mechanical", "message", {"text": "back out"})
    found = [e for e in lines(root, 1) if e["kind"] == "message"]
    assert [e["phase"] for e in found] == ["build", "check", "build"]


def test_a_failing_phase_reports_ok_false_and_the_error(root):
    with events.activate(root, 1, PROJECT):
        try:
            with events.phase(root, "mechanical", "build"):
                raise ValueError("broke")
        except ValueError:
            pass
    (end,) = [e for e in lines(root, 1) if e["kind"] == "phase_end"]
    assert end["data"]["ok"] is False
    assert end["data"]["error"].startswith("ValueError: broke")
    assert end["data"]["duration_ms"] >= 0


def test_an_unknown_phase_name_becomes_none_but_the_event_stays(root):
    with events.activate(root, 1, PROJECT):
        events.emit(root, "mechanical", "message", {}, phase="daydreaming")
    assert lines(root, 1)[0]["phase"] is None


# --- cache events ------------------------------------------------------------------------------


def test_cache_events_carry_a_short_key_prefix(root, fake_registry):
    registry = fake_registry()
    first = execute(PROJECT, root, registry=registry)
    caches = [e for e in lines(root, first.run_id) if e["kind"] == "cache"]
    assert len(caches) == len(registry)
    assert all(
        e["data"]["hit"] is False and e["data"]["key"].startswith("sha256:")
        and len(e["data"]["key"]) == len("sha256:") + 16
        for e in caches
    )
    second = execute(PROJECT, root, registry=registry)
    caches = [e for e in lines(root, second.run_id) if e["kind"] == "cache"]
    assert all(e["data"]["hit"] is True for e in caches)


# --- change_request events ---------------------------------------------------------------------


def test_filing_and_deciding_a_change_request_is_logged(root, make_ctx):
    ctx = make_ctx("mechanical")
    with events.activate(root, 1, PROJECT):  # as when a stage files it during its run
        request = board.submit_change_request(
            ctx, path="budgets.electronics.bay_mm", requested=[70, 40, 26],
            reason="battery height",
        )
    assert request.id == "CR-001"
    decided = board.process_change_requests(root, PROJECT)
    events_found = [e for e in lines(root, 1) if e["kind"] == "change_request"]
    assert [(e["stage"], e["data"]["status"], e["data"]["id"]) for e in events_found] == [
        ("mechanical", "filed", "CR-001"),
        ("mechanical", decided[0].status.value, "CR-001"),
    ]
    assert decided[0].status.value in ("accepted", "rejected")
    assert events_found[0]["data"]["requested"] == [70, 40, 26]
    assert events_found[1]["data"]["reason"]


# --- proposal events ---------------------------------------------------------------------------


def test_the_proposer_logs_a_proposal_event(root, fake_registry):
    script = Script([gate_result(verdict())], ["no_model"])  # no fix possible: straight to one
    report = go(root, fake_registry, script)
    assert report.no_proposal == {} and len(script.proposals) == 1
    events_found = [e for e in lines(root) if e["kind"] == "proposal"]
    assert [(e["stage"], e["data"]["requirement"]) for e in events_found] == [
        ("mechanical", "R-005")]
    assert events_found[0]["agent"]["name"] == "repair"
    assert events_found[0]["phase"] == "propose"


# --- gate_step and approval events -------------------------------------------------------------


def test_a_gate_run_logs_five_steps_and_the_approval(root, fake_registry):
    with events.activate(root, 1, PROJECT):
        events.emit(root, None, "message", {"text": "so the gate has somewhere to log"})
    result = run_gate(PROJECT, root, registry=fake_registry())
    assert not result.passed  # a bare fixture root has no builds to reproduce
    steps = [e for e in lines(root, 1) if e["kind"] == "gate_step"]
    names = {e["data"]["gate"] for e in steps}
    assert names == {"G1", "G2", "G3", "G4", "G5"}
    for name in names:
        actions = [e["data"]["action"] for e in steps if e["data"]["gate"] == name]
        assert actions == ["start", "end"]
    ends = {e["data"]["gate"]: e["data"]["result"] for e in steps if e["data"]["action"] == "end"}
    assert ends["G3"] == "fail"
    for step in steps:
        assert step["agent"]["name"] == "gate" and step["agent"]["kind"] == "script"
    (approval_event,) = [e for e in lines(root, 1) if e["kind"] == "approval"]
    assert approval_event["data"]["status"] == "needed"
    assert approval_event["agent"]["name"] == "gate"


# --- attach ------------------------------------------------------------------------------------


def test_attach_logs_a_standalone_step_to_the_newest_run(root):
    with events.activate(root, 4, PROJECT):
        events.emit(root, None, "message", {"text": "first run"})
    with events.attach(root, PROJECT), events.as_agent(root, "intake", {"name": "intake"}):
        events.emit(root, "intake", "message", {"text": "standalone"})
    found = lines(root, 4)
    assert len(found) == 2 and found[1]["agent"] == {"name": "intake"}
    assert not events.events_path(root, 5).exists()  # nothing new was started


def test_attach_never_hijacks_an_active_run(root):
    with events.activate(root, 4, PROJECT):
        events.emit(root, None, "message", {"text": "first run"})
    with events.activate(root, 7, PROJECT), events.attach(root, PROJECT):
        events.emit(root, None, "message", {"text": "inside run 7"})
    assert [e["run_id"] for e in lines(root, 7)] == [7]
    assert len(lines(root, 4)) == 1


def test_attach_is_silent_when_the_project_has_no_runs(root):
    with events.attach(root, PROJECT):
        events.emit(root, None, "message", {"text": "nothing"})
    assert not events.events_dir(root).exists()
