"""Run independent stages at the same time, never before the stages they need.

The scheduler knows nothing about builds or caches. It is given the stage graph, a `work`
function that runs one stage, and a set of hooks, and it does three things:

* starts a stage only when every stage it needs finished successfully;
* keeps up to `workers` stages running at once;
* runs a stage that declares `main_thread = True` on the calling (main) thread, alone: it starts
  only when no other stage is running and nothing else starts until it ends (gmsh and capytaine
  need the main thread and are process-global);
* when a stage fails, marks every stage that needs it (directly or through others) as blocked
  and carries on with everything else.

Hooks and bookkeeping run in the calling thread only. Stage code runs in worker threads. Stages
are started in topological order and results are reported to the hooks in topological order
within one wake-up, so the same graph and the same outcomes give the same hook calls whatever
the timing, apart from which stages happen to overlap.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field

from engenia.core.graph import Registry


@dataclass
class Hooks[T]:
    """Called from the thread that called `schedule`, never from a worker."""

    started: Callable[[str], None] = lambda stage: None
    done: Callable[[str, T], None] = lambda stage, result: None
    failed: Callable[[str, Exception], None] = lambda stage, error: None
    blocked: Callable[[str, list[str]], None] = lambda stage, causes: None


@dataclass
class Outcome[T]:
    done: dict[str, T] = field(default_factory=dict)
    failed: dict[str, Exception] = field(default_factory=dict)
    blocked: dict[str, list[str]] = field(default_factory=dict)  # stage -> failed root causes


def schedule[T](
    order: Sequence[str],
    registry: Registry,
    work: Callable[[str], T],
    *,
    workers: int,
    hooks: Hooks[T] | None = None,
) -> Outcome[T]:
    """Run `work(stage)` for every stage in `order` (a topological order of `registry`)."""
    hooks = hooks or Hooks()
    workers = max(1, workers)
    position = {name: i for i, name in enumerate(order)}
    outcome: Outcome[T] = Outcome()
    pending = list(order)
    running: dict[Future[T], str] = {}

    def causes_of(name: str) -> list[str]:
        roots: set[str] = set()
        for need in registry[name].needs:
            if need in outcome.failed:
                roots.add(need)
            elif need in outcome.blocked:
                roots.update(outcome.blocked[need])
        return sorted(roots, key=position.__getitem__)

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="engenia-stage") as pool:
        while pending or running:
            # A stage is blocked once everything it needs has settled and some of it failed, so its
            # recorded causes do not depend on which failure happened to be noticed first.
            # Topological order, so a stage blocked here settles its own dependants in this pass.
            for name in list(pending):
                needs = registry[name].needs
                settled = all(n in outcome.done or n in outcome.failed or n in outcome.blocked
                              for n in needs)
                causes = causes_of(name) if settled else []
                if causes:
                    pending.remove(name)
                    outcome.blocked[name] = causes
                    hooks.blocked(name, causes)
            ran_inline = False
            for name in list(pending):
                if len(running) >= workers:
                    break
                if not all(need in outcome.done for need in registry[name].needs):
                    continue
                if getattr(registry[name], "main_thread", False):
                    if running:
                        break  # let the running stages drain, start nothing new meanwhile
                    pending.remove(name)
                    hooks.started(name)
                    try:
                        result = work(name)
                    except Exception as exc:  # a stage failing is an outcome, not a crash
                        outcome.failed[name] = exc
                        hooks.failed(name, exc)
                    else:
                        outcome.done[name] = result
                        hooks.done(name, result)
                    ran_inline = True
                    break
                pending.remove(name)
                hooks.started(name)
                running[pool.submit(work, name)] = name
            if not running:
                if ran_inline:
                    continue
                break  # nothing can start: only possible if the graph was not a DAG
            finished, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in sorted(finished, key=lambda f: position[running[f]]):
                name = running.pop(future)
                try:
                    result = future.result()
                except Exception as exc:  # a stage failing is an outcome, not a crash
                    outcome.failed[name] = exc
                    hooks.failed(name, exc)
                else:
                    outcome.done[name] = result
                    hooks.done(name, result)
    return outcome
