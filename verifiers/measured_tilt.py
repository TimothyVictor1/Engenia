"""Measured tilt verifier.

The tilt of the printed prototype, read while it settles upright and recorded by
``engenia measure`` in ``projects/<p>/measurements.yaml`` under ``tilt_deg`` (degrees from
vertical), is compared with the requirement limit. The evidence level is ``measured``: the
number comes from the prototype, not from the design or a simulation. A measurement of zero
is a valid reading; the key must be present, so a run without ``--tilt-deg`` is an error.
"""

from __future__ import annotations

from engenia.contracts.models import CheckContext, Verdict
from verifiers._common import MissingInput, checked_measured_verdict, guard, read_measurement


@guard
def run(ctx: CheckContext) -> Verdict:
    tilt_deg = read_measurement(ctx, "tilt_deg")
    if tilt_deg < 0:
        raise MissingInput(f"measured tilt cannot be negative, got {tilt_deg}")
    return checked_measured_verdict(ctx, tilt_deg)
