"""Simulation verifiers consume the interface emitted by the actual stage/engine."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from engenia.contracts.models import CheckContext, Limit, StageContext
from engenia.core.engine import _write_build_files
from engenia.harness.checks import _load_verifier
from engenia.sim import simulation
from engenia.sim.simulation import SimulationStage

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def publish_simulation(tmp_path, monkeypatch):
    ctx = StageContext(root=tmp_path, project='demo', stage='simulation')
    cases = ctx.stage_build_dir('simprep') / 'cases.yaml'
    cases.parent.mkdir(parents=True)
    cases.write_text(yaml.safe_dump([
        {'id': name, 'solver': name} for name in ('waves', 'structure', 'flow')
    ]))

    def publish(tilt=20.0, safety_factor=2.5, sane=True):
        sanity = {'passed': sane, 'messages': [] if sane else ['rejected fixture result']}
        results = {
            'waves': {'wave_tilt': {
                'calm_harbour': {'wave_height_m': 0.1, 'max_tilt_deg': 8.0},
                'baltic_winter': {'wave_height_m': 0.5, 'max_tilt_deg': tilt},
                'storm': {'wave_height_m': 2.0, 'max_tilt_deg': 80.0},
            }, 'sanity': sanity},
            'structure': {'safety_factor': safety_factor, 'max_von_mises_mpa': 12.0,
                          'structure_depth_m': 3.0, 'sanity': sanity},
            'flow': {'drag_n': {'calm_harbour': 0.2},
                     'evidence': {'calm_harbour': 'calculated'}, 'sanity': sanity},
        }
        monkeypatch.setattr(simulation, '_solver', lambda name: SimpleNamespace(
            run_case=lambda case, ctx: results[name],
        ))
        stage = SimulationStage()
        stage.build(ctx)
        _write_build_files(stage, ctx)
        facts = json.loads(ctx.stage_interface_path('simulation').read_text())['facts']
        assert 'scenarios' not in facts
        return ctx, facts

    return publish


@pytest.mark.parametrize(('check', 'field', 'value', 'limit', 'params', 'expected'), [
    ('wave_tilt', 'tilt', 20.0, {'max': 30}, {'wave_height_m': 0.5}, 'pass'),
    ('wave_tilt', 'tilt', 45.0, {'max': 30}, {'wave_height_m': 0.5}, 'fail'),
    ('wave_tilt', 'tilt', 20.0, {'max': 30}, {}, 'pass'),
    ('stress_safety_factor', 'safety_factor', 2.5, {'min': 2}, {}, 'pass'),
    ('stress_safety_factor', 'safety_factor', 1.2, {'min': 2}, {}, 'fail'),
])
def test_verifier_reads_real_published_interface(
    publish_simulation, check, field, value, limit, params, expected,
):
    ctx, facts = publish_simulation(**{field: value})
    verdict = _load_verifier(REPO, check)(CheckContext(
        root=ctx.root, project=ctx.project, stage='simulation', check=check,
        requirement='R-001', params=params, limit=Limit(**limit),
    ))
    assert verdict.result == expected, verdict.message
    assert verdict.measured == value
    assert verdict.limit == Limit(**limit)
    assert verdict.evidence_level == 'simulated'
    assert facts['drag_n'] == {'calm_harbour': 0.2}
    assert facts['drag_evidence'] == {'calm_harbour': 'calculated'}


@pytest.mark.parametrize(('check', 'limit'), [
    ('wave_tilt', {'max': 30}), ('stress_safety_factor', {'min': 2}),
])
def test_verifiers_reject_results_removed_by_stage_sanity(publish_simulation, check, limit):
    ctx, facts = publish_simulation(sane=False)
    assert facts['wave_tilt'] == {} and facts['safety_factor'] is None
    assert facts['drag_n'] == {}
    verdict = _load_verifier(REPO, check)(CheckContext(
        root=ctx.root, project=ctx.project, stage='simulation', check=check,
        requirement='R-001', limit=Limit(**limit),
    ))
    assert verdict.result == 'error'
