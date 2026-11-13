"""Fixtures for the gate tests: a scratch repository with one fake stage and its own project.

Nothing here reads projects/harbour-listener. The real verifiers are copied in, so the gate
exercises real verifier code against a fake `mechanical` stage whose facts come from
src/mechanical/design.json.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from engenia.agents.verification.stage import deterministic_plan, dump_plan, load_requirements
from engenia.contracts.models import StageContext
from engenia.core.engine import run_pipeline
from engenia.core.hashing import file_hash
from engenia.gate.canary import CONTRACT

REPO = Path(__file__).resolve().parents[2]
PROJECT = "demo"


class FakeMechanical:
    name = "mechanical"
    needs: list[str] = []
    reads_contract = False
    checks = ["mass_budget", "wall_thickness_min"]

    def generate(self, ctx: StageContext) -> None:
        ctx.src_dir.mkdir(parents=True, exist_ok=True)
        (ctx.src_dir / "design.json").write_text(
            json.dumps({"dry_mass_g": 280.0, "wall_min_mm": 3.5}), encoding="utf-8"
        )

    def build(self, ctx: StageContext) -> None:
        if ctx.build_dir.exists():
            shutil.rmtree(ctx.build_dir)
        shutil.copytree(ctx.src_dir, ctx.build_dir)

    def interface(self, ctx: StageContext) -> dict:
        return json.loads((ctx.src_dir / "design.json").read_text(encoding="utf-8"))


def requirement(number: int, check: str, limit: dict, status: str = "approved") -> dict:
    return {
        "id": f"R-{number:03d}",
        "statement": f"The buoy shall satisfy {check}.",
        "value": 1,
        "unit": "x",
        "verify": {"method": "analysis", "check": check, "limit": limit},
        "affects": ["mechanical"],
        "status": status,
    }


@pytest.fixture
def registry() -> dict:
    return {"mechanical": FakeMechanical()}


@pytest.fixture
def root(tmp_path: Path, registry, monkeypatch) -> Path:
    shutil.copytree(REPO / "verifiers", tmp_path / "verifiers",
                    ignore=shutil.ignore_patterns("__pycache__"))
    project = tmp_path / "projects" / PROJECT
    (project / "spec").mkdir(parents=True)
    for req in (
        requirement(1, "mass_budget", {"max": 400}),
        requirement(2, "wall_thickness_min", {"min": 3}),
        requirement(3, "detection_rate", {"min": 0.9}),  # verifier does not exist yet
    ):
        (project / "spec" / f"{req['id']}.yaml").write_text(yaml.safe_dump(req), encoding="utf-8")
    (project / "contract").mkdir()
    (project / "contract" / "contract.json").write_text(json.dumps(CONTRACT), encoding="utf-8")
    write_plan(tmp_path)
    run_pipeline(PROJECT, tmp_path, registry=registry, store=tmp_path / "store")
    return tmp_path


def write_plan(root: Path, plan: list[dict] | None = None, *, approve: bool = True) -> None:
    project = root / "projects" / PROJECT
    if plan is None:
        plan = deterministic_plan(load_requirements(project / "spec"))
    path = project / "checks" / "checks.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_plan(plan), encoding="utf-8")
    approved = path.parent / "APPROVED"
    if approve:
        approved.write_text(file_hash(path) + "\n", encoding="utf-8")
    else:
        approved.unlink(missing_ok=True)


@pytest.fixture
def project_dir(root: Path) -> Path:
    return root / "projects" / PROJECT


@pytest.fixture
def set_plan(root: Path):
    """set_plan(plan=None, approve=True): write checks.yaml (default: the deterministic plan)."""
    return lambda plan=None, approve=True: write_plan(root, plan, approve=approve)


@pytest.fixture
def add_requirement(project_dir: Path):
    def add(number: int, check: str, limit: dict, status: str = "approved") -> None:
        req = requirement(number, check, limit, status)
        (project_dir / "spec" / f"{req['id']}.yaml").write_text(yaml.safe_dump(req))

    return add
