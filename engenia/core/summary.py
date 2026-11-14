"""A run's timing picture, computed from its event log: per-stage phase durations, how much ran
at the same time, and totals.

Everything here reads .engenia/events/<run_id>.jsonl only. Phase durations come from
phase_start / phase_end pairs: the log's duration_ms is kept, and the wall time between the
pair's timestamps is used when it is longer (clock skew between two events should not shrink a
phase). Parallelism is sampled at every phase boundary: after each start or end, how many
stages and how many distinct agents had a phase open. Cache hits/misses and the loop counters
(model calls, tool calls, repairs) are counted from their own events.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from engenia.core import events


def _ts(event: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(str(event["ts"]))


def _agent_name(event: dict[str, Any]) -> str | None:
    return (event.get("agent") or {}).get("name")


def run_summary(root: Path, run_id: int) -> dict[str, Any]:
    """The summary of one run's log. A run with no events gives empty structures."""
    found, _ = events.read_events(Path(root), run_id)
    stages: dict[str, dict[str, Any]] = {}
    open_phases: dict[tuple[str, str], list[dict[str, Any]]] = {}
    boundaries: list[tuple[datetime, str, str | None, bool]] = []  # ts, stage, agent, opening?
    totals: dict[str, Any] = {
        "duration_ms": 0, "model_calls": 0, "tool_calls": 0, "repairs": 0,
        "cache_hits": 0, "cache_misses": 0, "max_parallel_stages": 0, "max_parallel_agents": 0,
    }
    for event in found:
        kind, data = event.get("kind"), event.get("data") or {}
        stage = event.get("stage")
        if kind == "phase_start" and stage:
            boundaries.append((_ts(event), stage, _agent_name(event), True))
            open_phases.setdefault((stage, str(data.get("phase") or "")), []).append(event)
        elif kind == "phase_end" and stage:
            boundaries.append((_ts(event), stage, _agent_name(event), False))
            _close_phase(stages, open_phases, event)
        elif kind == "cache":
            totals["cache_hits" if data.get("hit") else "cache_misses"] += 1
        elif kind == "message" and data.get("role") == "assistant":
            totals["model_calls"] += 1
        elif kind == "tool_call":
            totals["tool_calls"] += 1
        elif kind == "repair" and data.get("phase") == "round":
            totals["repairs"] += 1
    series = _sample(boundaries, totals)
    return {"run_id": run_id, "stages": stages, "parallelism": series, "totals": totals}


def _close_phase(
    stages: dict[str, dict[str, Any]],
    open_phases: dict[tuple[str, str], list[dict[str, Any]]],
    event: dict[str, Any],
) -> None:
    stage, name = str(event["stage"]), str((event.get("data") or {}).get("phase") or "")
    pending = open_phases.get((stage, name)) or []
    start = pending.pop(0) if pending else None
    entry = stages.setdefault(stage, {"phases": {}, "total_ms": 0})
    stats = entry["phases"].setdefault(name, {"count": 0, "duration_ms": 0, "ok": True})
    duration = int((event.get("data") or {}).get("duration_ms") or 0)
    if start is not None:
        wall = int((_ts(event) - _ts(start)).total_seconds() * 1000)
        duration = max(duration, wall)
    stats["count"] += 1
    stats["duration_ms"] += duration
    entry["total_ms"] += duration
    if (event.get("data") or {}).get("ok") is False:
        stats["ok"] = False


def _sample(
    boundaries: list[tuple[datetime, str, str | None, bool]],
    totals: dict[str, Any],
) -> list[dict[str, int]]:
    """One point per boundary: the counts of stages and agents with an open phase after it."""
    if not boundaries:
        return []
    boundaries = sorted(boundaries, key=lambda item: item[0])
    origin = boundaries[0][0]
    open_stages: set[str] = set()
    open_agents: set[str] = set()
    series: list[dict[str, int]] = []
    for moment, stage, agent, opening in boundaries:
        (open_stages.add if opening else open_stages.discard)(stage)
        if agent is not None:
            (open_agents.add if opening else open_agents.discard)(agent)
        totals["max_parallel_stages"] = max(totals["max_parallel_stages"], len(open_stages))
        totals["max_parallel_agents"] = max(totals["max_parallel_agents"], len(open_agents))
        series.append({
            "t_ms": int((moment - origin).total_seconds() * 1000),
            "stages": len(open_stages),
            "agents": len(open_agents),
        })
    totals["duration_ms"] = int((boundaries[-1][0] - origin).total_seconds() * 1000)
    return series
