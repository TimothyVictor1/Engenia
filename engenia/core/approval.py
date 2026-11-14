"""Human sign-offs: `engenia approve` and `engenia approve --proposal`.

Two things need a person, and nothing in the engine does either on its own:

* the check plan. verify_plan writes checks/checks.yaml and checks/checks.sha256 (its hash). The
  gate only trusts a plan whose hash is in checks/APPROVED. `preview_plan` shows what changed
  since the plan that was last approved here; `apply_plan` copies checks.sha256 to APPROVED.
* a proposed requirement change. `propose_change` writes spec/proposals/R-xxx.yaml; `apply_proposal`
  moves it to spec/R-xxx.yaml with status approved, once a person has seen the difference.

"What changed" for the plan is measured against a copy of the plan taken at the last approval, in
<root>/.engenia/approved/<project>/checks.yaml. That copy is only for showing a difference; the
gate never reads it. Without one, the whole plan is shown as new.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from engenia.contracts.models import Requirement
from engenia.core import events
from engenia.core.errors import CoreError
from engenia.core.hashing import file_hash
from engenia.core.lint import Severity, lint_data

PLAN = "checks.yaml"
HASH_FILE = "checks.sha256"
APPROVED = "APPROVED"
PROPOSALS = "proposals"
_REQUIREMENT_ID = re.compile(r"^R-\d{3}$")


class ApprovalError(CoreError):
    """There is nothing valid to approve."""


@dataclass
class Preview:
    lines: list[str]  # what to show the person
    already: bool = False  # nothing to do: this exact content is already approved
    notes: list[str] = field(default_factory=list)  # what the person must do next


def _diff(old: str, new: str, old_name: str, new_name: str) -> list[str]:
    return [
        line.rstrip("\n")
        for line in difflib.unified_diff(
            old.splitlines(keepends=True), new.splitlines(keepends=True), old_name, new_name
        )
    ]


# --- the check plan ----------------------------------------------------------------------------


def _snapshot(root: Path, project: str) -> Path:
    return Path(root) / ".engenia" / "approved" / project / PLAN


def preview_plan(project: str, root: Path) -> Preview:
    checks = Path(root) / "projects" / project / "checks"
    plan, digest = checks / PLAN, checks / HASH_FILE
    if not plan.is_file() or not digest.is_file():
        raise ApprovalError(
            f"checks/{PLAN} or checks/{HASH_FILE} does not exist; run `engenia run {project}` first"
        )
    wanted = file_hash(plan).removeprefix("sha256:")
    given = digest.read_text("utf-8").strip().lower().removeprefix("sha256:")
    if given != wanted:
        raise ApprovalError(
            f"checks/{HASH_FILE} is not the hash of the current checks/{PLAN}; "
            f"run `engenia run {project}` so the plan is checked again"
        )
    approved = checks / APPROVED
    if approved.is_file() and approved.read_text("utf-8").strip().lower().removeprefix(
        "sha256:"
    ) == wanted:
        return Preview([f"checks/{PLAN} is already approved (sha256:{wanted})"], already=True)

    snapshot = _snapshot(root, project)
    new = plan.read_text("utf-8")
    if snapshot.is_file():
        lines = _diff(snapshot.read_text("utf-8"), new, "approved plan", f"checks/{PLAN}")
        header = f"changes to the check plan since it was last approved (sha256:{wanted}):"
    else:
        lines = [f"+ {line}" for line in new.splitlines()]
        header = f"the whole check plan is new (no earlier approval here) (sha256:{wanted}):"
    return Preview([header, *lines])


def apply_plan(project: str, root: Path) -> str:
    """Approve the plan: copy checks/checks.sha256 to checks/APPROVED. Returns the hash."""
    preview = preview_plan(project, root)  # checks again, so nothing stale is approved
    checks = Path(root) / "projects" / project / "checks"
    content = (checks / HASH_FILE).read_text("utf-8")
    if not preview.already:
        (checks / APPROVED).write_text(content, encoding="utf-8")
        snapshot = _snapshot(root, project)
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_text((checks / PLAN).read_text("utf-8"), encoding="utf-8")
    with events.attach(root, project):
        events.emit(root, None, "approval", {
            "what": "plan", "status": "given", "hash": content.strip(),
        })
    return content.strip()


# --- a proposed requirement change -------------------------------------------------------------


def _proposal_paths(project: str, root: Path, requirement: str) -> tuple[Path, Path]:
    if not _REQUIREMENT_ID.fullmatch(requirement):
        raise ApprovalError(f"not a requirement id: {requirement!r}")
    spec_dir = Path(root) / "projects" / project / "spec"
    return spec_dir / PROPOSALS / f"{requirement}.yaml", spec_dir / f"{requirement}.yaml"


def _approved_text(text: str) -> str:
    return re.sub(r"^status:\s*\w+\s*$", "status: approved", text, count=1, flags=re.MULTILINE)


def preview_proposal(project: str, root: Path, requirement: str) -> Preview:
    proposal, target = _proposal_paths(project, root, requirement)
    if not proposal.is_file():
        raise ApprovalError(f"no proposal for {requirement}: {proposal} does not exist")
    text = proposal.read_text("utf-8")
    try:
        data = yaml.safe_load(text)
        Requirement.model_validate({**data, "status": "approved"})
    except (yaml.YAMLError, ValueError, TypeError) as exc:
        raise ApprovalError(
            f"the proposal for {requirement} is not a valid requirement: {exc}"
        ) from exc
    if data.get("id") != requirement:
        raise ApprovalError(f"the proposal in {proposal.name} is for {data.get('id')!r}")
    errors = [
        f for f in lint_data(f"{requirement}.yaml", {**data, "status": "approved"},
                             Path(root) / "verifiers")
        if f.severity is Severity.ERROR
    ]
    if errors:
        raise ApprovalError("the proposal does not pass lint: " + "; ".join(map(str, errors)))
    new = _approved_text(text)
    old = target.read_text("utf-8") if target.is_file() else ""
    lines = _diff(old, new, f"spec/{requirement}.yaml (now)", f"spec/{requirement}.yaml (proposed)")
    notes = [
        f"checks/{PLAN} still has the old limit: run `engenia run <project>`, then "
        "`engenia approve <project>` to approve the new plan",
    ]
    return Preview([f"proposed change to {requirement}:", *lines], notes=notes)


def apply_proposal(project: str, root: Path, requirement: str) -> Preview:
    preview = preview_proposal(project, root, requirement)
    proposal, target = _proposal_paths(project, root, requirement)
    target.write_text(_approved_text(proposal.read_text("utf-8")), encoding="utf-8")
    proposal.unlink()
    with events.attach(root, project):
        events.emit(root, None, "approval", {
            "what": "proposal", "status": "given", "requirement": requirement,
            "hash": file_hash(target),
        })
    return preview


# --- drafted requirements ----------------------------------------------------------------------


def approve_requirements(paths: list[Path]) -> list[str]:
    """Mark drafted requirement files approved (status: approved). Returns their ids."""
    ids = []
    for path in paths:
        path.write_text(_approved_text(path.read_text("utf-8")), encoding="utf-8")
        ids.append(path.stem)
    return ids
