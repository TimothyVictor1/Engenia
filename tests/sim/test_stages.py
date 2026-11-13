import json
import shutil
import types

import yaml
from typer.testing import CliRunner

from engenia.contracts.models import StageContext
from engenia.core.cli import app
from engenia.sim import simulation
from engenia.sim.simprep import SimprepStage
from engenia.sim.simulation import SimulationStage
from tests.sim.conftest import REPO


def run_simprep(root):
    ctx = StageContext(project="fixture", stage="simprep", root=root)
    stage = SimprepStage()
    stage.generate(ctx)
    stage.build(ctx)
    return ctx, stage, yaml.safe_load((ctx.build_dir / "cases.yaml").read_text())


def test_simprep_writes_one_case_per_simulation_requirement(root):
    ctx, stage, cases = run_simprep(root)
    assert [(c["id"], c["solver"], c["requirement"]) for c in cases] == [
        ("SIM-011", "waves", "R-011"), ("SIM-012", "structure", "R-012"),
        ("SIM-FLOW", "flow", None),
    ]
    assert cases[0]["scenarios"] == ["short_chop", "swell"]
    assert (ctx.src_dir / "cases.yaml").is_file()
    assert stage.interface(ctx) == {"cases": ["SIM-011", "SIM-012", "SIM-FLOW"]}


def test_simprep_skips_draft_requirements(root):
    path = root / "projects/fixture/spec/R-011.yaml"
    path.write_text(path.read_text().replace("status: approved", "status: draft"))
    _, _, cases = run_simprep(root)
    assert [c["id"] for c in cases] == ["SIM-012", "SIM-FLOW"]


def fake_solver(monkeypatch, results):
    def fake_import(name):
        if name not in results:
            return None
        return types.SimpleNamespace(run_case=lambda case, ctx: results[name])
    monkeypatch.setattr(simulation, "_solver", lambda name: fake_import(name))


def simulate(root):
    run_simprep(root)
    ctx = StageContext(project="fixture", stage="simulation", root=root)
    stage = SimulationStage()
    stage.build(ctx)
    return stage.interface(ctx)


def test_facts_merge_and_missing_solver_is_not_run(root, monkeypatch):
    tilt = {"s": {"wave_height_m": 1.0, "max_tilt_deg": 2.0}}
    ok = {"passed": True, "messages": []}
    fake_solver(monkeypatch, {"waves": {"wave_tilt": tilt, "sanity": ok}})
    facts = simulate(root)
    assert facts == {
        "wave_tilt": tilt, "safety_factor": None, "max_von_mises_mpa": None,
        "structure_depth_m": None, "drag_n": {}, "drag_evidence": {},
        "not_run": ["SIM-012", "SIM-FLOW"],
        "sanity": {"SIM-011": {"passed": True, "messages": []}},
    }


def test_structure_scalars_are_merged(root, monkeypatch):
    structure = {"safety_factor": 2.5, "max_von_mises_mpa": 12.0, "structure_depth_m": 3.0,
                 "sanity": {"passed": True, "messages": []}}
    fake_solver(monkeypatch, {"structure": structure})
    facts = simulate(root)
    assert (facts["safety_factor"], facts["max_von_mises_mpa"], facts["structure_depth_m"]) == (
        2.5, 12.0, 3.0)
    assert facts["not_run"] == ["SIM-011", "SIM-FLOW"] and facts["wave_tilt"] == {}


def test_flow_drag_is_published_per_scenario(root, monkeypatch):
    flow = {"drag_n": {"short_chop": 0.2, "swell": 0.8},
            "evidence": {"short_chop": "calculated", "swell": "calculated"},
            "sanity": {"passed": True, "messages": []}}
    fake_solver(monkeypatch, {"flow": flow})
    facts = simulate(root)
    assert facts["drag_n"] == flow["drag_n"]
    assert facts["drag_evidence"] == flow["evidence"]


def test_case_failing_sanity_contributes_only_its_sanity(root, monkeypatch):
    bad = {"wave_tilt": {"s": {"wave_height_m": 1.0, "max_tilt_deg": 99.0}},
           "sanity": {"passed": False, "messages": ["s: nonsense"]}}
    fake_solver(monkeypatch, {"waves": bad})
    facts = simulate(root)
    assert facts["wave_tilt"] == {}
    assert facts["sanity"]["SIM-011"] == {"passed": False, "messages": ["s: nonsense"]}


def test_real_structure_module_absent_gives_not_run(root):
    if (REPO / "engenia/sim/structure").exists() or (REPO / "engenia/sim/structure.py").exists():
        return  # another lane's solver has landed; this check is only for its absence
    assert simulation._solver("structure") is None


def test_pipeline_gives_wave_tilt_for_every_scenario(tmp_path, monkeypatch):
    shutil.copytree(REPO / "projects/harbour-listener/spec",
                    tmp_path / "projects/harbour-listener/spec")
    monkeypatch.setenv("ENGENIA_ROOT", str(tmp_path))
    result = CliRunner().invoke(app, ["run", "harbour-listener"])
    assert result.exit_code == 0, result.output
    build = tmp_path / "projects/harbour-listener/build"
    facts = json.loads((build / "simulation/interface.json").read_text())["facts"]
    scenarios = sorted(p.stem for p in (build / "redteam/scenarios").glob("*.yaml"))
    assert sorted(facts["wave_tilt"]) == scenarios and len(scenarios) >= 1
    assert sorted(facts["drag_n"]) == scenarios
    assert all(value > 0 for value in facts["drag_n"].values())
    assert all(facts["drag_evidence"][name] == "calculated" for name in scenarios)
    assert facts["sanity"]["SIM-011"]["passed"] is True
    text = (build / "simulation/results.json").read_text()
    assert str(tmp_path) not in text and str(REPO) not in text
    first = (build / "simulation/interface.json").read_bytes()

    shutil.rmtree(build)  # rebuild from the committed source gives the same files
    result = CliRunner().invoke(app, ["run", "harbour-listener"])
    assert result.exit_code == 0, result.output
    assert (build / "simulation/interface.json").read_bytes() == first
