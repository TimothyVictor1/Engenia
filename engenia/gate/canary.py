"""G5 canary fixtures: for each verifier, inputs it must judge as a fail.

Each case writes a tiny scratch project (interfaces, contract) and a check limit chosen so the
right answer is "fail". The gate runs the real verifier file on it. A verifier that returns
anything but "fail" (for example one that always passes) fails the gate.

A verifier with no case here cannot be canary-tested, and the gate fails closed for it: add a
case when you add a verifier.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

CONTRACT: dict[str, Any] = {
    "version": 1,
    "budgets": {
        "mechanical": {"mass_g": 300, "outer_envelope_mm": [160, 160, 220]},
        "electronics": {"mass_g": 60, "bay_mm": [70, 40, 22], "energy_mwh_per_day": 180},
        "firmware": {"flash_kb": 512, "ram_kb": 64},
    },
    "interfaces": {"pin_map": {"A": "GPIO1"}, "mounts": {"board_holes_mm": [[3, 3]]}},
    "totals": {"mass_g_max": 400},
}


@dataclass(frozen=True)
class CanaryCase:
    stage: str  # the stage the verifier is run for
    interfaces: dict[str, dict[str, Any]]  # stage -> facts
    limit: dict[str, float]
    params: dict[str, Any] = field(default_factory=dict)


CASES: dict[str, CanaryCase] = {
    "mass_budget": CanaryCase(
        "mechanical", {"mechanical": {"dry_mass_g": 500.0}}, {"max": 400.0}
    ),
    "buoyancy_margin": CanaryCase(
        "mechanical",
        {
            "mechanical": {"dry_mass_g": 200.0, "displaced_volume_cm3": 100.0},
            "electronics": {"mass_g": 50.0},
        },
        {"min": 0.2},
    ),
    "print_bed_fit": CanaryCase(
        "mechanical",
        {"mechanical": {"bbox_mm": [300.0, 300.0, 300.0]}},
        {"equals": 1.0},
        {"bed_mm": [220, 220, 250]},
    ),
    "wall_thickness_min": CanaryCase(
        "mechanical", {"mechanical": {"wall_min_mm": 1.0}}, {"min": 3.0}
    ),
    "metacentric_height": CanaryCase(
        "mechanical",
        {
            "mechanical": {
                "bbox_mm": [100.0, 100.0, 200.0],
                "displaced_volume_cm3": 500.0,
                "centre_of_mass_mm": [0.0, 0.0, 150.0],
            }
        },
        {"min": 5.0},
    ),
    "bay_fit": CanaryCase(
        "electronics",
        {
            "electronics": {"board_mm": [100.0, 100.0, 10.0], "battery_mm": [10.0, 10.0, 10.0]},
            "mechanical": {"battery_bay_mm": [70.0, 40.0, 22.0]},
        },
        {"equals": 1.0},
    ),
    "battery_capacity": CanaryCase(
        "electronics",
        {"electronics": {"battery_wh": 1.0, "avg_power_mw": 1000.0}},
        {"min": 7.0},
    ),
    "erc_clean": CanaryCase("electronics", {"electronics": {"erc_errors": 3.0}}, {"max": 0.0}),
    "power_budget": CanaryCase(
        "electronics", {"electronics": {"avg_power_mw": 100.0}}, {"max": 25.0}
    ),
}
