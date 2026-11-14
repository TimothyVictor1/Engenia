"""The agent registry: who exists, which model each uses, and what live_state reports."""

import json

from engenia.core import agents, events
from engenia.stages.registry import REGISTRY

IDENTITY = {"name", "role", "model", "family", "kind"}


def test_the_registry_lists_every_agent(root):
    listed = {a.name for a in agents.registry(root)}
    assert listed == {*REGISTRY, "repair", "intake", "gate"} | {
        p.stem for p in (root / "verifiers").glob("*.py") if p.stem != "__init__"
    }
    assert len(agents.registry(root)) == len({a.name for a in agents.registry(root)})


def test_identities_carry_role_model_family_kind(root):
    for agent in agents.registry(root):
        assert set(agent.as_log()) == IDENTITY
    gate = agents.gate_agent()
    assert (gate.role, gate.kind) == ("gate", "script")
    verifier = agents.verifier_agent("buoyancy_margin")
    assert (verifier.role, verifier.kind) == ("verifier", "script")


def test_stages_without_a_model_are_deterministic(root, monkeypatch):
    monkeypatch.delenv(agents.DESIGN_MODEL_ENV, raising=False)
    monkeypatch.delenv(agents.VERIFY_MODEL_ENV, raising=False)
    listed = {a.name: a for a in agents.registry(root)}
    for name in ("mechanical", "verify_plan", "repair", "intake"):
        assert (listed[name].model, listed[name].kind) == ("", "deterministic")


def test_model_envs_pick_the_design_and_verify_agents(root, monkeypatch):
    monkeypatch.setenv(agents.DESIGN_MODEL_ENV, "design-model-x")
    monkeypatch.setenv(agents.VERIFY_MODEL_ENV, "verify-model-y")
    listed = {a.name: a for a in agents.registry(root)}
    for name in ("mechanical", "architect", "repair", "intake"):
        assert (listed[name].model, listed[name].kind) == ("design-model-x", "llm")
    assert listed["verify_plan"].model == "verify-model-y"
    assert listed["gate"].model == ""


def test_a_harness_model_map_beats_the_env(root, monkeypatch):
    monkeypatch.setattr(agents, "_agent_models", lambda: {
        "mechanical": {"model": "mapped-model", "family": "mapped-family"},
    })
    listed = {a.name: a for a in agents.registry(root)}
    assert (listed["mechanical"].model, listed["mechanical"].family) == (
        "mapped-model", "mapped-family")


def test_live_state_is_idle_with_no_history(root):
    state = agents.live_state(root, "harbour-listener")
    assert set(state) == {a.name for a in agents.registry(root)}
    assert all(v["status"] == "idle" and v["stage"] is None for v in state.values())


def test_an_open_phase_makes_the_agent_running(root):
    with events.activate(root, 1, "harbour-listener"):
        events.set_agent(root, "mechanical", agents.for_stage("mechanical").as_log())
        with events.phase(root, "mechanical", "generate"):
            state = agents.live_state(root, "harbour-listener")
    assert state["mechanical"]["status"] == "running"
    assert (state["mechanical"]["stage"], state["mechanical"]["phase"]) == (
        "mechanical", "generate")
    assert state["mechanical"]["counts"] == {"model_calls": 0, "tool_calls": 0, "repairs": 0}
    assert state["gate"]["status"] == "idle"


def test_counts_come_from_the_log(root):
    with events.activate(root, 1, "harbour-listener"):
        events.set_agent(root, "mechanical", agents.for_stage("mechanical").as_log())
        events.emit(root, "mechanical", "message", {"role": "assistant", "text": "hi"})
        events.emit(root, "mechanical", "tool_call", {"id": "1"})
        events.emit(root, "mechanical", "repair", {"phase": "round", "outcome": "repaired"})
        events.emit(root, "mechanical", "phase_start", {"phase": "build"})
        state = agents.live_state(root, "harbour-listener")
    assert state["mechanical"]["counts"] == {"model_calls": 1, "tool_calls": 1, "repairs": 1}
    assert (state["mechanical"]["status"], state["mechanical"]["phase"]) == ("running", "build")


def test_a_pending_stage_of_a_running_run_is_waiting(root):
    from engenia.core.runstate import RunStore

    store = RunStore(root)
    run_id = store.start_run("harbour-listener", ["mechanical", "integration"])
    store.stage_started(run_id, "mechanical")  # integration stays pending
    with events.activate(root, run_id, "harbour-listener"):
        events.emit(root, "mechanical", "message", {"text": "working"})
        state = agents.live_state(root, "harbour-listener")
    store.close()
    assert state["integration"]["status"] == "waiting"
    assert state["integration"]["stage"] == "integration"
    assert state["mechanical"]["status"] == "idle"


def test_events_carry_the_registered_agent(root):
    with events.activate(root, 1, "harbour-listener"):
        events.set_agent(root, "mechanical", {"name": "mechanical", "role": "design"})
        events.emit(root, "mechanical", "message", {"text": "hi"})
    (event,) = [json.loads(x) for x in events.events_path(root, 1).read_text().splitlines()]
    assert event["agent"] == {"name": "mechanical", "role": "design"}


def test_explicit_agent_and_phase_win_over_the_registry(root):
    with events.activate(root, 1, "harbour-listener"):
        events.set_agent(root, "mechanical", {"name": "stage-agent"})
        events.set_agent(root, "repair", {"name": "repair"})
        events.emit(root, "mechanical", "message", {}, agent={"name": "repair"})
    (event,) = [json.loads(x) for x in events.events_path(root, 1).read_text().splitlines()]
    assert event["agent"] == {"name": "repair"}
