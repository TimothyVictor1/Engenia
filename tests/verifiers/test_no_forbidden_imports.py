from __future__ import annotations

import ast
from pathlib import Path

import pytest

FORBIDDEN = {"httpx", "random", "time", "datetime"}
VERIFIER_DIR = Path(__file__).resolve().parents[2] / "verifiers"


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    return roots


@pytest.mark.parametrize("path", sorted(VERIFIER_DIR.glob("*.py")), ids=lambda p: p.name)
def test_verifier_does_not_import_forbidden_modules(path):
    imported = _imported_roots(path)
    assert FORBIDDEN.isdisjoint(imported), (
        f"{path.name} imports forbidden module(s): {sorted(FORBIDDEN & imported)}"
    )
