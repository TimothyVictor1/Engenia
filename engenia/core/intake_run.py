"""Intake as the commands use it: the model when one is configured, else the pack template."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from engenia.core import agents as agent_registry
from engenia.core import events
from engenia.core.errors import CoreError
from engenia.core.intake_template import IntakeTemplateError, template_intake
from engenia.harness.llm import agent_model

MODEL_ENV = ("ENGENIA_LLM_API_KEY", "ENGENIA_LLM_BASE_URL")


class IntakeFailed(CoreError):
    """Intake wrote nothing usable. The message says why."""


@dataclass
class IntakeResult:
    paths: list[Path]
    how: str  # "model" or "template (<pack> pack, no model configured)"
    intents: list[dict] = field(default_factory=list)  # design intents, not verified


INTENTS_REL = Path("build") / "intake" / "design_intents.yaml"
INTENTS_TITLE = "design intents (not verified):"


def read_intents(root: Path, project: str) -> list[dict]:
    """The design intents the last model intake saved, or none."""
    try:
        data = yaml.safe_load((Path(root) / "projects" / project / INTENTS_REL).read_text("utf-8"))
    except (OSError, yaml.YAMLError):
        return []
    return [i for i in data if isinstance(i, dict)] if isinstance(data, list) else []


def _number(x: object) -> str:
    return f"{x:g}" if isinstance(x, (int, float)) and not isinstance(x, bool) else str(x)


def requirement_lines(path: Path) -> list[str]:
    """Two lines for one requirement file: id and statement, then check, value, unit, limit."""
    data = yaml.safe_load(Path(path).read_text("utf-8")) or {}
    verify = data.get("verify") or {}
    limit = ", ".join(
        f"{k} {_number(v)}" for k, v in (verify.get("limit") or {}).items() if v is not None
    )
    value = data.get("value")
    value_text = "none" if value is None else f"{_number(value)} {data.get('unit') or ''}".strip()
    return [
        f"{data.get('id')}: {data.get('statement')}",
        f"    check: {verify.get('check')}; value: {value_text}; limit: {limit or 'none'}",
    ]


def summary_lines(paths: list[Path], intents: list[dict]) -> list[str]:
    """Every drafted requirement with its check, value, unit and limit, then the design intents
    as a separate list that says they are not verified."""
    lines: list[str] = []
    for path in paths:
        lines.extend(requirement_lines(path))
    if intents:
        lines.append(INTENTS_TITLE)
        lines.extend(f"  - {i.get('intent')} ({i.get('reason')})" for i in intents)
    return lines


def model_configured() -> bool:
    return bool(agent_model("intake")) and all(
        os.environ.get(name, "").strip() for name in MODEL_ENV
    )


def run_intake(request: str, project: str, root: Path, *, pack: str = "buoy") -> IntakeResult:
    root = Path(root)
    (root / "projects" / project / INTENTS_REL).unlink(missing_ok=True)  # never a stale list
    # Intake is planning work: it logs as the intake agent in the plan phase, to the active
    # run when there is one, else to the project's newest run log.
    with events.attach(root, project), events.as_agent(
        root, "intake", agent_registry.intake_agent().as_log()
    ), events.phase(root, "intake", "plan"):
        if model_configured():
            from engenia.agents.intake import IntakeError
            from engenia.agents.intake import intake as model_intake

            try:
                requirements = model_intake(request, project)
            except IntakeError as exc:
                raise IntakeFailed(str(exc)) from exc
            paths = [root / "projects" / project / "spec" / f"{r.id}.yaml" for r in requirements]
            how = "model"
        else:
            try:
                paths = template_intake(request, project, root, pack=pack)
            except IntakeTemplateError as exc:
                raise IntakeFailed(str(exc)) from exc
            how = f"template ({pack} pack, no model configured)"
    return IntakeResult(paths, how, read_intents(root, project))
