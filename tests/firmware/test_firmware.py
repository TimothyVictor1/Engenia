"""Exercise actual PlatformIO compilation, Unity tests and native WAV replay."""
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from engenia.agents.firmware.stage import FirmwareStage, _run
from engenia.contracts.models import StageContext

pytestmark = pytest.mark.slow


def hashes(directory):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.glob('*.wav')}


def test_native_pipeline_and_reproduction(tmp_path):
    ctx = StageContext(root=tmp_path, project="fixture", stage="firmware")
    ctx.contract_path.parent.mkdir(parents=True)
    default = Path(__file__).resolve().parents[2] / 'packs/buoy/default_contract.json'
    contract = json.loads(default.read_text())
    contract['interfaces']['pin_map'] = {'SENSOR': 'GPIO12', 'RADIO': 'GPIO7'}
    ctx.contract_path.write_text(json.dumps(contract))
    electronics = ctx.stage_interface_path('electronics')
    electronics.parent.mkdir(parents=True)
    duty = {'sample': 0.01, 'transmit': 0.02, 'sleep': 0.97}
    electronics.write_text(json.dumps({'facts': {'duty_cycle': duty}}))
    stage = FirmwareStage()
    stage.generate(ctx)
    original = hashes(ctx.src_dir / 'data')
    import sys
    _run([sys.executable, 'tools/make_data.py'], ctx.src_dir)
    assert hashes(ctx.src_dir / 'data') == original
    stage.build(ctx)
    facts = stage.interface(ctx)
    assert facts['pins_used'] == contract['interfaces']['pin_map']
    assert facts['duty_cycle'] == duty
    assert facts['detection_rate'] >= 0.9
    assert facts['false_alarms_per_hour'] <= 2
    assert facts['tests_passed'] == facts['tests_total'] == 2
    assert facts['contract_version'] == contract['version']
    assert '#define SENSOR "GPIO12"' in (ctx.src_dir / 'include/pins.h').read_text()
    assert '#define DUTY_SAMPLE 0.01' in (ctx.src_dir / 'src/duty.h').read_text()
    before = {p.name: p.read_bytes() for p in ctx.build_dir.iterdir()}
    other = StageContext(root=tmp_path / 'relocated', project='fixture', stage='firmware')
    shutil.copytree(ctx.src_dir, other.src_dir, ignore=shutil.ignore_patterns('.pio'))
    stage.build(other)
    assert {p.name: p.read_bytes() for p in other.build_dir.iterdir()} == before


def test_missing_electronics_interface_is_an_error(tmp_path):
    ctx = StageContext(root=tmp_path, project='fixture', stage='firmware')
    with pytest.raises(FileNotFoundError, match='electronics/interface.json'):
        FirmwareStage().generate(ctx)


def test_missing_duty_cycle_is_an_error(tmp_path):
    ctx = StageContext(root=tmp_path, project='fixture', stage='firmware')
    path = ctx.stage_interface_path('electronics')
    path.parent.mkdir(parents=True)
    path.write_text('{"facts": {}}')
    with pytest.raises(KeyError, match='duty_cycle'):
        FirmwareStage().generate(ctx)
