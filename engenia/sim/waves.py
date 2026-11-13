"""Level-1 wave tilt model for an upright axisymmetric hull.

The deterministic stand-in for the Capytaine pitch-RAO case (B1-S2 wires the sim
container). Same inputs always give the same numbers: no solver, no model call,
no clock.

FORMULA (deep-water linear theory, roll response to the wave slope):

    L     = g * T^2 / (2 * pi)          wavelength from the wave period T
    s     = pi * H / L                  wave slope amplitude for height H
    GM    = KB + BM - KG                metacentric height
    omega_n^2 = g * GM / k^2            square of the roll natural frequency
    omega = 2 * pi / T                  wave frequency
    RAO   = omega_n^2 / |omega_n^2 - omega^2|
                                        undamped roll response to the wave slope
    tilt  = atan(s) * RAO               maximum tilt amplitude, degrees

ASSUMES: deep-water Airy waves; pitch and roll are interchangeable for an
         axisymmetric hull; viscous damping is neglected, so beyond roll
         resonance the response flips phase and its amplitude is used.
ASSUMES: stability numbers follow the metacentric_height verifier conventions:
         the submerged volume is displaced_volume_cm3, the waterplane diameter
         is waterplane_diameter_mm when published, else min(bbox_mm[0:2]), and
         KG is centre_of_mass_mm[2] above the hull bottom.
ASSUMES: roll inertia is that of a uniform solid cylinder about a central
         horizontal diameter, k^2 = r^2/4 + h^2/12. The electronics mass sits
         close to the roll axis, so its inertia contribution is neglected.
"""

from __future__ import annotations

import math
from typing import Any

GRAVITY_M_S2 = 9.80665
MAX_TILT_DEG = 90.0  # plausibility bound of the level-1 model
RESONANCE_FRACTION = 0.01  # the undamped model is invalid within this of resonance


class ModelInputError(ValueError):
    """The linear tilt model cannot produce a plausible number for these inputs."""


def _number(facts: dict[str, Any], key: str) -> float:
    value = facts.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ModelInputError(f"simulation input {key} must be a number, got {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise ModelInputError(f"simulation input {key} must be finite, got {number}")
    return number


def _waterplane_diameter_mm(facts: dict[str, Any]) -> float:
    """The waterplane diameter: the published fact, else min(bbox_mm[0:2])."""
    if "waterplane_diameter_mm" in facts:
        return _number(facts, "waterplane_diameter_mm")
    bbox = facts.get("bbox_mm")
    if not isinstance(bbox, (list, tuple)) or len(bbox) < 2:
        raise ModelInputError("simulation input bbox_mm must have at least two values")
    return min(_number({"bbox[0]": bbox[0]}, "bbox[0]"), _number({"bbox[1]": bbox[1]}, "bbox[1]"))


def stability_from_facts(facts: dict[str, Any]) -> dict[str, float]:
    """Stability and roll-inertia inputs from the mechanical interface facts.

    Returns the numbers the model runs on, in mm and seconds. Raises
    :class:`ModelInputError` when a fact is missing or unusable, so the caller
    can fall back to publishing no tilt facts.
    """
    displaced_cm3 = _number(facts, "displaced_volume_cm3")
    diameter_mm = _waterplane_diameter_mm(facts)
    centre = facts.get("centre_of_mass_mm")
    if not isinstance(centre, (list, tuple)) or len(centre) < 3:
        raise ModelInputError("simulation input centre_of_mass_mm must have three values")
    kg_mm = _number({"centre_of_mass_mm[2]": centre[2]}, "centre_of_mass_mm[2]")
    bbox = facts.get("bbox_mm")
    if not isinstance(bbox, (list, tuple)) or len(bbox) < 3:
        raise ModelInputError("simulation input bbox_mm must have three values")
    height_mm = _number({"bbox_mm[2]": bbox[2]}, "bbox_mm[2]")

    if displaced_cm3 <= 0 or diameter_mm <= 0 or height_mm <= 0:
        raise ModelInputError(
            "displaced volume, waterplane diameter and hull height must be positive"
        )

    radius_m = diameter_mm / 2000.0
    volume_m3 = displaced_cm3 / 1e6
    area_m2 = math.pi * radius_m**2
    draft_mm = volume_m3 / area_m2 * 1000.0
    kb_mm = draft_mm / 2.0
    inertia_m4 = math.pi * radius_m**4 / 4.0
    bm_mm = inertia_m4 / volume_m3 * 1000.0
    gm_mm = kb_mm + bm_mm - kg_mm
    gyration2_m2 = radius_m**2 / 4.0 + (height_mm / 1000.0) ** 2 / 12.0
    out = {
        "displaced_volume_cm3": displaced_cm3,
        "waterplane_diameter_mm": diameter_mm,
        "draft_mm": draft_mm,
        "kb_mm": kb_mm,
        "bm_mm": bm_mm,
        "kg_mm": kg_mm,
        "gm_mm": gm_mm,
        "roll_radius_of_gyration_mm": math.sqrt(gyration2_m2) * 1000.0,
    }
    if gm_mm > 0:
        omega_n2 = GRAVITY_M_S2 * (gm_mm / 1000.0) / gyration2_m2
        out["roll_period_s"] = 2.0 * math.pi / math.sqrt(omega_n2)
    return out


def scenario_tilt(
    stability: dict[str, float], wave_height_m: float, wave_period_s: float
) -> dict[str, float]:
    """Tilt response of one scenario against the stability numbers.

    Returns the wave slope, the response amplitude and the maximum tilt in
    degrees. Raises :class:`ModelInputError` when the hull is unstable (no
    natural roll frequency), when the wave sits within
    :data:`RESONANCE_FRACTION` of roll resonance, or when the tilt lands outside
    the plausible 0 to 90 degree range.
    """
    gm_mm = stability["gm_mm"]
    if gm_mm <= 0:
        raise ModelInputError(
            f"hull GM is {gm_mm:.3f} mm: the upright hull is unstable, "
            "the linear tilt model is invalid"
        )
    if wave_period_s <= 0:
        raise ModelInputError(f"wave period must be positive, got {wave_period_s}")
    if wave_height_m < 0:
        raise ModelInputError(f"wave height must not be negative, got {wave_height_m}")

    gyration2_m2 = (stability["roll_radius_of_gyration_mm"] / 1000.0) ** 2
    omega_n2 = GRAVITY_M_S2 * (gm_mm / 1000.0) / gyration2_m2
    omega2 = (2.0 * math.pi / wave_period_s) ** 2
    if abs(omega_n2 - omega2) <= RESONANCE_FRACTION * omega_n2:
        raise ModelInputError(
            f"wave period {wave_period_s} s sits within {RESONANCE_FRACTION:.0%} of the "
            f"{stability['roll_period_s']:.3f} s roll resonance; "
            "the undamped linear model is invalid"
        )
    rao = omega_n2 / abs(omega_n2 - omega2)

    wavelength_m = GRAVITY_M_S2 * wave_period_s**2 / (2.0 * math.pi)
    slope_rad = math.pi * wave_height_m / wavelength_m
    tilt_deg = math.degrees(math.atan(slope_rad) * rao)
    if not math.isfinite(tilt_deg) or not 0.0 <= tilt_deg <= MAX_TILT_DEG:
        raise ModelInputError(
            f"max_tilt_deg {tilt_deg} is not a plausible tilt (0 to {MAX_TILT_DEG} degrees)"
        )
    return {
        "wave_slope_deg": math.degrees(slope_rad),
        "rao": rao,
        "max_tilt_deg": tilt_deg,
    }
