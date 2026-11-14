"""Fixtures for the harness lane. No test here touches the network or a real model."""

from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

import pytest
import yaml

from engenia.contracts.models import StageContext

MASS_VERIFIER = '''\
from engenia.contracts.models import EvidenceLevel, Verdict, VerdictResult


def run(ctx):
    mass = float((ctx.src_dir / "design.txt").read_text().split("=")[1])
    limit = ctx.limit.max
    ok = mass <= limit
    return Verdict(
        check=ctx.check,
        requirement=ctx.requirement,
        measured=mass,
        limit=ctx.limit,
        result=VerdictResult.PASS if ok else VerdictResult.FAIL,
        evidence_level=EvidenceLevel.CALCULATED,
        message=None if ok else f"mass {mass} g is over the {limit} g budget",
    )
'''

CHECK_PLAN = [
    {
        "id": "C-001",
        "requirement": "R-001",
        "check": "mass_budget",
        "params": {},
        "limit": {"max": 400},
    }
]


class FakeStage:
    """Minimal stage. build() replaces build/<stage>/ with a copy of src/<stage>/, as the stub
    stage does, which also wipes anything else in build/<stage>/."""

    name = "mechanical"
    needs: list[str] = []
    reads_contract = False

    def __init__(self, checks: list[str]):
        self.checks = checks
        self.build_calls = 0

    def generate(self, ctx: StageContext) -> None:
        raise AssertionError("the harness does not call Stage.generate")

    def build(self, ctx: StageContext) -> None:
        self.build_calls += 1
        if ctx.build_dir.exists():
            shutil.rmtree(ctx.build_dir)
        shutil.copytree(ctx.src_dir, ctx.build_dir)
        if "BREAK" in (ctx.src_dir / "design.txt").read_text():
            raise RuntimeError("design.txt is broken")

    def interface(self, ctx: StageContext) -> dict:
        return {}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from engenia.harness.llm import DESIGN_AGENTS

    for agent in DESIGN_AGENTS:
        monkeypatch.delenv(f"ENGENIA_MODEL_{agent.upper()}", raising=False)
    for name in (
        "ENGENIA_VERIFY_MODEL",
        "ENGENIA_ALLOW_SAME_FAMILY",
        "ENGENIA_LLM_BASE_URL",
        "ENGENIA_LLM_API_KEY",
        "ENGENIA_MAX_ATTEMPTS",
        "ENGENIA_DESIGN_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throwaway repository root with project `demo` and the mass_budget verifier."""
    (tmp_path / "projects" / "demo" / "spec").mkdir(parents=True)
    (tmp_path / "projects" / "demo" / "checks").mkdir()
    (tmp_path / "projects" / "demo" / "checks" / "checks.yaml").write_text(
        yaml.safe_dump(CHECK_PLAN), encoding="utf-8"
    )
    (tmp_path / "verifiers").mkdir()
    (tmp_path / "verifiers" / "mass_budget.py").write_text(MASS_VERIFIER, encoding="utf-8")
    monkeypatch.setenv("ENGENIA_ROOT", str(tmp_path))
    return tmp_path


@pytest.fixture
def ctx(root: Path) -> StageContext:
    return StageContext(project="demo", stage="mechanical", root=root)


@pytest.fixture
def make_stage():
    """Factory: make_stage(["check_a", ...]) -> a FakeStage naming those verifiers."""
    return FakeStage


@pytest.fixture
def stage() -> FakeStage:
    return FakeStage(checks=["mass_budget"])


def draft_requirement(
    number: int = 1, *, status: str = "draft", statement: str | None = None
) -> str:
    """Requirement YAML text. Pass statement without 'shall' to make a lint failure."""
    statement = statement or "The total mass of the buoy shall not exceed 400 g."
    return textwrap.dedent(
        f"""\
        id: R-{number:03d}
        statement: {statement}
        value: 400
        unit: g
        verify:
          method: analysis
          check: mass_budget
          limit: {{max: 400}}
        affects: [mechanical]
        status: {status}
        """
    )


@pytest.fixture
def requirement_text():
    return draft_requirement
