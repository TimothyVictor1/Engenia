"""Fixtures for the architect and verification agent tests. FakeLLM only; own fixture project."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from engenia.contracts.models import StageContext

REPO = Path(__file__).resolve().parents[2]


def requirement(number: int, check: str, limit: dict, *, status: str = "approved") -> dict:
    return {
        "id": f"R-{number:03d}",
        "statement": f"The buoy shall satisfy {check}.",
        "value": 1,
        "unit": "x",
        "verify": {"method": "analysis", "check": check, "limit": limit},
        "affects": ["mechanical"],
        "status": status,
    }


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("ENGENIA_LLM_BASE_URL", "ENGENIA_DESIGN_MODEL", "ENGENIA_VERIFY_MODEL",
                 "ENGENIA_MAX_ATTEMPTS"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    shutil.copytree(REPO / "verifiers", tmp_path / "verifiers", ignore=shutil.ignore_patterns(
        "__pycache__"))
    shutil.copytree(REPO / "packs" / "buoy", tmp_path / "packs" / "buoy",
                    ignore=shutil.ignore_patterns("__pycache__", "templates"))
    spec = tmp_path / "projects" / "demo" / "spec"
    spec.mkdir(parents=True)
    reqs = [
        requirement(1, "mass_budget", {"max": 400}),
        requirement(2, "print_bed_fit", {"equals": 1}),
        requirement(3, "flow_drag", {"min": 0.9}),  # no verifier yet
        requirement(4, "wall_thickness_min", {"min": 3}, status="draft"),
    ]
    for req in reqs:
        (spec / f"{req['id']}.yaml").write_text(yaml.safe_dump(req), encoding="utf-8")
    return tmp_path


def make_ctx(root: Path, stage: str) -> StageContext:
    return StageContext(project="demo", stage=stage, root=root)


@pytest.fixture
def verify_ctx(root: Path) -> StageContext:
    return make_ctx(root, "verify_plan")


@pytest.fixture
def architect_ctx(root: Path) -> StageContext:
    return make_ctx(root, "architect")
