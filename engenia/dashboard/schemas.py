"""JSON wire shapes. Stored artifacts are validated without rewriting their fields."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator

from engenia.agents.redteam.stage import Scenario
from engenia.contracts.models import ChangeRequest, Verdict

StageStatus = Literal["done", "failed", "not_run", "stub", "running", "blocked"]
Area = Literal["pipeline", "requirements", "contract", "scenarios", "design"]


class ResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PipelineStage(ResponseModel):
    name: str
    needs: list[str]
    status: StageStatus
    key: str | None


class PipelineResponse(ResponseModel):
    project: str
    updated: datetime
    stages: list[PipelineStage]


class RequirementLimit(ResponseModel):
    min: float | None
    max: float | None
    equals: float | None


class RequirementVerdict(ResponseModel):
    check: str
    measured: float | None
    result: Literal["pass", "fail", "error"]
    evidence_level: Literal["calculated", "simulated", "measured"]
    message: str | None
    stage: str


class RequirementItem(ResponseModel):
    id: str
    statement: str
    unit: str | None
    limit: RequirementLimit
    affects: list[str]
    method: str
    status: Literal["pass", "fail", "error", "unverified"]
    verdict: RequirementVerdict | None


class RequirementsResponse(ResponseModel):
    requirements: list[RequirementItem]


class StageResponse(ResponseModel):
    name: str
    status: StageStatus
    facts: dict[str, Any]
    verdicts: list[dict[str, Any]]

    @field_validator("verdicts")
    @classmethod
    def stored_verdicts(cls, value):
        for item in value:
            Verdict.model_validate(item)
        return value


class ContractResponse(ResponseModel):
    source: Literal["project", "default", "none"]
    contract: dict[str, Any] | None
    facts: dict[str, dict[str, Any]]
    change_requests: list[dict[str, Any]]

    @field_validator("change_requests")
    @classmethod
    def stored_requests(cls, value):
        for item in value:
            ChangeRequest.model_validate(item)
        return value


class ScenariosResponse(ResponseModel):
    scenarios: list[dict[str, Any]]

    @field_validator("scenarios")
    @classmethod
    def stored_scenarios(cls, value):
        for item in value:
            Scenario.model_validate(item)
        return value


class FileEntry(ResponseModel):
    path: str
    name: str
    size: int
    type: Literal["text", "json", "csv", "stl", "step", "image", "archive", "pdf", "binary"]
    modified: str


class FilesResponse(ResponseModel):
    stage: str
    files: list[FileEntry]


class ReleaseResponse(ResponseModel):
    available: bool
    name: str | None = None
    size: int | None = None
    modified: str | None = None


class EventsResponse(ResponseModel):
    changed: list[Area]


class AgentInfo(ResponseModel):
    """One agent's identity plus its live state (see engenia.core.agents.live_state)."""

    name: str
    role: str
    model: str
    family: str
    kind: Literal["llm", "deterministic", "script"]
    status: Literal["idle", "running", "waiting"]
    stage: str | None
    phase: str | None
    counts: dict[str, int]


class AgentsResponse(ResponseModel):
    project: str
    agents: list[AgentInfo]


class PhaseStat(ResponseModel):
    count: int
    duration_ms: int
    ok: bool


class StageSummary(ResponseModel):
    phases: dict[str, PhaseStat]
    total_ms: int


class ParallelPoint(ResponseModel):
    t_ms: int
    stages: int
    agents: int


class RunTotals(ResponseModel):
    duration_ms: int
    model_calls: int
    tool_calls: int
    repairs: int
    cache_hits: int
    cache_misses: int
    max_parallel_stages: int
    max_parallel_agents: int


class RunSummaryResponse(ResponseModel):
    run_id: int | None
    stages: dict[str, StageSummary]
    parallelism: list[ParallelPoint]
    totals: RunTotals


class InterfaceFile(ResponseModel):
    """Example downloaded interface.json; downloads themselves are arbitrary bytes."""

    stage: str
    key: str
    facts: dict[str, Any]
