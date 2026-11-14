"""The live design log: format, scrubbing, silence without a run, and the engine's own events."""

import json

from engenia.core import events
from engenia.core.engine import execute

from .test_repair_loop import Script, gate_result, go, verdict

KEYS = {"ts", "run_id", "project", "stage", "attempt", "kind", "data"}


def lines(root, run_id=1):
    path = events.events_path(root, run_id)
    return [json.loads(x) for x in path.read_text().splitlines()]


def test_emit_writes_the_exact_format(root):
    with events.activate(root, 7, "demo"):
        events.emit(root, "mechanical", "message", {"text": "hi"})
    (event,) = lines(root, 7)
    assert event["project"] == "demo"
    assert set(event) == KEYS
    assert (event["run_id"], event["stage"], event["attempt"], event["kind"]) == (
        7, "mechanical", 1, "message")
    assert event["data"] == {"text": "hi"}


def test_no_active_run_means_no_file_and_no_error(root):
    events.emit(root, "mechanical", "message", {"text": "hi"})
    assert not events.events_dir(root).exists()


def test_unknown_kind_is_dropped_not_raised(root):
    with events.activate(root, 1):
        events.emit(root, "mechanical", "gossip", {})
    assert not events.events_path(root, 1).exists()


def test_attempt_comes_from_the_registry(root):
    events.set_attempt(root, "mechanical", 3)
    with events.activate(root, 1):
        events.emit(root, "mechanical", "message", {})
    assert lines(root, 1)[0]["attempt"] == 3


def test_secrets_never_reach_the_log(root, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "hunter2-hunter2")
    with events.activate(root, 1):
        events.emit(root, "mechanical", "message", {
            "text": "key hunter2-hunter2, sk-abcdefghijklmnopqrstuvwx, Bearer abcdefghijklmnop12",
            "api_key": "whatever",
            "nested": [{"Authorization": "x"}],
        })
    raw = events.events_path(root, 1).read_text()
    for secret in ("hunter2", "sk-abcdef", "abcdefghijklmnop12", "whatever"):
        assert secret not in raw
    assert "[redacted]" in raw


def test_read_events_filters_by_stage_and_waits_for_whole_lines(root):
    with events.activate(root, 1):
        events.emit(root, "mechanical", "message", {"n": 1})
        events.emit(root, "firmware", "message", {"n": 2})
    path = events.events_path(root, 1)
    with open(path, "a") as handle:
        handle.write('{"half": ')  # a line still being written
    found, offset = events.read_events(root, 1, stage="firmware")
    assert [e["data"]["n"] for e in found] == [2]
    again, same = events.read_events(root, 1, offset=offset)
    assert again == [] and same == offset


def test_engine_logs_stage_messages_and_events_do_not_touch_keys(root, fake_registry):
    registry = fake_registry()
    first = execute("harbour-listener", root, registry=registry)
    found = lines(root, first.run_id)
    assert {e["stage"] for e in found} >= {"mechanical"}
    assert all(set(e) == KEYS and e["kind"] == "message" for e in found)
    assert any("started" in e["data"]["text"] for e in found)
    # the log lives outside projects/, so no stage input or output can see it
    assert not list((root / "projects").rglob("*.jsonl"))
    # a second run, with the first run's log on disk, sees the same keys: everything is cached
    second = execute("harbour-listener", root, registry=registry)
    assert [(r.stage, r.key) for r in second.results] == [(r.stage, r.key) for r in first.results]
    assert all(r.status.value == "cached" for r in second.results)


def test_repair_loop_emits_repair_events(root, fake_registry):
    script = Script([gate_result(verdict()), gate_result(verdict(result="pass"))], ["repaired"])
    report = go(root, fake_registry, script)
    repairs = [e for e in lines(root, report.run.run_id) if e["kind"] == "repair"]
    assert [(e["stage"], e["data"]["phase"], e["data"]["outcome"]) for e in repairs] == [
        ("mechanical", "round", "repaired")]
    assert repairs[0]["data"]["failed"] == ["buoyancy_margin"]


def test_project_filter_on_read_and_latest_run(root):
    with events.activate(root, 1, "alpha"):
        events.emit(root, "mechanical", "message", {"n": 1})
    with events.activate(root, 2, "beta"):
        events.emit(root, "mechanical", "message", {"n": 2})
    assert events.latest_run_id(root) == 2
    assert events.latest_run_id(root, "alpha") == 1
    assert events.latest_run_id(root, "nobody") is None
    assert events.read_events(root, 1, project="beta")[0] == []
    assert len(events.read_events(root, 1, project="alpha")[0]) == 1


def test_engine_stamps_the_project_on_every_event(root, fake_registry):
    report = execute("harbour-listener", root, registry=fake_registry())
    assert {e["project"] for e in lines(root, report.run_id)} == {"harbour-listener"}
