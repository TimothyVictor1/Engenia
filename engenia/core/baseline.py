"""Pack engineering baselines become draft requirements, never automatic approvals."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from engenia.contracts.models import Requirement
from engenia.core import events, spec
from engenia.core.errors import CoreError
from engenia.core.lint import Severity, lint_data


class BaselineError(CoreError):
    """A pack baseline is invalid; do not silently omit engineering obligations."""


def load_baseline(root: Path, pack: str) -> list[dict]:
    path = Path(root) / "packs" / pack / "pack.yaml"
    if not path.is_file():
        path = Path(__file__).resolve().parents[2] / "packs" / pack / "pack.yaml"
    try:
        data = yaml.safe_load(path.read_text("utf-8")) or {}
        items = data.get("baseline", [])
        if not isinstance(items, list):
            raise ValueError("baseline must be a list")
        return items
    except (OSError, ValueError, AttributeError, yaml.YAMLError) as exc:
        raise BaselineError(f"pack {pack}: {exc}") from exc


def covers(requirement: Requirement, baseline: dict) -> bool:
    if requirement.verify.check != baseline["check"] or requirement.unit != baseline["unit"]:
        return False
    depth = baseline.get("params", {}).get("depth_m")
    if depth is not None:
        match = re.search(
            r"\bat\s+([0-9]+(?:\.[0-9]+)?)\s*m\s+(?:submersion\s+)?depth\b",
            requirement.statement, re.I,
        )
        if not match or float(match.group(1)) < depth:
            return False
    limit = requirement.verify.limit
    if limit is None:
        return False
    for bound, value in baseline["limit"].items():
        given = getattr(limit, bound)
        if given is None:
            return False
        if bound == "min" and given < value or bound == "max" and given > value:
            return False
        if bound == "equals" and given != value:
            return False
        if baseline.get("params", {}).get("strict_min") and given == value:
            if requirement.origin != "derived":
                return False
    return True


def derive_baseline(project: str, root: Path, *, pack: str) -> list[Path]:
    directory = Path(root) / "projects" / project / "spec"
    existing = spec.load_requirements(directory) if any(directory.glob("R-*.yaml")) else []
    rejected = spec.load_requirements(directory / "rejected") if any(
        (directory / "rejected").glob("R-*.yaml")
    ) else []
    number = max((int(r.id[2:]) for r in existing + rejected), default=0) + 1
    drafts = []
    for item in load_baseline(root, pack):
        try:
            reason = item["reason"]
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError("baseline reason must be nonempty")
            data = {
                "id": f"R-{number + len(drafts):03d}", "statement": item["statement"],
                "value": item["value"], "unit": item["unit"],
                "verify": {"method": item["method"], "check": item["check"],
                           "limit": item["limit"]},
                "affects": item["affects"], "status": "draft", "origin": "derived",
                "reason": f"Pack baseline ({pack}): {reason}",
            }
            requirement = Requirement.model_validate(data)
            if any(covers(r, item) for r in existing + rejected):
                continue
            errors = [f for f in lint_data(
                f"{requirement.id}.yaml", data, Path(root) / "verifiers"
            ) if f.severity is Severity.ERROR]
            if errors:
                raise ValueError("; ".join(map(str, errors)))
        except (KeyError, TypeError, ValueError) as exc:
            raise BaselineError(f"pack {pack} baseline: {exc}") from exc
        drafts.append(data)
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for data in drafts:
        path = directory / f"{data['id']}.yaml"
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        paths.append(path)
        events.emit(root, "intake", "decision", {
            "requirement": data["id"], "origin": "derived", "status": "draft",
            "reason": data["reason"], "check": data["verify"]["check"],
        })
    return paths


def reject_derived(path: Path) -> None:
    """Archive an explicitly rejected draft so intake does not propose it again."""
    requirement = Requirement.model_validate(yaml.safe_load(path.read_text("utf-8")))
    if requirement.origin != "derived" or requirement.status != "draft":
        raise BaselineError("only draft derived requirements may be rejected")
    destination = path.parent / "rejected" / path.name
    destination.parent.mkdir(exist_ok=True)
    if destination.exists():
        raise BaselineError(f"already rejected: {requirement.id}")
    path.rename(destination)
