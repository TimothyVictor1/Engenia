"""Parallel scheduling: independent stages overlap, needs are respected, failures stay local."""

import inspect
import re
import threading
from pathlib import Path

import pytest

from engenia.core import graph
from engenia.core.engine import RunFailed, Status, execute, run_pipeline
from engenia.core.scheduler import Hooks, schedule

PROJECT = "harbour-listener"
DEPENDANTS_OF_MECHANICAL = {"integration", "simulation", "final", "report"}


class Node:
    def __init__(self, name, needs=()):
        self.name, self.needs = name, list(needs)


def test_independent_stages_run_at_the_same_time(root, fake_registry):
    # mechanical, electronics and firmware all need only the architect. Each waits for the
    # others inside build(), so the run can only finish if they really overlap.
    barrier = threading.Barrier(3, timeout=10)
    overrides = {name: {"barrier": barrier} for name in ("mechanical", "electronics", "firmware")}
    results = run_pipeline(PROJECT, root, registry=fake_registry(overrides), workers=3)
    assert len(results) == 12
    assert all(r.status is Status.RAN for r in results)


def test_one_worker_never_runs_two_stages_at_once(root, fake_registry):
    barrier = threading.Barrier(2, timeout=0.3)
    registry = fake_registry(
        {"mechanical": {"barrier": barrier}, "electronics": {"barrier": barrier}}
    )
    with pytest.raises(RunFailed, match="failed in build"):
        run_pipeline(PROJECT, root, registry=registry, workers=1)  # they cannot meet


def test_a_stage_never_starts_before_its_needs_have_finished():
    reg = {n: Node(n, needs) for n, needs in {
        "a": [], "b": ["a"], "c": ["a"], "d": ["b", "c"], "e": [],
    }.items()}
    log, lock = [], threading.Lock()

    def work(name):
        with lock:
            log.append(("start", name))
        import time

        time.sleep(0.02)
        with lock:
            log.append(("end", name))

    schedule(graph.topo_order(reg), reg, work, workers=4)
    index = {event: i for i, event in enumerate(log)}
    for name, node in reg.items():
        for need in node.needs:
            assert index[("end", need)] < index[("start", name)]
    assert index[("start", "e")] < index[("end", "a")]  # e is independent, so it overlaps


def test_a_failed_stage_blocks_only_its_dependants(root, recorder, fake_registry):
    registry = fake_registry({"mechanical": {"fail_in": "build"}, "redteam": {"fail_in": "build"}})
    report = execute(PROJECT, root, registry=registry, workers=4)
    assert not report.ok
    assert set(report.failed) == {"mechanical", "redteam"}
    # redteam is needed by simprep and simulation; integration and the rest hang off mechanical.
    assert set(report.blocked) == DEPENDANTS_OF_MECHANICAL | {"simprep"}
    assert report.blocked["simprep"] == ["redteam"]
    assert report.blocked["integration"] == ["mechanical"]
    assert report.blocked["simulation"] == ["mechanical", "redteam"]
    assert {r.stage for r in report.results} == (
        {"verify_plan", "architect", "electronics", "firmware", "testplan"}
    )
    assert recorder.stages("build").isdisjoint(DEPENDANTS_OF_MECHANICAL)


def test_blocking_follows_a_chain_of_needs():
    reg = {n: Node(n, needs) for n, needs in {"a": [], "b": ["a"], "c": ["b"], "z": []}.items()}

    def work(name):
        if name == "a":
            raise RuntimeError("boom")
        return name

    blocked = []
    hooks = Hooks(blocked=lambda name, causes: blocked.append((name, causes)))
    outcome = schedule(["a", "b", "c", "z"], reg, work, workers=2, hooks=hooks)
    assert list(outcome.failed) == ["a"]
    assert outcome.done == {"z": "z"}
    assert blocked == [("b", ["a"]), ("c", ["a"])]  # c names the root cause, not b


def test_run_pipeline_raises_after_everything_else_finished(root, fake_registry):
    registry = fake_registry({"firmware": {"fail_in": "generate"}})
    with pytest.raises(RunFailed, match="stage firmware failed in generate") as caught:
        run_pipeline(PROJECT, root, registry=registry)
    report = caught.value.report
    assert "mechanical" in {r.stage for r in report.results}  # a sibling still completed
    assert set(report.blocked) == DEPENDANTS_OF_MECHANICAL


def test_cli_reports_failures_and_exits_1_but_still_runs_the_rest(root, monkeypatch, fake_registry):
    from typer.testing import CliRunner

    from engenia.core.cli import app

    registry = fake_registry({"mechanical": {"fail_in": "build"}})
    monkeypatch.setattr("engenia.core.engine._default_registry", lambda: registry)
    result = CliRunner().invoke(app, ["run", PROJECT, "--workers", "3"])
    assert result.exit_code == 1
    assert "FAIL mechanical" in result.output and "blocked integration" in result.output
    assert "ran  firmware" in result.output
    assert "run 1 failed: 1 failed, 4 blocked" in result.output


# --- needs must cover every read of another stage's output ------------------------------------

_READS = [
    re.compile(r"""stage_build_dir\(\s*["'](\w+)["']"""),
    re.compile(r"""stage_interface_path\(\s*["'](\w+)["']"""),
    re.compile(r"""["']build["']\s*/\s*["'](\w+)["']"""),
    re.compile(r"""["']src["']\s*/\s*["'](\w+)["']"""),
]


def test_every_stage_that_reads_another_stages_output_lists_it_in_needs():
    """Without the need, a parallel run can read the output before it is written (a race)."""
    from engenia.contracts.models import STAGE_NAMES
    from engenia.stages.registry import REGISTRY

    missing = []
    for name, stage in REGISTRY.items():
        source = Path(inspect.getfile(type(stage))).read_text(encoding="utf-8")
        read = {m for pattern in _READS for m in pattern.findall(source)}
        for other in sorted(read & set(STAGE_NAMES) - {name}):
            if other not in stage.needs:
                missing.append(f"{name} reads {other} but needs {stage.needs}")
    assert not missing, "\n".join(missing)


def test_main_thread_stage_runs_on_the_main_thread_and_alone():
    class Heavy(Node):
        main_thread = True

    reg = {"a": Node("a"), "b": Node("b"), "heavy": Heavy("heavy", ["a"]), "c": Node("c", ["a"])}
    lock, active, seen = threading.Lock(), set(), {}

    def work(name):
        with lock:
            seen[name] = (threading.current_thread() is threading.main_thread(), set(active))
            active.add(name)
        threading.Event().wait(0.05)
        with lock:
            active.discard(name)

    outcome = schedule(["a", "b", "heavy", "c"], reg, work, workers=4)
    assert set(outcome.done) == set(reg) and not outcome.failed
    on_main, others = seen["heavy"]
    assert on_main and others == set()  # nothing else was running when it started
    assert not any(seen[n][0] for n in ("a", "b", "c"))
    # nothing started while it ran (work() is inline, so c starts only after it returned)
    assert "heavy" not in seen["c"][1]


def test_main_thread_stage_failure_blocks_its_dependants():
    class Heavy(Node):
        main_thread = True

    reg = {"heavy": Heavy("heavy"), "after": Node("after", ["heavy"])}

    def work(name):
        raise RuntimeError("boom")

    outcome = schedule(["heavy", "after"], reg, work, workers=2)
    assert list(outcome.failed) == ["heavy"] and outcome.blocked == {"after": ["heavy"]}
