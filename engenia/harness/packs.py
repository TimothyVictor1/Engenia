"""Per-project product pack: the pack named in projects/<p>/project.yaml.

A project may build a product from a pack other than the buoy: `project.yaml` in the
project directory names it. A missing project.yaml means the buoy pack, so older
projects run unchanged. A malformed project.yaml, or one naming a field other than
`pack`, is an error, never a silent fallback to the buoy pack.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from engenia.contracts.models import StageContext

REPO = Path(__file__).resolve().parents[2]
DEFAULT_PACK = "buoy"
PROJECT_FILE = "project.yaml"
_PACK_NAME = re.compile(r"[a-z][a-z0-9_]*")


def project_pack(ctx: StageContext) -> str:
    """The pack name from projects/<p>/project.yaml, defaulting to the buoy pack."""
    path = ctx.project_dir / PROJECT_FILE
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return DEFAULT_PACK
    except yaml.YAMLError as exc:
        raise ValueError(f"{path.name}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: expected a mapping")
    unknown = sorted(set(data) - {"pack"})
    if unknown:
        raise ValueError(f"{path.name}: unsupported keys {unknown}; only 'pack' is read")
    name = data.get("pack", DEFAULT_PACK)
    if not isinstance(name, str) or not _PACK_NAME.fullmatch(name):
        raise ValueError(f"{path.name}: pack must be a lowercase name, got {name!r}")
    return name


def pack_file(ctx: StageContext, name: str) -> Path:
    """packs/<pack>/<name>, preferring the run root's copy over the repository's."""
    pack = project_pack(ctx)
    local = ctx.root / "packs" / pack / name
    return local if local.is_file() else REPO / "packs" / pack / name


# Stages a product can leave out. A bracket has no electronics and so no firmware to write.
SKIPPABLE = ("electronics", "firmware")


def pack_config(ctx: StageContext) -> dict:
    """The project's pack.yaml as a dict (empty when the file is missing or not a mapping)."""
    try:
        data = yaml.safe_load(pack_file(ctx, "pack.yaml").read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def skipped_stages(ctx: StageContext) -> frozenset[str]:
    """Stages the project's pack leaves out: `skip_stages:` in pack.yaml, electronics and
    firmware only. A skipped stage still runs and publishes a `not_applicable` interface, so
    the graph, the cache and the gate treat it like any other stage."""
    names = pack_config(ctx).get("skip_stages") or []
    unknown = sorted(set(names) - set(SKIPPABLE))
    if not isinstance(names, list) or unknown:
        raise ValueError(
            f"pack.yaml: skip_stages may only name {list(SKIPPABLE)}, got {names!r}"
        )
    if "electronics" in names and "firmware" not in names:
        raise ValueError(
            "pack.yaml: skip_stages: skipping electronics also skips firmware, which needs it"
        )
    return frozenset(names)
