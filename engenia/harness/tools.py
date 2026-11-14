"""The four tools an agent gets: write_file, run_build, read_verdicts, record_decision.

write_file is sandboxed to one directory chosen by the harness at construction. Every refusal is
a `ToolError`; the agent loop returns its message to the model as the tool result.
"""

from __future__ import annotations

import inspect
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from engenia.contracts.models import Verdict
from engenia.core.events import Emitter, unified_diff
from engenia.harness.build import run_captured

_REQUIREMENT = re.compile(r"R-[0-9]{3}")

WriteGuard = Callable[[Path, str], None]
"""Optional extra rule for write_file. Called with (path relative to the sandbox, content)."""


class ToolError(Exception):
    """A tool call was refused or failed. The message is shown to the model."""


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "Write a text file, replacing it if it exists. The path is relative to your "
                "working directory; paths outside it are refused."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative file path."},
                    "content": {"type": "string", "description": "Full file content."},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_build",
            "description": "Run the build. Returns stdout, stderr and the exit code.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_verdicts",
            "description": (
                "Read the verdicts from the most recent verification run. Empty if there is "
                "none yet."
            ),
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "record_decision",
            "description": (
                "Record one design choice (a dimension, material, part or layout) in the live "
                "design log. Call it for every such choice, with the requirement it serves and "
                "the numbers behind it. It changes no files."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "What was decided, short."},
                    "choice": {"type": "string", "description": "The value or option chosen."},
                    "reason": {"type": "string", "description": "Why, citing the numbers."},
                    "requirements": {
                        "type": "array",
                        "items": {"type": "string", "pattern": "^R-[0-9]{3}$"},
                        "description": "Requirement ids this choice answers, e.g. R-004.",
                    },
                    "numbers": {
                        "type": "object",
                        "additionalProperties": {"type": "number"},
                        "description": "The figures used, e.g. {\"wall_mm\": 3.0, \"min_mm\": 3}.",
                    },
                    "alternatives": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "option": {"type": "string"},
                                "why_not": {"type": "string"},
                            },
                            "required": ["option", "why_not"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["title", "choice", "reason", "requirements", "numbers"],
                "additionalProperties": False,
            },
        },
    },
]


class Toolbox:
    """The tools for one agent session.

    allowed_dir     the only directory write_file may touch.
    build           the stage build, called with no arguments. It may print, raise, or raise
                    SystemExit(n); run_build reports all of that as stdout, stderr and exit code.
    verdicts_path   build/<stage>/verdict.json, read by read_verdicts. Missing means no verdicts.
    write_guard     optional extra rule applied to every write, raising ToolError to refuse.
    events          optional live-log emitter: code events for writes, decision events.
    """

    def __init__(
        self,
        allowed_dir: Path,
        build: Callable[[], None],
        verdicts_path: Path | None = None,
        *,
        write_guard: WriteGuard | None = None,
        events: Emitter | None = None,
    ) -> None:
        self.allowed_dir = Path(allowed_dir).resolve()
        self._build = build
        self._verdicts_path = verdicts_path
        self._write_guard = write_guard
        self.events = events
        self.written: list[Path] = []
        self._tools: dict[str, Callable[..., Any]] = {
            "write_file": self.write_file,
            "run_build": self.run_build,
            "read_verdicts": self.read_verdicts,
            "record_decision": self.record_decision,
        }

    # --- the model-facing interface -----------------------------------------------------------

    def schemas(self) -> list[dict[str, Any]]:
        return [dict(schema) for schema in TOOL_SCHEMAS]

    def call(self, name: str, arguments: dict[str, Any]) -> Any:
        """Run one tool by name. Raises ToolError for an unknown tool or bad arguments."""
        tool = self._tools.get(name)
        if tool is None:
            raise ToolError(f"unknown tool {name!r}; available: {', '.join(self._tools)}")
        try:
            inspect.signature(tool).bind(**arguments)
        except TypeError as exc:
            raise ToolError(f"bad arguments for {name}: {exc}") from None
        return tool(**arguments)

    # --- the three tools ----------------------------------------------------------------------

    def _resolve(self, raw: Any) -> Path:
        if not isinstance(raw, str) or not raw.strip():
            raise ToolError("path must be a non-empty string")
        if "\0" in raw:
            raise ToolError("path contains a null byte")
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = self.allowed_dir / candidate
        # resolve() collapses ".." and follows symlinks, so neither can lead out of the sandbox.
        resolved = candidate.resolve()
        if not resolved.is_relative_to(self.allowed_dir):
            raise ToolError(f"path is outside the allowed directory: {raw}")
        return resolved

    def write_file(self, path: str, content: str) -> dict[str, Any]:
        resolved = self._resolve(path)
        if not isinstance(content, str):
            raise ToolError("content must be a string")
        if resolved == self.allowed_dir or resolved.is_dir():
            raise ToolError(f"path is a directory: {path}")
        relative = resolved.relative_to(self.allowed_dir)
        if self._write_guard is not None:
            self._write_guard(relative, content)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8")
        old = self._read_old(resolved) if self.events is not None else None
        resolved.write_bytes(data)
        if self.events is not None and old != content:
            name = relative.as_posix()
            self.events.emit(
                "code", {"file": name, "content": content, "diff": unified_diff(name, old, content)}
            )
        if resolved not in self.written:
            self.written.append(resolved)
        return {"path": relative.as_posix(), "bytes": len(data)}

    @staticmethod
    def _read_old(path: Path) -> str | None:
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    def record_decision(
        self,
        title: str,
        choice: str,
        reason: str,
        requirements: list[str],
        numbers: dict[str, float],
        alternatives: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        for name, value in (("title", title), ("choice", choice), ("reason", reason)):
            if not isinstance(value, str) or not value.strip():
                raise ToolError(f"{name} must be a non-empty string")
        if not isinstance(requirements, list) or not all(
            isinstance(r, str) and _REQUIREMENT.fullmatch(r) for r in requirements
        ):
            raise ToolError("requirements must be a list of ids like R-004")
        if not isinstance(numbers, dict) or not all(
            isinstance(k, str) and isinstance(v, (int, float)) and not isinstance(v, bool)
            for k, v in numbers.items()
        ):
            raise ToolError("numbers must be an object of name -> number")
        alternatives = alternatives or []
        if not isinstance(alternatives, list) or not all(
            isinstance(a, dict) and isinstance(a.get("option"), str)
            and isinstance(a.get("why_not"), str)
            for a in alternatives
        ):
            raise ToolError("alternatives must be a list of {option, why_not}")
        if self.events is not None:
            self.events.emit("decision", {
                "title": title, "choice": choice, "reason": reason,
                "alternatives": [
                    {"option": a["option"], "why_not": a["why_not"]} for a in alternatives
                ],
                "requirements": requirements, "numbers": numbers,
            })
        return {"recorded": title}

    def run_build(self) -> dict[str, Any]:
        result = run_captured(self._build)
        return {"stdout": result.stdout, "stderr": result.stderr, "exit_code": result.exit_code}

    def read_verdicts(self) -> list[dict[str, Any]]:
        if self._verdicts_path is None or not self._verdicts_path.is_file():
            return []
        try:
            raw = json.loads(self._verdicts_path.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                raise ValueError("verdict file is not a list")
            return [Verdict.model_validate(item).model_dump(mode="json") for item in raw]
        except (ValueError, ValidationError) as exc:
            raise ToolError(f"verdict file is unreadable: {exc}") from None
