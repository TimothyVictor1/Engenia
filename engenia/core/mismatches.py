"""build/mismatches.json: where the stages' published facts disagree with each other or with
the contract.

Two sources, both read from files so the result does not depend on how the run was scheduled:

* the engine's own checks of each stage's interface.json against the contract (contract version,
  budgets, pin map, mounting holes). A stage that did not publish a fact is not judged on it.
* the integration stage's own list (build/integration/mismatches.json), copied across.

Each entry is {"check", "stages", "message", "source"}. The list is sorted, so equal inputs give
equal bytes.
"""

from __future__ import annotations

import json
from pathlib import Path

from engenia.contracts.models import Contract

MISMATCHES_FILE = "mismatches.json"


def _facts(build: Path, stage: str) -> dict:
    try:
        data = json.loads((build / stage / "interface.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    facts = data.get("facts") if isinstance(data, dict) else None
    return facts if isinstance(facts, dict) else {}


def _number(facts: dict, name: str) -> float | None:
    value = facts.get(name)
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def find_mismatches(build: Path, contract: Contract) -> list[dict]:
    found: list[dict] = []

    def add(check: str, stages: list[str], message: str, source: str = "engine") -> None:
        found.append({"check": check, "stages": stages, "message": message, "source": source})

    facts = {s: _facts(build, s) for s in ("mechanical", "electronics", "firmware")}
    for stage, own in facts.items():
        version = own.get("contract_version")
        if version is not None and version != contract.version:
            add("contract_version", [stage],
                f"{stage} was built for contract version {version}, the contract is "
                f"version {contract.version}")

    budgets = contract.budgets
    limits = [
        ("mechanical", "dry_mass_g", budgets.mechanical.mass_g),
        ("electronics", "mass_g", budgets.electronics.mass_g),
        ("firmware", "flash_kb", budgets.firmware.flash_kb),
        ("firmware", "ram_kb", budgets.firmware.ram_kb),
    ]
    for stage, name, limit in limits:
        value = _number(facts[stage], name)
        if value is not None and value > limit:
            add(f"budget_{name}", [stage], f"{stage} {name} {value:g} is over its budget {limit:g}")

    pins = contract.interfaces.pin_map
    for stage in ("electronics", "firmware"):
        used = facts[stage].get("pins_used")
        if used is not None and used != pins:
            add("pin_map", [stage], f"{stage} pins_used differ from the contract pin_map")
    holes = [list(h) for h in contract.interfaces.mounts.board_holes_mm]
    reported = facts["electronics"].get("board_holes_mm")
    if reported is not None and reported != holes:
        add("board_holes_mm", ["electronics"],
            "electronics board_holes_mm differ from the contract")

    try:
        integration = json.loads((build / "integration" / MISMATCHES_FILE).read_text("utf-8"))
    except (OSError, ValueError):
        integration = []
    for entry in integration if isinstance(integration, list) else []:
        if isinstance(entry, dict):
            add(str(entry.get("check")), list(entry.get("stages") or []),
                str(entry.get("message")), "integration")

    return sorted(found, key=lambda e: (e["source"], e["check"], e["stages"], e["message"]))


def write_mismatches(build: Path, contract: Contract) -> list[dict]:
    found = find_mismatches(build, contract)
    build.mkdir(parents=True, exist_ok=True)
    (build / MISMATCHES_FILE).write_text(json.dumps(found, indent=2) + "\n", encoding="utf-8")
    return found
