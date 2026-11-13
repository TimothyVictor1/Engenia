import shutil

from engenia.contracts.models import StageContext
from engenia.core.hashing import compute_keys
from engenia.sim import solver_digest
from engenia.sim.simulation import SimulationStage
from tests.sim.conftest import REPO, write_interface


def test_committed_digest_matches_the_solver_code():
    assert SimulationStage.solver_digest == solver_digest.compute(), (
        "run: uv run python -m engenia.sim.solver_digest --write"
    )


def copy_sim_tree(tmp_path):
    for sub in ("engenia/sim", "packs/buoy/sim"):
        shutil.copytree(REPO / sub, tmp_path / sub, ignore=shutil.ignore_patterns("__pycache__"))
    return tmp_path


def test_params_change_gives_a_new_digest(tmp_path):
    repo = copy_sim_tree(tmp_path)
    before = solver_digest.compute(repo)
    assert before == solver_digest.compute(REPO)
    params = repo / "packs/buoy/sim/waves/params.yaml"
    params.write_text(params.read_text().replace("gravity_m_s2: 9.81", "gravity_m_s2: 9.80"))
    assert solver_digest.compute(repo) != before


def test_solver_source_change_gives_a_new_digest(tmp_path):
    repo = copy_sim_tree(tmp_path)
    before = solver_digest.compute(repo)
    solver = repo / "engenia/sim/waves/solver.py"
    solver.write_text(solver.read_text() + "\n# changed\n")
    assert solver_digest.compute(repo) != before


def test_stage_key_changes_with_the_digest(root):
    for stage in ("mechanical", "integration", "redteam", "simprep"):
        write_interface(root, stage, {"x": 1})
    ctx = StageContext(project="fixture", stage="simulation", root=root)
    stage = SimulationStage()
    before = compute_keys(stage, ctx).stage_key

    class Changed(SimulationStage):
        solver_digest = "sha256:changed"

    # Same name and needs, different class source: the key must move.
    assert compute_keys(Changed(), ctx).stage_key != before
