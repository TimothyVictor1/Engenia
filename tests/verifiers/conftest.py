"""Small fixture repositories for verifier tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from engenia.contracts.models import CheckContext, Limit

PROJECT = "demo"
INTERFACE_KEY = "sha256:" + "a" * 64


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def write_interface(tmp_path):
    def _write(stage: str, facts: dict[str, Any]) -> None:
        _write_json(
            tmp_path / "projects" / PROJECT / "build" / stage / "interface.json",
            {"stage": stage, "key": INTERFACE_KEY, "facts": facts},
        )

    return _write


@pytest.fixture
def write_contract(tmp_path):
    def _write(
        *,
        mechanical_mass: float = 300,
        electronics_mass: float = 60,
        total_mass: float = 400,
    ) -> None:
        _write_json(
            tmp_path / "projects" / PROJECT / "contract" / "contract.json",
            {
                "version": 1,
                "budgets": {
                    "mechanical": {
                        "mass_g": mechanical_mass,
                        "outer_envelope_mm": [160, 160, 220],
                    },
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
            },
        )

    return _write


@pytest.fixture
def make_ctx(tmp_path):
    def _make(
        *,
        stage: str = "mechanical",
        check: str = "mass_budget",
        requirement: str = "R-001",
        params: dict[str, Any] | None = None,
        limit: Limit | None = None,
    ) -> CheckContext:
        return CheckContext(
            root=tmp_path,
            project=PROJECT,
            stage=stage,
            check=check,
            requirement=requirement,
            params=params or {},
            limit=limit or Limit(max=400),
        )

    return _make
