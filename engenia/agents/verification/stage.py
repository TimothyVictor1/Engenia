"""verify_plan: turns the approved requirements into the check plan (checks/checks.yaml).

generate() asks a model (ENGENIA_VERIFY_MODEL) to write the plan. The model sees only the
requirement files and the names and docstrings of the verifiers. It never sees src/ or build/:
its one writable folder is checks/, and it may write only checks/checks.yaml there (so it cannot
write checks/APPROVED, which is the human sign-off). If no model is set, the model endpoint fails,
or its plan does not validate in `max_attempts` tries, a deterministic plan is written instead.

build() decides, by script, whether the plan is acceptable (see validate_plan) and writes
checks/checks.sha256. A person reviews the plan and copies the hash into checks/APPROVED; the
gate fails if checks.yaml no longer matches that hash. No pass or fail decision comes from a model.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from engenia.contracts.models import (
    CheckPlanItem,
    Limit,
    Requirement,
    RequirementStatus,
    StageContext,
)
from engenia.core.hashing import file_hash
from engenia.harness.limits import Limits
from engenia.harness.llm import ChatFn, chat, check_model_families
from engenia.harness.loop import run_agent
from engenia.harness.tools import Toolbox, ToolError

PLAN_FILE = "checks.yaml"
HASH_FILE = "checks.sha256"
APPROVED_FILE = "APPROVED"
META_FILE = "meta.json"
VERIFIER_NAME = re.compile(r"^[a-z][a-z0-9_]*$")

SYSTEM_PROMPT = """\
You write the check plan for an engineering project. You are given the requirement files and the
list of available verifier scripts with their docstrings. Write one file, checks.yaml, with
write_file. It is a YAML list; each item has:

  id: C-001            (C- and three digits, unique)
  requirement: R-001   (the id of an existing requirement)
  check: mass_budget   (the name of a verifier listed below; use the requirement's verify.check)
  params: {}           (optional inputs for the verifier, such as {bed_mm: [220, 220, 250]})
  limit: {max: 400}    (EXACTLY the requirement's verify.limit; never loosen or tighten it)

Every approved requirement needs at least one item that uses its own verify.check. Use run_build
to validate the file; it lists problems. When run_build reports no problems, reply with a short
summary and no tool calls. Scripts, not you, decide pass and fail.
"""


# --- inputs ------------------------------------------------------------------------------------


def verifier_docs(root: Path) -> dict[str, str]:
    """Name -> docstring of every verifier in root/verifiers, read with ast (nothing is run)."""
    docs: dict[str, str] = {}
    folder = root / "verifiers"
    if not folder.is_dir():
        return docs
    for path in sorted(folder.glob("*.py")):
        name = path.stem
        if name.startswith("_") or not VERIFIER_NAME.fullmatch(name):
            continue
        try:
            docs[name] = ast.get_docstring(ast.parse(path.read_text("utf-8"))) or ""
        except (OSError, SyntaxError):
            docs[name] = ""
    return docs


def load_requirements(spec_dir: Path) -> dict[str, Requirement]:
    """id -> Requirement for every R-*.yaml that validates. Broken files are left to lint."""
    found: dict[str, Requirement] = {}
    for path in sorted(spec_dir.glob("R-*.yaml")):
        try:
            requirement = Requirement.model_validate(yaml.safe_load(path.read_text("utf-8")))
        except (OSError, yaml.YAMLError, ValidationError):
            continue
        found[requirement.id] = requirement
    return found


def _pack(ctx: StageContext) -> dict[str, Any]:
    for base in (ctx.root, Path(__file__).resolve().parents[3]):
        path = base / "packs" / "buoy" / "pack.yaml"
        if path.is_file():
            loaded = yaml.safe_load(path.read_text("utf-8"))
            return loaded if isinstance(loaded, dict) else {}
    return {}


def _requirement_limit(requirement: Requirement) -> Limit:
    return requirement.verify.limit or Limit()


# --- the deterministic plan --------------------------------------------------------------------


def deterministic_plan(
    requirements: dict[str, Requirement], pack: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """One item per approved requirement: check = verify.check, limit = verify.limit.

    Params come from the pack where a verifier needs them (print_bed_fit needs the bed size).
    """
    pack = pack or {}
    bed = (pack.get("printing") or {}).get("bed_mm")
    plan: list[dict[str, Any]] = []
    approved = sorted(
        (r for r in requirements.values() if r.status == RequirementStatus.APPROVED),
        key=lambda r: r.id,
    )
    for number, requirement in enumerate(approved, start=1):
        params: dict[str, Any] = {}
        if requirement.verify.check == "print_bed_fit" and bed:
            params["bed_mm"] = list(bed)
        plan.append(
            {
                "id": f"C-{number:03d}",
                "requirement": requirement.id,
                "check": requirement.verify.check,
                "params": params,
                "limit": _requirement_limit(requirement).model_dump(exclude_none=True),
            }
        )
    return plan


def dump_plan(plan: list[dict[str, Any]]) -> str:
    return yaml.safe_dump(plan, sort_keys=True, default_flow_style=False)


# --- validation by script ----------------------------------------------------------------------


@dataclass
class PlanReport:
    problems: list[str] = field(default_factory=list)
    items: list[CheckPlanItem] = field(default_factory=list)
    unverified: list[str] = field(default_factory=list)  # requirement ids with no verifier yet

    @property
    def ok(self) -> bool:
        return not self.problems


def validate_plan(
    text: str, requirements: dict[str, Requirement], verifiers: set[str] | frozenset[str]
) -> PlanReport:
    """Check a checks.yaml text. Pure function of its inputs; never calls a model.

    1. every item is a valid CheckPlanItem and ids are unique;
    2. every item cites an existing requirement (CheckPlanItem has no null requirement, so an
       item with `requirement: null` is rejected);
    3. every item names an existing verifier. The one exception is a requirement whose own
       verify.check has no verifier script yet: its item may name that check, and the
       requirement is listed as unverified;
    4. every approved requirement has at least one item that runs its own verify.check;
    5. every item's limit equals its requirement's limit (params may be added).
    """
    report = PlanReport()
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        report.problems.append(f"checks.yaml is not valid YAML: {exc}")
        return report
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        report.problems.append("checks.yaml must be a list of items")
        return report

    seen_ids: set[str] = set()
    for index, entry in enumerate(raw):
        label = f"item {index + 1}"
        if isinstance(entry, dict) and entry.get("requirement") is None:
            report.problems.append(
                f"{label}: requirement is null; internal checks are not allowed in the plan"
            )
            continue
        try:
            item = CheckPlanItem.model_validate(entry)
        except ValidationError as exc:
            for err in exc.errors():
                where = ".".join(str(p) for p in err["loc"])
                report.problems.append(f"{label}: {where}: {err['msg']}")
            continue
        label = f"{item.id} ({item.check} for {item.requirement})"
        if item.id in seen_ids:
            report.problems.append(f"{label}: duplicate id {item.id}")
        seen_ids.add(item.id)
        report.items.append(item)

        requirement = requirements.get(item.requirement)
        if requirement is None:
            report.problems.append(f"{label}: cites {item.requirement}, which does not exist")
            continue
        if item.check not in verifiers:
            if item.check == requirement.verify.check:
                if requirement.id not in report.unverified:
                    report.unverified.append(requirement.id)
            else:
                report.problems.append(f"{label}: no verifier named {item.check!r}")
        if item.limit != _requirement_limit(requirement):
            report.problems.append(
                f"{label}: limit {item.limit.model_dump(exclude_none=True)} differs from "
                f"the requirement's {_requirement_limit(requirement).model_dump(exclude_none=True)}"
            )

    for requirement in requirements.values():
        if requirement.status != RequirementStatus.APPROVED:
            continue
        if not any(
            i.requirement == requirement.id and i.check == requirement.verify.check
            for i in report.items
        ):
            report.problems.append(
                f"{requirement.id}: no item runs its verifier {requirement.verify.check!r}"
            )
    report.unverified.sort()
    return report


# --- the stage ---------------------------------------------------------------------------------


def _only_plan_file(relative: Path, content: str) -> None:
    if relative.as_posix() != PLAN_FILE:
        raise ToolError(f"you may write only {PLAN_FILE}, not {relative.as_posix()}")


class VerifyPlanStage:
    name = "verify_plan"
    needs: list[str] = []
    reads_contract = False
    checks: list[str] = []

    def __init__(
        self,
        *,
        chat_fn: ChatFn = chat,
        model: str | None = None,
        limits: Limits | None = None,
    ) -> None:
        self._chat_fn = chat_fn
        self._model = model
        self._limits = limits

    # -- generate ----

    def generate(self, ctx: StageContext) -> None:
        requirements = load_requirements(ctx.spec_dir)
        plan_dir = ctx.checks_path.parent
        plan_dir.mkdir(parents=True, exist_ok=True)

        source = "deterministic"
        check_model_families()
        model = self._model or os.environ.get("ENGENIA_VERIFY_MODEL", "").strip()
        if model and self._generate_with_model(ctx, model, requirements):
            source = "model"
        if source == "deterministic":
            ctx.checks_path.write_text(
                dump_plan(deterministic_plan(requirements, _pack(ctx))), encoding="utf-8"
            )

        # The record of this generation. It also tells the engine that src/ is not empty.
        ctx.src_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ctx.checks_path, ctx.src_dir / PLAN_FILE)
        (ctx.src_dir / META_FILE).write_text(
            json.dumps({"source": source}, sort_keys=True) + "\n", encoding="utf-8"
        )

    def _generate_with_model(
        self, ctx: StageContext, model: str, requirements: dict[str, Requirement]
    ) -> bool:
        """True when the model wrote a plan that passes validate_plan."""
        docs = verifier_docs(ctx.root)
        names = set(docs)
        plan_dir = ctx.checks_path.parent

        def check() -> str:
            if not ctx.checks_path.is_file():
                return "checks.yaml has not been written"
            report = validate_plan(ctx.checks_path.read_text("utf-8"), requirements, names)
            return "\n".join(report.problems)

        def build() -> None:
            problems = check()
            print(problems or "no problems")

        ctx.checks_path.unlink(missing_ok=True)
        # The sandbox is checks/ and nothing else, and read_verdicts has no verdict file to read,
        # so nothing from src/ or build/ can reach the model.
        tools = Toolbox(plan_dir, build=build, verdicts_path=None, write_guard=_only_plan_file)
        limits = self._limits or Limits.from_env()
        transcripts = ctx.build_dir / "transcripts"
        message = _task(requirements, docs)
        history = None
        for attempt in range(1, limits.max_attempts + 1):
            agent = run_agent(
                SYSTEM_PROMPT,
                message,
                tools,
                model,
                limits,
                transcript_dir=transcripts,
                attempt=attempt,
                history=history,
                chat_fn=self._chat_fn,
            )
            if agent.status == "error":
                ctx.checks_path.unlink(missing_ok=True)
                return False
            history = agent.messages
            problems = check()
            if not problems:
                return True
            message = f"The plan did not validate:\n{problems}\nFix checks.yaml."
        ctx.checks_path.unlink(missing_ok=True)
        return False

    # -- build ----

    def build(self, ctx: StageContext) -> None:
        if not ctx.checks_path.is_file():
            raise FileNotFoundError(f"missing {ctx.checks_path}")
        requirements = load_requirements(ctx.spec_dir)
        report = validate_plan(
            ctx.checks_path.read_text("utf-8"), requirements, set(verifier_docs(ctx.root))
        )
        if not report.ok:
            raise ValueError("check plan is not valid:\n" + "\n".join(report.problems))
        digest = file_hash(ctx.checks_path)
        (ctx.checks_path.parent / HASH_FILE).write_text(digest + "\n", encoding="utf-8")
        if ctx.build_dir.exists():
            shutil.rmtree(ctx.build_dir)
        ctx.build_dir.mkdir(parents=True)
        shutil.copyfile(ctx.checks_path, ctx.build_dir / PLAN_FILE)
        (ctx.build_dir / HASH_FILE).write_text(digest + "\n", encoding="utf-8")

    def interface(self, ctx: StageContext) -> dict:
        report = validate_plan(
            ctx.checks_path.read_text("utf-8"),
            load_requirements(ctx.spec_dir),
            set(verifier_docs(ctx.root)),
        )
        source = "unknown"
        try:
            source = json.loads((ctx.src_dir / META_FILE).read_text("utf-8")).get("source", source)
        except (OSError, ValueError):
            pass
        return {
            "source": source,
            "checks_sha256": file_hash(ctx.checks_path),
            "items": len(report.items),
            "unverified": report.unverified,
        }


def _task(requirements: dict[str, Requirement], docs: dict[str, str]) -> str:
    """The first user message. Requirement files and verifier docs only."""
    lines = ["Requirement files (approved):", ""]
    for requirement in sorted(requirements.values(), key=lambda r: r.id):
        if requirement.status != RequirementStatus.APPROVED:
            continue
        lines.append(f"--- {requirement.id}.yaml")
        lines.append(yaml.safe_dump(requirement.model_dump(mode="json", exclude_none=True)))
    lines += ["", "Available verifiers:", ""]
    for name, doc in docs.items():
        lines.append(f"--- {name}")
        lines.append(doc or "(no docstring)")
        lines.append("")
    lines.append("A requirement whose verify.check is not listed has no verifier yet: still add")
    lines.append("an item that names that check.")
    return "\n".join(lines)
