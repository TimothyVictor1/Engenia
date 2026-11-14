from __future__ import annotations

import pytest

from engenia.contracts.models import Limit
from verifiers import load


def test_mass_budget_stage_pass(write_interface, write_contract, make_ctx):
    write_contract(mechanical_mass=300, electronics_mass=60)
    write_interface("mechanical", {"dry_mass_g": 250})
    verdict = load("mass_budget")(
        make_ctx(stage="mechanical", check="mass_budget", limit=Limit(max=400))
    )
    assert verdict.result == "pass"
    assert verdict.measured == 250
    assert verdict.limit.max == 300


def test_mass_budget_stage_fail(write_interface, write_contract, make_ctx):
    write_contract(mechanical_mass=300, electronics_mass=60)
    write_interface("mechanical", {"dry_mass_g": 350})
    verdict = load("mass_budget")(
        make_ctx(stage="mechanical", check="mass_budget", limit=Limit(max=400))
    )
    assert verdict.result == "fail"
    assert verdict.measured == 350


def test_mass_budget_missing_interface(write_contract, make_ctx):
    write_contract()
    verdict = load("mass_budget")(make_ctx(stage="mechanical", check="mass_budget"))
    assert verdict.result == "error"
    assert "missing" in (verdict.message or "").lower()


def test_mass_budget_total_sums_both_stages(write_interface, make_ctx):
    write_interface("mechanical", {"dry_mass_g": 250})
    write_interface("electronics", {"mass_g": 100})
    verdict = load("mass_budget")(
        make_ctx(
            stage="integration",
            check="mass_budget",
            requirement="R-001",
            limit=Limit(max=400),
        )
    )
    assert verdict.result == "pass"
    assert verdict.measured == 350


def test_buoyancy_margin_pass(write_interface, make_ctx):
    write_interface("mechanical", {"dry_mass_g": 300, "displaced_volume_cm3": 500})
    write_interface("electronics", {"mass_g": 100})
    verdict = load("buoyancy_margin")(
        make_ctx(
            stage="mechanical",
            check="buoyancy_margin",
            requirement="R-005",
            limit=Limit(min=0.2),
        )
    )
    assert verdict.result == "pass"
    assert verdict.measured == pytest.approx(0.25625)


def test_buoyancy_margin_fail(write_interface, make_ctx):
    write_interface("mechanical", {"dry_mass_g": 300, "displaced_volume_cm3": 400})
    write_interface("electronics", {"mass_g": 100})
    verdict = load("buoyancy_margin")(
        make_ctx(
            stage="mechanical",
            check="buoyancy_margin",
            requirement="R-005",
            limit=Limit(min=0.2),
        )
    )
    assert verdict.result == "fail"
    assert verdict.measured == pytest.approx(0.005)


def test_buoyancy_margin_missing_interface(make_ctx):
    verdict = load("buoyancy_margin")(
        make_ctx(
            stage="mechanical",
            check="buoyancy_margin",
            requirement="R-005",
            limit=Limit(min=0.2),
        )
    )
    assert verdict.result == "error"


def test_buoyancy_margin_accepts_kg_m3_param(write_interface, make_ctx):
    write_interface("mechanical", {"dry_mass_g": 300, "displaced_volume_cm3": 500})
    write_interface("electronics", {"mass_g": 100})
    verdict = load("buoyancy_margin")(
        make_ctx(
            stage="mechanical",
            check="buoyancy_margin",
            requirement="R-005",
            params={"water_density_kg_m3": 1000},
            limit=Limit(min=0.2),
        )
    )
    assert verdict.result == "pass"
    assert verdict.measured == pytest.approx(0.25)


def test_print_bed_fit_pass_with_rotation(write_interface, make_ctx):
    write_interface("mechanical", {"bbox_mm": [250, 200, 100]})
    verdict = load("print_bed_fit")(
        make_ctx(
            stage="mechanical",
            check="print_bed_fit",
            requirement="R-002",
            params={"bed_mm": [220, 220, 250]},
            limit=Limit(equals=1),
        )
    )
    assert verdict.result == "pass"
    assert verdict.measured == 1.0


def test_print_bed_fit_fail(write_interface, make_ctx):
    write_interface("mechanical", {"bbox_mm": [300, 100, 100]})
    verdict = load("print_bed_fit")(
        make_ctx(
            stage="mechanical",
            check="print_bed_fit",
            requirement="R-002",
            params={"bed_mm": [220, 220, 250]},
            limit=Limit(equals=1),
        )
    )
    assert verdict.result == "fail"
    assert verdict.measured == 0.0


def test_print_bed_fit_missing_interface(make_ctx):
    verdict = load("print_bed_fit")(
        make_ctx(
            stage="mechanical",
            check="print_bed_fit",
            requirement="R-002",
            params={"bed_mm": [220, 220, 250]},
            limit=Limit(equals=1),
        )
    )
    assert verdict.result == "error"


def test_wall_thickness_min_pass(write_interface, make_ctx):
    write_interface("mechanical", {"wall_min_mm": 3.0})
    verdict = load("wall_thickness_min")(
        make_ctx(
            stage="mechanical",
            check="wall_thickness_min",
            requirement="R-004",
            limit=Limit(min=3),
        )
    )
    assert verdict.result == "pass"
    assert verdict.measured == 3.0


def test_wall_thickness_min_fail(write_interface, make_ctx):
    write_interface("mechanical", {"wall_min_mm": 2.0})
    verdict = load("wall_thickness_min")(
        make_ctx(
            stage="mechanical",
            check="wall_thickness_min",
            requirement="R-004",
            limit=Limit(min=3),
        )
    )
    assert verdict.result == "fail"
    assert verdict.measured == 2.0


def test_wall_thickness_min_missing_fact(write_interface, make_ctx):
    write_interface("mechanical", {"bbox_mm": [100, 100, 200]})
    verdict = load("wall_thickness_min")(
        make_ctx(
            stage="mechanical",
            check="wall_thickness_min",
            requirement="R-004",
            limit=Limit(min=3),
        )
    )
    assert verdict.result == "error"


def test_metacentric_height_pass(write_interface, make_ctx):
    write_interface(
        "mechanical",
        {
            "bbox_mm": [100, 100, 200],
            "displaced_volume_cm3": 1000,
            "centre_of_mass_mm": [0, 0, 50],
        },
    )
    verdict = load("metacentric_height")(
        make_ctx(
            stage="mechanical",
            check="metacentric_height",
            requirement="R-006",
            limit=Limit(min=5),
        )
    )
    assert verdict.result == "pass"
    assert verdict.measured > 5


def test_metacentric_height_fail(write_interface, make_ctx):
    write_interface(
        "mechanical",
        {
            "bbox_mm": [100, 100, 200],
            "displaced_volume_cm3": 1000,
            "centre_of_mass_mm": [0, 0, 200],
        },
    )
    verdict = load("metacentric_height")(
        make_ctx(
            stage="mechanical",
            check="metacentric_height",
            requirement="R-006",
            limit=Limit(min=5),
        )
    )
    assert verdict.result == "fail"
    assert verdict.measured < 0


def test_metacentric_height_missing_interface(make_ctx):
    verdict = load("metacentric_height")(
        make_ctx(
            stage="mechanical",
            check="metacentric_height",
            requirement="R-006",
            limit=Limit(min=5),
        )
    )
    assert verdict.result == "error"


def test_power_budget_pass(write_interface, make_ctx):
    write_interface("electronics", {"avg_power_mw": 20})
    verdict = load("power_budget")(
        make_ctx(
            stage="electronics",
            check="power_budget",
            requirement="R-007",
            limit=Limit(max=25),
        )
    )
    assert verdict.result == "pass"
    assert verdict.measured == 20


def test_power_budget_fail(write_interface, make_ctx):
    write_interface("electronics", {"avg_power_mw": 30})
    verdict = load("power_budget")(
        make_ctx(
            stage="electronics",
            check="power_budget",
            requirement="R-007",
            limit=Limit(max=25),
        )
    )
    assert verdict.result == "fail"
    assert verdict.measured == 30


def test_power_budget_missing_fact(write_interface, make_ctx):
    write_interface("electronics", {"mass_g": 50})
    verdict = load("power_budget")(
        make_ctx(
            stage="electronics",
            check="power_budget",
            requirement="R-007",
            limit=Limit(max=25),
        )
    )
    assert verdict.result == "error"


def test_battery_capacity_pass(write_interface, make_ctx):
    write_interface("electronics", {"battery_wh": 10, "avg_power_mw": 20})
    verdict = load("battery_capacity")(
        make_ctx(
            stage="electronics",
            check="battery_capacity",
            requirement="R-003",
            limit=Limit(min=7),
        )
    )
    assert verdict.result == "pass"
    assert verdict.measured == pytest.approx(10 * 1000 / 20 / 24 * 0.8)


def test_battery_capacity_fail(write_interface, make_ctx):
    write_interface("electronics", {"battery_wh": 1, "avg_power_mw": 100})
    verdict = load("battery_capacity")(
        make_ctx(
            stage="electronics",
            check="battery_capacity",
            requirement="R-003",
            limit=Limit(min=7),
        )
    )
    assert verdict.result == "fail"
    assert verdict.measured == pytest.approx(1 * 1000 / 100 / 24 * 0.8)


def test_battery_capacity_missing_interface(make_ctx):
    verdict = load("battery_capacity")(
        make_ctx(
            stage="electronics",
            check="battery_capacity",
            requirement="R-003",
            limit=Limit(min=7),
        )
    )
    assert verdict.result == "error"


def test_erc_clean_pass(write_interface, make_ctx):
    write_interface("electronics", {"erc_errors": 0})
    verdict = load("erc_clean")(
        make_ctx(
            stage="electronics",
            check="erc_clean",
            requirement="R-008",
            limit=Limit(equals=0),
        )
    )
    assert verdict.result == "pass"
    assert verdict.measured == 0


def test_erc_clean_fail(write_interface, make_ctx):
    write_interface("electronics", {"erc_errors": 2})
    verdict = load("erc_clean")(
        make_ctx(
            stage="electronics",
            check="erc_clean",
            requirement="R-008",
            limit=Limit(equals=0),
        )
    )
    assert verdict.result == "fail"
    assert verdict.measured == 2


def test_erc_clean_missing_interface(make_ctx):
    verdict = load("erc_clean")(
        make_ctx(
            stage="electronics",
            check="erc_clean",
            requirement="R-008",
            limit=Limit(equals=0),
        )
    )
    assert verdict.result == "error"


def test_bay_fit_pass(write_interface, make_ctx):
    write_interface(
        "electronics",
        {"board_mm": [60, 35, 15], "battery_mm": [50, 30, 20]},
    )
    write_interface("mechanical", {"battery_bay_mm": [70, 40, 22]})
    verdict = load("bay_fit")(
        make_ctx(
            stage="electronics",
            check="bay_fit",
            requirement="R-008",
            limit=Limit(equals=1),
        )
    )
    assert verdict.result == "pass"
    assert verdict.measured == 1.0


def test_bay_fit_fail(write_interface, make_ctx):
    write_interface(
        "electronics",
        {"board_mm": [80, 35, 15], "battery_mm": [50, 30, 20]},
    )
    write_interface("mechanical", {"battery_bay_mm": [70, 40, 22]})
    verdict = load("bay_fit")(
        make_ctx(
            stage="electronics",
            check="bay_fit",
            requirement="R-008",
            limit=Limit(equals=1),
        )
    )
    assert verdict.result == "fail"
    assert verdict.measured == 0.0


def test_bay_fit_missing_battery_size(write_interface, make_ctx):
    write_interface("electronics", {"board_mm": [60, 35, 15]})
    write_interface("mechanical", {"battery_bay_mm": [70, 40, 22]})
    verdict = load("bay_fit")(
        make_ctx(
            stage="electronics",
            check="bay_fit",
            requirement="R-008",
            limit=Limit(equals=1),
        )
    )
    assert verdict.result == "error"


def test_detection_rate_pass(write_interface, make_ctx):
    write_interface("firmware", {"detection_rate": 0.92})
    verdict = load("detection_rate")(
        make_ctx(
            stage="firmware",
            check="detection_rate",
            requirement="R-009",
            limit=Limit(min=0.9),
        )
    )
    assert verdict.result == "pass"
    assert verdict.measured == 0.92


def test_detection_rate_fail(write_interface, make_ctx):
    write_interface("firmware", {"detection_rate": 0.5})
    verdict = load("detection_rate")(
        make_ctx(
            stage="firmware",
            check="detection_rate",
            requirement="R-009",
            limit=Limit(min=0.9),
        )
    )
    assert verdict.result == "fail"
    assert verdict.measured == 0.5
    assert "0.5" in (verdict.message or "")


def test_detection_rate_missing_fact(write_interface, make_ctx):
    write_interface("firmware", {"false_alarms_per_hour": 1.0})
    verdict = load("detection_rate")(
        make_ctx(
            stage="firmware",
            check="detection_rate",
            requirement="R-009",
            limit=Limit(min=0.9),
        )
    )
    assert verdict.result == "error"
    assert "no 'detection_rate' fact" in (verdict.message or "")


@pytest.mark.parametrize("rate", [-0.1, 1.4])
def test_detection_rate_out_of_range_is_an_error(write_interface, make_ctx, rate):
    write_interface("firmware", {"detection_rate": rate})
    verdict = load("detection_rate")(
        make_ctx(
            stage="firmware",
            check="detection_rate",
            requirement="R-009",
            limit=Limit(min=0.9),
        )
    )
    assert verdict.result == "error"
    assert "within [0, 1]" in (verdict.message or "")


def test_false_alarm_rate_pass(write_interface, make_ctx):
    write_interface("firmware", {"false_alarms_per_hour": 1.5})
    verdict = load("false_alarm_rate")(
        make_ctx(
            stage="firmware",
            check="false_alarm_rate",
            requirement="R-010",
            limit=Limit(max=2),
        )
    )
    assert verdict.result == "pass"
    assert verdict.measured == 1.5


def test_false_alarm_rate_fail(write_interface, make_ctx):
    write_interface("firmware", {"false_alarms_per_hour": 3.0})
    verdict = load("false_alarm_rate")(
        make_ctx(
            stage="firmware",
            check="false_alarm_rate",
            requirement="R-010",
            limit=Limit(max=2),
        )
    )
    assert verdict.result == "fail"
    assert verdict.measured == 3.0


def test_false_alarm_rate_missing_fact(make_ctx):
    verdict = load("false_alarm_rate")(
        make_ctx(
            stage="firmware",
            check="false_alarm_rate",
            requirement="R-010",
            limit=Limit(max=2),
        )
    )
    assert verdict.result == "error"


def test_false_alarm_rate_negative_is_an_error(write_interface, make_ctx):
    write_interface("firmware", {"false_alarms_per_hour": -1.0})
    verdict = load("false_alarm_rate")(
        make_ctx(
            stage="firmware",
            check="false_alarm_rate",
            requirement="R-010",
            limit=Limit(max=2),
        )
    )
    assert verdict.result == "error"
    assert "cannot be negative" in (verdict.message or "")


def test_wave_tilt_pass_on_fallback_scenarios(write_interface, make_ctx):
    write_interface(
        "simulation",
        {
            "wave_tilt": {
                "calm_harbour": {"wave_height_m": 0.5, "max_tilt_deg": 8.0},
                "baltic_winter": {"wave_height_m": 1.5, "max_tilt_deg": 20.0},
            }
        },
    )
    verdict = load("wave_tilt")(
        make_ctx(
            stage="simulation",
            check="wave_tilt",
            requirement="R-011",
            limit=Limit(max=30),
        )
    )
    assert verdict.result == "pass"
    assert verdict.measured == 20.0  # the worse of the two judged scenarios
    assert verdict.evidence_level == "simulated"


def test_wave_tilt_fail_on_fallback_scenarios(write_interface, make_ctx):
    write_interface(
        "simulation",
        {
            "wave_tilt": {
                "calm_harbour": {"wave_height_m": 0.5, "max_tilt_deg": 8.0},
                "baltic_winter": {"wave_height_m": 1.5, "max_tilt_deg": 45.0},
            }
        },
    )
    verdict = load("wave_tilt")(
        make_ctx(
            stage="simulation",
            check="wave_tilt",
            requirement="R-011",
            limit=Limit(max=30),
        )
    )
    assert verdict.result == "fail"
    assert verdict.measured == 45.0
    assert verdict.evidence_level == "simulated"


def test_wave_tilt_condition_selects_matching_scenarios(write_interface, make_ctx):
    """With a wave-height condition, harsher scenarios are not judged (and cannot fail it)."""
    write_interface(
        "simulation",
        {
            "wave_tilt": {
                "calm_harbour": {"wave_height_m": 0.5, "max_tilt_deg": 10.0},
                "baltic_winter": {"wave_height_m": 1.5, "max_tilt_deg": 20.0},
                "storm": {"wave_height_m": 4.0, "max_tilt_deg": 50.0},
            }
        },
    )
    verdict = load("wave_tilt")(
        make_ctx(
            stage="simulation",
            check="wave_tilt",
            requirement="R-011",
            params={"wave_height_m": 0.5},
            limit=Limit(max=30),
        )
    )
    assert verdict.result == "pass"
    assert verdict.measured == 10.0


def test_wave_tilt_condition_with_no_matching_scenario(write_interface, make_ctx):
    write_interface(
        "simulation",
        {"wave_tilt": {"storm": {"wave_height_m": 4.0, "max_tilt_deg": 50.0}}},
    )
    verdict = load("wave_tilt")(
        make_ctx(
            stage="simulation",
            check="wave_tilt",
            requirement="R-011",
            params={"wave_height_m": 0.5},
            limit=Limit(max=30),
        )
    )
    assert verdict.result == "error"
    assert "no scenario with wave height at most 0.5" in (verdict.message or "")


def test_wave_tilt_condition_requires_scenario_wave_height(write_interface, make_ctx):
    write_interface(
        "simulation",
        {
            "wave_tilt": {
                "calm_harbour": {"max_tilt_deg": 10.0},
                "baltic_winter": {"wave_height_m": 1.5, "max_tilt_deg": 20.0},
            }
        },
    )
    verdict = load("wave_tilt")(
        make_ctx(
            stage="simulation",
            check="wave_tilt",
            requirement="R-011",
            params={"wave_height_m": 0.5},
            limit=Limit(max=30),
        )
    )
    assert verdict.result == "error"
    assert "calm_harbour" in (verdict.message or "") and "wave_height_m" in (
        verdict.message or ""
    )


def test_wave_tilt_missing_wave_tilt_fact(write_interface, make_ctx):
    write_interface("simulation", {"safety_factor": 2.5})
    verdict = load("wave_tilt")(
        make_ctx(
            stage="simulation",
            check="wave_tilt",
            requirement="R-011",
            limit=Limit(max=30),
        )
    )
    assert verdict.result == "error"
    assert "no 'wave_tilt' fact" in (verdict.message or "")


def test_wave_tilt_missing_fallback_scenario(write_interface, make_ctx):
    write_interface(
        "simulation",
        {"wave_tilt": {"calm_harbour": {"wave_height_m": 0.5, "max_tilt_deg": 8.0}}},
    )
    verdict = load("wave_tilt")(
        make_ctx(
            stage="simulation",
            check="wave_tilt",
            requirement="R-011",
            limit=Limit(max=30),
        )
    )
    assert verdict.result == "error"
    assert "baltic_winter" in (verdict.message or "")


def test_wave_tilt_scenario_without_tilt(write_interface, make_ctx):
    write_interface(
        "simulation",
        {
            "wave_tilt": {
                "calm_harbour": {"wave_height_m": 0.5, "max_tilt_deg": 8.0},
                "baltic_winter": {"wave_height_m": 1.5},
            }
        },
    )
    verdict = load("wave_tilt")(
        make_ctx(
            stage="simulation",
            check="wave_tilt",
            requirement="R-011",
            limit=Limit(max=30),
        )
    )
    assert verdict.result == "error"
    assert "baltic_winter" in (verdict.message or "") and "max_tilt_deg" in (
        verdict.message or ""
    )


def test_stress_safety_factor_pass(write_interface, make_ctx):
    write_interface("simulation", {"safety_factor": 2.5})
    verdict = load("stress_safety_factor")(
        make_ctx(
            stage="simulation",
            check="stress_safety_factor",
            requirement="R-012",
            limit=Limit(min=2),
        )
    )
    assert verdict.result == "pass"
    assert verdict.measured == 2.5
    assert verdict.evidence_level == "simulated"


def test_stress_safety_factor_fail(write_interface, make_ctx):
    write_interface("simulation", {"safety_factor": 1.2})
    verdict = load("stress_safety_factor")(
        make_ctx(
            stage="simulation",
            check="stress_safety_factor",
            requirement="R-012",
            limit=Limit(min=2),
        )
    )
    assert verdict.result == "fail"
    assert verdict.measured == 1.2
    assert verdict.evidence_level == "simulated"


def test_stress_safety_factor_missing_fact(make_ctx):
    verdict = load("stress_safety_factor")(
        make_ctx(
            stage="simulation",
            check="stress_safety_factor",
            requirement="R-012",
            limit=Limit(min=2),
        )
    )
    assert verdict.result == "error"


def test_stress_safety_factor_non_positive_is_an_error(write_interface, make_ctx):
    write_interface("simulation", {"safety_factor": 0.0})
    verdict = load("stress_safety_factor")(
        make_ctx(
            stage="simulation",
            check="stress_safety_factor",
            requirement="R-012",
            limit=Limit(min=2),
        )
    )
    assert verdict.result == "error"
    assert "must be positive" in (verdict.message or "")


# --- measured_* ---------------------------------------------------------------------------------


def measured_ctx(make_ctx, check, limit):
    return make_ctx(stage="mechanical", check=check, requirement="R-101", limit=limit)


def test_measured_mass_pass(write_measurements, make_ctx):
    write_measurements({"mass_g": 352.1, "waterline_mm": 48.2, "date": "2026-11-13"})
    verdict = load("measured_mass")(measured_ctx(make_ctx, "measured_mass", Limit(max=400)))
    assert verdict.result == "pass"
    assert verdict.measured == 352.1
    assert verdict.evidence_level == "measured"
    assert verdict.message is None


def test_measured_mass_fail(write_measurements, make_ctx):
    write_measurements({"mass_g": 430.0})
    verdict = load("measured_mass")(measured_ctx(make_ctx, "measured_mass", Limit(max=400)))
    assert verdict.result == "fail"
    assert verdict.measured == 430.0
    assert verdict.evidence_level == "measured"
    assert "above max 400" in (verdict.message or "")


def test_measured_mass_min_limit(write_measurements, make_ctx):
    write_measurements({"mass_g": 280.0})
    verdict = load("measured_mass")(measured_ctx(make_ctx, "measured_mass", Limit(min=300)))
    assert verdict.result == "fail"
    assert "below min 300" in (verdict.message or "")


@pytest.mark.parametrize("mass", [0.0, -3.0, "heavy"])
def test_measured_mass_unusable_value_is_an_error(write_measurements, make_ctx, mass):
    write_measurements({"mass_g": mass})
    verdict = load("measured_mass")(measured_ctx(make_ctx, "measured_mass", Limit(max=400)))
    assert verdict.result == "error"


def test_measured_mass_without_measurements_file(make_ctx):
    verdict = load("measured_mass")(measured_ctx(make_ctx, "measured_mass", Limit(max=400)))
    assert verdict.result == "error"
    assert "missing measurements.yaml" in (verdict.message or "")


def test_measured_mass_without_the_key(write_measurements, make_ctx):
    write_measurements({"waterline_mm": 48.2})
    verdict = load("measured_mass")(measured_ctx(make_ctx, "measured_mass", Limit(max=400)))
    assert verdict.result == "error"
    assert "no 'mass_g' measurement" in (verdict.message or "")


def test_measured_waterline_pass(write_measurements, make_ctx):
    write_measurements({"waterline_mm": 48.2, "date": "2026-11-13"})
    verdict = load("measured_waterline")(
        measured_ctx(make_ctx, "measured_waterline", Limit(max=60))
    )
    assert verdict.result == "pass"
    assert verdict.measured == 48.2
    assert verdict.evidence_level == "measured"


def test_measured_waterline_fail(write_measurements, make_ctx):
    write_measurements({"waterline_mm": 95.0})
    verdict = load("measured_waterline")(
        measured_ctx(make_ctx, "measured_waterline", Limit(max=60))
    )
    assert verdict.result == "fail"
    assert verdict.measured == 95.0
    assert verdict.evidence_level == "measured"


def test_measured_waterline_without_measurements(make_ctx):
    verdict = load("measured_waterline")(
        measured_ctx(make_ctx, "measured_waterline", Limit(max=60))
    )
    assert verdict.result == "error"


def test_measured_tilt_pass(write_measurements, make_ctx):
    write_measurements({"tilt_deg": 6.5, "date": "2026-11-13"})
    verdict = load("measured_tilt")(measured_ctx(make_ctx, "measured_tilt", Limit(max=30)))
    assert verdict.result == "pass"
    assert verdict.measured == 6.5
    assert verdict.evidence_level == "measured"


def test_measured_tilt_zero_is_a_valid_reading(write_measurements, make_ctx):
    write_measurements({"tilt_deg": 0.0})
    verdict = load("measured_tilt")(measured_ctx(make_ctx, "measured_tilt", Limit(max=30)))
    assert verdict.result == "pass"
    assert verdict.measured == 0.0


def test_measured_tilt_fail(write_measurements, make_ctx):
    write_measurements({"tilt_deg": 42.0})
    verdict = load("measured_tilt")(measured_ctx(make_ctx, "measured_tilt", Limit(max=30)))
    assert verdict.result == "fail"
    assert verdict.measured == 42.0
    assert verdict.evidence_level == "measured"


def test_measured_tilt_negative_is_an_error(write_measurements, make_ctx):
    write_measurements({"tilt_deg": -2.0})
    verdict = load("measured_tilt")(measured_ctx(make_ctx, "measured_tilt", Limit(max=30)))
    assert verdict.result == "error"
    assert "cannot be negative" in (verdict.message or "")


def test_measured_tilt_without_a_tilt_measurement_is_an_error(write_measurements, make_ctx):
    """`engenia measure` without --tilt-deg records no tilt_deg key; the check errors."""
    write_measurements({"mass_g": 352.1})
    verdict = load("measured_tilt")(measured_ctx(make_ctx, "measured_tilt", Limit(max=30)))
    assert verdict.result == "error"
    assert "no 'tilt_deg' measurement" in (verdict.message or "")
