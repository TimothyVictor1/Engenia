"""Read-only HTTP routes for a single project."""

from datetime import UTC, datetime
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from engenia.core import agents as agent_registry
from engenia.core import events as design_events
from engenia.core.cli import find_root
from engenia.core.summary import run_summary as compute_run_summary
from engenia.dashboard import files as build_files
from engenia.dashboard.changes import merged_stream, stream_design_log, stream_events
from engenia.dashboard.reader import ProjectReader, list_projects
from engenia.dashboard.schemas import (
    AgentsResponse,
    ContractResponse,
    FilesResponse,
    PipelineResponse,
    ProjectsResponse,
    ReleaseResponse,
    RequirementsResponse,
    RunSummaryResponse,
    ScenariosResponse,
    StageResponse,
)
from engenia.harness.llm import agent_models
from engenia.stages.registry import REGISTRY


def create_app(project: str, root: Path | None = None, *, switchable: bool = False) -> FastAPI:
    """The read-only API and the UI for one project. `switchable` says the server also serves
    the other projects at /p/<name>/ (create_multi_app), so the UI may offer a switcher."""
    reader = ProjectReader(root if root is not None else find_root(), project)
    app = FastAPI(title="Engenia project dashboard", docs_url=None, redoc_url=None)
    app.state.reader = reader

    async def unavailable(request: Request, exc: Exception):
        return JSONResponse(
            status_code=503, content={"detail": "Artifacts are being updated; retry"}
        )

    # A reader may catch a file halfway through a producer's write or replacement.
    for error in (
        ValueError,
        KeyError,
        TypeError,
        FileNotFoundError,
        yaml.YAMLError,
        ValidationError,
    ):
        app.add_exception_handler(error, unavailable)

    @app.get("/api/projects", response_model=ProjectsResponse)
    def projects():
        """Every project under the repository root, with its pack."""
        return {"current": reader.project, "switchable": switchable,
                "projects": list_projects(reader.root)}

    @app.get("/api/pipeline", response_model=PipelineResponse)
    def pipeline():
        return reader.pipeline()

    @app.get("/api/requirements", response_model=RequirementsResponse)
    def requirements():
        return reader.requirements()

    @app.get("/api/stages/{name}", response_model=StageResponse)
    def stage(name: str):
        if name not in REGISTRY:
            raise HTTPException(404, "Unknown stage")
        return reader.stage(name)

    @app.get("/api/contract", response_model=ContractResponse)
    def contract():
        return reader.contract()

    @app.get("/api/scenarios", response_model=ScenariosResponse)
    def scenarios():
        return reader.scenarios()

    def build_dir(*, src: bool = False) -> Path | None:
        directory = reader.directory / ("src" if src else "build")
        return None if directory.is_symlink() else directory

    @app.get("/api/files/{stage}", response_model=FilesResponse)
    def list_files(stage: str):
        """Every file in build/<stage>/ with size, type and modified time."""
        if stage not in REGISTRY:
            raise HTTPException(404, "Unknown stage")
        build = build_dir()
        files = build_files.describe(build / stage) if build else []
        return {"stage": stage, "files": files}

    @app.get("/api/release", response_model=ReleaseResponse)
    def release_info():
        found = build_files.newest_release(reader.root, reader.project)
        if found is None:
            return {"available": False}
        stat = found.stat()
        return {
            "available": True, "name": found.name, "size": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(timespec="seconds"),
        }

    @app.get("/api/download/release")
    def download_release():
        found = build_files.newest_release(reader.root, reader.project)
        if found is None:
            raise HTTPException(404, "No release has been made")
        return FileResponse(found, media_type="application/zip", filename=found.name)

    @app.get("/api/download/{archive}")
    def download(archive: str):
        """A zip of one stage's build output (`<stage>.zip`) or of the whole build and src
        (`all.zip`)."""
        name = archive.removesuffix(".zip") if archive.endswith(".zip") else None
        if name is None or (name != "all" and name not in REGISTRY):
            raise HTTPException(404, "Unknown archive")
        build, src = build_dir(), build_dir(src=True)
        if name == "all":
            parts = [("build", build), ("src", src)]
        else:
            parts = [(name, build / name if build else None)]
        entries = build_files.zip_entries([(prefix, d) for prefix, d in parts if d is not None])
        if not entries:
            raise HTTPException(404, "Nothing has been built yet")
        return StreamingResponse(
            build_files.stream_zip(entries), media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{reader.project}-{name}.zip"'},
        )

    @app.get("/api/agents", response_model=AgentsResponse)
    def agent_list():
        """The whole agent registry with live state, from the events log and run database."""
        configured = agent_models()
        agents = []
        for agent in agent_registry.live_state(reader.root, reader.project).values():
            real = configured.get(agent["name"])
            if real is not None:  # the harness's model map is the truth for who runs what
                agent = {**agent, "model": real["model"], "family": real["family"], "kind": "llm"}
            agents.append(agent)
        return {"project": reader.project, "agents": agents}

    @app.get("/api/run/summary", response_model=RunSummaryResponse)
    def run_summary(run_id: int | None = None):
        """One run's timing picture: per-stage phase durations, parallelism over time and
        totals. Defaults to the project's newest run with a log."""
        run = (run_id if run_id is not None
               else design_events.latest_run_id(reader.root, reader.project))
        if run is None:
            empty = {
                "duration_ms": 0, "model_calls": 0, "tool_calls": 0, "repairs": 0,
                "cache_hits": 0, "cache_misses": 0,
                "max_parallel_stages": 0, "max_parallel_agents": 0,
            }
            return {"run_id": None, "stages": {}, "parallelism": [], "totals": empty}
        return compute_run_summary(reader.root, run)

    @app.get("/api/files/{stage}/{filename:path}")
    def file(stage: str, filename: str):
        if stage not in REGISTRY or not filename or "\\" in filename:
            raise HTTPException(404, "File not found")
        requested = Path(filename)
        if requested.is_absolute() or ".." in requested.parts:
            raise HTTPException(404, "File not found")
        build = reader.directory / "build"
        directory = build / stage
        try:
            path = (directory / requested).resolve()
            allowed = (
                not build.is_symlink()
                and not directory.is_symlink()
                and path.is_relative_to(directory.resolve())
                and path.is_file()
            )
        except (OSError, RuntimeError, ValueError):
            allowed = False
        if not allowed:
            raise HTTPException(404, "File not found")
        return FileResponse(path)

    def checked_stage(stage: str | None) -> str | None:
        if stage is not None and stage not in REGISTRY:
            raise HTTPException(404, "Unknown stage")
        return stage

    @app.get("/api/events")
    async def events(
        request: Request, stage: str | None = None, run_id: int | None = None,
        project: str | None = None,
    ):
        """Change notifications (plain messages) plus the live design log (`event: design`
        messages, one per log line), filterable by ?stage=, ?run_id= and ?project=
        (default: this dashboard's project)."""
        stage = checked_stage(stage)
        project = project or reader.project
        return StreamingResponse(
            merged_stream(
                stream_events(reader, request),
                stream_design_log(reader, request, stage, run_id, project),
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/events/history")
    def events_history(
        stage: str | None = None, run_id: int | None = None, project: str | None = None
    ):
        """A finished run's whole log (the project's newest run when run_id is omitted),
        oldest first. ?project= defaults to this dashboard's project."""
        stage = checked_stage(stage)
        project = project or reader.project
        run = (run_id if run_id is not None
               else design_events.latest_run_id(reader.root, project))
        if run is None:
            return {"run_id": None, "events": []}
        found, _ = design_events.read_events(reader.root, run, stage=stage, project=project)
        return {"run_id": run, "events": found}

    @app.get("/api/previews/{run_id}/{filename}")
    def preview(run_id: int, filename: str):
        """An STL written by a mechanical build attempt; only for runs of this project."""
        directory = reader.root / ".engenia" / "previews" / str(run_id)
        if (
            not filename.endswith(".stl")
            or "/" in filename or "\\" in filename or "\0" in filename
            or filename.startswith(".")
            or design_events.run_project(reader.root, run_id) != reader.project
        ):
            raise HTTPException(404, "File not found")
        try:
            path = (directory / filename).resolve()
            allowed = (
                not (reader.root / ".engenia").is_symlink()
                and not directory.is_symlink()
                and path.parent == directory.resolve()
                and path.is_file()
            )
        except (OSError, RuntimeError, ValueError):
            allowed = False
        if not allowed:
            raise HTTPException(404, "File not found")
        return FileResponse(path, media_type="model/stl")

    static = Path(__file__).parent / "static"
    if static.is_dir():
        app.mount("/", StaticFiles(directory=static, html=True), name="static")
    return app


def create_multi_app(root: Path | None = None, default: str | None = None) -> FastAPI:
    """One server for every project: each is served by its own create_app at /p/<name>/ (the UI
    uses relative URLs, so it works unchanged there) and the UI's project switcher moves between
    them. `/` redirects to the default project: `default`, else harbour-listener, else the first."""
    root = root if root is not None else find_root()
    names = [item["name"] for item in list_projects(root)]
    if not names:
        raise ValueError(f"no projects with requirements under {Path(root) / 'projects'}")
    if default is not None and default not in names:
        raise ValueError(f"unknown project: {default}")
    start = default or ("harbour-listener" if "harbour-listener" in names else names[0])
    app = FastAPI(title="Engenia dashboard", docs_url=None, redoc_url=None)
    for name in names:
        app.mount(f"/p/{name}", create_app(name, root, switchable=True))

    @app.get("/api/projects", response_model=ProjectsResponse)
    def projects():
        return {"current": start, "switchable": True, "projects": list_projects(root)}

    @app.get("/", include_in_schema=False)
    def index():
        return RedirectResponse(f"/p/{start}/")

    return app
