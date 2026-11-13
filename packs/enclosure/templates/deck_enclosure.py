"""Rectangular gasketed deck enclosure for the deck-sensor product pack.

A deck-mounted sensor box (temperature, humidity, vibration) needs a shallow sealed box:
a printed PETG body with flanged feet, a bossed cable entry, and a lid whose O-ring groove
follows the buoy packs' sealing convention. `build_deck_enclosure` returns the body and the
lid as separate single solids, the same contract as packs/buoy/templates/hull.py and
mast_housing.py.

The parameter names match the mechanical stage's proposal fields (outer_diameter_mm carries
the shared "housing width" meaning, as in mast_housing), so a per-project pack hook can
wire this template into src/mechanical/model.py without renaming anything.

NOTE (T4-S3): the mechanical stage still resolves templates from packs/buoy only, so
`engenia run` does not load this file yet. It ships ready for that hook.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from build123d import Align, Box, Cylinder, Pos, Shape

BOTTOM = (Align.CENTER, Align.CENTER, Align.MIN)


@dataclass(frozen=True)
class DeckEnclosureParams:
    outer_diameter_mm: float  # shared proposal field: enclosure width
    height_mm: float
    wall_thickness_mm: float
    battery_bay_mm: tuple[float, float, float]
    board_holes_mm: tuple[tuple[float, float], ...]
    lid_thickness_mm: float = 4.2
    o_ring_groove_width_mm: float = 2.0
    o_ring_groove_depth_mm: float = 1.2
    standoff_height_mm: float = 6.0
    screw_hole_diameter_mm: float = 3.0
    infill_factor: float = 1.0

    def __post_init__(self) -> None:
        values = (
            self.outer_diameter_mm, self.height_mm, self.wall_thickness_mm,
            *self.battery_bay_mm, self.lid_thickness_mm, self.o_ring_groove_width_mm,
            self.o_ring_groove_depth_mm, self.standoff_height_mm,
            self.screw_hole_diameter_mm,
        )
        if len(self.battery_bay_mm) != 3 or any(not isfinite(v) or v <= 0 for v in values):
            raise ValueError("Dimensions must be finite and positive")
        if not 0 < self.infill_factor <= 1:
            raise ValueError("Invalid infill factor")
        x, y, z = self.battery_bay_mm
        w = self.wall_thickness_mm
        if self.outer_diameter_mm < max(x, y) + 8 * w:
            raise ValueError("Enclosure width is too small for the battery bay")
        if self.height_mm < z + self.standoff_height_mm + 3 * w:
            raise ValueError("Enclosure height is too small for the battery bay")
        if self.lid_thickness_mm - self.o_ring_groove_depth_mm < w:
            raise ValueError("Gasket groove leaves insufficient lid material")
        if len(self.board_holes_mm) != 4:
            raise ValueError("Four board holes are required")


def build_deck_enclosure(params: DeckEnclosureParams) -> tuple[Shape, Shape]:
    """Return a hollow rectangular body and a separate grooved lid, both connected solids."""
    p = params
    width, w, h = p.outer_diameter_mm, p.wall_thickness_mm, p.height_mm
    depth = max(p.battery_bay_mm[1] + 8 * w, width * 0.65)
    body = Box(width, depth, h, align=BOTTOM)
    body -= Pos(0, 0, w) * Box(width - 2 * w, depth - 2 * w, h, align=BOTTOM)
    # Four flanged feet with screw holes, outside the sealed cavity.
    foot_radius = 2 * w
    for sx in (-1, 1):
        for sy in (-1, 1):
            x = sx * (width / 2 + w)
            y = sy * (depth / 2 - foot_radius)
            body += Pos(x, y, 0) * Cylinder(foot_radius, w, align=BOTTOM)
            body -= Pos(x, y, 0) * Cylinder(p.screw_hole_diameter_mm / 2, w, align=BOTTOM)
    # A solid boss supports a cable gland through the front wall.
    body += Pos(0, -depth / 2, h / 2) * Box(5 * w, 2 * w, 5 * w)
    body -= Pos(0, -depth / 2, h / 2) * Box(2 * w, 4 * w, 2 * w)
    lid = Pos(0, 0, h) * Box(width, depth, p.lid_thickness_mm, align=BOTTOM)
    outer = Box(width - 2 * w, depth - 2 * w, p.o_ring_groove_depth_mm, align=BOTTOM)
    inner = Box(width - 2 * (w + p.o_ring_groove_width_mm),
                depth - 2 * (w + p.o_ring_groove_width_mm),
                p.o_ring_groove_depth_mm, align=BOTTOM)
    lid -= Pos(0, 0, h) * (outer - inner)
    body.label, lid.label = "body", "lid"
    if any(not part.is_valid or len(part.solids()) != 1 for part in (body, lid)):
        raise ValueError("Enclosure did not produce connected solids")
    return body, lid
