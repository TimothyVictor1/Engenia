"""Generate, build, verify, and try again with the failures, until pass or out of attempts.

Each attempt is: the agent runs (and may call run_build itself), then the harness runs the stage
build, then the stage's verifiers. The decision to stop with "pass" comes only from the
`Verdict.result` values the verifiers produced. The model's own words are never parsed for it.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Literal

import yaml

from engenia.contracts.models import Requirement, StageContext, Verdict
from engenia.core.events import Emitter
from engenia.harness.build import preserved_dir, run_captured
from engenia.harness.checks import (
    BUILD_CHECK_NAME,
    all_pass,
    error_verdict,
    run_stage_checks,
    verdict_path,
    write_verdicts,
)
from engenia.harness.limits import Limits
from engenia.harness.llm import ChatFn, chat, check_model_families
from engenia.harness.loop import run_agent
from engenia.harness.tools import Toolbox
from engenia.stages.base import Stage

DesignStatus = Literal["pass", "exhausted"]
CheckRunner = Callable[[Stage, StageContext], list[Verdict]]

DEFAULT_SYSTEM_PROMPT = """\
You are a design agent for the {stage} stage of an engineering project.

You have four tools: write_file (writes files under your source directory), run_build (runs the
stage build and returns its output), read_verdicts (shows the results of the latest
verification run) and record_decision. You MUST call record_decision for every design choice you
make (a dimension, material, part or layout), citing the requirement ids it answers and the numbers
behind it. Write the source files for this stage, run the build, and fix any build errors.
Verification is carried out by separate scripts after you finish. When your files are complete,
reply with a short summary and no tool calls.
"""


def _model_from_env() -> str:
    model = os.environ.get("ENGENIA_DESIGN_MODEL", "").strip()
    if not model:
        raise RuntimeError("ENGENIA_DESIGN_MODEL is not set and no model was given")
    return model


def default_task(stage: Stage, ctx: StageContext) -> str:
    """The first user message: the approved requirements this stage is affected by."""
    lines = [f"Stage: {stage.name}", "", "Requirements that apply to this stage:"]
    found = False
    for path in sorted(ctx.spec_dir.glob("R-*.yaml")):
        try:
            requirement = Requirement.model_validate(yaml.safe_load(path.read_text("utf-8")))
        except Exception:
            continue
        if requirement.status.value == "approved" and stage.name in requirement.affects:
            found = True
            value = ""
            if requirement.value is not None:
                value = f" ({requirement.value} {requirement.unit})"
            lines.append(f"- {requirement.id}: {requirement.statement}{value}")
    if not found:
        lines.append("- (none)")
    if stage.reads_contract and ctx.contract_path.is_file():
        contract = ctx.contract_path.read_text("utf-8")
        lines += ["", "Contract (your budgets and interfaces):", contract]
    return "\n".join(lines)


def feedback_message(attempt: int, verdicts: list[Verdict]) -> str:
    """The next user message after a failed attempt: the failing Verdicts, numbers included."""
    failed = []
    for verdict in verdicts:
        if verdict.result.value == "pass":
            continue
        failed.append(
            {
                "check": verdict.check,
                "requirement": verdict.requirement,
                "result": verdict.result.value,
                "measured": verdict.measured,
                "limit": verdict.limit.model_dump(exclude_none=True),
                "message": verdict.message,
            }
        )
    return (
        f"Attempt {attempt} did not pass verification. Failed checks:\n"
        f"{json.dumps(failed, indent=2)}\n\n"
        "Fix the source files so these checks pass, then run the build again."
    )


def generate_until_pass(
    stage: Stage,
    ctx: StageContext,
    *,
    model: str | None = None,
    limits: Limits | None = None,
    system_prompt: str | None = None,
    task: str | None = None,
    chat_fn: ChatFn = chat,
    run_checks: CheckRunner = run_stage_checks,
) -> DesignStatus:
    """Run attempts until every verdict passes ("pass") or max_attempts is used ("exhausted").

    system_prompt and task default to generic text built from the stage and its requirements;
    a stage's own agent passes its own. build/<stage>/verdict.json holds the last attempt's
    verdicts afterwards, and build/<stage>/transcripts/ holds one transcript per attempt.
    """
    check_model_families()  # refuses a design model in the verification model's family
    limits = limits or Limits.from_env()
    model = model or _model_from_env()
    system_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT.format(stage=stage.name)
    next_message = task if task is not None else default_task(stage, ctx)

    transcript_dir = ctx.build_dir / "transcripts"

    def build() -> None:
        # build() may replace build/<stage>/, which would take the transcripts with it.
        with preserved_dir(transcript_dir):
            stage.build(ctx)

    ctx.src_dir.mkdir(parents=True, exist_ok=True)
    tools = Toolbox(ctx.src_dir, build=build, verdicts_path=verdict_path(ctx),
                   events=Emitter(ctx.root, ctx.stage))

    history = None
    for attempt in range(1, limits.max_attempts + 1):
        agent = run_agent(
            system_prompt,
            next_message,
            tools,
            model,
            limits,
            transcript_dir=transcript_dir,
            attempt=attempt,
            history=history,
            chat_fn=chat_fn,
        )
        history = agent.messages

        built = run_captured(build)
        if built.exit_code != 0:
            detail = (built.stderr or built.stdout).strip()
            message = f"build exited {built.exit_code}: {detail}"
            verdicts = [error_verdict(BUILD_CHECK_NAME, message)]
        else:
            verdicts = run_checks(stage, ctx)
        write_verdicts(ctx, verdicts)

        if all_pass(verdicts):
            return "pass"
        next_message = feedback_message(attempt, verdicts)
    return "exhausted"
