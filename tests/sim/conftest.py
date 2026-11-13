"""A scratch project with hand-written upstream interfaces, so solver tests skip the pipeline."""

import json
import shutil
from pathlib import Path

import pytest
import yaml

from engenia.contracts.models import StageContext

REPO = Path(__file__).resolve().parents[2]
SPEC_DIR = REPO / "projects" / "harbour-listener" / "spec"


def write_interface(root: Path, stage: str, facts: dict) -> None:
    path = root / "projects" / "fixture" / "build" / stage / "interface.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"stage": stage, "key": "sha256:" + "0" * 64, "facts": facts}))


def write_scenario(root: Path, name: str, **fields) -> None:
    scenario = {
        "name": name, "wave_height_m": 0.5, "wave_period_s": 4.5, "water_temp_c": 10.0,
        "water_density": 1005.0, "depth_m": 5.0, "extra": {}, "rationale": "test",
        "targets": ["R-011"],
    } | fields
    path = root / "projects" / "fixture" / "build" / "redteam" / "scenarios" / f"{name}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(scenario))


@pytest.fixture
def root(tmp_path):
    shutil.copytree(SPEC_DIR, tmp_path / "projects" / "fixture" / "spec")
    write_interface(tmp_path, "mechanical", {
        "bbox_mm": [114.0, 114.0, 43.2], "centre_of_mass_mm": [0.0, 0.0, 22.8],
    })
    write_interface(tmp_path, "integration", {
        "total_mass_g": 191.9, "centre_of_mass_mm": [0.0, 0.0, 20.0],
    })
    write_scenario(tmp_path, "short_chop", wave_height_m=0.5, wave_period_s=4.5)
    write_scenario(tmp_path, "swell", wave_height_m=1.0, wave_period_s=8.0)
    return tmp_path


@pytest.fixture
def ctx(root):
    return StageContext(project="fixture", stage="simulation", root=root)
