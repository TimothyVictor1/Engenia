"""The engenia command line (PROJECT.md section 6, CLI commands).

lint, run, gate, matrix and measure work; plan and status print "not implemented".
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
) -> None:
    """Run every stage in dependency order, independent stages in parallel, rerunning only what
    changed. A failed stage blocks only the stages that need it."""
    _project_dir(project)

    def show(result: StageResult) -> None:
        label = "ran " if result.status is Status.RAN else "hit "
        typer.echo(f"{label} {result.stage}")

    try:
        report = execute(project, find_root(), on_result=show, workers=workers)
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
    typer.echo(f"done: {ran} ran, {len(report.results) - ran} from cache")
    if report.failed:
        typer.echo(
            f"run {report.run_id} failed: {len(report.failed)} failed, "
            f"{len(report.blocked)} blocked", err=True,
        )
        raise typer.Exit(1)


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
def measure(
    project: str,
    mass_g: float = typer.Option(..., "--mass-g", help="Mass of the printed prototype, in grams."),
    waterline_mm: float = typer.Option(
        ..., "--waterline-mm",
        help="Waterline height above the hull base of the printed prototype, in millimetres.",
    ),
    tilt_deg: float | None = typer.Option(
        None, "--tilt-deg", help="Tilt of the printed prototype at rest, in degrees."
    ),
    measured_on: str | None = typer.Option(
        None, "--date", help="Date of the measurement, YYYY-MM-DD. Default: today."
    ),
) -> None:
    """Record the printed prototype's measurements, compare with the predictions, calibrate."""
    from datetime import date as date_type

    from engenia.core.spec import SpecError
    from engenia.evidence.measure import MeasureError, measure

    root = find_root()
    _project_dir(project)
    try:
        result = measure(
            project,
            root,
            mass_g=mass_g,
            waterline_mm=waterline_mm,
            tilt_deg=tilt_deg,
            date_text=measured_on or date_type.today().isoformat(),
        )
    except SpecError as exc:
        for problem in exc.problems:
            typer.echo(f"error: {problem}", err=True)
        raise typer.Exit(1) from None
    except MeasureError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from None

    def show(path: Path) -> str:
        try:
            return str(path.relative_to(root))
        except ValueError:
            return str(path)

    typer.echo(f"measurements -> {show(result.measurements_path)}")
    typer.echo(
        f"predicted mass {result.predictions.mass_g:.6g} g,"
        f" measured {result.measurements['mass_g']:.6g} g,"
        f" factor {result.calibration.mass_factor:.6g}"
    )
    typer.echo(
        f"predicted waterline {result.predictions.waterline_mm:.6g} mm,"
        f" measured {result.measurements['waterline_mm']:.6g} mm,"
        f" offset {result.calibration.waterline_offset_mm:.6g} mm"
    )
    typer.echo(f"calibration -> {show(result.calibration_path)} ({result.calibration.source})")
    for requirement_id, reason in sorted(result.skipped.items()):
        typer.echo(f"skipped {requirement_id}: {reason}")
    for verdict in result.verdicts:
        limit = verdict.limit.model_dump(exclude_none=True)
        typer.echo(
            f"{verdict.requirement} {verdict.check}: {verdict.result.value}"
            f" (measured {verdict.measured}, limit {limit},"
            f" evidence {verdict.evidence_level.value})"
        )
    for requirement_id, path in sorted(result.proposals.items()):
        typer.echo(f"proposal {requirement_id} -> {show(path)}")
    for requirement_id, message in sorted(result.proposal_errors.items()):
        typer.echo(f"error: {requirement_id}: {message}", err=True)
    typer.echo(f"measure {project}: {'fail' if result.exit_code else 'pass'}")
    raise typer.Exit(result.exit_code)


@app.command()
def status(project: str) -> None:
    """Show run state and open change requests."""
    typer.echo("not implemented")
