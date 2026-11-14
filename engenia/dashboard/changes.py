"""Metadata polling for SSE. Area membership follows the data each endpoint reads."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator

from fastapi import Request

from engenia.core import events
from engenia.dashboard.reader import ProjectReader
from engenia.dashboard.schemas import EventsResponse

AREAS = ("pipeline", "requirements", "contract", "scenarios", "design")


def snapshot(reader: ProjectReader) -> dict[str, str]:
    """Hash relative names, nanosecond mtimes and sizes, including additions/deletions.

    Build files affect pipeline; verdicts also affect requirements; interfaces also
    affect contract. Mechanical/electronics outputs affect design, and red-team
    scenario YAML affects scenarios. SQLite WAL metadata is included for live runs.
    """
    hashes = {area: hashlib.sha256() for area in AREAS}
    for directory in ("spec", "contract", "build"):
        for path in sorted((reader.directory / directory).rglob("*")):
            try:
                if not path.is_file():
                    continue
                stat = path.stat()
            except FileNotFoundError:
                continue
            relative = path.relative_to(reader.directory)
            parts = relative.parts
            areas = set()
            if directory == "spec":
                areas.add("requirements")
            elif directory == "contract":
                areas.add("contract")
            else:
                areas.add("pipeline")
                if path.name == "verdict.json":
                    areas.add("requirements")
                if path.name == "interface.json":
                    areas.add("contract")
                if len(parts) > 1 and parts[1] in {"mechanical", "electronics"}:
                    areas.add("design")
                if parts[:3] == ("build", "redteam", "scenarios") and path.suffix == ".yaml":
                    areas.add("scenarios")
            token = f"{relative.as_posix()}\0{stat.st_mtime_ns}\0{stat.st_size}\0".encode()
            for area in areas:
                hashes[area].update(token)
    for database in reader.database_paths:
        for path in (database, database.with_name(database.name + "-wal")):
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            hashes["pipeline"].update(f"{path}\0{stat.st_mtime_ns}\0{stat.st_size}\0".encode())
    default = reader.root / "packs" / "buoy" / "default_contract.json"
    if not (reader.directory / "contract" / "contract.json").exists():
        try:
            stat = default.stat()
        except FileNotFoundError:
            pass
        else:
            hashes["contract"].update(f"{stat.st_mtime_ns}\0{stat.st_size}".encode())
    return {area: digest.hexdigest() for area, digest in hashes.items()}


def changed_areas(before: dict[str, str], after: dict[str, str]) -> list[str]:
    return [area for area in AREAS if before[area] != after[area]]


async def stream_events(reader: ProjectReader, request: Request) -> AsyncIterator[str]:
    before = await asyncio.to_thread(snapshot, reader)
    while not await request.is_disconnected():
        await asyncio.sleep(1)
        after = await asyncio.to_thread(snapshot, reader)
        changed = changed_areas(before, after)
        before = after
        if changed:
            yield f"data: {EventsResponse(changed=changed).model_dump_json()}\n\n"


LOG_POLL_SECONDS = 0.5


def format_log_event(event: dict) -> str:
    """One log line as a named SSE message. Named, so a plain `onmessage` listener (the change
    notifications above) never sees it; log readers use addEventListener("design", ...)."""
    return f"event: design\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


async def stream_design_log(
    reader: ProjectReader, request: Request, stage: str | None, run_id: int | None,
    project: str | None = None,
) -> AsyncIterator[str]:
    """Replay the run's log, then follow it. Without a run id it follows the project's newest
    run, and switches to a newer one when it appears. Only `project`'s events are sent."""
    root = reader.root
    current, offset = run_id, 0
    while not await request.is_disconnected():
        if run_id is None:
            newest = await asyncio.to_thread(events.latest_run_id, root, project)
            if newest is not None and newest != current:
                current, offset = newest, 0
        if current is not None:
            batch, offset = await asyncio.to_thread(
                events.read_events, root, current, stage=stage, offset=offset, project=project
            )
            for event in batch:
                yield format_log_event(event)
        await asyncio.sleep(LOG_POLL_SECONDS)


async def merged_stream(
    changes: AsyncIterator[str], log: AsyncIterator[str]
) -> AsyncIterator[str]:
    """Both streams on one connection. Ends when the change stream ends (client gone)."""
    queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def pump(source: AsyncIterator[str], *, last: bool) -> None:
        try:
            async for item in source:
                await queue.put(item)
        finally:
            if last:
                await queue.put(None)

    tasks = [asyncio.create_task(pump(changes, last=True)),
             asyncio.create_task(pump(log, last=False))]
    try:
        while (item := await queue.get()) is not None:
            yield item
    finally:
        for task in tasks:
            task.cancel()
