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

from engenia.agents.electronics.facts import Facts, load_facts
from engenia.agents.electronics.selection import assembly_size, power_facts, propose_selection
from engenia.contracts.models import ChangeRequest, Contract, Requirement, StageContext
from engenia.harness.proposer import propose_electronics_selection


def _pack_file(ctx: StageContext, name: str) -> Path:
    local = ctx.root / "packs" / "buoy" / name
    repo = Path(__file__).resolve().parents[3]
    return local if local.is_file() else repo / "packs" / "buoy" / name


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


class ElectronicsStage:
    name = "electronics"
    needs = ["architect"]
    reads_contract = True
    checks = ["power_budget", "battery_capacity", "erc_clean", "mass_budget", "bay_fit"]

    def generate(self, ctx: StageContext) -> None:
        contract_path = ctx.contract_path
        if not contract_path.is_file():
            contract_path = _pack_file(ctx, "default_contract.json")
        contract = Contract.model_validate_json(contract_path.read_text(encoding="utf-8"))
        requirements = [
            Requirement.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
            for path in sorted(ctx.spec_dir.glob("R-*.yaml"))
        ]
        facts = load_facts(_pack_file(ctx, "parts/facts.toml"))
        selection = propose_electronics_selection(
            ctx, contract, requirements, facts, propose_selection
        )
        selection["pins_used"] = dict(sorted(contract.interfaces.pin_map.items()))
        selection["board_holes_mm"] = [
            list(hole) for hole in contract.interfaces.mounts.board_holes_mm
        ]
        selection["contract_version"] = contract.version
        # Snapshot catalogue/profile: build never reselects against changed inputs.
        selection["facts"] = facts.model_dump(mode="json")
        ctx.src_dir.mkdir(parents=True, exist_ok=True)
        (ctx.src_dir / "selection.yaml").write_text(
            yaml.safe_dump(selection, sort_keys=True), encoding="utf-8"
        )
        shutil.copyfile(Path(__file__).with_name("circuit_template.py"), ctx.src_dir / "circuit.py")
        _request_bay(ctx, selection)

    def build(self, ctx: StageContext) -> None:
        ctx.build_dir.mkdir(parents=True, exist_ok=True)
        for name in ("build_facts.json", "circuit.net", "erc.json", "erc.txt", "bom.csv"):
            (ctx.build_dir / name).unlink(missing_ok=True)
        selection = yaml.safe_load((ctx.src_dir / "selection.yaml").read_text(encoding="utf-8"))
        facts = Facts.model_validate(selection["facts"])
        by_id = {p.id: p for p in facts.parts}
        parts = {role: by_id[part_id] for role, part_id in selection["parts"].items()}
        if set(parts) != {"microcontroller", "lora", "audio", "battery", "regulator"}:
            raise ValueError("selection must contain all five part functions")
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
        return json.loads((ctx.build_dir / "build_facts.json").read_text(encoding="utf-8"))
