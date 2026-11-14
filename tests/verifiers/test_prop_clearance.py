"""prop_clearance: (duct inner diameter - propeller diameter) / 2 inside a band."""

from __future__ import annotations

import pytest
import yaml

from engenia.contracts.models import Limit
from verifiers import load

STATEMENT = "The duct shall fit a 70 mm propeller with a tip clearance of 1 to 2 mm."


@pytest.fixture
def setup(tmp_path, write_interface, make_ctx):
    def _setup(inner, statement=STATEMENT, pack="thruster_duct", limit=None, **ctx):
        project = tmp_path / "projects" / "demo"
        (project / "spec").mkdir(parents=True, exist_ok=True)
        (project / "spec" / "R-001.yaml").write_text(yaml.safe_dump({"statement": statement}))
        if pack:
            (project / "project.yaml").write_text(f"pack: {pack}\n")
        write_interface("mechanical", {"duct_inner_diameter_mm": inner})
        return make_ctx(check="prop_clearance", limit=limit or Limit(min=1.0, max=2.0), **ctx)

    return _setup


def test_clearance_is_half_the_diameter_difference(setup):
    verdict = load("prop_clearance")(setup(73.1))
    assert verdict.result == "pass" and verdict.evidence_level == "calculated"
    assert verdict.measured == pytest.approx(1.55)


@pytest.mark.parametrize("inner, result", [
    (72.0, "pass"), (74.0, "pass"),  # 1.0 and 2.0 mm: the bounds, shift permitting
    (74.1, "fail"),  # 2.05 mm: over the maximum
    (71.9, "fail"),  # 0.95 mm: under the minimum
])
def test_the_band_is_enforced_at_both_ends(setup, inner, result):
    # With no scenarios in the pack there is no swelling shift, so 1.0 mm passes exactly.
    verdict = load("prop_clearance")(setup(inner, pack="buoy"))
    assert verdict.result == result


def test_the_packs_swelling_shift_tightens_the_minimum_only(setup):
    exact = load("prop_clearance")(setup(72.0))  # 1.0 mm nominal, 0.9 mm after the 0.1 shift
    assert exact.result == "fail" and "swelling shift" in exact.message
    assert exact.measured == pytest.approx(1.0)
    assert load("prop_clearance")(setup(72.2)).result == "pass"  # 1.1 mm, 1.0 after the shift
    assert load("prop_clearance")(setup(74.0)).result == "pass"  # the maximum has no shift


def test_params_override_the_propeller(setup):
    verdict = load("prop_clearance")(setup(73.1, params={"propeller_mm": 71.0}))
    assert verdict.measured == pytest.approx(1.05)


def test_a_one_sided_limit_works(setup):
    ctx = setup(73.1, limit=Limit(min=1.0))
    assert load("prop_clearance")(ctx).result == "pass"


@pytest.mark.parametrize("statement", ["The duct shall be round.", "The duct shall fit well."])
def test_a_requirement_with_no_propeller_is_an_error_not_a_pass(setup, statement):
    verdict = load("prop_clearance")(setup(73.1, statement=statement))
    assert verdict.result == "error" and "names no propeller" in verdict.message


def test_a_missing_duct_fact_is_an_error(setup, write_interface):
    ctx = setup(73.1)
    write_interface("mechanical", {"dry_mass_g": 30})
    assert load("prop_clearance")(ctx).result == "error"
