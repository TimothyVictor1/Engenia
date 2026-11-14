"""Frozen contracts (PROJECT.md section 6).

Only Timothy changes this file, and only on main. Every model rejects unknown fields.
"""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

STAGE_NAMES: tuple[str, ...] = (
    "verify_plan",
    "architect",
    "mechanical",
    "electronics",
    "firmware",
    "redteam",
    "testplan",
    "simprep",
    "integration",
    "simulation",
    "final",
    "report",
)

StageName = Literal[
    "verify_plan",
    "architect",
    "mechanical",
    "electronics",
    "firmware",
    "redteam",
    "testplan",
    "simprep",
    "integration",
    "simulation",
    "final",
    "report",
]

REQUIREMENT_ID_PATTERN = r"^R-\d{3}$"
CHANGE_REQUEST_ID_PATTERN = r"^CR-\d{3}$"
CHECK_ID_PATTERN = r"^C-\d{3}$"
SHA256_PATTERN = r"^sha256:[0-9a-f]{64}$"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VerifyMethod(StrEnum):
    INSPECTION = "inspection"
    ANALYSIS = "analysis"
    SIMULATION = "simulation"
    TEST = "test"


class RequirementStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"


class ChangeRequestStatus(StrEnum):
    OPEN = "open"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class VerdictResult(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"


class EvidenceLevel(StrEnum):
    CALCULATED = "calculated"
    SIMULATED = "simulated"
    MEASURED = "measured"


class Limit(_Model):
    """Any of min, max, equals. Lint rule L4 requires at least one."""

    min: float | None = None
    max: float | None = None
    equals: float | None = None


class VerifySpec(_Model):
    method: VerifyMethod
    check: str  # name of a file in verifiers/
    limit: Limit | None = None


class Requirement(_Model):
    """projects/<p>/spec/R-xxx.yaml. The id matching the file name is checked by the loader."""

    id: str = Field(pattern=REQUIREMENT_ID_PATTERN)
    statement: str
    value: float | None = None
    unit: str | None = None
    verify: VerifySpec
    affects: list[StageName] = Field(min_length=1)
    status: RequirementStatus
    origin: Literal["user", "derived"] = "user"
    reason: str | None = None

    @model_validator(mode="after")
    def _check_rules(self) -> Requirement:
        if not re.search(r"\bshall\b", self.statement):
            raise ValueError('statement must contain "shall"')
        if self.value is None and self.verify.method != VerifyMethod.INSPECTION:
            raise ValueError("value is required unless verify.method is inspection")
        if self.value is not None and not self.unit:
            raise ValueError("unit is required when value is set")
        return self


# --- Contract (projects/<p>/contract/contract.json) -------------------------------------------


class MechanicalBudget(_Model):
    mass_g: float
    outer_envelope_mm: tuple[float, float, float]


class ElectronicsBudget(_Model):
    mass_g: float
    bay_mm: tuple[float, float, float]
    energy_mwh_per_day: float


class FirmwareBudget(_Model):
    flash_kb: float
    ram_kb: float


class Budgets(_Model):
    mechanical: MechanicalBudget
    electronics: ElectronicsBudget
    firmware: FirmwareBudget


class Mounts(_Model):
    board_holes_mm: list[tuple[float, float]]


class Interfaces(_Model):
    pin_map: dict[str, str]
    mounts: Mounts


class Totals(_Model):
    mass_g_max: float


class Contract(_Model):
    version: int
    budgets: Budgets
    interfaces: Interfaces
    totals: Totals


class ChangeRequest(_Model):
    """projects/<p>/contract/change_requests/CR-xxx.json"""

    id: str = Field(pattern=CHANGE_REQUEST_ID_PATTERN)
    from_stage: StageName
    path: str
    requested: Any
    reason: str
    status: ChangeRequestStatus


class Interface(_Model):
    """build/<stage>/interface.json"""

    stage: StageName
    key: str = Field(pattern=SHA256_PATTERN)
    facts: dict[str, Any]


class Verdict(_Model):
    """One entry of build/<stage>/verdict.json (a list).

    `requirement` is None for an internal consistency check that is not tied to one
    requirement, such as an integration check or the gate canary.
    `message` is a human-readable reason, used for fail and error results.
    """

    check: str
    requirement: str | None = Field(default=None, pattern=REQUIREMENT_ID_PATTERN)
    measured: float | None
    limit: Limit
    result: VerdictResult
    evidence_level: EvidenceLevel
    artifact: str | None = Field(default=None, pattern=SHA256_PATTERN)
    message: str | None = None
    margin_pct: float | None = None


class CheckPlanItem(_Model):
    """One entry of projects/<p>/checks/checks.yaml"""

    id: str = Field(pattern=CHECK_ID_PATTERN)
    requirement: str = Field(pattern=REQUIREMENT_ID_PATTERN)
    check: str
    params: dict[str, Any] = Field(default_factory=dict)
    limit: Limit


# --- Contexts passed to stages and verifiers ---------------------------------------------------
# Section 6 names these types but does not list fields. They carry only where things live; every
# path is derived from (root, project, stage) so that all seats resolve paths the same way.


class _ProjectPaths(_Model):
    project: str
    stage: StageName
    root: Path  # repository root

    @property
    def project_dir(self) -> Path:
        return self.root / "projects" / self.project

    @property
    def spec_dir(self) -> Path:
        return self.project_dir / "spec"

    @property
    def checks_path(self) -> Path:
        return self.project_dir / "checks" / "checks.yaml"

    @property
    def contract_path(self) -> Path:
        return self.project_dir / "contract" / "contract.json"

    @property
    def src_dir(self) -> Path:
        return self.project_dir / "src" / self.stage

    @property
    def build_dir(self) -> Path:
        return self.stage_build_dir(self.stage)

    def stage_build_dir(self, stage: str) -> Path:
        return self.project_dir / "build" / stage

    def stage_interface_path(self, stage: str) -> Path:
        return self.stage_build_dir(stage) / "interface.json"


class StageContext(_ProjectPaths):
    """Passed to Stage.generate, Stage.build and Stage.interface."""


class CheckContext(_ProjectPaths):
    """Passed to verifiers/<name>.py run(). `stage` is the stage whose build is being checked."""

    check: str
    requirement: str = Field(pattern=REQUIREMENT_ID_PATTERN)
    params: dict[str, Any] = Field(default_factory=dict)
    limit: Limit
