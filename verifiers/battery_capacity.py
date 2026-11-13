"""Battery endurance verifier.

FORMULA:
    endurance_days = battery_wh * 1000 / avg_power_mw / 24 * derating_factor

``battery_wh`` and ``avg_power_mw`` come from the electronics interface. The derating
factor comes from params (``derating``, ``derating_factor`` or ``battery_derating``) and
defaults to 0.8. Pass when the result satisfies ctx.limit, normally ``limit.min``.
"""

from __future__ import annotations

from engenia.contracts.models import CheckContext, Verdict
from verifiers._common import (
    checked_verdict,
    derating_factor,
    fact_number,
    guard,
    interface_with,
)


@guard
def run(ctx: CheckContext) -> Verdict:
    _, facts = interface_with(
        ctx, ("battery_wh", "avg_power_mw"), ctx.stage, "electronics"
    )
    battery_wh = fact_number(facts, "battery_wh", "electronics")
    avg_power_mw = fact_number(facts, "avg_power_mw", "electronics")
    if battery_wh < 0:
        raise ValueError(f"battery_wh cannot be negative, got {battery_wh}")
    if avg_power_mw <= 0:
        raise ValueError(f"avg_power_mw must be positive, got {avg_power_mw}")
    derating = derating_factor(ctx)
    measured = battery_wh * 1000.0 / avg_power_mw / 24.0 * derating
    return checked_verdict(ctx, measured)
