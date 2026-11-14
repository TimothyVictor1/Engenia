"""bending_stress: cantilever root stress sigma = M c / I against the pack's PETG allowable."""

from __future__ import annotations

import math

import pytest
import yaml

from engenia.contracts.models import Limit
from verifiers import load

ALLOWABLE = 35 * 0.6  # the bracket pack's allowable stress x printed knock-down, MPa
G = 9.80665


@pytest.fixture
def setup(tmp_path, write_interface, make_ctx):
    """A project whose R-001 statement names the load, and a mechanical root section."""

    def _setup(statement, section=(5.0, 20.0), offset=0.0, pack="bracket", **ctx):
        project = tmp_path / "projects" / "demo"
        (project / "spec").mkdir(parents=True, exist_ok=True)
        (project / "spec" / "R-001.yaml").write_text(yaml.safe_dump({"statement": statement}))
        if pack:
            (project / "project.yaml").write_text(f"pack: {pack}\n")
        write_interface("mechanical", {"bending_root_section_mm": list(section),
                                       "bending_root_offset_mm": offset})
        return make_ctx(check="bending_stress", limit=Limit(min=3.0), **ctx)

    return _setup


STATEMENT = "The bracket shall hold a 0.5 kg load 150 mm from the wall."


def expected(force_n, lever, offset, b, h, allowable=ALLOWABLE):
    moment = force_n * (lever - offset)
    return allowable / (moment * (h / 2) / (b * h**3 / 12))


def test_safety_factor_is_allowable_over_m_c_over_i(setup):
    verdict = load("bending_stress")(setup(STATEMENT))
    assert verdict.result == "pass" and verdict.evidence_level == "calculated"
    # The bracket pack's gust scenario doubles the load, so the verdict is the worst case.
    assert verdict.measured == pytest.approx(expected(0.5 * G * 2.0, 150, 0, 5, 20))


def test_the_root_offset_shortens_the_lever(setup):
    near = load("bending_stress")(setup(STATEMENT, offset=23.4)).measured
    far = load("bending_stress")(setup(STATEMENT, offset=0.0)).measured
    assert near == pytest.approx(far * 150 / (150 - 23.4))


def test_a_thin_arm_fails_the_limit_with_a_reason(setup):
    verdict = load("bending_stress")(setup(STATEMENT, section=(3.0, 3.0)))
    assert verdict.result == "fail" and "below min" in verdict.message


def test_params_override_the_load_and_add_a_dynamic_factor(setup):
    ctx = setup(STATEMENT, params={"load_n": 10.0, "lever_mm": 100.0, "dynamic_factor": 1.5})
    verdict = load("bending_stress")(ctx)
    assert verdict.measured == pytest.approx(expected(10.0 * 1.5 * 2.0, 100, 0, 5, 20))


def test_a_pack_without_scenarios_applies_no_load_factor(setup):
    verdict = load("bending_stress")(setup(STATEMENT, pack="buoy"))
    assert verdict.measured == pytest.approx(expected(0.5 * G, 150, 0, 5, 20))


def test_newtons_are_accepted(setup):
    ctx = setup("The bracket shall carry 12 N 80 mm from the wall.", pack="buoy")
    assert load("bending_stress")(ctx).measured == pytest.approx(expected(12.0, 80, 0, 5, 20))


@pytest.mark.parametrize("statement", [
    "The bracket shall be strong.", "The bracket shall hold 0.5 kg.",
    "The bracket shall hold a load 150 mm from the wall."])
def test_a_requirement_with_no_load_is_an_error_not_a_pass(setup, statement):
    verdict = load("bending_stress")(setup(statement))
    assert verdict.result == "error" and verdict.measured is None
    assert "names no load" in verdict.message


def test_a_load_inside_the_arm_root_is_an_error(setup):
    verdict = load("bending_stress")(setup(STATEMENT, offset=150.0))
    assert verdict.result == "error" and "not beyond" in verdict.message


def test_missing_root_section_is_an_error(setup, write_interface):
    ctx = setup(STATEMENT)
    write_interface("mechanical", {"dry_mass_g": 40})
    assert load("bending_stress")(ctx).result == "error"


def test_the_allowable_comes_from_the_packs_structure_block(setup, tmp_path):
    pack = tmp_path / "packs" / "stiff"
    pack.mkdir(parents=True)
    (pack / "pack.yaml").write_text(yaml.safe_dump({"structure": {
        "allowable_stress_mpa": 70, "printed_knock_down_factor": 0.5}}))
    verdict = load("bending_stress")(setup(STATEMENT, pack="stiff"))
    assert verdict.measured == pytest.approx(expected(0.5 * G, 150, 0, 5, 20, allowable=35.0))
    assert not math.isnan(verdict.measured)
