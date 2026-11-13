"""Measured waterline verifier.

The waterline of the printed prototype, read off the hull while it floats and recorded by
``engenia measure`` in ``projects/<p>/measurements.yaml`` under ``waterline_mm`` (the height
of the water surface above the hull base, in millimetres), is compared with the requirement
limit. The evidence level is ``measured``: the number comes from the prototype, not from the
design or a simulation.
"""

from __future__ import annotations

from engenia.contracts.models import CheckContext, Verdict
from verifiers._common import MissingInput, checked_measured_verdict, guard, read_measurement


@guard
def run(ctx: CheckContext) -> Verdict:
    waterline_mm = read_measurement(ctx, "waterline_mm")
    if waterline_mm <= 0:
        raise MissingInput(f"measured waterline must be positive, got {waterline_mm}")
    return checked_measured_verdict(ctx, waterline_mm)
