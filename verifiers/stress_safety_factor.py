"""Stress safety-factor verifier, judged on simulated evidence.

FORMULA:
    measured = safety_factor from the simulation interface
    pass when measured satisfies ctx.limit, normally ``limit.min``

The number comes from the structure simulation, so the verdict's evidence level is "simulated".
A safety factor that is not positive is malformed output rather than a weak design, so it is an
``error``. Missing facts give ``error`` too, never ``pass``.

ASSUMES: the simulation interface publishes a single overall ``safety_factor`` fact for the
         load case the requirement names (for harbour-listener R-012, 3 m depth).
"""

from __future__ import annotations

from engenia.contracts.models import CheckContext, Verdict
from verifiers._common import checked_simulated_verdict, fact_from, guard


@guard
def run(ctx: CheckContext) -> Verdict:
    measured, _ = fact_from(ctx, "safety_factor", ctx.stage, "simulation")
    if measured <= 0:
        raise ValueError(f"safety_factor must be positive, got {measured}")
    return checked_simulated_verdict(ctx, measured)
