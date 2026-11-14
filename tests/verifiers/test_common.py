from __future__ import annotations

import pytest

from engenia.contracts.models import Limit
from verifiers._common import (
    MissingInput,
    checked_measured_verdict,
    compare,
    read_interface,
    read_measurement,
)


@pytest.mark.parametrize(
    ("measured", "limit", "expected"),
    [
        (5, Limit(min=4), "pass"),
        (3, Limit(min=4), "fail"),
        (5, Limit(max=6), "pass"),
        (7, Limit(max=6), "fail"),
        (5, Limit(equals=5), "pass"),
        (4, Limit(equals=5), "fail"),
        (5, Limit(min=0, max=10), "pass"),
        (11, Limit(min=0, max=10), "fail"),
        (-1, Limit(min=0, max=10), "fail"),
        (5, Limit(min=0, equals=5), "pass"),
        (6, Limit(min=0, equals=5), "fail"),
        (10, {"min": 0, "max": 10}, "pass"),
        (5, Limit(), "fail"),
    ],
)
def test_compare_handles_all_limit_kinds(measured, limit, expected):
    assert compare(measured, limit) == expected


def test_read_interface_returns_facts(write_interface, make_ctx):
    write_interface("mechanical", {"dry_mass_g": 123})
    facts = read_interface(make_ctx(stage="mechanical"))
    assert facts == {"dry_mass_g": 123}


def test_read_interface_missing_file_raises_clear_error(make_ctx):
    with pytest.raises(MissingInput, match="missing mechanical interface"):
        read_interface(make_ctx(stage="mechanical"))


# --- measurements -------------------------------------------------------------------------------


def test_read_measurement_returns_the_number(write_measurements, make_ctx):
    write_measurements({"mass_g": 352.1, "date": "2026-11-13"})
    assert read_measurement(make_ctx(), "mass_g") == 352.1


def test_read_measurement_without_a_file(make_ctx):
    with pytest.raises(MissingInput, match="missing measurements.yaml"):
        read_measurement(make_ctx(), "mass_g")


def test_read_measurement_without_the_key(write_measurements, make_ctx):
    write_measurements({"mass_g": 352.1})
    with pytest.raises(MissingInput, match="no 'tilt_deg' measurement"):
        read_measurement(make_ctx(), "tilt_deg")


def test_read_measurement_rejects_text(write_measurements, make_ctx):
    write_measurements({"mass_g": "heavy"})
    with pytest.raises(MissingInput, match="measurements.mass_g must be a number"):
        read_measurement(make_ctx(), "mass_g")


def test_read_measurements_rejects_a_list(tmp_path, make_ctx):
    path = tmp_path / "projects" / "demo" / "measurements.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("- 1\n- 2\n", encoding="utf-8")
    with pytest.raises(MissingInput, match="must be a mapping"):
        read_measurement(make_ctx(), "mass_g")


def test_read_measurement_rejects_broken_yaml(tmp_path, make_ctx):
    path = tmp_path / "projects" / "demo" / "measurements.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("mass_g: [unclosed\n", encoding="utf-8")
    with pytest.raises(MissingInput, match="cannot read measurements.yaml"):
        read_measurement(make_ctx(), "mass_g")


def test_checked_measured_verdict_carries_the_measured_level(make_ctx):
    verdict = checked_measured_verdict(make_ctx(limit=Limit(max=400)), 352.1)
    assert verdict.result == "pass"
    assert verdict.evidence_level == "measured"
    assert verdict.measured == 352.1
    assert verdict.message is None


def test_checked_measured_verdict_explains_a_fail(make_ctx):
    verdict = checked_measured_verdict(make_ctx(limit=Limit(max=400)), 430.0)
    assert verdict.result == "fail"
    assert verdict.evidence_level == "measured"
    assert "above max 400" in (verdict.message or "")
