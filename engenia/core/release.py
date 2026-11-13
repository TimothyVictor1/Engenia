"""`engenia release <project>`: a zip of the verified design, only if the gate passed.

The gate is run fresh (it is the judge; a report from earlier is not trusted). If it fails,
nothing is written. If it passes, release/<project>-<hash>.zip holds:

    build/...        every stage's build outputs, the trace matrix and the gate report
                     (agent transcripts are left out: they differ on every run)
    evidence/spec/        the requirement files
    evidence/checks/      the check plan, its hash and the approval
    evidence/contract/    the contract and the record of change request decisions
    MANIFEST.json    the sha256 of every file above

<hash> is the first 12 hex digits of the sha256 of the manifest, so equal contents give equal
names. The zip is written with sorted entries and fixed timestamps, so equal contents also give
equal bytes.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from engenia.core.errors import CoreError

EPOCH = (1980, 1, 1, 0, 0, 0)
SKIP_DIRS = ("transcripts",)


class ReleaseError(CoreError):
    """The gate did not pass, or there is nothing to release."""


def _collect(project_dir: Path) -> dict[str, Path]:
    files: dict[str, Path] = {}
    build = project_dir / "build"
    if build.is_dir():
        for path in sorted(build.rglob("*")):
            relative = path.relative_to(project_dir)
            if path.is_file() and not any(part in SKIP_DIRS for part in relative.parts):
                files[relative.as_posix()] = path
    evidence = {
        "spec": sorted((project_dir / "spec").glob("R-*.yaml")),
        "checks": [
            project_dir / "checks" / n for n in ("checks.yaml", "checks.sha256", "APPROVED")
        ],
        "contract": [project_dir / "contract" / n for n in ("contract.json", "decisions.json")],
    }
    for folder, paths in evidence.items():
        for path in paths:
            if path.is_file():
                files[f"evidence/{folder}/{path.name}"] = path
    return files


def release(
    project: str,
    root: Path,
    *,
    gate: Callable[..., Any] | None = None,
    registry: Any = None,
) -> Path:
    root = Path(root)
    if gate is None:
        from engenia.gate.gate import run_gate as gate
    result = gate(project, root, registry=registry) if registry else gate(project, root)
    if not result.passed:
        failed = [name for name, g in result.report["gates"].items() if not g["passed"]]
        raise ReleaseError(f"the gate did not pass ({', '.join(failed)}); nothing was released")

    files = _collect(root / "projects" / project)
    if not any(name.startswith("build/") for name in files):
        raise ReleaseError("there are no build outputs to release")
    manifest = {
        name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in sorted(files.items())
    }
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    digest = hashlib.sha256(manifest_bytes).hexdigest()[:12]

    target = root / "release" / f"{project}-{digest}.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".zip.part")
    with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, path in sorted(files.items()):
            _add(archive, name, path.read_bytes())
        _add(archive, "MANIFEST.json", manifest_bytes)
    partial.replace(target)
    return target


def _add(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name, EPOCH)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    archive.writestr(info, data)
