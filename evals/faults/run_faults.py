"""Planted-defect suite: ten faults a reviewer can plant, and the checks that must catch them.

Each fault folder here (``F01-wall-1mm`` ... ``F10-edited-verdict``) holds the smallest broken
piece of a project that one check should reject: build interfaces, for F08 a contract, for
F10 a hand-edited verdict file. ``run_faults.py`` copies each folder into a scratch project
under a temporary root, runs exactly the check that must catch the fault, and writes
``report.json``. It exits 0 only when every fault is caught (verdict ``fail``, or for the
edited verdict a gate problem); any miss, error or crash exits 1.

Usage::

    uv run python evals/faults/run_faults.py [--report PATH]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engenia.contracts.models import (  # noqa: E402
    CheckContext,
    Limit,
    StageContext,
    Verdict,
    VerdictResult,
)
from engenia.gate.gate import verdict_problem  # noqa: E402
from engenia.harness.checks import _load_verifier  # noqa: E402
from engenia.stages.integration.stage import IntegrationStage  # noqa: E402

DEFAULT_REPORT = Path(__file__).resolve().with_name("report.json")


@dataclass(frozen=True)
class Fault:
    """One planted defect and the check that must reject it.

    ``kind`` selects how the check runs: ``verifier`` calls ``verifiers/<check>.py`` through
    the harness loader, ``integration`` runs the deterministic integration stage and reads
    ``build/integration/verdict.json``, ``gate`` feeds the stored verdict to the gate's
    recomputation. ``stage`` and ``requirement`` are the check context's labels; ``limit``
    is ``None`` when the check computes its own result.
    """

    id: str
    folder: str
    description: str
    kind: str  # "verifier" | "integration" | "gate"
    check: str
    stage: str = ""
    requirement: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    limit: Limit | None = None


FAULTS: tuple[Fault, ...] = (
    Fault(
        "F01", "F01-wall-1mm", "hull wall 1 mm against the 3 mm minimum",
        kind="verifier", check="wall_thickness_min", stage="mechanical", requirement="R-004",
        limit=Limit(min=3),
    ),
    Fault(
        "F02", "F02-battery-too-small", "1 Wh battery against a 100 mW average load",
        kind="verifier", check="battery_capacity", stage="electronics", requirement="R-003",
        limit=Limit(min=7),
    ),
    Fault(
        "F03", "F03-hull-too-large", "260 mm hull against a 160 x 160 x 220 mm print bed",
        kind="verifier", check="print_bed_fit", stage="mechanical", requirement="R-002",
        params={"bed_mm": [160, 160, 220]}, limit=Limit(equals=1),
    ),
    Fault(
        "F04", "F04-mass-over-total", "350 g hull plus 100 g electronics over a 400 g total",
        kind="verifier", check="mass_budget", stage="integration", requirement="R-001",
        limit=Limit(max=400),
    ),
    Fault(
        "F05", "F05-negative-buoyancy", "400 g of hardware displacing only 100 cm3",
        kind="verifier", check="buoyancy_margin", stage="integration", requirement="R-005",
        params={"water_density_kg_m3": 1005}, limit=Limit(min=0.2),
    ),
    Fault(
        "F06", "F06-erc-error", "two electrical rule check errors",
        kind="verifier", check="erc_clean", stage="electronics", requirement="R-008",
        limit=Limit(max=0),
    ),
    # bay_fit is an electronics consistency check with no requirement of its own in the
    # example spec, so the verdict label borrows the electronics requirement id.
    Fault(
        "F07", "F07-board-larger-than-bay", "100 mm board in a 70 mm battery bay",
        kind="verifier", check="bay_fit", stage="electronics", requirement="R-003",
    ),
    Fault(
        "F08", "F08-pin-mismatch", "firmware drives LORA_CS on GPIO6; the contract says GPIO5",
        kind="integration", check="int_pins_match", stage="integration",
    ),
    Fault(
        "F09", "F09-low-detection-rate", "detection rate 0.5 against the 0.9 minimum",
        kind="verifier", check="detection_rate", stage="firmware", requirement="R-009",
        limit=Limit(min=0.9),
    ),
    Fault(
        "F10", "F10-edited-verdict", "hand-edited verdict: pass with 450 g against the 400 g max",
        kind="gate", check="mass_budget", stage="mechanical",
    ),
)


def _scratch_root(temp: Path) -> Path:
    """Prepare a temporary workspace: a copy of verifiers/ and an empty projects/ dir."""
    root = temp / "repo"
    shutil.copytree(
        ROOT / "verifiers",
        root / "verifiers",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    (root / "projects").mkdir(parents=True)
    return root


def _project_name(fault: Fault) -> str:
    """One project per fault, so faults sharing a scratch root cannot collide."""
    return fault.id.lower()


def _scratch_project(root: Path, fault: Fault) -> Path:
    """Copy the fault fixture into root/projects as a minimal project."""
    project_dir = root / "projects" / _project_name(fault)
    shutil.copytree(ROOT / "evals" / "faults" / fault.folder, project_dir)
    return project_dir


def _error(check: str, message: str) -> Verdict:
    return Verdict(
        check=check, requirement=None, measured=None, limit=Limit(),
        result=VerdictResult.ERROR, evidence_level="calculated", message=message,
    )


def _caught(fault: Fault, verdict: Verdict) -> bool:
    """A fault is caught when the check rejects it: verdict fail, or a gate problem."""
    if fault.kind == "gate":
        return verdict_problem(verdict) is not None
    return verdict.result == VerdictResult.FAIL


def _run_verifier_fault(fault: Fault, root: Path) -> Verdict:
    _scratch_project(root, fault)
    ctx = CheckContext(
        project=_project_name(fault),
        stage=fault.stage,  # type: ignore[arg-type]
        root=root,
        check=fault.check,
        requirement=fault.requirement,
        params=fault.params,
        limit=fault.limit if fault.limit is not None else Limit(),
    )
    run = _load_verifier(root, fault.check)
    return run(ctx)


def _run_integration_fault(fault: Fault, root: Path) -> Verdict:
    project_dir = _scratch_project(root, fault)
    ctx = StageContext(project=_project_name(fault), stage="integration", root=root)
    IntegrationStage().build(ctx)
    verdicts = json.loads((project_dir / "build/integration/verdict.json").read_text())
    for entry in verdicts:
        verdict = Verdict.model_validate(entry)
        if verdict.check == fault.check:
            return verdict
    raise ValueError(f"no {fault.check} verdict in build/integration/verdict.json")


def _run_gate_fault(fault: Fault, root: Path) -> Verdict:
    project_dir = _scratch_project(root, fault)
    path = project_dir / "build" / fault.stage / "verdict.json"
    entries = json.loads(path.read_text())
    for entry in entries:
        verdict = Verdict.model_validate(entry)
        if verdict.check == fault.check:
            return verdict
    raise ValueError(f"no {fault.check} verdict in {path}")


def _run_one(fault: Fault, root: Path) -> Verdict:
    runner = {
        "verifier": _run_verifier_fault,
        "integration": _run_integration_fault,
        "gate": _run_gate_fault,
    }[fault.kind]
    try:
        return runner(fault, root)
    except Exception as exc:  # noqa: BLE001 - a broken fixture is an uncaught fault
        return _error(fault.check, f"{type(exc).__name__}: {exc}")


def run_one(fault: Fault, root: Path | None = None) -> Verdict:
    """Run one fault's check and return the verdict. ``root`` defaults to a scratch copy."""
    if root is None:
        with tempfile.TemporaryDirectory(prefix="engenia-faults-") as temp:
            return run_one(fault, Path(temp))
    return _run_one(fault, _scratch_root(root))


def run_all(faults: tuple[Fault, ...] | None = None, report_path: Path | None = None) -> dict:
    """Run every fault and write the report. Returns the report payload."""
    faults = FAULTS if faults is None else faults
    entries: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="engenia-faults-") as temp:
        root = _scratch_root(Path(temp))
        for fault in faults:
            verdict = _run_one(fault, root)
            message = verdict.message
            if fault.kind == "gate" and message is None:
                message = verdict_problem(verdict)
            entries.append({
                "fault": fault.id,
                "expected_check": fault.check,
                "caught": _caught(fault, verdict),
                "message": message,
            })
    report = {
        "caught": sum(1 for entry in entries if entry["caught"]),
        "total": len(entries),
        "faults": entries,
    }
    destination = DEFAULT_REPORT if report_path is None else report_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.partition("\n\n")[0])
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT, help="report.json path")
    args = parser.parse_args(argv)
    report = run_all(report_path=args.report)
    for entry in report["faults"]:
        mark = "caught" if entry["caught"] else "MISSED"
        print(f"{entry['fault']} {entry['expected_check']:<20} {mark}")
        if not entry["caught"]:
            print(f"    {entry['fault']} not caught: {entry['message']}")
    print(f"{report['caught']}/{report['total']} faults caught")
    return 0 if report["caught"] == report["total"] else 1


if __name__ == "__main__":
    sys.exit(main())
