"""The pipeline: key every stage, use the cache, run only what changed, run independent stages
side by side.

Stages run on a small thread pool (scheduler.py) and start only when everything they need has
finished. A stage that fails blocks the stages that depend on it, directly or through others;
every other stage carries on. The run is recorded in .engenia/run.db (runstate.py). Change
requests from the stages are decided before the run starts and after it ends (board.py), and
build/mismatches.json lists where the stages' interfaces disagree (mismatches.py).

Parallel and serial runs write byte-identical files: a stage reads only its own inputs and its
upstream stages' finished interface.json, and writes only its own src/ and build/ directories.

For each stage, in dependency order:

1. Compute the stage's keys (hashing.py).
2. Decide whether the agent must write code (`generate`). It must when src/<stage>/ is empty, or
   when the stage's inputs changed since the code was written. Rewriting is never done for any
   other reason, because `generate` is the only step that calls a model.
3. Look the full stage key up in the cache. A hit restores build/<stage>/ and nothing runs.
4. On a miss, build, publish the interface, run scripted checks, and cache only passing results.
   Failed/error verdicts (including final) fail the stage and block its dependants.

Change propagation needs no extra code. A downstream stage's key contains its upstream
interface.json, so a stage that reruns but writes the same interface.json leaves every
downstream key unchanged and every downstream stage is a cache hit.

How the engine knows when "the inputs changed": after `generate` it records the stage's input key
in projects/<p>/src/.generated-from/<stage>. That file is committed with the code, so a fresh
clone knows which inputs its code was written for. Code with no such record (written by hand,
or by an older run) is treated as current and is never overwritten.

interface.json holds `key`, the hash of the build outputs (not the stage key), leaving out
interface.json, verdict.json and the transcripts/ directory. Two runs that produce the same
outputs and the same facts therefore write byte-identical interface files, which is what stops
propagation.
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from engenia.contracts.models import Interface, StageContext
from engenia.core import board, events, graph, spec
from engenia.core.cache import Cache, default_store
from engenia.core.errors import CoreError
from engenia.core.hashing import StageKeys, compute_keys, tree_files, tree_hash
from engenia.core.mismatches import write_mismatches
from engenia.core.runstate import RunStore
from engenia.core.scheduler import Hooks, schedule
from engenia.core.verification import verify_stage
from engenia.stages.base import Stage

MARKER_DIR = ".generated-from"
INTERFACE_FILE = "interface.json"
VERDICT_FILE = "verdict.json"
# Left out of the outputs hash in interface.json. The first two are written after the outputs
# exist. build/<stage>/transcripts/ (and everything under it) holds agent logs, which differ on
# every run and say nothing about the design.
TRANSCRIPTS_DIR = "transcripts"
_NOT_OUTPUTS = (INTERFACE_FILE, VERDICT_FILE, TRANSCRIPTS_DIR)


class PipelineError(CoreError):
    """A stage's own code (generate, build or interface) failed."""

    stage_key: str | None = None  # the key the stage was running under, when it got that far


class RunFailed(PipelineError):
    """A run finished but some stages failed. Everything that could run did; see `report`."""

    def __init__(self, message: str, report: RunReport) -> None:
        super().__init__(message)
        self.report = report


class Status(StrEnum):
    RAN = "ran"  # generate and/or build ran
    CACHED = "cached"  # build/<stage>/ restored from the cache, nothing ran


@dataclass(frozen=True)
class StageResult:
    stage: str
    status: Status
    generated: bool
    built: bool
    key: str
    resumed: bool = False  # skipped because an earlier, interrupted run had already finished it


class _Src(StrEnum):
    EMPTY = "empty"  # no files: code must be written
    STALE = "stale"  # written for other inputs: code must be rewritten
    CURRENT = "current"  # written for these inputs
    UNMARKED = "unmarked"  # no record of who wrote it: left alone


def marker_path(ctx: StageContext) -> Path:
    return ctx.project_dir / "src" / MARKER_DIR / ctx.stage


def _src_state(ctx: StageContext, input_key: str) -> _Src:
    if not ctx.src_dir.is_dir() or not tree_files(ctx.src_dir):
        return _Src.EMPTY
    try:
        recorded = marker_path(ctx).read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return _Src.UNMARKED
    return _Src.CURRENT if recorded == input_key else _Src.STALE


def _record_generation(ctx: StageContext, input_key: str) -> None:
    marker = marker_path(ctx)
    if ctx.src_dir.is_dir() and tree_files(ctx.src_dir):
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(input_key + "\n", encoding="utf-8")
    else:
        marker.unlink(missing_ok=True)  # wrote nothing, so there is nothing to call current


def _call(stage: Stage, phase: str, fn: Callable[[], object]) -> object:
    try:
        return fn()
    except Exception as exc:
        raise PipelineError(f"stage {stage.name} failed in {phase}: {exc}") from exc


def _write_build_files(stage: Stage, ctx: StageContext) -> None:
    facts = _call(stage, "interface", lambda: stage.interface(ctx))
    try:
        interface = Interface(
            stage=ctx.stage, key=tree_hash(ctx.build_dir, exclude=_NOT_OUTPUTS), facts=facts
        )
    except ValueError as exc:  # pydantic's ValidationError is a ValueError
        raise PipelineError(f"stage {stage.name} wrote an invalid interface: {exc}") from exc
    (ctx.build_dir / INTERFACE_FILE).write_text(
        json.dumps(interface.model_dump(mode="json"), sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    verdict = ctx.build_dir / VERDICT_FILE
    if not verdict.exists():
        verdict.write_text("[]\n", encoding="utf-8")


def _run_stage(stage: Stage, ctx: StageContext, cache: Cache) -> StageResult:
    keys: StageKeys = compute_keys(stage, ctx)
    try:
        return _run_keyed(stage, ctx, cache, keys)
    except PipelineError as exc:
        exc.stage_key = getattr(exc, "stage_key", None) or keys.stage_key
        raise


def _run_keyed(stage: Stage, ctx: StageContext, cache: Cache, keys: StageKeys) -> StageResult:

    generated = False
    state = _src_state(ctx, keys.input_key)
    # Empty src with a cached build for this exact key means the stage writes no code (or wrote
    # none last time): the cache already has the answer, so do not ask the agent again.
    needs_code = state in (_Src.EMPTY, _Src.STALE) and not (
        state is _Src.EMPTY and cache.has(keys.stage_key)
    )
    if needs_code:
        _call(stage, "generate", lambda: stage.generate(ctx))
        _record_generation(ctx, keys.input_key)
        generated = True
        keys = compute_keys(stage, ctx)  # the code is part of the full key

    built = False
    restored = cache.get(keys.stage_key, ctx.build_dir) and (
        ctx.stage_interface_path(ctx.stage).is_file()
    )
    if not restored:
        if ctx.build_dir.exists():
            shutil.rmtree(ctx.build_dir)  # no stale outputs from an earlier run
        ctx.build_dir.parent.mkdir(parents=True, exist_ok=True)
        _call(stage, "build", lambda: stage.build(ctx))
        _write_build_files(stage, ctx)
        _call(stage, "checks", lambda: verify_stage(stage, ctx))
        cache.put(keys.stage_key, ctx.build_dir)
        built = True

    if restored:
        _call(stage, "checks", lambda: verify_stage(stage, ctx, restored=True))
    status = Status.RAN if generated or built else Status.CACHED
    return StageResult(stage.name, status, generated, built, keys.stage_key)


@dataclass
class RunReport:
    run_id: int
    results: list[StageResult]  # stages that finished, in dependency order
    failed: dict[str, str]  # stage -> why
    blocked: dict[str, list[str]]  # stage -> the failed stages it was waiting for
    decisions: list[board.Decision]  # change requests decided before and after the run
    mismatches: list[dict]
    resumed_run: bool = False  # this run continued an earlier one (see `resume`)
    errors: dict[str, Exception] = field(default_factory=dict)  # stage -> the exception itself

    @property
    def ok(self) -> bool:
        return not self.failed

    @property
    def contract_changed(self) -> bool:
        return any(d.status.value == "accepted" for d in self.decisions)


def default_workers() -> int:
    return max(1, min(os.cpu_count() or 1, 4))


def _failure_message(stage: str, exc: Exception) -> str:
    return str(exc) if isinstance(exc, PipelineError) else f"stage {stage} failed: {exc}"


def execute(
    project: str,
    root: Path,
    *,
    registry: graph.Registry | None = None,
    store: Path | None = None,
    on_result: Callable[[StageResult], None] | None = None,
    workers: int | None = None,
    resume: bool = False,
    run_id: int | None = None,
) -> RunReport:
    """Run every stage of `project`, up to `workers` at a time, and report what happened.

    `resume` continues the newest run of the project that did not finish cleanly (stage_run in
    .engenia/run.db): a stage it finished (done or cached) is skipped when its key is still the
    same and its build is still there; every other stage, including one that was interrupted
    mid-run, is started again and its attempt count goes up. With no such run, a new one starts.
    `run_id` continues that exact run instead (the repair loop uses it to keep one run row).

    A failing stage does not stop the run: its dependants are blocked and the rest carry on.
    Stage failures are in the report. Raises SpecError, GraphError or BoardError for problems
    found before any stage runs.
    """
    root = Path(root)
    reg = registry if registry is not None else _default_registry()
    order = graph.topo_order(reg)
    spec.load_spec(root / "projects" / project / "spec")  # fail early on a broken spec
    cache = Cache(store if store is not None else default_store(root))
    decisions = board.process_change_requests(root, project)  # before anything reads the contract

    state = RunStore(root)
    try:
        if run_id is None and resume:
            run_id = state.latest_unfinished(project)
        kept: dict[str, dict] = {}
        continued = run_id is not None
        if run_id is not None:
            kept = state.reopen_run(run_id, order)
        else:
            run_id = state.start_run(project, order)
        failed: dict[str, str] = {}
        skipped: set[str] = set()  # added by worker threads; set.add is atomic

        def work(name: str) -> StageResult:
            ctx = StageContext(project=project, stage=name, root=root)
            if name in kept:
                try:
                    key = compute_keys(reg[name], ctx).stage_key
                except CoreError:
                    key = None  # the normal path reports what is wrong
                if key == kept[name]["key"] and ctx.stage_interface_path(name).is_file():
                    _call(reg[name], "checks", lambda: verify_stage(reg[name], ctx, restored=True))
                    skipped.add(name)
                    return StageResult(name, Status.CACHED, False, False, key, resumed=True)
            return _run_stage(reg[name], ctx, cache)

        def started(name: str) -> None:
            events.emit(root, name, "message", {"text": f"stage {name} started"})
            if name not in kept:
                state.stage_started(run_id, name)

        def restarted(name: str) -> None:
            # A stage kept from the earlier run that could not be skipped: it runs again.
            if name in kept and name not in skipped:
                state.stage_started(run_id, name)

        def done(name: str, result: StageResult) -> None:
            if name not in skipped:
                restarted(name)
                status = "done" if result.status is Status.RAN else "cached"
                events.emit(root, name, "message", {"text": f"stage {name} {status}"})
                state.stage_finished(run_id, name, status, key=result.key)
            if on_result:
                on_result(result)

        def fail(name: str, exc: Exception) -> None:
            message = _failure_message(name, exc)
            failed[name] = message
            events.emit(root, name, "message", {"text": message, "level": "error"})
            restarted(name)
            state.stage_finished(
                run_id, name, "failed", key=getattr(exc, "stage_key", None), message=message
            )

        def block(name: str, causes: list[str]) -> None:
            state.stage_blocked(run_id, name, "blocked by failed stage " + ", ".join(causes))

        hooks = Hooks(
            started=started,
            done=done, failed=fail, blocked=block,
        )
        with events.activate(root, run_id, project):
            outcome = schedule(
                order, reg, work, workers=workers or default_workers(), hooks=hooks
            )
        state.finish_run(run_id, "failed" if failed else "done")
    finally:
        state.close()

    decisions += board.process_change_requests(root, project)  # what the stages asked for
    mismatches = write_mismatches(
        root / "projects" / project / "build", board.load_contract(root, project)
    )
    return RunReport(
        run_id=run_id,
        results=[outcome.done[name] for name in order if name in outcome.done],
        failed={name: failed[name] for name in order if name in failed},
        blocked={name: outcome.blocked[name] for name in order if name in outcome.blocked},
        decisions=decisions,
        mismatches=mismatches,
        resumed_run=continued,
        errors={name: outcome.failed[name] for name in order if name in outcome.failed},
    )


def run_pipeline(
    project: str,
    root: Path,
    *,
    registry: graph.Registry | None = None,
    store: Path | None = None,
    on_result: Callable[[StageResult], None] | None = None,
    workers: int | None = None,
    resume: bool = False,
) -> list[StageResult]:
    """Run every stage of `project` (see `execute`) and return the results in dependency order.

    If any stage failed, everything that could still run has run, and RunFailed is raised
    (its `report` has the full picture). Raises SpecError, GraphError, BoardError, HashError or
    CacheError for problems found before any stage runs.
    """
    report = execute(
        project, root, registry=registry, store=store, on_result=on_result, workers=workers,
        resume=resume,
    )
    if report.failed:
        blocked = sorted(report.blocked)
        message = "; ".join(report.failed.values())
        if blocked:
            message += f" (not run, because they need a failed stage: {', '.join(blocked)})"
        first = next(iter(report.errors.values()))
        raise RunFailed(message, report) from (first.__cause__ or first)
    return report.results


def _default_registry() -> graph.Registry:
    from engenia.stages.registry import REGISTRY

    return REGISTRY
