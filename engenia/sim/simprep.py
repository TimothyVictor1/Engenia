"""Simprep stage: one simulation case per approved simulation requirement.

generate() writes src/simprep/cases.yaml, a list of cases:

    - id: SIM-011
      solver: waves            # "waves", "structure", or "flow"
      requirement: R-011
      scenarios: [calm_harbour, ...]   # red-team scenario names; empty means every scenario

build() copies it to build/simprep/. Which solver answers a requirement follows from the name of
its verify.check (SOLVER_FOR_CHECK).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml

from engenia.contracts.models import StageContext
from engenia.core import spec

CASES_FILE = "cases.yaml"
SOLVER_FOR_CHECK = {"wave_tilt": "waves", "stress_safety_factor": "structure"}


def _scenario_names(ctx: StageContext) -> list[str]:
    """Red-team scenario names, from its build if present, else its source. Empty if neither."""
    for base in (ctx.stage_build_dir("redteam"), ctx.project_dir / "src" / "redteam"):
        directory = base / "scenarios"
        if directory.is_dir():
            return sorted(path.stem for path in directory.glob("*.yaml"))
    return []


def build_cases(ctx: StageContext) -> list[dict]:
    names = _scenario_names(ctx)
    cases = []
    for requirement in sorted(spec.load_requirements(ctx.spec_dir), key=lambda r: r.id):
        if requirement.status != "approved" or requirement.verify.method != "simulation":
            continue
        solver = SOLVER_FOR_CHECK.get(requirement.verify.check)
        if solver is None:
            raise ValueError(
                f"{requirement.id}: no simulation solver for check {requirement.verify.check!r}"
            )
        cases.append({
            "id": f"SIM-{requirement.id.removeprefix('R-')}",
            "solver": solver,
            "requirement": requirement.id,
            "scenarios": list(names),
        })
    if names:
        cases.append({
            "id": "SIM-FLOW",
            "solver": "flow",
            "requirement": None,
            "scenarios": list(names),
        })
    return cases


def load_cases(path: Path) -> list[dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path.name}: expected a list of cases")
    return data


class SimprepStage:
    name = "simprep"
    needs: list[str] = ["redteam"]
    reads_contract = False
    checks: list[str] = []

    def generate(self, ctx: StageContext) -> None:
        ctx.src_dir.mkdir(parents=True, exist_ok=True)
        (ctx.src_dir / CASES_FILE).write_text(
            yaml.safe_dump(build_cases(ctx), sort_keys=True, default_flow_style=False),
            encoding="utf-8",
        )

    def build(self, ctx: StageContext) -> None:
        source = ctx.src_dir / CASES_FILE
        if not source.is_file():
            raise ValueError(f"missing {CASES_FILE}; run generate() first")
        load_cases(source)  # reject a malformed file before it is published
        if ctx.build_dir.exists():
            shutil.rmtree(ctx.build_dir)
        ctx.build_dir.mkdir(parents=True)
        shutil.copy2(source, ctx.build_dir / CASES_FILE)

    def interface(self, ctx: StageContext) -> dict:
        cases = load_cases(ctx.build_dir / CASES_FILE)
        return {"cases": [case["id"] for case in cases]}
