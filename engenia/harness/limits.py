"""Per-run limits for an agent."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_MAX_TOOL_CALLS_PER_ATTEMPT = 40
DEFAULT_MAX_WALL_SECONDS_PER_ATTEMPT = 600.0


@dataclass(frozen=True)
class Limits:
    """An attempt is one agent session followed by one build and one round of checks."""

    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    max_tool_calls_per_attempt: int = DEFAULT_MAX_TOOL_CALLS_PER_ATTEMPT
    max_wall_seconds_per_attempt: float = DEFAULT_MAX_WALL_SECONDS_PER_ATTEMPT

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.max_tool_calls_per_attempt < 1:
            raise ValueError("max_tool_calls_per_attempt must be at least 1")
        if self.max_wall_seconds_per_attempt <= 0:
            raise ValueError("max_wall_seconds_per_attempt must be greater than 0")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Limits:
        """Defaults, with max_attempts taken from ENGENIA_MAX_ATTEMPTS when it is set."""
        source = os.environ if env is None else env
        raw = source.get("ENGENIA_MAX_ATTEMPTS", "").strip()
        if not raw:
            return cls()
        try:
            max_attempts = int(raw)
        except ValueError:
            raise ValueError(f"ENGENIA_MAX_ATTEMPTS must be an integer, got {raw!r}") from None
        if max_attempts < 1:
            raise ValueError(f"ENGENIA_MAX_ATTEMPTS must be at least 1, got {max_attempts}")
        return cls(max_attempts=max_attempts)
