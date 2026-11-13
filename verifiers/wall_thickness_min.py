"""Minimum wall thickness verifier.

FORMULA:
    measured = wall_min_mm from the interface
    pass when measured satisfies ctx.limit, normally ``limit.min``
"""

from __future__ import annotations

from engenia.contracts.models import CheckContext, Verdict
from verifiers._common import checked_verdict, fact_from, guard


@guard
def run(ctx: CheckContext) -> Verdict:
    measured, _ = fact_from(ctx, "wall_min_mm", ctx.stage, "mechanical")
    return checked_verdict(ctx, measured)
