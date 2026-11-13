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


class EventsResponse(ResponseModel):
    changed: list[Area]


class InterfaceFile(ResponseModel):
    """Example downloaded interface.json; downloads themselves are arbitrary bytes."""

    stage: str
    key: str
    facts: dict[str, Any]
