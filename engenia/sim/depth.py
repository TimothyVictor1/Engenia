"""Water depth and pressure helpers shared by the structure solver and pack proposers.

A requirement states its depth in its statement ("... at 50 m depth"). This module has no heavy
imports, so a pack template can use it without loading the finite element stack.
"""

from __future__ import annotations

import re

GRAVITY_M_S2 = 9.80665
_DEPTH = re.compile(r"\bat\s+([0-9]+(?:\.[0-9]+)?)\s*m\s+depth\b", re.IGNORECASE)


def parse_depth_m(statement: str) -> float | None:
    """The depth in metres a statement names ("at 50 m depth"), or None."""
    match = _DEPTH.search(statement or "")
    return float(match.group(1)) if match else None


def pressure_mpa(density_kg_m3: float, depth_m: float) -> float:
    """External hydrostatic gauge pressure, rho * g * depth, in MPa."""
    return density_kg_m3 * GRAVITY_M_S2 * depth_m / 1e6
