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
from engenia.harness.packs import pack_file, project_pack
from engenia.harness.tools import Toolbox, ToolError

CONTRACT_FILE = "contract.json"
META_FILE = "meta.json"

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


def check_contract(text: str) -> ContractReport:
    """Validate a contract.json text. Pure function; the numbers it reports are measured."""
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
        "budgets.electronics.mass_g": elec.mass_g,
        "budgets.electronics.energy_mwh_per_day": elec.energy_mwh_per_day,
        "budgets.firmware.flash_kb": firm.flash_kb,
        "budgets.firmware.ram_kb": firm.ram_kb,
        "totals.mass_g_max": maximum,
        **{
            f"budgets.mechanical.outer_envelope_mm[{i}]": v
            for i, v in enumerate(mech.outer_envelope_mm)
        },
        **{f"budgets.electronics.bay_mm[{i}]": v for i, v in enumerate(elec.bay_mm)},
    }
    for path, value in positives.items():
        if not value > 0:
            report.problems.append(f"{path} must be greater than 0, got {value}")

    bay, envelope = sorted(elec.bay_mm), sorted(mech.outer_envelope_mm)
    if any(b > e for b, e in zip(bay, envelope, strict=True)):
        report.problems.append(
            f"electronics bay {list(elec.bay_mm)} mm does not fit the mechanical envelope "
            f"{list(mech.outer_envelope_mm)} mm"
        )

    bx, by, _ = elec.bay_mm
    for hx, hy in contract.interfaces.mounts.board_holes_mm:
        if not (0 <= hx <= bx and 0 <= hy <= by):
            report.problems.append(f"board hole ({hx}, {hy}) lies outside the {bx} x {by} mm bay")

    seen: dict[str, str] = {}
    for signal, pin in contract.interfaces.pin_map.items():
        if pin in seen:
            report.problems.append(f"pin {pin} is used by both {seen[pin]} and {signal}")
        seen.setdefault(pin, signal)

    if not report.problems:
        report.contract = contract
    return report


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
        if source == "default":
            ctx.contract_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(_pack_file(ctx, "default_contract.json"), ctx.contract_path)

        ctx.src_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ctx.contract_path, ctx.src_dir / CONTRACT_FILE)
        (ctx.src_dir / META_FILE).write_text(
            json.dumps({"source": source}, sort_keys=True) + "\n", encoding="utf-8"
        )

    def _propose(self, ctx: StageContext, model: str) -> bool:
        """True when the model wrote a contract that passes check_contract."""
        folder = ctx.contract_path.parent
        folder.mkdir(parents=True, exist_ok=True)

        def check() -> ContractReport:
            if not ctx.contract_path.is_file():
                return ContractReport(problems=["contract.json has not been written"])
            return check_contract(ctx.contract_path.read_text("utf-8"))

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
        report = check_contract(ctx.contract_path.read_text("utf-8"))
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
        report = check_contract(ctx.contract_path.read_text("utf-8"))
        source = "unknown"
        try:
            source = json.loads((ctx.src_dir / META_FILE).read_text("utf-8")).get("source", source)
        except (OSError, ValueError):
            pass
        return {
            "source": source,
            "contract_sha256": file_hash(ctx.contract_path),
            **report.sums,
        }


__all__ = ["ArchitectStage", "ContractReport", "check_contract"]
