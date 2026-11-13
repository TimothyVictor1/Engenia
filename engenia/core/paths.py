"""Where things live: the repository root.

Kept apart from cli.py so the engine and the cache can find the root without importing the CLI.
"""

from __future__ import annotations

import os
from pathlib import Path


def find_root() -> Path:
    """Repository root: $ENGENIA_ROOT, else the nearest parent of cwd holding projects/."""
    env = os.environ.get("ENGENIA_ROOT")
    if env:
        return Path(env).resolve()
    cwd = Path.cwd().resolve()
    for candidate in (cwd, *cwd.parents):
        if (candidate / "projects").is_dir() and (candidate / "pyproject.toml").is_file():
            return candidate
    return Path(__file__).resolve().parents[2]
