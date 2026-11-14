"""AI proposals for design parameters, with the deterministic function as the fallback.

`propose` asks the design model (ENGENIA_DESIGN_MODEL) for a parameter set, using the same
generate-build-verify-retry loop as any design agent (`generate_until_pass`). The model writes
proposal.json in a scratch folder; scripts check it (`validate` returns Verdicts with numbers),
and a failed attempt sends those numbers back to the model. When no model is configured, the model
endpoint fails, or the attempts run out, `fallback()` (the stage's existing deterministic
function) is used. Which one produced the parameters is recorded in src/<stage>/proposer.json
as {"proposer": "model" | "deterministic"}; `proposer_fact` reads it back for a stage interface.

The model-family guard runs first and its refusal is never swallowed by the fallback.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from engenia.contracts.models import (
    Contract,
    EvidenceLevel,
    Limit,
    Requirement,
    StageContext,
    Verdict,
    VerdictResult,
)
from engenia.harness.design_loop import generate_until_pass
from engenia.harness.limits import Limits
from engenia.harness.llm import ChatFn, LLMError, chat, check_model_families

PROPOSAL_FILE = "proposal.json"
RECORD_FILE = "proposer.json"

Proposer = Literal["model", "deterministic"]
Validator = Callable[[dict[str, Any]], list[Verdict]]

PROPOSER_PROMPT = """\
You propose design parameters for the {stage} stage of an engineering project. Write one file,
proposal.json, containing a single JSON object with exactly the parameters described in the task.
Call record_decision for every parameter you choose, citing the requirement ids and numbers.
Use run_build to load it; scripts then check your numbers against the requirements and the
contract, and any failing numbers are sent back to you. When the file is complete, reply with a
short summary and no tool calls.
"""


def proposer_fact(ctx: StageContext) -> str:
    """ "model" or "deterministic", as recorded by the last call of `propose` for this stage."""
    try:
        return json.loads((ctx.src_dir / RECORD_FILE).read_text("utf-8"))["proposer"]
    except (OSError, ValueError, KeyError):
        return "deterministic"


def _record(ctx: StageContext, proposer: Proposer) -> None:
    ctx.src_dir.mkdir(parents=True, exist_ok=True)
    (ctx.src_dir / RECORD_FILE).write_text(
        json.dumps({"proposer": proposer}, sort_keys=True) + "\n", encoding="utf-8"
    )


class _ProposalStage:
    """A scratch stage whose only job is to carry the proposal through the design loop."""

    needs: list[str] = []
    reads_contract = False
    checks: list[str] = []

    def __init__(self, name: str) -> None:
        self.name = name

    def generate(self, ctx: StageContext) -> None:  # the loop never calls this
        raise AssertionError("the proposer does not call Stage.generate")

    def build(self, ctx: StageContext) -> None:
        if ctx.build_dir.exists():
            shutil.rmtree(ctx.build_dir)
        shutil.copytree(ctx.src_dir, ctx.build_dir)

    def interface(self, ctx: StageContext) -> dict:
        return {}


def _load_proposal(path: Path) -> dict[str, Any] | str:
    """The proposal object, or a sentence saying what is wrong with the file."""
    try:
        loaded = json.loads(path.read_text("utf-8"))
    except OSError:
        return f"{PROPOSAL_FILE} has not been written"
    except ValueError as exc:
        return f"{PROPOSAL_FILE} is not valid JSON: {exc}"
    return loaded if isinstance(loaded, dict) else f"{PROPOSAL_FILE} must hold a JSON object"


def _model_available(model: str | None, chat_fn: ChatFn) -> str | None:
    name = model or os.environ.get("ENGENIA_DESIGN_MODEL", "").strip()
    if not name:
        return None
    if chat_fn is chat and not os.environ.get("ENGENIA_LLM_BASE_URL", "").strip():
        return None
    return name


def propose(
    ctx: StageContext,
    *,
    task: str,
    validate: Validator,
    fallback: Callable[[], dict[str, Any]],
    finish: Callable[[dict[str, Any]], dict[str, Any]] = lambda proposal: proposal,
    model: str | None = None,
    limits: Limits | None = None,
    chat_fn: ChatFn = chat,
) -> dict[str, Any]:
    """The parameters for the stage in `ctx`: the model's, if it passes `validate`, else fallback().

    `finish` turns the accepted proposal into the stage's full result (for example by adding
    numbers computed by script). It runs after validation.
    """
    check_model_families()
    name = _model_available(model, chat_fn)
    if name is not None:
        accepted = _ask_model(ctx, name, task, validate, limits, chat_fn)
        if accepted is not None:
            _record(ctx, "model")
            return finish(accepted)
    params = fallback()
    _record(ctx, "deterministic")
    return params


def _ask_model(
    ctx: StageContext,
    model: str,
    task: str,
    validate: Validator,
    limits: Limits | None,
    chat_fn: ChatFn,
) -> dict[str, Any] | None:
    with tempfile.TemporaryDirectory(prefix="engenia-proposal-") as tmp:
        scratch = StageContext(project=ctx.project, stage=ctx.stage, root=Path(tmp))
        stage = _ProposalStage(ctx.stage)

        def run_checks(_stage: Any, sctx: StageContext) -> list[Verdict]:
            proposal = _load_proposal(sctx.build_dir / PROPOSAL_FILE)
            if isinstance(proposal, str):
                return [_failure("proposal_file", proposal)]
            return validate(proposal)

        try:
            status = generate_until_pass(
                stage,
                scratch,
                model=model,
                limits=limits or Limits.from_env(),
                system_prompt=PROPOSER_PROMPT.format(stage=ctx.stage),
                task=task,
                chat_fn=chat_fn,
                run_checks=run_checks,
            )
        except (LLMError, OSError, RuntimeError):
            return None
        if status != "pass":
            return None
        proposal = _load_proposal(scratch.build_dir / PROPOSAL_FILE)
        return proposal if isinstance(proposal, dict) else None


# --- Verdict helpers ---------------------------------------------------------------------------


def _failure(check: str, message: str) -> Verdict:
    return Verdict(
        check=check,
        requirement=None,
        measured=None,
        limit=Limit(),
        result=VerdictResult.FAIL,
        evidence_level=EvidenceLevel.CALCULATED,
        message=message,
    )


def _number(check: str, measured: float, limit: Limit, message: str | None = None) -> Verdict:
    ok = math.isfinite(measured)
    if ok and limit.min is not None:
        ok = measured >= limit.min
    if ok and limit.max is not None:
        ok = measured <= limit.max
    return Verdict(
        check=check,
        requirement=None,
        measured=measured if math.isfinite(measured) else None,
        limit=limit,
        result=VerdictResult.PASS if ok else VerdictResult.FAIL,
        evidence_level=EvidenceLevel.CALCULATED,
        message=None if ok else (message or f"{check}: {measured} is outside the limit"),
    )


# --- mechanical --------------------------------------------------------------------------------

MECHANICAL_TASK = """\
Stage: mechanical. Propose the buoy hull parameters (all lengths in mm) as a JSON object with
exactly these keys: outer_diameter_mm, height_mm, wall_thickness_mm, battery_bay_mm [x, y, z],
board_holes_mm [[x, y] x4], lid_thickness_mm, o_ring_groove_width_mm, o_ring_groove_depth_mm,
standoff_height_mm, screw_hole_diameter_mm, infill_factor.

Rules the scripts check: the hull must be buildable (the bay fits inside the sealing rim, the
four board holes lie inside the bay, the O-ring groove leaves a wall of lid material, the standoff
is taller than the wall); wall_thickness_mm must meet every wall minimum below; battery_bay_mm
must be at least the contract bay; outer_diameter_mm must fit the envelope's x and y; and
height_mm + lid_thickness_mm must fit the envelope's z.

{requirements}

Contract:
{contract}
"""


def mechanical_validator(contract: Contract, wall_minimum: float | None) -> Validator:
    ex, ey, ez = contract.budgets.mechanical.outer_envelope_mm
    bay = contract.budgets.electronics.bay_mm

    def validate(params: dict[str, Any]) -> list[Verdict]:
        verdicts: list[Verdict] = []
        try:
            from packs.buoy.templates.hull import HullParams

            HullParams(**params)
        except ImportError:
            pass  # no CAD kernel here: the numeric checks below still run
        except (TypeError, ValueError) as exc:
            verdicts.append(_failure("hull_buildable", f"hull parameters rejected: {exc}"))
        try:
            diameter = float(params["outer_diameter_mm"])
            height = float(params["height_mm"]) + float(params["lid_thickness_mm"])
            wall = float(params["wall_thickness_mm"])
            proposed_bay = [float(v) for v in params["battery_bay_mm"]]
            if len(proposed_bay) != 3:
                raise ValueError("battery_bay_mm needs three values")
        except (KeyError, TypeError, ValueError) as exc:
            malformed = _failure("hull_fields", f"missing or malformed field: {exc!r}")
            return [*verdicts, malformed]
        widest = min(ex, ey)
        verdicts.append(
            _number(
                "hull_diameter_mm",
                diameter,
                Limit(max=widest),
                f"outer diameter {diameter} mm exceeds the {widest} mm envelope",
            )
        )
        verdicts.append(
            _number(
                "hull_assembled_height_mm",
                height,
                Limit(max=ez),
                f"hull plus lid {height} mm exceeds the {ez} mm limit",
            )
        )
        slack = min(p - b for p, b in zip(proposed_bay, bay, strict=True))
        verdicts.append(
            _number(
                "hull_bay_slack_mm",
                slack,
                Limit(min=0),
                f"bay {proposed_bay} is smaller than {list(bay)}",
            )
        )
        if wall_minimum is not None:
            verdicts.append(
                _number(
                    "hull_wall_mm",
                    wall,
                    Limit(min=wall_minimum),
                    f"wall {wall} mm is under the {wall_minimum} mm minimum",
                )
            )
        return verdicts

    return validate


def _requirement_lines(requirements: list[Requirement]) -> str:
    lines = ["Requirements:"]
    for r in requirements:
        if r.status.value == "approved":
            limit = r.verify.limit.model_dump(exclude_none=True) if r.verify.limit else {}
            lines.append(f"- {r.id}: {r.statement} (check {r.verify.check}, limit {limit})")
    return "\n".join(lines)


def propose_mechanical_params(
    ctx: StageContext,
    contract: Contract | dict,
    requirements: list[Requirement],
    fallback: Callable[[Contract | dict, list[Requirement]], dict[str, Any]],
    **kwargs: Any,
) -> dict[str, Any]:
    """The mechanical stage's hook: `propose_mechanical_params(ctx, contract, requirements,
    propose_params)` returns what `propose_params(contract, requirements)` would, unless the
    design model proposes hull parameters that pass the scripted checks."""
    c = contract if isinstance(contract, Contract) else Contract.model_validate(contract)
    walls = [
        r.verify.limit.min
        for r in requirements
        if r.status.value == "approved"
        and r.verify.check == "wall_thickness_min"
        and r.verify.limit is not None
        and r.verify.limit.min is not None
    ]
    return propose(
        ctx,
        task=MECHANICAL_TASK.format(
            requirements=_requirement_lines(requirements), contract=c.model_dump_json(indent=2)
        ),
        validate=mechanical_validator(c, max(walls, default=None)),
        fallback=lambda: fallback(contract, requirements),
        **kwargs,
    )


# --- electronics -------------------------------------------------------------------------------

ELECTRONICS_TASK = """\
Stage: electronics. Choose one part for each function. Write a JSON object
{{"parts": {{{roles_example}}}}}
using ids from the catalogue below. Scripts compute power and size from your choice and check
that the battery voltage suits the regulator, that the design lasts {target_text}, and
that the assembly fits the bay {bay} mm.

{requirements}

Catalogue:
{catalogue}
"""


def _roles_example(roles: set[str]) -> str:
    return ", ".join(f'"{role}": id' for role in sorted(roles))


def _required_functions(facts: Any) -> set[str]:
    """The part functions the design needs, from the pack's catalogue."""
    from engenia.agents.electronics.facts import required_functions

    return set(required_functions(facts))


def _selection_from_parts(contract: Contract, facts: Any, target: float, parts_ids: Any) -> dict:
    """The same record `propose_selection` returns, computed from the chosen part ids.
    Raises ValueError saying what is wrong with the choice. A None target (no endurance
    requirement) is recorded as zero days, so downstream formatting stays numeric."""
    from engenia.agents.electronics.selection import assembly_size, power_facts

    target = 0.0 if target is None else target
    roles = _required_functions(facts)
    if not isinstance(parts_ids, dict) or set(parts_ids) != roles:
        raise ValueError(f"parts must name exactly {sorted(roles)}")
    by_id = {p.id: p for p in facts.parts}
    parts = {}
    for role in sorted(roles):
        part = by_id.get(parts_ids[role])
        if part is None:
            raise ValueError(f"{role}: unknown part id {parts_ids[role]!r}")
        if part.function != role:
            raise ValueError(f"{part.id} is a {part.function}, not a {role}")
        parts[role] = part
    regulator, battery = parts["regulator"], parts["battery"]
    lo, hi = regulator.supply_voltage_v
    if not lo <= battery.supply_voltage_v[0] <= battery.supply_voltage_v[1] <= hi:
        raise ValueError(f"{battery.id} voltage range is outside the regulator input {lo}-{hi} V")
    for role in sorted(roles - {"battery", "regulator"}):
        p = parts[role]
        if not p.supply_voltage_v[0] <= regulator.output_voltage_v <= p.supply_voltage_v[1]:
            raise ValueError(f"{p.id}: incompatible regulated voltage")
    bay = contract.budgets.electronics.bay_mm
    size = assembly_size(battery, facts, bay)
    chosen = {
        "parts": {key: part.id for key, part in sorted(parts.items())},
        "duty_cycle": dict(facts.design.duty_cycle),
        "target_endurance_days": target,
        "bay_mm": list(bay),
        "board_mm": list(facts.design.board_mm),
        "assembly_mm": size,
        "mass_g": sum(p.mass_g for p in parts.values()) + facts.design.support_mass_g,
        **power_facts(parts, facts, facts.design.duty_cycle),
    }
    fits = all(s <= b for s, b in zip(size, bay, strict=True))
    chosen["requested_bay_mm"] = (
        None if fits else [max(s, b) for s, b in zip(size, bay, strict=True)]
    )
    return chosen


def electronics_validator(contract: Contract, facts: Any, target: float | None) -> Validator:
    def validate(proposal: dict[str, Any]) -> list[Verdict]:
        try:
            chosen = _selection_from_parts(contract, facts, target, proposal.get("parts"))
        except ValueError as exc:
            return [_failure("selection_valid", str(exc))]
        bay = contract.budgets.electronics.bay_mm
        slack = min(b - s for s, b in zip(chosen["assembly_mm"], bay, strict=True))
        verdicts = []
        if target is not None:
            verdicts.append(
                _number(
                    "endurance_days",
                    chosen["endurance_days"],
                    Limit(min=target),
                    f"endurance {chosen['endurance_days']:.2f} days is under {target} days",
                )
            )
        verdicts.append(
            _number(
                "bay_slack_mm",
                slack,
                Limit(min=0),
                f"assembly {chosen['assembly_mm']} mm does not fit the bay {list(bay)} mm",
            )
        )
        return verdicts

    return validate


def propose_electronics_selection(
    ctx: StageContext,
    contract: Contract,
    requirements: list[Requirement],
    facts: Any,
    fallback: Callable[[Contract, list[Requirement], Any], dict[str, Any]],
    **kwargs: Any,
) -> dict[str, Any]:
    """The electronics stage's hook: `propose_electronics_selection(ctx, contract, requirements,
    facts, propose_selection)` returns what `propose_selection(...)` would, unless the design
    model chooses parts that pass the scripted checks."""
    from engenia.agents.electronics.selection import endurance_target

    target = endurance_target(requirements)
    roles = _required_functions(facts)
    catalogue = "\n".join(
        f"- {p.id} ({p.function}): {p.supply_voltage_v[0]}-{p.supply_voltage_v[1]} V, "
        f"{p.mass_g} g, size {list(p.size_mm)} mm, capacity {p.capacity_mah} mAh"
        for p in facts.parts
    )
    return propose(
        ctx,
        task=ELECTRONICS_TASK.format(
            roles_example=_roles_example(roles),
            target_text=(
                f"at least {target:g} days" if target is not None else "as long as possible"
            ),
            bay=list(contract.budgets.electronics.bay_mm),
            requirements=_requirement_lines(requirements),
            catalogue=catalogue,
        ),
        validate=electronics_validator(contract, facts, target),
        finish=lambda proposal: _selection_from_parts(contract, facts, target, proposal["parts"]),
        fallback=lambda: fallback(contract, requirements, facts),
        **kwargs,
    )


# --- firmware ----------------------------------------------------------------------------------

FIRMWARE_TASK = """\
Stage: firmware. Propose the {detector} detector's settings as a JSON object with exactly
these keys: {keys_doc}. {extra_doc} The audio is mono, sampled at 2000 Hz.
Scripts check the ranges: {ranges}. The firmware is then built and its detection and
false-alarm checks run on your numbers.

{requirements}
"""

FIRMWARE_KEYS = {
    "tone": ("frequency_hz", "threshold", "hold_s"),
    "threshold": ("threshold", "hold_s"),
}

FIRMWARE_RANGES = {
    "frequency_hz": (
        "detector_frequency_hz",
        20,
        900,
        "frequency {value} Hz is outside 20 to 900 Hz",
    ),
    "threshold": (
        "detector_threshold",
        1e-6,
        0.999999,
        "threshold {value} must be above 0 and below 1",
    ),
    "hold_s": ("detector_hold_s", 0.01, 5, "hold {value} s is outside 0.01 to 5 s"),
}


def firmware_validator(detector_type: str = "tone") -> Validator:
    keys = FIRMWARE_KEYS[detector_type]

    def validate(proposal: dict[str, Any]) -> list[Verdict]:
        if set(proposal) != set(keys):
            return [_failure("detector_fields", f"keys must be exactly {list(keys)}")]
        try:
            values = {key: float(proposal[key]) for key in keys}
        except (TypeError, ValueError):
            return [_failure("detector_fields", "every value must be a number")]
        verdicts = []
        for key in keys:
            check, low, high, message = FIRMWARE_RANGES[key]
            verdicts.append(
                _number(
                    check, values[key], Limit(min=low, max=high), message.format(value=values[key])
                )
            )
        return verdicts

    return validate


def propose_firmware_detector(
    ctx: StageContext,
    requirements: list[Requirement],
    detector_type: str,
    fallback: Callable[[], dict[str, Any]],
    **kwargs: Any,
) -> dict[str, Any]:
    """The firmware stage's hook: returns what `fallback()` would (the default detector
    settings) unless the design model proposes settings that pass the scripted range checks."""
    keys_doc = {
        "tone": "frequency_hz (the tone to detect), threshold (amplitude, relative to full "
        "scale), hold_s (seconds the tone must continue before an event fires)",
        "threshold": "threshold (block RMS amplitude, relative to full scale), hold_s (seconds "
        "the level must stay above the threshold before an event fires)",
    }
    extra = {
        "tone": "",
        "threshold": "The detector fires on sustained signal level; there is no frequency setting.",
    }
    ranges = ", ".join(
        f"{name} from {low:g} to {high:g}" if low > 1e-9 else f"{name} above 0 and below {high:g}"
        for name, low, high, _ in (FIRMWARE_RANGES[key] for key in FIRMWARE_KEYS[detector_type])
    )
    return propose(
        ctx,
        task=FIRMWARE_TASK.format(
            detector=detector_type,
            keys_doc=keys_doc[detector_type],
            extra_doc=extra[detector_type],
            ranges=ranges,
            requirements=_requirement_lines(requirements),
        ),
        validate=firmware_validator(detector_type),
        finish=lambda proposal: {key: float(proposal[key]) for key in FIRMWARE_KEYS[detector_type]},
        fallback=fallback,
        **kwargs,
    )
