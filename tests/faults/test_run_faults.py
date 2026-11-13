"""Tests for the planted-defect suite in evals/faults.

The suite is its own fixture material: every fault folder under evals/faults must stay
runnable, and the runner must catch all ten faults. Nothing here touches
projects/harbour-listener.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SUITE_DIR = REPO / "evals" / "faults"


def _load_runner():
    spec = importlib.util.spec_from_file_location("faults_runner", SUITE_DIR / "run_faults.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclass resolution reads sys.modules while executing
    spec.loader.exec_module(module)
    return module


run_faults = _load_runner()


@pytest.mark.parametrize("fault", run_faults.FAULTS, ids=lambda fault: fault.id)
def test_fault_folder_is_minimal_and_described(fault: object) -> None:
    """Every fault folder exists, holds fixture files, and has a one-line README."""
    folder = SUITE_DIR / fault.folder  # type: ignore[attr-defined]
    assert folder.is_dir()
    assert any(folder.iterdir()), f"{fault.folder} has no fixture files"  # type: ignore[attr-defined]
    readme = (folder / "README.md").read_text(encoding="utf-8").strip()
    lines = [line for line in readme.splitlines() if line.strip()]
    assert len(lines) == 1, f"{fault.folder}/README.md must be exactly one line"
    assert "must return" in lines[0] or "must reject" in lines[0]


@pytest.mark.parametrize("fault", run_faults.FAULTS, ids=lambda fault: fault.id)
def test_fault_is_caught(fault: object) -> None:
    """The one check each fault names must reject it: verdict fail, or a gate problem."""
    verdict = run_faults.run_one(fault)  # type: ignore[attr-defined]
    assert run_faults._caught(fault, verdict), (  # type: ignore[attr-defined]
        f"{fault.id} was not caught: {verdict.message}"  # type: ignore[attr-defined]
    )
    if fault.kind != "gate":  # type: ignore[attr-defined]
        assert verdict.result == "fail"


def test_run_all_catches_every_fault_and_writes_the_report(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    report = run_faults.run_all(report_path=report_path)

    assert report["total"] == 10
    assert report["caught"] == 10
    assert report_path.is_file()
    on_disk = json.loads(report_path.read_text(encoding="utf-8"))
    assert on_disk == report
    for entry in report["faults"]:
        assert set(entry) == {"fault", "expected_check", "caught", "message"}
        assert entry["caught"] is True
        assert entry["message"], f"{entry['fault']} caught without a reason"


def test_report_is_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "one.json"
    second = tmp_path / "two.json"
    run_faults.run_all(report_path=first)
    run_faults.run_all(report_path=second)
    assert first.read_bytes() == second.read_bytes()


def test_uncaught_fault_is_reported_and_exits_one(tmp_path: Path, monkeypatch) -> None:
    """A fault the check lets through is scored not caught, and the suite exits 1."""
    from engenia.contracts.models import Limit

    passes = run_faults.Fault(
        "F99", "F09-low-detection-rate", "0.5 detection rate against a 0.4 minimum",
        kind="verifier", check="detection_rate", stage="firmware", requirement="R-009",
        limit=Limit(min=0.4),
    )
    report_path = tmp_path / "report.json"
    report = run_faults.run_all(faults=(passes,), report_path=report_path)
    assert report["caught"] == 0
    assert report["total"] == 1
    entry = report["faults"][0]
    assert entry["caught"] is False
    assert entry["message"] is None  # a passing verdict has no failure message

    monkeypatch.setattr(run_faults, "FAULTS", (passes,))
    assert run_faults.main(["--report", str(report_path)]) == 1
