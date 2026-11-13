"""Audit mode of the verification agent. Advisory only: nothing here can change a Verdict.

The audit input is exactly three things: the requirement files, checks.yaml and the Verdict list.
It never sees src/ or build/ contents. `audit_data` finds the problems by script, from the same
inputs, so the audit exists even with no model; a model (ENGENIA_VERIFY_MODEL) may add a written
opinion, which `render_audit` puts under an "Advisory" heading, after the scripted findings.
The final stage's pass decision does not read the model's text.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import yaml

from engenia.contracts.models import (
    CheckPlanItem,
    EvidenceLevel,
    Requirement,
    RequirementStatus,
    Verdict,
    VerdictResult,
    VerifyMethod,
)
from engenia.harness.llm import ChatFn, LLMError, message_of

AUDIT_PROMPT = """\
You audit the verification of an engineering project. You receive the requirement files, the check
plan (checks.yaml) and the list of Verdicts that scripts produced. Write a short advisory note in
Markdown: which requirements look weakly verified and why (for example a check that does not
really test the requirement's statement, or a limit that looks too loose). You cannot change any
Verdict and you do not decide pass or fail. Do not repeat the lists of missing checks, errors or
unverified requirements; a script reports those.
"""


@dataclass
class AuditData:
    no_check: list[str] = field(default_factory=list)  # approved, no item in checks.yaml
    error_checks: list[str] = field(default_factory=list)  # "<check> (<requirement>): message"
    calculated_only: list[str] = field(default_factory=list)  # method asked simulation or test
    unverified: list[str] = field(default_factory=list)  # approved, no pass or fail Verdict

    @property
    def error_count(self) -> int:
        return len(self.error_checks)


def requirements_without_verdict(
    requirements: Sequence[Requirement], verdicts: Sequence[Verdict]
) -> list[str]:
    """Approved requirements with no pass or fail Verdict (an unverified one has none)."""
    judged = {v.requirement for v in verdicts if v.result != VerdictResult.ERROR}
    return sorted(
        r.id for r in requirements if r.status == RequirementStatus.APPROVED and r.id not in judged
    )


def audit_data(
    requirements: Sequence[Requirement],
    plan: Sequence[CheckPlanItem],
    verdicts: Sequence[Verdict],
) -> AuditData:
    approved = [r for r in requirements if r.status == RequirementStatus.APPROVED]
    planned = {item.requirement for item in plan}
    data = AuditData()
    data.no_check = sorted(r.id for r in approved if r.id not in planned)
    data.unverified = requirements_without_verdict(approved, verdicts)
    data.error_checks = [
        f"{v.check} ({v.requirement or 'no requirement'}): {v.message or 'no message'}"
        for v in verdicts
        if v.result == VerdictResult.ERROR
    ]
    for requirement in sorted(approved, key=lambda r: r.id):
        if requirement.verify.method not in (VerifyMethod.SIMULATION, VerifyMethod.TEST):
            continue
        mine = [
            v for v in verdicts if v.requirement == requirement.id
            and v.result != VerdictResult.ERROR
        ]
        if mine and all(v.evidence_level == EvidenceLevel.CALCULATED for v in mine):
            data.calculated_only.append(
                f"{requirement.id}: method is {requirement.verify.method.value}, "
                "evidence is only calculated"
            )
    return data


def _section(title: str, lines: Sequence[str]) -> list[str]:
    return [f"## {title}", ""] + ([f"- {line}" for line in lines] if lines else ["None."]) + [""]


def render_audit(
    data: AuditData,
    *,
    gate_passed: bool,
    final_passed: bool,
    source: str,
    advice: str | None = None,
    override_note: str | None = None,
) -> str:
    """The text of build/final/audit.md. Deterministic for the same arguments."""
    lines = [
        "# Final audit",
        "",
        f"Result of the final stage: {'pass' if final_passed else 'FAIL'} "
        f"(gate exit code {0 if gate_passed else 1}).",
        "The lists below come from scripts. The audit never changes a Verdict.",
        f"Audit source: {source}.",
        "",
    ]
    if override_note:
        lines += ["## Model-family override", "", override_note, ""]
    lines += _section("Requirements with no check", data.no_check)
    lines += _section("Checks with result error", data.error_checks)
    lines += _section(
        "Verified only at calculated level, though the method asked for simulation or test",
        data.calculated_only,
    )
    lines += _section("Unverified requirements (no pass or fail Verdict)", data.unverified)
    if advice:
        lines += ["## Advisory note from the verification model", "", advice.strip(), ""]
    return "\n".join(lines)


def audit_input(
    requirement_files: Sequence[str], checks_text: str, verdicts: Sequence[Verdict]
) -> str:
    """The one user message sent to the model: requirement files, checks.yaml, Verdicts."""
    verdict_yaml = yaml.safe_dump(
        [v.model_dump(mode="json", exclude_none=True) for v in verdicts], sort_keys=True
    )
    parts: list[str] = ["Requirement files:", *requirement_files]
    parts += ["", "checks.yaml:", checks_text, "", "Verdicts:", verdict_yaml]
    return "\n".join(parts)


def model_advice(
    model: str, task: str, chat_fn: ChatFn
) -> str | None:
    """The model's advisory note, or None when the model cannot be used."""
    try:
        response = chat_fn(
            model,
            [{"role": "system", "content": AUDIT_PROMPT}, {"role": "user", "content": task}],
            None,
            0,
        )
        content: Any = message_of(response).get("content")
    except (LLMError, OSError):
        return None
    return content.strip() if isinstance(content, str) and content.strip() else None
