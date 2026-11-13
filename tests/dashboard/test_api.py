import ast
import asyncio
import importlib
import json
import os
import shutil
import sqlite3

import pytest
from fastapi.testclient import TestClient

from engenia.dashboard import create_app
from engenia.dashboard.changes import changed_areas, snapshot, stream_events
from engenia.dashboard.reader import ProjectReader
from engenia.dashboard.schemas import (
    ContractResponse,
    EventsResponse,
    InterfaceFile,
    PipelineResponse,
    RequirementsResponse,
    ScenariosResponse,
    StageResponse,
)
from tests.dashboard.conftest import PROJECT, SAMPLES, sample, write_json

MODELS = {
    "pipeline": PipelineResponse,
    "requirements": RequirementsResponse,
    "stages": StageResponse,
    "contract": ContractResponse,
    "scenarios": ScenariosResponse,
    "files": InterfaceFile,
    "events": EventsResponse,
}


@pytest.mark.parametrize("name", MODELS)
def test_samples_validate(name):
    MODELS[name].model_validate(sample(name))


@pytest.mark.parametrize(
    "endpoint, model",
    [
        ("pipeline", PipelineResponse),
        ("requirements", RequirementsResponse),
        ("stages/mechanical", StageResponse),
        ("contract", ContractResponse),
        ("scenarios", ScenariosResponse),
        ("files/mechanical/interface.json", InterfaceFile),
    ],
)
def test_endpoint_shapes(client, endpoint, model):
    response = client.get(f"/api/{endpoint}")
    assert response.status_code == 200
    model.model_validate(response.json())
    assert response.json() == model.model_validate(response.json()).model_dump(mode="json") or (
        endpoint == "pipeline"  # ISO timestamps may serialise UTC as Z.
    )


def test_statuses_and_unverified(client):
    stages = {item["name"]: item for item in client.get("/api/pipeline").json()["stages"]}
    assert stages["mechanical"]["status"] == "failed"
    assert stages["testplan"]["status"] == "stub"
    assert stages["firmware"]["status"] == "not_run"
    assert stages["firmware"]["key"] is None
    assert stages["mechanical"]["needs"] == ["architect"]
    unverified = client.get("/api/requirements").json()["requirements"][1]
    assert unverified["status"] == "unverified"
    assert unverified["verdict"] is None
    assert client.get("/api/stages/firmware").json() == {
        "name": "firmware",
        "status": "not_run",
        "facts": {},
        "verdicts": [],
    }
    assert client.get("/api/stages/unknown").status_code == 404


@pytest.mark.parametrize(
    "result, stage_status", [("pass", "done"), ("fail", "failed"), ("error", "failed")]
)
def test_results_are_copied_without_evaluation(project, client, result, stage_status):
    verdict = sample("stages")["verdicts"][0]
    verdict.update(result=result, measured=1000000, limit={"max": 0})
    write_json(project / "build" / "mechanical" / "verdict.json", [verdict])
    assert client.get("/api/stages/mechanical").json()["status"] == stage_status
    requirement = client.get("/api/requirements").json()["requirements"][0]
    assert requirement["status"] == result
    assert requirement["verdict"]["result"] == result


def test_latest_verdict_and_stored_fields(project, client):
    older = project / "build" / "mechanical" / "verdict.json"
    os.utime(older, ns=(1, 1))
    verdict = sample("stages")["verdicts"][0]
    verdict.pop("message")
    verdict["result"] = "error"
    write_json(project / "build" / "simulation" / "verdict.json", [verdict])
    requirement = client.get("/api/requirements").json()["requirements"][0]
    assert requirement["status"] == "error"
    assert requirement["verdict"]["stage"] == "simulation"
    assert requirement["verdict"]["message"] is None
    assert client.get("/api/stages/simulation").json()["verdicts"] == [verdict]


def test_contract_sources_and_stored_requests(project, client):
    assert (
        client.get("/api/contract").json()["change_requests"]
        == sample("contract")["change_requests"]
    )
    (project / "contract" / "contract.json").unlink()
    default = project.parents[1] / "packs" / "buoy" / "default_contract.json"
    write_json(default, sample("contract")["contract"])
    assert client.get("/api/contract").json()["source"] == "default"
    default.unlink()
    response = client.get("/api/contract").json()
    assert response["source"] == "none"
    assert response["contract"] is None


def test_empty_project(project, client):
    shutil.rmtree(project / "build")
    shutil.rmtree(project / "contract")
    for endpoint, model in [
        ("pipeline", PipelineResponse),
        ("requirements", RequirementsResponse),
        ("contract", ContractResponse),
        ("scenarios", ScenariosResponse),
    ]:
        response = client.get(f"/api/{endpoint}")
        assert response.status_code == 200
        model.model_validate(response.json())
    assert all(s["status"] == "not_run" for s in client.get("/api/pipeline").json()["stages"])


@pytest.mark.parametrize(
    "url",
    [
        "/api/files/mechanical/%2e%2e/electronics/private.json",
        "/api/files/mechanical/..%2felectronics/private.json",
        "/api/files/mechanical/%2Fetc/passwd",
        "/api/files/mechanical/%5c..%5celectronics/private.json",
        "/api/files/mechanical/%00",
        "/api/files/unknown/interface.json",
        "/api/files/mechanical/absent.json",
    ],
)
def test_download_refuses_escapes(project, client, url):
    write_json(project / "build" / "electronics" / "private.json", {"private": True})
    assert client.get(url).status_code == 404


def test_download_bytes_nested_files_and_symlinks(project, client):
    directory = project / "build" / "mechanical"
    asset = directory / "cad" / "hull.stl"
    asset.parent.mkdir()
    asset.write_bytes(b"solid hull\nendsolid hull\n")
    response = client.get("/api/files/mechanical/cad/hull.stl")
    assert response.status_code == 200 and response.content == asset.read_bytes()
    target = project / "build" / "electronics" / "private.json"
    write_json(target, {"private": True})
    (directory / "escape.json").symlink_to(target)
    assert client.get("/api/files/mechanical/escape.json").status_code == 404
    shutil.rmtree(directory)
    directory.symlink_to(target.parent, target_is_directory=True)
    assert client.get("/api/files/mechanical/private.json").status_code == 404


@pytest.mark.parametrize("location", ["root", "project"])
def test_latest_run_overrides_artifacts(project, client, location):
    base = project if location == "project" else project.parents[1]
    path = base / ".engenia" / "run.db"
    path.parent.mkdir()
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE run (id INTEGER PRIMARY KEY, project TEXT, started TEXT)")
        db.execute("CREATE TABLE stage_run (run_id INTEGER, stage TEXT, key TEXT, status TEXT)")
        db.executemany(
            "INSERT INTO run VALUES (?, ?, ?)",
            [(1, PROJECT, "2026-11-12"), (2, PROJECT, "2026-11-13"), (3, "other", "2026-11-14")],
        )
        db.executemany(
            "INSERT INTO stage_run VALUES (?, ?, ?, ?)",
            [
                (1, "mechanical", "old", "failed"),
                (2, "mechanical", "current", "done"),
                (2, "electronics", None, "running"),
                (2, "firmware", None, "blocked"),
                (2, "integration", None, "failed"),
                (3, "mechanical", "other", "failed"),
            ],
        )
    original = path.read_bytes()
    stages = {s["name"]: s for s in client.get("/api/pipeline").json()["stages"]}
    assert stages["mechanical"]["status"] == "done"
    assert stages["mechanical"]["key"] == "current"
    for name, status in [
        ("electronics", "running"),
        ("firmware", "blocked"),
        ("integration", "failed"),
    ]:
        assert stages[name]["status"] == status
        assert client.get(f"/api/stages/{name}").json()["status"] == status
    assert path.read_bytes() == original


@pytest.mark.parametrize(
    "relative, areas",
    [
        ("build/mechanical/hull.stl", ["pipeline", "design"]),
        ("build/electronics/interface.json", ["pipeline", "contract", "design"]),
        ("build/mechanical/verdict.json", ["pipeline", "requirements", "design"]),
        ("build/redteam/scenarios/storm.yaml", ["pipeline", "scenarios"]),
        ("spec/R-005.yaml", ["requirements"]),
        ("contract/change_requests/CR-001.json", ["contract"]),
        (".engenia/run.db", ["pipeline"]),
    ],
)
def test_changed_areas_creation_update_deletion(project, relative, areas):
    reader = ProjectReader(project.parents[1], PROJECT)
    path = project / relative
    if path.exists():
        path.unlink()
    before = snapshot(reader)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("first")
    after = snapshot(reader)
    assert changed_areas(before, after) == areas
    assert changed_areas(after, snapshot(reader)) == []
    path.write_text("different size")
    updated = snapshot(reader)
    assert changed_areas(after, updated) == areas
    path.unlink()
    assert changed_areas(updated, snapshot(reader)) == areas


def test_events_endpoint(client, monkeypatch):
    async def finite_stream(reader, request):
        yield f"data: {json.dumps(sample('events'))}\n\n"

    monkeypatch.setattr(
        importlib.import_module("engenia.dashboard.app"), "stream_events", finite_stream
    )
    response = client.get("/api/events")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    EventsResponse.model_validate(json.loads(response.text.removeprefix("data: ").strip()))


def test_stream_polls_every_second_and_stops(project, monkeypatch):
    reader = ProjectReader(project.parents[1], PROJECT)

    class Request:
        disconnected = False

        async def is_disconnected(self):
            return self.disconnected

    request = Request()

    async def tick(seconds):
        assert seconds == 1
        (project / "build" / "mechanical" / "hull.stl").write_text("changed")

    monkeypatch.setattr("engenia.dashboard.changes.asyncio.sleep", tick)

    async def consume():
        stream = stream_events(reader, request)
        event = await anext(stream)
        assert json.loads(event.removeprefix("data: ")) == {"changed": ["pipeline", "design"]}
        request.disconnected = True
        with pytest.raises(StopAsyncIteration):
            await anext(stream)

    asyncio.run(consume())


def test_source_has_no_measured_limit_comparisons():
    for path in SAMPLES.parent.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
                strings = {
                    n.value
                    for n in ast.walk(node)
                    if isinstance(n, ast.Constant) and isinstance(n.value, str)
                }
                attrs = {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}
                assert not {"measured", "limit"} & (names | strings | attrs), path


def test_cli_defaults_and_host_override(project, monkeypatch):
    from engenia.dashboard.__main__ import main

    monkeypatch.setenv("ENGENIA_ROOT", str(project.parents[1]))
    calls = []
    monkeypatch.setattr("engenia.dashboard.__main__.uvicorn.run", lambda *a, **kw: calls.append(kw))
    monkeypatch.setattr("sys.argv", ["dashboard", "--project", PROJECT, "--port", "8080"])
    main()
    assert calls.pop() == {"host": "127.0.0.1", "port": 8080}
    monkeypatch.setattr("sys.argv", ["dashboard", "--project", PROJECT, "--host", "0.0.0.0"])
    main()
    assert calls.pop() == {"host": "0.0.0.0", "port": 8080}


def test_static_mount_without_editing_frontend(project, monkeypatch, tmp_path):
    module = importlib.import_module("engenia.dashboard.app")
    backend = tmp_path / "fake-dashboard"
    static = backend / "static"
    static.mkdir(parents=True)
    (static / "index.html").write_text("<html>Dashboard</html>")
    monkeypatch.setattr(module, "__file__", str(backend / "app.py"))
    with TestClient(create_app(PROJECT, project.parents[1])) as client:
        assert client.get("/").text == "<html>Dashboard</html>"
        assert client.get("/api/pipeline").status_code == 200


def test_invalid_project_paths(project):
    for name in ["..", "/tmp", "../harbour-listener", "missing"]:
        with pytest.raises(ValueError):
            create_app(name, project.parents[1])


def test_transient_partial_json_is_retryable(project, client):
    (project / "build" / "mechanical" / "interface.json").write_text('{"facts":')
    assert client.get("/api/pipeline").status_code == 503


def test_api_is_read_only(client):
    assert client.post("/api/pipeline", json={}).status_code == 405


def test_get_requests_do_not_write_artifacts(project, client):
    def metadata():
        return {
            p.relative_to(project): (p.stat().st_mtime_ns, p.read_bytes())
            for p in project.rglob("*")
            if p.is_file()
        }

    before = metadata()
    for endpoint in [
        "pipeline",
        "requirements",
        "stages/mechanical",
        "contract",
        "scenarios",
        "files/mechanical/interface.json",
    ]:
        assert client.get(f"/api/{endpoint}").status_code == 200
    assert metadata() == before


def test_latest_run_uses_started_time(project, client):
    path = project / ".engenia" / "run.db"
    path.parent.mkdir()
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE run (id TEXT PRIMARY KEY, started TEXT)")
        db.execute("CREATE TABLE stage_run (run_id TEXT, stage TEXT, key TEXT, status TEXT)")
        db.executemany(
            "INSERT INTO run VALUES (?, ?)",
            [("z-old", "2026-11-12T10:00:00Z"), ("a-new", "2026-11-13T10:00:00Z")],
        )
        db.executemany(
            "INSERT INTO stage_run VALUES (?, ?, ?, ?)",
            [("z-old", "firmware", None, "failed"), ("a-new", "firmware", None, "running")],
        )
    assert client.get("/api/stages/firmware").json()["status"] == "running"


def test_build_directory_symlink_cannot_escape(project, client):
    build = project / "build"
    elsewhere = project.parent / "elsewhere"
    build.rename(elsewhere)
    build.symlink_to(elsewhere, target_is_directory=True)
    assert client.get("/api/files/mechanical/interface.json").status_code == 404
