"""Unit tests for the level-1 wave tilt model."""

from __future__ import annotations

import math

import pytest

from engenia.sim import waves


def test_stability_numbers_match_the_hand_computed_hull(stability):
    assert stability["draft_mm"] == pytest.approx(100.0)
    assert stability["kb_mm"] == pytest.approx(50.0)
    assert stability["bm_mm"] == pytest.approx(6.25)
    assert stability["gm_mm"] == pytest.approx(16.25)
    assert stability["roll_radius_of_gyration_mm"] == pytest.approx(38.188131, abs=1e-4)
    assert stability["roll_period_s"] == pytest.approx(0.601065, abs=1e-4)


def test_waterplane_diameter_fact_overrides_the_bbox(stability):
    facts = {
        "bbox_mm": [100.0, 100.0, 100.0],
        "displaced_volume_cm3": 785.398163,
        "centre_of_mass_mm": [0.0, 0.0, 40.0],
        "waterplane_diameter_mm": 200.0,
    }
    wider = waves.stability_from_facts(facts)
    assert wider["waterplane_diameter_mm"] == 200.0
    assert wider["gm_mm"] > stability["gm_mm"]  # a wider waterplane is stiffer


def test_tilt_matches_the_hand_computation(stability):
    numbers = waves.scenario_tilt(stability, 0.5, 4.0)
    assert numbers["wave_slope_deg"] == pytest.approx(3.603975, abs=1e-4)
    assert numbers["rao"] == pytest.approx(1.023102, abs=1e-4)
    assert numbers["max_tilt_deg"] == pytest.approx(3.682381, abs=1e-4)


def test_doubling_wave_height_doubles_tilt_within_one_percent(stability):
    half = waves.scenario_tilt(stability, 0.5, 4.0)["max_tilt_deg"]
    full = waves.scenario_tilt(stability, 1.0, 4.0)["max_tilt_deg"]
    assert full / half == pytest.approx(2.0, rel=0.01)


def test_zero_wave_height_gives_zero_tilt(stability):
    assert waves.scenario_tilt(stability, 0.0, 4.0)["max_tilt_deg"] == 0.0


def test_a_longer_wave_of_the_same_height_tilts_less(stability):
    short = waves.scenario_tilt(stability, 0.5, 3.0)["max_tilt_deg"]
    long = waves.scenario_tilt(stability, 0.5, 8.0)["max_tilt_deg"]
    assert long < short  # steep short chop, gentle long swell


def test_near_resonance_is_rejected(stability):
    gyration2 = (stability["roll_radius_of_gyration_mm"] / 1000.0) ** 2
    omega_n2 = waves.GRAVITY_M_S2 * (stability["gm_mm"] / 1000.0) / gyration2
    period = 2.0 * math.pi / math.sqrt(omega_n2 * 0.995)  # 0.5 percent below T_n
    with pytest.raises(waves.ModelInputError, match="resonance"):
        waves.scenario_tilt(stability, 0.5, period)


def test_beyond_resonance_uses_the_response_amplitude(stability):
    numbers = waves.scenario_tilt(stability, 0.02, 0.5)
    assert numbers["rao"] > 1.0  # amplitude only: no phase sign flip in the tilt
    assert 0.0 <= numbers["max_tilt_deg"] <= 90.0


def test_unstable_hull_is_rejected_by_the_model():
    facts = {
        "bbox_mm": [100.0, 100.0, 100.0],
        "displaced_volume_cm3": 785.398163,
        "centre_of_mass_mm": [0.0, 0.0, 80.0],  # KG 80 > KB + BM
    }
    with pytest.raises(waves.ModelInputError, match="unstable"):
        waves.scenario_tilt(waves.stability_from_facts(facts), 0.5, 4.0)


@pytest.mark.parametrize(
    "facts",
    [
        {"bbox_mm": [100.0, 100.0, 100.0]},  # no displaced volume
        {
            "bbox_mm": [100.0, 100.0, 100.0],
            "displaced_volume_cm3": 0.0,  # not positive
            "centre_of_mass_mm": [0.0, 0.0, 40.0],
        },
        {
            "bbox_mm": [100.0, 100.0, 100.0],
            "displaced_volume_cm3": 785.398163,
            "centre_of_mass_mm": [0.0, 0.0],  # too short
        },
        {
            "bbox_mm": [100.0, 100.0],  # no height
            "displaced_volume_cm3": 785.398163,
            "centre_of_mass_mm": [0.0, 0.0, 40.0],
        },
        {
            "bbox_mm": [100.0, 100.0, 100.0],
            "displaced_volume_cm3": "lots",  # not a number
            "centre_of_mass_mm": [0.0, 0.0, 40.0],
        },
    ],
)
def test_unusable_facts_raise_model_input_error(facts):
    with pytest.raises(waves.ModelInputError):
        waves.stability_from_facts(facts)
