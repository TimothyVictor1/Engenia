"""The trace matrix: rows, recomputed verdicts, counts, files and the CLI command."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from typer.testing import CliRunner

from engenia.core.cli import app
from engenia.evidence.report import (
    EVIDENCE_LEVELS,
    RESULTS,
    TRACE_JSON,
    TRACE_MD,
    ReportStage,
    _counts,
    build_matrix,
    render_markdown,
    write_matrix,
)

runner = CliRunner()


def row_of(payload: dict, requirement_id: str) -> dict:
    return next(row for row in payload["rows"] if row["id"] == requirement_id)


# --- rows --------------------------------------------------------------------------------------


def test_no_check_plan_gives_unverified_rows(scratch_root: Path) -> None:
    payload = build_matrix("demo", scratch_root)
    assert payload["project"] == "demo"
    assert [row["id"] for row in payload["rows"]] == ["R-001", "R-002", "R-003"]
    for row in payload["rows"]:
        assert row["result"] == "unverified"
        assert row["measured"] is None
        assert row["evidence_level"] is None
        assert row["artifact"] is None
        assert row["message"] is None
    assert row_of(payload, "R-002")["check"] == "wall_thickness_min"
    assert row_of(payload, "R-002")["limit"] == {"min": 3.0}
    assert row_of(payload, "R-002")["stages"] == ["mechanical"]
    assert payload["counts"]["by_result"] == {
        "pass": 0, "fail": 0, "error": 0, "unverified": 3,
    }
    assert payload["counts"]["by_evidence_level"] == {
        "calculated": 0, "simulated": 0, "measured": 0, "none": 3,
    }


def test_plan_without_builds_gives_error_rows_never_pass(
    scratch_root: Path, write_checks
) -> None:
    write_checks()
    payload = build_matrix("demo", scratch_root)
    for row in payload["rows"]:
        assert row["result"] == "error"
        assert row["measured"] is None
        assert row["message"], "an error row must say why"
    assert "no 'wall_min_mm' fact" in row_of(payload, "R-002")["message"]
    assert payload["counts"]["by_result"]["error"] == 3
    assert payload["counts"]["by_result"]["pass"] == 0


def test_missing_verifier_gives_an_unverified_row(
    scratch_root: Path, write_checks
) -> None:
    write_checks()
    (scratch_root / "verifiers" / "wall_thickness_min.py").unlink()
    payload = build_matrix("demo", scratch_root)
    assert row_of(payload, "R-002")["result"] == "unverified"
    assert row_of(payload, "R-001")["result"] == "error"
    assert row_of(payload, "R-003")["result"] == "error"


def test_verdicts_reach_the_rows(
    scratch_root, write_checks, write_interface, write_contract
) -> None:
    write_checks()
    write_contract(electronics_mass=60)
    write_interface("mechanical", {"dry_mass_g": 280.0, "wall_min_mm": 2.0})
    write_interface("electronics", {"mass_g": 50.0})
    write_interface("firmware", {"detection_rate": 0.95, "false_alarms_per_hour": 1.0})

    payload = build_matrix("demo", scratch_root)
    assert row_of(payload, "R-001")["result"] == "pass"
    # requirement-level mass_budget runs at integration: total mass against the requirement limit
    assert row_of(payload, "R-001")["measured"] == 330.0
    assert row_of(payload, "R-001")["limit"] == {"max": 400.0}
    assert row_of(payload, "R-002")["result"] == "fail"
    assert row_of(payload, "R-002")["measured"] == 2.0
    assert row_of(payload, "R-002")["limit"] == {"min": 3.0}
    assert row_of(payload, "R-002")["evidence_level"] == "calculated"
    assert "below min" in row_of(payload, "R-002")["message"]
    assert row_of(payload, "R-003")["result"] == "pass"
    assert row_of(payload, "R-003")["measured"] == 0.95
    assert payload["counts"]["by_result"] == {
        "pass": 2, "fail": 1, "error": 0, "unverified": 0,
    }
    assert payload["counts"]["by_evidence_level"]["calculated"] == 3


def test_malformed_firmware_fact_is_error_not_fail(
    scratch_root, write_checks, write_interface
) -> None:
    write_checks()
    write_interface("firmware", {"detection_rate": 1.5})
    payload = build_matrix("demo", scratch_root)
    row = row_of(payload, "R-003")
    assert row["result"] == "error"
    assert "within [0, 1]" in row["message"]
    assert payload["counts"]["by_result"]["fail"] == 0


def test_counts_bucket_every_result_and_evidence_level() -> None:
    rows = [
        {"result": "pass", "evidence_level": "calculated"},
        {"result": "fail", "evidence_level": "simulated"},
        {"result": "fail", "evidence_level": "measured"},
        {"result": "error", "evidence_level": "calculated"},
        {"result": "unverified", "evidence_level": None},
        {"result": "unexpected", "evidence_level": "unexpected"},
    ]
    counts = _counts(rows)
    assert set(counts["by_result"]) == set(RESULTS)
    assert counts["by_result"] == {
        "pass": 1, "fail": 2, "error": 1, "unverified": 2,
    }
    assert counts["by_evidence_level"] == {
        "calculated": 2, "simulated": 1, "measured": 1, "none": 2,
    }
    assert set(counts["by_evidence_level"]) == set(EVIDENCE_LEVELS)


# --- markdown ----------------------------------------------------------------------------------


def test_markdown_lists_rows_and_counts(scratch_root: Path) -> None:
    payload = build_matrix("demo", scratch_root)
    text = render_markdown(payload)
    assert text.startswith("# Trace matrix: demo")
    assert "unverified" in text and "never a pass" in text
    for row in payload["rows"]:
        assert f"| {row['id']} |" in text
    assert "Counts by result: 0 pass, 0 fail, 0 error, 3 unverified (3 requirements)." in text
    assert "Counts by evidence level: 0 calculated, 0 simulated, 0 measured, 3 none." in text


def test_markdown_escapes_pipes_in_a_statement(scratch_root: Path) -> None:
    (scratch_root / "projects" / "demo" / "spec" / "R-002.yaml").write_text(
        "id: R-002\n"
        "statement: The hull wall shall stay thick | and intact.\n"
        "value: 3\n"
        "unit: mm\n"
        "verify:\n  method: analysis\n  check: wall_thickness_min\n  limit: {min: 3}\n"
        "affects: [mechanical]\n"
        "status: approved\n",
        encoding="utf-8",
    )
    payload = build_matrix("demo", scratch_root)
    row = row_of(payload, "R-002")
    assert row["statement"] == "The hull wall shall stay thick | and intact."
    assert "| The hull wall shall stay thick \\| and intact. |" in render_markdown(payload)


def test_matrix_is_identical_in_a_copied_root(
    scratch_root: Path, write_checks, tmp_path: Path
) -> None:
    """G3 rebuilds the report stage in another root: the matrix text must not move."""
    write_checks()
    first = build_matrix("demo", scratch_root)
    other = tmp_path / "elsewhere"
    shutil.copytree(scratch_root, other)
    second = build_matrix("demo", other)
    assert second == first
    assert render_markdown(second) == render_markdown(first)


# --- files and the stage ------------------------------------------------------------------------


def test_write_matrix_writes_both_files_deterministically(scratch_root: Path) -> None:
    first_payload, first_text = write_matrix("demo", scratch_root)
    report = scratch_root / "projects" / "demo" / "build" / "report"
    json_path, md_path = report / TRACE_JSON, report / TRACE_MD
    assert json_path.is_file() and md_path.is_file()
    assert json.loads(json_path.read_text(encoding="utf-8")) == first_payload
    assert md_path.read_text(encoding="utf-8") == first_text

    second_payload, second_text = write_matrix("demo", scratch_root)
    assert second_payload == first_payload
    assert second_text == first_text
    assert json_path.read_bytes() == json_path.read_bytes()


def test_report_stage_protocol_and_interface(scratch_root, write_checks, stage_ctx) -> None:
    stage = ReportStage()
    assert stage.name == "report"
    assert stage.needs == ["final"]
    assert stage.reads_contract is False
    assert stage.checks == []

    ctx = stage_ctx()
    stage.generate(ctx)
    assert (scratch_root / "projects" / "demo" / "src" / "report").is_dir()
    assert not (scratch_root / "projects" / "demo" / "build" / "report").exists()
    stage.build(ctx)
    facts = stage.interface(ctx)
    assert facts["requirements"] == 3
    assert facts["by_result"]["unverified"] == 3
    assert set(facts["by_evidence_level"]) == set(EVIDENCE_LEVELS)


# --- the CLI ------------------------------------------------------------------------------------


def test_cli_matrix_writes_and_prints_the_matrix(
    scratch_root: Path, write_checks, monkeypatch
) -> None:
    write_checks()
    monkeypatch.setenv("ENGENIA_ROOT", str(scratch_root))
    result = runner.invoke(app, ["matrix", "demo"])
    assert result.exit_code == 0, result.output
    assert "# Trace matrix: demo" in result.output
    assert "| R-003 |" in result.output
    report = scratch_root / "projects" / "demo" / "build" / "report"
    assert (report / TRACE_JSON).is_file()
    assert (report / TRACE_MD).is_file()


def test_cli_matrix_on_a_broken_spec_exits_one(scratch_root: Path, monkeypatch) -> None:
    (scratch_root / "projects" / "demo" / "spec" / "R-003.yaml").write_text(
        "id: R-999\nstatement: mismatched\n", encoding="utf-8"
    )
    monkeypatch.setenv("ENGENIA_ROOT", str(scratch_root))
    result = runner.invoke(app, ["matrix", "demo"])
    assert result.exit_code == 1
    assert "error:" in result.output
