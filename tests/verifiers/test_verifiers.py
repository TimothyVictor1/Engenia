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
