"""Shared fixtures for the core tests.

`root` is a scratch repository root that holds a copy of the example spec, with its own cache
store, so no test touches the real repository or a real store.

`FakeStage` is a stage that records what the engine asks of it and whose interface follows the
specification, so a spec edit can be seen travelling through the graph.
"""

from __future__ import annotations

import json
import shutil
import threading
import time
from pathlib import Path

import pytest

from engenia.contracts.models import StageContext
from engenia.core.spec import load_requirements
from engenia.stages.registry import REGISTRY

# The core tests own their fixture project: three requirements under tests/core/fixtures/spec.
# They never read projects/harbour-listener, which other lanes are free to change.
EXAMPLE_PROJECT = "harbour-listener"  # a name only; the project lives in a scratch directory
FIXTURE_SPEC = Path(__file__).resolve().parent / "fixtures" / "spec"


@pytest.fixture
def root(tmp_path, monkeypatch) -> Path:
    shutil.copytree(FIXTURE_SPEC, tmp_path / "projects" / EXAMPLE_PROJECT / "spec")
    monkeypatch.setenv("ENGENIA_ROOT", str(tmp_path))
    monkeypatch.setenv("ENGENIA_STORE", str(tmp_path / "store"))
    return tmp_path


@pytest.fixture
def project_dir(root) -> Path:
    return root / "projects" / EXAMPLE_PROJECT


@pytest.fixture
def make_ctx(root):
    """make_ctx("mechanical") gives the StageContext of that stage in the scratch root."""

    def make(stage: str, base: Path | None = None) -> StageContext:
        return StageContext(project=EXAMPLE_PROJECT, stage=stage, root=base or root)

    return make


@pytest.fixture
def edit():
    """edit(path, old, new) replaces text in a file and insists that `old` was there."""

    def do(path: Path, old: str, new: str) -> None:
        text = path.read_text(encoding="utf-8")
        assert old in text, f"{old!r} not in {path}"
        path.write_text(text.replace(old, new), encoding="utf-8")

    return do


class Recorder:
    """Which stages had generate() and build() called, in order."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def add(self, phase: str, stage: str) -> None:
        self.events.append((phase, stage))

    def stages(self, phase: str) -> set[str]:
        return {stage for p, stage in self.events if p == phase}

    def count(self, phase: str, stage: str) -> int:
        return sum(1 for p, s in self.events if (p, s) == (phase, stage))

    def reset(self) -> None:
        self.events.clear()


class FakeStage:
    """Writes code in generate, copies it in build, and reports spec values as facts.

    With follow_spec=False the facts ignore the spec, so a rerun gives the same interface.
    """

    checks: list[str] = []

    def __init__(
        self,
        name: str,
        needs: list[str],
        recorder: Recorder,
        *,
        follow_spec: bool = True,
        reads_contract: bool = False,
        writes_code: bool = True,
        fail_in: str | None = None,
        transcripts: bool = False,
        delay: float = 0.0,
        barrier: threading.Barrier | None = None,
    ) -> None:
        self.name = name
        self.needs = needs
        self.reads_contract = reads_contract
        self.recorder = recorder
        self.follow_spec = follow_spec
        self.writes_code = writes_code
        self.fail_in = fail_in
        self.transcripts = transcripts
        self.delay = delay
        self.barrier = barrier  # build waits here: only passes if its peers build at the same time

    def generate(self, ctx: StageContext) -> None:
        self.recorder.add("generate", self.name)
        if self.fail_in == "generate":
            raise RuntimeError("model gave up")
        if self.writes_code:
            ctx.src_dir.mkdir(parents=True, exist_ok=True)
            (ctx.src_dir / "code.txt").write_text(f"code for {self.name}\n", encoding="utf-8")

    def build(self, ctx: StageContext) -> None:
        self.recorder.add("build", self.name)
        if self.fail_in == "build":
            raise RuntimeError("tool crashed")
        if self.barrier is not None:
            self.barrier.wait()
        if self.delay:
            time.sleep(self.delay)
        ctx.build_dir.mkdir(parents=True, exist_ok=True)
        code = ctx.src_dir / "code.txt"
        text = code.read_text(encoding="utf-8") if code.exists() else "no code\n"
        (ctx.build_dir / "out.txt").write_text("built from: " + text, encoding="utf-8")
        if self.transcripts:  # a log that is different on every build
            n = self.recorder.count("build", self.name)
            log = ctx.build_dir / "transcripts" / f"run-{n}.txt"
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(f"model said something different, attempt {n}\n", encoding="utf-8")

    def interface(self, ctx: StageContext) -> dict:
        own = {}
        if self.follow_spec:
            requirements = load_requirements(ctx.spec_dir)
            own = {r.id: r.value for r in requirements if self.name in r.affects}
        up = {}
        for need in self.needs:
            data = json.loads(ctx.stage_interface_path(need).read_text(encoding="utf-8"))
            up[need] = data["facts"]
        return {"own": own, "up": up}


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()


@pytest.fixture
def fake_registry(recorder):
    """fake_registry(**options): the real stage names and needs, with FakeStage behaviour.

    `overrides` maps a stage name to FakeStage options for that stage alone.
    """

    def build(overrides: dict[str, dict] | None = None, **options) -> dict[str, FakeStage]:
        overrides = overrides or {}
        return {
            name: FakeStage(
                name,
                list(real.needs),
                recorder,
                **{"reads_contract": real.reads_contract, **options, **overrides.get(name, {})},
            )
            for name, real in REGISTRY.items()
        }

    return build


@pytest.fixture
def fake_stage(recorder):
    """fake_stage("mechanical", **options): one FakeStage with the real needs of that stage."""

    def make(name: str, **options) -> FakeStage:
        real = REGISTRY[name]
        opts = {"reads_contract": real.reads_contract, **options}
        return FakeStage(name, list(real.needs), recorder, **opts)

    return make
