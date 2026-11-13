"""Ask the design model to repair a stage whose checks failed.

`repair_stage` gives the model the failed verdicts (check, measured, limit, message), the current
source files of the stage, and the three ordinary agent tools. The tools reach only
src/<stage>/ of the stage being repaired. After each attempt the harness rebuilds the stage and
runs its checks again; "repaired" comes only from those verdicts all passing (see design_loop).

Returns:
  "repaired"   every check of the stage passes after at most MAX_REPAIR_ATTEMPTS attempts.
  "no_model"   no usable model (no API key and endpoint, or no design model name); nothing was
               changed or written.
  "exhausted"  the attempts ran out. The stage's src is put back as it was before the repair;
               build/<stage>/ and its transcripts hold the last attempt.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Literal

from engenia.contracts.models import StageContext, Verdict
from engenia.harness.checks import run_stage_checks
from engenia.harness.design_loop import CheckRunner, generate_until_pass
from engenia.harness.limits import Limits
from engenia.harness.llm import ChatFn, chat, check_model_families
from engenia.stages.base import Stage

RepairStatus = Literal["repaired", "no_model", "exhausted"]

MAX_REPAIR_ATTEMPTS = 3
MAX_FILE_CHARS = 20_000
MAX_SOURCE_CHARS = 80_000

REPAIR_PROMPT = """\
You repair the {stage} stage of an engineering project. Its checks failed. You get the failed
verdicts and the current source files. You have three tools: write_file (writes files under this
stage's source directory only), run_build (runs the stage build and returns its output) and
read_verdicts (shows the latest verification results). Change only what the failures require.
Run the build after your changes. Verification is carried out by separate scripts, and the numbers
in them decide, not your own summary. When the files are ready, reply with a short summary and no
tool calls.
"""


def _model_name(model: str | None) -> str:
    return (model or os.environ.get("ENGENIA_DESIGN_MODEL", "")).strip()


def _model_available(model: str | None, chat_fn: ChatFn) -> str | None:
    name = _model_name(model)
    if not name:
        return None
    if chat_fn is chat and not (
        os.environ.get("ENGENIA_LLM_API_KEY", "").strip()
        and os.environ.get("ENGENIA_LLM_BASE_URL", "").strip()
    ):
        return None
    return name


def failed_summary(failed: list[Verdict]) -> list[dict]:
    return [
        {
            "check": v.check,
            "requirement": v.requirement,
            "result": v.result.value,
            "measured": v.measured,
            "limit": v.limit.model_dump(exclude_none=True),
            "message": v.message,
        }
        for v in failed
    ]


def _source_listing(src_dir: Path) -> str:
    """The text files of src/<stage>/ as one block, with a cap so the prompt stays bounded."""
    parts: list[str] = []
    total = 0
    for path in sorted(p for p in src_dir.rglob("*") if p.is_file()):
        relative = path.relative_to(src_dir)
        if any(part.startswith(".") or part == "__pycache__" for part in relative.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            parts.append(f"--- {relative.as_posix()} (binary, not shown)")
            continue
        if len(text) > MAX_FILE_CHARS or total + len(text) > MAX_SOURCE_CHARS:
            parts.append(f"--- {relative.as_posix()} ({len(text)} characters, not shown)")
            continue
        total += len(text)
        parts.append(f"--- {relative.as_posix()}\n{text}")
    return "\n".join(parts) or "(no files)"


def repair_task(stage: Stage, ctx: StageContext, failed: list[Verdict]) -> str:
    return (
        f"Stage: {stage.name}\n\nThese checks failed:\n"
        f"{json.dumps(failed_summary(failed), indent=2)}\n\n"
        f"Current source files of the stage:\n{_source_listing(ctx.src_dir)}\n\n"
        "Fix the source files so these checks pass, then run the build."
    )


def repair_stage(
    stage: Stage,
    ctx: StageContext,
    failed: list[Verdict],
    *,
    model: str | None = None,
    limits: Limits | None = None,
    chat_fn: ChatFn = chat,
    run_checks: CheckRunner = run_stage_checks,
) -> RepairStatus:
    """Try to repair `stage` so the checks behind `failed` pass. See the module docstring."""
    if not failed:
        raise ValueError("repair_stage needs at least one failed verdict")
    name = _model_available(model, chat_fn)
    if name is None:
        return "no_model"
    check_model_families()  # a refusal to use this model pair is never swallowed

    limits = limits or Limits.from_env()
    limits = replace(limits, max_attempts=min(limits.max_attempts, MAX_REPAIR_ATTEMPTS))
    task = repair_task(stage, ctx, failed)

    with tempfile.TemporaryDirectory(prefix="engenia-repair-") as tmp:
        backup = Path(tmp) / "src"
        existed = ctx.src_dir.is_dir()
        if existed:
            shutil.copytree(ctx.src_dir, backup, symlinks=True)
        status = generate_until_pass(
            stage, ctx, model=name, limits=limits,
            system_prompt=REPAIR_PROMPT.format(stage=stage.name),
            task=task, chat_fn=chat_fn, run_checks=run_checks,
        )
        if status == "pass":
            return "repaired"
        shutil.rmtree(ctx.src_dir, ignore_errors=True)
        if existed:
            shutil.copytree(backup, ctx.src_dir, symlinks=True)
    return "exhausted"
