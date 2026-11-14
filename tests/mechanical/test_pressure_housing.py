"""The pressure_housing pack: sizing rules, O-ring table, intake rules, scenarios, loads.

These tests own their fixtures (a contract and requirements written here) and never read the
sensor-pod example project. Sizing and table tests are pure functions and fast. The one test
that builds CAD is marked slow.
"""

from __future__ import annotations

import copy
import math
from pathlib import Path

import pytest
import yaml

from engenia.agents.redteam.stage import pack_scenarios, resolve_targets
from engenia.contracts.models import Contract, Requirement, StageContext
from engenia.core.intake_template import template_intake
from engenia.sim import depth
from packs.pressure_housing.templates import sensor_pod as pod

REPO = Path(__file__).resolve().parents[2]
PACK = REPO / "packs" / "pressure_housing"
CONTRACT = {
    "version": 2,
    "budgets": {
        "mechanical": {"mass_g": 190, "outer_envelope_mm": [80, 80, 140]},
        "electronics": {"mass_g": 60, "bay_mm": [64, 36, 22], "energy_mwh_per_day": 150},
        "firmware": {"flash_kb": 512, "ram_kb": 64},
    },
    "interfaces": {"pin_map": {"HYDROPHONE_ADC": "GPIO34"},
                   "mounts": {"board_holes_mm": [[3, 3]]}},
    "totals": {"mass_g_max": 250},
}


def requirement(rid, statement, check, limit, method="analysis", status="approved"):
    return Requirement.model_validate({
        "id": rid, "statement": statement, "value": next(iter(limit.values())), "unit": "x",
        "verify": {"method": method, "check": check, "limit": limit},
        "affects": ["simulation" if method == "simulation" else "mechanical"], "status": status,
    })


def stress(depth_m=50.0, factor=1.5):
    return requirement("R-001",
                       f"The pod shall keep a stress safety factor of at least {factor} at "
                       f"{depth_m:g} m depth.", "stress_safety_factor", {"min": factor},
                       "simulation")


@pytest.fixture
def pack():
    return yaml.safe_load((PACK / "pack.yaml").read_text())


def contract(**changes):
    data = copy.deepcopy(CONTRACT)
    for path, value in changes.items():
        node = data
        *head, last = path.split("__")
        for key in head:
            node = node[key]
        node[last] = value
    return Contract.model_validate(data)


def size(requirements, pack, **changes):
    return pod.propose(contract(**changes), requirements, pack, PACK)


# --- the O-ring table ------------------------------------------------------------------------


def test_o_ring_table_is_sorted_and_every_size_is_standard_pair():
    rings, table = pod.load_o_rings()
    assert rings == sorted(rings)
    assert {cs for cs, _ in rings} == set(table["cs_mm"])
    assert len(rings) == len(set(rings))


def test_o_ring_mass_is_a_torus():
    # 42 x 2 mm: area pi, centreline length pi * 44 -> 434.3 mm3 of NBR at 1.25 g/cm3.
    assert pod.o_ring_mass_g(42.0, 2.0, 1.25) == pytest.approx(
        math.pi * math.pi * 44 / 1000 * 1.25
    )


def test_the_chosen_o_ring_is_in_the_table_and_within_the_stretch_limit(pack):
    params, _ = size([stress()], pack)
    rings, _ = pod.load_o_rings()
    assert (params["o_ring_cs_mm"], params["o_ring_id_mm"]) in rings
    p = pod.Params(**params)
    stretch = (p.groove_root_diameter_mm - p.o_ring_id_mm) / p.o_ring_id_mm
    assert pack["pod"]["o_ring"]["stretch_min"] <= stretch <= pack["pod"]["o_ring"]["stretch_max"]
    squeeze = (p.o_ring_cs_mm - p.groove_depth_mm - p.fit_gap_mm) / p.o_ring_cs_mm
    assert squeeze == pytest.approx(pack["pod"]["o_ring"]["squeeze"])


def test_no_ring_for_a_bore_the_table_cannot_seal(pack):
    with pytest.raises(ValueError, match="no standard O-ring"):
        size([stress()], pack, budgets__electronics__bay_mm=[64, 100, 100])


# --- sizing ----------------------------------------------------------------------------------


def test_every_dimension_follows_the_contract_bay(pack):
    small, _ = size([stress()], pack)
    big, _ = size([stress()], pack, budgets__electronics__bay_mm=[64, 40, 26])
    assert big["bore_diameter_mm"] > small["bore_diameter_mm"]
    diagonal = math.hypot(40, 26) + 2 * pack["pod"]["bay_clearance_mm"]
    assert big["bore_diameter_mm"] >= diagonal
    assert small["tube_length_mm"] == (
        64 + 2 * pack["pod"]["axial_clearance_mm"] + 2 * small["plug_depth_mm"]
    )


def test_a_deeper_requirement_gives_a_thicker_wall(pack):
    shallow, _ = size([stress(20)], pack)
    deep, _ = size([stress(150)], pack)
    assert deep["wall_thickness_mm"] > shallow["wall_thickness_mm"]
    assert deep["design_pressure_mpa"] > shallow["design_pressure_mpa"]


def test_the_wall_never_falls_below_a_wall_requirement(pack):
    wall = requirement("R-002", "The pod shall have a wall of 5 mm.", "wall_thickness_min",
                       {"min": 5.0})
    params, decisions = size([stress(), wall], pack)
    assert params["wall_thickness_mm"] >= 5.0
    reason = next(d for d in decisions if d["title"] == "Wall thickness")
    assert "R-002" in reason["requirements"]


def test_the_pack_scenarios_set_the_design_depth_and_density(pack):
    params, _ = size([stress(50)], pack)
    factor, density = pod.pack_scenarios(PACK)
    assert (factor, density) == (1.2, 1028.0)
    assert params["design_depth_m"] == pytest.approx(50 * factor)
    assert params["design_pressure_mpa"] == pytest.approx(
        depth.pressure_mpa(density, 50 * factor), abs=1e-6
    )


def test_collapse_governs_a_shallow_pod_and_the_decision_says_so(pack):
    _, decisions = size([stress(50)], pack)
    wall = next(d for d in decisions if d["title"] == "Wall thickness")
    assert "collapse governs" in wall["reason"]


def test_a_pod_without_a_stated_depth_is_refused(pack):
    vague = requirement("R-001", "The pod shall be strong.", "stress_safety_factor",
                        {"min": 2.0}, "simulation")
    with pytest.raises(ValueError, match="names the depth"):
        size([vague], pack)
    with pytest.raises(ValueError, match="names the depth"):
        size([], pack)


def test_a_draft_requirement_is_ignored(pack):
    draft = requirement("R-001", "The pod shall hold at 500 m depth.",
                        "stress_safety_factor", {"min": 1.5}, "simulation", status="draft")
    with pytest.raises(ValueError, match="names the depth"):
        size([draft], pack)


def test_pressure_beyond_the_material_is_refused(pack):
    with pytest.raises(ValueError, match="exceeds what printed PETG carries"):
        size([stress(5000)], pack)


def test_an_envelope_that_is_too_small_reports_what_it_needs(pack):
    with pytest.raises(pod.PodEnvelopeError) as raised:
        size([stress()], pack, budgets__mechanical__outer_envelope_mm=[50, 50, 80])
    assert raised.value.required[2] > 80 and raised.value.available == [50, 50, 80]


def test_the_cable_gland_is_dropped_when_it_does_not_fit(pack):
    tight = copy.deepcopy(pack)
    tight["pod"]["gland"]["thread_mm"] = 40.0
    params, decisions = size([stress()], tight)
    assert params["cable_gland"] is False and params["gland_mass_g"] == 0.0
    assert next(d for d in decisions if d["title"] == "Cable gland boss")["choice"] == "none"


def test_the_cable_gland_is_optional_in_the_pack(pack):
    plain = copy.deepcopy(pack)
    plain["pod"]["cable_gland"] = False
    params, _ = size([stress()], plain)
    assert params["cable_gland"] is False


def test_sizing_is_deterministic_and_records_every_choice(pack):
    first = size([stress()], pack)
    assert first == size([stress()], pack)
    titles = [d["title"] for d in first[1]]
    for wanted in ("Bore diameter", "O-ring", "Wall thickness", "Groove", "End cap",
                   "Tube length", "Cable gland boss", "Overall size", "Design pressure"):
        assert wanted in titles
    assert all(d["reason"] and d["choice"] for d in first[1])


def test_params_reject_inconsistent_geometry(pack):
    good, _ = size([stress()], pack)
    for change in ({"outer_diameter_mm": 99.0}, {"plug_depth_mm": 3.0},
                   {"groove_depth_mm": 40.0}, {"infill_factor": 0.0},
                   {"boss_diameter_mm": 90.0}, {"tube_length_mm": float("nan")}):
        with pytest.raises(ValueError):
            pod.Params(**{**good, **change})


# --- intake, scenarios and the structure loads -----------------------------------------------


def test_intake_turns_the_example_request_into_pod_requirements(tmp_path):
    request = ("A sensor pod that survives 50 m, fits a 60 mm sensor, under 250 g, "
               "near-neutral buoyancy, wall at least 3 mm, fits the 3D printer bed")
    paths = template_intake(request, "pod", tmp_path, pack="pressure_housing")
    found = {yaml.safe_load(p.read_text())["verify"]["check"]: yaml.safe_load(p.read_text())
             for p in paths}
    assert set(found) == {"mass_budget", "stress_safety_factor", "wall_thickness_min",
                          "buoyancy_margin", "bay_fit", "print_bed_fit"}
    sf = found["stress_safety_factor"]
    assert sf["statement"].endswith("at 50 m depth.") and sf["verify"]["limit"] == {"min": 1.5}
    assert found["buoyancy_margin"]["verify"]["limit"] == {"min": -0.05}
    assert depth.parse_depth_m(sf["statement"]) == 50.0


def test_intake_takes_an_explicit_buoyancy_margin_over_near_neutral(tmp_path):
    paths = template_intake("A pod rated 30 m with a buoyancy margin of 0.1",
                            "pod", tmp_path, pack="pressure_housing")
    limits = [yaml.safe_load(p.read_text())["verify"]["limit"] for p in paths]
    assert {"min": 0.1} in limits and {"min": -0.05} not in limits


def test_the_pack_ships_scenarios_that_name_checks_not_requirement_ids(tmp_path):
    spec = tmp_path / "projects" / "p" / "spec"
    spec.mkdir(parents=True)
    (tmp_path / "projects" / "p" / "project.yaml").write_text("pack: pressure_housing\n")
    ctx = StageContext(project="p", stage="redteam", root=tmp_path)
    scenarios = pack_scenarios(ctx)
    assert {s.name for s in scenarios} == {"max_depth_plus_20pct", "cold_water"}
    assert all(s.target_checks and not s.targets for s in scenarios)
    assert any(s.extra.get("depth_factor") == 1.2 for s in scenarios)
    stress_file = spec / "R-002.yaml"
    stress_file.write_text(yaml.safe_dump({"verify": {"check": "stress_safety_factor"}}))
    kept, skipped = resolve_targets(scenarios, spec)
    assert {s.name for s in kept} == {"max_depth_plus_20pct", "cold_water"}
    assert all("R-002" in s.targets for s in kept) and not skipped


def test_the_depth_helper_reads_the_statement_the_intake_writes():
    assert depth.parse_depth_m("shall hold at 12.5 m depth.") == 12.5
    assert depth.parse_depth_m("shall hold at depth") is None
    assert depth.pressure_mpa(1000.0, 10.0) == pytest.approx(0.0980665)


def test_the_structure_solver_finds_the_stress_requirement_by_its_check(tmp_path):
    from engenia.sim.structure import solver

    spec = tmp_path / "projects" / "p" / "spec"
    spec.mkdir(parents=True)
    (spec / "R-001.yaml").write_text(yaml.safe_dump(
        {"statement": "at 3 m depth", "verify": {"check": "mass_budget"}}))
    (spec / "R-007.yaml").write_text(yaml.safe_dump(
        {"statement": "at 50 m depth", "verify": {"check": "stress_safety_factor"}}))
    ctx = StageContext(project="p", stage="simulation", root=tmp_path)
    assert solver._depth(ctx, {}) == (50.0, "R-007")
    assert solver._depth(ctx, {"requirement": "R-001"}) == (3.0, "R-001")


def test_the_structure_solver_reads_the_material_from_the_projects_pack(tmp_path):
    from engenia.sim.structure import solver

    (tmp_path / "projects" / "p").mkdir(parents=True)
    ctx = StageContext(project="p", stage="simulation", root=tmp_path)
    assert "mesh_size_mm" not in solver._material(ctx)  # the buoy pack's block
    (tmp_path / "projects" / "p" / "project.yaml").write_text("pack: pressure_housing\n")
    assert solver._material(ctx)["mesh_size_mm"] == 3.0


def test_engenia_new_writes_project_yaml_for_the_pack(tmp_path):
    from engenia.core.new_project import PACKS, STAGE_PACKS, _create

    assert "pressure_housing" in PACKS and "pressure_housing" in STAGE_PACKS
    _create("pod", tmp_path, "pressure_housing")
    project = tmp_path / "projects" / "pod"
    assert (project / "project.yaml").read_text() == "pack: pressure_housing\n"
    contract_file = project / "contract" / "contract.json"
    assert Contract.model_validate_json(contract_file.read_text()).totals.mass_g_max == 250


# --- geometry (builds CAD) -------------------------------------------------------------------


@pytest.mark.slow
def test_the_template_builds_closed_parts_that_fit_the_bay(pack):
    params, _ = size([stress()], pack)
    p = pod.Params(**params)
    parts = pod.build(p)
    assert set(parts) == {"tube", "cap_rear", "cap_front"}
    assert all(len(part.solids()) == 1 and part.is_valid for part in parts.values())
    size_xyz = parts["tube"].bounding_box().size
    assert size_xyz.X == pytest.approx(p.outer_diameter_mm, abs=1e-3)
    assert size_xyz.Z == pytest.approx(p.tube_length_mm, abs=1e-3)
    facts = pod.extra_facts(p)
    assert facts["internal_volume_cm3"] == pytest.approx(
        math.pi * (p.bore_diameter_mm / 2) ** 2 * p.cavity_length_mm / 1000, rel=1e-6)
    vessel = pod.analysis_solids(p)["vessel"]
    assert len(vessel.solids()) == 1
    assert set(pod.hardware(p)) == {"o_ring_rear", "o_ring_front", "cable_gland"}
