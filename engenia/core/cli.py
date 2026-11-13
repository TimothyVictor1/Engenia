"""The engenia command line (PROJECT.md section 6, CLI commands).

Step 0: lint and run work; plan, gate, matrix and status print "not implemented".
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import typer
import yaml
from pydantic import ValidationError

from engenia.contracts.models import Interface, Requirement, StageContext
from engenia.stages.registry import REGISTRY

app = typer.Typer(
    name="engenia",
    help="Engenia: from a written product request to a verified design.",
    no_args_is_help=True,
    add_completion=False,
)


def find_root() -> Path:
    """Repository root: $ENGENIA_ROOT, else the nearest parent of cwd holding projects/."""
    env = os.environ.get("ENGENIA_ROOT")
    if env:
        return Path(env).resolve()
    cwd = Path.cwd().resolve()
    for candidate in (cwd, *cwd.parents):
        if (candidate / "projects").is_dir() and (candidate / "pyproject.toml").is_file():
            return candidate
    return Path(__file__).resolve().parents[2]


def _project_dir(project: str) -> Path:
    path = find_root() / "projects" / project
    if not path.is_dir():
        typer.echo(f"error: unknown project: {project}", err=True)
        raise typer.Exit(1)
    return path


def _topo_order() -> list[str]:
    """Stage names in dependency order, ties broken by registration order."""
    order: list[str] = []
    visiting: set[str] = set()

    def visit(name: str) -> None:
        if name in order:
            return
        if name in visiting:
            raise RuntimeError(f"cycle in stage graph at {name}")
        if name not in REGISTRY:
            raise RuntimeError(f"unknown stage in needs: {name}")
        visiting.add(name)
        for dep in REGISTRY[name].needs:
            visit(dep)
        visiting.discard(name)
        order.append(name)

    for name in REGISTRY:
        visit(name)
    return order


def _tree_key(directory: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in directory.rglob("*") if p.is_file()):
        digest.update(path.relative_to(directory).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return f"sha256:{digest.hexdigest()}"


@app.command()
def lint(project: str) -> None:
    """Validate the spec files of a project against the Requirement schema."""
    spec_dir = _project_dir(project) / "spec"
    files = sorted(spec_dir.glob("R-*.yaml"))
    if not files:
        typer.echo(f"error: no spec files in {spec_dir}", err=True)
        raise typer.Exit(1)
    failed = False
    for path in files:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            requirement = Requirement.model_validate(data)
            if requirement.id != path.stem:
                raise ValueError(f"id {requirement.id} does not match file name {path.name}")
        except (yaml.YAMLError, ValidationError, ValueError) as exc:
            failed = True
            typer.echo(f"FAIL {path.name}: {exc}", err=True)
        else:
            typer.echo(f"ok   {path.name}")
    if failed:
        raise typer.Exit(1)


@app.command()
def run(project: str) -> None:
    """Run every stage serially in dependency order (step 0: stubs only)."""
    _project_dir(project)
    root = find_root()
    for name in _topo_order():
        stage = REGISTRY[name]
        ctx = StageContext(project=project, stage=name, root=root)
        stage.generate(ctx)
        stage.build(ctx)
        interface = Interface(stage=name, key=_tree_key(ctx.build_dir), facts=stage.interface(ctx))
        (ctx.build_dir / "interface.json").write_text(
            json.dumps(interface.model_dump(mode="json"), sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        (ctx.build_dir / "verdict.json").write_text("[]\n", encoding="utf-8")
        typer.echo(f"ran  {name}")


@app.command()
def plan(project: str) -> None:
    """Show which stages will run and why."""
    typer.echo("not implemented")


@app.command()
def gate(project: str) -> None:
    """Run all checks, reproduction and coverage."""
    typer.echo("not implemented")


@app.command()
def matrix(project: str) -> None:
    """Write the trace matrix."""
    typer.echo("not implemented")


@app.command()
def status(project: str) -> None:
    """Show run state and open change requests."""
    typer.echo("not implemented")
