"""`engenia new`: from a written request to a released design, with a person at every approval.

    1. create       projects/<p>/ with spec/ and the pack's contract
    2. intake       draft requirements (the model if configured, else the pack template)
    3. approve      the drafted requirements          <- a person (or --yes)
    4. run          every stage, which includes planning the checks (verify_plan)
    5. approve      the check plan                    <- a person (or --yes)
    6. repair       the gate, then up to 3 repair rounds per failing stage, then proposals
    7. release      only if the gate passed

The loop stops at the first step that cannot go on and says which, leaving everything done so far
in place (drafts in spec/, builds in build/, proposals in spec/proposals/). Nothing here decides a
pass: the gate does. A proposal is never applied; `engenia approve <p> --proposal R-xxx` is how a
person takes one, after which `engenia new` is not needed again: use `engenia run --repair`.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from engenia.core import approval, graph
from engenia.core.engine import RunReport, execute
from engenia.core.errors import CoreError
from engenia.core.intake_run import (
    INTENTS_TITLE,
    IntakeFailed,
    requirement_lines,
    run_intake,
)
from engenia.core.lint import Severity, lint_spec_dir
from engenia.core.release import ReleaseError, release

PACKS = ("buoy", "enclosure")
Confirm = Callable[[str, list[str]], bool]  # (question, lines to show) -> approved?


class NewProjectError(CoreError):
    """The project cannot be created (bad name, or it already exists)."""


@dataclass
class NewReport:
    project: str
    pack: str
    requirements: list[dict] = field(default_factory=list)  # id, statement, check
    stages: dict[str, int] = field(default_factory=dict)  # ran / cached / failed / blocked
    verdicts: dict[str, int] = field(default_factory=dict)  # pass / fail / error
    repairs: list[dict] = field(default_factory=list)
    proposals: list[Path] = field(default_factory=list)
    release_path: Path | None = None
    stopped: str | None = None  # why the flow ended before a release
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.release_path is not None


def _pack_contract(root: Path, pack: str) -> Path | None:
    repo = Path(__file__).resolve().parents[2]
    for base in (Path(root), repo):
        for name in (pack, "buoy"):  # a pack without its own contract uses the buoy default
            path = base / "packs" / name / "default_contract.json"
            if path.is_file():
                return path
    return None


def _create(project: str, root: Path, pack: str) -> None:
    if not project or not project.replace("-", "").replace("_", "").isalnum():
        raise NewProjectError(f"invalid project name: {project!r}")
    directory = Path(root) / "projects" / project
    if directory.exists():
        raise NewProjectError(
            f"projects/{project} already exists; `engenia new` only starts new projects "
            f"(use `engenia intake` and `engenia run` on an existing one)"
        )
    (directory / "spec").mkdir(parents=True)
    contract = _pack_contract(root, pack)
    if contract is not None:
        (directory / "contract").mkdir()
        shutil.copyfile(contract, directory / "contract" / "contract.json")


def _describe(path: Path) -> dict:
    data = yaml.safe_load(path.read_text("utf-8"))
    return {"id": data["id"], "statement": data["statement"], "check": data["verify"]["check"]}


def _count(items: list[str]) -> dict[str, int]:
    return {name: items.count(name) for name in sorted(set(items))}


def new_project(
    request: str,
    project: str,
    root: Path,
    *,
    pack: str = "buoy",
    confirm: Confirm,
    echo: Callable[[str], None] = lambda line: None,
    registry: graph.Registry | None = None,
    store: Path | None = None,
    workers: int | None = None,
    gate: Callable[..., Any] | None = None,
    repair: Callable[..., str] | None = None,
    propose: Callable[..., Path] | None = None,
) -> NewReport:
    root = Path(root)
    if pack not in PACKS:
        raise NewProjectError(f"unknown pack {pack!r}; choose one of {', '.join(PACKS)}")
    report = NewReport(project, pack)
    _create(project, root, pack)
    echo(f"1. created projects/{project} ({pack} pack)")
    if pack != "buoy":
        report.notes.append(
            f"the {pack} pack has no stage support yet: the stages still build with the buoy "
            "templates and parts (see packs/enclosure/README.md)"
        )

    # 2. intake
    try:
        intake = run_intake(request, project, root, pack=pack)
    except IntakeFailed as exc:
        report.stopped = f"intake: {exc}"
        return report
    spec_dir = root / "projects" / project / "spec"
    if any(f.severity is Severity.ERROR for f in lint_spec_dir(spec_dir, root / "verifiers")):
        report.stopped = "intake: the drafted requirements do not pass lint"
        return report
    drafts = [_describe(p) for p in intake.paths]
    echo(f"2. intake: {len(drafts)} draft requirement(s) from the {intake.how}")

    # 3. approve the requirements
    shown = [line for p in intake.paths for line in requirement_lines(p)]
    if intake.intents:
        shown.append(INTENTS_TITLE)
        shown.extend(f"  - {i.get('intent')} ({i.get('reason')})" for i in intake.intents)
    if not confirm("Approve these requirements?", shown):
        report.stopped = "the requirements were not approved (drafts are in spec/)"
        return report
    approval.approve_requirements(intake.paths)
    report.requirements = drafts
    echo("3. requirements approved")

    # 4. run (this plans the checks)
    run: RunReport = execute(project, root, registry=registry, store=store, workers=workers)
    report.stages = _count(
        [("ran" if r.status.value == "ran" else "cached") for r in run.results]
        + ["failed"] * len(run.failed) + ["blocked"] * len(run.blocked)
    )
    echo(f"4. run: {', '.join(f'{n} {k}' for k, n in report.stages.items())}")
    if not run.ok:
        # A stage that failed its own scripted checks (mass_budget, ...) is repaired now, up to 3
        # rounds, then a change is proposed. The stages that need it stay blocked until it passes.
        from engenia.core.repair_loop import run_with_repair

        fixing = run_with_repair(
            project, root, registry=registry, store=store, workers=workers, gate=gate,
            repair=repair, propose=propose, initial_run=run, stages_only=True,
        )
        report.repairs += fixing.rounds
        report.proposals += fixing.proposals
        run = fixing.run
        report.stages = _count(
            [("ran" if r.status.value == "ran" else "cached") for r in run.results]
            + ["failed"] * len(run.failed) + ["blocked"] * len(run.blocked)
        )
        if fixing.rounds:
            echo(f"4. repair: {len(fixing.rounds)} round(s); now "
                 f"{', '.join(f'{n} {k}' for k, n in report.stages.items())}")
    if not run.ok:
        report.stopped = "stages failed: " + "; ".join(run.failed.values())
        return report

    # 5. approve the check plan
    try:
        preview = approval.preview_plan(project, root)
    except approval.ApprovalError as exc:
        report.stopped = f"check plan: {exc}"
        return report
    if not preview.already and not confirm("Approve this check plan?", preview.lines):
        report.stopped = "the check plan was not approved"
        return report
    approval.apply_plan(project, root)
    echo("5. check plan approved")

    # 6. gate, repair, proposals
    from engenia.core.repair_loop import run_with_repair

    loop = run_with_repair(
        project, root, registry=registry, store=store, workers=workers,
        gate=gate, repair=repair, propose=propose,
    )
    report.repairs += loop.rounds
    report.proposals += loop.proposals
    if loop.gate is not None:
        verdicts = loop.gate.report.get("verdicts", [])
        report.verdicts = _count([v["result"] for v in verdicts])
    echo(f"6. gate: {'pass' if loop.passed else 'FAIL'}"
         + (f" after {len(loop.rounds)} repair round(s)" if loop.rounds else ""))
    if not loop.passed:
        report.stopped = loop.reason or "the gate did not pass"
        return report

    # 7. release
    try:
        report.release_path = release(project, root, gate=gate)
    except (ReleaseError, CoreError) as exc:
        report.stopped = f"release: {exc}"
        return report
    echo(f"7. released {report.release_path}")
    return report


def summary(report: NewReport) -> list[str]:
    """The short step-by-step summary printed at the end."""
    def counts(found: dict[str, int]) -> str:
        return ", ".join(f"{n} {k}" for k, n in found.items()) or "none"

    lines = [f"engenia new {report.project} ({report.pack} pack)"]
    lines.append(f"  requirements: {len(report.requirements)}"
                 + "".join(f"\n    {r['id']} {r['check']}" for r in report.requirements))
    lines.append(f"  stages:       {counts(report.stages)}")
    lines.append(f"  verdicts:     {counts(report.verdicts)}")
    lines.append(f"  repairs:      {len(report.repairs)} round(s)"
                 + "".join(f"\n    round {r['round']} {r['stage']}: {r['outcome']}"
                           for r in report.repairs))
    lines.append(f"  proposals:    {len(report.proposals)}"
                 + "".join(f"\n    {p} (not applied)" for p in report.proposals))
    lines.append(f"  release:      {report.release_path or 'none'}")
    lines += [f"  note: {note}" for note in report.notes]
    if report.stopped:
        lines.append(f"  STOPPED: {report.stopped}")
    return lines
