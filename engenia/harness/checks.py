"""Run a stage's verifiers and write build/<stage>/verdict.json.

A stage names its verifiers in `Stage.checks`. The check plan (projects/<p>/checks/checks.yaml)
says which requirement each one is run for, with what params and limit. Each verifier is
`verifiers/<name>.py` with `run(ctx: CheckContext) -> Verdict`.

This module fails closed. A verifier that is missing, crashes, returns something that is not a
Verdict, or has no entry in the check plan produces a Verdict with result "error", never a pass.
"""

from __future__ import annotations

import json
import re
import sys
import types
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from engenia.contracts.models import (
    CheckContext,
    CheckPlanItem,
    EvidenceLevel,
    Limit,
    StageContext,
    Verdict,
    VerdictResult,
)
from engenia.core import events
from engenia.stages.base import Stage

VERIFIER_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
BUILD_CHECK_NAME = "build"


def error_verdict(check: str, message: str, requirement: str | None = None) -> Verdict:
    return Verdict(
        check=check,
        requirement=requirement,
        measured=None,
        limit=Limit(),
        result=VerdictResult.ERROR,
        evidence_level=EvidenceLevel.CALCULATED,
        message=message,
    )


def all_pass(verdicts: list[Verdict]) -> bool:
    """The only pass rule in the harness: every Verdict.result is "pass"."""
    return all(v.result == VerdictResult.PASS for v in verdicts)


def verdict_path(ctx: StageContext) -> Path:
    return ctx.build_dir / "verdict.json"


def write_verdicts(ctx: StageContext, verdicts: list[Verdict]) -> None:
    ctx.build_dir.mkdir(parents=True, exist_ok=True)
    payload = [v.model_dump(mode="json") for v in verdicts]
    verdict_path(ctx).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    for item in payload:
        events.emit(ctx.root, ctx.stage, "verdict", item)


def _load_plan(ctx: StageContext) -> list[CheckPlanItem]:
    path = ctx.checks_path
    if not path.is_file():
        return []
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    if not isinstance(raw, list):
        raise ValueError("checks.yaml must be a list")
    return [CheckPlanItem.model_validate(item) for item in raw]


def _ensure_on_sys_path(root: Path) -> None:
    """Put the repository root on sys.path if it is not already there.

    The `engenia` console script does not have it there, and verifiers import their shared
    helpers as `verifiers._common`.
    """
    wanted = root.resolve()
    for entry in sys.path:
        try:
            if Path(entry or ".").resolve() == wanted:
                return
        except OSError:
            continue
    sys.path.insert(0, str(wanted))


def _load_verifier(root: Path, name: str):
    if not VERIFIER_NAME.fullmatch(name):
        raise ValueError(f"invalid verifier name: {name!r}")
    path = root / "verifiers" / f"{name}.py"
    if not path.is_file():
        raise FileNotFoundError(f"verifier not found: verifiers/{name}.py")
    _ensure_on_sys_path(root)
    # A `verifiers` package cached from another root, or from before an edit, must not be reused.
    for cached in [m for m in sys.modules if m == "verifiers" or m.startswith("verifiers.")]:
        del sys.modules[cached]
    # Compile the verifier itself from source instead of importing it. An import would read and
    # write __pycache__, which can serve stale bytecode for an edited file and writes outside
    # build/. Its imports of shared helpers (`from verifiers._common import ...`, or the relative
    # form, thanks to __package__) are ordinary imports, so no bytecode is written for those.
    module = types.ModuleType(f"engenia_verifier_{name}")
    module.__file__ = str(path)
    module.__package__ = "verifiers"
    code = compile(path.read_text(encoding="utf-8"), str(path), "exec")
    keep_bytecode_flag = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        exec(code, module.__dict__)
    finally:
        sys.dont_write_bytecode = keep_bytecode_flag
    run = getattr(module, "run", None)
    if not callable(run):
        raise AttributeError(f"verifiers/{name}.py has no run(ctx)")
    return run


def run_stage_checks(stage: Stage, ctx: StageContext) -> list[Verdict]:
    """Run every verifier the stage names, once per matching check plan entry."""
    try:
        plan = _load_plan(ctx)
    except (OSError, ValueError, yaml.YAMLError, ValidationError) as exc:
        return [error_verdict(name, f"check plan is unreadable: {exc}") for name in stage.checks]

    verdicts: list[Verdict] = []
    for name in stage.checks:
        items = [item for item in plan if item.check == name]
        if not items:
            verdicts.append(error_verdict(name, f"no entry for {name} in the check plan"))
            continue
        try:
            run = _load_verifier(ctx.root, name)
        except Exception as exc:
            verdicts.extend(error_verdict(name, str(exc), item.requirement) for item in items)
            continue
        for item in items:
            verdicts.append(_run_one(run, ctx, name, item))
    return verdicts


def _run_one(run, ctx: StageContext, name: str, item: CheckPlanItem) -> Verdict:
    try:
        check_ctx = CheckContext(
            project=ctx.project,
            stage=ctx.stage,
            root=ctx.root,
            check=name,
            requirement=item.requirement,
            params=item.params,
            limit=item.limit,
        )
        verdict = run(check_ctx)
    except Exception as exc:  # a crashing verifier is an error verdict, not a pass
        return error_verdict(name, f"verifier raised {type(exc).__name__}: {exc}", item.requirement)
    if not isinstance(verdict, Verdict):
        return error_verdict(
            name, f"verifier returned {type(verdict).__name__}, not Verdict", item.requirement
        )
    return verdict
