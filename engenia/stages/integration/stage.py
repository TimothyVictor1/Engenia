"""Deterministic assembly and cross-stage checks, without an agent or CAD."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from engenia.contracts.models import Contract, StageContext, Verdict

STAGES = ("mechanical", "electronics", "firmware")


def _fact(facts: dict, stage: str, name: str):
    data = facts.get(stage, {})
    if data.get("stub") or data.get(name) is None:
        raise ValueError(f"{stage}: missing {name}" + (" (stub)" if data.get("stub") else ""))
    return data[name]


def _number(facts: dict, stage: str, name: str) -> float:
    value = _fact(facts, stage, name)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{stage}: invalid {name}")
    if value < 0:
        raise ValueError(f"{stage}: invalid {name}")
    return value


def _vector(facts: dict, stage: str, name: str) -> list:
    value = _fact(facts, stage, name)
    if not isinstance(value, list) or len(value) != 3 or any(
        isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
        for v in value
    ):
        raise ValueError(f"{stage}: invalid {name}")
    return value


def _smallest_clearance_mm(facts: dict) -> float:
    """Each component must fit the bay with 1 mm on both sides of each axis."""
    bay = _vector(facts, "mechanical", "battery_bay_mm")
    board = _vector(facts, "electronics", "board_mm")
    battery = _vector(facts, "electronics", "battery_mm")
    if any(size <= 0 for item in (board, battery, bay) for size in item):
        raise ValueError("mechanical/electronics: invalid battery_bay_mm, board_mm or battery_mm")
    return min((space - size) / 2 for item in (board, battery)
               for size, space in zip(item, bay, strict=True))


def int_board_fits_bay(facts: dict, contract: dict) -> bool:
    """Each component needs at least 1 mm clearance per side on every axis."""
    return _smallest_clearance_mm(facts) >= 1.0


def int_mounts_match(facts: dict, contract: dict) -> bool:
    return _fact(facts, "electronics", "board_holes_mm") == (
        contract["interfaces"]["mounts"]["board_holes_mm"]
    )


def int_mass_total(facts: dict, contract: dict) -> bool:
    return (_number(facts, "mechanical", "dry_mass_g")
            + _number(facts, "electronics", "mass_g")) <= contract["totals"]["mass_g_max"]


def int_pins_match(facts: dict, contract: dict) -> bool:
    firmware = _fact(facts, "firmware", "pins_used")
    electronics = _fact(facts, "electronics", "pins_used")
    return firmware == electronics == contract["interfaces"]["pin_map"]


def int_contract_version(facts: dict, contract: dict) -> bool:
    versions = [_fact(facts, stage, "contract_version") for stage in STAGES]
    return all(version == contract["version"] for version in versions)


CHECKS = (
    (int_board_fits_bay, ("mechanical", "electronics")),
    (int_mounts_match, ("electronics",)),
    (int_mass_total, ("mechanical", "electronics")),
    (int_pins_match, ("electronics", "firmware")),
    (int_contract_version, STAGES),
)


def _write(path: Path, value) -> None:
    path.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n",
                    encoding="utf-8")


def _assembly(facts: dict) -> dict:
    result = {"total_mass_g": None, "centre_of_mass_mm": None, "displaced_volume_cm3": None}
    try:
        hull = _number(facts, "mechanical", "dry_mass_g")
        electronics = _number(facts, "electronics", "mass_g")
        result["total_mass_g"] = hull + electronics
        centre = _vector(facts, "mechanical", "centre_of_mass_mm")
        bay = _vector(facts, "mechanical", "battery_bay_centre_mm")
        if hull + electronics > 0:
            result["centre_of_mass_mm"] = [
                (hull * h + electronics * e) / (hull + electronics)
                for h, e in zip(centre, bay, strict=True)
            ]
    except ValueError:
        pass
    try:
        result["displaced_volume_cm3"] = _number(facts, "mechanical", "displaced_volume_cm3")
    except ValueError:
        pass
    return result


class IntegrationStage:
    name = "integration"
    needs = list(STAGES)
    reads_contract = True
    checks: list[str] = []  # These checks run here, not through the verifier loader.

    def generate(self, ctx: StageContext) -> None:
        ctx.src_dir.mkdir(parents=True, exist_ok=True)

    def build(self, ctx: StageContext) -> None:
        path = ctx.contract_path
        if not path.is_file():
            path = ctx.root / "packs" / "buoy" / "default_contract.json"
            if not path.is_file():
                path = Path(__file__).resolve().parents[3] / "packs/buoy/default_contract.json"
        contract = Contract.model_validate_json(path.read_text()).model_dump(mode="json")
        facts = {}
        for stage in STAGES:
            try:
                data = json.loads(ctx.stage_interface_path(stage).read_text())
                facts[stage] = (
                    data["facts"] if isinstance(data, dict)
                    and isinstance(data.get("facts"), dict) else {}
                )
            except (OSError, ValueError, KeyError, TypeError):
                facts[stage] = {}
        assembly = _assembly(facts)
        artifact = "sha256:" + hashlib.sha256(
            json.dumps({"facts": facts, "contract": contract}, sort_keys=True).encode()
        ).hexdigest()
        verdicts, mismatches = [], []
        for check, stages in CHECKS:
            name = check.__name__
            message = None
            measured = None
            mass_check = name == "int_mass_total"
            if mass_check:
                limit = {"max": contract["totals"]["mass_g_max"]}
            elif name == "int_board_fits_bay":
                limit = {"min": 1.0}
            else:
                limit = {"equals": 1.0}
            try:
                if name == "int_board_fits_bay":
                    measured = _smallest_clearance_mm(facts)
                    passed = measured >= 1.0
                else:
                    passed = check(facts, contract)
                    measured = assembly["total_mass_g"] if mass_check else float(passed)
                result = "pass" if passed else "fail"
                if result == "fail":
                    message = f"{name}: mismatch"
            except ValueError as exc:
                result, message = "error", str(exc)
            involved = list(stages)
            if name == "int_contract_version" and result != "pass":
                involved = [s for s in stages if facts[s].get("stub")
                            or facts[s].get("contract_version") != contract["version"]]
            if result != "pass":
                mismatches.append({"check": name, "stages": involved, "message": message})
            verdicts.append(Verdict(
                check=name, requirement="R-001" if mass_check else None,
                measured=measured, limit=limit,
                result=result, evidence_level="calculated", artifact=artifact, message=message,
            ).model_dump(mode="json"))
        assembly["failed_checks"] = [v["check"] for v in verdicts if v["result"] != "pass"]
        ctx.build_dir.mkdir(parents=True, exist_ok=True)
        _write(ctx.build_dir / "assembly.json", assembly)
        _write(ctx.build_dir / "verdict.json", verdicts)
        _write(ctx.build_dir / "mismatches.json", mismatches)

    def interface(self, ctx: StageContext) -> dict:
        return json.loads((ctx.build_dir / "assembly.json").read_text())
