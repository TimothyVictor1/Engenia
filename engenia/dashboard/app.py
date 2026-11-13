"""Read-only HTTP routes for a single project."""

from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from engenia.core.cli import find_root
from engenia.dashboard.changes import stream_events
from engenia.dashboard.reader import ProjectReader
from engenia.dashboard.schemas import (
    ContractResponse,
    PipelineResponse,
    RequirementsResponse,
    ScenariosResponse,
    StageResponse,
)
from engenia.stages.registry import REGISTRY


def create_app(project: str, root: Path | None = None) -> FastAPI:
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

    @app.get("/api/events")
    async def events(request: Request):
        return StreamingResponse(
            stream_events(reader, request),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    static = Path(__file__).parent / "static"
    if static.is_dir():
        app.mount("/", StaticFiles(directory=static, html=True), name="static")
    return app
