"""The agents of the system: who acts, with which model, and what they are doing now.

Every event in the design log carries one of these identities, and `GET /api/agents` lists
the whole registry with live state. An identity is static (name, role, model, family, kind);
the live part (status, current stage and phase, counts) is derived from the run's event log
plus the run database, so a dashboard in another process sees the same picture.

Model names come from the harness's per-agent model map (engenia.harness.llm.agent_models)
when that exists, else from the environment: the design model (ENGENIA_DESIGN_MODEL) drives
the design agents, the repair agent and intake; the verification model
(ENGENIA_VERIFY_MODEL) drives verify_plan and final. A stage with no model configured is
deterministic. Verifier scripts and the gate are scripts: they call no model at all.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from engenia.core import events as design_events
from engenia.harness.llm import model_family

DESIGN_MODEL_ENV = "ENGENIA_DESIGN_MODEL"
VERIFY_MODEL_ENV = "ENGENIA_VERIFY_MODEL"

# Role names the harness may key its per-agent model map by, for agents whose role is not
# the model kind itself ("design" and "verify" coarse keys -> the roles they cover).
_ROLE_ALIASES = {"design": "design", "verify": "verify_plan"}


def _agent_models() -> dict[str, dict[str, str]]:
    """Per-agent {model, family} from the harness when it provides one (t2-model-per-agent);
    an empty dict (and the env fallbacks below) until that lands."""
    try:
        from engenia.harness.llm import agent_models
    except ImportError:
        return {}
    try:
        found = agent_models()
    except Exception:  # noqa: BLE001 - a broken map must not break the registry
        return {}
    return found if isinstance(found, dict) else {}


def _model_for(name: str, role: str, env: str | None) -> tuple[str, str]:
    """(model, family) for one agent: the harness map when it names us, else the env var."""
    known = _agent_models()
    info = known.get(name) or known.get(role) or known.get(_ROLE_ALIASES.get(role, ""))
    model = str((info or {}).get("model", "") or "").strip()
    if not model and env is not None:
        model = os.environ.get(env, "").strip()
    family = str((info or {}).get("family", "") or "").strip()
    if not family and model:
        family = model_family(model)
    return model, family

# Roles of the stage agents. The stage list is frozen (AGENTS.md section 3); a stage with a
# model gets kind "llm" when that model is configured, else it is deterministic.
STAGE_ROLES: dict[str, str] = {
    "verify_plan": "verify_plan",
    "architect": "design",
    "mechanical": "design",
    "electronics": "design",
    "firmware": "design",
    "redteam": "redteam",
    "testplan": "testplan",
    "simprep": "simulation",
    "integration": "integration",
    "simulation": "simulation",
    "final": "final",
    "report": "report",
}
STAGE_MODEL_ENV: dict[str, str] = {
    "verify_plan": VERIFY_MODEL_ENV,
    "architect": DESIGN_MODEL_ENV,
    "mechanical": DESIGN_MODEL_ENV,
    "electronics": DESIGN_MODEL_ENV,
    "firmware": DESIGN_MODEL_ENV,
    "final": VERIFY_MODEL_ENV,
}


@dataclass(frozen=True)
class Agent:
    """One agent's static identity, as it is stamped on events and listed in the registry."""

    name: str
    role: str
    model: str = ""
    family: str = ""
    kind: str = "deterministic"

    def as_log(self) -> dict[str, str]:
        return {
            "name": self.name, "role": self.role, "model": self.model,
            "family": self.family, "kind": self.kind,
        }


def _stage_agent(stage: str) -> Agent:
    env = STAGE_MODEL_ENV.get(stage)
    model, family = _model_for(stage, STAGE_ROLES[stage], env)
    return Agent(
        name=stage, role=STAGE_ROLES[stage], model=model, family=family,
        kind="llm" if model else "deterministic",
    )


def _model_agent(name: str, role: str, env: str) -> Agent:
    model, family = _model_for(name, role, env)
    return Agent(
        name=name, role=role, model=model, family=family,
        kind="llm" if model else "deterministic",
    )


def for_stage(stage: str) -> Agent:
    """The agent identity of a pipeline stage, with the model it is configured with now."""
    if stage not in STAGE_ROLES:
        raise KeyError(f"unknown stage: {stage}")
    return _stage_agent(stage)


def repair_agent() -> Agent:
    return _model_agent("repair", "repair", DESIGN_MODEL_ENV)


def intake_agent() -> Agent:
    return _model_agent("intake", "intake", DESIGN_MODEL_ENV)


def gate_agent() -> Agent:
    return Agent(name="gate", role="gate", kind="script")


def verifier_agent(check: str) -> Agent:
    """The script behind one check: it reads build outputs and writes one Verdict."""
    return Agent(name=check, role="verifier", kind="script")


def verifier_agents(root: Path) -> list[Agent]:
    """One agent per verifier script in <root>/verifiers, in name order."""
    return [
        verifier_agent(path.stem)
        for path in sorted((Path(root) / "verifiers").glob("*.py"))
        if path.stem != "__init__"
    ]


def registry(root: Path) -> list[Agent]:
    """Every agent in the system: stage agents, repair, intake, gate and the verifier scripts."""
    return [
        *(_stage_agent(stage) for stage in STAGE_ROLES),
        repair_agent(),
        intake_agent(),
        gate_agent(),
        *verifier_agents(root),
    ]


# --- live state --------------------------------------------------------------------------------


def _latest_run(root: Path, project: str) -> tuple[str | None, dict[str, str]]:
    """(run status, stage -> status) of the project's newest run, read-only; (None, {}) if none."""
    for path in (
        Path(root) / "projects" / project / ".engenia" / "run.db",
        Path(root) / ".engenia" / "run.db",
    ):
        if not path.is_file():
            continue
        try:
            with closing(
                sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=0.1)
            ) as db:
                db.row_factory = sqlite3.Row
                db.execute("PRAGMA query_only = ON")
                columns = {row[1] for row in db.execute('PRAGMA table_info("run")')}
                order = '"started" DESC, id DESC' if "started" in columns else "id DESC"
                if "project" in columns:
                    row = db.execute(
                        f'SELECT * FROM "run" WHERE project = ? ORDER BY {order} LIMIT 1',
                        (project,),
                    ).fetchone()
                else:
                    row = db.execute(f'SELECT * FROM "run" ORDER BY {order} LIMIT 1').fetchone()
                if row is None:
                    continue
                stages = {
                    r["stage"]: r["status"]
                    for r in db.execute(
                        "SELECT stage, status FROM stage_run WHERE run_id = ?", (row["id"],)
                    )
                }
                return row["status"], stages
        except sqlite3.OperationalError:
            continue  # the producer may be creating tables or holding a lock
    return None, {}


def live_state(root: Path, project: str) -> dict[str, dict[str, Any]]:
    """Live status, current stage/phase and counts for every agent, from the newest run.

    An agent with an open phase (a phase_start with no matching phase_end) is running. A stage
    agent whose stage is still pending or blocked in the newest run is waiting. Everyone else
    is idle. Counts come from the run's events: assistant messages (model calls), tool calls
    and repair rounds.
    """
    root = Path(root)
    run_status, stage_rows = _latest_run(root, project)
    run_id = design_events.latest_run_id(root, project)
    in_log = (
        _agents_in_log(design_events.read_events(root, run_id)[0]) if run_id is not None else {}
    )
    out: dict[str, dict[str, Any]] = {}
    for agent in registry(root):
        state = in_log.get(agent.name, {})
        running = state.get("status") == "running"
        waiting = (
            run_status == "running"
            and agent.name in STAGE_ROLES
            and not running
            and stage_rows.get(agent.name) in ("pending", "blocked")
        )
        out[agent.name] = {
            **agent.as_log(),
            "status": "running" if running else "waiting" if waiting else "idle",
            "stage": agent.name if waiting else state.get("stage"),
            "phase": None if waiting else state.get("phase"),
            "counts": state.get(
                "counts", {"model_calls": 0, "tool_calls": 0, "repairs": 0}
            ),
        }
    return out


def _agents_in_log(found: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per-agent live state derived from one run's event lines."""
    open_phases: dict[str, dict[str, Any]] = {}
    last: dict[str, dict[str, Any]] = {}
    for event in found:
        name = (event.get("agent") or {}).get("name")
        if not name:
            continue
        entry = last.setdefault(
            name, {"status": "idle", "stage": None, "phase": None,
                   "counts": {"model_calls": 0, "tool_calls": 0, "repairs": 0}}
        )
        if event.get("stage") is not None:
            entry["stage"] = event["stage"]
        kind, data = event.get("kind"), event.get("data") or {}
        if kind == "phase_start":
            open_phases[name] = {"stage": event.get("stage"), "phase": data.get("phase")}
        elif kind == "phase_end":
            open_phases.pop(name, None)
        elif kind == "message" and data.get("role") == "assistant":
            entry["counts"]["model_calls"] += 1
        elif kind == "tool_call":
            entry["counts"]["tool_calls"] += 1
        elif kind == "repair" and data.get("phase") == "round":
            entry["counts"]["repairs"] += 1
    for name, entry in last.items():
        if name in open_phases:
            entry["status"] = "running"
            entry["stage"] = open_phases[name]["stage"]
            entry["phase"] = open_phases[name]["phase"]
    return last
