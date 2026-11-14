"""Red-team stage: environmental stress scenarios for the simulation stage.

Sprint 1: propose_scenarios() returns four fixed scenarios and makes no model call, so the
pipeline runs end to end. The harness may replace propose_scenarios() later; generate()
resolves it through the module namespace, so a monkeypatch takes effect.

A project can also own its scenarios: when projects/<p>/scenarios/ holds *.yaml files,
generate() uses those instead of the fixed proposal (project_scenarios). A project file
that does not validate is an error, never a silent fallback to the buoy scenarios.

Scenario files live at src/redteam/scenarios/<name>.yaml and are validated and copied to
build/redteam/scenarios/ by build(). Units: wave_height_m is significant wave height in
metres, wave_period_s in seconds, water_temp_c in degrees Celsius, water_density in kg/m3,
depth_m is the water depth at the mooring in metres. The wave and water fields are optional:
scenarios for products that do not float (a deck-mounted enclosure) omit them, and the wave
and flow solvers skip those scenarios, recording why in their sanity messages.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from engenia.contracts.models import StageContext

SCENARIOS_DIRNAME = "scenarios"


class Scenario(BaseModel):
    """One red-team scenario file (src/redteam/scenarios/<name>.yaml).

    The wave and water fields are optional: a product that does not float (for
    example a deck-mounted enclosure) leaves them unset, and the solvers that
    need them skip such scenarios and say why. `water_temp_c` stays required:
    every product has an ambient temperature.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    wave_height_m: float | None = None
    wave_period_s: float | None = None
    water_temp_c: float
    water_density: float | None = None
    depth_m: float | None = None
    extra: dict[str, Any] = Field(default_factory=dict)
    rationale: str
    targets: list[str] = Field(min_length=1)


def propose_scenarios() -> list[Scenario]:
    """The four fixed sprint-1 scenarios. No model call; the harness may replace this."""
    return [
        Scenario(
            name="calm_harbour",
            wave_height_m=0.1,
            wave_period_s=3.0,
            water_temp_c=15.0,
            water_density=1005.0,
            depth_m=3.0,
            extra={"shipping_traffic": False},
            rationale=(
                "Baseline sheltered-harbour conditions: upright floating and the tilt "
                "limit must hold in light chop."
            ),
            targets=["R-006", "R-011"],
        ),
        Scenario(
            name="baltic_winter",
            wave_height_m=0.5,
            wave_period_s=4.5,
            water_temp_c=2.0,
            water_density=1003.0,
            depth_m=5.0,
            extra={"air_temp_c": -10.0},
            rationale=(
                "Cold brackish water with short winter chop: the power budget and the "
                "metacentric height must hold at low temperature and density."
            ),
            targets=["R-006", "R-007", "R-011"],
        ),
        Scenario(
            name="storm",
            wave_height_m=2.0,
            wave_period_s=8.0,
            water_temp_c=12.0,
            water_density=1005.0,
            depth_m=6.0,
            extra={"wind_speed_ms": 20.0},
            rationale=(
                "Design storm in open water: the worst case for tilt limits and hull "
                "stress at depth."
            ),
            targets=["R-006", "R-011", "R-012"],
        ),
        Scenario(
            name="flooded_bay",
            wave_height_m=0.4,
            wave_period_s=3.5,
            water_temp_c=14.0,
            water_density=1000.0,
            depth_m=3.0,
            extra={"hull_flood_percent": 10.0},
            rationale=(
                "Hull partially flooded after a seal failure in a rain-swollen bay of "
                "fresh water: buoyancy margin, upright floating and hull stress degrade."
            ),
            targets=["R-005", "R-006", "R-012"],
        ),
    ]


def project_scenarios(ctx: StageContext) -> list[Scenario]:
    """Scenarios owned by the project: projects/<p>/scenarios/*.yaml.

    Empty when the project ships none, so generate() falls back to the fixed proposal.
    A file that does not validate raises: a broken project scenario must not quietly
    become a buoy scenario.
    """
    directory = ctx.project_dir / SCENARIOS_DIRNAME
    paths = sorted(directory.glob("*.yaml")) if directory.is_dir() else []
    scenarios: list[Scenario] = []
    for path in paths:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            scenario = Scenario.model_validate(data)
        except (yaml.YAMLError, ValidationError) as exc:
            raise ValueError(f"{path.name}: {exc}") from exc
        if scenario.name != path.stem:
            raise ValueError(
                f"{path.name}: scenario name {scenario.name} does not match file name"
            )
        scenarios.append(scenario)
    return scenarios


def _scenario_path(src_dir: Path, name: str) -> Path:
    return src_dir / SCENARIOS_DIRNAME / f"{name}.yaml"


class RedteamStage:
    """Writes scenario files to src/redteam/scenarios/ and validated copies to build/."""

    name = "redteam"
    needs: list[str] = []
    reads_contract = False
    checks: list[str] = []

    def generate(self, ctx: StageContext) -> None:
        """Write the proposed scenarios to src/redteam/scenarios/*.yaml.

        The project's own scenarios (projects/<p>/scenarios/) win when present; the fixed
        proposal is the fallback.
        """
        scenarios_dir = ctx.src_dir / SCENARIOS_DIRNAME
        if scenarios_dir.is_dir():
            shutil.rmtree(scenarios_dir)
        scenarios_dir.mkdir(parents=True, exist_ok=True)
        for scenario in project_scenarios(ctx) or propose_scenarios():
            payload = scenario.model_dump(mode="json")
            _scenario_path(ctx.src_dir, scenario.name).write_text(
                yaml.safe_dump(payload, sort_keys=True, default_flow_style=False),
                encoding="utf-8",
            )

    def build(self, ctx: StageContext) -> None:
        """Validate the scenario files and copy them to build/redteam/scenarios/."""
        scenarios = self._load_scenarios(ctx)
        self._check_targets(ctx, scenarios)
        if ctx.build_dir.exists():
            shutil.rmtree(ctx.build_dir)
        out_dir = ctx.build_dir / SCENARIOS_DIRNAME
        out_dir.mkdir(parents=True)
        for scenario in scenarios:
            source = _scenario_path(ctx.src_dir, scenario.name)
            shutil.copy2(source, out_dir / source.name)

    def interface(self, ctx: StageContext) -> dict:
        """Facts for later stages: the names of the built scenarios."""
        out_dir = ctx.build_dir / SCENARIOS_DIRNAME
        names = sorted(path.stem for path in out_dir.glob("*.yaml")) if out_dir.is_dir() else []
        return {"scenarios": names}

    def _load_scenarios(self, ctx: StageContext) -> list[Scenario]:
        scenarios_dir = ctx.src_dir / SCENARIOS_DIRNAME
        paths = sorted(scenarios_dir.glob("*.yaml"))
        if not paths:
            raise ValueError(f"no scenario files in {scenarios_dir}; run generate() first")
        scenarios = []
        for path in paths:
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
                scenario = Scenario.model_validate(data)
            except (yaml.YAMLError, ValidationError) as exc:
                raise ValueError(f"{path.name}: {exc}") from exc
            if scenario.name != path.stem:
                raise ValueError(
                    f"{path.name}: scenario name {scenario.name} does not match file name"
                )
            scenarios.append(scenario)
        return scenarios

    def _check_targets(self, ctx: StageContext, scenarios: list[Scenario]) -> None:
        """Every target must be a requirement id that exists in the project spec."""
        spec_ids = {path.stem for path in ctx.spec_dir.glob("R-*.yaml")}
        for scenario in scenarios:
            unknown = [target for target in scenario.targets if target not in spec_ids]
            if unknown:
                raise ValueError(
                    f"{scenario.name}.yaml: targets unknown requirement ids {unknown}"
                )
