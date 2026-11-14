"""Cantilever root bending-stress verifier (bracket pack): safety factor of the arm at its root.

FORMULA:
    M      = F * (lever - root_offset)        bending moment at the root section, N mm
    sigma  = M * c / I = 6 M / (b h^2)        c = h / 2, I = b h^3 / 12 for a b x h rectangle
    safety factor = allowable / sigma
    allowable     = allowable_stress_mpa * printed_knock_down_factor   (the pack's PETG numbers)
    pass when the safety factor satisfies ctx.limit, normally ``limit.min``

The load F and the lever (distance from the mounting datum, the mast axis or the wall face, to
the load) come from the requirement the check serves: its statement says "a 0.5 kg load 150 mm
from ...", read by engenia.sim.loads. params may override them (``load_n``, ``lever_mm``) and may
add a ``dynamic_factor`` (default 1) that multiplies the load. A requirement that states a force
with no distance ("a flange carrying 15 N of thrust") takes its lever from the mechanical fact
``bending_lever_mm``: the thruster_duct pack's flange is judged this way. The pack's red-team
scenarios that target this check (a gust, a thrust surge, ice) carry an ``extra.load_factor``; the
largest one multiplies the load too, so the verdict is the safety factor under the worst
scenario. They are read from packs/<pack>/scenarios/, static files, so the number does not
depend on which stage finished. The root section b x h (``bending_root_section_mm``) and the
distance from the datum to the root (``bending_root_offset_mm``) are mechanical facts.

Because the number is a closed-form beam formula, the evidence level is "calculated". It covers
bending of the arm only: no shear, torsion, clamp friction or stress concentration at the fillet.
"""

from __future__ import annotations

import yaml

from engenia.contracts.models import CheckContext, Verdict
from engenia.harness.packs import DEFAULT_PACK, REPO, pack_dir
from engenia.sim.duct import parse_thrust_n
from engenia.sim.loads import parse_load, scenario_load_factor
from verifiers._common import (
    MissingInput,
    as_number,
    checked_verdict,
    fact_number,
    fact_vector,
    guard,
    read_interface,
)


def _allowable_mpa(ctx: CheckContext) -> float:
    """The pack's allowable stress with its printed knock-down; the buoy pack's PETG numbers
    when the project's pack has no `structure:` block."""
    for base in dict.fromkeys((pack_dir(ctx), REPO / "packs" / DEFAULT_PACK)):
        path = base / "pack.yaml"
        if path.is_file():
            structure = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("structure")
            if structure:
                return as_number(structure["allowable_stress_mpa"], "allowable_stress_mpa") \
                    * as_number(structure["printed_knock_down_factor"], "printed_knock_down_factor")
    raise MissingInput("no pack.yaml with a structure: block for the allowable stress")


def _load(ctx: CheckContext) -> tuple[float, float | None]:
    """(force N, lever mm) from params, else from the requirement's statement. A statement that
    gives only a force ("15 N of thrust") has no lever: the lever is None and comes from the
    mechanical facts (``bending_lever_mm``), which is how a flange under thrust is judged."""
    params = ctx.params or {}
    if "load_n" in params and "lever_mm" in params:
        return as_number(params["load_n"], "params.load_n"), as_number(
            params["lever_mm"], "params.lever_mm")
    path = ctx.spec_dir / f"{ctx.requirement}.yaml"
    try:
        statement = yaml.safe_load(path.read_text(encoding="utf-8"))["statement"]
    except (OSError, yaml.YAMLError, KeyError, TypeError) as exc:
        raise MissingInput(f"cannot read the statement of {ctx.requirement}: {exc}") from exc
    load = parse_load(statement)
    if load is not None:
        return load.force_n, load.lever_mm
    thrust = parse_thrust_n(statement)
    if thrust is None:
        raise MissingInput(
            f"{ctx.requirement} names no load and lever arm (for example '0.5 kg load 150 mm "
            "from the mast axis') and no thrust"
        )
    return thrust, None


@guard
def run(ctx: CheckContext) -> Verdict:
    facts = read_interface(ctx, "mechanical")
    width, depth = fact_vector(facts, "bending_root_section_mm", "mechanical", length=2)
    offset = fact_number(facts, "bending_root_offset_mm", "mechanical")
    force, lever = _load(ctx)
    if lever is None:
        lever = fact_number(facts, "bending_lever_mm", "mechanical") + offset
    force *= as_number((ctx.params or {}).get("dynamic_factor", 1.0), "params.dynamic_factor")
    force *= scenario_load_factor(pack_dir(ctx), ctx.check)
    if min(width, depth, force) <= 0:
        raise MissingInput("root section and load must be positive")
    arm = lever - offset
    if arm <= 0:
        raise MissingInput(f"the load ({lever:g} mm) is not beyond the arm root ({offset:g} mm)")
    sigma = 6 * force * arm / (width * depth**2)  # M c / I with M = F arm, c = h/2, I = b h^3/12
    return checked_verdict(ctx, _allowable_mpa(ctx) / sigma)
