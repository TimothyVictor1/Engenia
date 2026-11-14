"""Propeller tip-clearance verifier (thruster_duct pack): how far the blade tips are from the duct.

FORMULA:
    clearance = (duct_inner_diameter - propeller_diameter) / 2        mm
    pass when the clearance satisfies ctx.limit (``min`` and/or ``max``), and the clearance minus
    the swelling shift still satisfies ``min``

The duct inner diameter (``duct_inner_diameter_mm``, the throat at the propeller plane) is a
mechanical fact. The propeller diameter comes from the requirement the check serves: its statement
says "a 70 mm propeller", read by engenia.sim.duct (params may override it with ``propeller_mm``).
The pack's red-team scenarios that target this check carry ``extra.clearance_shift_mm``, an
allowance for printed PETG swelling inward in water; the largest one is applied to the minimum
only, so the blades cannot bind after the swell. The scenarios are packs/<pack>/scenarios/ files,
static, so the number does not depend on which stage finished.

The measured value is the nominal clearance. It is a closed-form difference of two diameters, so
the evidence level is "calculated". It says nothing about blade deflection, propeller runout or
the thruster's own mounting tolerance.
"""

from __future__ import annotations

import yaml

from engenia.contracts.models import CheckContext, Verdict, VerdictResult
from engenia.harness.packs import pack_dir
from engenia.sim.duct import parse_propeller_mm
from engenia.sim.loads import scenario_extra_max
from verifiers._common import (
    MissingInput,
    as_number,
    calculated_verdict,
    checked_verdict,
    fact_from,
    guard,
)


def _propeller_mm(ctx: CheckContext) -> float:
    params = ctx.params or {}
    if "propeller_mm" in params:
        return as_number(params["propeller_mm"], "params.propeller_mm")
    path = ctx.spec_dir / f"{ctx.requirement}.yaml"
    try:
        statement = yaml.safe_load(path.read_text(encoding="utf-8"))["statement"]
    except (OSError, yaml.YAMLError, KeyError, TypeError) as exc:
        raise MissingInput(f"cannot read the statement of {ctx.requirement}: {exc}") from exc
    diameter = parse_propeller_mm(statement)
    if diameter is None:
        raise MissingInput(
            f"{ctx.requirement} names no propeller diameter (for example 'a 70 mm propeller')"
        )
    return diameter


@guard
def run(ctx: CheckContext) -> Verdict:
    inner, _ = fact_from(ctx, "duct_inner_diameter_mm", "mechanical")
    propeller = _propeller_mm(ctx)
    if min(inner, propeller) <= 0:
        raise MissingInput("duct and propeller diameters must be positive")
    clearance = (inner - propeller) / 2
    verdict = checked_verdict(ctx, clearance)
    shift = scenario_extra_max(pack_dir(ctx), ctx.check, "clearance_shift_mm", 0.0)
    low = ctx.limit.min
    if verdict.result == VerdictResult.PASS and low is not None and clearance - shift < low:
        return calculated_verdict(
            ctx, clearance, VerdictResult.FAIL,
            message=(f"measured {clearance:g} is {clearance - shift:g} after the {shift:g} mm "
                     f"swelling shift, below min {low:g}"),
        )
    return verdict
