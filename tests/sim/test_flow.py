"""Flow drag and flooding checks on isolated upstream interfaces."""

from __future__ import annotations

import copy

import pytest

from engenia.sim.flow import run_case
from engenia.sim.waves.solver import flooded_buoy, load_buoy
from tests.sim.conftest import write_interface, write_scenario

CASE = {"id": "SIM-FLOW", "solver": "flow", "scenarios": []}


def test_drag_is_positive_quadratic_and_repeatable(root, ctx, monkeypatch):
    monkeypatch.setattr("engenia.sim.flow.solver.shutil.which", lambda _: None)
    write_scenario(root, "short_chop", extra={"current_speed_ms": 0.2})
    first = run_case(CASE, ctx)
    assert first == run_case(CASE, ctx)
    assert first["sanity"]["passed"]
    assert first["evidence"]["short_chop"] == "calculated"
    assert first["drag_n"]["short_chop"] > 0
    write_scenario(root, "short_chop", extra={"current_speed_ms": 0.4})
    doubled = run_case(CASE, ctx)
    assert doubled["drag_n"]["short_chop"] == pytest.approx(
        4 * first["drag_n"]["short_chop"], rel=1e-4
    )


def test_invalid_current_fails_sanity(root, ctx):
    write_scenario(root, "short_chop", extra={"current_speed_ms": 0})
    result = run_case(CASE, ctx)
    assert not result["sanity"]["passed"]
    assert "short_chop" not in result["drag_n"]


def test_flooding_adds_mass_and_increases_draft(root, ctx):
    write_interface(root, "mechanical", {
        "bbox_mm": [114.0, 114.0, 43.2], "centre_of_mass_mm": [0.0, 0.0, 22.8],
        "internal_volume_cm3": 300.0, "wall_min_mm": 3.0,
    })
    normal = load_buoy(ctx)
    scenario = {"water_density": 1005.0, "extra": {"hull_flood_percent": 10.0}}
    flooded, added_mass = flooded_buoy(normal, scenario)
    assert added_mass == pytest.approx(0.03015)
    assert flooded["mass_kg"] == pytest.approx(normal["mass_kg"] + added_mass)
    assert flooded["com_z_m"] < normal["com_z_m"]
    write_scenario(root, "short_chop", extra=copy.deepcopy(scenario["extra"]))
    wet_drag = run_case(CASE, ctx)
    write_scenario(root, "short_chop", extra={})
    dry_drag = run_case(CASE, ctx)
    assert wet_drag["sanity"]["passed"]
    assert wet_drag["drag_n"]["short_chop"] > dry_drag["drag_n"]["short_chop"]
