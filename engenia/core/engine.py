"""The serial pipeline: key every stage, use the cache, run only what changed.

For each stage, in dependency order:

1. Compute the stage's keys (hashing.py).
2. Decide whether the agent must write code (`generate`). It must when src/<stage>/ is empty, or
   when the stage's inputs changed since the code was written. Rewriting is never done for any
   other reason, because `generate` is the only step that calls a model.
3. Look the full stage key up in the cache. A hit restores build/<stage>/ and nothing runs.
4. On a miss, run `build`, write interface.json and verdict.json, and store the result.

Change propagation needs no extra code. A downstream stage's key contains its upstream
interface.json, so a stage that reruns but writes the same interface.json leaves every
downstream key unchanged and every downstream stage is a cache hit.

How the engine knows when "the inputs changed": after `generate` it records the stage's input key
in projects/<p>/src/.generated-from/<stage>. That file is committed with the code, so a fresh
clone knows which inputs its code was written for. Code with no such record (written by hand,
or by an older run) is treated as current and is never overwritten.

interface.json holds `key`, the hash of the build outputs (not the stage key). Two runs that
produce the same outputs and the same facts therefore write byte-identical interface files, which
is what stops propagation.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from engenia.contracts.models import Interface, StageContext
from engenia.core import graph, spec
from engenia.core.cache import Cache, default_store
from engenia.core.errors import CoreError
from engenia.core.hashing import StageKeys, compute_keys, tree_files, tree_hash
from engenia.stages.base import Stage

MARKER_DIR = ".generated-from"
INTERFACE_FILE = "interface.json"
VERDICT_FILE = "verdict.json"
# Written after the outputs exist, so they are not part of the outputs hash in interface.json.
_NOT_OUTPUTS = (INTERFACE_FILE, VERDICT_FILE)


class PipelineError(CoreError):
    """A stage's own code (generate, build or interface) failed."""


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
        cache.put(keys.stage_key, ctx.build_dir)
        built = True

    status = Status.RAN if generated or built else Status.CACHED
    return StageResult(stage.name, status, generated, built, keys.stage_key)


def run_pipeline(
    project: str,
    root: Path,
    *,
    registry: graph.Registry | None = None,
    store: Path | None = None,
    on_result: Callable[[StageResult], None] | None = None,
) -> list[StageResult]:
    """Run every stage of `project` serially, in dependency order, stopping at the first failure.

    Raises SpecError, GraphError, HashError, CacheError or PipelineError (all CoreError).
    `on_result` is called after each stage so a caller can show progress.
    """
    root = Path(root)
    reg = registry if registry is not None else _default_registry()
    order = graph.topo_order(reg)
    spec.load_spec(root / "projects" / project / "spec")  # fail early on a broken spec
    cache = Cache(store if store is not None else default_store(root))

    results = []
    for name in order:
        ctx = StageContext(project=project, stage=name, root=root)
        result = _run_stage(reg[name], ctx, cache)
        results.append(result)
        if on_result:
            on_result(result)
    return results


def _default_registry() -> graph.Registry:
    from engenia.stages.registry import REGISTRY

    return REGISTRY
