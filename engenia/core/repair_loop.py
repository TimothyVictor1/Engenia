"""The outer repair loop: `engenia run --repair`.

After a run, the gate decides. If it fails because verdicts failed, the loop does, at most
MAX_ROUNDS times:

    1. for every stage with a failed verdict, call `repair_stage(stage, ctx, failed)` (the
       harness asks the design model to fix the stage's source and checks the result itself);
    2. run the pipeline again, so repaired stages and everything downstream rebuild;
    3. run the gate again.

Each repair round is written to that stage's row in stage_run (the run keeps one row per stage,
so the row tells the whole story: "repair round 1: repaired; ...").

If the gate still fails after MAX_ROUNDS rounds (or the harness has no model, in which case more
rounds could not help), the loop stops repairing the stage and calls
`propose_change(requirement, failed, ctx=ctx)` for each requirement with a failed verdict. That
writes spec/proposals/R-xxx.yaml. A proposal is never applied here: `engenia approve <p>
--proposal R-xxx` is how a person accepts one.

A gate that fails for a reason no stage repair can touch (the plan was not approved, a stage did
not rebuild identically, no verdicts at all) ends the loop at once and is reported as it is.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from engenia.contracts.models import StageContext, Verdict, VerdictResult
from engenia.core import agents as agent_registry
from engenia.core import events, graph, spec
from engenia.core.engine import RunReport, StageResult, execute
from engenia.core.runstate import RunStore

MAX_ROUNDS = 3


@dataclass
class RepairReport:
    run: RunReport  # the last pipeline run
    gate: Any  # the last GateResult, or None when the pipeline itself failed
    rounds: list[dict] = field(default_factory=list)  # {"round", "stage", "outcome"}
    proposals: list[Path] = field(default_factory=list)
    no_proposal: dict[str, str] = field(default_factory=dict)  # requirement -> why not
    reason: str = ""  # why the loop stopped without a passing gate; empty when it passed

    @property
    def passed(self) -> bool:
        return self.gate is not None and self.gate.passed


def failed_verdicts_by_stage(report: dict[str, Any]) -> dict[str, list[Verdict]]:
    """The failed or errored verdicts of a gate report, grouped by the stage that produced them."""
    grouped: dict[str, list[Verdict]] = {}
    for entry in report.get("verdicts", []):
        stage = entry.get("stage")
        if stage is None or entry.get("result") == VerdictResult.PASS.value:
            continue
        fields = {k: v for k, v in entry.items() if k not in ("stage", "item")}
        verdict = Verdict.model_validate(fields)
        grouped.setdefault(stage, []).append(verdict)
    return grouped


def run_with_repair(
    project: str,
    root: Path,
    *,
    registry: graph.Registry | None = None,
    store: Path | None = None,
    workers: int | None = None,
    resume: bool = False,
    on_result: Callable[[StageResult], None] | None = None,
    max_rounds: int = MAX_ROUNDS,
    gate: Callable[..., Any] | None = None,
    repair: Callable[..., str] | None = None,
    propose: Callable[..., Path] | None = None,
) -> RepairReport:
    """Run the pipeline and the gate, repairing failed stages. See the module docstring.

    `gate`, `repair` and `propose` default to the real run_gate, repair_stage and propose_change;
    they are parameters so tests can stand in for the model.
    """
    root = Path(root)
    if gate is None:
        from engenia.gate.gate import run_gate as gate
    if repair is None:
        from engenia.harness.repair import repair_stage as repair
    if propose is None:
        from engenia.agents.intake import propose_change as propose
    if registry is None:
        from engenia.stages.registry import REGISTRY as registry

    common = {"registry": registry, "store": store, "workers": workers}
    run = execute(project, root, on_result=on_result, resume=resume, **common)
    result = RepairReport(run=run, gate=None)
    verdict_gate = _checked_gate(run, project, root, registry, gate)
    if verdict_gate is None:
        result.reason = "stages failed; nothing to repair: " + "; ".join(run.failed.values())
        return result
    run_id = run.run_id
    result.gate = verdict_gate
    rounds = 0
    no_model = False
    while not verdict_gate.passed and rounds < max_rounds:
        failures = failed_verdicts_by_stage(verdict_gate.report)
        if not failures:
            result.reason = "the gate failed for reasons no stage repair can change"
            break
        rounds += 1
        order = [name for name in graph.topo_order(registry) if name in failures]
        for name in order:
            ctx = StageContext(project=project, stage=name, root=root)  # type: ignore[arg-type]
            with events.activate(root, run_id, project):
                with events.as_agent(root, name, agent_registry.repair_agent().as_log()):
                    with events.phase(root, name, "repair"):
                        outcome = repair(registry[name], ctx, failures[name])
                events.emit(root, name, "repair", {
                    "phase": "round", "round": rounds, "outcome": outcome,
                    "failed": [v.check for v in failures[name]],
                })
            result.rounds.append({"round": rounds, "stage": name, "outcome": outcome})
            _record(root, lambda s, n=name, o=outcome, r=rounds: s.stage_repair(
                run_id, n, round_number=r, outcome=o))
            no_model = no_model or outcome == "no_model"
        if no_model:
            result.reason = "no design model is configured, so repair cannot run"
            break
        run = execute(project, root, on_result=on_result, run_id=run_id, **common)
        result.run = run
        with events.activate(root, run_id, project):
            verdict_gate = _checked_gate(run, project, root, registry, gate)
        result.gate = verdict_gate
        if verdict_gate is None:
            result.reason = "stages failed after repair: " + "; ".join(run.failed.values())
            break

    if result.gate is not None and not result.gate.passed:
        failures = failed_verdicts_by_stage(result.gate.report)
        with events.activate(root, run_id, project):
            _propose(project, root, failures, propose, result)
        if not result.reason:
            result.reason = f"the gate still fails after {rounds} repair round(s)"
        _record_failure(root, run_id, failures, result)
    elif result.passed:
        _record(root, lambda s: s.finish_run(run_id, "done"))
    return result


def _checked_gate(run, project, root, registry, gate):
    """Use early scripted failures for repair; incomplete builds cannot run the full gate."""
    if run.ok:
        return gate(project, root, registry=registry)
    from engenia.gate.gate import GateResult

    entries = []
    for name, message in run.failed.items():
        if "failed in checks:" not in message:
            return None  # generation/build exceptions still stop before model repair
        path = root / "projects" / project / "build" / name / "verdict.json"
        try:
            stored = [Verdict.model_validate(v) for v in json.loads(path.read_text())]
        except (OSError, ValueError, TypeError):
            return None
        failures = [v for v in stored if v.result != VerdictResult.PASS]
        if not failures:
            return None
        entries += [dict(v.model_dump(mode="json"), stage=name) for v in failures]
    return GateResult(passed=False, report={"passed": False, "verdicts": entries})


def _record(root: Path, action: Callable[[RunStore], None]) -> None:
    state = RunStore(root)
    try:
        action(state)
    finally:
        state.close()


def _propose(
    project: str,
    root: Path,
    failures: dict[str, list[Verdict]],
    propose: Callable[..., Path],
    result: RepairReport,
) -> None:
    """One proposal per requirement that has a failed verdict. Nothing is applied."""
    requirements = spec.load_requirements(root / "projects" / project / "spec")
    by_requirement: dict[str, tuple[str, list[Verdict]]] = {}
    for stage, verdicts in failures.items():
        for verdict in verdicts:
            if verdict.requirement is not None:
                entry = by_requirement.setdefault(verdict.requirement, (stage, []))
                entry[1].append(verdict)
    repair = agent_registry.repair_agent().as_log()
    for requirement_id in sorted(by_requirement):
        stage, verdicts = by_requirement[requirement_id]
        requirement = next((r for r in requirements if r.id == requirement_id), None)
        if requirement is None:
            result.no_proposal[requirement_id] = "not in the spec"
            continue
        ctx = StageContext(project=project, stage=stage, root=root)  # type: ignore[arg-type]
        try:
            with events.as_agent(root, stage, repair), events.phase(root, stage, "propose"):
                written = propose(requirement, verdicts, ctx=ctx)
                result.proposals.append(written)
                events.emit(root, stage, "proposal", {
                    "requirement": requirement_id, "file": written.name,
                })
        except ValueError as exc:
            result.no_proposal[requirement_id] = str(exc)


def _record_failure(
    root: Path, run_id: int, failures: dict[str, list[Verdict]], result: RepairReport
) -> None:
    proposed = {p.stem: p for p in result.proposals}

    def write(state: RunStore) -> None:
        for stage, verdicts in failures.items():
            ids = sorted({v.requirement for v in verdicts if v.requirement})
            made = [f"proposed change to {i} ({proposed[i].name}; needs approval)"
                    for i in ids if i in proposed]
            note = "gate still failing" + (": " + ", ".join(made) if made else "")
            state.stage_note(run_id, stage, note, status="failed")
        state.finish_run(run_id, "failed")

    _record(root, write)
