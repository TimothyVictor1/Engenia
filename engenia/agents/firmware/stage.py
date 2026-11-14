"""Template proposal and deterministic native firmware verification."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

from engenia.contracts.models import Contract, Requirement, StageContext
from engenia.core import events
from engenia.core.spec import SpecError, load_requirements
from engenia.harness.packs import pack_file
from engenia.harness.proposer import propose_firmware_detector, proposer_fact

TEMPLATES = Path(__file__).with_name("templates")
# Detector families the templates ship: the tone detector (Goertzel-like bin power) for
# the buoy, and a plain block-RMS threshold sampler for packs without a tuned frequency.
DETECTORS = {
    "tone": {"frequency_hz": 200.0, "threshold": 0.15, "hold_s": 0.15},
    "threshold": {"threshold": 0.25, "hold_s": 0.15},
}
DEFAULT_DETECTOR = DETECTORS["tone"]
# SNR (dB) -> replay file; must match tools/make_data.py in the common template.
SNR_FILES = {-6: "tone_snr_m6db", 0: "tone_in_noise", 6: "tone_snr_p6db"}


def _write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _run(args: list[str], cwd: Path) -> str:
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=300)
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed:\n{result.stdout}\n{result.stderr}")
    return result.stdout


def default_detector(detector_type: str = "tone") -> dict:
    try:
        return dict(DETECTORS[detector_type])
    except KeyError:
        raise ValueError(
            f"unknown detector type {detector_type!r}; the pack must name one of "
            f"{sorted(DETECTORS)}"
        ) from None


def _detector_type(ctx: StageContext) -> str:
    """The detector family the project's pack asks for, defaulting to the tone detector."""
    path = pack_file(ctx, "pack.yaml")
    try:
        pack = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError:
        return "tone"
    detector = (pack.get("firmware") or {}).get("detector", "tone")
    if detector not in DETECTORS:
        raise ValueError(
            f"{path.name}: unknown firmware detector {detector!r}; expected {sorted(DETECTORS)}"
        )
    return detector


def _requirements(ctx: StageContext) -> list[Requirement]:
    try:
        return load_requirements(ctx.spec_dir)
    except SpecError:
        return []


def _emit_detector_decisions(ctx: StageContext, detector_type: str, detector: dict) -> None:
    """Deterministic fallback: the detector family and each setting, with the requirements the
    detection and false-alarm checks answer."""
    requirements = _requirements(ctx)
    detect = events.relevant_requirements(requirements, "firmware", ("detection_rate",))
    false_alarm = events.relevant_requirements(requirements, "firmware", ("false_alarm_rate",))
    both = sorted({*detect, *false_alarm})
    stage = ctx.stage
    others = [name for name in sorted(DETECTORS) if name != detector_type]
    events.emit_decision(
        ctx.root, stage, title="Detector type", choice=detector_type,
        reason=f"the pack asks for the {detector_type} detector",
        requirements=both, numbers={},
        alternatives=[{"option": name, "why_not": "not the detector the pack names"}
                      for name in others],
    )
    if "frequency_hz" in detector:
        events.emit_decision(
            ctx.root, stage, title="Detector frequency", choice=f"{detector['frequency_hz']:g} Hz",
            reason=(f"{detector['frequency_hz']:g} Hz is the tone the {detector_type} template "
                    "is tuned to; replay data is 2000 Hz mono"),
            requirements=detect, numbers={"frequency_hz": detector["frequency_hz"],
                                          "sample_rate_hz": 2000},
        )
    events.emit_decision(
        ctx.root, stage, title="Detection threshold", choice=f"{detector['threshold']:g}",
        reason=(f"threshold {detector['threshold']:g}: low enough to catch the tone, high "
                "enough to stay quiet on the quiet recording"),
        requirements=both, numbers={"threshold": detector["threshold"]},
    )
    events.emit_decision(
        ctx.root, stage, title="Hold time", choice=f"{detector['hold_s']:g} s",
        reason=(f"hold {detector['hold_s']:g} s merges one tone into a single detection, "
                "so it counts once"),
        requirements=false_alarm, numbers={"hold_s": detector["hold_s"]},
    )


def propose_firmware(ctx: StageContext) -> None:
    """Materialize a reviewable source snapshot, including all generation inputs."""
    contract_path = ctx.contract_path
    if not contract_path.is_file():
        contract_path = pack_file(ctx, "default_contract.json")
    contract = Contract.model_validate_json(contract_path.read_text())
    electronics = ctx.stage_interface_path("electronics")
    duty = json.loads(electronics.read_text())["facts"]["duty_cycle"]
    detector_type = _detector_type(ctx)
    detector = propose_firmware_detector(
        ctx, _requirements(ctx), detector_type, lambda: default_detector(detector_type)
    )
    if proposer_fact(ctx) == "deterministic":
        _emit_detector_decisions(ctx, detector_type, detector)
    # Common build system and data generation, then the detector variant's sources.
    shutil.copytree(TEMPLATES / "common", ctx.src_dir, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(TEMPLATES / detector_type, ctx.src_dir, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    include = ctx.src_dir / "include"
    include.mkdir(exist_ok=True)
    pins = contract.interfaces.pin_map
    lines = ["#ifndef PINS_H", "#define PINS_H"]
    for name, value in sorted(pins.items()):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", name):
            raise ValueError(f"invalid C pin identifier: {name}")
        lines.append(f"#define {name} {json.dumps(value)}")
    (include / "pins.h").write_text("\n".join([*lines, "#endif", ""]))
    (ctx.src_dir / "src/duty.h").write_text(
        "#ifndef DUTY_H\n#define DUTY_H\n" + "".join(
            f"#define DUTY_{name.upper()} {value:.12g}\n" for name, value in sorted(duty.items())
        ) + "#endif\n"
    )
    config = {
        "pins_used": pins, "duty_cycle": duty, "contract_version": contract.version,
        "detector": detector,
    }
    # Recorded only when it differs from the long-standing tone default, so existing
    # project snapshots keep their exact shape.
    if detector_type != "tone":
        config["detector_type"] = detector_type
    _write(ctx.src_dir / "config.json", config)
    _run([sys.executable, "tools/make_data.py"], ctx.src_dir)


class FirmwareStage:
    name = "firmware"
    needs = ["architect", "electronics"]
    reads_contract = True
    checks = ["detection_rate", "false_alarm_rate"]

    def generate(self, ctx: StageContext) -> None:
        propose_firmware(ctx)

    def build(self, ctx: StageContext) -> None:
        ctx.build_dir.mkdir(parents=True, exist_ok=True)
        if shutil.which("pio") is None:
            # No PlatformIO: publish why, never raise. Verifiers turn this into an error verdict.
            reason = "PlatformIO (pio) is not installed; firmware replay was not run"
            events.emit_decision(
                ctx.root, ctx.stage, title="Firmware replay", choice="not_run", reason=reason,
                requirements=[], numbers={},
            )
            _write(ctx.build_dir / "detections.json", {})
            _write(ctx.build_dir / "metrics.json", {"not_run": {"reason": reason}})
            return
        _run(["pio", "run", "-e", "native"], ctx.src_dir)
        output = _run(["pio", "test", "-e", "native"], ctx.src_dir)
        results = re.findall(r"test_detector.c:\d+:\s*(\w+)\s*\[(PASSED|FAILED|IGNORED)\]", output)
        if not results or any(status != "PASSED" for _, status in results):
            raise RuntimeError(f"Unity tests missing or unsuccessful:\n{output}")
        # PlatformIO test replaces the native executable with the Unity runner.
        _run(["pio", "run", "-e", "native"], ctx.src_dir)
        config = json.loads((ctx.src_dir / "config.json").read_text())
        detector_type = config.get("detector_type", "tone")
        detector = config.get("detector") or default_detector(detector_type)
        events.emit_decision(
            ctx.root, ctx.stage, title="Replay conditions",
            choice="tone at -6, 0, +6 dB SNR plus noise-only",
            reason=("fixed-seed synthesised signals go through the compiled native detector "
                    "with the settings the firmware proposer chose"),
            requirements=[],
            numbers={"seed": 20261113, **{k: v for k, v in detector.items()}},
        )
        detections = {}
        for name in [*SNR_FILES.values(), "quiet"]:
            args = [sys.executable, "tools/replay.py", f"data/{name}.wav"]
            if detector_type == "tone":
                args += ["--frequency", str(detector["frequency_hz"])]
            args += ["--threshold", str(detector["threshold"]), "--hold", str(detector["hold_s"])]
            detections[name] = json.loads(_run(args, ctx.src_dir))
        truth = json.loads((ctx.src_dir / "data/truth.json").read_text())
        by_snr = {
            str(snr): sum(any(start <= t < end for t in detections[name])
                          for start, end in truth) / len(truth)
            for snr, name in SNR_FILES.items()
        }
        metrics = {
            # The headline rate is the nominal 0 dB case the requirements are written against.
            "detection_rate": by_snr["0"],
            "detection_rate_by_snr_db": by_snr,
            "false_alarms_per_hour": len(detections["quiet"]) * 6,
            "evidence": "simulated",
            "tests_passed": len(results), "tests_total": len(results),
            # Portable source-size estimate; native executable size includes host runtime.
            "flash_kb": round(sum(p.stat().st_size for p in
                                  (ctx.src_dir / "src").glob("*.[ch]")) / 1024, 6),
        }
        _write(ctx.build_dir / "detections.json", detections)
        _write(ctx.build_dir / "metrics.json", metrics)

    def interface(self, ctx: StageContext) -> dict:
        return {
            **json.loads((ctx.src_dir / "config.json").read_text()),
            **json.loads((ctx.build_dir / "metrics.json").read_text()),
            "proposer": proposer_fact(ctx),
        }
