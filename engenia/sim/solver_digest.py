"""Digest of the solver code and settings, so the simulation stage key follows them.

The stage key hashes the source of the stage class, nothing it calls. SimulationStage therefore
carries `solver_digest` in its class body: a hash of every engenia/sim/**/*.py except
simulation.py (whose class source the key already hashes) and of every
packs/buoy/sim/*/params.yaml. Changing a solver or its params changes the digest; a test fails
until the committed value is refreshed with

    uv run python -m engenia.sim.solver_digest --write

which edits simulation.py, and that changes the key.
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
STAGE_FILE = Path(__file__).with_name("simulation.py")
_LINE = re.compile(r'^(    solver_digest = ")[^"]*(")$', re.MULTILINE)


def sources(repo: Path = REPO) -> list[Path]:
    code = [
        p for p in (repo / "engenia" / "sim").rglob("*.py")
        if p.name != "simulation.py" and "__pycache__" not in p.parts
    ]
    params = (repo / "packs" / "buoy" / "sim").glob("*/params.yaml")
    return sorted([*code, *params], key=lambda p: p.relative_to(repo).as_posix())


def compute(repo: Path = REPO) -> str:
    digest = hashlib.sha256()
    for path in sources(repo):
        digest.update(path.relative_to(repo).as_posix().encode() + b"\0")
        digest.update(path.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return "sha256:" + digest.hexdigest()


def committed() -> str:
    return _LINE.search(STAGE_FILE.read_text(encoding="utf-8")).group(0).split('"')[1]


def write() -> None:
    text = STAGE_FILE.read_text(encoding="utf-8")
    STAGE_FILE.write_text(_LINE.sub(rf"\g<1>{compute()}\g<2>", text), encoding="utf-8")


if __name__ == "__main__":
    if "--write" in sys.argv[1:]:
        write()
    print(compute())
