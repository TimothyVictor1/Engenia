"""Simulation stage: runs every case from simprep through its solver and merges the facts.

For each case in build/simprep/cases.yaml, `engenia.sim.<solver>.run_case(case, ctx)` returns a
dict. A solver module that does not exist gives the case status "not_run". The dict may hold:

    wave_tilt        {scenario: {"wave_height_m": float, "max_tilt_deg": float}}
    safety_factor, max_von_mises_mpa, structure_depth_m     float
    sanity           {"passed": bool, "messages": [str]}

A case whose sanity did not pass contributes only its sanity entry to the interface facts.
build/simulation/results.json holds each case's status and returned dict; it has no absolute
paths or timestamps. The facts are derived from it by interface().
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import math
import shutil

from engenia.contracts.models import StageContext
from engenia.sim.simprep import CASES_FILE, load_cases

RESULTS_FILE = "results.json"
SCALARS = ("safety_factor", "max_von_mises_mpa", "structure_depth_m")


def _solver(name: str):
    """The module engenia.sim.<name>, or None when it does not exist."""
    module = f"engenia.sim.{name}"
    if importlib.util.find_spec(module) is None:
        return None
    return importlib.import_module(module)


def _clean(value):
    """JSON-safe copy: rejects NaN and infinity, which would make the output unreadable."""
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("solver returned a non-finite number")
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    return value


def _sanity_passed(result: dict) -> bool:
    sanity = result.get("sanity")
    return True if sanity is None else bool(sanity.get("passed"))


class SimulationStage:
    name = "simulation"
    needs = ["integration", "redteam", "simprep"]
    reads_contract = False
    checks: list[str] = []

    def generate(self, ctx: StageContext) -> None:
        ctx.src_dir.mkdir(parents=True, exist_ok=True)

    def build(self, ctx: StageContext) -> None:
        cases = load_cases(ctx.stage_build_dir("simprep") / CASES_FILE)
        results = {}
        for case in sorted(cases, key=lambda c: c["id"]):
            module = _solver(case["solver"])
            if module is None:
                results[case["id"]] = {"status": "not_run"}
                continue
            result = _clean(module.run_case(case, ctx))
            results[case["id"]] = {
                "status": "ok" if _sanity_passed(result) else "failed_sanity",
                "result": result,
            }
        if ctx.build_dir.exists():
            shutil.rmtree(ctx.build_dir)
        ctx.build_dir.mkdir(parents=True)
        (ctx.build_dir / RESULTS_FILE).write_text(
            json.dumps(results, sort_keys=True, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )

    def interface(self, ctx: StageContext) -> dict:
        results = json.loads((ctx.build_dir / RESULTS_FILE).read_text(encoding="utf-8"))
        facts: dict = {
            "wave_tilt": {},
            "safety_factor": None,
            "max_von_mises_mpa": None,
            "structure_depth_m": None,
            "not_run": [],
            "sanity": {},
        }
        for case_id in sorted(results):
            entry = results[case_id]
            if entry["status"] == "not_run":
                facts["not_run"].append(case_id)
                continue
            result = entry["result"]
            sanity = result.get("sanity") or {"passed": True, "messages": []}
            facts["sanity"][case_id] = {
                "passed": bool(sanity["passed"]),
                "messages": list(sanity.get("messages", [])),
            }
            if entry["status"] != "ok":
                continue
            facts["wave_tilt"].update(result.get("wave_tilt", {}))
            for key in SCALARS:
                if result.get(key) is not None:
                    facts[key] = result[key]
        return facts
