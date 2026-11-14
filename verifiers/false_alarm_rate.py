"""Firmware false-alarm-rate verifier.

FORMULA:
    measured = false_alarms_per_hour from the firmware interface
    pass when measured satisfies ctx.limit, normally ``limit.max``

A negative alarm count is malformed telemetry rather than a bad design, so it is an ``error``.
Missing facts, or a ``not_run`` fact (replay could not run), give ``error``, never ``pass``.
The verdict is "simulated" when the facts say ``evidence: simulated``, else "calculated".
"""

from __future__ import annotations

from engenia.contracts.models import CheckContext, Verdict
from verifiers._common import evidence_verdict, fact_from, guard, raise_if_not_run


@guard
def run(ctx: CheckContext) -> Verdict:
    raise_if_not_run(ctx, ctx.stage, "firmware")
    measured, source = fact_from(ctx, "false_alarms_per_hour", ctx.stage, "firmware")
    if measured < 0:
        raise ValueError(f"false_alarms_per_hour cannot be negative, got {measured}")
    return evidence_verdict(ctx, measured, source)
