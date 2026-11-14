"""The engenia command line (PROJECT.md section 6, CLI commands).

lint, run, gate and matrix work; plan and status print "not implemented".
"""

from __future__ import annotations

from pathlib import Path

import typer

from engenia.core.board import BoardError
from engenia.core.engine import StageResult, Status, default_workers, execute
from engenia.core.errors import CoreError
from engenia.core.lint import Severity, lint_spec_dir, report_lines
from engenia.core.paths import find_root
from engenia.core.spec import SpecError

app = typer.Typer(
    name="engenia",
    help="Engenia: from a written product request to a verified design.",
    no_args_is_help=True,
    add_completion=False,
)


def _project_dir(project: str) -> Path:
    path = find_root() / "projects" / project
    if not path.is_dir():
        typer.echo(f"error: unknown project: {project}", err=True)
        raise typer.Exit(1)
    return path


@app.command()
def lint(project: str) -> None:
    """Validate and lint the spec of a project. Exits 1 if any finding is an error."""
    spec_dir = _project_dir(project) / "spec"
    findings = lint_spec_dir(spec_dir, find_root() / "verifiers")
    for line in report_lines(spec_dir, findings):
        typer.echo(line)
    errors = sum(f.severity is Severity.ERROR for f in findings)
    warnings = len(findings) - errors
    typer.echo(f"lint {project}: {errors} error(s), {warnings} warning(s)")
    if errors:
        raise typer.Exit(1)


@app.command()
def run(
    project: str,
    workers: int = typer.Option(
        default_workers(), "--workers", "-j", min=1,
        help="Stages to run at the same time. 1 runs them one by one.",
    ),
    resume: bool = typer.Option(
        False, "--resume",
        help="Continue the last run that did not finish: skip its finished stages, redo the rest.",
    ),
    repair: bool = typer.Option(
        False, "--repair",
        help="Run the gate; if verdicts fail, ask the design model to repair the stages "
        "(3 rounds), then write requirement change proposals. Proposals are never applied.",
    ),
) -> None:
    """Run every stage in dependency order, independent stages in parallel, rerunning only what
    changed. A failed stage blocks only the stages that need it."""
    _project_dir(project)

    def show(result: StageResult) -> None:
        label = "skip" if result.resumed else "ran " if result.status is Status.RAN else "hit "
        typer.echo(f"{label} {result.stage}")

    try:
        if repair:
            raise typer.Exit(_run_with_repair(project, workers, resume, show))
        report = execute(project, find_root(), on_result=show, workers=workers, resume=resume)
    except SpecError as exc:
        for problem in exc.problems:
            typer.echo(f"error: {problem}", err=True)
        raise typer.Exit(1) from None
    except (CoreError, BoardError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from None
    for decision in report.decisions:
        typer.echo(f"{decision.status.value} {decision.id} ({decision.path}): {decision.reason}")
    for name, why in report.failed.items():
        typer.echo(f"FAIL {name}: {why}", err=True)
    for name, causes in report.blocked.items():
        typer.echo(f"blocked {name}: needs failed {', '.join(causes)}", err=True)
    if report.mismatches:
        typer.echo(f"{len(report.mismatches)} interface mismatch(es): see build/mismatches.json")
    if report.contract_changed:
        typer.echo("the contract changed: run again so the stages that read it rebuild")
    ran = sum(r.status is Status.RAN for r in report.results)
    skipped = sum(r.resumed for r in report.results)
    typer.echo(
        f"done: {ran} ran, {len(report.results) - ran - skipped} from cache"
        + (f", {skipped} skipped (finished in run {report.run_id})" if skipped else "")
    )
    if report.failed:
        typer.echo(
            f"run {report.run_id} failed: {len(report.failed)} failed, "
            f"{len(report.blocked)} blocked", err=True,
        )
        raise typer.Exit(1)


def _run_with_repair(project: str, workers: int, resume: bool, show) -> int:
    from engenia.core.repair_loop import run_with_repair

    report = run_with_repair(project, find_root(), workers=workers, resume=resume, on_result=show)
    for item in report.rounds:
        typer.echo(f"repair round {item['round']}: {item['stage']}: {item['outcome']}")
    for line in report.run.failed.values():
        typer.echo(f"FAIL {line}", err=True)
    if report.passed:
        typer.echo("gate: pass")
        return 0
    typer.echo(f"gate: FAIL ({report.reason})", err=True)
    for path in report.proposals:
        typer.echo(f"proposal: {path} (not applied; review it, then `engenia approve {project} "
                   f"--proposal {path.stem}`)")
    for requirement, why in report.no_proposal.items():
        typer.echo(f"no proposal for {requirement}: {why}")
    return 1


@app.command()
def intake(
    request: str = typer.Argument(..., help="The product request, in plain language."),
    project: str = typer.Option(..., "--project", "-p", help="Project to write the spec into."),
    pack: str = typer.Option("buoy", "--pack", help="Pack whose template is used without a model."),
) -> None:
    """Turn a plain-language request into draft spec/R-xxx.yaml files that pass lint.

    Uses the design model when ENGENIA_LLM_API_KEY, ENGENIA_LLM_BASE_URL and ENGENIA_DESIGN_MODEL
    are set; otherwise a deterministic template from the pack. Files are always drafts.
    """
    from engenia.core.intake_run import IntakeFailed, run_intake

    root = find_root()
    try:
        result = run_intake(request, project, root, pack=pack)
    except IntakeFailed as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from None
    written, how = result.paths, result.how
    for path in written:
        typer.echo(f"wrote {path.relative_to(root)}")
    typer.echo(f"intake: {len(written)} draft requirement(s) from the {how}")
    spec_dir = root / "projects" / project / "spec"
    findings = lint_spec_dir(spec_dir, root / "verifiers")
    if any(f.severity is Severity.ERROR for f in findings):
        for line in report_lines(spec_dir, findings):
            typer.echo(line, err=True)
        raise typer.Exit(1)
    typer.echo(f"lint {project}: ok. Review the drafts, then set status: approved.")


@app.command()
def new(
    request: str = typer.Argument(..., help="The product request, in plain language."),
    project: str = typer.Option(..., "--project", "-p", help="Name of the new project."),
    pack: str = typer.Option("buoy", "--pack", help="Product pack: buoy or enclosure."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Approve every step without asking."),
    workers: int = typer.Option(default_workers(), "--workers", "-j", min=1),
) -> None:
    """Create a project and take it from the request to a release, asking at each approval.

    Creates the project, runs intake, asks you to approve the drafted requirements, runs the
    stages (which plans the checks), asks you to approve the check plan, runs the gate with the
    repair loop, and on a pass writes the release zip. Ends with a short summary. Without --yes
    every approval is a question to you; a change proposal is never applied.
    """
    from engenia.core.new_project import NewProjectError, new_project, summary

    def confirm(question: str, lines: list[str]) -> bool:
        for line in lines:
            typer.echo(f"  {line}")
        return True if yes else typer.confirm(question, default=False)

    try:
        report = new_project(
            request, project, find_root(), pack=pack, confirm=confirm, echo=typer.echo,
            workers=workers,
        )
    except NewProjectError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from None
    except SpecError as exc:
        for problem in exc.problems:
            typer.echo(f"error: {problem}", err=True)
        raise typer.Exit(1) from None
    except CoreError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from None
    typer.echo("")
    for line in summary(report):
        typer.echo(line)
    if not report.ok:
        raise typer.Exit(1)


@app.command()
def approve(
    project: str,
    proposal: str = typer.Option(
        None, "--proposal", help="Accept spec/proposals/<R-xxx>.yaml instead of the check plan."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask for confirmation."),
) -> None:
    """Show what changed, then approve: copy checks/checks.sha256 to checks/APPROVED, or with
    --proposal accept a requirement change proposal into spec/."""
    from engenia.core import approval

    root = find_root()
    _project_dir(project)
    try:
        if proposal:
            preview = approval.preview_proposal(project, root, proposal)
        else:
            preview = approval.preview_plan(project, root)
    except approval.ApprovalError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from None
    for line in preview.lines:
        typer.echo(line)
    if preview.already:
        return
    if not yes and not typer.confirm("Approve this?", default=False):
        typer.echo("not approved")
        raise typer.Exit(1)
    if proposal:
        approval.apply_proposal(project, root, proposal)
        typer.echo(f"approved: spec/{proposal}.yaml replaced by the proposal")
    else:
        digest = approval.apply_plan(project, root)
        typer.echo(f"approved: checks/APPROVED = {digest}")
    for note in preview.notes:
        typer.echo(f"next: {note}")


@app.command()
def release(project: str) -> None:
    """Run the gate and, only if it passes, zip the build outputs and evidence to
    release/<project>-<hash>.zip."""
    from engenia.core.release import ReleaseError
    from engenia.core.release import release as make_release

    _project_dir(project)
    try:
        path = make_release(project, find_root())
    except (ReleaseError, CoreError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from None
    typer.echo(f"released: {path}")


@app.command()
def plan(project: str) -> None:
    """Show which stages will run and why."""
    typer.echo("not implemented")


@app.command()
def gate(project: str) -> None:
    """Run all checks, reproduction and coverage."""
    from engenia.gate.gate import format_report, run_gate

    root = find_root()
    _project_dir(project)
    result = run_gate(project, root)
    for line in format_report(result):
        typer.echo(line)
    raise typer.Exit(result.exit_code)


@app.command()
def matrix(project: str) -> None:
    """Write the trace matrix (build/report/trace_matrix.md and .json) and print it."""
    from engenia.core.errors import CoreError
    from engenia.core.spec import SpecError
    from engenia.evidence.report import write_matrix

    root = find_root()
    _project_dir(project)
    try:
        _, text = write_matrix(project, root)
    except SpecError as exc:
        for problem in exc.problems:
            typer.echo(f"error: {problem}", err=True)
        raise typer.Exit(1) from None
    except CoreError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from None
    typer.echo(text)


@app.command()
def status(project: str) -> None:
    """Show run state and open change requests."""
    typer.echo("not implemented")
