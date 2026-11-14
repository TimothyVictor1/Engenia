"""The thruster_duct pack: sizing rules, statement parsing, intake bands, geometry.

The tests own their fixtures and never read the thruster-duct example project. Sizing is a pure
function and fast; the geometry test builds CAD and is marked slow.
"""

from __future__ import annotations

import copy
import math
from pathlib import Path

import pytest
import yaml

from engenia.contracts.models import Contract, Requirement
from engenia.core.intake_template import template_intake
from engenia.sim.duct import parse_bolts, parse_propeller_mm, parse_thrust_n
from engenia.sim.loads import scenario_extra_max, scenario_load_factor
from packs.thruster_duct.templates import duct as dt

REPO = Path(__file__).resolve().parents[2]
PACK = REPO / "packs" / "thruster_duct"
CLEARANCE = "The duct shall fit a 70 mm propeller with a tip clearance of 1 to 2 mm."
FLANGE = ("The 4 x M4 flange shall carry 15 N of thrust with a bending safety factor of at "
          "least 3.")
ALLOWABLE = 35 * 0.6


@pytest.fixture
def pack():
    return yaml.safe_load((PACK / "pack.yaml").read_text())


@pytest.fixture
def contract():
    return Contract.model_validate_json((PACK / "default_contract.json").read_text())


def requirement(rid, statement, check, limit, status="approved"):
    return Requirement.model_validate({
        "id": rid, "statement": statement, "value": next(iter(limit.values())), "unit": "x",
        "verify": {"method": "analysis", "check": check, "limit": limit},
        "affects": ["mechanical"], "status": status,
    })


def clearance(statement=CLEARANCE, low=1.0, high=2.0):
    limit = {k: v for k, v in (("min", low), ("max", high)) if v is not None}
    return requirement("R-001", statement, "prop_clearance", limit)


def flange(statement=FLANGE, factor=3.0):
    return requirement("R-002", statement, "bending_stress", {"min": factor})


def size(pack, contract, requirements):
    return dt.propose(contract, requirements, pack, PACK)


# --- statement parsing and scenarios ---------------------------------------------------------


def test_the_statement_parsers_read_propeller_bolts_and_thrust():
    assert parse_propeller_mm(CLEARANCE) == 70
    assert parse_propeller_mm("a propeller of 50 mm") == 50
    assert parse_bolts(FLANGE).count == 4 and parse_bolts(FLANGE).size_mm == 4
    assert parse_thrust_n(FLANGE) == 15 and parse_thrust_n("thrust of 12 N") == 12
    assert parse_propeller_mm("round") is None and parse_bolts("a flange") is None
    assert parse_thrust_n("a 15 mm flange") is None


def test_the_pack_scenarios_carry_a_load_factor_and_a_clearance_shift():
    assert scenario_load_factor(PACK, "bending_stress") == 2.0
    assert scenario_extra_max(PACK, "prop_clearance", "clearance_shift_mm", 0.0) == 0.1
    assert scenario_extra_max(PACK, "mass_budget", "clearance_shift_mm", 0.0) == 0.0


# --- sizing -----------------------------------------------------------------------------------


def test_the_throat_is_the_propeller_plus_the_middle_of_the_clearance_band(pack, contract):
    params, decisions = size(pack, contract, [clearance(), flange()])
    shift = 0.1
    assert params["clearance_mm"] == pytest.approx((1.0 + shift + 2.0) / 2, abs=0.05)
    assert params["throat_diameter_mm"] == pytest.approx(70 + 2 * params["clearance_mm"])
    titles = [d["title"] for d in decisions]
    for wanted in ("Tip clearance", "Throat diameter", "Wall thickness", "Nozzle profile",
                   "Mounting flange", "Fillets", "Overall size"):
        assert wanted in titles


def test_a_different_propeller_gives_a_different_throat(pack, contract):
    small, _ = size(pack, contract, [clearance(CLEARANCE.replace("70", "50")), flange()])
    large, _ = size(pack, contract, [clearance(CLEARANCE.replace("70", "90")), flange()])
    assert small["throat_diameter_mm"] < 70 < large["throat_diameter_mm"]
    assert small["length_mm"] < large["length_mm"]  # the length follows the throat


def test_the_profile_comes_from_the_pack_rules(pack, contract):
    params, _ = size(pack, contract, [clearance(), flange()])
    rules = pack["duct"]
    assert params["length_mm"] == pytest.approx(
        rules["length_ratio"] * params["throat_diameter_mm"])
    assert params["plane_mm"] == pytest.approx(rules["plane_ratio"] * params["length_mm"])
    assert params["land_mm"] == pytest.approx(rules["land_ratio"] * 70)
    changed = copy.deepcopy(pack)
    changed["duct"]["length_ratio"] = 0.8
    longer, _ = size(changed, contract, [clearance(), flange()])
    assert longer["length_mm"] > params["length_mm"]


def test_the_bolts_come_from_the_requirement_else_the_pack(pack, contract):
    params, _ = size(pack, contract, [clearance(), flange(FLANGE.replace("4 x M4", "6 x M5"))])
    assert params["bolt_count"] == 6 and params["bolt_mm"] == 5
    assert params["bolt_hole_mm"] == pytest.approx(5 + pack["duct"]["flange"]["bolt_clearance_mm"])
    default, _ = size(pack, contract, [clearance()])
    assert default["bolt_count"] == pack["duct"]["flange"]["default_bolts"]
    assert default["design_thrust_n"] == 0.0


def test_a_larger_thrust_gives_a_thicker_flange_by_the_beam_formula(pack, contract):
    light, _ = size(pack, contract, [clearance(), flange()])
    heavy, _ = size(pack, contract, [clearance(), flange(FLANGE.replace("15 N", "600 N"))])
    assert heavy["flange_thickness_mm"] > light["flange_thickness_mm"]
    p = dt.Params(**heavy)
    net = 2 * math.pi * p.bolt_circle_radius_mm - p.bolt_count * p.bolt_hole_mm
    sigma = 6 * p.design_thrust_n * (p.flange_width_mm / 2) / (net * p.flange_thickness_mm**2)
    assert ALLOWABLE / sigma >= 3.0
    assert p.design_thrust_n == pytest.approx(600 * 2.0)  # the thrust-surge scenario factor


def test_the_wall_requirement_sets_the_wall(pack, contract):
    wall = requirement("R-003", "The duct shall have a wall of 2.5 mm.", "wall_thickness_min",
                       {"min": 2.5})
    params, decisions = size(pack, contract, [clearance(), flange(), wall])
    assert params["wall_mm"] >= 2.5 and params["flange_thickness_mm"] >= params["wall_mm"]
    assert "R-003" in next(d for d in decisions if d["title"] == "Wall thickness")["requirements"]


def test_one_sided_clearance_aims_inside_the_bound(pack, contract):
    low, _ = size(pack, contract, [clearance(low=1.0, high=None)])
    assert low["clearance_mm"] == pytest.approx(1.0 + 0.1 + pack["duct"]["clearance_margin_mm"])
    high, _ = size(pack, contract, [clearance(low=None, high=2.0)])
    assert high["clearance_mm"] == pytest.approx(2.0 - pack["duct"]["clearance_margin_mm"])


def test_an_impossible_clearance_band_is_refused(pack, contract):
    with pytest.raises(ValueError, match="no tip clearance"):
        size(pack, contract, [clearance(low=1.0, high=1.05)])


def test_a_duct_without_a_propeller_is_refused(pack, contract):
    for requirements in ([], [clearance("The duct shall clear the blades.")],
                         [requirement("R-001", CLEARANCE, "prop_clearance", {"min": 1.0},
                                      status="draft")]):
        with pytest.raises(ValueError, match="names the propeller"):
            size(pack, contract, requirements)
    with pytest.raises(ValueError, match="min or a max"):
        size(pack, contract, [requirement("R-001", CLEARANCE, "prop_clearance", {"equals": 1})])


def test_a_small_envelope_reports_what_the_duct_needs(pack, contract):
    data = contract.model_dump(mode="json")
    data["budgets"]["mechanical"]["outer_envelope_mm"] = [60, 60, 20]
    with pytest.raises(dt.DuctEnvelopeError) as raised:
        size(pack, Contract.model_validate(data), [clearance(), flange()])
    assert raised.value.available == [60, 60, 20] and raised.value.required[-1] > 60


def test_sizing_is_deterministic(pack, contract):
    assert size(pack, contract, [clearance(), flange()]) == size(
        pack, contract, [clearance(), flange()])


def test_params_reject_inconsistent_geometry(pack, contract):
    good, _ = size(pack, contract, [clearance(), flange()])
    for change in ({"throat_diameter_mm": 99.0}, {"bolt_hole_mm": 3.0}, {"bolt_count": 2},
                   {"land_mm": 60.0}, {"flange_width_mm": 3.0}, {"flange_thickness_mm": 0.8},
                   {"lip_mm": 5.0}, {"infill_factor": 0.0}, {"wall_mm": float("nan")}):
        with pytest.raises(ValueError):
            dt.Params(**{**good, **change})


def test_the_facts_publish_what_the_verifiers_read(pack, contract):
    params, _ = size(pack, contract, [clearance(), flange()])
    p = dt.Params(**params)
    facts = dt.extra_facts(p)
    assert facts["duct_inner_diameter_mm"] == p.throat_diameter_mm
    assert facts["bending_root_offset_mm"] == 0.0
    assert facts["bending_lever_mm"] == pytest.approx(p.flange_width_mm / 2)
    width, depth = facts["bending_root_section_mm"]
    assert depth == p.flange_thickness_mm
    assert width == pytest.approx(2 * math.pi * p.bolt_circle_radius_mm
                                  - p.bolt_count * p.bolt_hole_mm)
    assert dt.hardware(p) == {} and dt.analysis_solids(p) == {}


# --- intake -----------------------------------------------------------------------------------


def test_intake_makes_a_clearance_band_and_a_flange_requirement_from_the_example(tmp_path):
    request = ("A thruster duct that fits a 70 mm propeller with 1-2 mm tip clearance, 4 x M4 "
               "flange carrying 15 N of thrust at safety factor 3, under 150 g, walls at least "
               "1.5 mm, fits the print bed")
    paths = template_intake(request, "d", tmp_path, pack="thruster_duct")
    found = {yaml.safe_load(p.read_text())["verify"]["check"]: yaml.safe_load(p.read_text())
             for p in paths}
    assert set(found) == {"prop_clearance", "bending_stress", "mass_budget",
                          "wall_thickness_min", "print_bed_fit"}
    assert found["prop_clearance"]["verify"]["limit"] == {"min": 1, "max": 2}
    assert parse_propeller_mm(found["prop_clearance"]["statement"]) == 70
    flange_requirement = found["bending_stress"]
    assert flange_requirement["verify"]["limit"] == {"min": 3}
    assert parse_bolts(flange_requirement["statement"]).count == 4
    assert parse_thrust_n(flange_requirement["statement"]) == 15
    assert found["mass_budget"]["verify"]["limit"] == {"max": 150}


def test_intake_without_bolts_still_makes_one_flange_requirement(tmp_path):
    paths = template_intake("A duct for a 50 mm propeller with 0.8 to 1.5 mm tip clearance, "
                            "flange takes 8 N thrust at safety factor 2", "d", tmp_path,
                            pack="thruster_duct")
    checks = [yaml.safe_load(p.read_text())["verify"]["check"] for p in paths]
    assert checks.count("bending_stress") == 1 and "prop_clearance" in checks


# --- geometry (builds CAD) --------------------------------------------------------------------


@pytest.mark.slow
def test_the_template_builds_a_ring_that_clears_the_propeller_and_has_its_bolt_holes(
        pack, contract):
    params, _ = size(pack, contract, [clearance(), flange()])
    p = dt.Params(**params)
    duct = dt.build(p)["duct"]
    assert duct.is_valid and len(duct.solids()) == 1
    box = duct.bounding_box().size
    assert box.X == pytest.approx(2 * p.flange_outer_radius_mm, abs=0.01)
    assert box.Z == pytest.approx(p.length_mm, abs=0.01)
    # The throat is empty at the propeller plane, and every bolt hole is empty through the flange.
    from build123d import Cylinder, Pos

    bore = Pos(0, 0, p.plane_mm) * Cylinder(p.throat_radius_mm - 1e-3, 1.0)
    assert (duct & bore).volume == pytest.approx(0.0, abs=1e-6)
    mid = p.length_mm - p.flange_thickness_mm / 2
    for k in range(p.bolt_count):
        angle = 2 * math.pi * k / p.bolt_count
        hole = Pos(p.bolt_circle_radius_mm * math.cos(angle),
                   p.bolt_circle_radius_mm * math.sin(angle), mid) * Cylinder(
            p.bolt_hole_mm / 2 - 1e-3, p.flange_thickness_mm)
        assert (duct & hole).volume == pytest.approx(0.0, abs=1e-6)
    # And the flange is solid between the holes.
    web = Pos(p.bolt_circle_radius_mm * math.cos(math.pi / 4),
              p.bolt_circle_radius_mm * math.sin(math.pi / 4), mid) * Cylinder(
        1.0, p.flange_thickness_mm - 0.2)
    assert (duct & web).volume > 0.5 * web.volume
