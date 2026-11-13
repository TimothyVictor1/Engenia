"""Stress safety factor verifier: hull safety factor from the simulation.

FACTS (simulation interface):
    safety_factor: allowable stress divided by the peak von Mises stress of the
        hull at the scenario depth, or null when no structure result exists.

MEASURED: the safety factor, compared with the check plan limit (R-012: min 2).

RESULT:
    pass/fail   a finite, positive safety factor is published
    error       safety_factor is null (the structure case has not produced a
                result: "not simulated" never counts as safe), or the fact is
                missing, not a number, or not positive.

ASSUMES: safety_factor comes from the structure simulation at the depth the
         stress requirement states.
"""

from __future__ import annotations

from typing import Any

from engenia.contracts.models import CheckContext, EvidenceLevel, Verdict, VerdictResult
from verifiers._common import (
    MissingInput,
    as_number,
    compare,
    comparison_message,
    error_verdict,
    guard,
    read_interface,
)

FACT_KEY = "safety_factor"


@guard
def run(ctx: CheckContext) -> Verdict:
    facts: dict[str, Any] = read_interface(ctx)
    if FACT_KEY not in facts:
        raise MissingInput(f"simulation interface has no '{FACT_KEY}' fact")
    raw = facts[FACT_KEY]
    if raw is None:
        return error_verdict(
            ctx, f"{FACT_KEY} is null: the structure case has not produced a result"
        )
    measured = as_number(raw, f"simulation.{FACT_KEY}")
    if measured <= 0:
        raise MissingInput(f"{FACT_KEY} must be positive, got {measured}")
    result = compare(measured, ctx.limit)
    message = None if result == VerdictResult.PASS else comparison_message(measured, ctx.limit)
    return Verdict(
        check=ctx.check,
        requirement=ctx.requirement,
        measured=measured,
        limit=ctx.limit,
        result=result,
        evidence_level=EvidenceLevel.SIMULATED,
        message=message,
    )
