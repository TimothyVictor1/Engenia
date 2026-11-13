"""Scratch repositories for the trace-matrix (evidence) tests.

The scratch root is a miniature repository: a copy of the real verifiers/ (so the matrix
recompute can load them exactly as the gate does) and the fixture spec from fixtures/spec.
Nothing here touches projects/harbour-listener.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from engenia.contracts.models import StageContext

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
PROJECT = "demo"
INTERFACE_KEY = "sha256:" + "a" * 64


@pytest.fixture
def scratch_root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(
        REPO / "verifiers",
        root / "verifiers",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    shutil.copytree(HERE / "fixtures" / "spec", root / "projects" / PROJECT / "spec")
    return root


@pytest.fixture
def write_checks(scratch_root: Path):
    def _write(name: str = "checks.yaml") -> Path:
        source = HERE / "fixtures" / name
        target = scratch_root / "projects" / PROJECT / "checks" / "checks.yaml"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(source, target)
        return target

    return _write


@pytest.fixture
def write_interface(scratch_root: Path):
    def _write(stage: str, facts: dict[str, Any]) -> None:
        path = scratch_root / "projects" / PROJECT / "build" / stage / "interface.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"stage": stage, "key": INTERFACE_KEY, "facts": facts}),
            encoding="utf-8",
        )

    return _write


@pytest.fixture
def write_contract(scratch_root: Path):
    def _write(*, electronics_mass: float = 60, total_mass: float = 400) -> None:
        path = scratch_root / "projects" / PROJECT / "contract" / "contract.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "budgets": {
                        "mechanical": {"mass_g": 300, "outer_envelope_mm": [160, 160, 220]},
                        "electronics": {
                            "mass_g": electronics_mass,
                            "bay_mm": [70, 40, 22],
                            "energy_mwh_per_day": 180,
                        },
                        "firmware": {"flash_kb": 512, "ram_kb": 64},
                    },
                    "interfaces": {
                        "pin_map": {"HYDROPHONE_ADC": "GPIO34", "LORA_CS": "GPIO5"},
                        "mounts": {"board_holes_mm": [[3, 3], [67, 3], [3, 37], [67, 37]]},
                    },
                    "totals": {"mass_g_max": total_mass},
                }
            ),
            encoding="utf-8",
        )

    return _write


@pytest.fixture
def stage_ctx(scratch_root: Path):
    def _make(stage: str = "report") -> StageContext:
        return StageContext(project=PROJECT, stage=stage, root=scratch_root)

    return _make
