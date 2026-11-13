"""The simulation stage: level-1 wave tilt for every red-team scenario.

Replaced the step-0 stub. build() reads the built red-team scenarios and the
mechanical interface facts, computes the level-1 wave tilt of each scenario
(engenia.sim.waves; the Capytaine case in the sim container comes later), and
writes build/simulation/results.json. Two runs produce byte-identical output.

The interface publishes exactly two facts:

    wave_tilt:      {scenario: {"wave_height_m": float, "max_tilt_deg": float}}
    safety_factor:  float or null

safety_factor is null until the structure case (CalculiX, B1-S3) is wired; the
stress_safety_factor verifier turns null into an "error" verdict, never a pass.

When the mechanical interface lacks the hull facts the wave model needs (a
non-cylindrical template, or a design that omits the displaced volume), the
stage publishes an empty wave_tilt mapping rather than guessing: the verifier
then reports "error" for the wave-tilt requirement, which is the honest result
for a design the model cannot describe.
"""

from __future__ import annotations

import json
import math
import shutil
from typing import Any

import yaml

from engenia.contracts.models import StageContext
from engenia.sim import waves

RESULTS_FILE = "results.json"
SCENARIOS_DIRNAME = "scenarios"
ROUND = 6  # every published float, so runs stay byte-identical


def _round(value: float) -> float:
    return round(value, ROUND)


def _read_facts(ctx: StageContext, stage: str) -> dict[str, Any] | None:
    """The facts object of build/<stage>/interface.json, or None when unusable."""
    path = ctx.stage_interface_path(stage)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    facts = data.get("facts") if isinstance(data, dict) else None
    return facts if isinstance(facts, dict) else None


def _load_scenarios(ctx: StageContext) -> list[dict[str, Any]]:
    """The built red-team scenarios, sorted by name, with their wave numbers."""
    folder = ctx.stage_build_dir("redteam") / SCENARIOS_DIRNAME
    paths = sorted(folder.glob("*.yaml"))
    if not paths:
        raise ValueError(f"no red-team scenarios in {folder}; run the redteam stage first")
    scenarios: list[dict[str, Any]] = []
    for path in paths:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ValueError(f"{path.name}: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"{path.name}: scenario must be a mapping")
        for key in ("wave_height_m", "wave_period_s"):
            value = data.get(key)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise ValueError(f"{path.name}: {key} must be a finite number")
        if data["wave_period_s"] <= 0:
            raise ValueError(
                f"{path.name}: wave_period_s must be positive, got {data['wave_period_s']}"
            )
        if data["wave_height_m"] < 0:
            raise ValueError(
                f"{path.name}: wave_height_m must not be negative, got {data['wave_height_m']}"
            )
        scenarios.append(
            {
                "name": path.stem,
                "wave_height_m": float(data["wave_height_m"]),
                "wave_period_s": float(data["wave_period_s"]),
            }
        )
    return scenarios


def _results(ctx: StageContext) -> dict[str, Any]:
    scenarios = _load_scenarios(ctx)
    mechanical = _read_facts(ctx, "mechanical")
    hull: dict[str, float] | None = None
    input_error: str | None = None
    if mechanical is not None:
        try:
            hull = waves.stability_from_facts(mechanical)
        except waves.ModelInputError as exc:
            input_error = str(exc)

    unstable = hull is not None and hull["gm_mm"] <= 0
    tilt: dict[str, dict[str, float]] = {}
    detail: dict[str, dict[str, float]] = {}
    for scenario in scenarios:
        name = str(scenario["name"])
        height, period = scenario["wave_height_m"], scenario["wave_period_s"]
        if hull is None:
            continue  # no hull facts: the stage publishes no tilt for any scenario
        if unstable:  # an unstable upright hull rolls over: publish the bound
            max_tilt_deg = waves.MAX_TILT_DEG
            detail[name] = {
                "wave_height_m": _round(height),
                "wave_period_s": _round(period),
                "max_tilt_deg": _round(max_tilt_deg),
            }
        else:
            numbers = waves.scenario_tilt(hull, height, period)
            max_tilt_deg = numbers["max_tilt_deg"]
            detail[name] = {
                "wave_height_m": _round(height),
                "wave_period_s": _round(period),
                "max_tilt_deg": _round(max_tilt_deg),
                "wave_slope_deg": _round(numbers["wave_slope_deg"]),
                "rao": _round(numbers["rao"]),
            }
        tilt[name] = {"wave_height_m": _round(height), "max_tilt_deg": _round(max_tilt_deg)}

    notes = [
        "safety_factor is null: the structure case is not wired yet (B1-S3), "
        "so stress_safety_factor reports error, never pass"
    ]
    if hull is None:
        notes.append(
            "wave_tilt is empty: the mechanical interface has no usable hull facts "
            + (f"({input_error})" if input_error else "(missing)")
        )
    elif unstable:
        notes.append(
            f"hull GM is {hull['gm_mm']:.3f} mm (unstable upright): every max_tilt_deg "
            f"is published at the {waves.MAX_TILT_DEG} degree plausibility bound"
        )
    return {
        "model": {
            "name": "wave_tilt_linear_v1",
            "module": "engenia.sim.waves",
            "inputs": {} if hull is None else {key: _round(value) for key, value in hull.items()},
            "scenarios": detail,
        },
        "wave_tilt": tilt,
        "safety_factor": None,
        "notes": notes,
    }


class SimulationStage:
    name = "simulation"
    needs = ["integration", "redteam", "simprep"]
    reads_contract = False
    checks = ["wave_tilt", "stress_safety_factor"]

    def generate(self, ctx: StageContext) -> None:
        """No agent this sprint: the stage is deterministic."""
        ctx.src_dir.mkdir(parents=True, exist_ok=True)

    def build(self, ctx: StageContext) -> None:
        if ctx.build_dir.exists():
            shutil.rmtree(ctx.build_dir)  # no stale outputs from an earlier run
        ctx.build_dir.mkdir(parents=True, exist_ok=True)
        results = _results(ctx)
        (ctx.build_dir / RESULTS_FILE).write_text(
            json.dumps(results, sort_keys=True, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )

    def interface(self, ctx: StageContext) -> dict[str, Any]:
        """The two published facts, read back from the results file."""
        results = json.loads((ctx.build_dir / RESULTS_FILE).read_text(encoding="utf-8"))
        return {"wave_tilt": results["wave_tilt"], "safety_factor": results["safety_factor"]}

