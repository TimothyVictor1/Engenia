"""Template proposal and deterministic native firmware verification."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from engenia.contracts.models import Contract, Requirement, StageContext
from engenia.core.spec import SpecError, load_requirements
from engenia.harness.proposer import propose_firmware_detector, proposer_fact

DEFAULT_DETECTOR = {"frequency_hz": 200.0, "threshold": 0.15, "hold_s": 0.15}


def _write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _run(args: list[str], cwd: Path) -> str:
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=300)
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed:\n{result.stdout}\n{result.stderr}")
    return result.stdout


def default_detector() -> dict:
    return dict(DEFAULT_DETECTOR)


def _requirements(ctx: StageContext) -> list[Requirement]:
    try:
        return load_requirements(ctx.spec_dir)
    except SpecError:
        return []


def propose_firmware(ctx: StageContext) -> None:
    """Materialize a reviewable source snapshot, including all generation inputs."""
    contract_path = ctx.contract_path
    if not contract_path.is_file():
        local = ctx.root / "packs/buoy/default_contract.json"
        contract_path = local if local.is_file() else (
            Path(__file__).resolve().parents[3] / "packs/buoy/default_contract.json"
        )
    contract = Contract.model_validate_json(contract_path.read_text())
    duty = {"sample": 0.003, "transmit": 0.0005, "sleep": 0.9965}
    electronics = ctx.build_dir.parent / "electronics/interface.json"
    if electronics.is_file():
        duty = json.loads(electronics.read_text())["facts"].get("duty_cycle", duty)
    detector = propose_firmware_detector(ctx, _requirements(ctx), default_detector)
    shutil.copytree(Path(__file__).with_name("templates"), ctx.src_dir, dirs_exist_ok=True,
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
    _write(ctx.src_dir / "config.json", {
        "pins_used": pins, "duty_cycle": duty, "contract_version": contract.version,
        "detector": detector,
    })
    _run([sys.executable, "tools/make_data.py"], ctx.src_dir)


class FirmwareStage:
    name = "firmware"
    needs = ["architect"]
    reads_contract = True
    checks = ["detection_rate", "false_alarm_rate"]

    def generate(self, ctx: StageContext) -> None:
        propose_firmware(ctx)

    def build(self, ctx: StageContext) -> None:
        ctx.build_dir.mkdir(parents=True, exist_ok=True)
        _run(["pio", "run", "-e", "native"], ctx.src_dir)
        output = _run(["pio", "test", "-e", "native"], ctx.src_dir)
        results = re.findall(r"test_detector.c:\d+:\s*(\w+)\s*\[(PASSED|FAILED|IGNORED)\]", output)
        if not results or any(status != "PASSED" for _, status in results):
            raise RuntimeError(f"Unity tests missing or unsuccessful:\n{output}")
        # PlatformIO test replaces the native executable with the Unity runner.
        _run(["pio", "run", "-e", "native"], ctx.src_dir)
        detector = json.loads((ctx.src_dir / "config.json").read_text()).get(
            "detector", DEFAULT_DETECTOR
        )
        detections = {}
        for name in ("tone_in_noise", "quiet"):
            detections[name] = json.loads(_run(
                [sys.executable, "tools/replay.py", f"data/{name}.wav",
                 "--frequency", str(detector["frequency_hz"]),
                 "--threshold", str(detector["threshold"]),
                 "--hold", str(detector["hold_s"])], ctx.src_dir
            ))
        truth = json.loads((ctx.src_dir / "data/truth.json").read_text())
        hits = sum(any(start <= t < end for t in detections["tone_in_noise"])
                   for start, end in truth)
        metrics = {
            "detection_rate": hits / len(truth),
            "false_alarms_per_hour": len(detections["quiet"]) * 6,
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
