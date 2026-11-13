"""Load projects/<p>/spec/R-*.yaml into Requirement objects.

The spec folder is the only source of truth (rule 1), so loading is strict: a file whose id does
not match its name, two files with the same id, bad YAML or a schema violation all stop the load
with one error that lists every problem found.

`load_spec` also keeps each file's raw bytes. Stage keys hash those bytes (see hashing.py), and
reading them in the same pass as the parse means the key and the parsed value cannot disagree.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from engenia.contracts.models import Requirement
from engenia.core.errors import CoreError

SPEC_GLOB = "R-*.yaml"

# Problem codes shared with lint.py so both report the same things under the same ids.
ID_MISMATCH = "S1"
ID_DUPLICATE = "S2"


class SpecError(CoreError):
    """The spec folder cannot be loaded. `problems` holds one line per problem."""

    def __init__(self, problems: Sequence[str]):
        self.problems = list(problems)
        super().__init__("\n".join(self.problems))


@dataclass(frozen=True)
class SpecFile:
    path: Path
    raw: bytes  # exact file bytes, hashed as they are
    requirement: Requirement


@dataclass(frozen=True)
class IdentityProblem:
    code: str  # ID_MISMATCH or ID_DUPLICATE
    file: str  # file name the problem is reported against
    message: str


def spec_paths(spec_dir: Path) -> list[Path]:
    """The requirement files of a spec folder, sorted by name."""
    return sorted(p for p in Path(spec_dir).glob(SPEC_GLOB) if p.is_file())


def parse_yaml(path: Path, raw: bytes) -> Any:
    """Parse one spec file. Raises SpecError on bad UTF-8 or bad YAML."""
    try:
        return yaml.safe_load(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise SpecError([f"{path.name}: not valid UTF-8 ({exc.reason})"]) from None
    except yaml.YAMLError as exc:
        detail = " ".join(str(exc).split())
        raise SpecError([f"{path.name}: invalid YAML: {detail}"]) from None


def identity_problems(entries: Sequence[tuple[Path, Any]]) -> list[IdentityProblem]:
    """Check that every id matches its file name and that no id is used twice.

    `entries` is (path, parsed YAML). Entries that are not mappings with a string id are skipped
    here; schema validation reports those.
    """
    problems: list[IdentityProblem] = []
    by_id: dict[str, list[str]] = defaultdict(list)
    for path, data in entries:
        if not isinstance(data, dict) or not isinstance(data.get("id"), str):
            continue
        by_id[data["id"]].append(path.name)
        if data["id"] != path.stem:
            problems.append(
                IdentityProblem(
                    ID_MISMATCH,
                    path.name,
                    f"id {data['id']} does not match the file name (expected id {path.stem})",
                )
            )
    for req_id, names in by_id.items():
        if len(names) > 1:
            for name in names:
                problems.append(
                    IdentityProblem(
                        ID_DUPLICATE,
                        name,
                        f"duplicate id {req_id}, used by {', '.join(sorted(names))}",
                    )
                )
    return problems


def schema_problems(exc: ValidationError) -> list[str]:
    """One readable line per schema violation, e.g. "verify.limit.mn: Extra inputs ..."."""
    lines = []
    for err in exc.errors():
        where = ".".join(str(part) for part in err["loc"])
        msg = err["msg"].removeprefix("Value error, ")
        lines.append(f"{where}: {msg}" if where else msg)
    return lines


def load_spec(spec_dir: Path) -> list[SpecFile]:
    """Load and validate every R-*.yaml in `spec_dir`, sorted by file name.

    Raises SpecError listing every problem: no files, bad YAML, schema violations, an id that
    does not match its file name, duplicate ids.
    """
    spec_dir = Path(spec_dir)
    paths = spec_paths(spec_dir)
    if not paths:
        raise SpecError([f"no requirement files ({SPEC_GLOB}) in {spec_dir}"])

    problems: list[str] = []
    parsed: list[tuple[Path, bytes, Any]] = []
    for path in paths:
        raw = path.read_bytes()
        try:
            parsed.append((path, raw, parse_yaml(path, raw)))
        except SpecError as exc:
            problems.extend(exc.problems)

    identity = identity_problems([(path, data) for path, _, data in parsed])
    problems.extend(f"{p.file}: {p.message}" for p in identity)

    files: list[SpecFile] = []
    for path, raw, data in parsed:
        try:
            files.append(SpecFile(path, raw, Requirement.model_validate(data)))
        except ValidationError as exc:
            problems.extend(f"{path.name}: {line}" for line in schema_problems(exc))

    if problems:
        raise SpecError(problems)
    return files


def load_requirements(spec_dir: Path) -> list[Requirement]:
    """The validated requirements of a spec folder, sorted by file name."""
    return [f.requirement for f in load_spec(spec_dir)]
