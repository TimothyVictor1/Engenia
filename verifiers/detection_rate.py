"""Firmware detection-rate verifier.

FORMULA:
    measured = detection_rate from the firmware interface
    pass when measured satisfies ctx.limit, normally ``limit.min``

A rate outside [0, 1] is malformed telemetry rather than a bad design, so it is an ``error``.
Missing facts, or a ``not_run`` fact (replay could not run), give ``error``, never ``pass``.
The verdict is "simulated" when the facts say ``evidence: simulated``, else "calculated".
"""

from __future__ import annotations

from engenia.contracts.models import CheckContext, Verdict
from verifiers._common import evidence_verdict, fact_from, guard, raise_if_not_run


@guard
def run(ctx: CheckContext) -> Verdict:
    raise_if_not_run(ctx, ctx.stage, "firmware")
    measured, source = fact_from(ctx, "detection_rate", ctx.stage, "firmware")
    if not 0.0 <= measured <= 1.0:
        raise ValueError(f"detection_rate must be within [0, 1], got {measured}")
    return evidence_verdict(ctx, measured, source)
