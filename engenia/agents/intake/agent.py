"""Intake agent: turns a written product request into draft Requirement files.

The agent writes projects/<p>/spec/R-xxx.yaml. After it finishes, this module runs `engenia lint`
and, if lint fails, sends the lint output back to the agent for another round. At most
MAX_LINT_ROUNDS fix rounds are used; if lint still fails the call raises IntakeError.

Rules the code enforces itself, whatever the model does:
  * only files named R-nnn.yaml directly in the spec directory can be written;
  * every file must say `status: draft`; intake never approves a requirement;
  * a requirement that is already approved is never overwritten.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml

from engenia.contracts.models import STAGE_NAMES, Requirement, RequirementStatus
from engenia.harness.build import run_captured
from engenia.harness.limits import Limits
from engenia.harness.llm import ChatFn, chat
from engenia.harness.loop import run_agent
from engenia.harness.tools import Toolbox, ToolError

MAX_LINT_ROUNDS = 3
PROJECT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
SPEC_FILE = re.compile(r"^R-\d{3}\.yaml$")

SYSTEM_PROMPT = f"""\
You are the intake agent of Engenia. You turn a written product request into requirement files.

Write one file per requirement with write_file. The path is the file name only, R-nnn.yaml with
three digits, and the id inside must match the file name. Every file has exactly these fields:

id: R-001
statement: One obligation, in one sentence, containing the word "shall".
value: 400            # a number, or null only when verify.method is inspection
unit: g               # required whenever value is set
verify:
  method: analysis    # one of: inspection, analysis, simulation, test
  check: mass_budget  # lower_snake_case name of the check that will verify it
  limit: {{max: 400}}   # any of min, max, equals
affects: [mechanical] # at least one of: {", ".join(STAGE_NAMES)}
status: draft

Always write status: draft. Never write any other status. Do not edit requirements marked
approved. Use only what the request says; if it gives no number for something, do not invent one,
leave that requirement out and say so in your final message.

In this task, run_build runs `engenia lint` on the spec directory and returns its output.
read_verdicts is not used. When the files are complete and lint passes, reply with a short summary
and no tool calls.
"""


class IntakeError(RuntimeError):
    """Intake could not produce requirement files that pass lint."""


def _find_root() -> Path:
    from engenia.core.cli import find_root  # imported late: the CLI module is heavy

    return find_root()


def _status_of(text: str) -> object:
    """The `status` field of requirement YAML, or None if it is unparseable or absent."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    return data.get("status") if isinstance(data, dict) else None


def _lint(project: str) -> None:
    """Run `engenia lint <project>`. Exits with its exit code, like the command does."""
    import typer

    from engenia.core.cli import lint as lint_command

    try:
        lint_command(project)
    except typer.Exit as exc:
        raise SystemExit(exc.exit_code) from None


def _existing_summary(spec_dir: Path) -> tuple[str, str]:
    """(text listing the requirements already in the spec dir, the next free id)."""
    lines: list[str] = []
    highest = 0
    for path in sorted(spec_dir.glob("R-*.yaml")):
        match = re.fullmatch(r"R-(\d{3})\.yaml", path.name)
        if match:
            highest = max(highest, int(match.group(1)))
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            lines.append(f"- {path.stem} [{data.get('status')}]: {data.get('statement')}")
        except (OSError, yaml.YAMLError, AttributeError):
            lines.append(f"- {path.stem} [unreadable]")
    return "\n".join(lines) or "(none)", f"R-{highest + 1:03d}"


def intake(
    text: str,
    project: str,
    *,
    model: str | None = None,
    limits: Limits | None = None,
    chat_fn: ChatFn = chat,
) -> list[Requirement]:
    """Write draft requirement files for `text` into projects/<project>/spec/ and return them.

    The repository root is the one `engenia lint` uses ($ENGENIA_ROOT, else found from cwd).
    Raises IntakeError if no files were written or lint still fails after MAX_LINT_ROUNDS rounds.
    """
    if not PROJECT_NAME.fullmatch(project):
        raise IntakeError(f"invalid project name: {project!r}")
    model = model or os.environ.get("ENGENIA_DESIGN_MODEL", "").strip()
    if not model:
        raise IntakeError("ENGENIA_DESIGN_MODEL is not set and no model was given")
    limits = limits or Limits.from_env()

    root = _find_root()
    spec_dir = root / "projects" / project / "spec"
    spec_dir.mkdir(parents=True, exist_ok=True)

    def guard(relative: Path, content: str) -> None:
        if len(relative.parts) != 1 or not SPEC_FILE.fullmatch(relative.name):
            raise ToolError("write only files named R-nnn.yaml, directly in the spec directory")
        status = _status_of(content)
        if status is not None and status != RequirementStatus.DRAFT.value:
            raise ToolError('status must be "draft"; intake never approves requirements')
        existing = spec_dir / relative.name
        if existing.is_file() and _status_of(existing.read_text("utf-8")) == "approved":
            raise ToolError(f"{relative.stem} is approved; intake does not change approved ones")

    tools = Toolbox(spec_dir, build=lambda: _lint(project), write_guard=guard)
    transcript_dir = root / "projects" / project / "build" / "intake" / "transcripts"

    listing, next_id = _existing_summary(spec_dir)
    message = (
        f"Product request:\n{text.strip()}\n\n"
        f"Requirements already in the spec directory:\n{listing}\n\n"
        f"The next free id is {next_id}."
    )

    history = None
    problems = ""
    for round_number in range(0, MAX_LINT_ROUNDS + 1):
        agent = run_agent(
            SYSTEM_PROMPT,
            message,
            tools,
            model,
            limits,
            transcript_dir=transcript_dir,
            attempt=round_number + 1,
            history=history,
            chat_fn=chat_fn,
        )
        history = agent.messages

        linted = run_captured(lambda: _lint(project))
        problems = (linted.stderr + linted.stdout).strip()
        if linted.exit_code == 0 and not tools.written:
            problems = "No requirement files were written."
        elif linted.exit_code == 0:
            problems = ""
        if not problems:
            break
        if round_number == MAX_LINT_ROUNDS:
            raise IntakeError(
                f"intake output still fails lint after {MAX_LINT_ROUNDS} fix rounds:\n{problems}"
            )
        message = (
            f"`engenia lint` reported problems (fix round {round_number + 1} of "
            f"{MAX_LINT_ROUNDS}):\n{problems}\n\nFix the files and run the build again."
        )

    requirements: list[Requirement] = []
    for path in sorted(tools.written):
        requirement = Requirement.model_validate(yaml.safe_load(path.read_text("utf-8")))
        if requirement.id != path.stem or requirement.status != RequirementStatus.DRAFT:
            raise IntakeError(f"{path.name} is not a draft requirement with a matching id")
        requirements.append(requirement)
    return requirements
