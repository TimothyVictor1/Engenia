import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from engenia.dashboard import create_app

SAMPLES = Path(__file__).resolve().parents[2] / "engenia" / "dashboard" / "samples"
PROJECT = "harbour-listener"


def sample(name):
    return json.loads((SAMPLES / f"{name}.json").read_text())


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


@pytest.fixture
def project(tmp_path):
    directory = tmp_path / "projects" / PROJECT
    spec = directory / "spec"
    spec.mkdir(parents=True)
    for item in sample("requirements")["requirements"]:
        data = {key: item[key] for key in ("id", "statement", "unit", "affects")}
        data.update(
            value=0.20,
            status="approved",
            verify={"method": item["method"], "check": "buoyancy_margin", "limit": item["limit"]},
        )
        (spec / f"{item['id']}.yaml").write_text(yaml.safe_dump(data))
    build = directory / "build"
    write_json(build / "mechanical" / "interface.json", sample("files"))
    write_json(build / "mechanical" / "verdict.json", sample("stages")["verdicts"])
    write_json(build / "testplan" / "interface.json", {"facts": {"stub": True}})
    scenarios = build / "redteam" / "scenarios"
    scenarios.mkdir(parents=True)
    (scenarios / "storm.yaml").write_text(yaml.safe_dump(sample("scenarios")["scenarios"][0]))
    write_json(directory / "contract" / "contract.json", sample("contract")["contract"])
    for item in sample("contract")["change_requests"]:
        write_json(directory / "contract" / "change_requests" / f"{item['id']}.json", item)
    return directory


@pytest.fixture
def app(project):
    return create_app(PROJECT, project.parents[1])


@pytest.fixture
def client(app):
    with TestClient(app) as client:
        yield client


def event_line(run_id, agent, stage, kind, data, ts, phase=None):
    return json.dumps({
        "ts": ts, "run_id": run_id, "project": PROJECT, "stage": stage,
        "attempt": 1, "kind": kind, "data": data, "agent": agent, "phase": phase,
    })


MECHANICAL = {
    "name": "mechanical", "role": "design", "model": "", "family": "", "kind": "deterministic",
}


@pytest.fixture
def root_events(project):
    """Two finished runs in the dashboard root's event log: run 1 has a tool call only; run 2,
    the newest, has loop counts, a closed build phase, a cache hit and a check phase still open."""
    verifiers = project.parents[1] / "verifiers"  # the registry lists verifier scripts too
    verifiers.mkdir(exist_ok=True)
    (verifiers / "buoyancy_margin.py").write_text("# stub\n")
    events_dir = project.parents[1] / ".engenia" / "events"
    events_dir.mkdir(parents=True)
    run1 = [
        event_line(1, MECHANICAL, "mechanical", "tool_call", {"id": "t1"},
                   "2026-11-14T08:00:00.000+00:00"),
    ]
    run2 = [
        event_line(2, MECHANICAL, "mechanical", "message",
                   {"role": "assistant", "text": "hi", "tool_calls": 0},
                   "2026-11-14T08:01:00.000+00:00"),
        event_line(2, MECHANICAL, "mechanical", "tool_call", {"id": "t2"},
                   "2026-11-14T08:01:00.100+00:00"),
        event_line(2, MECHANICAL, "mechanical", "tool_call", {"id": "t3"},
                   "2026-11-14T08:01:00.200+00:00"),
        event_line(2, MECHANICAL, "mechanical", "phase_start", {"phase": "build"},
                   "2026-11-14T08:01:00.300+00:00", phase="build"),
        event_line(2, MECHANICAL, "mechanical", "phase_end",
                   {"phase": "build", "duration_ms": 12, "ok": True},
                   "2026-11-14T08:01:00.312+00:00", phase="build"),
        event_line(2, MECHANICAL, "mechanical", "cache",
                   {"key": "sha256:abcdef0123456789", "hit": True},
                   "2026-11-14T08:01:00.330+00:00", phase="cache"),
        event_line(2, MECHANICAL, "mechanical", "phase_start", {"phase": "check"},
                   "2026-11-14T08:01:00.400+00:00", phase="check"),
    ]
    (events_dir / "1.jsonl").write_text("\n".join(run1) + "\n")
    (events_dir / "2.jsonl").write_text("\n".join(run2) + "\n")
    return project.parents[1]
