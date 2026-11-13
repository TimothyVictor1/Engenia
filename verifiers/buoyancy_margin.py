"""Buoyancy margin verifier.

FORMULA:
    margin = (displaced_volume_cm3 * water_density - total_mass_g) / total_mass_g

``water_density`` is in g/cm3 and comes from params; the default is 1.005 g/cm3. The
params form ``water_density_kg_m3`` from PROJECT.md is also accepted. ``total_mass_g`` is
an explicit interface fact when one is published, otherwise it is mechanical
``dry_mass_g`` plus electronics ``mass_g``.
"""

from __future__ import annotations

from engenia.contracts.models import CheckContext, Verdict
from verifiers._common import (
    MissingInput,
    checked_verdict,
    fact_from,
    fact_number,
    guard,
    try_read_interface,
    water_density_g_cm3,
)


def _total_mass_g(ctx: CheckContext) -> float:
    try:
        total, _ = fact_from(
            ctx, "total_mass_g", ctx.stage, "mechanical", "electronics", "integration"
        )
        return total
    except MissingInput:
        pass

    mechanical = try_read_interface(ctx, "mechanical")
    electronics = try_read_interface(ctx, "electronics")
    if mechanical is None or electronics is None:
        missing = []
        if mechanical is None:
            missing.append("mechanical")
        if electronics is None:
            missing.append("electronics")
        raise MissingInput(
            "total mass needs mechanical and electronics interfaces; missing "
            + ", ".join(missing)
        )
    return fact_number(mechanical, "dry_mass_g", "mechanical") + fact_number(
        electronics, "mass_g", "electronics"
    )


@guard
def run(ctx: CheckContext) -> Verdict:
    displaced, _ = fact_from(
        ctx, "displaced_volume_cm3", ctx.stage, "mechanical", "integration"
    )
    total_mass = _total_mass_g(ctx)
    if total_mass <= 0:
        raise MissingInput(f"total mass must be positive, got {total_mass}")
    water_density = water_density_g_cm3(ctx)
    measured = (displaced * water_density - total_mass) / total_mass
    return checked_verdict(ctx, measured)
