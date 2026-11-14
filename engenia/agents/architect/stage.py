"""architect: proposes projects/<p>/contract/contract.json, the budgets every design stage obeys.

generate() asks a model (ENGENIA_DESIGN_MODEL) for a contract, using the approved requirements
and packs/buoy/pack.yaml. A script (check_contract) then validates it against the Contract model
and checks that the budgets add up within the totals. When the check fails, the measured sums go
back to the model, up to `max_attempts` times (3 by default). If no model is set, the endpoint
fails, or the attempts run out, packs/buoy/default_contract.json is used and the interface says
"source": "default".

The model's one writable folder is contract/, and it may write only contract.json there.
No pass or fail decision comes from a model.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from engenia.agents.verification.stage import load_requirements
from engenia.contracts.models import Contract, RequirementStatus, StageContext
from engenia.core.hashing import file_hash
from engenia.harness.limits import Limits
from engenia.harness.llm import ChatFn, agent_model, chat, check_model_families
from engenia.harness.loop import run_agent
from engenia.harness.packs import pack_file, project_pack, skipped_stages
from engenia.harness.tools import Toolbox, ToolError

CONTRACT_FILE = "contract.json"
META_FILE = "meta.json"
SOURCES_FILE = "budget_sources.json"  # next to contract.json: the Contract model has no room for it

# Budgets derived from a total-mass requirement keep this share of the total back for what no
# stage budgets (wiring, fasteners, sealant) and as margin against the measured numbers.
MASS_RESERVE = 0.10
# Energy budgets derived from an average-power requirement keep this margin.
POWER_MARGIN = 0.10

SYSTEM_PROMPT = """\
You are the architect of an engineering project. Propose the contract: the budgets and interfaces
that the mechanical, electronics and firmware stages must obey. Write one file, contract.json,
with write_file, in exactly this shape:

{
  "version": 1,
  "budgets": {
    "mechanical":  {"mass_g": 300, "outer_envelope_mm": [160, 160, 220]},
    "electronics": {"mass_g": 60, "bay_mm": [70, 40, 22], "energy_mwh_per_day": 180},
    "firmware":    {"flash_kb": 512, "ram_kb": 64}
  },
  "interfaces": {
    "pin_map": {"HYDROPHONE_ADC": "GPIO34", "LORA_CS": "GPIO5"},
    "mounts":  {"board_holes_mm": [[3, 3], [67, 3], [3, 37], [67, 37]]}
  },
  "totals": {"mass_g_max": 400}
}

Derive every budget a requirement sets from that requirement (for example a total-mass limit
becomes totals.mass_g_max and is split between the mechanical and electronics budgets with some
margin); use the pack defaults only for budgets no requirement sets. A script re-derives those
budgets from the requirements after you finish, so a number that contradicts them is replaced.

The mechanical and electronics mass budgets must add up to no more than totals.mass_g_max, the
electronics bay must fit inside the mechanical envelope, the board holes must lie inside the bay,
and no two signals may share a pin. Use run_build to have a script check the file; it reports the
measured sums. When it reports no problems, reply with a short summary and no tool calls.
"""


# --- the script that decides ---------------------------------------------------------------------


@dataclass
class ContractReport:
    problems: list[str] = field(default_factory=list)
    sums: dict[str, float] = field(default_factory=dict)
    contract: Contract | None = None

    @property
    def ok(self) -> bool:
        return not self.problems


def check_contract(text: str, skip: frozenset[str] = frozenset()) -> ContractReport:
    """Validate a contract.json text. Pure function; the numbers it reports are measured.

    `skip` names the stages the pack leaves out (electronics, firmware): their budgets may be
    zero, and the bay, mounts and pins that only an electronics design needs are not checked."""
    report = ContractReport()
    try:
        contract = Contract.model_validate_json(text)
    except ValidationError as exc:
        for err in exc.errors():
            where = ".".join(str(p) for p in err["loc"])
            report.problems.append(f"{where}: {err['msg']}" if where else err["msg"])
        return report
    except ValueError as exc:
        report.problems.append(f"contract is not valid JSON: {exc}")
        return report

    mech, elec = contract.budgets.mechanical, contract.budgets.electronics
    firm = contract.budgets.firmware
    total = mech.mass_g + elec.mass_g
    maximum = contract.totals.mass_g_max
    report.sums = {
        "mass_g_sum": total,
        "mass_g_max": maximum,
        "mass_g_headroom": maximum - total,
    }
    if total > maximum:
        report.problems.append(
            f"mechanical {mech.mass_g} g + electronics {elec.mass_g} g = {total} g, "
            f"over totals.mass_g_max {maximum} g by {total - maximum} g"
        )

    positives = {
        "budgets.mechanical.mass_g": mech.mass_g,
        "totals.mass_g_max": maximum,
        **{
            f"budgets.mechanical.outer_envelope_mm[{i}]": v
            for i, v in enumerate(mech.outer_envelope_mm)
        },
    }
    if "electronics" not in skip:
        positives |= {
            "budgets.electronics.mass_g": elec.mass_g,
            "budgets.electronics.energy_mwh_per_day": elec.energy_mwh_per_day,
            **{f"budgets.electronics.bay_mm[{i}]": v for i, v in enumerate(elec.bay_mm)},
        }
    if "firmware" not in skip:
        positives |= {"budgets.firmware.flash_kb": firm.flash_kb,
                      "budgets.firmware.ram_kb": firm.ram_kb}
    for path, value in positives.items():
        if not value > 0:
            report.problems.append(f"{path} must be greater than 0, got {value}")
    for stage, value in (("electronics", elec.mass_g), ("firmware", firm.flash_kb)):
        if stage in skip and value != 0:
            report.problems.append(
                f"the pack skips {stage}, so its budget must be 0, got {value}"
            )

    if "electronics" not in skip:
        bay, envelope = sorted(elec.bay_mm), sorted(mech.outer_envelope_mm)
        if any(b > e for b, e in zip(bay, envelope, strict=True)):
            report.problems.append(
                f"electronics bay {list(elec.bay_mm)} mm does not fit the mechanical envelope "
                f"{list(mech.outer_envelope_mm)} mm"
            )

        bx, by, _ = elec.bay_mm
        for hx, hy in contract.interfaces.mounts.board_holes_mm:
            if not (0 <= hx <= bx and 0 <= hy <= by):
                report.problems.append(
                    f"board hole ({hx}, {hy}) lies outside the {bx} x {by} mm bay"
                )

    seen: dict[str, str] = {}
    for signal, pin in contract.interfaces.pin_map.items():
        if pin in seen:
            report.problems.append(f"pin {pin} is used by both {seen[pin]} and {signal}")
        seen.setdefault(pin, signal)

    if not report.problems:
        report.contract = contract
    return report


# --- budgets from this project's requirements ---------------------------------------------------


def _limit_max(requirement) -> float | None:
    limit = requirement.verify.limit
    if limit is not None and limit.max is not None:
        return float(limit.max)
    if limit is None and requirement.value is not None:
        return float(requirement.value)
    return None


def derive_budgets(
    contract: dict[str, Any],
    requirements: list,
    default: dict[str, Any],
    *,
    base: str = "pack default",
) -> tuple[dict[str, Any], dict[str, str]]:
    """Set the budgets that an approved requirement governs, from that requirement.

    Returns the new contract (a copy) and, for every budget path, where its number came from: a
    requirement id, or `base` ("pack default", or "model" for a budget the model proposed and no
    requirement governs). Pure function.

    - mass_budget with a maximum (the total mass): totals.mass_g_max is that maximum; the usable
      part (1 - MASS_RESERVE) is split between mechanical and electronics in the proportion of
      the pack default.
    - power_budget with a maximum in mW: energy_mwh_per_day = mW x 24 x (1 - POWER_MARGIN).
    """
    result = json.loads(json.dumps(contract))
    budgets, totals = result["budgets"], result["totals"]
    approved = [r for r in requirements if r.status == RequirementStatus.APPROVED]

    def strictest(check: str):
        found = [(v, r) for r in approved if r.verify.check == check
                 and (v := _limit_max(r)) is not None]
        return min(found, key=lambda pair: (pair[0], pair[1].id)) if found else None

    sources = dict.fromkeys(
        (
            "totals.mass_g_max",
            "budgets.mechanical.mass_g",
            "budgets.electronics.mass_g",
            "budgets.electronics.energy_mwh_per_day",
            "budgets.mechanical.outer_envelope_mm",
            "budgets.electronics.bay_mm",
            "budgets.firmware.flash_kb",
            "budgets.firmware.ram_kb",
        ),
        base,
    )

    mass = strictest("mass_budget")
    if mass is not None:
        total, requirement = mass
        d_mech = default["budgets"]["mechanical"]["mass_g"]
        d_elec = default["budgets"]["electronics"]["mass_g"]
        usable = total * (1 - MASS_RESERVE)
        electronics = round(usable * d_elec / (d_mech + d_elec), 1)
        totals["mass_g_max"] = total
        budgets["electronics"]["mass_g"] = electronics
        budgets["mechanical"]["mass_g"] = round(usable - electronics, 1)
        for path in ("totals.mass_g_max", "budgets.mechanical.mass_g",
                     "budgets.electronics.mass_g"):
            sources[path] = requirement.id
    power = strictest("power_budget")
    if power is not None and power[1].unit == "mW":
        milliwatts, requirement = power
        budgets["electronics"]["energy_mwh_per_day"] = round(
            milliwatts * 24 * (1 - POWER_MARGIN), 1
        )
        sources["budgets.electronics.energy_mwh_per_day"] = requirement.id
    return result, sources


def _feedback(report: ContractReport) -> str:
    return (
        "The contract did not pass the script check.\n"
        f"Measured: {json.dumps(report.sums, sort_keys=True)}\n"
        "Problems:\n- " + "\n- ".join(report.problems) + "\nFix contract.json."
    )


# --- the stage -----------------------------------------------------------------------------------


def _only_contract_file(relative: Path, content: str) -> None:
    if relative.as_posix() != CONTRACT_FILE:
        raise ToolError(f"you may write only {CONTRACT_FILE}, not {relative.as_posix()}")


def _pack_file(ctx: StageContext, name: str) -> Path:
    return pack_file(ctx, name)


def _task(ctx: StageContext) -> str:
    lines = ["Approved requirements:"]
    for requirement in sorted(load_requirements(ctx.spec_dir).values(), key=lambda r: r.id):
        if requirement.status != RequirementStatus.APPROVED:
            continue
        value = "" if requirement.value is None else f" ({requirement.value} {requirement.unit})"
        lines.append(f"- {requirement.id}: {requirement.statement}{value}")
    pack = _pack_file(ctx, "pack.yaml")
    if pack.is_file():
        lines += [
            "",
            f"Product pack (packs/{project_pack(ctx)}/pack.yaml):",
            pack.read_text("utf-8"),
        ]
    return "\n".join(lines)


class ArchitectStage:
    name = "architect"
    needs = ["verify_plan"]
    reads_contract = False
    checks: list[str] = []

    def __init__(
        self,
        *,
        chat_fn: ChatFn = chat,
        model: str | None = None,
        limits: Limits | None = None,
    ) -> None:
        self._chat_fn = chat_fn
        self._model = model
        self._limits = limits

    def generate(self, ctx: StageContext) -> None:
        source = "default"
        model = self._model or agent_model("architect")
        check_model_families(model=model)
        if model and self._propose(ctx, model):
            source = "model"
        default_path = _pack_file(ctx, "default_contract.json")
        if source == "default":
            ctx.contract_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(default_path, ctx.contract_path)

        # This project's approved requirements set the budgets they govern, whoever proposed the
        # rest; the record of where each number came from sits beside the contract.
        requirements = list(load_requirements(ctx.spec_dir).values())
        derived, sources = derive_budgets(
            json.loads(ctx.contract_path.read_text("utf-8")),
            requirements,
            json.loads(default_path.read_text("utf-8")),
            base="model" if source == "model" else "pack default",
        )
        if check_contract(json.dumps(derived), skipped_stages(ctx)).ok:
            ctx.contract_path.write_text(json.dumps(derived, indent=2) + "\n", encoding="utf-8")
        else:  # the derived numbers do not fit together: keep what was proposed, claim nothing
            sources = dict.fromkeys(sources, "model" if source == "model" else "pack default")
        (ctx.contract_path.parent / SOURCES_FILE).write_text(
            json.dumps(sources, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

        ctx.src_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ctx.contract_path, ctx.src_dir / CONTRACT_FILE)
        (ctx.src_dir / META_FILE).write_text(
            json.dumps({"source": source, "budget_sources": sources}, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _propose(self, ctx: StageContext, model: str) -> bool:
        """True when the model wrote a contract that passes check_contract."""
        folder = ctx.contract_path.parent
        folder.mkdir(parents=True, exist_ok=True)

        def check() -> ContractReport:
            if not ctx.contract_path.is_file():
                return ContractReport(problems=["contract.json has not been written"])
            return check_contract(ctx.contract_path.read_text("utf-8"), skipped_stages(ctx))

        def build() -> None:
            report = check()
            print(_feedback(report) if not report.ok else f"no problems; {report.sums}")

        ctx.contract_path.unlink(missing_ok=True)
        tools = Toolbox(folder, build=build, verdicts_path=None, write_guard=_only_contract_file)
        limits = self._limits or Limits.from_env()
        transcripts = ctx.build_dir / "transcripts"
        message = _task(ctx)
        history = None
        for attempt in range(1, limits.max_attempts + 1):
            agent = run_agent(
                SYSTEM_PROMPT,
                message,
                tools,
                model,
                limits,
                transcript_dir=transcripts,
                agent="architect",
                attempt=attempt,
                history=history,
                chat_fn=self._chat_fn,
            )
            if agent.status == "error":
                break
            history = agent.messages
            report = check()
            if report.ok:
                return True
            message = _feedback(report)
        ctx.contract_path.unlink(missing_ok=True)
        return False

    def build(self, ctx: StageContext) -> None:
        if not ctx.contract_path.is_file():
            raise FileNotFoundError(f"missing {ctx.contract_path}")
        report = check_contract(ctx.contract_path.read_text("utf-8"), skipped_stages(ctx))
        if not report.ok:
            raise ValueError(
                f"contract is not valid (measured {report.sums}):\n- "
                + "\n- ".join(report.problems)
            )
        if ctx.build_dir.exists():
            shutil.rmtree(ctx.build_dir)
        ctx.build_dir.mkdir(parents=True)
        shutil.copyfile(ctx.contract_path, ctx.build_dir / CONTRACT_FILE)

    def interface(self, ctx: StageContext) -> dict[str, Any]:
        report = check_contract(ctx.contract_path.read_text("utf-8"), skipped_stages(ctx))
        source, budget_sources = "unknown", {}
        try:
            meta = json.loads((ctx.src_dir / META_FILE).read_text("utf-8"))
            source, budget_sources = meta.get("source", source), meta.get("budget_sources", {})
        except (OSError, ValueError):
            pass
        return {
            "source": source,
            "budget_sources": budget_sources,
            "contract_sha256": file_hash(ctx.contract_path),
            **report.sums,
        }


__all__ = ["ArchitectStage", "ContractReport", "check_contract"]
