"""gmsh needs the main thread and is process-global: a parallel run must still solve."""

import json
import shutil

import pytest
from typer.testing import CliRunner

from engenia.core.cli import app
from engenia.sim.simulation import SimulationStage
from tests.sim.conftest import REPO


def _run(tmp_path, monkeypatch, name):
    root = tmp_path / name
    shutil.copytree(REPO / "projects/harbour-listener/spec",
                    root / "projects/harbour-listener/spec")
    from tests.core.pipeline_fixture import approve_fixture

    approve_fixture(root, "harbour-listener")
    monkeypatch.setenv("ENGENIA_ROOT", str(root))
    monkeypatch.setenv("ENGENIA_STORE", str(root / "store"))
    result = CliRunner().invoke(app, ["run", "harbour-listener", "-j", "4"])
    assert result.exit_code == 0, result.output
    return root / "projects/harbour-listener/build/simulation"


def test_simulation_stage_declares_main_thread():
    assert SimulationStage.main_thread is True


@pytest.mark.slow
def test_parallel_run_fills_structure_and_waves_and_is_byte_identical(tmp_path, monkeypatch):
    first = _run(tmp_path, monkeypatch, "one")
    second = _run(tmp_path, monkeypatch, "two")
    facts = json.loads((first / "interface.json").read_text())["facts"]
    assert facts["safety_factor"] is not None
    assert facts["wave_tilt"]
    for name in ("interface.json", "results.json"):
        assert (first / name).read_bytes() == (second / name).read_bytes()
