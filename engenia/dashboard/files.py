"""Read-only file listing and zip downloads of a project's build output.

Everything here is path-safe in the same way as the single-file route: a symlink (file or
directory) is never followed, and a path that resolves outside the directory being listed is
left out. Nothing is written inside the project; zips are built in a temporary file.
"""

from __future__ import annotations

import re
import tempfile
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

CHUNK = 64 * 1024
TEXT_EXT = {".txt", ".log", ".md", ".py", ".yaml", ".yml", ".toml", ".ini", ".c", ".h", ".cpp",
            ".sh", ".kicad_sch", ".kicad_pcb", ".net", ".xml", ".svg", ".html"}
TYPES = {".json": "json", ".csv": "csv", ".stl": "stl", ".step": "step", ".stp": "step",
         ".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image", ".zip": "archive",
         ".pdf": "pdf", ".bin": "binary", ".hex": "binary", ".elf": "binary"}


def file_type(name: str) -> str:
    ext = Path(name).suffix.lower()
    if ext in TYPES:
        return TYPES[ext]
    return "text" if ext in TEXT_EXT else "binary"


def safe_files(directory: Path) -> list[tuple[str, Path]]:
    """Every regular file below `directory` as (posix relative path, path), sorted. Symlinks
    are skipped, so nothing outside the directory can be listed or zipped."""
    if directory.is_symlink() or not directory.is_dir():
        return []
    root = directory.resolve()
    found: list[tuple[str, Path]] = []
    stack = [directory]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except OSError:
            continue
        for path in entries:
            try:
                if path.is_symlink():
                    continue
                if path.is_dir():
                    stack.append(path)
                elif path.is_file() and path.resolve().is_relative_to(root):
                    found.append((path.relative_to(directory).as_posix(), path))
            except (OSError, RuntimeError, ValueError):
                continue
    return sorted(found)


def describe(directory: Path) -> list[dict]:
    out = []
    for relative, path in safe_files(directory):
        try:
            stat = path.stat()
        except OSError:
            continue  # removed while we were listing
        out.append({
            "path": relative, "name": path.name, "size": stat.st_size, "type": file_type(path.name),
            "modified": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(timespec="seconds"),
        })
    return out


def zip_entries(prefix_dirs: list[tuple[str, Path]]) -> list[tuple[str, Path]]:
    """Archive names and paths for each (prefix, directory) pair."""
    entries: list[tuple[str, Path]] = []
    for prefix, directory in prefix_dirs:
        entries += [(f"{prefix}/{relative}" if prefix else relative, path)
                    for relative, path in safe_files(directory)]
    return sorted(entries)


def stream_zip(entries: list[tuple[str, Path]]) -> Iterator[bytes]:
    """The zip, in chunks. It is built in a temporary file first, so a large build is not held
    in memory and a file that disappears mid-way is skipped rather than breaking the download."""
    with tempfile.SpooledTemporaryFile(max_size=32 * 1024 * 1024) as buffer:
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, path in entries:
                try:
                    archive.write(path, name)
                except OSError:
                    continue
        buffer.seek(0)
        while chunk := buffer.read(CHUNK):
            yield chunk


def newest_release(root: Path, project: str) -> Path | None:
    """release/<project>-<12 hex>.zip, the newest one, or None."""
    directory = root / "release"
    if directory.is_symlink() or not directory.is_dir():
        return None
    pattern = re.compile(rf"^{re.escape(project)}-[0-9a-f]{{12}}\.zip$")
    candidates = [p for p in directory.iterdir()
                  if pattern.match(p.name) and p.is_file() and not p.is_symlink()]
    return max(candidates, key=lambda p: (p.stat().st_mtime_ns, p.name), default=None)
