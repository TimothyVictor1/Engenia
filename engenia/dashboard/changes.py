"""Metadata polling for SSE. Area membership follows the data each endpoint reads."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator

from fastapi import Request

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
