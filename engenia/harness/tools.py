"""The three tools an agent gets: write_file, run_build, read_verdicts. Nothing else.

write_file is sandboxed to one directory chosen by the harness at construction. Every refusal is
a `ToolError`; the agent loop returns its message to the model as the tool result.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from engenia.contracts.models import Verdict
from engenia.harness.build import run_captured

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
]


class Toolbox:
    """The tools for one agent session.

    allowed_dir     the only directory write_file may touch.
    build           the stage build, called with no arguments. It may print, raise, or raise
                    SystemExit(n); run_build reports all of that as stdout, stderr and exit code.
    verdicts_path   build/<stage>/verdict.json, read by read_verdicts. Missing means no verdicts.
    write_guard     optional extra rule applied to every write, raising ToolError to refuse.
    """

    def __init__(
        self,
        allowed_dir: Path,
        build: Callable[[], None],
        verdicts_path: Path | None = None,
        *,
        write_guard: WriteGuard | None = None,
    ) -> None:
        self.allowed_dir = Path(allowed_dir).resolve()
        self._build = build
        self._verdicts_path = verdicts_path
        self._write_guard = write_guard
        self.written: list[Path] = []
        self._tools: dict[str, Callable[..., Any]] = {
            "write_file": self.write_file,
            "run_build": self.run_build,
            "read_verdicts": self.read_verdicts,
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
        resolved.write_bytes(data)
        if resolved not in self.written:
            self.written.append(resolved)
        return {"path": relative.as_posix(), "bytes": len(data)}

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
