"""run_summary: per-stage phase durations, parallelism over time and totals, from the log."""

import time

from engenia.core import events
from engenia.core.summary import run_summary

PROJECT = "harbour-listener"


def write(root, run_id, agent, stage, kind, data, **kwargs):
    events.emit(root, stage, kind, data, agent=agent, **kwargs)


def phase(root, run_id, agent, stage, name, start, end_ms):
    """Emit one closed phase with a controllable duration_ms."""
    write(root, run_id, agent, stage, "phase_start", {"phase": name})
    time.sleep(start)
    write(root, run_id, agent, stage, "phase_end",
          {"phase": name, "duration_ms": end_ms, "ok": True})


def test_empty_log_gives_empty_structures(root):
    with events.activate(root, 1, PROJECT):
        pass
    summary = run_summary(root, 1)
    assert summary == {
        "run_id": 1, "stages": {}, "parallelism": [],
        "totals": {
            "duration_ms": 0, "model_calls": 0, "tool_calls": 0, "repairs": 0,
            "cache_hits": 0, "cache_misses": 0,
            "max_parallel_stages": 0, "max_parallel_agents": 0,
        },
    }


def test_phase_durations_are_summed_per_stage_and_phase(root):
    with events.activate(root, 1, PROJECT):
        phase(root, 1, {"name": "mechanical"}, "mechanical", "generate", 0, 10)
        phase(root, 1, {"name": "mechanical"}, "mechanical", "build", 0, 20)
        phase(root, 1, {"name": "mechanical"}, "mechanical", "build", 0, 30)
    stages = run_summary(root, 1)["stages"]
    assert stages["mechanical"]["phases"]["build"] == {"count": 2, "duration_ms": 50, "ok": True}
    assert stages["mechanical"]["phases"]["generate"] == {
        "count": 1, "duration_ms": 10, "ok": True}
    assert stages["mechanical"]["total_ms"] == 60


def test_a_failed_phase_is_marked_not_ok(root):
    with events.activate(root, 1, PROJECT):
        write(root, 1, {"name": "mechanical"}, "mechanical", "phase_start", {"phase": "check"})
        write(root, 1, {"name": "mechanical"}, "mechanical", "phase_end",
              {"phase": "check", "duration_ms": 5, "ok": False, "error": "ValueError: x"})
    phases = run_summary(root, 1)["stages"]["mechanical"]["phases"]
    assert phases["check"] == {"count": 1, "duration_ms": 5, "ok": False}


def test_parallelism_counts_stages_and_distinct_agents(root):
    mech, elec = {"name": "mechanical"}, {"name": "electronics"}
    with events.activate(root, 1, PROJECT):
        write(root, 1, mech, "mechanical", "phase_start", {"phase": "build"})
        write(root, 1, elec, "electronics", "phase_start", {"phase": "build"})
        write(root, 1, elec, "electronics", "phase_end",
              {"phase": "build", "duration_ms": 1, "ok": True})
        write(root, 1, mech, "mechanical", "phase_end",
              {"phase": "build", "duration_ms": 2, "ok": True})
    series = run_summary(root, 1)["parallelism"]
    assert [(p["stages"], p["agents"]) for p in series] == [(1, 1), (2, 2), (1, 1), (0, 0)]
    totals = run_summary(root, 1)["totals"]
    assert totals["max_parallel_stages"] == 2 and totals["max_parallel_agents"] == 2


def test_totals_count_calls_caches_and_repairs(root):
    with events.activate(root, 1, PROJECT):
        write(root, 1, {"name": "mechanical"}, "mechanical", "message",
              {"role": "assistant", "text": "thinking"})
        write(root, 1, {"name": "mechanical"}, "mechanical", "tool_call", {"id": "1"})
        write(root, 1, {"name": "mechanical"}, "mechanical", "tool_call", {"id": "2"})
        write(root, 1, {"name": "repair"}, "mechanical", "repair",
              {"phase": "round", "outcome": "repaired"})
        write(root, 1, {"name": "mechanical"}, "mechanical", "cache",
              {"key": "sha256:abc", "hit": False})
        write(root, 1, {"name": "firmware"}, "firmware", "cache",
              {"key": "sha256:def", "hit": True})
    totals = run_summary(root, 1)["totals"]
    assert totals["model_calls"] == 1 and totals["tool_calls"] == 2
    assert totals["repairs"] == 1 and totals["cache_hits"] == 1 and totals["cache_misses"] == 1


def test_a_phase_end_is_never_shorter_than_its_logged_duration(root):
    with events.activate(root, 1, PROJECT):
        write(root, 1, {"name": "mechanical"}, "mechanical", "phase_start", {"phase": "build"})
        time.sleep(0.02)
        write(root, 1, {"name": "mechanical"}, "mechanical", "phase_end",
              {"phase": "build", "duration_ms": 0, "ok": True})
    assert run_summary(root, 1)["stages"]["mechanical"]["phases"]["build"]["duration_ms"] >= 20
