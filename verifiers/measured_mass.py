"""Measured mass verifier.

The mass of the printed prototype, weighed on a scale and recorded by ``engenia measure``
in ``projects/<p>/measurements.yaml`` under ``mass_g`` (grams), is compared with the
requirement limit. The evidence level is ``measured``: the number comes from the prototype,
not from the design or a simulation.
"""

from __future__ import annotations

from engenia.contracts.models import CheckContext, Verdict
from verifiers._common import MissingInput, checked_measured_verdict, guard, read_measurement


@guard
def run(ctx: CheckContext) -> Verdict:
    mass_g = read_measurement(ctx, "mass_g")
    if mass_g <= 0:
        raise MissingInput(f"measured mass must be positive, got {mass_g}")
    return checked_measured_verdict(ctx, mass_g)
