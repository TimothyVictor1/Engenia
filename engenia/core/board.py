"""The contract board: where stages read their budgets and interfaces, and ask for changes.

Stages read `projects/<p>/contract/contract.json` (or the pack default when the project has none).
A stage that cannot live within its budget writes a ChangeRequest with status `open` through
`submit_change_request`; it never edits contract.json. The engine decides:

* A request is accepted only when the contract stays valid and within budget after the change:
  it must name an existing field under `budgets`, validate as a Contract, keep every budget
  figure positive, keep mechanical + electronics mass within `totals.mass_g_max`, and keep the
  electronics bay inside the mechanical outer envelope (boxes may be turned by 90 degrees).
  Totals and interfaces are not budgets, so a stage cannot change them; that needs Timothy.
* Otherwise it is rejected, and the reason is recorded.

An accepted request is applied to contract.json and the contract `version` goes up by one, so
every stage that reads the contract is rebuilt on the next run. The request file keeps the new
status (the ChangeRequest contract has no field for a reason), and the reason for every decision
goes to `projects/<p>/contract/decisions.json`.

Requests are decided in id order, each against the contract left by the one before. The engine
decides before a run starts and after it finishes, never while stages are running, so a parallel
run sees the same contract as a serial one.
"""

from __future__ import annotations

import copy
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from engenia.contracts.models import (
    ChangeRequest,
    ChangeRequestStatus,
    Contract,
    StageContext,
)
from engenia.core.errors import CoreError

REQUESTS_DIR = "change_requests"
DECISIONS_FILE = "decisions.json"
_PACK_CONTRACT = Path("packs") / "buoy" / "default_contract.json"


class BoardError(CoreError):
    """A change request or the contract could not be read."""


@dataclass(frozen=True)
class Decision:
    id: str
    status: ChangeRequestStatus  # accepted or rejected
    reason: str
    from_stage: str
    path: str
    contract_version: int  # version of the contract after this decision


def contract_dir(root: Path, project: str) -> Path:
    return Path(root) / "projects" / project / "contract"


def _pack_contract(root: Path) -> Path:
    local = Path(root) / _PACK_CONTRACT
    return local if local.is_file() else Path(__file__).resolve().parents[2] / _PACK_CONTRACT


def load_contract(root: Path, project: str) -> Contract:
    """The project's contract, or the pack default when the project has not got one yet."""
    path = contract_dir(root, project) / "contract.json"
    if not path.is_file():
        path = _pack_contract(root)
    try:
        return Contract.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BoardError(f"cannot read contract {path}: {exc}") from exc


def read_budget(contract: Contract, stage: str) -> dict[str, Any] | None:
    """The budget of `stage`, or None for a stage that has none."""
    budget = getattr(contract.budgets, stage, None)
    return budget.model_dump(mode="json") if budget is not None else None


def read_interfaces(contract: Contract) -> dict[str, Any]:
    return contract.interfaces.model_dump(mode="json")


def submit_change_request(
    ctx: StageContext, *, path: str, requested: Any, reason: str
) -> ChangeRequest:
    """Write a new open ChangeRequest from `ctx.stage` and return it. Ids are never reused."""
    directory = contract_dir(ctx.root, ctx.project) / REQUESTS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    for number in range(1, 1000):
        request = ChangeRequest(
            id=f"CR-{number:03d}", from_stage=ctx.stage, path=path, requested=requested,
            reason=reason, status=ChangeRequestStatus.OPEN,
        )
        try:
            with (directory / f"{request.id}.json").open("x", encoding="utf-8") as stream:
                stream.write(request.model_dump_json(indent=2) + "\n")
        except FileExistsError:
            continue  # another stage took this number first
        return request
    raise BoardError("change request id space exhausted")


def load_requests(root: Path, project: str) -> list[tuple[Path, ChangeRequest]]:
    directory = contract_dir(root, project) / REQUESTS_DIR
    found = []
    for path in sorted(directory.glob("CR-*.json")):
        try:
            found.append((path, ChangeRequest.model_validate_json(path.read_text("utf-8"))))
        except (OSError, ValueError) as exc:
            raise BoardError(f"cannot read change request {path}: {exc}") from exc
    return found


# --- the decision -----------------------------------------------------------------------------


def _numbers(value: Any):
    if isinstance(value, bool):
        yield math.nan
    elif isinstance(value, (int, float)):
        yield value
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _numbers(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _numbers(item)


def _apply(data: dict, path: str, value: Any) -> str | None:
    """Set data[path] (dotted) in place. Returns a reason when the path is not an existing field."""
    parts = path.split(".")
    node: Any = data
    for part in parts[:-1]:
        if not isinstance(node, dict) or part not in node:
            return f"path {path!r} does not exist in the contract"
        node = node[part]
    if not isinstance(node, dict) or parts[-1] not in node:
        return f"path {path!r} does not exist in the contract"
    node[parts[-1]] = value
    return None


def _budget_problem(contract: Contract) -> str | None:
    budgets = contract.budgets.model_dump(mode="json")
    for number in _numbers(budgets):
        if not math.isfinite(number) or number <= 0:
            return "every budget figure must be a positive number"
    mass = contract.budgets.mechanical.mass_g + contract.budgets.electronics.mass_g
    if mass > contract.totals.mass_g_max:
        return (
            f"mechanical + electronics mass budgets total {mass:g} g, over the "
            f"{contract.totals.mass_g_max:g} g total"
        )
    bay = sorted(contract.budgets.electronics.bay_mm)
    envelope = sorted(contract.budgets.mechanical.outer_envelope_mm)
    if any(b > e for b, e in zip(bay, envelope, strict=True)):
        return (
            f"electronics bay {list(contract.budgets.electronics.bay_mm)} mm does not fit in the "
            f"mechanical outer envelope {list(contract.budgets.mechanical.outer_envelope_mm)} mm"
        )
    return None


def evaluate(contract: Contract, request: ChangeRequest) -> tuple[Contract | None, str]:
    """Return (new contract, reason) if the request is acceptable, else (None, reason)."""
    if request.path != "budgets" and not request.path.startswith("budgets."):
        return None, (
            f"{request.path!r} is not a budget; only budgets can be changed by a stage "
            "(totals, interfaces and version need Timothy)"
        )
    data = contract.model_dump(mode="json")
    problem = _apply(data, request.path, copy.deepcopy(request.requested))
    if problem:
        return None, problem
    try:
        changed = Contract.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        return None, f"requested value is not valid for {where}: {first['msg']}"
    problem = _budget_problem(changed)
    if problem:
        return None, problem
    return changed, "stays within budgets"


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def process_change_requests(root: Path, project: str) -> list[Decision]:
    """Decide every open request. Returns the decisions made now, in id order."""
    open_requests = [
        (p, r) for p, r in load_requests(root, project) if r.status is ChangeRequestStatus.OPEN
    ]
    if not open_requests:
        return []
    directory = contract_dir(root, project)
    contract = load_contract(root, project)
    decisions: list[Decision] = []
    changed = False
    for path, request in open_requests:
        updated, reason = evaluate(contract, request)
        if updated is not None:
            status = ChangeRequestStatus.ACCEPTED
            if updated != contract:
                updated = updated.model_copy(update={"version": contract.version + 1})
                contract, changed = updated, True
        else:
            status = ChangeRequestStatus.REJECTED
        decisions.append(Decision(
            request.id, status, reason, request.from_stage, request.path, contract.version
        ))
        path.write_text(
            request.model_copy(update={"status": status}).model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
    if changed:
        _write_json(directory / "contract.json", contract.model_dump(mode="json"))
    _record_decisions(directory / DECISIONS_FILE, decisions)
    return decisions


def _record_decisions(path: Path, decisions: list[Decision]) -> None:
    try:
        history = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        history = []
    except ValueError as exc:
        raise BoardError(f"cannot read {path}: {exc}") from exc
    known = {entry["id"] for entry in history}
    for d in decisions:
        if d.id not in known:
            history.append({
                "id": d.id, "status": d.status.value, "reason": d.reason,
                "from_stage": d.from_stage, "path": d.path, "contract_version": d.contract_version,
            })
    _write_json(path, sorted(history, key=lambda entry: entry["id"]))
