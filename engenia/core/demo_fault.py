"""`engenia demo-fault <project> <F-id>`: plant a fault from `evals/faults/` in a scratch copy,
run it there, and copy the event logs back, so the dashboard shows the gate catching the fault
and the repair loop reacting.

The real project is never touched: the fault runs in a throw-away root, and the only thing
written under the real root is `.engenia/events/` (new event logs for the copied runs). The
scratch copy needs `verifiers/` and the pack templates because the scratch run executes the
real pipeline.

Two fault kinds exist:

- source faults edit `src/` and remove the stage's `.generated-from` marker, so the engine
  rebuilds that stage from the injected source. `run_with_repair` then repairs it (with a
  model) or writes proposals (without one).
- verdict faults edit a stored `build/<stage>/verdict.json`, so the gate's G2 catches the
  tampering. Nothing can repair that; the gate is run once to show the catch.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Any

import yaml

from engenia.core.engine import RunReport, execute
from engenia.core.errors import CoreError
from engenia.core.repair_loop import RepairReport, run_with_repair
from engenia.gate.gate import GateResult, run_gate

FAULTS_DIR = Path(__file__).resolve().parents[2] / "evals" / "faults"
COPY_DIRS = ("verifiers", "packs", "containers")
_IGNORED = {"__pycache__", ".git", ".venv"}
_FAULT_ID = re.compile(r"^F\d{2}(-|$)")


class DemoError(CoreError):
    """The demo cannot run: unknown fault, a fault with no recipe, or a broken base project."""


@dataclass
class DemoResult:
    fault_id: str
    caught: bool
    detail: str
    copied_runs: list[int] = field(default_factory=list)
    scratch: Path | None = None  # removed when the demo ends; the logs live on in the root


def fault_ids() -> list[str]:
    return sorted(p.name for p in FAULTS_DIR.iterdir() if _FAULT_ID.match(p.name))


def _suite():
    """Load evals/faults/run_faults.py by path, without importing the evals package."""
    path = FAULTS_DIR / "run_faults.py"
    spec = spec_from_file_location("engenia_demo_faults", path)
    if spec is None or spec.loader is None:
        raise DemoError(f"cannot load {path}")
    module = module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations against sys.modules
    spec.loader.exec_module(module)
    return module


def _fault_key(folder: str) -> str:
    """The F-id of a fault folder: 'F01-wall-1mm' -> 'F01'."""
    return folder.split("-")[0].upper()


def _resolve(fault: str) -> Path:
    wanted = fault.strip().upper()
    for name in fault_ids():
        if wanted in (name.upper(), _fault_key(name)):
            return FAULTS_DIR / name
    raise DemoError(
        f"unknown fault {fault!r}; known faults: {', '.join(fault_ids())}"
    )


# --- recipes ---------------------------------------------------------------------------------


def _set(data: dict, dotted: str, value: Any) -> None:
    keys = dotted.split(".")
    node = data
    for key in keys[:-1]:
        node = node.setdefault(key, {})
    node[keys[-1]] = value


def _edit_yaml(project_dir: Path, rel: str, updates: dict[str, Any]) -> None:
    """Update keys (dotted paths for nested ones) in one yaml file under the scratch project."""
    path = project_dir / rel
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for key, value in updates.items():
        _set(data, key, value)
    path.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")


def _unmark(project_dir: Path, stage: str) -> None:
    """Drop the marker, so the engine rebuilds the stage from the (injected) source."""
    marker = project_dir / "src" / ".generated-from" / stage
    marker.unlink(missing_ok=True)


def _source(stage: str, rel: str, updates: dict[str, Any]) -> Callable[[Path], None]:
    def inject(project_dir: Path) -> None:
        _edit_yaml(project_dir, rel, updates)
        _unmark(project_dir, stage)

    return inject


def _f08(project_dir: Path) -> None:
    """Swap the LoRa CS pin in firmware config and header; the interface then disagrees
    with the pins.py driver and pin_conflicts fails."""
    _edit_yaml(project_dir, "src/firmware/config.json", {"pins_used.LORA_CS": "GPIO6"})
    header = project_dir / "src/firmware/include/pins.h"
    header.write_text(
        header.read_text(encoding="utf-8").replace("GPIO5", "GPIO6"), encoding="utf-8"
    )
    _unmark(project_dir, "firmware")


def _f09(project_dir: Path) -> None:
    """Raise the detector threshold so the detection-rate check fails at integration."""
    _edit_yaml(project_dir, "src/firmware/config.json", {"detector.threshold": 0.6})
    _unmark(project_dir, "firmware")


def _f10(project_dir: Path) -> None:
    """Edit the stored mechanical verdict: a pass whose own numbers say fail. The gate's G2
    recomputation catches the contradiction; no repair can fix a verdict."""
    verdict = project_dir / "build/mechanical/verdict.json"
    entries = json.loads(verdict.read_text(encoding="utf-8"))
    for entry in entries:
        if entry.get("check") == "wall_thickness_min":
            entry["measured"] = entry["limit"]["min"] - 1.0  # the recompute will say fail
            entry["result"] = "pass"
            entry["message"] = None
    verdict.write_text(json.dumps(entries, indent=2, sort_keys=True) + "\n", encoding="utf-8")


RECIPES: dict[str, tuple[Callable[[Path], None], str]] = {
    "F01": (_source("mechanical", "src/mechanical/params.yaml", {"wall_thickness_mm": 1.0}),
            "source"),
    "F02": (_source("electronics", "src/electronics/selection.yaml",
                    {"duty_cycle.sample": 1.0, "duty_cycle.sleep": 1.0,
                     "duty_cycle.transmit": 1.0}),
            "source"),
    "F03": (_source("mechanical", "src/mechanical/params.yaml", {"outer_diameter_mm": 260}),
            "source"),
    "F04": (_source("mechanical", "src/mechanical/params.yaml", {"outer_diameter_mm": 215}),
            "source"),
    "F05": (_source("mechanical", "src/mechanical/params.yaml", {"height_mm": 25}),
            "source"),
    "F07": (_source("electronics", "src/electronics/selection.yaml",
                    {"facts.design.board_mm": [100.0, 60.0, 1.6]}),
            "source"),
    "F08": (_f08, "source"),
    "F09": (_f09, "source"),
    "F10": (_f10, "verdict"),
}


def _recipe(fault_dir: Path) -> tuple[Callable[[Path], None], str]:
    key = _fault_key(fault_dir.name)
    if key not in RECIPES:
        raise DemoError(
            f"{fault_dir.name} has no injection recipe for demo-fault yet"
        )
    return RECIPES[key]


# --- the demo --------------------------------------------------------------------------------


def _copytree(source: Path, target: Path) -> None:
    shutil.copytree(
        source, target,
        ignore=shutil.ignore_patterns(*_IGNORED),
        dirs_exist_ok=True,
    )


def run_demo_fault(project: str, fault: str, root: Path) -> DemoResult:
    """Plant and run one fault in a scratch copy; returns what the gate did with it."""
    root = Path(root)
    fault_dir = _resolve(fault)
    inject, kind = _recipe(fault_dir)
    if not (root / "projects" / project).is_dir():
        raise DemoError(f"no project {project!r} under {root}")

    scratch = Path(tempfile.mkdtemp(prefix="engenia-demo-"))
    result = DemoResult(fault_id=fault_dir.name, caught=False, detail="", scratch=scratch)
    _copytree(root / "projects" / project, scratch / "projects" / project)
    for name in COPY_DIRS:
        if (root / name).is_dir():
            _copytree(root / name, scratch / name)
    try:
        if kind == "source":
            _demo_source(project, scratch, inject, result)
        else:
            _demo_verdict(project, scratch, inject, result)
    finally:
        _copy_events(project, root, scratch, result)
        shutil.rmtree(scratch, ignore_errors=True)
    return result


def _clean_run(project: str, scratch: Path) -> RunReport:
    """A first pass that must succeed: it writes src/, build/ and the cache the fault needs."""
    report = execute(project, scratch)
    if not report.ok:
        failed = "; ".join(f"{s}: {m}" for s, m in report.failed.items())
        raise DemoError(f"the project must pass before a fault can be planted: {failed}")
    return report


def _demo_source(
    project: str,
    scratch: Path,
    inject: Callable[[Path], None],
    result: DemoResult,
) -> None:
    _clean_run(project, scratch)
    inject(scratch / "projects" / project)
    faulted = execute(project, scratch)  # rebuilds the injected stage; it fails its checks
    gate = run_gate(project, scratch)  # the catch, logged to the faulted run's events
    _run_repair(project, scratch, result)
    result.caught = bool(faulted.failed) or (gate is not None and not gate.passed)
    result.detail = _gate_detail(gate, faulted)


def _demo_verdict(
    project: str,
    scratch: Path,
    inject: Callable[[Path], None],
    result: DemoResult,
) -> None:
    _clean_run(project, scratch)
    execute(project, scratch)  # restores the build from the cache, so the edit sticks
    inject(scratch / "projects" / project)
    gate = run_gate(project, scratch)
    result.caught = gate is not None and not gate.passed
    result.detail = _gate_detail(gate, None)


def _run_repair(project: str, scratch: Path, result: DemoResult) -> None:
    """Let the repair loop react. With a model it fixes the fault; without one it proposes."""
    try:
        repair = run_with_repair(project, scratch)
    except CoreError as exc:  # no model, a broken registry: the demo still shows the catch
        result.detail += f"; repair loop did not run ({exc})"
        return
    if isinstance(repair, RepairReport):
        if repair.passed:
            result.detail += "; the repair loop fixed the fault"
        elif repair.no_proposal or not repair.proposals:
            result.detail += "; the repair loop wrote no fix (no design model?)"
        else:
            result.detail += "; the repair loop wrote proposals"


def _gate_detail(gate: GateResult | None, faulted: RunReport | None) -> str:
    parts: list[str] = []
    if faulted is not None and faulted.failed:
        parts.append("stage checks failed: " + ", ".join(sorted(faulted.failed)))
    if gate is None:
        parts.append("no gate report")
    elif not gate.passed:
        failing = [name for name, info in gate.report["gates"].items() if not info["passed"]]
        parts.append("the gate caught it in " + ", ".join(failing))
    else:
        parts.append("the gate did not catch it")
    return "; ".join(parts)


def _copy_events(project: str, root: Path, scratch: Path, result: DemoResult) -> None:
    """Copy the scratch runs' event logs into the real root under fresh run ids.

    Only `.engenia/events/` under the real root is written. The project field inside each
    event already names the real project, so the logs need no rewriting. A marker file in the
    scratch remembers what was copied, so a repeated demo cannot duplicate a run.
    """
    files = sorted(
        (path for path in (scratch / ".engenia" / "events").glob("*.jsonl")),
        key=lambda path: int(path.stem),
    )
    if not files:
        return
    done = scratch / ".engenia" / "events" / ".copied"
    copied = set(done.read_text(encoding="utf-8").split()) if done.is_file() else set()
    todo = [path for path in files if path.name not in copied]
    if not todo:
        return
    events_dir = root / ".engenia" / "events"
    events_dir.mkdir(parents=True, exist_ok=True)
    base = max(
        (int(path.stem) for path in events_dir.glob("*.jsonl") if path.stem.isdigit()),
        default=0,
    ) + 1
    for offset, path in enumerate(todo):
        target = events_dir / f"{base + offset}.jsonl"
        shutil.copyfile(path, target)
        result.copied_runs.append(base + offset)
    done.write_text(" ".join(sorted(copied | {path.name for path in todo})), encoding="utf-8")
    result.detail += (
        f"; {len(result.copied_runs)} run log(s) copied to the dashboard"
        f" (run id {', '.join(str(i) for i in result.copied_runs)})"
    )


def list_faults() -> list[dict[str, str]]:
    """One row per fault: id, folder name, and what its scenario does."""
    rows: list[dict[str, str]] = []
    for fault in _suite().FAULTS:
        rows.append({
            "id": fault.id, "name": fault.folder, "scenario": fault.description,
        })
    return rows
