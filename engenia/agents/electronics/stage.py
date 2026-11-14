"""Electronics selection, reproducible SKiDL build, and build facts."""

from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

from engenia.agents.electronics.facts import Facts, load_facts, required_functions
from engenia.agents.electronics.selection import assembly_size, power_facts, propose_selection
from engenia.contracts.models import ChangeRequest, Contract, Requirement, StageContext
from engenia.core import events
from engenia.harness.packs import pack_file, project_pack
from engenia.harness.proposer import propose_electronics_selection, proposer_fact


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _request_bay(ctx: StageContext, selection: dict) -> None:
    requested = selection["requested_bay_mm"]
    if requested is None:
        return
    directory = ctx.contract_path.parent / "change_requests"
    directory.mkdir(parents=True, exist_ok=True)
    for path in sorted(directory.glob("CR-*.json")):
        request = ChangeRequest.model_validate_json(path.read_text(encoding="utf-8"))
        if (
            request.from_stage == "electronics"
            and request.status == "open"
            and request.path == "budgets.electronics.bay_mm"
            and request.requested == requested
        ):
            return
    reason = (
        f"Battery {selection['parts']['battery']} and board need {requested} mm; "
        f"calculated endurance {selection['endurance_days']:.6f} days for "
        f"{selection['target_endurance_days']:g} days requested."
    )
    if selection["endurance_days"] < selection["target_endurance_days"]:
        reason += " No approved battery meets endurance; bay growth alone is insufficient."
    # Exclusive create preserves requests concurrently submitted by another lane.
    for number in range(1, 1000):
        identifier = f"CR-{number:03d}"
        request = ChangeRequest(
            id=identifier,
            from_stage="electronics",
            path="budgets.electronics.bay_mm",
            requested=requested,
            reason=reason,
            status="open",
        )
        try:
            with (directory / f"{identifier}.json").open("x", encoding="utf-8") as stream:
                stream.write(request.model_dump_json(indent=2) + "\n")
            return
        except FileExistsError:
            continue
    raise RuntimeError("change request id space exhausted")


# Roles in the pack's part catalogue, in the words a reader of the live log expects.
ROLE_TITLES = {
    "lora": "Radio", "audio": "Sensor (audio)", "microcontroller": "Microcontroller",
    "regulator": "Regulator", "battery": "Battery",
}


def _emit_selection_decisions(
    ctx: StageContext, contract: Contract, requirements: list[Requirement], facts: Facts,
    selection: dict,
) -> None:
    """Deterministic fallback: one decision event per part role, the board and the bay."""
    by_id = {p.id: p for p in facts.parts}
    budget_ids = events.relevant_requirements(
        requirements, "electronics", ("power_budget", "mass_budget"))
    battery_ids = events.relevant_requirements(
        requirements, "electronics", ("battery_capacity", "power_budget"))
    fit_ids = events.relevant_requirements(requirements, "electronics", ("bay_fit",))
    for role, part_id in sorted(selection["parts"].items()):
        part = by_id[part_id]
        title = ROLE_TITLES.get(role, role.replace("_", " ").capitalize())
        if role == "battery":
            target = selection["target_endurance_days"]
            promise = f"the {target:g} day target" if target else "no endurance target"
            events.emit_decision(
                ctx.root, ctx.stage, title=title, choice=part_id,
                reason=(f"{part_id}: {part.capacity_mah:g} mAh gives "
                        f"{selection['endurance_days']:.2f} days against {promise}; "
                        "the smallest approved cell that meets it"),
                requirements=battery_ids,
                numbers={"capacity_mah": part.capacity_mah,
                         "endurance_days": round(selection["endurance_days"], 6),
                         "target_days": target, "mass_g": part.mass_g},
            )
            continue
        events.emit_decision(
            ctx.root, ctx.stage, title=title, choice=part_id,
            reason=(f"{part_id}: lowest id among approved {role} parts that run at "
                    f"{part.supply_voltage_v[0]:g}-{part.supply_voltage_v[1]:g} V; "
                    f"{part.active_current_ma:g} mA active, {part.mass_g:g} g"),
            requirements=budget_ids,
            numbers={"active_current_ma": part.active_current_ma,
                     "sleep_current_ma": part.sleep_current_ma, "mass_g": part.mass_g},
        )
    board, bay = selection["board_mm"], contract.budgets.electronics.bay_mm
    assembly, asked = selection["assembly_mm"], selection["requested_bay_mm"]
    fits = asked is None
    events.emit_decision(
        ctx.root, ctx.stage, title="Board size vs bay",
        choice=f"board {board[0]:g} x {board[1]:g} x {board[2]:g} mm",
        reason=(f"battery and board stack to {assembly[0]:g} x {assembly[1]:g} x {assembly[2]:g} "
                f"mm, which {'fits' if fits else 'does not fit'} the "
                f"{bay[0]:g} x {bay[1]:g} x {bay[2]:g} mm bay"
                + ("" if fits else f"; a bay of {asked} mm is requested")),
        requirements=fit_ids,
        numbers={"board_x_mm": board[0], "board_y_mm": board[1], "board_z_mm": board[2],
                 "assembly_z_mm": assembly[2], "bay_z_mm": bay[2]},
    )


class ElectronicsStage:
    name = "electronics"
    needs = ["architect"]
    reads_contract = True
    checks = ["power_budget", "battery_capacity", "erc_clean", "mass_budget", "bay_fit"]

    def generate(self, ctx: StageContext) -> None:
        contract_path = ctx.contract_path
        if not contract_path.is_file():
            contract_path = pack_file(ctx, "default_contract.json")
        contract = Contract.model_validate_json(contract_path.read_text(encoding="utf-8"))
        requirements = [
            Requirement.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
            for path in sorted(ctx.spec_dir.glob("R-*.yaml"))
        ]
        facts = load_facts(pack_file(ctx, "parts/facts.toml"))
        selection = propose_electronics_selection(
            ctx, contract, requirements, facts, propose_selection
        )
        if proposer_fact(ctx) == "deterministic":
            _emit_selection_decisions(ctx, contract, requirements, facts, selection)
        selection["pins_used"] = dict(sorted(contract.interfaces.pin_map.items()))
        selection["board_holes_mm"] = [
            list(hole) for hole in contract.interfaces.mounts.board_holes_mm
        ]
        selection["contract_version"] = contract.version
        # Snapshot catalogue/profile: build never reselects against changed inputs.
        buoy = project_pack(ctx) == "buoy"
        selection["facts"] = facts.model_dump(
            mode="json", exclude={"design": {"active_modes"}} if buoy else None
        )
        ctx.src_dir.mkdir(parents=True, exist_ok=True)
        (ctx.src_dir / "selection.yaml").write_text(
            yaml.safe_dump(selection, sort_keys=True), encoding="utf-8"
        )
        circuit = "circuit_buoy.py" if buoy else "circuit_template.py"
        shutil.copyfile(Path(__file__).with_name(circuit), ctx.src_dir / "circuit.py")
        _request_bay(ctx, selection)

    def build(self, ctx: StageContext) -> None:
        ctx.build_dir.mkdir(parents=True, exist_ok=True)
        for name in ("build_facts.json", "circuit.net", "erc.json", "erc.txt", "bom.csv"):
            (ctx.build_dir / name).unlink(missing_ok=True)
        selection = yaml.safe_load((ctx.src_dir / "selection.yaml").read_text(encoding="utf-8"))
        facts = Facts.model_validate(selection["facts"])
        by_id = {p.id: p for p in facts.parts}
        parts = {role: by_id[part_id] for role, part_id in selection["parts"].items()}
        if set(parts) != set(required_functions(facts)):
            raise ValueError(
                "selection must contain exactly the pack's part functions "
                f"{sorted(required_functions(facts))}"
            )
        if any(role != part.function for role, part in parts.items()):
            raise ValueError("selected part function mismatch")
        profile = facts.design.model_dump()
        profile["duty_cycle"] = selection["duty_cycle"]
        profile = type(facts.design).model_validate(profile)
        result = subprocess.run(
            [
                sys.executable,
                str((ctx.src_dir / "circuit.py").resolve()),
                str(ctx.build_dir.resolve()),
            ],
            cwd=ctx.build_dir,
            env={**os.environ, "PYTHONHASHSEED": "0"},
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
            check=False,
        )
        log = result.stdout + result.stderr
        # Replace nested paths before the root so their specific labels survive.
        for path, label in (
            (ctx.build_dir, "<build>"),
            (ctx.src_dir, "<src>"),
            (ctx.root, "<root>"),
        ):
            log = log.replace(str(path.resolve()), label)
        (ctx.build_dir / "build.log").write_text(log, encoding="utf-8")
        if result.returncode:
            raise RuntimeError(
                f"SKiDL build failed; see {ctx.build_dir / 'build.log'}\n{result.stderr[-4000:]}"
            )
        erc = json.loads((ctx.build_dir / "erc.json").read_text(encoding="utf-8"))
        built = {
            "mass_g": sum(p.mass_g for p in parts.values()) + facts.design.support_mass_g,
            "board_mm": list(facts.design.board_mm),
            "battery_mm": list(parts["battery"].size_mm),
            "board_holes_mm": selection["board_holes_mm"],
            "contract_version": selection["contract_version"],
            "duty_cycle": dict(profile.duty_cycle),
            "assembly_mm": assembly_size(parts["battery"], facts, selection["bay_mm"]),
            **power_facts(parts, facts, profile.duty_cycle),
            "pins_used": erc["pins_used"],
            "internal_pins": erc["internal_pins"],
            # SKiDL reports unconnected/undriven pins as warnings; count those too.
            "erc_errors": erc["errors"] + erc["warnings"],
            "erc_warnings": erc["warnings"],
        }
        with (ctx.build_dir / "bom.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream, lineterminator="\n")
            writer.writerow(["id", "function", "quantity", "mass_g", "source"])
            for part in sorted(parts.values(), key=lambda p: p.id):
                writer.writerow([part.id, part.function, 1, part.mass_g, part.source])
            writer.writerow(
                [
                    "support-assembly",
                    "pcb/passives/connectors",
                    1,
                    facts.design.support_mass_g,
                    facts.design.source,
                ]
            )
        _write_json(ctx.build_dir / "build_facts.json", built)

    def interface(self, ctx: StageContext) -> dict:
        facts = json.loads((ctx.build_dir / "build_facts.json").read_text(encoding="utf-8"))
        facts["proposer"] = proposer_fact(ctx)
        return facts
