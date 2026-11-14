"""`engenia gate <project>`: the final, script-only judge of a project.

Checks, in this order (all decisions are made by scripts; no model is called):

  approval  checks/APPROVED holds the hash of the current checks/checks.yaml, and the plan still
            validates against the requirements and the verifiers (the verify_plan rules)
  G1        every item in checks.yaml is run, by the verifier it names, against the stage that
            produces the facts; every Verdict must pass
  G2        every Verdict's result agrees with its measured value and limit, recomputed here.
            Covers the Verdicts of G1 and every build/<stage>/verdict.json on disk, so a
            hand-edited "pass" is caught
  G3        every stage is rebuilt from src/ in a scratch copy of the project and the output
            hashes are compared with build/ (transcripts/, interface.json and verdict.json are
            not outputs); interface.json is compared too
  G4        every approved requirement has a Verdict with result pass or fail
  G5        every verifier in use is run once on a fixture it must fail, and must say fail

Requirements whose verifier script does not exist yet are reported as "unverified". They are not
errors and do not fail G1 or G4. The gate exits 0 only if G1 passes and approval, G2, G3, G4 and
G5 hold. build/gate/report.json holds the details and contains no timestamps.
"""

from __future__ import annotations

import json
import math
import shutil
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from engenia.agents.verification.stage import (
    APPROVED_FILE,
    load_requirements,
    validate_plan,
    verifier_docs,
)
from engenia.contracts.models import (
    CheckContext,
    CheckPlanItem,
    Interface,
    Limit,
    Requirement,
    RequirementStatus,
    StageContext,
    Verdict,
    VerdictResult,
)
from engenia.core import agents, events, graph
from engenia.core.hashing import file_hash, tree_files, tree_hash
from engenia.gate.canary import CASES, CONTRACT
from engenia.harness.build import run_captured
from engenia.harness.checks import _load_verifier, _run_one, error_verdict

NOT_OUTPUTS = ("interface.json", "verdict.json", "transcripts")
REPORT_RELATIVE = Path("build") / "gate" / "report.json"
COPIED_ROOT_DIRS = ("verifiers", "packs", "containers")


@dataclass
class GateResult:
    passed: bool
    report: dict[str, Any]

    @property
    def exit_code(self) -> int:
        return 0 if self.passed else 1


# --- G2: recompute a result from measured and limit ------------------------------------------


def recompute_result(measured: float | None, limit: Limit) -> VerdictResult | None:
    """pass or fail from the numbers alone. None when there is no number to compare.

    Every bound present must hold; a limit with no bound is a fail (nothing was verified).
    `equals` uses the same tiny tolerance as the verifiers.
    """
    if measured is None or not math.isfinite(measured):
        return None
    checks: list[bool] = []
    if limit.min is not None:
        checks.append(measured >= limit.min)
    if limit.max is not None:
        checks.append(measured <= limit.max)
    if limit.equals is not None:
        checks.append(math.isclose(measured, limit.equals, rel_tol=1e-9, abs_tol=1e-12))
    if not checks:
        return VerdictResult.FAIL
    return VerdictResult.PASS if all(checks) else VerdictResult.FAIL


def verdict_problem(verdict: Verdict) -> str | None:
    """Why this Verdict's result disagrees with its numbers, or None when it agrees."""
    if verdict.result == VerdictResult.ERROR:
        return None
    expected = recompute_result(verdict.measured, verdict.limit)
    if expected is None:
        return f"result {verdict.result.value} but measured is {verdict.measured!r}"
    if expected != verdict.result:
        return (
            f"result {verdict.result.value} but measured {verdict.measured} against limit "
            f"{verdict.limit.model_dump(exclude_none=True)} gives {expected.value}"
        )
    return None


# --- helpers -----------------------------------------------------------------------------------


def _requirement_ids_by_status(
    requirements: dict[str, Requirement],
) -> list[Requirement]:
    return sorted(
        (r for r in requirements.values() if r.status == RequirementStatus.APPROVED),
        key=lambda r: r.id,
    )


# Checks whose requirement is about the assembled product, not one stage's budget. They run at
# integration against the requirement's own limit; stage budgets stay separate internal checks.
REQUIREMENT_LEVEL_STAGE = {"mass_budget": "integration"}


def producing_stage(
    item: CheckPlanItem, requirement: Requirement | None, registry: graph.Registry, order: list[str]
) -> str | None:
    """The stage whose facts the check reads: the integration stage for a requirement-level
    check, else the last stage that lists the check in `checks`, else the last stage the
    requirement affects."""
    level = REQUIREMENT_LEVEL_STAGE.get(item.check)
    if level is not None and level in registry:
        return level
    listing = [s for s in order if item.check in registry[s].checks]
    if listing:
        return listing[-1]
    affects = [s for s in order if requirement is not None and s in requirement.affects]
    return affects[-1] if affects else None


def _approval(checks_path: Path) -> list[str]:
    if not checks_path.is_file():
        return [f"{checks_path.name} does not exist"]
    approved = checks_path.parent / APPROVED_FILE
    if not approved.is_file():
        return [f"checks/{APPROVED_FILE} does not exist; the check plan has not been approved"]
    wanted = file_hash(checks_path).removeprefix("sha256:")
    given = approved.read_text("utf-8").strip().lower().removeprefix("sha256:")
    if given != wanted:
        return [
            f"checks.yaml changed after approval: its hash is sha256:{wanted}, "
            f"checks/{APPROVED_FILE} holds {approved.read_text('utf-8').strip()!r}"
        ]
    return []


def _load_plan_items(path: Path) -> list[CheckPlanItem]:
    if not path.is_file():
        return []
    raw = yaml.safe_load(path.read_text("utf-8")) or []
    items = []
    for entry in raw if isinstance(raw, list) else []:
        try:
            items.append(CheckPlanItem.model_validate(entry))
        except ValueError:
            continue  # reported by the plan validation
    return items


def _verdict_json(verdict: Verdict, stage: str | None, item_id: str | None) -> dict[str, Any]:
    out = verdict.model_dump(mode="json")
    out["stage"] = stage
    out["item"] = item_id
    return out


# --- G1 ----------------------------------------------------------------------------------------


def _run_items(
    project: str,
    root: Path,
    items: list[CheckPlanItem],
    requirements: dict[str, Requirement],
    verifiers: set[str],
    registry: graph.Registry,
    order: list[str],
) -> tuple[list[tuple[Verdict, str | None, str]], list[dict[str, str]]]:
    """Run every item. Returns ((verdict, stage, item id) list, unverified entries)."""
    results: list[tuple[Verdict, str | None, str]] = []
    unverified: list[dict[str, str]] = []
    for item in items:
        requirement = requirements.get(item.requirement)
        if item.check not in verifiers:
            unverified.append(
                {
                    "requirement": item.requirement,
                    "check": item.check,
                    "status": "unverified",
                    "reason": f"no verifier verifiers/{item.check}.py yet",
                }
            )
            continue
        stage = producing_stage(item, requirement, registry, order)
        if stage is None:
            results.append(
                (
                    error_verdict(item.check, "no stage produces the facts", item.requirement),
                    None,
                    item.id,
                )
            )
            continue
        ctx = StageContext(project=project, stage=stage, root=root)  # type: ignore[arg-type]
        try:
            run = _load_verifier(root, item.check)
        except Exception as exc:
            results.append((error_verdict(item.check, str(exc), item.requirement), stage, item.id))
            continue
        results.append((_run_one(run, ctx, item.check, item), stage, item.id))
    return results, unverified


# --- G3 ----------------------------------------------------------------------------------------


def _outputs(directory: Path) -> dict[str, str]:
    return {rel: file_hash(path) for rel, path in tree_files(directory, exclude=NOT_OUTPUTS)}


def _diff(real: dict[str, str], rebuilt: dict[str, str]) -> list[str]:
    lines = [f"{name}: differs from rebuild" for name in sorted(real) if name in rebuilt
             and real[name] != rebuilt[name]]
    lines += [f"{name}: in build/ but not produced by rebuild" for name in sorted(real)
              if name not in rebuilt]
    lines += [f"{name}: produced by rebuild but missing from build/" for name in sorted(rebuilt)
              if name not in real]
    return lines


def _reproduce(
    project: str, root: Path, registry: graph.Registry, order: list[str]
) -> list[str]:
    problems: list[str] = []
    project_dir = root / "projects" / project
    with tempfile.TemporaryDirectory(prefix="engenia-gate-") as tmp:
        scratch = Path(tmp)
        ignore = shutil.ignore_patterns("__pycache__", ".git", ".venv")
        shutil.copytree(project_dir, scratch / "projects" / project, ignore=ignore)
        for name in COPIED_ROOT_DIRS:
            if (root / name).is_dir():
                shutil.copytree(root / name, scratch / name, ignore=ignore)

        for name in order:
            stage = registry[name]
            real_ctx = StageContext(project=project, stage=name, root=root)  # type: ignore[arg-type]
            ctx = StageContext(project=project, stage=name, root=scratch)  # type: ignore[arg-type]
            if not real_ctx.build_dir.is_dir():
                problems.append(f"{name}: build/{name}/ does not exist; the stage has not run")
                continue
            if ctx.build_dir.exists():
                shutil.rmtree(ctx.build_dir)
            built = run_captured(lambda s=stage, c=ctx: s.build(c))
            if built.exit_code != 0:
                detail = (built.stderr or built.stdout).strip().splitlines()[-1:] or [""]
                problems.append(f"{name}: rebuild from src/ failed: {detail[0]}")
                continue
            try:
                rebuilt = _outputs(ctx.build_dir)
                real = _outputs(real_ctx.build_dir)
                facts = stage.interface(ctx)
                interface = Interface(
                    stage=name,  # type: ignore[arg-type]
                    key=tree_hash(ctx.build_dir, exclude=NOT_OUTPUTS),
                    facts=facts,
                )
            except Exception as exc:
                problems.append(f"{name}: cannot compare rebuild: {type(exc).__name__}: {exc}")
                continue
            (ctx.build_dir / "interface.json").write_text(
                json.dumps(interface.model_dump(mode="json"), sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
            problems += [f"{name}: {line}" for line in _diff(real, rebuilt)]
            try:
                recorded = json.loads(real_ctx.stage_interface_path(name).read_text("utf-8"))
            except (OSError, ValueError):
                problems.append(f"{name}: build/{name}/interface.json is missing or unreadable")
                continue
            expected = interface.model_dump(mode="json")
            if recorded.get("key") != expected["key"]:
                problems.append(f"{name}: interface.json key differs from the rebuilt outputs")
            if recorded.get("facts") != json.loads(json.dumps(expected["facts"])):
                problems.append(f"{name}: interface.json facts differ from the rebuild")
    return problems


# --- G5 ----------------------------------------------------------------------------------------


def _canary(root: Path, name: str) -> str | None:
    """None when the verifier says fail on its fixture; otherwise a problem description."""
    case = CASES.get(name)
    if case is None:
        return f"{name}: no canary fixture (add one to engenia/gate/canary.py)"
    with tempfile.TemporaryDirectory(prefix="engenia-canary-") as tmp:
        scratch = Path(tmp)
        build = scratch / "projects" / "_canary" / "build"
        for stage, facts in case.interfaces.items():
            (build / stage).mkdir(parents=True)
            (build / stage / "interface.json").write_text(
                json.dumps({"stage": stage, "key": "sha256:" + "0" * 64, "facts": facts}),
                encoding="utf-8",
            )
        contract = scratch / "projects" / "_canary" / "contract" / "contract.json"
        contract.parent.mkdir(parents=True)
        contract.write_text(json.dumps(CONTRACT), encoding="utf-8")
        for relative, data in case.files.items():
            path = scratch / "projects" / "_canary" / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(yaml.safe_dump(data), encoding="utf-8")
        try:
            run = _load_verifier(root, name)
            ctx = CheckContext(
                project="_canary",
                stage=case.stage,  # type: ignore[arg-type]
                root=scratch,
                check=name,
                requirement="R-000",
                params=case.params,
                limit=Limit(**case.limit),
            )
            verdict = run(ctx)
        except Exception as exc:
            return f"{name}: canary run raised {type(exc).__name__}: {exc}"
    if not isinstance(verdict, Verdict):
        return f"{name}: canary returned {type(verdict).__name__}, not Verdict"
    if verdict.result != VerdictResult.FAIL:
        return f"{name}: returned {verdict.result.value} on a fixture that must fail"
    if (problem := verdict_problem(verdict)) is not None:
        return f"{name}: canary verdict is inconsistent: {problem}"
    return None


# --- the gate ----------------------------------------------------------------------------------


def run_gate(
    project: str,
    root: Path,
    *,
    registry: graph.Registry | None = None,
    write_report: bool = True,
    stages: Sequence[str] | None = None,
) -> GateResult:
    """Run the gate. `stages` limits G3 (rebuild) and the stored-verdict part of G2 to those
    stages. The default is every stage except `final`, whose output is made by the gate itself
    (rebuilding it would run the gate again, without end).

    Each gate step is logged as a gate_step event (start, then end with result and the items
    it covered), and the approval check as an approval event (needed/given, with the plan's
    hash). The events go to the active run's log, else to the project's newest one; they are
    a side channel and never affect the gate's decisions."""
    root = Path(root)
    if registry is None:
        from engenia.stages.registry import REGISTRY

        registry = REGISTRY
    with events.attach(root, project):
        return _run_gate(
            project, root, registry, write_report=write_report, stages=stages,
        )


def _run_gate(
    project: str,
    root: Path,
    registry: graph.Registry,
    *,
    write_report: bool = True,
    stages: Sequence[str] | None = None,
) -> GateResult:
    gate_agent = agents.gate_agent().as_log()

    def step(name: str, action: str, *, result: str | None = None, items: list | None = None
             ) -> None:
        data: dict[str, Any] = {"gate": name, "action": action}
        if result is not None:
            data["result"] = result
        if items is not None:
            data["items"] = items
        events.emit(root, None, "gate_step", data, agent=gate_agent)

    order = graph.topo_order(registry)
    covered = [n for n in order if (n in stages if stages is not None else n != "final")]
    project_dir = root / "projects" / project
    checks_path = project_dir / "checks" / "checks.yaml"

    requirements = load_requirements(project_dir / "spec")
    approved = _requirement_ids_by_status(requirements)
    verifiers = set(verifier_docs(root))

    # approval, and the plan still valid
    approval_problems = _approval(checks_path)
    if checks_path.is_file():
        plan = validate_plan(checks_path.read_text("utf-8"), requirements, verifiers)
        approval_problems += [f"plan: {p}" for p in plan.problems]
    items = _load_plan_items(checks_path)
    wanted_hash = (
        file_hash(checks_path).removeprefix("sha256:") if checks_path.is_file() else None
    )
    events.emit(root, None, "approval", {
        "status": "needed" if approval_problems else "given",
        "hash": f"sha256:{wanted_hash}" if wanted_hash else None,
        "problems": approval_problems,
    }, agent=gate_agent)

    # G1
    step("G1", "start")
    results, unverified = _run_items(
        project, root, items, requirements, verifiers, registry, order
    )
    verdicts = [verdict for verdict, _, _ in results]
    g1 = [
        f"{item_id}: {v.check} for {v.requirement}: {v.result.value}"
        + (f" ({v.message})" if v.message else "")
        for v, _, item_id in results
        if v.result != VerdictResult.PASS
    ]
    g1 += [
        f"{u['check']} for {u['requirement']}: unverified ({u['reason']})" for u in unverified
    ]
    step("G1", "end", result="fail" if g1 else "pass",
         items=[item.id for item in items])

    # G2
    step("G2", "start")
    g2: list[str] = []
    items_by_id = {i.id: i for i in items}
    for verdict, _, item_id in results:
        item = items_by_id[item_id]  # a duplicate id keeps the last item; the plan check flags it
        if verdict.check != item.check or verdict.requirement != item.requirement:
            g2.append(
                f"{item_id}: verdict is for {verdict.check}/{verdict.requirement}, "
                f"not {item.check}/{item.requirement}"
            )
        if (problem := verdict_problem(verdict)) is not None:
            g2.append(f"{item_id}: {problem}")
    for name in covered:
        path = project_dir / "build" / name / "verdict.json"
        if not path.is_file():
            continue
        try:
            raw = json.loads(path.read_text("utf-8"))
            stored = [Verdict.model_validate(entry) for entry in raw]
        except (OSError, ValueError, TypeError) as exc:
            g2.append(f"build/{name}/verdict.json is unreadable: {exc}")
            continue
        for index, verdict in enumerate(stored):
            if verdict.result != VerdictResult.PASS:
                g2.append(
                    f"build/{name}/verdict.json[{index}] {verdict.check}: stored result is "
                    f"{verdict.result.value}"
                    + (f" ({verdict.message})" if verdict.message else "")
                )
            if (problem := verdict_problem(verdict)) is not None:
                g2.append(f"build/{name}/verdict.json[{index}] {verdict.check}: {problem}")
    step("G2", "end", result="fail" if g2 else "pass", items=[item.id for item in items])

    # G3
    step("G3", "start")
    g3 = _reproduce(project, root, registry, covered)
    step("G3", "end", result="fail" if g3 else "pass", items=list(covered))

    # G4
    step("G4", "start")
    g4: list[str] = []
    unverified_ids: list[str] = []
    for requirement in approved:
        if requirement.verify.check not in verifiers:
            unverified_ids.append(requirement.id)
            if not any(u["requirement"] == requirement.id for u in unverified):
                unverified.append(
                    {
                        "requirement": requirement.id,
                        "check": requirement.verify.check,
                        "status": "unverified",
                        "reason": f"no verifier verifiers/{requirement.verify.check}.py yet",
                    }
                )
            g4.append(
                f"{requirement.id}: no runnable verifier verifiers/{requirement.verify.check}.py"
            )
            continue
        if not any(
            v.requirement == requirement.id
            and v.result in (VerdictResult.PASS, VerdictResult.FAIL)
            for v in verdicts
        ):
            g4.append(f"{requirement.id}: no pass or fail Verdict")
    step("G4", "end", result="fail" if g4 else "pass",
         items=[r.id for r in approved])

    # G5
    step("G5", "start")
    g5 = [
        problem
        for name in sorted({i.check for i in items if i.check in verifiers})
        if (problem := _canary(root, name)) is not None
    ]
    step("G5", "end", result="fail" if g5 else "pass",
         items=sorted({i.check for i in items if i.check in verifiers}))

    gates = {
        "approval": approval_problems,
        "G1": g1,
        "G2": g2,
        "G3": g3,
        "G4": g4,
        "G5": g5,
    }
    passed = all(not problems for problems in gates.values())
    unverified.sort(key=lambda u: (u["requirement"], u["check"]))
    report: dict[str, Any] = {
        "project": project,
        "passed": passed,
        "gates": {
            name: {"passed": not problems, "problems": problems}
            for name, problems in gates.items()
        },
        "verdicts": [_verdict_json(v, stage, item_id) for v, stage, item_id in results],
        "unverified": unverified,
    }
    if write_report:
        target = project_dir / REPORT_RELATIVE
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return GateResult(passed, report)


def format_report(result: GateResult) -> list[str]:
    lines = []
    for name, gate in result.report["gates"].items():
        lines.append(f"{name}: {'pass' if gate['passed'] else 'FAIL'}")
        lines += [f"  - {problem}" for problem in gate["problems"]]
    for entry in result.report["unverified"]:
        lines.append(f"unverified: {entry['requirement']} ({entry['reason']})")
    lines.append(f"gate {result.report['project']}: {'pass' if result.passed else 'FAIL'}")
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    """python -m engenia.gate <project>. Also what `engenia gate` should call."""
    from engenia.core.paths import find_root

    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: python -m engenia.gate <project>", file=sys.stderr)
        return 2
    root = find_root()
    if not (root / "projects" / args[0]).is_dir():
        print(f"error: unknown project: {args[0]}", file=sys.stderr)
        return 1
    result = run_gate(args[0], root)
    for line in format_report(result):
        print(line)
    return result.exit_code
