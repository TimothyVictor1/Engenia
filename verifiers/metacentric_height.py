"""Metacentric height verifier for an upright cylinder.

FORMULA:
    GM = KB + BM - KG
    KB = T / 2                         centre of buoyancy, half the draft
    BM = I / V                         metacentric radius
    I  = pi * r^4 / 4                  second moment of a circular waterplane
    T  = V / (pi * r^2)                draft from displaced volume and waterplane area
    KG = centre_of_mass_mm[2]          centre of gravity above the hull bottom

Lengths are converted to cm internally and GM is returned in mm.

ASSUMES: the hull floats as an upright circular cylinder with a flat bottom.
ASSUMES: the waterplane diameter is min(bbox_mm[0], bbox_mm[1]) unless the interface
         supplies waterplane_diameter_mm.
ASSUMES: KG is centre_of_mass_mm[2] (or center_of_mass_mm[2]) measured from the hull
         bottom; the centre of mass is not recomputed from the parts.
ASSUMES: displaced_volume_cm3 is the submerged volume and the cylinder remains upright.
"""

from __future__ import annotations

import math

from engenia.contracts.models import CheckContext, Verdict
from verifiers._common import (
    MissingInput,
    checked_verdict,
    fact_from,
    guard,
    vector_from,
)


def _centre_of_mass_z_mm(ctx: CheckContext) -> float:
    last_error: MissingInput | None = None
    for key in ("centre_of_mass_mm", "center_of_mass_mm"):
        try:
            vector, _ = vector_from(
                ctx, key, ctx.stage, "mechanical", "integration", length=None
            )
        except MissingInput as exc:
            last_error = exc
            continue
        if len(vector) < 3:
            raise MissingInput(f"{key} must contain at least three values")
        return vector[2]
    raise MissingInput("no centre_of_mass_mm or center_of_mass_mm fact") from last_error


@guard
def run(ctx: CheckContext) -> Verdict:
    bbox, _ = vector_from(ctx, "bbox_mm", ctx.stage, "mechanical")
    displaced, _ = fact_from(
        ctx, "displaced_volume_cm3", ctx.stage, "mechanical", "integration"
    )
    if displaced <= 0:
        raise MissingInput(f"displaced_volume_cm3 must be positive, got {displaced}")

    try:
        diameter, _ = fact_from(
            ctx, "waterplane_diameter_mm", ctx.stage, "mechanical", "integration"
        )
    except MissingInput:
        diameter = min(bbox[0], bbox[1])
    if diameter <= 0:
        raise MissingInput(f"waterplane diameter must be positive, got {diameter}")

    radius_cm = diameter / 20.0
    area_cm2 = math.pi * radius_cm**2
    draft_cm = displaced / area_cm2
    kb_cm = draft_cm / 2.0
    inertia_cm4 = math.pi * radius_cm**4 / 4.0
    bm_cm = inertia_cm4 / displaced
    kg_cm = _centre_of_mass_z_mm(ctx) / 10.0
    measured_mm = (kb_cm + bm_cm - kg_cm) * 10.0
    return checked_verdict(ctx, measured_mm)
