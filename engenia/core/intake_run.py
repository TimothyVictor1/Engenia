"""Intake as the commands use it: the model when one is configured, else the pack template."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

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


def model_configured() -> bool:
    return bool(agent_model("intake")) and all(
        os.environ.get(name, "").strip() for name in MODEL_ENV
    )


def run_intake(request: str, project: str, root: Path, *, pack: str = "buoy") -> IntakeResult:
    root = Path(root)
    if model_configured():
        from engenia.agents.intake import IntakeError
        from engenia.agents.intake import intake as model_intake

        try:
            requirements = model_intake(request, project)
        except IntakeError as exc:
            raise IntakeFailed(str(exc)) from exc
        paths = [root / "projects" / project / "spec" / f"{r.id}.yaml" for r in requirements]
        return IntakeResult(paths, "model")
    try:
        paths = template_intake(request, project, root, pack=pack)
    except IntakeTemplateError as exc:
        raise IntakeFailed(str(exc)) from exc
    return IntakeResult(paths, f"template ({pack} pack, no model configured)")
