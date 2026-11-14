"""Intake agent: turns a written product request into draft Requirement files.

The agent writes projects/<p>/spec/R-xxx.yaml. After it finishes, this module runs `engenia lint`
and, if lint fails, sends the lint output back to the agent for another round. At most
MAX_LINT_ROUNDS fix rounds are used; if lint still fails the call raises IntakeError.

The model is given the registry of checks that exist (registry.py) and may only use those. After
each round the draft is checked with `engenia lint` plus the registry rules (unknown check, limit
the check cannot test, wrong unit). Problems go back to the model for up to MAX_LINT_ROUNDS fix
rounds. A requirement still invalid after that is dropped and the pack template (the same
deterministic intake used without a model) is asked for what it can supply instead.

Requirements no check can verify are not written as requirements. The model lists them in
`design_intents.yaml` and intake saves that, with anything it had to drop, to
projects/<p>/build/intake/design_intents.yaml as "design intents, not verified".

Rules the code enforces itself, whatever the model does:
  * only files named R-nnn.yaml (and design_intents.yaml) directly in the spec directory can be
    written;
  * every file must say `status: draft`; intake never approves a requirement;
  * a requirement that is already approved is never overwritten.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from engenia.agents.intake.registry import (
    CheckInfo,
    check_requirement,
    load_registry,
    registry_text,
)
from engenia.contracts.models import STAGE_NAMES, Requirement, RequirementStatus
from engenia.harness.build import run_captured
from engenia.harness.limits import Limits
from engenia.harness.llm import ChatFn, agent_model, chat, check_model_families
from engenia.harness.loop import run_agent
from engenia.harness.tools import Toolbox, ToolError

MAX_LINT_ROUNDS = 3
PROJECT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
SPEC_FILE = re.compile(r"^R-\d{3}\.yaml$")

INTENTS_FILE = "design_intents.yaml"

SYSTEM_PROMPT = f"""\
You are the intake agent of Engenia. You turn a written product request into requirement files.
Every requirement is checked later by a script, so it must be testable by one of the checks listed
below.

Write one file per requirement with write_file. The path is the file name only, R-nnn.yaml with
three digits, and the id inside must match the file name. Every file has exactly these fields:

id: R-001
statement: One obligation, in one sentence, containing the word "shall".
value: 400            # a number
unit: g               # the check's unit, exactly as listed below
verify:
  method: analysis    # one of: analysis, simulation, test (never inspection)
  check: mass_budget  # EXACTLY a name from the list of checks below
  limit: {{max: 400}}   # the bound the check tests, as listed below
affects: [mechanical] # at least one of: {", ".join(STAGE_NAMES)}
status: draft

THE CHECKS YOU MAY USE. verify.check must be one of these names, spelled exactly. Do not invent a
check name: a name not in this list is rejected and the requirement is thrown away.

{{registry}}

Rules:
1. Every requirement needs a number (value), a unit and a limit (min or max, equals only where the
   check says so) that its check can test. A vague goal must be translated into numbers using
   the checks. Example: "survive 0.5 m waves" becomes a wave_tilt requirement (maximum tilt
   30 degrees in simulated waves up to 0.5 m significant height) plus a buoyancy_margin
   requirement. If the request gives a number, use it. If the request gives no number and you
   cannot translate the goal with a check, do not invent one.
2. If no check can verify something the request asks for (for example "uses a hydrophone", "has
   a LoRa radio", "made of PETG"), do NOT write a requirement and do NOT make up a check. Write
   it in design_intents.yaml instead (see below).
3. Always write status: draft. Never write any other status. Do not edit requirements marked
   approved.

Design intents. Write ONE file called design_intents.yaml, a YAML list, one item per thing no
check can verify:

- intent: The buoy uses a hydrophone as its sensor.
  reason: no check measures sensor choice

In this task, run_build runs `engenia lint` on the spec directory plus the check rules above and
returns its output. read_verdicts is not used. When the files are complete and run_build passes,
reply with a short summary and no tool calls.
"""


def system_prompt(registry: dict[str, CheckInfo]) -> str:
    return SYSTEM_PROMPT.replace("{registry}", registry_text(registry))


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


@dataclass
class IntakeReport:
    """What one intake call produced."""

    requirements: list[Requirement]
    intents: list[dict[str, str]] = field(default_factory=list)  # design intents, not verified
    from_template: list[str] = field(default_factory=list)  # ids the pack template supplied
    dropped: list[str] = field(default_factory=list)  # model files dropped as still invalid


def _pack_of(root: Path, project: str) -> str:
    try:
        data = yaml.safe_load((root / "projects" / project / "project.yaml").read_text("utf-8"))
    except (OSError, yaml.YAMLError):
        return "buoy"
    name = data.get("pack") if isinstance(data, dict) else None
    return name if isinstance(name, str) and re.fullmatch(r"[a-z][a-z0-9_]*", name) else "buoy"


def _registry_problems(
    spec_dir: Path, registry: dict[str, CheckInfo]
) -> dict[str, list[str]]:
    """file name -> problems, for every draft requirement the registry cannot test."""
    found: dict[str, list[str]] = {}
    for path in sorted(spec_dir.glob("R-*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue  # lint reports an unreadable file
        if isinstance(data, dict) and data.get("status") == RequirementStatus.APPROVED.value:
            continue
        problems = check_requirement(data, registry)
        if problems:
            found[path.name] = problems
    return found


def _intents_from(text: str) -> list[dict[str, str]]:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return []
    items = data.get("intents") if isinstance(data, dict) else data
    out = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict) and str(item.get("intent", "")).strip():
            out.append({
                "intent": " ".join(str(item["intent"]).split()),
                "reason": " ".join(str(item.get("reason", "no check can verify it")).split()),
            })
        elif isinstance(item, str) and item.strip():
            out.append({"intent": " ".join(item.split()), "reason": "no check can verify it"})
    return out


def intake(
    text: str,
    project: str,
    *,
    model: str | None = None,
    limits: Limits | None = None,
    chat_fn: ChatFn = chat,
    pack: str | None = None,
) -> list[Requirement]:
    """Write draft requirement files for `text` into projects/<project>/spec/ and return them.

    See `intake_report` for the design intents and the fallback details."""
    return intake_report(
        text, project, model=model, limits=limits, chat_fn=chat_fn, pack=pack
    ).requirements


def intake_report(
    text: str,
    project: str,
    *,
    model: str | None = None,
    limits: Limits | None = None,
    chat_fn: ChatFn = chat,
    pack: str | None = None,
) -> IntakeReport:
    """Write draft requirement files for `text` and report what happened.

    The repository root is the one `engenia lint` uses ($ENGENIA_ROOT, else found from cwd).
    The model sees the registry of checks in <root>/verifiers; after MAX_LINT_ROUNDS fix rounds,
    requirements still invalid are dropped and the pack template fills in what it can. Raises
    IntakeError if nothing valid is left.
    """
    if not PROJECT_NAME.fullmatch(project):
        raise IntakeError(f"invalid project name: {project!r}")
    model = model or agent_model("intake")
    if not model:
        raise IntakeError("ENGENIA_DESIGN_MODEL is not set and no model was given")
    check_model_families(model=model)
    limits = limits or Limits.from_env()

    root = _find_root()
    spec_dir = root / "projects" / project / "spec"
    spec_dir.mkdir(parents=True, exist_ok=True)
    registry = load_registry(root / "verifiers")
    pack = pack or _pack_of(root, project)

    def guard(relative: Path, content: str) -> None:
        if len(relative.parts) != 1 or not (
            SPEC_FILE.fullmatch(relative.name) or relative.name == INTENTS_FILE
        ):
            raise ToolError(
                f"write only files named R-nnn.yaml (and {INTENTS_FILE}), directly in the "
                "spec directory"
            )
        if relative.name == INTENTS_FILE:
            return
        status = _status_of(content)
        if status is not None and status != RequirementStatus.DRAFT.value:
            raise ToolError('status must be "draft"; intake never approves requirements')
        existing = spec_dir / relative.name
        if existing.is_file() and _status_of(existing.read_text("utf-8")) == "approved":
            raise ToolError(f"{relative.stem} is approved; intake does not change approved ones")

    def check_all() -> None:
        """`engenia lint` plus the registry rules; exits 1 if either finds a problem."""
        code = 0
        try:
            _lint(project)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
        extra = _registry_problems(spec_dir, registry)
        for name, problems in extra.items():
            for problem in problems:
                print(f"{name}: C1 error: {problem}", file=sys.stderr)
        if code or extra:
            raise SystemExit(code or 1)

    tools = Toolbox(spec_dir, build=check_all, write_guard=guard)
    transcript_dir = root / "projects" / project / "build" / "intake" / "transcripts"

    listing, next_id = _existing_summary(spec_dir)
    message = (
        f"Product request:\n{text.strip()}\n\n"
        f"Requirements already in the spec directory:\n{listing}\n\n"
        f"The next free id is {next_id}."
    )
    prompt = system_prompt(registry)

    history = None
    problems = ""
    exhausted = False
    for round_number in range(0, MAX_LINT_ROUNDS + 1):
        agent = run_agent(
            prompt,
            message,
            tools,
            model,
            limits,
            transcript_dir=transcript_dir,
            agent="intake",
            attempt=round_number + 1,
            history=history,
            chat_fn=chat_fn,
        )
        history = agent.messages

        checked = run_captured(check_all)
        problems = (checked.stderr + checked.stdout).strip()
        written_reqs = [p for p in tools.written if SPEC_FILE.fullmatch(p.name)]
        if checked.exit_code == 0 and not written_reqs:
            problems = "No requirement files were written."
        elif checked.exit_code == 0:
            problems = ""
        if not problems:
            break
        if round_number == MAX_LINT_ROUNDS:
            exhausted = True
            break
        message = (
            f"`engenia lint` and the check rules reported problems (fix round "
            f"{round_number + 1} of {MAX_LINT_ROUNDS}):\n{problems}\n\n"
            "Fix the files and run the build again. Use only the checks in the list; anything "
            "no check can verify goes in design_intents.yaml."
        )

    intents_path = spec_dir / INTENTS_FILE
    intents: list[dict[str, str]] = []
    if intents_path.is_file():
        intents = _intents_from(intents_path.read_text(encoding="utf-8"))
        intents_path.unlink()

    report = IntakeReport(requirements=[], intents=intents)
    if exhausted:
        _fall_back(text, project, root, spec_dir, registry, tools, pack, problems, report)

    for path in sorted(p for p in tools.written if p.is_file() and SPEC_FILE.fullmatch(p.name)):
        requirement = Requirement.model_validate(yaml.safe_load(path.read_text("utf-8")))
        if requirement.id != path.stem or requirement.status != RequirementStatus.DRAFT:
            raise IntakeError(f"{path.name} is not a draft requirement with a matching id")
        report.requirements.append(requirement)
    _save_intents(root, project, report.intents)
    return report


def _fall_back(
    text: str,
    project: str,
    root: Path,
    spec_dir: Path,
    registry: dict[str, CheckInfo],
    tools: Toolbox,
    pack: str,
    problems: str,
    report: IntakeReport,
) -> None:
    """Drop the files this run wrote that are still invalid, then ask the pack template."""
    from engenia.core.intake_template import IntakeTemplateError, template_intake
    from engenia.core.lint import Severity, lint_spec_dir

    bad = {
        f.file for f in lint_spec_dir(spec_dir, root / "verifiers")
        if f.severity is Severity.ERROR
    } | set(_registry_problems(spec_dir, registry))
    for path in list(tools.written):
        if path.name not in bad or not path.is_file():
            continue
        try:
            statement = (yaml.safe_load(path.read_text("utf-8")) or {}).get("statement")
        except (yaml.YAMLError, AttributeError):
            statement = None
        report.dropped.append(path.stem)
        report.intents.append({
            "intent": " ".join(str(statement or path.stem).split()),
            "reason": f"dropped, still invalid after {MAX_LINT_ROUNDS} fix rounds",
        })
        path.unlink()
        tools.written.remove(path)

    try:
        added = template_intake(text, project, root, pack=pack)
    except IntakeTemplateError:
        added = []
    for path in added:
        tools.written.append(path.resolve())
        report.from_template.append(path.stem)

    left = run_captured(lambda: _lint(project))
    leftover = (left.stderr + left.stdout).strip()
    extra = _registry_problems(spec_dir, registry)
    if left.exit_code != 0 or extra or not any(
        p.is_file() and SPEC_FILE.fullmatch(p.name) for p in tools.written
    ):
        detail = leftover or "\n".join(f"{n}: {'; '.join(p)}" for n, p in extra.items())
        raise IntakeError(
            f"intake output still fails lint after {MAX_LINT_ROUNDS} fix rounds and the pack "
            f"template gave nothing usable:\n{detail or problems}"
        )


def _save_intents(root: Path, project: str, intents: list[dict[str, str]]) -> None:
    """projects/<p>/build/intake/design_intents.yaml; removed when there are none."""
    path = root / "projects" / project / "build" / "intake" / INTENTS_FILE
    if not intents:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    header = "# Design intents, not verified: no check can test these. A person must.\n"
    path.write_text(header + yaml.safe_dump(intents, sort_keys=False), encoding="utf-8")
