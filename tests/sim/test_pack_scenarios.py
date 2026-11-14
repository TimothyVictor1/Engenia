"""Nonfloating product scenarios do not run buoy solvers."""

from engenia.agents.redteam.stage import Scenario
from engenia.sim.flow import solver as flow
from engenia.sim.waves import solver as waves


def test_wave_fields_are_optional():
    scenario = Scenario.model_validate({
        "name": "heat_soak", "water_temp_c": 35,
        "rationale": "Enclosure on deck", "targets": ["R-003"],
    })
    assert scenario.wave_height_m is None
    assert scenario.wave_period_s is None
    assert scenario.water_density is None
    assert scenario.depth_m is None


def test_wave_solver_records_nonfloating_skip(monkeypatch):
    monkeypatch.setattr(waves, "load_params", lambda ctx: {"gravity_m_s2": 9.81})
    monkeypatch.setattr(waves, "load_buoy", lambda ctx: {"com_source": "assembly"})
    monkeypatch.setattr(waves, "load_scenarios", lambda ctx: {"heat_soak": {
        "name": "heat_soak", "wave_height_m": None, "wave_period_s": None,
        "water_density": None, "depth_m": None,
    }})
    result = waves.run_case({}, None)
    assert result["wave_tilt"] == {}
    assert result["sanity"]["passed"] is True
    assert "heat_soak: skipped" in result["notes"][0]


def test_flow_solver_records_nonfloating_skip(monkeypatch):
    monkeypatch.setattr(flow, "load_params", lambda ctx: {"cd": 1.0})
    monkeypatch.setattr(flow, "load_buoy", lambda ctx: {})
    monkeypatch.setattr(flow, "load_scenarios", lambda ctx: {"heat_soak": {
        "name": "heat_soak", "water_density": None,
    }})
    result = flow.run_case({}, None)
    assert result["drag_n"] == {}
    assert result["sanity"]["passed"] is True
    assert "heat_soak: skipped" in result["notes"][0]
