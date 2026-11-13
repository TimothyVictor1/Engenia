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
