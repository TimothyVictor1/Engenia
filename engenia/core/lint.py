"""Spec lint rules.

Rules L1 to L6 run on the raw YAML of each requirement, not on a validated Requirement. The
Requirement model already rejects some of the same mistakes (no "shall", missing value), but a
model error has no rule id and stops at the first problem. Working on raw data lets every rule
report under its own id.

Two more ids are reported by the loader checks, so that every broken file produces a finding:

* S0  the file fails the Requirement schema in a way no L rule explains (bad YAML, unknown field,
      wrong type). Reported only when no L rule found an error in that file, to avoid saying the
      same thing twice.
* S1  the id does not match the file name.
* S2  the id is used by more than one file.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from engenia.contracts.models import STAGE_NAMES, Requirement
from engenia.core import spec


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


# L6 is a warning until sprint 2, when verifiers/ is populated. Change this one line to make a
# missing verifier an error.
L6_SEVERITY = Severity.WARNING

VAGUE_WORDS = (
    "robust",
    "fast",
    "sufficient",
    "adequate",
    "easy",
    "user-friendly",
    "appropriate",
    "as needed",
)

_SHALL = re.compile(r"\bshall\b", re.IGNORECASE)
_VAGUE = {word: re.compile(rf"\b{re.escape(word)}\b", re.IGNORECASE) for word in VAGUE_WORDS}
_VERIFIER_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: Severity
    file: str
    message: str

    def __str__(self) -> str:
        return f"{self.file}: {self.rule} {self.severity.value}: {self.message}"


@dataclass(frozen=True)
class Target:
    """One requirement file as the rules see it."""

    file: str
    data: dict[str, Any]
    verifiers_dir: Path

    @property
    def statement(self) -> str:
        value = self.data.get("statement")
        return value if isinstance(value, str) else ""

    @property
    def verify(self) -> dict[str, Any]:
        value = self.data.get("verify")
        return value if isinstance(value, dict) else {}


@dataclass(frozen=True)
class Rule:
    id: str
    severity: Severity
    summary: str
    check: Callable[[Target], list[str]]  # returns one message per problem


def _l1(t: Target) -> list[str]:
    count = len(_SHALL.findall(t.statement))
    if count == 1:
        return []
    return [f'statement must contain exactly one "shall", found {count}']


def _l2(t: Target) -> list[str]:
    found = [word for word, pattern in _VAGUE.items() if pattern.search(t.statement)]
    return [f'vague word "{word}": state a measurable limit instead' for word in found]


def _l3(t: Target) -> list[str]:
    if t.verify.get("method") == "inspection":
        return []
    if t.data.get("value") is None:
        return ["value is required unless verify.method is inspection"]
    if t.data.get("unit") in (None, ""):
        return ["unit is required when value is set"]
    return []


def _l4(t: Target) -> list[str]:
    limit = t.verify.get("limit")
    if isinstance(limit, dict) and any(limit.get(k) is not None for k in ("min", "max", "equals")):
        return []
    return ["verify.limit must set at least one of min, max, equals"]


def _l5(t: Target) -> list[str]:
    affects = t.data.get("affects")
    if not isinstance(affects, list):
        return []  # a missing or malformed list is a schema problem (S0)
    return [
        f"affects names {name!r}, which is not a stage (stages: {', '.join(STAGE_NAMES)})"
        for name in affects
        if name not in STAGE_NAMES
    ]


def _l6(t: Target) -> list[str]:
    check = t.verify.get("check")
    if not isinstance(check, str) or not check:
        return ["verify.check is missing"]
    # Only plain identifiers are looked up, so a check name can never point outside verifiers/.
    if not _VERIFIER_NAME.match(check) or not (t.verifiers_dir / f"{check}.py").is_file():
        return [f"verify.check {check!r} has no file verifiers/{check}.py"]
    return []


RULES: tuple[Rule, ...] = (
    Rule("L1", Severity.ERROR, 'statement contains exactly one "shall"', _l1),
    Rule("L2", Severity.ERROR, "no vague words", _l2),
    Rule("L3", Severity.ERROR, "value and unit present unless method is inspection", _l3),
    Rule("L4", Severity.ERROR, "limit has at least one of min, max, equals", _l4),
    Rule("L5", Severity.ERROR, "affects names only real stage names", _l5),
    Rule("L6", L6_SEVERITY, "verify.check names an existing file in verifiers/", _l6),
)


def lint_data(file: str, data: Any, verifiers_dir: Path) -> list[Finding]:
    """Run rules L1 to L6, and S0 if the schema rejects what they did not explain."""
    if not isinstance(data, dict):
        return [Finding("S0", Severity.ERROR, file, "file must hold a YAML mapping")]
    target = Target(file, data, Path(verifiers_dir))
    findings = [
        Finding(rule.id, rule.severity, file, message)
        for rule in RULES
        for message in rule.check(target)
    ]
    if not any(f.severity is Severity.ERROR for f in findings):
        try:
            Requirement.model_validate(data)
        except ValidationError as exc:
            findings.extend(
                Finding("S0", Severity.ERROR, file, line) for line in spec.schema_problems(exc)
            )
    return findings


def lint_spec_dir(spec_dir: Path, verifiers_dir: Path) -> list[Finding]:
    """Lint every requirement file in `spec_dir`. Findings are sorted by file, then rule."""
    paths = spec.spec_paths(spec_dir)
    if not paths:
        return [Finding("S0", Severity.ERROR, str(spec_dir), f"no {spec.SPEC_GLOB} files found")]

    findings: list[Finding] = []
    parsed: list[tuple[Path, Any]] = []
    for path in paths:
        try:
            data = spec.parse_yaml(path, path.read_bytes())
        except spec.SpecError as exc:
            findings.extend(
                Finding("S0", Severity.ERROR, path.name, p.removeprefix(f"{path.name}: "))
                for p in exc.problems
            )
            continue
        parsed.append((path, data))
        findings.extend(lint_data(path.name, data, verifiers_dir))

    findings.extend(
        Finding(p.code, Severity.ERROR, p.file, p.message)
        for p in spec.identity_problems(parsed)
    )
    return sorted(findings, key=lambda f: (f.file, f.rule, f.message))


def has_errors(findings: list[Finding]) -> bool:
    return any(f.severity is Severity.ERROR for f in findings)
