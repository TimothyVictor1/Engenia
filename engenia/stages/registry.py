"""Maps stage name to a Stage instance. Registration order is the section 2 diagram order."""

from __future__ import annotations

from engenia.agents.architect.stage import ArchitectStage
from engenia.agents.electronics.stage import ElectronicsStage
from engenia.agents.firmware.stage import FirmwareStage
from engenia.agents.mechanical.stage import MechanicalStage
from engenia.agents.redteam.stage import RedteamStage
from engenia.agents.testplan.stage import TestplanStage
from engenia.agents.verification.stage import VerifyPlanStage
from engenia.evidence.report import ReportStage
from engenia.gate.final import FinalStage
from engenia.sim.simprep import SimprepStage
from engenia.sim.simulation import SimulationStage
from engenia.stages.base import Stage
from engenia.stages.integration.stage import IntegrationStage

_STAGES: list[Stage] = [
    VerifyPlanStage(),
    ArchitectStage(),
    MechanicalStage(),
    ElectronicsStage(),
    FirmwareStage(),
    RedteamStage(),
    TestplanStage(),
    SimprepStage(),
    IntegrationStage(),
    SimulationStage(),
    FinalStage(),
    ReportStage(),
]

REGISTRY: dict[str, Stage] = {stage.name: stage for stage in _STAGES}


def get_stage(name: str) -> Stage:
    try:
        return REGISTRY[name]
    except KeyError:
        raise KeyError(f"unknown stage: {name}") from None


def all_stages() -> list[Stage]:
    return list(REGISTRY.values())
