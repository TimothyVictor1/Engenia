"""Mass budget verifier.

FORMULA:
    mechanical stage: measured = mechanical.dry_mass_g
                      limit    = contract.budgets.mechanical.mass_g as a max
    electronics stage: measured = electronics.mass_g
                       limit    = contract.budgets.electronics.mass_g as a max
    any other stage:   measured = mechanical.dry_mass_g + electronics.mass_g
                       limit    = ctx.limit (the total-mass requirement, normally a max)

The per-stage budget is used when the check is run for one design stage. A total-mass
check (for example at integration) uses the limit from the check plan instead.
"""

from __future__ import annotations

from typing import Any

from engenia.contracts.models import CheckContext, Limit, Verdict
from verifiers._common import (
    MissingInput,
    checked_verdict,
    fact_number,
    guard,
    read_contract,
    read_interface,
    try_read_interface,
)


def _stage_budget(contract: dict[str, Any], stage: str) -> float:
    try:
        value = contract["budgets"][stage]["mass_g"]
    except (KeyError, TypeError) as exc:
        raise MissingInput(f"contract.json has no budgets.{stage}.mass_g") from exc
    return fact_number({"mass_g": value}, "mass_g", f"contract.budgets.{stage}")


@guard
def run(ctx: CheckContext) -> Verdict:
    if ctx.stage in ("mechanical", "electronics"):
        facts = read_interface(ctx, ctx.stage)
        key = "dry_mass_g" if ctx.stage == "mechanical" else "mass_g"
        measured = fact_number(facts, key, ctx.stage)
        budget = _stage_budget(read_contract(ctx), ctx.stage)
        return checked_verdict(ctx, measured, limit=Limit(max=budget))

    mechanical = try_read_interface(ctx, "mechanical")
    electronics = try_read_interface(ctx, "electronics")
    if mechanical is None or electronics is None:
        missing = []
        if mechanical is None:
            missing.append("mechanical")
        if electronics is None:
            missing.append("electronics")
        raise MissingInput(
            "total mass needs mechanical and electronics interfaces; missing "
            + ", ".join(missing)
        )
    measured = fact_number(mechanical, "dry_mass_g", "mechanical") + fact_number(
        electronics, "mass_g", "electronics"
    )
    return checked_verdict(ctx, measured)
