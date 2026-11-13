"""Content-addressed store for stage builds, under $ENGENIA_STORE.

Layout:

    <store>/blobs/<aa>/<sha256 hex>      file contents, named by their own hash
    <store>/entries/<key hex>.json       for one stage key: the list of files and their blob hashes

`put(key, build_dir)` stores every file of a build directory (outputs, interface.json,
verdict.json). `get(key, build_dir)` replaces `build_dir` with exactly those files, byte for byte.
Equal files share one blob, so a rerun that produced the same outputs costs no new space.

The store is untrusted input. Every blob is re-hashed as it is restored and every path in an entry
is checked to stay inside the target. Anything wrong makes `get` return False (a miss), never a
half-restored directory: files are written to a scratch directory and swapped in at the end.
Writes use a temporary file and an atomic rename, so a crash or a second process never leaves a
partly written blob or entry.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path, PurePosixPath

from engenia.core.errors import CoreError
from engenia.core.hashing import tree_files
from engenia.core.paths import find_root

ENTRY_VERSION = 1
_KEY = re.compile(r"^sha256:([0-9a-f]{64})$")
_HEX = re.compile(r"^[0-9a-f]{64}$")


class CacheError(CoreError):
    """Misuse of the cache: a bad key, or a build directory that cannot be stored."""


def default_store(root: Path | None = None) -> Path:
    """$ENGENIA_STORE, else <root>/.engenia/store. A relative path is taken from the repo root."""
    base = Path(root) if root is not None else find_root()
    configured = os.environ.get("ENGENIA_STORE")
    path = Path(configured) if configured else Path(".engenia") / "store"
    return path if path.is_absolute() else base / path


def _key_hex(key: str) -> str:
    match = _KEY.match(key) if isinstance(key, str) else None
    if not match:
        raise CacheError(f"bad cache key (want sha256:<64 hex>): {key!r}")
    return match.group(1)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_atomic(target: Path, write) -> None:
    """Call write(tmp_path) on a temporary file next to `target`, then rename it into place."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".tmp-")
    os.close(fd)
    try:
        write(Path(tmp))
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class Cache:
    def __init__(self, store: Path):
        self.store = Path(store)

    def _blob(self, sha_hex: str) -> Path:
        return self.store / "blobs" / sha_hex[:2] / sha_hex

    def _entry(self, key: str) -> Path:
        return self.store / "entries" / f"{_key_hex(key)}.json"

    def has(self, key: str) -> bool:
        return self._entry(key).is_file()

    def put(self, key: str, build_dir: Path) -> None:
        """Store every file under `build_dir` as the result for `key`. Replaces an older entry."""
        self._entry(key)  # validates the key before anything is written
        build_dir = Path(build_dir)
        if not build_dir.is_dir():
            raise CacheError(f"nothing to store, not a directory: {build_dir}")

        files = []
        for rel, path in tree_files(build_dir):
            sha = _sha256_file(path)
            blob = self._blob(sha)
            if not blob.is_file():
                _write_atomic(blob, lambda tmp, src=path: shutil.copyfile(src, tmp))
            files.append(
                {
                    "path": rel,
                    "sha256": sha,
                    "size": path.stat().st_size,
                    "executable": bool(path.stat().st_mode & 0o100),
                }
            )
        entry = {"version": ENTRY_VERSION, "key": key, "files": files}
        text = json.dumps(entry, sort_keys=True, indent=2) + "\n"
        _write_atomic(self._entry(key), lambda tmp: tmp.write_text(text, encoding="utf-8"))

    def get(self, key: str, build_dir: Path) -> bool:
        """Make `build_dir` hold exactly the files stored for `key`. False on a miss.

        On a miss, or if the entry or a blob is damaged, `build_dir` is left as it was.
        """
        entry_path = self._entry(key)
        files = self._read_entry(entry_path, key)
        if files is None:
            return False

        build_dir = Path(build_dir)
        build_dir.parent.mkdir(parents=True, exist_ok=True)
        scratch = Path(tempfile.mkdtemp(dir=build_dir.parent, prefix=f".{build_dir.name}.restore-"))
        try:
            for item in files:
                if not self._restore_file(scratch, item):
                    shutil.rmtree(scratch, ignore_errors=True)
                    return False
            os.chmod(scratch, 0o755)  # mkdtemp makes it private
            if build_dir.is_symlink() or build_dir.is_file():
                build_dir.unlink()
            elif build_dir.exists():
                shutil.rmtree(build_dir)
            os.replace(scratch, build_dir)
        except BaseException:
            shutil.rmtree(scratch, ignore_errors=True)
            raise
        return True

    def _read_entry(self, entry_path: Path, key: str) -> list[dict] | None:
        try:
            entry = json.loads(entry_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):  # missing, unreadable or not JSON: all count as a miss
            return None
        if not isinstance(entry, dict) or entry.get("version") != ENTRY_VERSION:
            return None
        if entry.get("key") != key or not isinstance(entry.get("files"), list):
            return None
        files = entry["files"]
        for item in files:
            if not isinstance(item, dict) or not _safe_relpath(item.get("path")):
                return None
            if not isinstance(item.get("sha256"), str) or not _HEX.match(item["sha256"]):
                return None
        return files

    def _restore_file(self, scratch: Path, item: dict) -> bool:
        blob = self._blob(item["sha256"])
        target = scratch / item["path"]
        try:
            if _sha256_file(blob) != item["sha256"]:
                blob.unlink(missing_ok=True)  # damaged: drop it so the next put rewrites it
                return False
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(blob, target)
            os.chmod(target, 0o755 if item.get("executable") else 0o644)
        except OSError:
            return False
        return True


def _safe_relpath(value: object) -> bool:
    """A plain relative path: no '..', no '.', no empty parts, no backslashes, not absolute."""
    if not isinstance(value, str) or not value or "\\" in value or "\0" in value:
        return False
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value:  # as_posix() folds "a//b" and "a/./b"
        return False
    return all(part not in ("", ".", "..") for part in path.parts)


def put(key: str, build_dir: Path, *, store: Path | None = None) -> None:
    """Store `build_dir` under `key` in `store` (default: $ENGENIA_STORE)."""
    Cache(store or default_store()).put(key, build_dir)


def get(key: str, build_dir: Path, *, store: Path | None = None) -> bool:
    """Restore the build stored under `key` into `build_dir`. False if there is none."""
    return Cache(store or default_store()).get(key, build_dir)


def has(key: str, *, store: Path | None = None) -> bool:
    return Cache(store or default_store()).has(key)
