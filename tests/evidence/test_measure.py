"""`engenia measure`: measurement files, calibration, measured verdicts, change proposals.

Nothing here touches projects/harbour-listener. The scratch root from conftest holds a copy
of the real verifiers/ and a fixture spec; the tests add requirements whose verify.method is
"test", the printed-prototype requirements this command judges.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from engenia.core.cli import app
from engenia.evidence.measure import (
    CALIBRATION_FILE,
    MEASUREMENTS_FILE,
    MeasureError,
    measure,
)
from engenia.evidence.report import build_matrix

runner = CliRunner()

PROJECT = "demo"
DATE = "2026-11-13"


def predicted_waterline_mm(
    mass_g: float, diameter_mm: float = 160.0, rho: float = 1.005
) -> float:
    """The documented prediction: displaced volume over waterplane area, in millimetres."""
    return mass_g / rho / (math.pi * (diameter_mm / 20.0) ** 2) * 10.0


def write_requirement(
    root: Path,
    number: int,
    check: str,
    limit: dict[str, float] | None,
    *,
    method: str = "test",
    status: str = "approved",
    affects: tuple[str, ...] = ("mechanical",),
    value: float = 1,
    unit: str = "x",
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": f"R-{number:03d}",
        "statement": f"The printed prototype shall satisfy {check}.",
        "value": value,
        "unit": unit,
        "verify": {"method": method, "check": check, "limit": limit},
        "affects": list(affects),
        "status": status,
    }
    path = root / "projects" / PROJECT / "spec" / f"R-{number:03d}.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return data


def write_checks(root: Path, entries: list[dict[str, Any]]) -> Path:
    path = root / "projects" / PROJECT / "checks" / "checks.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(entries), encoding="utf-8")
    return path


def run_measure(scratch_root: Path, **overrides: Any):
    kwargs: dict[str, Any] = {
        "mass_g": 352.1,
        "waterline_mm": 48.2,
        "tilt_deg": 6.5,
        "date_text": DATE,
    }
    kwargs.update(overrides)
    return measure(PROJECT, scratch_root, **kwargs)


def read_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture
def predictions(write_interface):
    """Build interfaces that predict mass 340 g (dry 280 + electronics 60) and a draft.

    The default interfaces are written when the fixture is requested; call it again to
    override the facts.
    """

    def _write(
        *,
        total_mass_g: float | None = None,
        dry_mass_g: float = 280.0,
        electronics_mass: float = 60.0,
        bbox_mm: tuple[float, float, float] = (160.0, 160.0, 220.0),
    ) -> None:
        if total_mass_g is not None:
            write_interface("integration", {"total_mass_g": total_mass_g})
        write_interface("mechanical", {"dry_mass_g": dry_mass_g, "bbox_mm": list(bbox_mm)})
        write_interface("electronics", {"mass_g": electronics_mass})

    _write()
    return _write


# --- files --------------------------------------------------------------------------------------


def test_measure_writes_measurements_and_calibration(scratch_root, predictions) -> None:
    write_requirement(scratch_root, 4, "measured_mass", {"max": 400}, value=400, unit="g")
    result = run_measure(scratch_root)

    assert result.measurements_path == scratch_root / "projects" / PROJECT / MEASUREMENTS_FILE
    assert read_yaml(result.measurements_path) == {
        "mass_g": 352.1,
        "waterline_mm": 48.2,
        "date": DATE,
        "tilt_deg": 6.5,
    }
    assert result.calibration_path == scratch_root / "projects" / PROJECT / CALIBRATION_FILE
    calibration = read_yaml(result.calibration_path)
    assert list(calibration) == ["mass_factor", "waterline_offset_mm", "source"]
    assert calibration["mass_factor"] == pytest.approx(352.1 / 340.0, abs=1e-6)
    expected_waterline = predicted_waterline_mm(340.0)
    assert result.predictions.waterline_mm == pytest.approx(expected_waterline)
    assert calibration["waterline_offset_mm"] == pytest.approx(48.2 - expected_waterline, abs=1e-6)
    assert calibration["source"] == f"measured {DATE}"


def test_measure_without_tilt_omits_the_key(scratch_root, predictions) -> None:
    write_requirement(scratch_root, 4, "measured_mass", {"max": 400})
    result = run_measure(scratch_root, tilt_deg=None)
    assert "tilt_deg" not in read_yaml(result.measurements_path)
    assert result.exit_code == 0


def test_total_mass_fact_wins_over_the_stage_sum(scratch_root, predictions) -> None:
    predictions(total_mass_g=345.5)
    result = run_measure(scratch_root)
    assert result.predictions.mass_g == 345.5
    assert result.calibration.mass_factor == pytest.approx(352.1 / 345.5, abs=1e-6)


# --- measured verdicts --------------------------------------------------------------------------


def test_passing_requirement_gets_a_measured_verdict(scratch_root, predictions) -> None:
    write_requirement(scratch_root, 4, "measured_mass", {"max": 400}, value=400, unit="g")
    write_requirement(scratch_root, 5, "measured_waterline", {"max": 60}, value=60, unit="mm")
    result = run_measure(scratch_root)

    assert [(v.requirement, v.check) for v in result.verdicts] == [
        ("R-004", "measured_mass"),
        ("R-005", "measured_waterline"),
    ]
    verdict = result.verdicts[0]
    assert verdict.result.value == "pass"
    assert verdict.measured == 352.1
    assert verdict.evidence_level.value == "measured"
    assert verdict.limit.model_dump(exclude_none=True) == {"max": 400.0}
    assert result.exit_code == 0


def test_failing_requirement_gets_a_change_proposal(scratch_root, predictions) -> None:
    write_requirement(scratch_root, 4, "measured_mass", {"max": 400}, value=400, unit="g")
    result = run_measure(scratch_root, mass_g=430.0)

    verdict = result.verdicts[0]
    assert verdict.result.value == "fail"
    assert verdict.measured == 430.0
    assert verdict.evidence_level.value == "measured"
    assert result.exit_code == 1
    proposal = result.proposals["R-004"]
    assert proposal == scratch_root / "projects" / PROJECT / "spec" / "proposals" / "R-004.yaml"
    data = read_yaml(proposal)
    assert data["status"] == "draft"
    assert data["verify"]["limit"] == {"max": 452}  # the measured mass plus a 5 percent margin
    assert data["value"] == 452  # the value stated the old bound
    text = proposal.read_text(encoding="utf-8")
    assert "measured_mass (fail): measured 430.0" in text


def test_tilt_requirement_without_a_tilt_measurement_is_an_error(scratch_root, predictions) -> None:
    write_requirement(scratch_root, 6, "measured_tilt", {"max": 30}, affects=("simulation",))
    result = run_measure(scratch_root, tilt_deg=None)

    verdict = result.verdicts[0]
    assert verdict.result.value == "error"
    assert "no 'tilt_deg' measurement" in (verdict.message or "")
    assert verdict.evidence_level.value == "calculated"  # an error compares no measurement
    assert result.proposals == {} and result.proposal_errors == {}
    assert result.exit_code == 1


def test_check_that_is_not_a_measured_check_is_skipped(scratch_root, predictions) -> None:
    write_requirement(scratch_root, 4, "bucket_leak", {"max": 1})
    result = run_measure(scratch_root)

    assert result.verdicts == []
    assert result.skipped == {"R-004": "bucket_leak is not a measured check"}
    assert result.exit_code == 0


def test_draft_requirement_is_not_checked(scratch_root, predictions) -> None:
    write_requirement(scratch_root, 4, "measured_mass", {"max": 400}, status="draft")
    result = run_measure(scratch_root)
    assert result.verdicts == []
    assert result.exit_code == 0


def test_unproposable_failure_is_reported_not_raised(scratch_root, predictions) -> None:
    """A test requirement without a limit gives a fail verdict and a proposal error."""
    write_requirement(scratch_root, 4, "measured_mass", None)
    result = run_measure(scratch_root, mass_g=430.0)

    assert result.verdicts[0].result.value == "fail"
    assert "no failed verdict with a measured number" in result.proposal_errors["R-004"]
    assert not (scratch_root / "projects" / PROJECT / "spec" / "proposals").exists()
    assert result.exit_code == 1


# --- a measurement reaches the trace matrix -----------------------------------------------------


def test_measured_verdict_reaches_the_trace_matrix(scratch_root, predictions) -> None:
    write_requirement(scratch_root, 4, "measured_mass", {"max": 400}, value=400, unit="g")
    write_checks(
        scratch_root,
        [{"id": "C-004", "requirement": "R-004", "check": "measured_mass",
          "params": {}, "limit": {"max": 400.0}}],
    )
    run_measure(scratch_root)

    payload = build_matrix(PROJECT, scratch_root)
    row = next(row for row in payload["rows"] if row["id"] == "R-004")
    assert row["result"] == "pass"
    assert row["measured"] == 352.1
    assert row["evidence_level"] == "measured"
    assert row["check"] == "measured_mass"


# --- errors -------------------------------------------------------------------------------------


def test_unknown_project_raises_and_writes_nothing(scratch_root) -> None:
    with pytest.raises(MeasureError, match="unknown project: nope"):
        measure("nope", scratch_root, mass_g=1, waterline_mm=1, date_text=DATE)
    assert not (scratch_root / "projects" / "nope").exists()


def test_without_predictions_nothing_is_written(scratch_root) -> None:
    write_requirement(scratch_root, 4, "measured_mass", {"max": 400})
    with pytest.raises(MeasureError, match="no predicted mass"):
        run_measure(scratch_root)
    assert not (scratch_root / "projects" / PROJECT / MEASUREMENTS_FILE).exists()
    assert not (scratch_root / "projects" / PROJECT / CALIBRATION_FILE).exists()


def test_without_a_hull_bbox_no_waterline_is_predicted(scratch_root, write_interface) -> None:
    write_interface("mechanical", {"dry_mass_g": 280.0})
    write_interface("electronics", {"mass_g": 60.0})
    with pytest.raises(MeasureError, match="no predicted waterline"):
        run_measure(scratch_root)
    assert not (scratch_root / "projects" / PROJECT / MEASUREMENTS_FILE).exists()


@pytest.mark.parametrize(
    ("mass", "waterline", "tilt"),
    [(0.0, 48.2, 6.5), (-1.0, 48.2, 6.5), (352.1, 0.0, 6.5), (352.1, -3.0, 6.5),
     (352.1, 48.2, -0.5)],
)
def test_unusable_numbers_write_nothing(
    scratch_root, predictions, mass, waterline, tilt
) -> None:
    with pytest.raises(MeasureError, match="must be a"):
        run_measure(scratch_root, mass_g=mass, waterline_mm=waterline, tilt_deg=tilt)
    assert not (scratch_root / "projects" / PROJECT / MEASUREMENTS_FILE).exists()


# --- the command --------------------------------------------------------------------------------


def test_cli_prints_predictions_calibration_and_verdicts(scratch_root, predictions,
                                                         monkeypatch) -> None:
    monkeypatch.setenv("ENGENIA_ROOT", str(scratch_root))
    write_requirement(scratch_root, 4, "measured_mass", {"max": 400}, value=400, unit="g")
    result = runner.invoke(
        app,
        ["measure", PROJECT, "--mass-g", "352.1", "--waterline-mm", "48.2",
         "--tilt-deg", "6.5", "--date", DATE],
    )

    assert result.exit_code == 0, result.output
    assert "measurements -> projects/demo/measurements.yaml" in result.output
    assert "predicted mass 340 g, measured 352.1 g, factor 1.03559" in result.output
    assert f"predicted waterline {predicted_waterline_mm(340.0):.6g} mm" in result.output
    assert "calibration -> projects/demo/calibration.yaml (measured 2026-11-13)" in result.output
    assert "R-004 measured_mass: pass (measured 352.1" in result.output
    assert "evidence measured" in result.output
    assert "measure demo: pass" in result.output


def test_cli_failing_requirement_exits_one_with_a_proposal(scratch_root, predictions,
                                                           monkeypatch) -> None:
    monkeypatch.setenv("ENGENIA_ROOT", str(scratch_root))
    write_requirement(scratch_root, 4, "measured_mass", {"max": 400}, value=400, unit="g")
    result = runner.invoke(
        app,
        ["measure", PROJECT, "--mass-g", "430", "--waterline-mm", "48.2", "--date", DATE],
    )

    assert result.exit_code == 1
    assert "R-004 measured_mass: fail (measured 430.0" in result.output
    assert "proposal R-004 -> projects/demo/spec/proposals/R-004.yaml" in result.output
    assert "measure demo: fail" in result.output


def test_cli_without_a_tilt_measurement_reports_the_error(scratch_root, predictions,
                                                          monkeypatch) -> None:
    monkeypatch.setenv("ENGENIA_ROOT", str(scratch_root))
    write_requirement(scratch_root, 6, "measured_tilt", {"max": 30}, affects=("simulation",))
    result = runner.invoke(
        app, ["measure", PROJECT, "--mass-g", "352.1", "--waterline-mm", "48.2", "--date", DATE]
    )

    assert result.exit_code == 1
    assert "R-006 measured_tilt: error" in result.output
    assert "proposal ->" not in result.output


def test_cli_unknown_project(scratch_root, monkeypatch) -> None:
    monkeypatch.setenv("ENGENIA_ROOT", str(scratch_root))
    result = runner.invoke(app, ["measure", "nope", "--mass-g", "1", "--waterline-mm", "1"])
    assert result.exit_code == 1
    assert "error: unknown project: nope" in result.output


def test_cli_missing_predictions(scratch_root, monkeypatch) -> None:
    monkeypatch.setenv("ENGENIA_ROOT", str(scratch_root))
    result = runner.invoke(
        app, ["measure", PROJECT, "--mass-g", "352.1", "--waterline-mm", "48.2"]
    )
    assert result.exit_code == 1
    assert "error: no predicted mass" in result.output


def test_cli_defaults_the_date_to_today(scratch_root, predictions, monkeypatch) -> None:
    """Without --date the source line carries today's date; no clock is read in the library."""
    from datetime import date as date_type

    monkeypatch.setenv("ENGENIA_ROOT", str(scratch_root))
    write_requirement(scratch_root, 4, "measured_mass", {"max": 400})
    result = runner.invoke(
        app, ["measure", PROJECT, "--mass-g", "352.1", "--waterline-mm", "48.2"]
    )

    assert result.exit_code == 0, result.output
    calibration = read_yaml(scratch_root / "projects" / PROJECT / CALIBRATION_FILE)
    assert calibration["source"] == f"measured {date_type.today().isoformat()}"


def write_plan(root: Path, tests: list[dict[str, Any]]) -> None:
    import json

    path = root / "projects" / PROJECT / "build" / "testplan" / "physical-tests.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"project": PROJECT, "tests": tests, "not_covered": []}))


def test_measure_reads_the_test_ids_of_the_physical_test_plan(scratch_root, predictions) -> None:
    write_plan(scratch_root, [
        {"id": "T-001", "requirement": "R-001", "quantity": "mass", "limit": {"max": 300},
         "measurement_key": "mass_g"},
        {"id": "T-006", "requirement": "R-006", "quantity": "waterline", "limit": {"max": 60},
         "measurement_key": "waterline_mm"},
        {"id": "T-007", "requirement": "R-007", "quantity": "tilt", "limit": {"max": 10},
         "measurement_key": "tilt_deg"},
        {"id": "T-004", "requirement": "R-004", "quantity": "buoyancy", "limit": {"min": 0.2},
         "measurement_key": None},
    ])
    result = run_measure(scratch_root, tilt_deg=None)
    assert [(t["id"], t["result"]) for t in result.planned] == [
        ("T-001", "fail"), ("T-006", "pass"),
    ]
    assert result.plan_notes == []
    assert result.exit_code == 0  # informational: verdicts decide the exit code


def test_measure_without_a_plan_says_so(scratch_root, predictions) -> None:
    result = run_measure(scratch_root)
    assert result.planned == []
    assert "testplan" in " ".join(result.plan_notes)


def test_cli_prints_planned_test_ids(scratch_root, predictions, monkeypatch) -> None:
    write_plan(scratch_root, [
        {"id": "T-001", "requirement": "R-001", "quantity": "mass", "limit": {"max": 400},
         "measurement_key": "mass_g"},
    ])
    monkeypatch.setenv("ENGENIA_ROOT", str(scratch_root))
    result = runner.invoke(
        app, ["measure", PROJECT, "--mass-g", "352.1", "--waterline-mm", "48.2"]
    )
    assert "T-001 (R-001) mass: pass" in result.output
