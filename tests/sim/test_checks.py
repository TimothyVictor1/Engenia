"""The two simulation verifiers: selection, pass, fail and error semantics."""

from __future__ import annotations

import pytest

from engenia.contracts.models import CheckContext, Limit
from engenia.gate.canary import CASES
from engenia.gate.gate import verdict_problem
from verifiers import load

WAVE_FACTS = {
    "wave_tilt": {
        "inshore": {"wave_height_m": 0.4, "max_tilt_deg": 2.9473},
        "boundary": {"wave_height_m": 0.5, "max_tilt_deg": 3.682381},
        "storm": {"wave_height_m": 2.0, "max_tilt_deg": 3.619666},
    },
    "safety_factor": None,
}


# --- wave_tilt ---------------------------------------------------------------------------------


def test_takes_the_largest_qualifying_tilt(sim_ctx_factory):
    verdict = load("wave_tilt")(sim_ctx_factory(facts=WAVE_FACTS))
    assert verdict.result == "pass"
    assert verdict.measured == pytest.approx(3.682381)
    assert verdict.evidence_level == "simulated"
    assert verdict_problem(verdict) is None


def test_the_threshold_is_inclusive(sim_ctx_factory):
    # boundary sits at exactly 0.5 m and carries the largest qualifying tilt.
    verdict = load("wave_tilt")(sim_ctx_factory(facts=WAVE_FACTS))
    assert verdict.measured == WAVE_FACTS["wave_tilt"]["boundary"]["max_tilt_deg"]


def test_scenarios_above_the_threshold_are_ignored(sim_ctx_factory):
    facts = {
        "wave_tilt": {
            "ok": {"wave_height_m": 0.5, "max_tilt_deg": 10.0},
            "big": {"wave_height_m": 2.0, "max_tilt_deg": 89.0},
        }
    }
    verdict = load("wave_tilt")(sim_ctx_factory(facts=facts))
    assert verdict.result == "pass"
    assert verdict.measured == 10.0


def test_params_override_the_threshold(sim_ctx_factory):
    facts = {"wave_tilt": {"chop": {"wave_height_m": 1.0, "max_tilt_deg": 35.0}}}
    verdict = load("wave_tilt")(
        sim_ctx_factory(facts=facts, params={"max_wave_height_m": 1.0})
    )
    assert verdict.result == "fail"
    assert verdict.measured == 35.0
    assert verdict_problem(verdict) is None


def test_the_alias_param_name_works(sim_ctx_factory):
    facts = {"wave_tilt": {"chop": {"wave_height_m": 0.6, "max_tilt_deg": 5.0}}}
    verdict = load("wave_tilt")(
        sim_ctx_factory(facts=facts, params={"significant_wave_height_m": 0.6})
    )
    assert verdict.result == "pass"


def test_the_default_threshold_is_half_a_metre(sim_ctx_factory):
    facts = {"wave_tilt": {"chop": {"wave_height_m": 0.6, "max_tilt_deg": 5.0}}}
    verdict = load("wave_tilt")(sim_ctx_factory(facts=facts))
    assert verdict.result == "error"  # 0.6 m does not qualify at the default 0.5 m


def test_no_qualifying_scenario_is_an_error(sim_ctx_factory):
    facts = {"wave_tilt": {"storm": {"wave_height_m": 2.0, "max_tilt_deg": 3.6}}}
    verdict = load("wave_tilt")(sim_ctx_factory(facts=facts))
    assert verdict.result == "error"
    assert verdict.measured is None
    assert "no scenario" in (verdict.message or "")


def test_an_empty_wave_tilt_fact_is_an_error(sim_ctx_factory):
    verdict = load("wave_tilt")(sim_ctx_factory(facts={"wave_tilt": {}}))
    assert verdict.result == "error"
    assert "no scenario" in (verdict.message or "")


def test_a_missing_wave_tilt_fact_is_an_error(sim_ctx_factory):
    verdict = load("wave_tilt")(sim_ctx_factory(facts={"safety_factor": 2.5}))
    assert verdict.result == "error"
    assert "wave_tilt" in (verdict.message or "")


def test_a_missing_simulation_interface_is_an_error(sim_ctx_factory):
    verdict = load("wave_tilt")(sim_ctx_factory(write=False))
    assert verdict.result == "error"
    assert "missing" in (verdict.message or "").lower()


def test_an_entry_without_max_tilt_deg_is_an_error(sim_ctx_factory):
    facts = {"wave_tilt": {"broken": {"wave_height_m": 0.4}}}
    verdict = load("wave_tilt")(sim_ctx_factory(facts=facts))
    assert verdict.result == "error"
    assert "max_tilt_deg" in (verdict.message or "")


def test_an_implausible_tilt_is_an_error(sim_ctx_factory):
    facts = {"wave_tilt": {"odd": {"wave_height_m": 0.4, "max_tilt_deg": 95.0}}}
    verdict = load("wave_tilt")(sim_ctx_factory(facts=facts))
    assert verdict.result == "error"
    assert "plausible" in (verdict.message or "")


def test_a_negative_wave_height_is_an_error(sim_ctx_factory):
    facts = {"wave_tilt": {"odd": {"wave_height_m": -0.4, "max_tilt_deg": 5.0}}}
    verdict = load("wave_tilt")(sim_ctx_factory(facts=facts))
    assert verdict.result == "error"
    assert "negative" in (verdict.message or "")


def test_a_failing_tilt_names_the_limit(sim_ctx_factory):
    facts = {"wave_tilt": {"gust": {"wave_height_m": 0.5, "max_tilt_deg": 45.0}}}
    verdict = load("wave_tilt")(sim_ctx_factory(facts=facts))
    assert verdict.result == "fail"
    assert "above max 30" in (verdict.message or "")


# --- stress_safety_factor ----------------------------------------------------------------------


def test_safety_factor_pass(sim_ctx_factory):
    verdict = load("stress_safety_factor")(
        sim_ctx_factory(
            facts={"safety_factor": 2.5},
            check="stress_safety_factor",
            requirement="R-012",
            limit=Limit(min=2),
        )
    )
    assert verdict.result == "pass"
    assert verdict.measured == 2.5
    assert verdict.evidence_level == "simulated"
    assert verdict_problem(verdict) is None


def test_safety_factor_fail(sim_ctx_factory):
    verdict = load("stress_safety_factor")(
        sim_ctx_factory(
            facts={"safety_factor": 1.5},
            check="stress_safety_factor",
            requirement="R-012",
            limit=Limit(min=2),
        )
    )
    assert verdict.result == "fail"
    assert "below min 2" in (verdict.message or "")


def test_a_null_safety_factor_is_an_error(sim_ctx_factory):
    verdict = load("stress_safety_factor")(
        sim_ctx_factory(
            facts={"safety_factor": None},
            check="stress_safety_factor",
            requirement="R-012",
            limit=Limit(min=2),
        )
    )
    assert verdict.result == "error"
    assert verdict.measured is None
    assert "null" in (verdict.message or "")


def test_a_missing_safety_factor_fact_is_an_error(sim_ctx_factory):
    verdict = load("stress_safety_factor")(
        sim_ctx_factory(
            facts={"wave_tilt": {}},
            check="stress_safety_factor",
            requirement="R-012",
            limit=Limit(min=2),
        )
    )
    assert verdict.result == "error"
    assert "safety_factor" in (verdict.message or "")


def test_a_missing_interface_is_an_error(sim_ctx_factory):
    verdict = load("stress_safety_factor")(
        sim_ctx_factory(
            check="stress_safety_factor", requirement="R-012", limit=Limit(min=2), write=False
        )
    )
    assert verdict.result == "error"


def test_a_non_numeric_safety_factor_is_an_error(sim_ctx_factory):
    verdict = load("stress_safety_factor")(
        sim_ctx_factory(
            facts={"safety_factor": "high"},
            check="stress_safety_factor",
            requirement="R-012",
            limit=Limit(min=2),
        )
    )
    assert verdict.result == "error"
    assert "number" in (verdict.message or "")


@pytest.mark.parametrize("value", [0.0, -1.0])
def test_a_non_positive_safety_factor_is_an_error(sim_ctx_factory, value):
    verdict = load("stress_safety_factor")(
        sim_ctx_factory(
            facts={"safety_factor": value},
            check="stress_safety_factor",
            requirement="R-012",
            limit=Limit(min=2),
        )
    )
    assert verdict.result == "error"
    assert "positive" in (verdict.message or "")


# --- the whole slice: stage facts fed into both verifiers ---------------------------------------


def test_stage_facts_drive_the_check_plan(sim_root, run_sim_stage, write_interface):
    facts = run_sim_stage(sim_root)
    write_interface("simulation", facts)

    wave = load("wave_tilt")(
        CheckContext(
            root=sim_root,
            project="demo",
            stage="simulation",
            check="wave_tilt",
            requirement="R-011",
            params={},
            limit=Limit(max=30),
        )
    )
    assert wave.result == "pass"
    assert wave.measured == pytest.approx(3.682381, abs=1e-3)

    stress = load("stress_safety_factor")(
        CheckContext(
            root=sim_root,
            project="demo",
            stage="simulation",
            check="stress_safety_factor",
            requirement="R-012",
            params={},
            limit=Limit(min=2),
        )
    )
    assert stress.result == "error"  # the structure case is not wired yet


def test_the_canary_fixtures_fail_their_verifiers(tmp_path, write_interface):
    for name in ("wave_tilt", "stress_safety_factor"):
        case = CASES[name]
        (facts,) = case.interfaces.values()
        write_interface("simulation", facts, project="_canary")
        ctx = CheckContext(
            root=tmp_path,
            project="_canary",
            stage=case.stage,
            check=name,
            requirement="R-000",
            params=case.params,
            limit=Limit(**case.limit),
        )
        verdict = load(name)(ctx)
        assert verdict.result == "fail", (name, verdict.message)
        assert verdict_problem(verdict) is None
