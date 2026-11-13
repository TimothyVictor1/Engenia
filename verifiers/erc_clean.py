"""Electrical rule check verifier.

FORMULA:
    measured = erc_errors from the electronics interface
    pass exactly when erc_errors == 0
"""

from __future__ import annotations

from engenia.contracts.models import CheckContext, Verdict, VerdictResult
from verifiers._common import calculated_verdict, fact_from, guard


@guard
def run(ctx: CheckContext) -> Verdict:
    measured, _ = fact_from(ctx, "erc_errors", ctx.stage, "electronics")
    if measured < 0:
        raise ValueError(f"erc_errors cannot be negative, got {measured}")
    passed = measured == 0
    message = None if passed else f"{int(measured)} ERC error(s) reported"
    return calculated_verdict(
        ctx,
        measured,
        VerdictResult.PASS if passed else VerdictResult.FAIL,
        message=message,
    )
