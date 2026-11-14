"""`engenia new`: the whole flow in a scratch root, with the deterministic fallbacks.

No model is configured, so intake uses the pack template and verify_plan writes its deterministic
plan. The other stages are fakes (their own lanes test them) and the gate is a stand-in whose
verdict is chosen by each test; release is the real one.
"""

import json
import shutil
import zipfile
from functools import partial
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from engenia.agents.verification.stage import VerifyPlanStage
from engenia.core.cli import app
from engenia.core.new_project import new_project, summary
from engenia.harness.llm import FakeLLM, text_response, tool_call_response
from engenia.harness.repair import repair_stage
from tests.core.conftest import FakeStage

runner = CliRunner()
REPO = Path(__file__).resolve().parents[2]
REQUEST = (
    "The buoy must weigh no more than 400 g. It should run for 7 days on one battery. "
    "Walls at least 3 mm thick."
)
PROJECT = "deckbox"


@pytest.fixture
def world(root, monkeypatch, fake_registry):
    """A scratch root that has the repo's verifiers and packs, and fake stages."""
    for name in ("verifiers", "packs"):
        shutil.copytree(REPO / name, root / name, ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("ENGENIA_LLM_API_KEY", "ENGENIA_LLM_BASE_URL", "ENGENIA_DESIGN_MODEL",
                 "ENGENIA_VERIFY_MODEL"):
        monkeypatch.delenv(name, raising=False)
    registry = fake_registry()
    registry["verify_plan"] = VerifyPlanStage()  # the real one, in its deterministic mode
    monkeypatch.setattr("engenia.core.engine._default_registry", lambda: registry)
    monkeypatch.setattr("engenia.stages.registry.REGISTRY", registry)

    world = SimpleNamespace(root=root, calls=[], proposals=[], repair_outcome="no_model")
    world.verdicts = [verdict("pass")]

    def gate(project, root_, **kwargs):
        report = {"verdicts": world.verdicts, "gates": {"G1": {"passed": gate_ok()}}}
        return SimpleNamespace(passed=gate_ok(), report=report)

    def gate_ok():
        return all(v["result"] == "pass" for v in world.verdicts)

    def repair(stage, ctx, failed):
        world.calls.append(("repair", stage.name))
        return world.repair_outcome

    def propose(requirement, failed, *, ctx):
        path = ctx.spec_dir / "proposals" / f"{requirement.id}.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# proposal\n")
        world.proposals.append(path)
        return path

    monkeypatch.setattr("engenia.gate.gate.run_gate", gate)
    monkeypatch.setattr("engenia.harness.repair.repair_stage", repair)
    monkeypatch.setattr("engenia.agents.intake.propose_change", propose)
    return world


def verdict(result, requirement="R-001", stage="mechanical"):
    return {
        "check": "mass_budget", "requirement": requirement, "measured": 1.0,
        "limit": {"max": 400}, "result": result, "evidence_level": "calculated",
        "artifact": None, "message": None, "stage": stage, "item": "C-001",
    }


def new(*extra, request=REQUEST, project=PROJECT, input=None):
    args = ["new", request, "--project", project, *extra]
    return runner.invoke(app, args, input=input)


def test_the_whole_flow_asks_at_both_approvals_and_releases(world):
    result = new(input="y\ny\n")
    assert result.exit_code == 0, result.output
    assert result.output.count("Approve") == 2
    assert "Approve these requirements? [y/N]" in result.output
    assert "Approve this check plan? [y/N]" in result.output
    project = world.root / "projects" / PROJECT
    assert [p.name for p in sorted((project / "spec").glob("R-*.yaml"))] == [
        "R-001.yaml", "R-002.yaml", "R-003.yaml"
    ]
    assert "status: approved" in (project / "spec" / "R-001.yaml").read_text()
    assert (project / "checks" / "APPROVED").read_text() == (
        (project / "checks" / "checks.sha256").read_text()
    )
    zips = list((world.root / "release").glob(f"{PROJECT}-*.zip"))
    assert len(zips) == 1
    with zipfile.ZipFile(zips[0]) as archive:
        assert "evidence/spec/R-001.yaml" in archive.namelist()
    for line in ("requirements: 3", "stages:", "verdicts:     1 pass", "repairs:      0 round(s)",
                 "proposals:    0", f"release:      {zips[0]}"):
        assert line in result.output


def test_yes_skips_both_questions(world):
    result = new("--yes")
    assert result.exit_code == 0, result.output
    assert "[y/N]" not in result.output
    assert (world.root / "projects" / PROJECT / "checks" / "APPROVED").is_file()


def test_declining_the_requirements_stops_before_anything_runs(world):
    result = new(input="n\n")
    project = world.root / "projects" / PROJECT
    assert result.exit_code == 1 and "STOPPED: the requirements were not approved" in result.output
    assert "status: draft" in (project / "spec" / "R-001.yaml").read_text()
    assert not (project / "build").exists() and not (world.root / "release").exists()


def test_declining_the_check_plan_stops_before_the_gate(world):
    result = new(input="y\nn\n")
    project = world.root / "projects" / PROJECT
    assert result.exit_code == 1 and "STOPPED: the check plan was not approved" in result.output
    assert (project / "checks" / "checks.yaml").is_file()
    assert not (project / "checks" / "APPROVED").exists()
    assert world.calls == [] and not (world.root / "release").exists()


def test_a_failing_gate_gives_repairs_and_proposals_but_no_release(world):
    world.verdicts = [verdict("fail")]
    result = new("--yes")
    assert result.exit_code == 1
    assert ("repair", "mechanical") in world.calls
    assert "round 1 mechanical: no_model" in result.output
    assert "proposals:    1" in result.output and "R-001.yaml (not applied)" in result.output
    assert "release:      none" in result.output and "STOPPED" in result.output
    project = world.root / "projects" / PROJECT
    assert "status: approved" in (project / "spec" / "R-001.yaml").read_text()  # not rewritten
    assert not (world.root / "release").exists()


def test_a_request_with_nothing_the_pack_knows_stops_at_intake(world):
    result = new("--yes", request="Make it good.")
    assert result.exit_code == 1 and "STOPPED: intake:" in result.output
    assert not (world.root / "release").exists()


def test_an_existing_project_is_refused(world):
    (world.root / "projects" / PROJECT).mkdir()
    result = new("--yes")
    assert result.exit_code == 1 and "already exists" in result.output


def test_an_unknown_pack_is_refused(world):
    result = new("--yes", "--pack", "submarine")
    assert result.exit_code == 1 and "unknown pack" in result.output
    assert not (world.root / "projects" / PROJECT).exists()


def test_the_enclosure_pack_has_its_own_intake_rules(world):
    request = "Weighs at most 350 g, walls 2.5 mm thick, and it must fit on the print bed."
    result = new("--yes", "--pack", "enclosure", request=request)
    assert result.exit_code == 0, result.output
    spec = world.root / "projects" / PROJECT / "spec"
    checks = {p.read_text().split("check: ")[1].split("\n")[0] for p in spec.glob("R-*.yaml")}
    assert checks == {"mass_budget", "wall_thickness_min", "print_bed_fit"}
    assert "enclosure pack has no stage support yet" in result.output
    assert (world.root / "projects" / PROJECT / "contract" / "contract.json").is_file()


# --- stage-level repair inside `engenia new` ---------------------------------------------------

class MassStage(FakeStage):
    """A mechanical stage whose design.txt sets its mass. The real mass_budget verifier checks it
    against the contract's 300 g mechanical budget."""

    checks = ["mass_budget"]

    def generate(self, ctx):
        super().generate(ctx)
        (ctx.src_dir / "design.txt").write_text("mass=860")

    def build(self, ctx):
        super().build(ctx)
        mass = float((ctx.src_dir / "design.txt").read_text().split("=")[1])
        interface = {"stage": "mechanical", "key": "sha256:" + "0" * 64,
                     "facts": {"dry_mass_g": mass}}
        (ctx.build_dir / "interface.json").write_text(json.dumps(interface))

    def interface(self, ctx):
        mass = float((ctx.src_dir / "design.txt").read_text().split("=")[1])
        return {"dry_mass_g": mass}


def mass_world(world, fake_registry, recorder):
    registry = fake_registry()
    real = registry["mechanical"]
    registry["mechanical"] = MassStage(
        "mechanical", real.needs, recorder, reads_contract=real.reads_contract
    )
    registry["verify_plan"] = VerifyPlanStage()
    return registry


def run_new(world, registry, repair):
    return new_project(
        REQUEST, PROJECT, world.root, confirm=lambda question, lines: True,
        registry=registry, repair=repair,
    )


def test_a_stage_failing_mass_budget_is_repaired_and_its_dependants_then_run(
    world, fake_registry, recorder
):
    registry = mass_world(world, fake_registry, recorder)
    llm = FakeLLM([
        tool_call_response(("write_file", {"path": "design.txt", "content": "mass=280"}),
                           ("run_build", {})),
        text_response("lowered the mass"),
    ])
    repair = partial(repair_stage, model="test-model", chat_fn=llm)

    report = run_new(world, registry, repair)

    assert report.ok, report.stopped
    assert report.repairs == [{"round": 1, "stage": "mechanical", "outcome": "repaired"}]
    assert recorder.count("build", "integration") == 1  # blocked at first, built after the repair
    assert "failed" not in report.stages and "blocked" not in report.stages
    assert "repairs:      1 round(s)" in "\n".join(summary(report))


def test_a_stage_that_cannot_be_repaired_gets_a_proposal_and_its_dependants_stay_blocked(
    world, fake_registry, recorder
):
    registry = mass_world(world, fake_registry, recorder)
    rounds = [text_response("no change")] * 9  # three attempts a round, three rounds
    repair = partial(repair_stage, model="test-model", chat_fn=FakeLLM(rounds))

    report = run_new(world, registry, repair)

    assert not report.ok and report.stopped.startswith("stages failed")
    assert [r["round"] for r in report.repairs] == [1, 2, 3]
    assert all(r["outcome"] == "exhausted" for r in report.repairs)
    assert [p.name for p in report.proposals] == ["R-001.yaml"]
    assert recorder.count("build", "integration") == 0
    assert report.release_path is None
