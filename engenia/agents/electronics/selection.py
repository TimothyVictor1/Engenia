"""Deterministic selection and explicit energy/packing calculations; no verdicts."""

from __future__ import annotations

import math
from itertools import permutations

from engenia.agents.electronics.facts import Facts, PartFact
from engenia.contracts.models import Contract, Requirement


def endurance_target(requirements: list[Requirement]) -> float:
    targets = []
    for req in requirements:
        if req.status != "approved" or req.verify.check != "battery_capacity":
            continue
        scale = {"day": 1, "days": 1, "d": 1, "h": 1 / 24, "hour": 1 / 24}
        if req.unit not in scale or req.value is None or not math.isfinite(req.value):
            raise ValueError(f"{req.id}: endurance requires a finite duration in days or hours")
        values = [req.value]
        if req.verify.limit:
            values += [v for v in (req.verify.limit.min, req.verify.limit.equals) if v is not None]
        target = max(values) * scale[req.unit]
        if not math.isfinite(target) or target <= 0:
            raise ValueError(f"{req.id}: endurance must be positive")
        targets.append(target)
    if not targets:
        raise ValueError("an approved battery_capacity requirement is required")
    return max(targets)


def power_facts(parts: dict[str, PartFact], facts: Facts, duty: dict[str, float]) -> dict:
    """Battery-side average mW, including converter losses and quiescent current."""
    reg, battery = parts["regulator"], parts["battery"]
    mode_power = {}
    for mode in duty:
        current = facts.design.support_current_ma
        for function in ("microcontroller", "lora", "audio"):
            part = parts[function]
            active = (
                function == "audio"
                or (function == "microcontroller" and mode != "sleep")
                or (function == "lora" and mode == "transmit")
            )
            current += part.active_current_ma if active else part.sleep_current_ma
        quiescent = reg.sleep_current_ma if mode == "sleep" else reg.active_current_ma
        mode_power[mode] = (
            reg.output_voltage_v * current / facts.design.regulator_efficiency
            + battery.nominal_voltage_v * quiescent
        )
    avg = sum(mode_power[mode] * duty[mode] for mode in duty)
    if avg <= 0:
        raise ValueError("average power must be positive")
    wh = battery.capacity_mah * battery.nominal_voltage_v / 1000
    return {
        "avg_power_mw": avg,
        "battery_wh": wh,
        "usable_battery_wh": wh * facts.design.usable_capacity_fraction,
        "endurance_days": wh * facts.design.usable_capacity_fraction * 1000 / (avg * 24),
        "mode_power_mw": mode_power,
    }


def assembly_size(battery: PartFact, facts: Facts, bay: tuple) -> list[float]:
    """Board above a flat cell; allow cell rotation in its plane, never on its edge."""
    bx, by, bz = facts.design.board_mm
    x, y, z = battery.size_mm
    candidates = [
        [max(bx, x), max(by, y), bz + z + facts.design.assembly_clearance_mm],
        [max(bx, y), max(by, x), bz + z + facts.design.assembly_clearance_mm],
    ]
    return min(
        candidates,
        key=lambda size: (
            math.prod(max(s, b) for s, b in zip(size, bay, strict=True)),
            math.prod(size),
            tuple(size),
        ),
    )


def propose_selection(contract: Contract, requirements: list[Requirement], facts: Facts) -> dict:
    """Smallest means cell volume, then capacity, mass, and id for stable ties.

    If no endurance-capable assembly fits, minimize required bay volume increase
    among capable cells. If capacity is impossible too, maximize endurance and
    leave that shortfall visible; a larger bay alone cannot solve it.
    """
    bay = contract.budgets.electronics.bay_mm
    if any(not math.isfinite(x) or x <= 0 for x in bay):
        raise ValueError("bay_mm must be finite and positive")
    target = endurance_target(requirements)
    fixed = {}
    for function in ("microcontroller", "lora", "audio", "regulator"):
        fixed[function] = min(
            (p for p in facts.parts if p.function == function), key=lambda p: p.id
        )
    voltage = fixed["regulator"].output_voltage_v
    for function in ("microcontroller", "lora", "audio"):
        p = fixed[function]
        if not p.supply_voltage_v[0] <= voltage <= p.supply_voltage_v[1]:
            raise ValueError(f"{p.id}: incompatible regulated voltage")
    # All modules must individually fit the declared board envelope.
    for part in fixed.values():
        if not any(
            all(s <= b for s, b in zip(dims, facts.design.board_mm, strict=True))
            for dims in permutations(part.size_mm)
        ):
            raise ValueError(f"{part.id}: module exceeds board envelope")
    candidates = []
    for battery in facts.parts:
        if battery.function != "battery":
            continue
        lo, hi = fixed["regulator"].supply_voltage_v
        if not lo <= battery.supply_voltage_v[0] <= battery.supply_voltage_v[1] <= hi:
            continue
        parts = {**fixed, "battery": battery}
        power = power_facts(parts, facts, facts.design.duty_cycle)
        size = assembly_size(battery, facts, bay)
        candidates.append(
            {
                "parts": {key: part.id for key, part in sorted(parts.items())},
                "duty_cycle": dict(facts.design.duty_cycle),
                "target_endurance_days": target,
                "bay_mm": list(bay),
                "board_mm": list(facts.design.board_mm),
                "assembly_mm": size,
                "mass_g": sum(p.mass_g for p in parts.values()) + facts.design.support_mass_g,
                **power,
            }
        )
    if not candidates:
        raise ValueError("no battery compatible with regulator input range")
    by_id = {p.id: p for p in facts.parts}

    def smallest(candidate):
        p = by_id[candidate["parts"]["battery"]]
        return math.prod(p.size_mm), p.capacity_mah, p.mass_g, p.id

    capable = [c for c in candidates if c["endurance_days"] >= target]
    fitting = [
        c for c in capable if all(s <= b for s, b in zip(c["assembly_mm"], bay, strict=True))
    ]
    if fitting:
        chosen = min(fitting, key=smallest)
    else:
        pool = capable or candidates
        chosen = min(
            pool,
            key=lambda c: (
                0 if capable else -c["endurance_days"],
                math.prod(max(s, b) for s, b in zip(c["assembly_mm"], bay, strict=True)),
                smallest(c),
            ),
        )
    chosen["requested_bay_mm"] = (
        [max(s, b) for s, b in zip(chosen["assembly_mm"], bay, strict=True)]
        if any(s > b for s, b in zip(chosen["assembly_mm"], bay, strict=True))
        else None
    )
    return chosen
