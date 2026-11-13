import copy
import json

import capytaine as cpt
import numpy as np
import pytest

from engenia.sim.waves import hull, run_case
from engenia.sim.waves.solver import load_buoy, load_params, mesh_messages
from tests.sim.conftest import write_interface, write_scenario

CASE = {"id": "SIM-011", "solver": "waves", "requirement": "R-011", "scenarios": []}


def tilt(ctx, name="short_chop"):
    result = run_case(CASE, ctx)
    assert result["sanity"] == {"passed": True, "messages": []}, result
    return result["wave_tilt"][name]["max_tilt_deg"]


def test_doubling_wave_height_doubles_tilt(root, ctx):
    base = tilt(ctx)
    write_scenario(root, "short_chop", wave_height_m=1.0, wave_period_s=4.5)
    doubled = tilt(ctx)
    assert doubled == pytest.approx(2 * base, rel=0.01)


def test_long_waves_follow_the_wave_slope(root, ctx):
    # A hull far smaller than the wave rides the surface: pitch amplitude is the slope, k * a.
    write_scenario(root, "swell", wave_height_m=1.0, wave_period_s=8.0, depth_m=60.0)
    omega, g = 2 * np.pi / 8.0, 9.81
    k = omega**2 / g  # deep water, the 60 m depth is over a wavelength
    expected = np.degrees(k * 0.5)
    assert tilt(ctx, "swell") == pytest.approx(expected, rel=0.03)


def test_reports_every_scenario_with_its_wave_height(ctx):
    result = run_case(CASE, ctx)
    assert result["wave_tilt"].keys() == {"short_chop", "swell"}
    assert result["wave_tilt"]["swell"]["wave_height_m"] == 1.0
    assert all(0 < v["max_tilt_deg"] < 90 for v in result["wave_tilt"].values())


def test_two_runs_are_identical(ctx):
    assert json.dumps(run_case(CASE, ctx), sort_keys=True) == json.dumps(
        run_case(CASE, ctx), sort_keys=True
    )


def hull_mesh(ctx, mass_scale=1.0):
    buoy, params = load_buoy(ctx), load_params(ctx)
    area = np.pi * buoy["radius_m"] ** 2
    draft = buoy["mass_kg"] * mass_scale / (1005.0 * area)
    return hull.build_mesh(buoy["radius_m"], buoy["height_m"], draft, params["mesh_resolution"])


def test_closed_mesh_with_right_volume_passes(ctx):
    level1 = load_buoy(ctx)["mass_kg"] / 1005.0
    assert mesh_messages(hull_mesh(ctx), level1, 0.05) == []


def test_open_mesh_is_rejected(ctx):
    mesh = hull_mesh(ctx)
    assert hull.is_closed(mesh)
    open_mesh = cpt.Mesh(vertices=mesh.vertices, faces=mesh.faces[:-1])
    assert not hull.is_closed(open_mesh)
    messages = mesh_messages(open_mesh, load_buoy(ctx)["mass_kg"] / 1005.0, 0.05)
    assert "hull mesh is not closed" in messages


def test_wrong_displaced_volume_is_rejected(ctx):
    level1 = load_buoy(ctx)["mass_kg"] / 1005.0
    for scale in (1.2, 0.8):
        messages = mesh_messages(hull_mesh(ctx, mass_scale=scale), level1, 0.05)
        assert len(messages) == 1 and "displaced volume" in messages[0]


def test_failed_mesh_check_fails_the_case(root, ctx, monkeypatch):
    monkeypatch.setattr(hull, "is_closed", lambda mesh: False)
    result = run_case(CASE, ctx)
    assert result["sanity"]["passed"] is False
    assert result["wave_tilt"] == {}
    assert any("not closed" in m for m in result["sanity"]["messages"])


def test_sinking_hull_fails_the_case(root, ctx):
    write_interface(root, "integration", {"total_mass_g": 900.0, "centre_of_mass_mm": None})
    result = run_case(CASE, ctx)
    assert result["sanity"]["passed"] is False
    assert any("does not float" in m for m in result["sanity"]["messages"])


def test_missing_upstream_facts_fail_the_case_without_raising(root, ctx):
    (root / "projects/fixture/build/integration/interface.json").unlink()
    result = run_case(CASE, ctx)
    assert result["sanity"]["passed"] is False


def test_stale_scenario_list_is_flagged(ctx):
    case = copy.deepcopy(CASE) | {"scenarios": ["short_chop"]}
    result = run_case(case, ctx)
    assert result["sanity"]["passed"] is False
    assert "swell" in result["sanity"]["messages"][0]


def test_hull_centre_of_mass_used_when_assembly_has_none(root, ctx):
    write_interface(root, "integration", {"total_mass_g": 191.9, "centre_of_mass_mm": None})
    result = run_case(CASE, ctx)
    assert result["sanity"]["passed"] and result["notes"]
