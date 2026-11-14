"""Cache invalidation against independent scratch project inputs."""
import json

import pytest

from engenia.contracts.models import StageContext
from engenia.core.hashing import compute_keys
from engenia.stages.registry import REGISTRY


def context(tmp_path, stage):
    ctx = StageContext(root=tmp_path, project="deck-sensor", stage=stage)
    ctx.spec_dir.mkdir(parents=True)
    (ctx.spec_dir / "R-001.yaml").write_text('''id: R-001
statement: The enclosure shall have a wall of at least 3 mm.
value: 3
unit: mm
verify: {method: analysis, check: wall_thickness_min, limit: {min: 3}}
affects: [mechanical]
status: approved
''')
    for need in REGISTRY[stage].needs:
        path = ctx.stage_interface_path(need)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"stage": need, "key": "sha256:" + "0" * 64, "facts": {}}))
    return ctx


@pytest.mark.parametrize(('stage', 'relative', 'old', 'new'), [
    ('mechanical', 'packs/buoy/pack.yaml',
     'materials: {PETG: {density_g_cm3: 1.27}}',
     'materials: {PETG: {density_g_cm3: 1.31}}'),
    ('redteam', 'projects/deck-sensor/scenarios/heat.yaml', 'temperature: 30', 'temperature: 50'),
    ('simulation', 'packs/buoy/sim/waves/params.yaml', 'resolution: 8', 'resolution: 16'),
    ('simulation', 'packs/buoy/sim/flow/params.yaml', 'cd: 1', 'cd: 2'),
    ('simulation', 'packs/buoy/sim/structure/params.yaml', 'mesh_mm: 5', 'mesh_mm: 4'),
    ('simulation', 'engenia/sim/structure/solver.py', 'MESH = 5', 'MESH = 4'),
    ('verify_plan', 'verifiers/wall_thickness_min.py', '# v1', '# v2'),
    ('mechanical', 'verifiers/wall_thickness_min.py', '# v1', '# v2'),
    ('mechanical', 'engenia/agents/mechanical/builder.py', '# v1', '# v2'),
    ('mechanical', 'projects/deck-sensor/checks/checks.yaml', 'params: {}', 'params: {k: 2}'),
    ('mechanical', 'projects/deck-sensor/calibration/m.json', '{"factor":1}', '{"factor":2}'),
])
def test_declared_input_edit_invalidates_keys(tmp_path, stage, relative, old, new):
    ctx = context(tmp_path, stage)
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(old)
    before = compute_keys(REGISTRY[stage], ctx)
    path.write_text(new)
    after = compute_keys(REGISTRY[stage], ctx)
    assert before.input_key != after.input_key
    assert before.stage_key != after.stage_key


def test_optional_input_creation_and_deletion_invalidates(tmp_path):
    ctx = context(tmp_path, 'redteam')
    stage = REGISTRY['redteam']
    absent = compute_keys(stage, ctx)
    directory = ctx.project_dir / 'scenarios'
    directory.mkdir()
    path = directory / 'heat.yaml'
    path.write_text('temperature: 50')
    assert compute_keys(stage, ctx) != absent
    path.unlink()
    directory.rmdir()
    assert compute_keys(stage, ctx) == absent


def test_check_plan_edit_rebuilds_without_self_invalidating_generation(tmp_path):
    ctx = context(tmp_path, 'verify_plan')
    ctx.checks_path.parent.mkdir()
    ctx.checks_path.write_text('params: {}')
    before = compute_keys(REGISTRY['verify_plan'], ctx)
    ctx.checks_path.write_text('params: {k: 2}')
    after = compute_keys(REGISTRY['verify_plan'], ctx)
    assert before.stage_key != after.stage_key
    assert before.input_key == after.input_key
