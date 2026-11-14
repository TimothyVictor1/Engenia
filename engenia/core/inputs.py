"""Explicit file/tree inputs for built-in stages, relative to the repository root.

Stages can override these declarations with input_files/input_trees attributes. The
{project} placeholder resolves to projects/<name>; absent paths are hashed too, so
creating or deleting an optional input invalidates the cache. Trees include names
and contents, never timestamps. Keep these declarations beside stage registration
when adding a new reader. Shared code is deliberately conservative.
"""

from engenia.stages.base import Stage

COMMON_TREES = ("engenia/core", "engenia/contracts", "engenia/harness")
PACK_TREES = ("packs",)
PROJECT_TREES = ("{project}/calibration",)

# Generation and build helpers must count, including module-level functions (the
# class-source hash alone cannot see them). Verification is an input to acceptance.
STAGE_TREES = {
    "verify_plan": ("engenia/agents/verification",),
    "architect": ("engenia/agents/architect", "engenia/agents/verification"),
    "mechanical": ("engenia/agents/mechanical",),
    "electronics": ("engenia/agents/electronics",),
    "firmware": ("engenia/agents/firmware",),
    "redteam": ("engenia/agents/redteam", "{project}/scenarios"),
    "testplan": ("engenia/agents/testplan",),
    "simprep": ("engenia/sim",),
    "integration": ("engenia/stages/integration",),
    "simulation": ("engenia/sim",),
    "final": ("engenia",),
    "report": ("engenia",),
}


def declared_inputs(stage: Stage) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return (files, trees); custom stages opt in without inheriting buoy inputs."""
    builtin = type(stage).__module__.startswith("engenia.") and stage.name in STAGE_TREES
    files = ("uv.lock", "engenia/stages/base.py", "engenia/stages/registry.py",
             "{project}/checks/checks.yaml", "{project}/checks/APPROVED",
             "{project}/project.yaml",
             "{project}/calibration.yaml", "{project}/calibration.json") if builtin else ()
    trees = (COMMON_TREES + PACK_TREES + PROJECT_TREES + ("verifiers", "calibration")
             + STAGE_TREES[stage.name]) if builtin else ()
    # verify_plan produces the plan: hashing its own output into its input key
    # would force regeneration on every run. Downstream stages hash it instead.
    if stage.name == "verify_plan":
        files = tuple(p for p in files if "/checks/" not in p)
    files = tuple(getattr(stage, "input_files", files))
    trees = tuple(getattr(stage, "input_trees", trees))
    if stage.checks:
        files += ("{project}/checks/checks.yaml",)
        trees += ("verifiers",)
    return files, trees
