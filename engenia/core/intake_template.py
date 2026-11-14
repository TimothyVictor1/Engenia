"""Deterministic intake: a request in plain language to draft requirement files, no model.

`engenia intake` uses this when no design model is configured. The rules come from the pack
(packs/<pack>/intake.yaml): each names a quantity, a pattern that finds its number in the
request, the statement and verifier it becomes, and which way the limit points. A quantity the
request gives no number for is left out (nothing is invented). Every file is written with
status draft, numbered after the highest id already in the spec, and linted before it is written;
a quantity whose verifier already has a requirement is skipped, so running intake twice on the
same request adds nothing. Approved requirements are never touched.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from engenia.contracts.models import Requirement
from engenia.core import spec
from engenia.core.errors import CoreError
from engenia.core.lint import Severity, lint_data

_CLAUSES = re.compile(r"[.;\n]+(?!\d)")  # not at a decimal point


class IntakeTemplateError(CoreError):
    """The pack has no intake rules, or the request gave nothing the rules understand."""


def pack_file(root: Path, pack: str) -> Path:
    local = Path(root) / "packs" / pack / "intake.yaml"
    return local if local.is_file() else Path(__file__).resolve().parents[2] / (
        "packs/" + pack + "/intake.yaml"
    )


def _number(text: str) -> float | int:
    value = float(text)
    return int(value) if value.is_integer() else value


def match_rules(text: str, rules: list[dict]) -> list[tuple[dict, float | int]]:
    """(rule, number) for every rule that finds a number in some clause of `text`."""
    clauses = [c.strip() for c in _CLAUSES.split(text) if c.strip()]
    found = []
    for rule in rules:
        pattern = re.compile(rule["pattern"], re.IGNORECASE)
        for clause in clauses:
            match = pattern.search(clause)
            if match:
                # A rule with a fixed `value` (a yes/no property) needs no number in the request.
                given = [g for g in match.groups() if g is not None]
                found.append((rule, rule["value"] if "value" in rule else _number(given[0])))
                break
    return found


def template_intake(text: str, project: str, root: Path, *, pack: str = "buoy") -> list[Path]:
    """Write draft requirement files for `text`; return their paths, in id order."""
    path = pack_file(root, pack)
    try:
        rules = yaml.safe_load(path.read_text(encoding="utf-8"))["requirements"]
    except (OSError, KeyError, TypeError, yaml.YAMLError) as exc:
        raise IntakeTemplateError(
            f"pack {pack!r} has no usable intake rules ({path}): {exc}"
        ) from exc

    spec_dir = Path(root) / "projects" / project / "spec"
    has_files = spec_dir.is_dir() and any(spec_dir.glob("R-*.yaml"))
    existing = spec.load_requirements(spec_dir) if has_files else []
    have = {r.verify.check for r in existing}
    next_number = max((int(r.id[2:]) for r in existing), default=0) + 1

    drafts: list[tuple[str, dict]] = []
    for rule, value in match_rules(text, rules):
        if rule["check"] in have:
            continue
        identifier = f"R-{next_number + len(drafts):03d}"
        data = {
            "id": identifier,
            "statement": rule["statement"].format(value=value),
            "value": value,
            "unit": rule["unit"],
            "verify": {
                "method": rule["method"],
                "check": rule["check"],
                "limit": {rule["bound"]: value},
            },
            "affects": list(rule["affects"]),
            "status": "draft",
        }
        Requirement.model_validate(data)
        errors = [
            f for f in lint_data(f"{identifier}.yaml", data, Path(root) / "verifiers")
            if f.severity is Severity.ERROR
        ]
        if errors:
            raise IntakeTemplateError(
                f"the {rule['name']} requirement does not pass lint: "
                + "; ".join(map(str, errors))
            )
        drafts.append((identifier, data))
    if not drafts:
        raise IntakeTemplateError(
            "the request gives no number for any quantity the pack knows "
            "(or every one already has a requirement); nothing was written"
        )
    spec_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for identifier, data in drafts:
        target = spec_dir / f"{identifier}.yaml"
        target.write_text(
            yaml.safe_dump(data, sort_keys=False, default_flow_style=None), encoding="utf-8"
        )
        written.append(target)
    return written
