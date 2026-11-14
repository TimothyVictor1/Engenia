"""The project switcher: /api/projects, and one server for every project (create_multi_app)."""

from __future__ import annotations

import pytest
import yaml
from fastapi.testclient import TestClient

from engenia.dashboard import create_app, create_multi_app
from engenia.dashboard.reader import list_projects


def make_project(root, name, pack=None, requirements=2):
    spec = root / "projects" / name / "spec"
    spec.mkdir(parents=True)
    for number in range(1, requirements + 1):
        (spec / f"R-{number:03d}.yaml").write_text(yaml.safe_dump({"id": f"R-{number:03d}"}))
    if pack:
        (root / "projects" / name / "project.yaml").write_text(f"pack: {pack}\n")


@pytest.fixture
def root(tmp_path):
    make_project(tmp_path, "harbour-listener")
    make_project(tmp_path, "sensor-pod", "pressure_housing", requirements=6)
    make_project(tmp_path, "antenna-bracket", "bracket", requirements=4)
    (tmp_path / "projects" / "empty").mkdir()  # no spec, not a project
    (tmp_path / "projects" / "not-a-dir.txt").write_text("x")
    return tmp_path


def test_list_projects_names_each_with_its_pack_and_skips_non_projects(root):
    assert list_projects(root) == [
        {"name": "antenna-bracket", "pack": "bracket", "requirements": 4},
        {"name": "harbour-listener", "pack": "buoy", "requirements": 2},
        {"name": "sensor-pod", "pack": "pressure_housing", "requirements": 6},
    ]


def test_a_malformed_project_yaml_falls_back_to_the_buoy_pack(root):
    (root / "projects" / "sensor-pod" / "project.yaml").write_text("pack: [oops\n")
    assert {p["name"]: p["pack"] for p in list_projects(root)}["sensor-pod"] == "buoy"


def test_a_single_project_server_lists_projects_but_is_not_switchable(root):
    with TestClient(create_app("sensor-pod", root)) as client:
        body = client.get("/api/projects").json()
    assert body["current"] == "sensor-pod" and body["switchable"] is False
    assert [p["name"] for p in body["projects"]] == [
        "antenna-bracket", "harbour-listener", "sensor-pod"]


def test_the_multi_project_server_serves_each_project_at_its_own_address(root):
    with TestClient(create_multi_app(root)) as client:
        for name in ("antenna-bracket", "harbour-listener", "sensor-pod"):
            body = client.get(f"/p/{name}/api/projects").json()
            assert body["current"] == name and body["switchable"] is True
            assert len(body["projects"]) == 3
            pipeline = client.get(f"/p/{name}/api/pipeline")
            assert pipeline.status_code == 200 and pipeline.json()["project"] == name
        assert client.get("/p/nonexistent/api/projects").status_code == 404


def test_the_root_redirects_to_the_default_project(root):
    with TestClient(create_multi_app(root), follow_redirects=False) as client:
        assert client.get("/").headers["location"] == "/p/harbour-listener/"
    with TestClient(create_multi_app(root, default="sensor-pod"),
                    follow_redirects=False) as client:
        assert client.get("/").headers["location"] == "/p/sensor-pod/"
        assert client.get("/api/projects").json()["current"] == "sensor-pod"


def test_the_default_is_the_first_project_when_there_is_no_harbour_listener(tmp_path):
    make_project(tmp_path, "b-project")
    make_project(tmp_path, "a-project")
    with TestClient(create_multi_app(tmp_path), follow_redirects=False) as client:
        assert client.get("/").headers["location"] == "/p/a-project/"


def test_the_multi_project_server_refuses_an_unknown_default_or_no_projects(root, tmp_path):
    with pytest.raises(ValueError, match="unknown project"):
        create_multi_app(root, default="nope")
    with pytest.raises(ValueError, match="no projects"):
        create_multi_app(tmp_path / "elsewhere")
