"""The live design log: one JSON line per event in <root>/.engenia/events/<run_id>.jsonl.

    {"ts", "run_id", "project", "stage", "attempt", "kind", "data", "agent", "phase"}

kind is one of message, tool_call, tool_result, code, decision, preview, verdict, repair,
phase_start, phase_end, change_request, proposal, cache, gate_step, approval, fault.

Every event also names its actor: `agent` is {name, role, model, family, kind} (kind is
"llm", "deterministic" or "script") and `phase` is the stage phase the actor is in (plan,
propose, generate, build, check, repair, publish or cache). Both may be null when nothing
is known. phase_start / phase_end wrap every stage phase; phase_end carries duration_ms and
a result (ok | fail | skipped).

The log is a side channel. It is written outside projects/ and outside build/, nothing reads it
back into a stage, and emitting never raises: a failure to log must not change a run, its
outputs or its cache keys. Secrets are scrubbed from every event before it is written.

The engine marks a run active with `activate(root, run_id)`. With no active run, `emit` does
nothing, so code that is called outside a run (unit tests, a lone `repair_stage`) stays silent.
`attach(root, project)` borrows the project's newest run log when no run is active, so
stand-alone steps (the gate, an approval) land in the log the dashboard already follows.
"""

from __future__ import annotations

import contextlib
import difflib
import json
import os
import re
import threading
import time
from collections.abc import Iterator, Mapping
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

KINDS = (
    "message", "tool_call", "tool_result", "code", "decision", "preview", "verdict", "repair",
    "phase_start", "phase_end", "change_request", "proposal", "cache", "gate_step", "approval",
    "fault",
)
PHASES = ("plan", "propose", "generate", "build", "check", "repair", "publish", "cache")
MAX_TEXT = 20_000  # one string in one event; longer text is cut and marked

_lock = threading.Lock()
_active: dict[str, int] = {}  # resolved root -> run id
_projects: dict[tuple[str, int], str] = {}  # (resolved root, run id) -> project
_attempts: dict[tuple[str, str], int] = {}  # (resolved root, stage) -> current attempt
_agents: dict[tuple[str, str], Any] = {}  # (resolved root, stage) -> agent identity
_phases: dict[tuple[str, str], str] = {}  # (resolved root, stage) -> current phase
_agent_context: ContextVar[dict[str, str] | None] = ContextVar("event_agent", default=None)

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


def set_agent(root: Path, stage: str, agent: Mapping[str, Any] | None) -> None:
    """Record the agent acting on `stage` at `root`; its identity is stamped on the stage's
    events until another `set_agent` for the same stage."""
    with _lock:
        if agent is None:
            _agents.pop((_key(root), stage), None)
        else:
            _agents[(_key(root), stage)] = dict(agent)


def agent_for(root: Path, stage: str) -> dict[str, Any] | None:
    with _lock:
        found = _agents.get((_key(root), stage))
    return dict(found) if found else None


@contextlib.contextmanager
def as_agent(root: Path, stage: str, agent: Mapping[str, Any] | None) -> Iterator[None]:
    """Act as `agent` on `stage` for the block; restores what was there before."""
    previous = agent_for(root, stage)
    set_agent(root, stage, agent)
    try:
        yield
    finally:
        set_agent(root, stage, previous)


@contextlib.contextmanager
def phase(root: Path, stage: str, name: str) -> Iterator[None]:
    """Wrap one stage phase: emits phase_start, then phase_end with duration_ms and a result
    (ok | fail, plus error text when it failed), and makes `name` the stage's phase for events
    emitted inside the block."""
    key = (_key(root), stage)
    with _lock:
        previous = _phases.get(key)
        _phases[key] = name
    started = time.monotonic()
    emit(root, stage, "phase_start", {"phase": name}, phase=name)
    error: str | None = None
    try:
        yield
    except BaseException as exc:
        error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        duration = int((time.monotonic() - started) * 1000)
        data: dict[str, Any] = {
            "phase": name, "duration_ms": duration, "ok": error is None,
            "result": "fail" if error else "ok",
        }
        if error is not None:
            data["error"] = error
        emit(root, stage, "phase_end", data, phase=name)
        with _lock:
            if previous is None:
                _phases.pop(key, None)
            else:
                _phases[key] = previous


@contextlib.contextmanager
def attach(root: Path, project: str) -> Iterator[None]:
    """Log to the active run when there is one, else to the project's newest run log.

    For stand-alone steps (the gate, an approval, intake) that run outside `execute` but whose
    events belong in the log the dashboard follows. With no run at all, emits stay silent.
    """
    if active_run(root) is not None:
        yield
        return
    run_id = latest_run_id(root, project)
    if run_id is None:
        yield
        return
    with activate(root, run_id, project):
        yield


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


@contextlib.contextmanager
def agent_context(agent: str, model: str) -> Iterator[None]:
    """Attach the actual caller to events, isolated across parallel stages."""
    from engenia.harness.llm import model_family

    token = _agent_context.set({"name": agent, "model": model, "family": model_family(model)})
    try:
        yield
    finally:
        _agent_context.reset(token)


def _event_agent(root: Path, stage: str | None) -> dict[str, Any] | None:
    """The agent to stamp on an event: the actual model caller when the harness set one, else
    the identity registered for this stage, else the harness's model map for the stage."""
    from engenia.harness.llm import agent_models

    actual = _agent_context.get()
    if actual is not None:
        return actual
    if stage:
        registered = agent_for(root, stage)
        if registered is not None:
            return registered
    configured = agent_models().get(stage or "")
    if configured is None or not configured.get("model"):
        return None
    return {"name": stage, "kind": "llm", **configured}


def emit(
    root: Path,
    stage: str | None,
    kind: str,
    data: dict[str, Any],
    *,
    attempt: int | None = None,
    run_id: int | None = None,
    project: str | None = None,
    agent: Mapping[str, Any] | None = None,
    phase: str | None = None,
) -> None:
    """Append one event to the active run's log. Never raises; does nothing with no active run.

    `agent` and `phase` override what is stamped; by default the phase comes from an open
    `phase` block and the agent from the actual model caller (`agent_context`), else the
    identity registered for this stage (`set_agent`), else the harness model map."""
    try:
        if kind not in KINDS:
            raise ValueError(f"unknown event kind {kind!r}")
        if phase is not None and phase not in PHASES:
            phase = None
        run = run_id if run_id is not None else active_run(root)
        if run is None:
            return
        with _lock:
            project = project or _projects.get((_key(root), run))
            if phase is None and stage:
                phase = _phases.get((_key(root), stage))
        if agent is None:
            base = agent_for(root, stage) if stage else None
            if base is not None:
                live = _agent_context.get()
                agent = {**base, **live} if live else base
            else:
                agent = _event_agent(root, stage)
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
            "agent": scrub(dict(agent)) if agent else None,
            "phase": phase,
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

    def __init__(self, root: Path, stage: str, *, agent: str | None = None) -> None:
        self.root = Path(root)
        self.stage = stage
        self.agent = agent or stage

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
