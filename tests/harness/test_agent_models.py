"""Model routing, family separation and event attribution for individual agents."""

import pytest

from engenia.core import events
from engenia.core.intake_run import model_configured
from engenia.harness.design_loop import generate_until_pass
from engenia.harness.llm import (
    DESIGN_AGENTS,
    FakeLLM,
    ModelFamilyError,
    agent_model,
    agent_models,
    check_model_families,
    text_response,
    tool_call_response,
)
from engenia.harness.proposer import propose
from engenia.harness.repair import repair_stage


@pytest.mark.parametrize("agent", DESIGN_AGENTS)
def test_agent_override_and_blank_fallback(monkeypatch, agent):
    monkeypatch.setenv("ENGENIA_DESIGN_MODEL", "Fallback/default")
    variable = f"ENGENIA_MODEL_{agent.upper()}"
    monkeypatch.setenv(variable, "  Individual/model  ")
    assert agent_models()[agent] == {"model": "Individual/model", "family": "individual"}
    monkeypatch.setenv(variable, "  ")
    assert agent_models()[agent] == {"model": "Fallback/default", "family": "fallback"}
    monkeypatch.delenv("ENGENIA_DESIGN_MODEL")
    assert agent_models()[agent] == {"model": "", "family": ""}


@pytest.mark.parametrize("agent", DESIGN_AGENTS)
def test_every_override_is_guarded_even_without_shared_default(agent):
    env = {f"ENGENIA_MODEL_{agent.upper()}": "Verify/design",
           "ENGENIA_VERIFY_MODEL": "Verify/check"}
    with pytest.raises(ModelFamilyError, match=f"ENGENIA_MODEL_{agent.upper()}"):
        check_model_families(env)
    env["ENGENIA_ALLOW_SAME_FAMILY"] = "1"
    assert f"ENGENIA_MODEL_{agent.upper()}" in check_model_families(env)


def test_actual_explicit_model_is_guarded(monkeypatch, ctx, stage):
    monkeypatch.setenv("ENGENIA_VERIFY_MODEL", "Verify/check")
    llm = FakeLLM([])
    with pytest.raises(ModelFamilyError):
        generate_until_pass(stage, ctx, model="Verify/design", chat_fn=llm)
    assert not llm.calls


@pytest.mark.parametrize("agent", ("mechanical", "electronics", "firmware", "redteam"))
def test_design_loop_routes_override(monkeypatch, ctx, stage, agent):
    monkeypatch.setenv("ENGENIA_DESIGN_MODEL", "Default/model")
    monkeypatch.setenv(f"ENGENIA_MODEL_{agent.upper()}", "Chosen/model")
    stage.name = agent
    llm = FakeLLM([tool_call_response(
        ("write_file", {"path": "design.txt", "content": "mass=100"})
    ), text_response("done")])
    assert generate_until_pass(stage, ctx, chat_fn=llm) == "pass"
    assert {call.model for call in llm.calls} == {"Chosen/model"}


def test_proposer_routes_stage_override(monkeypatch, ctx):
    monkeypatch.setenv("ENGENIA_MODEL_MECHANICAL", "Chosen/proposer")
    llm = FakeLLM([tool_call_response(
        ("write_file", {"path": "proposal.json", "content": '{"value": 1}'})
    ), text_response("done")])
    assert propose(ctx, task="choose", validate=lambda _: [], fallback=lambda: {},
                   chat_fn=llm) == {"value": 1}
    assert {call.model for call in llm.calls} == {"Chosen/proposer"}


def test_repair_uses_repair_override_and_attributes_all_events(monkeypatch, ctx, stage):
    from engenia.harness.checks import run_stage_checks

    monkeypatch.setenv("ENGENIA_MODEL_MECHANICAL", "Stage/model")
    monkeypatch.setenv("ENGENIA_MODEL_REPAIR", "Repair/model")
    ctx.src_dir.mkdir(parents=True)
    (ctx.src_dir / "design.txt").write_text("mass=450")
    failed = run_stage_checks(stage, ctx)
    llm = FakeLLM([tool_call_response(
        ("write_file", {"path": "design.txt", "content": "mass=100"})
    ), text_response("fixed")])
    with events.activate(ctx.root, 1):
        assert repair_stage(stage, ctx, failed, chat_fn=llm) == "repaired"
    assert {call.model for call in llm.calls} == {"Repair/model"}
    logged, _ = events.read_events(ctx.root, 1)
    assert logged
    assert all(e["agent"] == {"name": "repair", "model": "Repair/model", "family": "repair"}
               for e in logged)
    # Context must be restored after the repair.
    with events.activate(ctx.root, 1):
        events.emit(ctx.root, "mechanical", "message", {})
    logged, _ = events.read_events(ctx.root, 1)
    assert logged[-1]["agent"]["model"] == "Stage/model"


def test_explicit_model_is_reported_for_tool_and_code_events(ctx, stage):
    llm = FakeLLM([tool_call_response(
        ("write_file", {"path": "design.txt", "content": "mass=100"})
    ), text_response("done")])
    with events.activate(ctx.root, 1):
        generate_until_pass(stage, ctx, model="Explicit/model", chat_fn=llm)
    logged, _ = events.read_events(ctx.root, 1)
    assert {e["kind"] for e in logged} >= {"message", "tool_call", "tool_result", "code"}
    assert all(e["agent"] == {"name": "mechanical", "model": "Explicit/model",
                              "family": "explicit"} for e in logged)


def test_intake_override_configures_model_without_shared_default(monkeypatch):
    monkeypatch.setenv("ENGENIA_LLM_API_KEY", "test-key")
    monkeypatch.setenv("ENGENIA_LLM_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("ENGENIA_MODEL_INTAKE", "Intake/model")
    assert model_configured()


def test_verification_does_not_inherit_design_default(monkeypatch):
    monkeypatch.setenv("ENGENIA_DESIGN_MODEL", "Design/model")
    monkeypatch.setenv("ENGENIA_VERIFY_MODEL", "Verify/model")
    for agent in ("verify_plan", "final"):
        assert agent_model(agent) == "Verify/model"
        assert agent_models()[agent] == {"model": "Verify/model", "family": "verify"}


def test_architect_uses_its_override(monkeypatch, ctx):
    import json
    from pathlib import Path

    from engenia.agents.architect.stage import ArchitectStage

    monkeypatch.setenv("ENGENIA_DESIGN_MODEL", "Default/model")
    monkeypatch.setenv("ENGENIA_MODEL_ARCHITECT", "Architect/model")
    contract_path = Path(__file__).resolve().parents[2] / "packs/buoy/default_contract.json"
    contract = contract_path.read_text()
    llm = FakeLLM([tool_call_response(
        ("write_file", {"path": "contract.json", "content": contract})
    ), text_response("done")])
    ArchitectStage(chat_fn=llm).generate(ctx)
    assert json.loads(ctx.contract_path.read_text()) == json.loads(contract)
    assert {call.model for call in llm.calls} == {"Architect/model"}


def test_parallel_event_contexts_do_not_mix(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    barrier = Barrier(2)

    def emit(agent):
        with events.agent_context(agent, f"{agent}/model"):
            barrier.wait(timeout=5)
            events.emit(tmp_path, agent, "message", {})

    with events.activate(tmp_path, 1), ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(emit, ["mechanical", "electronics"]))
    logged, _ = events.read_events(tmp_path, 1)
    assert len(logged) == 2
    for event in logged:
        assert event["agent"] == {"name": event["stage"],
                                  "model": f"{event['stage']}/model", "family": event["stage"]}
