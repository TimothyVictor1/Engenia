"""The report stage: the trace matrix (`build/report/trace_matrix.md` and `.json`).

One row per requirement in the spec: id, statement, the stages it affects, its check, the
measured value, the limit, the result, the evidence level and the artifact hash, plus counts by
result and by evidence level. A requirement with no verdict is "unverified" and is never a pass.

The verdicts are recomputed here from the current build outputs, by running the approved check
plan (checks/checks.yaml) through the same harness loaders the gate uses, so the matrix can be
produced by `engenia run` alone and stays reproducible when the gate rebuilds every stage (G3).
A requirement whose verifier file does not exist yet has no verdict to recompute: its row is
"unverified", like the gate's unverified list. Verdict recombination and the pass/fail decisions
stay with the scripts; nothing here calls a model, the network, the clock or random numbers.

`build()` writes both files. `engenia matrix <project>` recomputes the same matrix fresh and
rewrites them, which also refreshes the matrix after a verifier or plan edit that no stage key
covers. The report stage is keyed like every stage (spec, upstream interface, src), so an
unchanged project serves the cached matrix.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from engenia.contracts.models import (
    CheckPlanItem,
    Requirement,
    StageContext,
    Verdict,
)
from engenia.core.spec import load_spec
from engenia.harness.checks import VERIFIER_NAME, _load_verifier, _run_one, error_verdict

TRACE_JSON = "trace_matrix.json"
TRACE_MD = "trace_matrix.md"
RESULTS = ("pass", "fail", "error", "unverified")
EVIDENCE_LEVELS = ("calculated", "simulated", "measured", "none")


# --- verdict recomputation ---------------------------------------------------------------------


def _plan_items(checks_path: Path) -> list[CheckPlanItem]:
    """The check plan entries. Entries that do not validate are left to the gate to report."""
    if not checks_path.is_file():
        return []
    raw = yaml.safe_load(checks_path.read_text("utf-8"))
    if not isinstance(raw, list):
        return []
    items = []
    for entry in raw:
        if isinstance(entry, dict):
            try:
                items.append(CheckPlanItem.model_validate(entry))
            except ValidationError:
                continue
    return items


def _available_verifiers(root: Path) -> set[str]:
    folder = root / "verifiers"
    if not folder.is_dir():
        return set()
    return {
        path.stem
        for path in folder.glob("*.py")
        if not path.stem.startswith("_") and VERIFIER_NAME.fullmatch(path.stem)
    }


def _recompute_verdicts(
    project: str, root: Path, requirements: dict[str, Requirement], items: list[CheckPlanItem]
) -> list[Verdict]:
    """Run every plan item once, for the stage that produces its facts, as the gate's G1 does."""
    from engenia.core import graph
    from engenia.gate.gate import producing_stage
    from engenia.stages.registry import REGISTRY

    order = graph.topo_order(REGISTRY)
    verdicts: list[Verdict] = []
    for item in items:
        stage_name = producing_stage(item, requirements.get(item.requirement), REGISTRY, order)
        if stage_name is None:
            verdicts.append(
                error_verdict(item.check, "no stage produces the facts", item.requirement)
            )
            continue
        ctx = StageContext(project=project, stage=stage_name, root=root)  # type: ignore[arg-type]
        try:
            run = _load_verifier(root, item.check)
        except Exception as exc:  # the harness turns a bad verifier into an error verdict
            verdicts.append(error_verdict(item.check, str(exc), item.requirement))
            continue
        verdicts.append(_run_one(run, ctx, item.check, item))
    return verdicts


# --- the matrix --------------------------------------------------------------------------------


def _limit_text(limit: Any) -> dict[str, float]:
    if limit is None:
        return {}
    return limit.model_dump(exclude_none=True) if hasattr(limit, "model_dump") else dict(limit)


def _row_unverified(requirement: Requirement) -> dict[str, Any]:
    return {
        "id": requirement.id,
        "statement": requirement.statement,
        "stages": list(requirement.affects),
        "check": requirement.verify.check,
        "measured": None,
        "limit": _limit_text(requirement.verify.limit),
        "result": "unverified",
        "evidence_level": None,
        "artifact": None,
        "message": None,
    }


def _row_from_verdict(requirement: Requirement, verdict: Verdict) -> dict[str, Any]:
    return {
        "id": requirement.id,
        "statement": requirement.statement,
        "stages": list(requirement.affects),
        "check": requirement.verify.check,
        "measured": verdict.measured,
        "limit": _limit_text(verdict.limit),
        "result": verdict.result.value,
        "evidence_level": verdict.evidence_level.value,
        "artifact": verdict.artifact,
        "message": verdict.message,
    }


def _counts(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    by_result = {name: 0 for name in RESULTS}
    by_evidence = {name: 0 for name in EVIDENCE_LEVELS}
    for row in rows:
        by_result[row["result"] if row["result"] in by_result else "unverified"] += 1
        evidence = row["evidence_level"]
        by_evidence[evidence if evidence in by_evidence else "none"] += 1
    return {"by_result": by_result, "by_evidence_level": by_evidence}


def build_matrix(project: str, root: Path) -> dict[str, Any]:
    """The trace matrix payload: one row per requirement plus counts. Pure function of files."""
    root = Path(root)
    spec_files = load_spec(root / "projects" / project / "spec")
    requirements = {f.requirement.id: f.requirement for f in spec_files}
    available = _available_verifiers(root)
    items = _plan_items(root / "projects" / project / "checks" / "checks.yaml")
    verdicts = _recompute_verdicts(project, root, requirements, items)

    by_key: dict[tuple[str, str], Verdict] = {}
    for verdict in verdicts:  # the last verdict for a requirement and check wins
        if verdict.requirement is not None:
            by_key[(verdict.requirement, verdict.check)] = verdict

    rows = []
    for spec_file in spec_files:  # sorted by file name, so the rows follow the spec order
        requirement = spec_file.requirement
        verdict = by_key.get((requirement.id, requirement.verify.check))
        if requirement.verify.check not in available or verdict is None:
            rows.append(_row_unverified(requirement))
        else:
            rows.append(_row_from_verdict(requirement, verdict))
    return {"project": project, "rows": rows, "counts": _counts(rows)}


def _number_text(value: float | None) -> str:
    return "—" if value is None else f"{value:.12g}"


def _limit_cell(limit: dict[str, float]) -> str:
    parts = [f"{name} {_number_text(value)}" for name, value in limit.items()]
    return ", ".join(parts) if parts else "—"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render_markdown(payload: dict[str, Any]) -> str:
    """The human-readable matrix. Same rows as the JSON; no timestamps anywhere."""
    lines = [
        f"# Trace matrix: {payload['project']}",
        "",
        "One row per requirement, in spec order. The verdicts are recomputed from the build",
        "outputs by the approved check plan. \"unverified\" means no verdict was recorded and is",
        "never a pass. Refresh with `engenia matrix <project>`.",
        "",
        "| Requirement | Statement | Stages | Check | Measured | Limit | Result | Evidence "
        "| Artifact |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in payload["rows"]:
        lines.append(
            "| {id} | {statement} | {stages} | {check} | {measured} | {limit} | {result} "
            "| {evidence} | {artifact} |".format(
                id=row["id"],
                statement=_cell(row["statement"]),
                stages=_cell(", ".join(row["stages"])),
                check=_cell(row["check"]),
                measured=_number_text(row["measured"]),
                limit=_cell(_limit_cell(row["limit"])),
                result=row["result"],
                evidence=row["evidence_level"] or "—",
                artifact=row["artifact"] or "—",
            )
        )
    counts = payload["counts"]
    lines += [
        "",
        "Counts by result: "
        + ", ".join(f"{count} {name}" for name, count in counts["by_result"].items())
        + f" ({len(payload['rows'])} requirements).",
        "Counts by evidence level: "
        + ", ".join(f"{count} {name}" for name, count in counts["by_evidence_level"].items())
        + ".",
        "",
    ]
    for row in payload["rows"]:
        if row["message"]:
            lines.append(f"- {row['id']} ({row['result']}): {row['message']}")
    return "\n".join(lines)


def write_matrix(project: str, root: Path) -> tuple[dict[str, Any], str]:
    """Compute the matrix, write both files under build/report/, and return (payload, text)."""
    payload = build_matrix(project, root)
    text = render_markdown(payload)
    target = Path(root) / "projects" / project / "build" / "report"
    target.mkdir(parents=True, exist_ok=True)
    (target / TRACE_JSON).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (target / TRACE_MD).write_text(text, encoding="utf-8")
    return payload, text


# --- the stage ---------------------------------------------------------------------------------


class ReportStage:
    name = "report"
    needs = ["final"]
    reads_contract = False
    checks: list[str] = []  # the matrix is written by build; no verifier runs after it

    def generate(self, ctx: StageContext) -> None:
        ctx.src_dir.mkdir(parents=True, exist_ok=True)

    def build(self, ctx: StageContext) -> None:
        write_matrix(ctx.project, ctx.root)

    def interface(self, ctx: StageContext) -> dict:
        path = ctx.build_dir / TRACE_JSON
        payload = json.loads(path.read_text(encoding="utf-8"))
        return {
            "requirements": len(payload["rows"]),
            "by_result": payload["counts"]["by_result"],
            "by_evidence_level": payload["counts"]["by_evidence_level"],
        }
