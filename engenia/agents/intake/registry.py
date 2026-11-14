"""The registry of checks intake may use, and the rules that keep requirements testable.

The registry is the set of verifier scripts in `<root>/verifiers/`, each described for the model:
what it measures, its unit, which way its limit points, and an example requirement. The
descriptions for the known verifiers are kept here; a verifier with no entry is listed with the
first line of its docstring and no unit, so a new verifier is usable at once and documented later.

`measured_*` verifiers judge a printed prototype after `engenia measure`; intake never plans them.

`check_requirement` is the "unknown check" rule plus a testability rule: the check must be in the
registry, the limit must point the way the check can test, and a requirement the check cannot
verify is reported as such. `engenia lint` runs on top of this for the schema rules L1 to L6.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Any

EXCLUDED_PREFIXES = ("measured_",)


@dataclass(frozen=True)
class CheckInfo:
    name: str
    measures: str
    unit: str | None
    limit: str  # "min", "max" or "equals": the bound the check can test
    method: str
    affects: tuple[str, ...]
    example: str
    note: str = ""

    def describe(self) -> str:
        unit = self.unit if self.unit else "see the check"
        lines = [
            f"- {self.name}: {self.measures}",
            f"    unit: {unit}; limit: {self.limit}; verify.method: {self.method}; "
            f"affects: [{', '.join(self.affects)}]",
            f"    example: {self.example}",
        ]
        if self.note:
            lines.append(f"    note: {self.note}")
        return "\n".join(lines)


def _example(
    rid: str, statement: str, value: object, unit: str, check: str, method: str, limit: str,
    affects: str,
) -> str:
    return (
        f"{{id: {rid}, statement: \"{statement}\", value: {value}, unit: {unit}, "
        f"verify: {{method: {method}, check: {check}, limit: {{{limit}: {value}}}}}, "
        f"affects: [{affects}], status: draft}}"
    )


_KNOWN: tuple[CheckInfo, ...] = (
    CheckInfo(
        "mass_budget", "total mass of the design, from the CAD and the parts", "g", "max",
        "analysis", ("mechanical", "electronics"),
        _example("R-001", "The total mass of the buoy shall not exceed 400 g.", 400, "g",
                 "mass_budget", "analysis", "max", "mechanical, electronics"),
    ),
    CheckInfo(
        "battery_capacity", "battery endurance in days, from battery energy and average power",
        "day", "min", "analysis", ("electronics", "mechanical"),
        _example("R-002", "The buoy shall operate for at least 30 days on one battery charge.",
                 30, "day", "battery_capacity", "analysis", "min", "electronics, mechanical"),
    ),
    CheckInfo(
        "power_budget", "average electrical power draw of the electronics", "mW", "max",
        "analysis", ("electronics",),
        _example("R-003", "The electronics shall draw an average power of at most 25 mW.",
                 25, "mW", "power_budget", "analysis", "max", "electronics"),
    ),
    CheckInfo(
        "buoyancy_margin",
        "(displaced weight - mass) / mass: how far the hull floats above the water line",
        "ratio", "min", "analysis", ("mechanical",),
        _example("R-004", "The buoy shall have a buoyancy margin of at least 0.2.", 0.2,
                 "ratio", "buoyancy_margin", "analysis", "min", "mechanical"),
    ),
    CheckInfo(
        "metacentric_height", "metacentric height GM of the floating hull (upright stability)",
        "mm", "min", "analysis", ("mechanical",),
        _example("R-005", "The buoy shall float upright with a metacentric height of at least "
                 "10 mm.", 10, "mm", "metacentric_height", "analysis", "min", "mechanical"),
    ),
    CheckInfo(
        "wall_thickness_min", "thinnest wall of the printed hull", "mm", "min", "analysis",
        ("mechanical",),
        _example("R-006", "The hull shall have a minimum wall thickness of at least 2 mm.", 2,
                 "mm", "wall_thickness_min", "analysis", "min", "mechanical"),
    ),
    CheckInfo(
        "detection_rate", "fraction of target tones the firmware detects in the recorded test "
        "audio (0 to 1)", "ratio", "min", "analysis", ("firmware",),
        _example("R-007", "The firmware shall detect the target tone in the recorded test "
                 "audio with a detection rate of at least 0.9.", 0.9, "ratio", "detection_rate",
                 "analysis", "min", "firmware"),
    ),
    CheckInfo(
        "false_alarm_rate", "false alarms per hour the firmware raises on the quiet recording",
        "1/h", "max", "analysis", ("firmware",),
        _example("R-008", "The firmware shall raise at most 1 false alarms per hour on the "
                 "quiet recording.", 1, "1/h", "false_alarm_rate", "analysis", "max",
                 "firmware"),
    ),
    CheckInfo(
        "wave_tilt",
        "largest tilt from vertical, in degrees, over the simulated wave scenarios whose wave "
        "height is at most the one the requirement names",
        "deg", "max", "simulation", ("simulation",),
        _example("R-009", "The buoy shall keep its tilt within 30 degrees in simulated waves "
                 "up to 0.5 m significant height.", 30, "deg", "wave_tilt", "simulation", "max",
                 "simulation"),
        note="to turn 'survive 0.5 m waves' into something testable, use wave_tilt (max tilt "
             "deg, in scenarios up to 0.5 m) together with buoyancy_margin",
    ),
    CheckInfo(
        "stress_safety_factor", "smallest stress safety factor of the hull at the design depth, "
        "from the structural simulation", "ratio", "min", "simulation", ("simulation",),
        _example("R-010", "The hull shall keep a stress safety factor of at least 1.5 at the "
                 "design depth.", 1.5, "ratio", "stress_safety_factor", "simulation", "min",
                 "simulation"),
    ),
    CheckInfo(
        "print_bed_fit", "whether every printed part fits the printer bed (1 fits, 0 does not)",
        "ratio", "equals", "analysis", ("mechanical",),
        _example("R-011", "Every printed part shall fit the print bed.", 1, "ratio",
                 "print_bed_fit", "analysis", "equals", "mechanical"),
    ),
    CheckInfo(
        "bay_fit", "whether the board and battery fit the electronics bay (1 fits, 0 does not)",
        "ratio", "equals", "analysis", ("electronics", "mechanical"),
        _example("R-012", "The board and battery shall fit the electronics bay.", 1, "ratio",
                 "bay_fit", "analysis", "equals", "electronics, mechanical"),
    ),
    CheckInfo(
        "erc_clean", "number of electrical rule check errors in the schematic (0 is clean)",
        "errors", "equals", "analysis", ("electronics",),
        _example("R-013", "The schematic shall have no electrical rule check errors.", 0,
                 "errors", "erc_clean", "analysis", "equals", "electronics"),
    ),
)
_KNOWN_BY_NAME = {info.name: info for info in _KNOWN}


def _docstring_line(path: Path) -> str:
    try:
        doc = ast.get_docstring(ast.parse(path.read_text(encoding="utf-8"))) or ""
    except (OSError, SyntaxError, ValueError):
        doc = ""
    return doc.strip().splitlines()[0].strip() if doc.strip() else "(no description)"


def load_registry(verifiers_dir: Path) -> dict[str, CheckInfo]:
    """Every check intake may use: the verifier scripts present in `verifiers_dir`."""
    registry: dict[str, CheckInfo] = {}
    for path in sorted(Path(verifiers_dir).glob("*.py")):
        name = path.stem
        if name.startswith("_") or name.startswith(EXCLUDED_PREFIXES):
            continue
        info = _KNOWN_BY_NAME.get(name)
        if info is None:
            info = CheckInfo(
                name, _docstring_line(path), None, "max or min", "analysis", ("mechanical",),
                "(no example; follow the pattern of the others and read the unit from the "
                "request)",
            )
        registry[name] = info
    return registry


def registry_text(registry: dict[str, CheckInfo]) -> str:
    """The registry as the prompt shows it."""
    return "\n".join(info.describe() for info in registry.values()) or "(no checks available)"


def check_requirement(data: Any, registry: dict[str, CheckInfo]) -> list[str]:
    """Problems that make a requirement untestable by the registry. Empty when it is testable."""
    if not isinstance(data, dict):
        return []  # lint reports a file that is not a mapping
    verify = data.get("verify")
    if not isinstance(verify, dict):
        return []
    check = verify.get("check")
    if not isinstance(check, str) or not check:
        return []  # lint L6 reports a missing check
    info = registry.get(check)
    if info is None:
        names = ", ".join(registry) or "(none)"
        return [
            f"unknown check {check!r}: no such verifier. Use only one of: {names}. If none of "
            "them can verify this requirement, do not write it; list it as a design intent in "
            "your final message instead"
        ]
    problems: list[str] = []
    if verify.get("method") == "inspection":
        problems.append(
            f"{check} measures a number, so verify.method cannot be inspection: give a value, "
            "unit and limit"
        )
    limit = verify.get("limit")
    if isinstance(limit, dict) and info.limit in ("min", "max", "equals"):
        given = [k for k in ("min", "max", "equals") if limit.get(k) is not None]
        if given and info.limit not in given:
            problems.append(
                f"{check} tests a {info.limit} limit, but verify.limit sets "
                f"{', '.join(given)}"
            )
    if info.unit and data.get("unit") not in (None, "") and data.get("unit") != info.unit:
        problems.append(
            f"{check} is measured in {info.unit}, but unit is {data.get('unit')!r}"
        )
    return problems
