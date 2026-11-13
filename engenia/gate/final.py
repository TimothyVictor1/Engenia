"""The final stage: runs the full gate on the integrated design, then audits the verification.

Pass or fail is decided by scripts only: the gate exit code, plus two conditions counted here:
  - zero approved requirements without a Verdict (a requirement with no verifier yet counts as
    without, although the gate itself only reports it as "unverified")
  - zero Verdicts with result error
Each is a Verdict in build/final/verdict.json. The stage does not raise on failure: it writes a
failing Verdict and `passed: false` in its interface, so the report stage still has all the facts.

generate() asks the verification model (ENGENIA_VERIFY_MODEL) for an advisory audit note, in
audit mode (see engenia.agents.verification.audit). build() is deterministic: it runs the gate,
finds the audit lists by script and writes build/final/audit.md, including the saved note. With no
model the audit is the scripted lists alone. The note never affects a Verdict.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

import yaml

from engenia.agents.verification.audit import (
    audit_data,
    audit_input,
    model_advice,
    render_audit,
    requirements_without_verdict,
)
from engenia.agents.verification.stage import load_requirements
from engenia.contracts.models import (
    CheckPlanItem,
    EvidenceLevel,
    Limit,
    StageContext,
    Verdict,
    VerdictResult,
)
from engenia.core import graph
from engenia.gate.gate import GateResult, recompute_result, run_gate
from engenia.harness.llm import ChatFn, chat, check_model_families

AUDIT_FILE = "audit.md"
SUMMARY_FILE = "summary.json"
META_FILE = "meta.json"
ADVICE_FILE = "advice.md"


class FinalStage:
    name = "final"
    reads_contract = False
    checks: list[str] = []

    def __init__(
        self,
        *,
        needs: list[str] | None = None,
        registry: graph.Registry | None = None,
        chat_fn: ChatFn = chat,
        model: str | None = None,
    ) -> None:
        self.needs = list(needs) if needs is not None else ["simulation", "testplan"]
        self._registry = registry
        self._chat_fn = chat_fn
        self._model = model

    # -- helpers ----

    def _reg(self) -> graph.Registry:
        if self._registry is not None:
            return self._registry
        from engenia.stages.registry import REGISTRY

        return REGISTRY

    def _upstream(self) -> list[str]:
        """Every stage final depends on, directly or not, that exists in the registry."""
        registry, seen, todo = self._reg(), set(), list(self.needs)
        while todo:
            name = todo.pop()
            if name in seen or name not in registry:
                continue
            seen.add(name)
            todo += list(registry[name].needs)
        return sorted(seen)

    def _gate(self, ctx: StageContext, *, write_report: bool) -> GateResult:
        return run_gate(
            ctx.project, ctx.root, registry=self._reg(), write_report=write_report,
            stages=self._upstream(),
        )

    @staticmethod
    def _gate_verdicts(result: GateResult) -> list[Verdict]:
        return [
            Verdict.model_validate({k: v for k, v in raw.items() if k not in ("stage", "item")})
            for raw in result.report["verdicts"]
        ]

    def _verdicts(self, ctx: StageContext, result: GateResult) -> list[Verdict]:
        """The gate's own Verdicts, plus those stored by the upstream stages."""
        found = self._gate_verdicts(result)
        for name in self._upstream():
            path = ctx.project_dir / "build" / name / "verdict.json"
            try:
                stored = json.loads(path.read_text("utf-8"))
                found += [Verdict.model_validate(raw) for raw in stored]
            except (OSError, ValueError, TypeError):
                continue  # an unreadable file is already a G2 problem
        return found

    # -- generate: the advisory model audit ----

    def generate(self, ctx: StageContext) -> None:
        override = check_model_families()  # raises when design and verify models share a family
        model = self._model or os.environ.get("ENGENIA_VERIFY_MODEL", "").strip()
        advice = None
        if model:
            requirements = load_requirements(ctx.spec_dir)
            files = [
                p.read_text("utf-8") for p in sorted(ctx.spec_dir.glob("R-*.yaml"))
                if p.stem in requirements
            ]
            checks = ctx.checks_path.read_text("utf-8") if ctx.checks_path.is_file() else ""
            verdicts = self._verdicts(ctx, self._gate(ctx, write_report=False))
            advice = model_advice(model, audit_input(files, checks, verdicts), self._chat_fn)
        ctx.src_dir.mkdir(parents=True, exist_ok=True)
        (ctx.src_dir / ADVICE_FILE).unlink(missing_ok=True)
        if advice:
            (ctx.src_dir / ADVICE_FILE).write_text(advice + "\n", encoding="utf-8")
        meta = {
            "audit_source": "model" if advice else "deterministic",
            "family_override": override,
        }
        (ctx.src_dir / META_FILE).write_text(
            json.dumps(meta, sort_keys=True) + "\n", encoding="utf-8"
        )

    # -- build: deterministic ----

    def build(self, ctx: StageContext) -> None:
        meta: dict[str, Any] = {}
        try:
            meta = json.loads((ctx.src_dir / META_FILE).read_text("utf-8"))
        except (OSError, ValueError):
            pass
        try:
            advice = (ctx.src_dir / ADVICE_FILE).read_text("utf-8")
        except OSError:
            advice = None

        result = self._gate(ctx, write_report=True)
        verdicts = self._verdicts(ctx, result)
        requirements = list(load_requirements(ctx.spec_dir).values())
        plan = _plan_items(ctx.checks_path)
        data = audit_data(requirements, plan, verdicts)
        # Only the gate's fresh Verdicts count as having a verdict; stored ones cannot mask a gap.
        missing = requirements_without_verdict(requirements, self._gate_verdicts(result))

        data.unverified = missing
        final = [
            _count_verdict("final_gate", 0.0 if result.passed else 1.0, "gate exit code"),
            _count_verdict(
                "requirements_without_verdict", float(len(missing)),
                ", ".join(missing) or None,
            ),
            _count_verdict(
                "error_verdicts", float(data.error_count), "; ".join(data.error_checks) or None
            ),
        ]
        passed = all(v.result == VerdictResult.PASS for v in final)

        if ctx.build_dir.exists():
            shutil.rmtree(ctx.build_dir)
        ctx.build_dir.mkdir(parents=True)
        source = meta.get("audit_source", "deterministic") if advice else "deterministic"
        (ctx.build_dir / AUDIT_FILE).write_text(
            render_audit(
                data, gate_passed=result.passed, final_passed=passed, source=source,
                advice=advice, override_note=meta.get("family_override"),
            ),
            encoding="utf-8",
        )
        (ctx.build_dir / "verdict.json").write_text(
            json.dumps([v.model_dump(mode="json") for v in final], indent=2) + "\n",
            encoding="utf-8",
        )
        summary = {
            "passed": passed,
            "gate_exit_code": result.exit_code,
            "requirements_without_verdict": missing,
            "error_verdicts": data.error_count,
            "audit_source": source,
        }
        (ctx.build_dir / SUMMARY_FILE).write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    def interface(self, ctx: StageContext) -> dict:
        return json.loads((ctx.build_dir / SUMMARY_FILE).read_text("utf-8"))


def _plan_items(path: Path) -> list[CheckPlanItem]:
    if not path.is_file():
        return []
    raw = yaml.safe_load(path.read_text("utf-8")) or []
    items = []
    for entry in raw if isinstance(raw, list) else []:
        try:
            items.append(CheckPlanItem.model_validate(entry))
        except ValueError:
            continue
    return items


def _count_verdict(check: str, measured: float, message: str | None) -> Verdict:
    """A "must be zero" Verdict. The result is recomputed from the numbers, as G2 does."""
    limit = Limit(equals=0)
    result = recompute_result(measured, limit) or VerdictResult.FAIL
    return Verdict(
        check=check, requirement=None, measured=measured, limit=limit, result=result,
        evidence_level=EvidenceLevel.CALCULATED,
        message=None if result == VerdictResult.PASS else message,
    )
