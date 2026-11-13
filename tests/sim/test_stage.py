"""The simulation stage publishes exactly the two interface facts."""

from __future__ import annotations

import json

import pytest

from tests.sim.conftest import ELECTRONICS_FACTS, HULL_FACTS


def test_publishes_exactly_the_two_facts(sim_root, run_sim_stage):
    facts = run_sim_stage(sim_root)
    assert set(facts) == {"wave_tilt", "safety_factor"}
    assert facts["safety_factor"] is None
    for entry in facts["wave_tilt"].values():
        assert set(entry) == {"wave_height_m", "max_tilt_deg"}
        assert isinstance(entry["wave_height_m"], float)
        assert isinstance(entry["max_tilt_deg"], float)


def test_every_scenario_is_published_including_those_above_the_threshold(sim_root, run_sim_stage):
    facts = run_sim_stage(sim_root)
    assert set(facts["wave_tilt"]) == {"boundary", "inshore", "storm"}
    assert facts["wave_tilt"]["storm"]["wave_height_m"] == 2.0  # above 0.5 m: still published


def test_qualifying_tilt_values(sim_root, run_sim_stage):
    facts = run_sim_stage(sim_root)
    assert facts["wave_tilt"]["inshore"]["max_tilt_deg"] == pytest.approx(2.9473, abs=1e-3)
    assert facts["wave_tilt"]["boundary"]["max_tilt_deg"] == pytest.approx(3.682381, abs=1e-3)
    assert facts["wave_tilt"]["storm"]["max_tilt_deg"] == pytest.approx(3.619666, abs=1e-3)


def test_two_builds_are_byte_identical(sim_root, run_sim_stage, results_file):
    run_sim_stage(sim_root)
    first = results_file().read_text()
    run_sim_stage(sim_root)
    assert results_file().read_text() == first


def test_tilt_doubles_with_wave_height_within_one_percent(sim_root, run_sim_stage, write_scenario):
    write_scenario("full", 1.0, 4.0)  # same period, twice the height
    facts = run_sim_stage(sim_root)
    ratio = (
        facts["wave_tilt"]["full"]["max_tilt_deg"] / facts["wave_tilt"]["boundary"]["max_tilt_deg"]
    )
    assert ratio == pytest.approx(2.0, rel=0.01)


def test_missing_hull_facts_publish_an_empty_wave_tilt(
    tmp_path, run_sim_stage, write_interface, write_scenario, results_file
):
    write_interface("electronics", ELECTRONICS_FACTS)
    write_scenario("inshore", 0.4, 4.0)
    facts = run_sim_stage(tmp_path)
    assert facts["wave_tilt"] == {}
    assert facts["safety_factor"] is None
    notes = json.loads(results_file().read_text())["notes"]
    assert any("wave_tilt is empty" in note for note in notes)


def test_unusable_hull_facts_publish_an_empty_wave_tilt(
    tmp_path, run_sim_stage, write_interface, write_scenario
):
    write_interface("mechanical", {"bbox_mm": [100.0, 100.0, 100.0]})
    write_scenario("inshore", 0.4, 4.0)
    assert run_sim_stage(tmp_path)["wave_tilt"] == {}


def test_unstable_hull_publishes_the_plausibility_bound(
    tmp_path, run_sim_stage, write_interface, write_scenario, results_file
):
    unstable = {**HULL_FACTS, "centre_of_mass_mm": [0.0, 0.0, 80.0]}  # KG 80 > KB + BM
    write_interface("mechanical", unstable)
    write_scenario("inshore", 0.4, 4.0)
    facts = run_sim_stage(tmp_path)
    assert facts["wave_tilt"]["inshore"]["max_tilt_deg"] == 90.0
    notes = json.loads(results_file().read_text())["notes"]
    assert any("unstable" in note for note in notes)


def test_missing_scenarios_raise(tmp_path, run_sim_stage, write_interface):
    write_interface("mechanical", HULL_FACTS)
    with pytest.raises(ValueError, match="red-team scenarios"):
        run_sim_stage(tmp_path)


def test_a_scenario_without_a_wave_period_raises(
    tmp_path, run_sim_stage, write_interface, write_scenario
):
    write_interface("mechanical", HULL_FACTS)
    write_scenario("calm", 0.1, 0.0)
    with pytest.raises(ValueError, match="wave_period_s"):
        run_sim_stage(tmp_path)
