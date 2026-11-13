"""The engenia command line (PROJECT.md section 6, CLI commands).

lint and run work; plan, gate, matrix and status print "not implemented".
"""

from __future__ import annotations

from pathlib import Path

import typer

from engenia.core.engine import StageResult, Status, run_pipeline
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
def run(project: str) -> None:
    """Run every stage serially in dependency order, rerunning only what changed."""
    _project_dir(project)

    def show(result: StageResult) -> None:
        label = "ran " if result.status is Status.RAN else "hit "
        typer.echo(f"{label} {result.stage}")

    try:
        results = run_pipeline(project, find_root(), on_result=show)
    except SpecError as exc:
        for problem in exc.problems:
            typer.echo(f"error: {problem}", err=True)
        raise typer.Exit(1) from None
    except CoreError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from None
    ran = sum(r.status is Status.RAN for r in results)
    typer.echo(f"done: {ran} ran, {len(results) - ran} from cache")


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
    """Write the trace matrix."""
    typer.echo("not implemented")


@app.command()
def status(project: str) -> None:
    """Show run state and open change requests."""
    typer.echo("not implemented")
