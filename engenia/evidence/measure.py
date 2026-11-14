"""`engenia measure`: record the printed prototype's measurements and calibrate.

The command takes the numbers a person measured on the printed prototype, saves them to
projects/<p>/measurements.yaml, compares them with the design's predictions read from the
built interfaces, and writes projects/<p>/calibration.yaml:

    mass_factor          measured mass / predicted mass
    waterline_offset_mm  measured waterline - predicted waterline
    source               "measured <date>"

The predicted mass is the design's total mass: the integration interface's ``total_mass_g``
when it is published, otherwise mechanical ``dry_mass_g`` plus electronics ``mass_g``. The
predicted waterline is the design's draft, from the same level-1 hydrostatics the waves
solver uses: displaced volume (mass over water density) divided by the waterplane area of
the hull, whose diameter comes from the mechanical ``bbox_mm``. Water density is the
verifiers' default, 1.005 g/cm3.

The command then runs the measured verifiers (verifiers/measured_*.py) for every approved
requirement whose verify.method is "test" and whose check is a measured check. Those
verdicts carry the evidence level "measured". A failed verdict becomes a draft change
proposal through engenia.agents.intake.propose_change, written to projects/<p>/spec/
proposals/. A measurement file that cannot be compared is never half-written: the
predictions are computed before anything is recorded.

Nothing here calls a model, the network or random numbers, and no clock is read: the caller
supplies the date.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from engenia.contracts.models import (
    CheckContext,
    Limit,
    Requirement,
    RequirementStatus,
    StageContext,
    Verdict,
    VerdictResult,
    VerifyMethod,
)
from engenia.core.errors import CoreError
from engenia.core.spec import load_spec

MEASUREMENTS_FILE = "measurements.yaml"
CALIBRATION_FILE = "calibration.yaml"
WATER_DENSITY_G_CM3 = 1.005  # the verifiers' default surface-water density
DECIMALS = 6  # published digits, like the interface facts

# The measured checks and the measurement key each one reads.
MEASURED_CHECKS: dict[str, str] = {
    "measured_mass": "mass_g",
    "measured_waterline": "waterline_mm",
    "measured_tilt": "tilt_deg",
}


class MeasureError(CoreError):
    """The measurement cannot be recorded or compared; nothing was written."""


@dataclass(frozen=True)
class Predictions:
    mass_g: float
    waterline_mm: float


@dataclass(frozen=True)
class Calibration:
    mass_factor: float
    waterline_offset_mm: float
    source: str


@dataclass(frozen=True)
class MeasureResult:
    measurements: dict[str, Any]
    predictions: Predictions
    calibration: Calibration
    measurements_path: Path
    calibration_path: Path
    verdicts: list[Verdict] = field(default_factory=list)
    proposals: dict[str, Path] = field(default_factory=dict)  # requirement id -> proposal
    proposal_errors: dict[str, str] = field(default_factory=dict)  # requirement id -> reason
    skipped: dict[str, str] = field(default_factory=dict)  # requirement id -> why not checked

    @property
    def exit_code(self) -> int:
        if self.proposal_errors:
            return 1
        if any(verdict.result is not VerdictResult.PASS for verdict in self.verdicts):
            return 1
        return 0


# --- predictions from the built interfaces ------------------------------------------------------


def _facts(root: Path, project: str, stage: str) -> dict[str, Any] | None:
    """The facts of ``build/<stage>/interface.json``, or None when it is absent or unusable."""
    path = root / "projects" / project / "build" / stage / "interface.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    facts = data.get("facts") if isinstance(data, dict) else None
    return facts if isinstance(facts, dict) else None


def _number(facts: dict[str, Any], key: str, source: str) -> float:
    value = facts.get(key)
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise MeasureError(f"{source} fact '{key}' is not a usable number: {value!r}")
    return float(value)


def _predicted_mass_g(root: Path, project: str) -> float:
    for stage in ("integration", "mechanical", "electronics"):
        facts = _facts(root, project, stage)
        if facts is not None and "total_mass_g" in facts:
            return _number(facts, "total_mass_g", stage)
    mechanical, electronics = _facts(root, project, "mechanical"), _facts(
        root, project, "electronics"
    )
    if (
        mechanical is None
        or electronics is None
        or "dry_mass_g" not in mechanical
        or "mass_g" not in electronics
    ):
        raise MeasureError(
            "no predicted mass: run `engenia run <p>` first (needs the integration interface,"
            " or mechanical dry_mass_g plus electronics mass_g)"
        )
    return _number(mechanical, "dry_mass_g", "mechanical") + _number(
        electronics, "mass_g", "electronics"
    )


def _predicted_waterline_mm(root: Path, project: str, mass_g: float) -> float:
    mechanical = _facts(root, project, "mechanical")
    if mechanical is None or "bbox_mm" not in mechanical:
        raise MeasureError(
            "no predicted waterline: the mechanical interface has no bbox_mm;"
            " run `engenia run <p>` first"
        )
    bbox = mechanical["bbox_mm"]
    if (
        not isinstance(bbox, list)
        or len(bbox) != 3
        or any(
            isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in bbox
        )
    ):
        raise MeasureError(f"mechanical fact 'bbox_mm' is not three numbers: {bbox!r}")
    diameter_mm = max(float(bbox[0]), float(bbox[1]))
    if diameter_mm <= 0:
        raise MeasureError(f"mechanical bbox_mm has no usable diameter: {bbox!r}")
    area_cm2 = math.pi * (diameter_mm / 20.0) ** 2  # mm to cm, radius = diameter / 2
    displaced_cm3 = mass_g / WATER_DENSITY_G_CM3
    return displaced_cm3 / area_cm2 * 10.0  # cm to mm


# --- the measured requirements ------------------------------------------------------------------


def _test_requirements(root: Path, project: str) -> list[Requirement]:
    """The approved requirements verified by a physical test, in spec order."""
    spec_files = load_spec(root / "projects" / project / "spec")
    return [
        spec_file.requirement
        for spec_file in spec_files
        if spec_file.requirement.status is RequirementStatus.APPROVED
        and spec_file.requirement.verify.method is VerifyMethod.TEST
    ]


def _stage_ctx(project: str, requirement: Requirement, root: Path) -> StageContext:
    """The context for a requirement's check: its last affected stage, as the gate resolves."""
    return StageContext(project=project, stage=requirement.affects[-1], root=root)


def _propose(project: str, requirement: Requirement, result: MeasureResult, root: Path) -> None:
    """Write a change proposal for a requirement the measurement broke."""
    from engenia.agents.intake import propose_change

    failed = [
        verdict
        for verdict in result.verdicts
        if verdict.requirement == requirement.id
        and verdict.result is VerdictResult.FAIL
        and verdict.measured is not None
    ]
    try:
        result.proposals[requirement.id] = propose_change(
            requirement, failed, ctx=_stage_ctx(project, requirement, root)
        )
    except ValueError as exc:
        result.proposal_errors[requirement.id] = str(exc)


def _check_requirements(project: str, root: Path, result: MeasureResult) -> None:
    """Run the measured check of every test requirement, and propose changes for failures."""
    from engenia.harness.checks import _load_verifier, error_verdict

    for requirement in _test_requirements(root, project):
        check = requirement.verify.check
        if check not in MEASURED_CHECKS:
            result.skipped[requirement.id] = f"{check} is not a measured check"
            continue
        ctx = CheckContext(
            project=project,
            stage=requirement.affects[-1],  # type: ignore[arg-type]
            root=root,
            check=check,
            requirement=requirement.id,
            params={},
            limit=requirement.verify.limit or Limit(),
        )
        try:
            run = _load_verifier(root, check)
            verdict = run(ctx)
        except Exception as exc:  # a crashing verifier is an error verdict, not a pass
            verdict = error_verdict(check, f"verifier raised {type(exc).__name__}: {exc}",
                                    requirement.id)
        if not isinstance(verdict, Verdict):
            verdict = error_verdict(
                check, f"verifier returned {type(verdict).__name__}, not Verdict",
                requirement.id,
            )
        result.verdicts.append(verdict)
        if verdict.result is VerdictResult.FAIL:
            _propose(project, requirement, result, root)


# --- the command --------------------------------------------------------------------------------


def _check_numbers(mass_g: float, waterline_mm: float, tilt_deg: float | None) -> None:
    for name, value in (("--mass-g", mass_g), ("--waterline-mm", waterline_mm)):
        if not math.isfinite(value) or value <= 0:
            raise MeasureError(f"{name} must be a positive number, got {value}")
    if tilt_deg is not None and (not math.isfinite(tilt_deg) or tilt_deg < 0):
        raise MeasureError(f"--tilt-deg must be a non-negative number, got {tilt_deg}")


def measure(
    project: str,
    root: Path,
    *,
    mass_g: float,
    waterline_mm: float,
    tilt_deg: float | None = None,
    date_text: str,
) -> MeasureResult:
    """Record the measurements, write the calibration, then check and propose.

    Raises :class:`MeasureError` when the project is unknown, a number is unusable, or the
    predictions cannot be computed. A measurement that breaks a requirement still writes
    everything: the verdicts and the proposals carry the failure.
    """
    root = Path(root)
    project_dir = root / "projects" / project
    if not project_dir.is_dir():
        raise MeasureError(f"unknown project: {project}")
    _check_numbers(mass_g, waterline_mm, tilt_deg)

    predicted_mass_g = _predicted_mass_g(root, project)
    predicted_waterline_mm = _predicted_waterline_mm(root, project, predicted_mass_g)

    measurements: dict[str, Any] = {
        "mass_g": mass_g,
        "waterline_mm": waterline_mm,
        "date": date_text,
    }
    if tilt_deg is not None:
        measurements["tilt_deg"] = tilt_deg
    measurements_path = project_dir / MEASUREMENTS_FILE
    measurements_path.write_text(
        yaml.safe_dump(measurements, sort_keys=False), encoding="utf-8"
    )

    calibration = Calibration(
        mass_factor=round(mass_g / predicted_mass_g, DECIMALS),
        waterline_offset_mm=round(waterline_mm - predicted_waterline_mm, DECIMALS),
        source=f"measured {date_text}",
    )
    calibration_path = project_dir / CALIBRATION_FILE
    calibration_path.write_text(
        yaml.safe_dump(
            {
                "mass_factor": calibration.mass_factor,
                "waterline_offset_mm": calibration.waterline_offset_mm,
                "source": calibration.source,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    result = MeasureResult(
        measurements=measurements,
        predictions=Predictions(mass_g=predicted_mass_g, waterline_mm=predicted_waterline_mm),
        calibration=calibration,
        measurements_path=measurements_path,
        calibration_path=calibration_path,
    )
    _check_requirements(project, root, result)
    return result
