"""The outer repair loop: repair failed stages up to 3 rounds, then propose, never apply."""

import sqlite3
from types import SimpleNamespace

from typer.testing import CliRunner

from engenia.core.cli import app
from engenia.core.repair_loop import MAX_ROUNDS, failed_verdicts_by_stage, run_with_repair
from engenia.core.runstate import db_path

PROJECT = "harbour-listener"
runner = CliRunner()


def verdict(stage="mechanical", requirement="R-005", result="fail", measured=0.1):
    return {
        "check": "buoyancy_margin", "requirement": requirement, "measured": measured,
        "limit": {"min": 0.2}, "result": result, "evidence_level": "calculated",
        "artifact": None, "message": None, "stage": stage, "item": "C-005",
    }


def gate_result(*verdicts, passed=None, gates=None):
    ok = all(v["result"] == "pass" for v in verdicts) if passed is None else passed
    return SimpleNamespace(passed=ok, report={"verdicts": list(verdicts), "gates": gates or {}})


class Script:
    """Stand-ins for the gate, the repairer and the proposer; they record every call."""

    def __init__(self, gates, repair_outcomes=("exhausted",) * 9):
        self.gates, self.outcomes = list(gates), list(repair_outcomes)
        self.gate_calls = 0
        self.repairs, self.proposals = [], []

    def gate(self, project, root, **kwargs):
        self.gate_calls += 1
        return self.gates.pop(0) if len(self.gates) > 1 else self.gates[0]

    def repair(self, stage, ctx, failed):
        self.repairs.append((stage.name, [v.check for v in failed]))
        outcome = self.outcomes.pop(0)
        if outcome == "repaired":  # the model changed the code
            ctx.src_dir.mkdir(parents=True, exist_ok=True)
            (ctx.src_dir / "code.txt").write_text(f"repaired {len(self.repairs)}\n")
        return outcome

    def propose(self, requirement, failed, *, ctx):
        self.proposals.append((requirement.id, [v.check for v in failed], ctx.stage))
        path = ctx.spec_dir / "proposals" / f"{requirement.id}.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# proposal\n")
        return path


def go(root, fake_registry, script, **kwargs):
    return run_with_repair(
        PROJECT, root, registry=fake_registry(), gate=script.gate, repair=script.repair,
        propose=script.propose, **kwargs,
    )


def rows(root, run_id=1):
    db = sqlite3.connect(db_path(root))
    db.row_factory = sqlite3.Row
    try:
        found = db.execute("SELECT * FROM stage_run WHERE run_id = ?", (run_id,))
        return {r["stage"]: dict(r) for r in found}
    finally:
        db.close()


def test_a_passing_gate_needs_no_repair(root, fake_registry):
    script = Script([gate_result(verdict(result="pass"))])
    report = go(root, fake_registry, script)
    assert report.passed and script.repairs == [] and script.proposals == []


def test_a_failed_verdict_is_repaired_then_the_stage_and_its_dependants_rebuild(
    root, fake_registry, recorder
):
    script = Script([gate_result(verdict()), gate_result(verdict(result="pass"))],
                    ["repaired"])
    report = go(root, fake_registry, script)
    assert report.passed and report.rounds == [
        {"round": 1, "stage": "mechanical", "outcome": "repaired"}
    ]
    assert script.repairs == [("mechanical", ["buoyancy_margin"])]
    assert recorder.count("build", "mechanical") == 2  # again after the repair
    assert recorder.count("build", "integration") == 2  # it follows mechanical
    assert recorder.count("build", "firmware") == 1  # untouched
    assert script.proposals == []
    found = rows(root)["mechanical"]
    assert found["message"] == "repair round 1: repaired" and found["status"] == "done"
    assert found["attempts"] == 3  # built, repair round, rebuilt
    run = sqlite3.connect(db_path(root)).execute("SELECT status FROM run").fetchall()
    assert run == [("done",)]


def test_three_failed_rounds_then_a_proposal_that_is_never_applied(
    root, fake_registry, project_dir
):
    spec_before = {p.name: p.read_bytes() for p in (project_dir / "spec").glob("R-*.yaml")}
    script = Script([gate_result(verdict())])
    report = go(root, fake_registry, script)

    assert not report.passed and len(script.repairs) == MAX_ROUNDS == 3
    assert [r["round"] for r in report.rounds] == [1, 2, 3]
    assert script.proposals == [("R-005", ["buoyancy_margin"], "mechanical")]
    assert report.proposals == [project_dir / "spec" / "proposals" / "R-005.yaml"]
    assert "3 repair round" in report.reason
    # the spec itself is exactly what it was
    assert {p.name: p.read_bytes() for p in (project_dir / "spec").glob("R-*.yaml")} == spec_before

    found = rows(root)["mechanical"]
    assert found["status"] == "failed"
    assert found["message"].count("repair round") == 3
    assert "proposed change to R-005 (R-005.yaml; needs approval)" in found["message"]
    run = sqlite3.connect(db_path(root)).execute("SELECT status FROM run").fetchall()
    assert run == [("failed",)]


def test_the_proposal_comes_only_after_the_third_round(root, fake_registry):
    order = []
    script = Script([gate_result(verdict())])
    original_repair, original_propose = script.repair, script.propose
    script.repair = lambda *a: order.append("repair") or original_repair(*a)
    script.propose = lambda *a, **k: order.append("propose") or original_propose(*a, **k)
    go(root, fake_registry, script)
    assert order == ["repair", "repair", "repair", "propose"]


def test_with_no_model_it_goes_straight_to_a_proposal(root, fake_registry):
    script = Script([gate_result(verdict())], ["no_model"])
    report = go(root, fake_registry, script)
    assert len(script.repairs) == 1 and len(script.proposals) == 1
    assert "no design model" in report.reason
    assert rows(root)["mechanical"]["message"].startswith("repair round 1: no_model")


def test_a_repair_that_breaks_the_build_ends_the_loop_without_a_proposal(
    root, fake_registry
):
    registry = fake_registry()
    script = Script([gate_result(verdict())], ["repaired"])
    original = script.repair

    def repair(stage, ctx, failed):
        outcome = original(stage, ctx, failed)
        registry["mechanical"].fail_in = "build"
        return outcome

    report = run_with_repair(
        PROJECT, root, registry=registry, gate=script.gate, repair=repair, propose=script.propose
    )
    assert not report.passed and "stages failed after repair" in report.reason
    assert script.proposals == []


def test_a_gate_failure_no_repair_can_touch_stops_the_loop(root, fake_registry):
    script = Script([gate_result(passed=False, gates={"approval": {"passed": False}})])
    report = go(root, fake_registry, script)
    assert script.repairs == [] and script.proposals == []
    assert "no stage repair" in report.reason


def test_a_failed_pipeline_is_not_repaired(root, fake_registry):
    registry = fake_registry({"mechanical": {"fail_in": "build"}})
    script = Script([gate_result(verdict())])
    report = run_with_repair(
        PROJECT, root, registry=registry, gate=script.gate, repair=script.repair,
        propose=script.propose,
    )
    assert not report.passed and script.gate_calls == 0 and "nothing to repair" in report.reason


def test_a_failure_with_no_requirement_gets_a_repair_but_no_proposal(root, fake_registry):
    script = Script([gate_result(verdict(requirement=None))])
    go(root, fake_registry, script)
    assert len(script.repairs) == 3 and script.proposals == []


def test_a_requirement_with_no_measurement_is_reported_not_guessed(root, fake_registry):
    from engenia.agents.intake import propose_change

    script = Script([gate_result(verdict(measured=None, result="error"))])
    report = run_with_repair(
        PROJECT, root, registry=fake_registry(), gate=script.gate, repair=script.repair,
        propose=propose_change,
    )
    assert report.proposals == [] and "R-005" in report.no_proposal


def test_the_real_proposer_writes_a_draft_below_spec_proposals(root, fake_registry, project_dir):
    from engenia.agents.intake import propose_change

    script = Script([gate_result(verdict(measured=0.12))])
    report = run_with_repair(
        PROJECT, root, registry=fake_registry(), gate=script.gate, repair=script.repair,
        propose=propose_change,
    )
    path = project_dir / "spec" / "proposals" / "R-005.yaml"
    assert report.proposals == [path] and "status: draft" in path.read_text()
    assert "0.20" in (project_dir / "spec" / "R-005.yaml").read_text()  # still the old one


def test_failed_verdicts_are_grouped_by_the_stage_that_produced_them():
    report = {"verdicts": [
        verdict("mechanical"), verdict("firmware", result="error"),
        verdict("electronics", result="pass"), verdict(stage=None),
    ]}
    grouped = failed_verdicts_by_stage(report)
    assert set(grouped) == {"mechanical", "firmware"}
    assert grouped["firmware"][0].result.value == "error"


def test_the_command_runs_the_loop(root, monkeypatch, fake_registry):
    monkeypatch.setattr("engenia.core.engine._default_registry", fake_registry)
    monkeypatch.setattr("engenia.stages.registry.REGISTRY", fake_registry())
    script = Script([gate_result(verdict())], ["no_model"])
    monkeypatch.setattr("engenia.gate.gate.run_gate", script.gate)
    monkeypatch.setattr("engenia.harness.repair.repair_stage", script.repair)
    monkeypatch.setattr("engenia.agents.intake.propose_change", script.propose)
    result = runner.invoke(app, ["run", PROJECT, "--repair"])
    assert result.exit_code == 1, result.output
    assert "repair round 1: mechanical: no_model" in result.output
    assert "not applied" in result.output
