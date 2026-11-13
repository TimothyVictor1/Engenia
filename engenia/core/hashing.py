"""Content hashes and stage keys.

A stage key is a sha256 over everything a stage's output may depend on. Same key, same output:
that is what lets the cache skip a stage (rule 5). Everything that feeds a key is listed here.

The key covers six parts, always in this order:

1. requirements: the raw bytes of the R-*.yaml files whose `affects` names the stage. A
   spec-wide stage (see SPEC_WIDE_STAGES) reads the whole specification instead, so it gets
   every approved requirement, whatever `affects` says; draft requirements do not count for it
2. upstream: the interface.json of every stage in `needs`, as canonical JSON
3. contract: contract.json as canonical JSON, only if the stage has `reads_contract`
4. src: every file under src/<stage>/
5. stage_class: the source of the stage class and of its base classes in the same package
6. container: the text of containers/<family>/DIGEST, if that file exists

Each part is reduced to one digest and the key is the sha256 of the canonical JSON of the six
(label, digest) pairs. Canonical JSON means sorted keys and no whitespace. `input_key` is the
same hash without part 4. It decides whether the agent must write new code, because the code
cannot be part of the question "does the code need rewriting?" (technical plan, section 6).

Spec files are hashed as bytes, not as parsed values. A whitespace-only or comment-only edit to
an R-xxx.yaml file therefore changes the key of every stage it affects, and those stages rerun.
That is deliberate: it is cheap to explain and impossible to get wrong. The rerun is cheap too,
because a stage that produces the same interface.json does not wake its downstream stages.
JSON inputs (interface.json, contract.json) are hashed as canonical JSON instead, so how they
were pretty-printed never matters.

Nothing here depends on absolute paths, file times, file modes or the machine, so two fresh
clones with the same committed files produce the same keys. Files that only the operating system
or Python create (.DS_Store, Thumbs.db, __pycache__) are left out of every tree hash.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from engenia.contracts.models import StageContext
from engenia.core import graph, spec
from engenia.core.errors import CoreError
from engenia.stages.base import Stage

KEY_VERSION = 1  # bump when the way keys are built changes, so old cache entries stop matching

# Stages that read every requirement, not only those whose `affects` names them. A stage class
# outside this set can opt in by setting `reads_all_requirements = True`; no contract change.
SPEC_WIDE_STAGES = frozenset(
    {"verify_plan", "architect", "testplan", "simprep", "redteam", "final", "report"}
)

PART_ORDER = ("requirements", "upstream", "contract", "src", "stage_class", "container")

IGNORED_FILES = frozenset({".DS_Store", "Thumbs.db"})
IGNORED_DIRS = frozenset({"__pycache__"})

# Which container image family builds each stage. A stage class may override this with a
# `container_family` attribute. Stages not listed here have no container digest in their key.
CONTAINER_FAMILIES = {
    "mechanical": "cad",
    "electronics": "eda",
    "firmware": "firmware",
    "simprep": "sim",
    "simulation": "sim",
}
_FAMILY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


class HashError(CoreError):
    """An input to a hash is missing or cannot be hashed."""


def canonical_json(value: Any) -> bytes:
    """Sorted keys, no whitespace, ASCII only, no NaN. The same value gives the same bytes."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _digest(value: Any) -> str:
    return _sha256(canonical_json(value))


def file_hash(path: Path) -> str:
    """sha256 of the file's bytes, as "sha256:<hex>"."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def tree_files(directory: Path, exclude: Iterable[str] = ()) -> list[tuple[str, Path]]:
    """(relative posix path, absolute path) of every file under `directory`, sorted by path.

    `exclude` holds relative posix paths to leave out. An entry names a file, or a directory
    together with everything under it; it matches whole path parts only, so "transcripts" does
    not match "transcripts.txt" or "sub/transcripts". Symlinks and anything that is not a regular
    file raise HashError: a link points outside the tree, so the tree would not be the whole
    story. Excluded paths are not looked at, so a link inside an excluded directory is fine.
    """
    root = Path(directory)
    skip = set(exclude)
    found: list[tuple[str, Path]] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        here = Path(dirpath)
        dirnames[:] = sorted(
            d
            for d in dirnames
            if d not in IGNORED_DIRS and (here / d).relative_to(root).as_posix() not in skip
        )
        for name in dirnames:
            if (here / name).is_symlink():
                raise HashError(f"symlink not allowed in a hashed tree: {here / name}")
        for name in filenames:
            if name in IGNORED_FILES:
                continue
            path = here / name
            if path.is_symlink():
                raise HashError(f"symlink not allowed in a hashed tree: {path}")
            if not path.is_file():
                raise HashError(f"not a regular file: {path}")
            rel = path.relative_to(root).as_posix()
            if rel not in skip:
                found.append((rel, path))
    found.sort(key=lambda item: item[0])
    return found


def tree_hash(directory: Path, exclude: Iterable[str] = ()) -> str:
    """sha256 over the sorted (relative path, file hash) pairs of every file in the tree.

    File names and contents count; times, modes and empty directories do not.
    """
    pairs = [[rel, file_hash(path)] for rel, path in tree_files(directory, exclude)]
    return _digest(pairs)


EMPTY_TREE_HASH = _digest([])


# --- stage keys -------------------------------------------------------------------------------


@dataclass(frozen=True)
class StageKeys:
    parts: dict[str, str]  # label to digest, in PART_ORDER; useful to explain why a key changed
    input_key: str  # every part except src
    stage_key: str  # every part


def reads_all_requirements(stage: Stage) -> bool:
    """True for the spec-wide stages and for any stage class that sets the opt-in attribute."""
    return stage.name in SPEC_WIDE_STAGES or bool(getattr(stage, "reads_all_requirements", False))


def _requirements_part(stage: Stage, ctx: StageContext) -> str:
    files = {f.requirement.id: f for f in spec.load_spec(ctx.spec_dir)}
    if reads_all_requirements(stage):
        wanted = sorted(
            (f.requirement for f in files.values() if f.requirement.status == "approved"),
            key=lambda r: r.id,
        )
    else:
        wanted = graph.requirements_for(
            stage.name, (f.requirement for f in files.values()), {stage.name: stage}
        )
    return _digest([[files[r.id].path.name, _sha256(files[r.id].raw)] for r in wanted])


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not allowed")


def _read_json(path: Path, what: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject_constant)
    except FileNotFoundError:
        raise
    except (OSError, ValueError) as exc:
        raise HashError(f"{what} is not valid JSON: {path}: {exc}") from None


def _upstream_part(stage: Stage, ctx: StageContext) -> str:
    pairs = []
    for need in sorted(stage.needs):
        path = ctx.stage_interface_path(need)
        try:
            pairs.append([need, _digest(_read_json(path, "interface.json"))])
        except FileNotFoundError:
            raise HashError(
                f"stage {stage.name!r} needs {need!r}, which has no interface.json yet ({path})"
            ) from None
    return _digest(pairs)


def _contract_part(stage: Stage, ctx: StageContext) -> str:
    if not stage.reads_contract:
        return _digest(None)
    try:
        return _digest({"contract": _read_json(ctx.contract_path, "contract.json")})
    except FileNotFoundError:
        return _digest({"contract": "absent"})  # a missing contract is a state, not an error


def _src_part(ctx: StageContext) -> str:
    return tree_hash(ctx.src_dir) if ctx.src_dir.is_dir() else EMPTY_TREE_HASH


def _class_part(stage: Stage) -> str:
    """Source of the stage's class and of every base class in the same top-level package.

    A stage that inherits its behaviour (as every step 0 stub does) is only keyed correctly if
    the base class counts. Classes from other packages (pydantic, typing) are left out so a
    library upgrade does not invalidate every stage. Code the class merely calls is not covered.
    """
    cls = type(stage)
    package = cls.__module__.split(".")[0]
    sources = []
    for klass in cls.__mro__:
        if klass is object or klass.__module__.split(".")[0] not in {package, "engenia"}:
            continue
        try:
            source = inspect.getsource(klass)
        except (OSError, TypeError) as exc:
            raise HashError(f"cannot read source of {klass.__qualname__}: {exc}") from None
        normalized = source.replace("\r\n", "\n").encode("utf-8")
        sources.append([f"{klass.__module__}.{klass.__qualname__}", _sha256(normalized)])
    return _digest(sources)


def _container_part(stage: Stage, ctx: StageContext) -> str:
    family = getattr(stage, "container_family", None) or CONTAINER_FAMILIES.get(stage.name)
    if family is None:
        return _digest(None)
    if not _FAMILY.match(family):
        raise HashError(f"bad container family name: {family!r}")
    path = ctx.root / "containers" / family / "DIGEST"
    try:
        return _digest({"family": family, "digest": path.read_text(encoding="utf-8").strip()})
    except FileNotFoundError:
        return _digest({"family": family, "digest": None})


def compute_keys(stage: Stage, ctx: StageContext) -> StageKeys:
    """Hash every declared input of `stage`. Raises SpecError, HashError."""
    parts = {
        "requirements": _requirements_part(stage, ctx),
        "upstream": _upstream_part(stage, ctx),
        "contract": _contract_part(stage, ctx),
        "src": _src_part(ctx),
        "stage_class": _class_part(stage),
        "container": _container_part(stage, ctx),
    }
    assert tuple(parts) == PART_ORDER

    def combine(labels: Iterable[str], kind: str) -> str:
        pairs = [[label, parts[label]] for label in labels]
        return _digest({"v": KEY_VERSION, "kind": kind, "stage": stage.name, "parts": pairs})

    return StageKeys(
        parts=parts,
        input_key=combine((k for k in PART_ORDER if k != "src"), "inputs"),
        stage_key=combine(PART_ORDER, "stage"),
    )


def stage_key(stage: Stage, ctx: StageContext) -> str:
    """The full key: when it is unchanged, the stage's build is unchanged."""
    return compute_keys(stage, ctx).stage_key


def input_key(stage: Stage, ctx: StageContext) -> str:
    """The key without src/<stage>/. While it is unchanged, the code need not be rewritten."""
    return compute_keys(stage, ctx).input_key
