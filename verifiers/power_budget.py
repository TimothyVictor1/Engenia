"""Average power budget verifier.

FORMULA:
    measured = avg_power_mw from the electronics interface
    pass when measured satisfies ctx.limit, normally ``limit.max``
"""

from __future__ import annotations

from engenia.contracts.models import CheckContext, Verdict
from verifiers._common import checked_verdict, fact_from, guard


@guard
def run(ctx: CheckContext) -> Verdict:
    measured, _ = fact_from(ctx, "avg_power_mw", ctx.stage, "electronics")
    if measured < 0:
        raise ValueError(f"avg_power_mw cannot be negative, got {measured}")
    return checked_verdict(ctx, measured)
