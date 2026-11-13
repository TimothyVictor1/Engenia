"""Proposed requirement changes after a check fails for a requirement.

`propose_change` writes projects/<p>/spec/proposals/R-xxx.yaml: the requirement with a new limit
(and its value, when the value was the old limit), status draft, and a reason in comments that
cites each failed verdict. It is deterministic and calls no model. The file lives below spec/, so
`engenia lint <p>` and the spec loader (which read spec/R-*.yaml only) do not see it until a
person moves it up; before writing, it is linted with the same rules, and an error stops the write.

The new bound covers what was measured with a margin: a `max` limit becomes the measured value plus
5 percent, a `min` limit the measured value minus 5 percent (both to three significant figures),
an `equals` limit the measured value. Approving the change is for a person.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import yaml

from engenia.contracts.models import Requirement, RequirementStatus, StageContext, Verdict
from engenia.core.lint import Severity, lint_data

PROPOSALS_DIRNAME = "proposals"
MARGIN = 0.05
SIGNIFICANT_FIGURES = 3


def _round(x: float, direction: str) -> float:
    if x == 0:
        return 0.0
    scale = 10 ** (math.floor(math.log10(abs(x))) - (SIGNIFICANT_FIGURES - 1))
    rounder = {"up": math.ceil, "down": math.floor, "near": round}[direction]
    return float(rounder(x / scale) * scale)


def _number(x: float) -> float | int:
    return int(x) if float(x).is_integer() else x


def _new_limit(old: dict[str, float], measured: list[float]) -> dict[str, float]:
    new: dict[str, float] = {}
    if old.get("max") is not None:
        new["max"] = _round(max(measured) + abs(max(measured)) * MARGIN, "up")
    if old.get("min") is not None:
        new["min"] = _round(min(measured) - abs(min(measured)) * MARGIN, "down")
    if old.get("equals") is not None:
        new["equals"] = _round(measured[0], "near")
    return {key: _number(value) for key, value in new.items()}


def _one_line(text: object) -> str:
    return " ".join(str(text).split())


def propose_change(requirement: Requirement, failed: list[Verdict], *, ctx: StageContext) -> Path:
    """Write the proposal for `requirement` and return its path. Raises ValueError if the
    failures give nothing to propose from, or if the proposal would not pass lint."""
    mine = [
        v for v in failed
        if v.requirement in (None, requirement.id) and v.result.value != "pass"
    ]
    measured = [v.measured for v in mine if v.measured is not None]
    if requirement.verify.limit is None or not measured:
        raise ValueError(
            f"{requirement.id}: no failed verdict with a measured number to propose a limit from"
        )
    old = requirement.verify.limit.model_dump(exclude_none=True)
    new = _new_limit(old, measured)

    data = requirement.model_dump(mode="json")
    data["verify"]["limit"] = new
    data["status"] = RequirementStatus.DRAFT.value
    # A value that stated the old bound states the new one.
    for key in ("max", "min", "equals"):
        if key in old and requirement.value == old[key]:
            data["value"] = new[key]
            pattern = re.compile(rf"(?<![\d.]){re.escape(f'{old[key]:g}')}(?![\d.])")
            data["statement"] = pattern.sub(f"{new[key]:g}", data["statement"], count=1)
            break

    header = [f"Proposed change to {requirement.id}: verify.limit {old} -> {new}"
              + (f", value {requirement.value} -> {data['value']}"
                 if data["value"] != requirement.value else "")]
    header.append("Reason: these verdicts failed.")
    for v in mine:
        header.append(
            f"- {v.check} ({v.result.value}): measured {v.measured}, limit "
            f"{v.limit.model_dump(exclude_none=True)}"
            + (f", {_one_line(v.message)}" if v.message else "")
        )
    header.append("Draft. A person approves it by moving it to spec/ with status: approved.")

    findings = lint_data(f"{requirement.id}.yaml", data, ctx.root / "verifiers")
    errors = [f for f in findings if f.severity is Severity.ERROR]
    if errors:
        raise ValueError("proposal does not pass lint: " + "; ".join(str(f) for f in errors))

    path = ctx.spec_dir / PROPOSALS_DIRNAME / f"{requirement.id}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(data, sort_keys=False, default_flow_style=None, allow_unicode=True)
    path.write_text("".join(f"# {line}\n" for line in header) + body, encoding="utf-8")
    return path
