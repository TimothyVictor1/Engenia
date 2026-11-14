"""testplan stage: a physical test plan generated from the requirements.

For every approved requirement that a person can verify on the printed prototype, the stage
writes the procedure, the instrument, the expected value and the pass limit to
build/testplan/physical-tests.md (for the person at the bench) and physical-tests.json (for
`engenia measure`, which reads the test ids and limits from it).

No model is called. The procedures are fixed templates, chosen by the requirement's
verify.check; the numbers (expected value, unit, limit) come from the requirement itself, so
the plan can never disagree with the spec. A requirement whose check has no physical template
(stress, detection rate, ERC, power budget) is listed under "Not covered", with the reason,
instead of being dropped.

Test ids are stable: requirement R-005 gives test T-005. Output has no timestamps, so equal
specs give equal files.

physical-tests.json:
    {"project": ..., "tests": [{"id": "T-001", "requirement": "R-001", "quantity": "mass",
      "check": ..., "title": ..., "instrument": ..., "procedure": [...],
      "expected": {"value": 400, "unit": "g", "text": "..."}, "limit": {"max": 400},
      "measure_option": "--mass-g" | null, "measurement_key": "mass_g" | null}],
     "not_covered": [{"requirement": ..., "check": ..., "reason": ...}]}

``measurement_key`` is set only when `engenia measure` takes that number directly, so that it
can compare the value just measured with the limit of the planned test.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass

from engenia.contracts.models import Limit, Requirement, RequirementStatus, StageContext
from engenia.core.inputs import COMMON_TREES, PACK_TREES, PROJECT_TREES
from engenia.core.spec import load_spec

PLAN_JSON = "physical-tests.json"
PLAN_MD = "physical-tests.md"


@dataclass(frozen=True)
class Template:
    quantity: str
    title: str
    instrument: str
    procedure: tuple[str, ...]
    measure_option: str | None = None
    measurement_key: str | None = None


MASS = Template(
    "mass",
    "Mass of the assembled prototype",
    "Digital scale, 0.1 g resolution or better, calibrated with a known mass",
    (
        "Assemble the prototype as it will be deployed: hull, lid, electronics, battery, seals.",
        "Zero the scale. Place the assembled prototype on it and wait for a stable reading.",
        "Record the mass in grams. Repeat three times, removing the prototype each time.",
        "Use the mean of the three readings as the measured mass.",
        "Record it with `engenia measure <project> --mass-g <value> --waterline-mm <value>`.",
    ),
    "--mass-g",
    "mass_g",
)

WATERLINE = Template(
    "waterline",
    "Waterline height of the floating prototype",
    "Calm freshwater or saltwater tank, steel ruler or calipers, waterproof marker; water "
    "density measured with a hydrometer",
    (
        "Float the assembled prototype upright in calm water. Wait 5 minutes for it to settle.",
        "Mark the water line on the hull with the marker.",
        "Remove and dry the prototype. Measure from the hull base to the mark, in millimetres.",
        "Repeat at three points around the hull and use the mean.",
        "Record the water density. Record the height with "
        "`engenia measure <project> --waterline-mm <value> --mass-g <value>`.",
    ),
    "--waterline-mm",
    "waterline_mm",
)

BUOYANCY = Template(
    "buoyancy",
    "Buoyancy margin",
    "Digital scale (0.1 g), vessel big enough to submerge the prototype, thermometer, hydrometer",
    (
        "Weigh the assembled, sealed prototype dry: total mass m in grams.",
        "Fill the vessel with water and measure its density in g/cm3 (hydrometer).",
        "Put the vessel on the scale and zero it. Push the prototype fully under water with a "
        "thin rod, without touching the vessel. Do not let it touch the bottom.",
        "Read the scale: the added grams divided by the water density is the displaced volume "
        "V in cm3.",
        "Compute margin = (V x density - m) / m. Repeat three times and use the mean.",
        "Keep the prototype under water for 10 minutes and check the inside for leaks.",
    ),
)

TILT_AT_REST = Template(
    "tilt",
    "Tilt of the floating prototype at rest",
    "Calm water tank, digital inclinometer (0.1 degree) fixed to the lid or a smartphone level "
    "calibrated on a flat surface",
    (
        "Float the assembled prototype in calm water. Wait 5 minutes for it to settle.",
        "Read the inclinometer. The tilt is the absolute angle from vertical, in degrees.",
        "Rotate the prototype 90 degrees about its vertical axis, let it settle and read again.",
        "Use the larger of the two readings as the measured tilt.",
        "Record it with `engenia measure <project> --tilt-deg <value> --mass-g <value> "
        "--waterline-mm <value>`.",
    ),
    "--tilt-deg",
    "tilt_deg",
)

INCLINING = Template(
    "tilt",
    "Metacentric height by inclining test",
    "Calm water tank, digital inclinometer (0.1 degree), a small known mass (about 5 g), ruler",
    (
        "Float the assembled prototype upright in calm water and let it settle.",
        "Place the known mass w (grams) on the lid at a measured distance d (mm) from the axis.",
        "Read the heel angle theta once it settles. Use a small angle, below 10 degrees.",
        "Compute GM = w x d / (M x tan(theta)) in mm, where M is the total mass of the "
        "prototype in grams, including w.",
        "Repeat with the mass on the opposite side and use the mean of the two GM values.",
    ),
)

WAVE_TILT = Template(
    "tilt",
    "Tilt in waves",
    "Wave tank or sheltered water with a known wave height, wave staff or ruler, inclinometer "
    "(logging) or a fixed camera with a protractor overlay",
    (
        "Float the prototype free in the tank at the wave height named in the requirement.",
        "Run waves at the scenario period for at least 20 wave cycles.",
        "Record the maximum tilt from vertical in degrees from the inclinometer log or video.",
        "Repeat at every wave height in the requirement and keep the largest reading.",
    ),
)

PRINT_FIT = Template(
    "fit",
    "Printed hull fits the print bed",
    "Calipers or steel ruler, 0.1 mm resolution; the printer's stated bed size",
    (
        "Measure the printed hull's three bounding dimensions in millimetres. Do this on the "
        "printed part, not on the CAD file.",
        "Sort the three dimensions and the three bed dimensions in ascending order.",
        "The part fits if each sorted dimension is no larger than the matching bed dimension.",
        "Record the result as 1 (fits) or 0 (does not fit), with the dimensions.",
    ),
)

BAY_FIT = Template(
    "fit",
    "Electronics and battery fit the bay",
    "Calipers, 0.1 mm resolution; the printed hull and the real board and battery",
    (
        "Measure the bay in the printed hull, and the board and the battery, in millimetres.",
        "Place the board, then the battery, in the bay in the orientation used in the design. "
        "Check that nothing is forced and the lid closes.",
        "Compare the sorted dimensions of each part with the sorted dimensions of the bay.",
        "Record the result as 1 (both fit) or 0 (either does not), with the dimensions.",
    ),
)

ENDURANCE = Template(
    "endurance",
    "Battery endurance",
    "Inline power meter or source meter (current to 0.01 mA, logging), the real battery "
    "(rated capacity from its datasheet), a thermometer",
    (
        "Fully charge the battery. Put the power meter in series with the battery.",
        "Run the firmware in its normal duty cycle at about 20 C for at least 24 hours, logging "
        "current and voltage.",
        "Compute the average power in mW over the whole log, including every sleep and "
        "transmit period.",
        "Endurance in days = battery Wh x 1000 / average power mW / 24 x 0.8, where 0.8 is the "
        "derating the design checks use.",
        "For a final proof, run the unit on the battery until it stops and record the days.",
    ),
)

# verify.check -> template. Checks not listed here are reported under "Not covered".
TEMPLATES: dict[str, Template] = {
    "mass_budget": MASS,
    "measured_mass": MASS,
    "measured_waterline": WATERLINE,
    "buoyancy_margin": BUOYANCY,
    "measured_tilt": TILT_AT_REST,
    "metacentric_height": INCLINING,
    "wave_tilt": WAVE_TILT,
    "print_bed_fit": PRINT_FIT,
    "bay_fit": BAY_FIT,
    "battery_capacity": ENDURANCE,
}

NOT_COVERED = {
    "stress_safety_factor": "needs a pressure rig and a destructive test, not a bench measurement",
    "detection_rate": "software behaviour, shown by replaying audio, not at the bench",
    "false_alarm_rate": "software behaviour, shown by replaying audio, not at the bench",
    "erc_clean": "an electrical rule check of the netlist, not a physical measurement",
    "power_budget": "covered indirectly by the battery endurance test (average power is logged)",
    "wall_thickness_min": "needs a cut section of the print; measure it from a sacrificial part",
}


def _limit_text(limit: Limit | None, unit: str | None) -> str:
    if limit is None:
        return "no limit"
    suffix = f" {unit}" if unit else ""
    parts = []
    if limit.min is not None:
        parts.append(f">= {limit.min:g}{suffix}")
    if limit.max is not None:
        parts.append(f"<= {limit.max:g}{suffix}")
    if limit.equals is not None:
        parts.append(f"= {limit.equals:g}{suffix}")
    return " and ".join(parts) or "no limit"


def _test_id(requirement: Requirement) -> str:
    return "T-" + requirement.id.removeprefix("R-")


def _entry(requirement: Requirement, template: Template) -> dict:
    limit = requirement.verify.limit
    value = requirement.value
    unit = requirement.unit
    text = _limit_text(limit, unit)
    return {
        "id": _test_id(requirement),
        "requirement": requirement.id,
        "statement": requirement.statement,
        "check": requirement.verify.check,
        "quantity": template.quantity,
        "title": template.title,
        "instrument": template.instrument,
        "procedure": list(template.procedure),
        "expected": {"value": value, "unit": unit, "text": text},
        "limit": limit.model_dump(exclude_none=True) if limit else {},
        "pass_rule": f"Pass when the measured value is {text}.",
        "measure_option": template.measure_option,
        "measurement_key": template.measurement_key,
    }


def make_plan(ctx: StageContext) -> dict:
    """The plan as plain data, from the approved requirements in the project's spec folder."""
    tests: list[dict] = []
    not_covered: list[dict] = []
    for spec_file in load_spec(ctx.spec_dir):
        requirement = spec_file.requirement
        if requirement.status is not RequirementStatus.APPROVED:
            continue
        check = requirement.verify.check
        template = TEMPLATES.get(check)
        if template is not None:
            tests.append(_entry(requirement, template))
        else:
            not_covered.append(
                {
                    "requirement": requirement.id,
                    "check": check,
                    "reason": NOT_COVERED.get(
                        check, f"no physical test template for the check '{check}'"
                    ),
                }
            )
    tests.sort(key=lambda t: t["id"])
    not_covered.sort(key=lambda n: n["requirement"])
    return {"project": ctx.project, "tests": tests, "not_covered": not_covered}


def render_markdown(plan: dict) -> str:
    lines = [f"# Physical test plan: {plan['project']}", ""]
    lines += [
        "Generated from the approved requirements. Each test names the requirement it checks, "
        "the instrument, the procedure, the expected value and the pass limit. A measured "
        "number replaces the calculated one only when it is recorded with `engenia measure`.",
        "",
    ]
    if not plan["tests"]:
        lines += ["No approved requirement can be verified by a physical measurement.", ""]
    for test in plan["tests"]:
        lines += [f"## {test['id']} {test['title']} ({test['requirement']})", ""]
        lines += [f"Requirement: {test['statement']}", ""]
        lines += [f"- **Instrument:** {test['instrument']}"]
        lines += [f"- **Expected value / pass limit:** {test['expected']['text']}"]
        lines += [f"- **Pass rule:** {test['pass_rule']}"]
        if test["measure_option"]:
            lines += [f"- **Recorded with:** `engenia measure` option `{test['measure_option']}`"]
        lines += ["", "Procedure:", ""]
        lines += [f"{n}. {step}" for n, step in enumerate(test["procedure"], start=1)]
        lines += [""]
    if plan["not_covered"]:
        lines += ["## Not covered by a physical test", ""]
        lines += [
            f"- {item['requirement']} ({item['check']}): {item['reason']}"
            for item in plan["not_covered"]
        ]
        lines += [""]
    return "\n".join(lines)


class TestplanStage:
    __test__ = False  # not a pytest class, despite the name

    name = "testplan"
    needs: list[str] = []
    reads_contract = False
    checks: list[str] = []

    # The plan is a function of the spec, so the spec is an input (the default list has none).
    input_files = (
        "uv.lock", "engenia/stages/base.py", "engenia/stages/registry.py",
        "{project}/checks/checks.yaml", "{project}/checks/APPROVED", "{project}/project.yaml",
        "{project}/calibration.yaml", "{project}/calibration.json",
    )
    input_trees = (
        COMMON_TREES + PACK_TREES + PROJECT_TREES
        + ("verifiers", "calibration", "engenia/agents/testplan", "{project}/spec")
    )

    def generate(self, ctx: StageContext) -> None:
        plan = make_plan(ctx)
        ctx.src_dir.mkdir(parents=True, exist_ok=True)
        (ctx.src_dir / PLAN_JSON).write_text(
            json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        (ctx.src_dir / PLAN_MD).write_text(render_markdown(plan), encoding="utf-8")

    def build(self, ctx: StageContext) -> None:
        if ctx.build_dir.exists():
            shutil.rmtree(ctx.build_dir)
        shutil.copytree(ctx.src_dir, ctx.build_dir)

    def interface(self, ctx: StageContext) -> dict:
        data = json.loads((ctx.build_dir / PLAN_JSON).read_text(encoding="utf-8"))
        return {
            "tests": len(data["tests"]),
            "test_ids": [t["id"] for t in data["tests"]],
            "not_covered": [n["requirement"] for n in data["not_covered"]],
        }
