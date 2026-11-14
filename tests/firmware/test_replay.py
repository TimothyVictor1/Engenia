"""Replay data and the no-PlatformIO path. Neither needs pio, so neither is slow."""
import hashlib
import json
import sys
import wave

import numpy as np

from engenia.agents.firmware import stage as stage_module
from engenia.agents.firmware.stage import FirmwareStage, _run
from engenia.contracts.models import StageContext
from engenia.core import events


def _generated(tmp_path):
    ctx = StageContext(root=tmp_path, project="fixture", stage="firmware")
    ctx.contract_path.parent.mkdir(parents=True)
    from pathlib import Path
    default = Path(__file__).resolve().parents[2] / "packs/buoy/default_contract.json"
    ctx.contract_path.write_text(default.read_text())
    electronics = ctx.stage_interface_path("electronics")
    electronics.parent.mkdir(parents=True)
    duty = {"sample": 0.01, "transmit": 0.02, "sleep": 0.97}
    electronics.write_text(json.dumps({"facts": {"duty_cycle": duty}}))
    FirmwareStage().generate(ctx)
    return ctx


def _hashes(directory):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.glob("*.wav")}


def _rms(path):
    with wave.open(str(path), "rb") as stream:
        return np.frombuffer(stream.readframes(stream.getnframes()), "<i2") / 32768


def test_snr_fixtures_are_fixed_seed_and_ordered_by_level(tmp_path):
    ctx = _generated(tmp_path)
    data = ctx.src_dir / "data"
    first = _hashes(data)
    assert set(first) == {"quiet.wav", "tone_in_noise.wav", "tone_snr_m6db.wav",
                          "tone_snr_p6db.wav"}
    _run([sys.executable, "tools/make_data.py"], ctx.src_dir)
    assert _hashes(data) == first
    # Burst power (seconds 2-3) grows with SNR; noise realisation is shared.
    power = {n: float(np.mean(_rms(data / f"{n}.wav")[4000:6000] ** 2))
             for n in ("tone_snr_m6db", "tone_in_noise", "tone_snr_p6db")}
    assert power["tone_snr_m6db"] < power["tone_in_noise"] < power["tone_snr_p6db"]


def test_missing_pio_publishes_not_run(tmp_path, monkeypatch):
    ctx = _generated(tmp_path)
    monkeypatch.setattr(stage_module.shutil, "which", lambda name: None)
    with events.activate(tmp_path, 5, "fixture"):
        FirmwareStage().build(ctx)
    facts = FirmwareStage().interface(ctx)
    assert "PlatformIO" in facts["not_run"]["reason"]
    assert "detection_rate" not in facts and "false_alarms_per_hour" not in facts
    found = [json.loads(x) for x in events.events_path(tmp_path, 5).read_text().splitlines()]
    assert any(e["kind"] == "decision" and e["data"]["choice"] == "not_run" for e in found)
    first = {p.name: p.read_bytes() for p in ctx.build_dir.iterdir()}
    FirmwareStage().build(ctx)
    assert {p.name: p.read_bytes() for p in ctx.build_dir.iterdir()} == first
