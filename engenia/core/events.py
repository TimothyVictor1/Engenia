"""The live design log: one JSON line per event in <root>/.engenia/events/<run_id>.jsonl.

    {"ts", "run_id", "project", "stage", "attempt", "kind", "data"}

kind is one of message, tool_call, tool_result, code, decision, preview, verdict, repair.

The log is a side channel. It is written outside projects/ and outside build/, nothing reads it
back into a stage, and emitting never raises: a failure to log must not change a run, its
outputs or its cache keys. Secrets are scrubbed from every event before it is written.

The engine marks a run active with `activate(root, run_id)`. With no active run, `emit` does
nothing, so code that is called outside a run (unit tests, a lone `repair_stage`) stays silent.
"""

from __future__ import annotations

import contextlib
import difflib
import json
import os
import re
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

KINDS = ("message", "tool_call", "tool_result", "code", "decision", "preview", "verdict", "repair")
MAX_TEXT = 20_000  # one string in one event; longer text is cut and marked

_lock = threading.Lock()
_active: dict[str, int] = {}  # resolved root -> run id
_projects: dict[tuple[str, int], str] = {}  # (resolved root, run id) -> project
_attempts: dict[tuple[str, str], int] = {}  # (resolved root, stage) -> current attempt

_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{16,}"),
    re.compile(r"(?i)(api[_-]?key|token|secret|password)(\"?\s*[:=]\s*\"?)[^\s\",}]{8,}"),
]
_SECRET_KEY = re.compile(r"(?i)(api[_-]?key|authorization|secret|password|token)")
_ENV_SECRET_NAME = re.compile(r"(?i)(API_?KEY|TOKEN|SECRET|PASSWORD)")


def events_dir(root: Path) -> Path:
    return Path(root) / ".engenia" / "events"


def events_path(root: Path, run_id: int) -> Path:
    return events_dir(root) / f"{run_id}.jsonl"


def _key(root: Path) -> str:
    return str(Path(root).resolve())


@contextlib.contextmanager
def activate(root: Path, run_id: int, project: str | None = None) -> Iterator[None]:
    """Mark `run_id` as the run events go to; restores the previous one on exit.

    `project` is stamped on every event of the run."""
    key = _key(root)
    with _lock:
        previous = _active.get(key)
        _active[key] = run_id
        if project is not None:
            _projects[(key, run_id)] = project
    try:
        yield
    finally:
        with _lock:
            if previous is None:
                _active.pop(key, None)
            else:
                _active[key] = previous


def active_run(root: Path) -> int | None:
    with _lock:
        return _active.get(_key(root))


def set_attempt(root: Path, stage: str, attempt: int) -> None:
    with _lock:
        _attempts[(_key(root), stage)] = attempt


def current_attempt(root: Path, stage: str) -> int:
    with _lock:
        return _attempts.get((_key(root), stage), 1)


# --- scrubbing --------------------------------------------------------------------------------


def _env_secrets() -> list[str]:
    return [
        value for name, value in os.environ.items()
        if _ENV_SECRET_NAME.search(name) and len(value) >= 8
    ]


def scrub_text(text: str, secrets: list[str] | None = None) -> str:
    for secret in secrets if secrets is not None else _env_secrets():
        text = text.replace(secret, "[redacted]")
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(
            lambda m: f"{m.group(1)}{m.group(2)}[redacted]" if m.lastindex == 2 else "[redacted]",
            text,
        )
    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT] + f"\n... [{len(text) - MAX_TEXT} more characters not logged]"
    return text


def scrub(value: Any, secrets: list[str] | None = None) -> Any:
    """A JSON-safe copy of `value` with secrets removed."""
    secrets = _env_secrets() if secrets is None else secrets
    if isinstance(value, str):
        return scrub_text(value, secrets)
    if isinstance(value, dict):
        return {
            str(k): ("[redacted]" if _SECRET_KEY.search(str(k)) and isinstance(v, str)
                     else scrub(v, secrets))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [scrub(v, secrets) for v in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return scrub_text(str(value), secrets)


# --- writing ----------------------------------------------------------------------------------


def emit(
    root: Path,
    stage: str | None,
    kind: str,
    data: dict[str, Any],
    *,
    attempt: int | None = None,
    run_id: int | None = None,
    project: str | None = None,
) -> None:
    """Append one event to the active run's log. Never raises; does nothing with no active run."""
    try:
        if kind not in KINDS:
            raise ValueError(f"unknown event kind {kind!r}")
        run = run_id if run_id is not None else active_run(root)
        if run is None:
            return
        with _lock:
            project = project or _projects.get((_key(root), run))
        if attempt is None:
            attempt = current_attempt(root, stage) if stage else 1
        event = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "run_id": run,
            "project": project,
            "stage": stage,
            "attempt": attempt,
            "kind": kind,
            "data": scrub(data),
        }
        line = json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
        path = events_path(root, run)
        with _lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(line)
    except Exception:  # noqa: BLE001 - the log must never break a run
        return


class Emitter:
    """`emit` bound to one stage of one root; handed to the agent toolbox and loop."""

    def __init__(self, root: Path, stage: str) -> None:
        self.root = Path(root)
        self.stage = stage

    def emit(self, kind: str, data: dict[str, Any]) -> None:
        emit(self.root, self.stage, kind, data)

    def set_attempt(self, attempt: int) -> None:
        set_attempt(self.root, self.stage, attempt)


def unified_diff(path: str, old: str | None, new: str) -> str:
    return "".join(
        difflib.unified_diff(
            (old or "").splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{path}" if old is not None else "/dev/null",
            tofile=f"b/{path}",
        )
    )


def emit_code(root: Path, stage: str, path: str, old: str | None, new: str) -> None:
    """A code event for one written src file: {file, content, diff}."""
    if old == new:
        return
    emit(root, stage, "code", {"file": path, "content": new, "diff": unified_diff(path, old, new)})


def emit_decision(
    root: Path,
    stage: str,
    *,
    title: str,
    choice: str,
    reason: str,
    requirements: list[str] | None = None,
    numbers: dict[str, float] | None = None,
    alternatives: list[dict[str, str]] | None = None,
) -> None:
    emit(root, stage, "decision", {
        "title": title, "choice": choice, "reason": reason,
        "alternatives": alternatives or [],
        "requirements": requirements or [],
        "numbers": numbers or {},
    })


# --- reading ----------------------------------------------------------------------------------


def run_project(root: Path, run_id: int) -> str | None:
    """The project a run's log belongs to (from its first line), or None."""
    try:
        with open(events_path(root, run_id), encoding="utf-8") as handle:
            return json.loads(handle.readline()).get("project")
    except (OSError, ValueError, AttributeError):
        return None


def latest_run_id(root: Path, project: str | None = None) -> int | None:
    """The newest run with a log; with `project`, the newest run of that project."""
    ids = sorted(
        (int(p.stem) for p in events_dir(root).glob("*.jsonl") if p.stem.isdigit()), reverse=True
    )
    for run_id in ids:
        if project is None or run_project(root, run_id) == project:
            return run_id
    return None


def relevant_requirements(requirements: list[Any], stage: str, checks: tuple[str, ...]
                          ) -> list[str]:
    """Ids of approved requirements that affect `stage` and are verified by one of `checks`."""
    return sorted(
        r.id for r in requirements
        if r.status == "approved" and stage in r.affects and r.verify.check in checks
    )


def read_events(root: Path, run_id: int, *, stage: str | None = None, offset: int = 0,
                project: str | None = None) -> tuple[list[dict[str, Any]], int]:
    """Complete lines of a run's log from byte `offset`, and the offset after them."""
    path = events_path(root, run_id)
    try:
        with open(path, "rb") as handle:
            handle.seek(offset)
            raw = handle.read()
    except OSError:
        return [], offset
    end = raw.rfind(b"\n") + 1  # a half-written last line waits for the next read
    out: list[dict[str, Any]] = []
    for line in raw[:end].splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if stage is not None and event.get("stage") != stage:
            continue
        if project is not None and event.get("project") != project:
            continue
        out.append(event)
    return out, offset + end
