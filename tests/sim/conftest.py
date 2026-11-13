"""Scratch repositories for the simulation lane: its own fixture project.

Nothing here reads projects/harbour-listener. The fixture hull is a 100 mm
cylinder whose stability numbers are hand-checked in test_waves.py.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from engenia.contracts.models import CheckContext, Limit, StageContext
from engenia.sim import waves
from engenia.sim.simulation import RESULTS_FILE, SimulationStage

PROJECT = "demo"
INTERFACE_KEY = "sha256:" + "0" * 64

HULL_FACTS: dict[str, Any] = {
    "bbox_mm": [100.0, 100.0, 100.0],
    "displaced_volume_cm3": 785.398163,  # a 10 cm draft on a 100 mm waterplane
    "centre_of_mass_mm": [0.0, 0.0, 40.0],
    "dry_mass_g": 500.0,
    "wall_min_mm": 3.0,
}
ELECTRONICS_FACTS: dict[str, Any] = {"mass_g": 100.0}


@pytest.fixture
def write_interface(tmp_path: Path):
    """write_interface(stage, facts): an interface.json in the fixture build tree."""

    def _write(stage: str, facts: dict[str, Any], project: str = PROJECT) -> Path:
        path = tmp_path / "projects" / project / "build" / stage / "interface.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"stage": stage, "key": INTERFACE_KEY, "facts": facts}, indent=2) + "\n",
            encoding="utf-8",
        )
        return path

    return _write


@pytest.fixture
def write_scenario(tmp_path: Path):
    """write_scenario(name, wave_height_m, wave_period_s): a built red-team scenario."""

    def _write(name: str, wave_height_m: float, wave_period_s: float) -> Path:
        folder = tmp_path / "projects" / PROJECT / "build" / "redteam" / "scenarios"
        folder.mkdir(parents=True, exist_ok=True)
        data = {
            "name": name,
            "wave_height_m": wave_height_m,
            "wave_period_s": wave_period_s,
            "water_temp_c": 15.0,
            "water_density": 1005.0,
            "depth_m": 3.0,
            "extra": {},
            "rationale": "fixture",
            "targets": ["R-011"],
        }
        path = folder / f"{name}.yaml"
        path.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")
        return path

    return _write


@pytest.fixture
def results_file(tmp_path: Path):
    """The path of the simulation stage's results.json in the fixture build tree."""

    def _path() -> Path:
        return tmp_path / "projects" / PROJECT / "build" / "simulation" / RESULTS_FILE

    return _path


@pytest.fixture
def sim_root(tmp_path: Path, write_interface, write_scenario) -> Path:
    """A scratch repo: hull facts plus scenarios at 0.4, 0.5 and 2.0 m waves."""
    write_interface("mechanical", HULL_FACTS)
    write_interface("electronics", ELECTRONICS_FACTS)
    write_interface("integration", {"total_mass_g": 600.0, "failed_checks": []})
    write_scenario("inshore", 0.4, 4.0)
    write_scenario("boundary", 0.5, 4.0)
    write_scenario("storm", 2.0, 8.0)
    return tmp_path


@pytest.fixture
def stability() -> dict[str, float]:
    """Stability numbers of the fixture hull."""
    return waves.stability_from_facts(HULL_FACTS)


@pytest.fixture
def sim_ctx_factory(tmp_path: Path, write_interface):
    """make(facts, check=..., requirement=..., params=..., limit=..., write=True)."""

    def _make(
        facts: dict[str, Any] | None = None,
        *,
        check: str = "wave_tilt",
        requirement: str = "R-011",
        params: dict[str, Any] | None = None,
        limit: Limit | None = None,
        write: bool = True,
    ) -> CheckContext:
        if write:
            write_interface("simulation", facts if facts is not None else {})
        return CheckContext(
            root=tmp_path,
            project=PROJECT,
            stage="simulation",
            check=check,
            requirement=requirement,
            params=params or {},
            limit=limit or Limit(max=30),
        )

    return _make


@pytest.fixture
def run_sim_stage(tmp_path: Path):
    """Run the real simulation stage build() and return its interface facts."""

    def _run(root: Path | None = None) -> dict[str, Any]:
        ctx = StageContext(project=PROJECT, stage="simulation", root=root or tmp_path)
        SimulationStage().build(ctx)
        return SimulationStage().interface(ctx)

    return _run
