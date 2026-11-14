"""Scripted acceptance before a stage is published to the cache.

Checks requiring both design branches run at integration, where both interfaces
are complete. A project need only run the checks present in its check plan.
"""
from __future__ import annotations

import json
import threading
from types import SimpleNamespace

from engenia.contracts.models import StageContext, Verdict
from engenia.core.spec import load_requirements
from engenia.harness.checks import _load_plan, run_stage_checks, write_verdicts
from engenia.stages.base import Stage

# Verifier loading replaces shared sys.modules entries. Serialize loader + execution
# until the harness has a loader independent of Python's process-global imports.
CHECK_LOCK = threading.Lock()
JOINT_CHECKS = {"buoyancy_margin", "bay_fit", "metacentric_height"}


def verify_stage(stage: Stage, ctx: StageContext, *, restored: bool = False) -> None:
    """Persist all evidence, then reject any fail/error (including build-owned checks)."""
    path = ctx.build_dir / "verdict.json"
    stored = [Verdict.model_validate(v) for v in json.loads(path.read_text("utf-8"))]
    if not restored:
        names = list(stage.checks)
        builtin = type(stage).__module__.startswith("engenia.")
        if builtin:
            plan = _load_plan(ctx)
            planned = {item.check for item in plan}
            names = [n for n in names if n in planned and n not in JOINT_CHECKS]
            if stage.name == "integration":
                names += sorted(JOINT_CHECKS & planned)
            if stage.name == "simulation":
                requirements = {r.id: r for r in load_requirements(ctx.spec_dir)}
                names += sorted({item.check for item in plan
                                 if item.requirement in requirements
                                 and "simulation" in requirements[item.requirement].affects})
        if names:
            with CHECK_LOCK:
                checked = run_stage_checks(SimpleNamespace(checks=names), ctx)
            stored += checked
            write_verdicts(ctx, stored)
    failures = [v for v in stored if v.result != "pass"]
    if failures:
        detail = "; ".join(f"{v.check}: {v.result.value} ({v.message or v.measured})"
                           for v in failures)
        raise ValueError(f"scripted checks failed: {detail}")
