"""Firmware false-alarm-rate verifier.

FORMULA:
    measured = false_alarms_per_hour from the firmware interface
    pass when measured satisfies ctx.limit, normally ``limit.max``

A negative alarm count is malformed telemetry rather than a bad design, so it is an ``error``.
Missing facts give ``error`` too, never ``pass``.
"""

from __future__ import annotations

from engenia.contracts.models import CheckContext, Verdict
from verifiers._common import checked_verdict, fact_from, guard


@guard
def run(ctx: CheckContext) -> Verdict:
    measured, _ = fact_from(ctx, "false_alarms_per_hour", ctx.stage, "firmware")
    if measured < 0:
        raise ValueError(f"false_alarms_per_hour cannot be negative, got {measured}")
    return checked_verdict(ctx, measured)
