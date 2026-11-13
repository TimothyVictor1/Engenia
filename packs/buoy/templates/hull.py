"""Buoy in mm; lid returned assembled. Hole XY origin is the bay's lower-left.

The battery occupies the clear well below the board. Blind standoffs and their
supporting tabs start above that well. The lid has an underside O-ring groove.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import hypot, isfinite

from build123d import Align, Box, Cylinder, Pos, Shape

BOTTOM = (Align.CENTER, Align.CENTER, Align.MIN)


@dataclass(frozen=True)
class HullParams:
    outer_diameter_mm: float
    height_mm: float  # hull alone; assembled height includes lid_thickness_mm
    wall_thickness_mm: float
    battery_bay_mm: tuple[float, float, float]
    board_holes_mm: tuple[tuple[float, float], ...]
    lid_thickness_mm: float = 3.6
    o_ring_groove_width_mm: float = 2.0
    o_ring_groove_depth_mm: float = 1.2
    standoff_height_mm: float = 6.0
    screw_hole_diameter_mm: float = 3.0
    infill_factor: float = 1.0

    def __post_init__(self) -> None:
        positive = (
            self.outer_diameter_mm, self.height_mm, self.wall_thickness_mm,
            *self.battery_bay_mm, self.lid_thickness_mm,
            self.o_ring_groove_width_mm, self.o_ring_groove_depth_mm,
            self.standoff_height_mm, self.screw_hole_diameter_mm,
        )
        if len(self.battery_bay_mm) != 3 or any(not isfinite(v) or v <= 0 for v in positive):
            raise ValueError("Dimensions must be finite, positive mm values; bay must have XYZ")
        if not isfinite(self.infill_factor) or not 0 < self.infill_factor <= 1:
            raise ValueError("infill_factor must be in (0, 1]")
        w = self.wall_thickness_mm
        if self.lid_thickness_mm - self.o_ring_groove_depth_mm < w - 1e-9:
            raise ValueError("O-ring groove must leave at least one wall of lid material")
        if self.standoff_height_mm <= w:
            raise ValueError("Standoff height must exceed its blind-hole floor thickness")
        x, y, z = self.battery_bay_mm
        rim = 2 * w + self.o_ring_groove_width_mm
        if hypot(x / 2 + w, y / 2 + w) >= self.outer_diameter_mm / 2 - rim:
            raise ValueError("Battery bay does not fit inside the sealing rim")
        if w + z + self.standoff_height_mm >= self.height_mm - w:
            raise ValueError("Battery bay and board standoffs do not fit below the lid")
        if len(self.board_holes_mm) != 4 or len(set(map(tuple, self.board_holes_mm))) != 4:
            raise ValueError("Exactly four distinct board mounting holes are required")
        radius = self.screw_hole_diameter_mm / 2 + w
        for hx, hy in self.board_holes_mm:
            if not (isfinite(hx) and isfinite(hy) and 0 <= hx <= x and 0 <= hy <= y):
                raise ValueError("Board hole must lie within the bay XY footprint")
            if hypot(hx - x / 2, hy - y / 2) + radius >= self.outer_diameter_mm / 2 - rim:
                raise ValueError("Board standoff does not fit inside the sealing rim")


def build_hull(params: HullParams) -> tuple[Shape, Shape]:
    """Return connected, watertight hull and grooved lid solids (no seal hardware)."""
    p = params
    r, w, h = p.outer_diameter_mm / 2, p.wall_thickness_mm, p.height_mm
    x, y, z = p.battery_bay_mm
    hull = Cylinder(r, h, align=BOTTOM) - Pos(0, 0, w) * Cylinder(r - w, h, align=BOTTOM)

    # Wider rim supports the seal, with w of radial material beside the groove.
    rim_width = 2 * w + p.o_ring_groove_width_mm
    rim = Cylinder(r, w, align=BOTTOM) - Cylinder(r - rim_width, w, align=BOTTOM)
    hull += Pos(0, 0, h - w) * rim

    bay = Box(x + 2 * w, y + 2 * w, z + w, align=BOTTOM)
    bay -= Box(x, y, z + w, align=BOTTOM)
    hull += Pos(0, 0, w) * bay

    boss_radius = p.screw_hole_diameter_mm / 2 + w
    for hx, hy in p.board_holes_mm:
        cx, cy = hx - x / 2, hy - y / 2
        # Join each boss to the nearest well wall, including non-corner mounts.
        if x / 2 - abs(cx) <= y / 2 - abs(cy):
            edge = (1 if cx >= 0 else -1) * (x / 2 + w / 2)
            tab = Pos((edge + cx) / 2, cy, w + z) * Box(
                abs(edge - cx) + w, 2 * boss_radius, w, align=BOTTOM
            )
        else:
            edge = (1 if cy >= 0 else -1) * (y / 2 + w / 2)
            tab = Pos(cx, (edge + cy) / 2, w + z) * Box(
                2 * boss_radius, abs(edge - cy) + w, w, align=BOTTOM
            )
        hull += tab
        hull += Pos(cx, cy, w + z) * Cylinder(boss_radius, p.standoff_height_mm, align=BOTTOM)
        hull -= Pos(cx, cy, z + 2 * w) * Cylinder(
            p.screw_hole_diameter_mm / 2, p.standoff_height_mm, align=BOTTOM
        )

    lid = Cylinder(r, p.lid_thickness_mm, align=BOTTOM)
    groove = Cylinder(r - w, p.o_ring_groove_depth_mm, align=BOTTOM)
    groove -= Cylinder(r - w - p.o_ring_groove_width_mm, p.o_ring_groove_depth_mm, align=BOTTOM)
    lid = Pos(0, 0, h) * (lid - groove)
    hull.label, lid.label = "hull", "lid"
    if any(not part.is_valid or len(part.solids()) != 1 for part in (hull, lid)):
        raise ValueError("Template did not produce two valid connected solids")
    return hull, lid
