"""The stage DAG, built from the stage registry.

Every function takes an optional `registry` (stage name to Stage). The default is the real
registry, imported on first use so that this module can be imported by stage code without a loop.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from engenia.contracts.models import Requirement
from engenia.core.errors import CoreError
from engenia.stages.base import Stage

Registry = Mapping[str, Stage]


class GraphError(CoreError):
    """The stage graph is not a valid DAG: unknown stage, unknown need, or a cycle."""


def _registry(registry: Registry | None) -> Registry:
    if registry is not None:
        return registry
    from engenia.stages.registry import REGISTRY

    return REGISTRY


def topo_order(registry: Registry | None = None) -> list[str]:
    """Stage names, every stage after the stages it needs. Ties follow registration order.

    Raises GraphError for a stage registered under the wrong name, a need that names no stage,
    or a cycle (the message shows the cycle).
    """
    reg = _registry(registry)
    for key, stage in reg.items():
        if stage.name != key:
            raise GraphError(f"stage registered as {key!r} calls itself {stage.name!r}")
        for need in stage.needs:
            if need not in reg:
                raise GraphError(f"stage {key!r} needs unknown stage {need!r}")

    order: list[str] = []
    done: set[str] = set()
    path: list[str] = []

    def visit(name: str) -> None:
        if name in done:
            return
        if name in path:
            cycle = [*path[path.index(name) :], name]
            raise GraphError("cycle in stage graph: " + " -> ".join(cycle))
        path.append(name)
        for need in reg[name].needs:
            visit(need)
        path.pop()
        done.add(name)
        order.append(name)

    for name in reg:
        visit(name)
    return order


def downstream(stage: str, registry: Registry | None = None) -> list[str]:
    """Every stage that needs `stage`, directly or through others, in topological order."""
    reg = _registry(registry)
    order = topo_order(reg)
    if stage not in reg:
        raise GraphError(f"unknown stage {stage!r}")
    reached = {stage}
    for name in order:  # a stage always comes after its needs, so one pass is enough
        if name not in reached and any(need in reached for need in reg[name].needs):
            reached.add(name)
    reached.discard(stage)
    return [name for name in order if name in reached]


def requirements_for(
    stage: str, requirements: Iterable[Requirement], registry: Registry | None = None
) -> list[Requirement]:
    """The requirements whose `affects` names `stage`, sorted by id."""
    reg = _registry(registry)
    if stage not in reg:
        raise GraphError(f"unknown stage {stage!r}")
    return sorted((r for r in requirements if stage in r.affects), key=lambda r: r.id)
